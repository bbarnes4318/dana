"""Pipeline for converting approved TrainingExamples to approved RAG Documents.

Allows reviewed, compliance-safe objection handling, stage guidance, and call
examples to be retrieved during live calls.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional
from pydantic import BaseModel, Field

from storage.repository import Repository
from rag.document import Document
from rag.embeddings import EmbeddingProvider, get_embedding_provider
from rag.vector_store import BaseVectorStore, get_vector_store
from training.ingestion import redact_text

logger = logging.getLogger(__name__)


class TrainingRagBuildResult(BaseModel):
    """Structured result of a training-to-RAG build run."""

    total_training_examples_scanned: int = 0
    eligible_examples: int = 0
    skipped_examples: int = 0
    documents_created: int = 0
    documents_upserted: int = 0
    vector_store_count: Optional[int] = None
    skipped_reasons: dict[str, int] = Field(default_factory=dict)
    document_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TrainingRagDocumentBuilder:
    """Builder that scans, validates, and processes TrainingExamples into RAG Documents."""

    def __init__(
        self,
        repository: Repository | None = None,
        vector_store: BaseVectorStore | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self.repository = repository or Repository()
        self.vector_store = vector_store or get_vector_store()
        self.embedding_provider = embedding_provider or get_embedding_provider()

    def _get_field(self, obj: Any, name: str, default: Any = None) -> Any:
        """Helper to get field from either a Pydantic model or a dictionary."""
        if hasattr(obj, name):
            return getattr(obj, name)
        if isinstance(obj, dict):
            return obj.get(name, default)
        return default

    def is_example_eligible_for_rag(self, example: Any) -> tuple[bool, str]:
        """Check if a training example is eligible to be built into a RAG document."""
        # 1. approved_by and approved_at must not be empty
        approved_by = self._get_field(example, "approved_by")
        approved_at = self._get_field(example, "approved_at")
        if not approved_by or not approved_at:
            return False, "not approved"

        # 2. use_for must contain "rag" or "prompt"
        use_for = self._get_field(example, "use_for") or []
        use_for_lower = [str(u).lower() for u in use_for]
        if not ("rag" in use_for_lower or "prompt" in use_for_lower):
            return False, "use_for does not include rag or prompt"

        # 3. ideal_response and user_text must not be empty
        ideal_response = self._get_field(example, "ideal_response")
        user_text = self._get_field(example, "user_text")
        if not ideal_response or not str(ideal_response).strip():
            return False, "ideal_response empty"
        if not user_text or not str(user_text).strip():
            return False, "user_text empty"

        # 4. compliance checks on labels
        labels = self._get_field(example, "labels") or {}
        if labels.get("compliance_pass") is False:
            return False, "compliance pass is false"

        compliance_risk = str(labels.get("compliance_risk", "none")).lower()
        if compliance_risk in ("medium", "high", "critical"):
            return False, f"compliance risk {compliance_risk}"

        # 5. sensitive data detection
        _, user_text_redacted_count = redact_text(user_text)
        _, ideal_response_redacted_count = redact_text(ideal_response)
        
        bad_response = self._get_field(example, "bad_response")
        bad_response_redacted_count = 0
        if bad_response:
            _, bad_response_redacted_count = redact_text(bad_response)

        if user_text_redacted_count > 0 or ideal_response_redacted_count > 0 or bad_response_redacted_count > 0:
            return False, "obvious sensitive data is present"

        # 6. ideal_response length check for spoken RAG context
        # Skip if ideal_response exceeds 500 characters
        if len(ideal_response) > 500:
            return False, "ideal_response is too long for spoken RAG context"

        # 7. ideal_response asks multiple questions
        # Count "?" characters. If > 1 and labels["allow_multiple_questions"] is not True, it's ineligible
        if ideal_response.count("?") > 1 and not labels.get("allow_multiple_questions") is True:
            return False, "ideal_response asks multiple questions"

        return True, "eligible"

    def training_example_to_document(self, example: Any) -> Document | None:
        """Convert an eligible training example into a Document."""
        eligible, reason = self.is_example_eligible_for_rag(example)
        if not eligible:
            return None

        example_id = self._get_field(example, "id")
        stage = self._get_field(example, "stage", "unknown")
        user_text = self._get_field(example, "user_text")
        ideal_response = self._get_field(example, "ideal_response")
        bad_response = self._get_field(example, "bad_response")
        labels = self._get_field(example, "labels") or {}
        approved_by = self._get_field(example, "approved_by")
        approved_at = self._get_field(example, "approved_at")
        use_for = self._get_field(example, "use_for") or []
        source_id = self._get_field(example, "source_id")
        call_id = self._get_field(example, "call_id")

        # Objection type
        objection_type = labels.get("objection_type") or labels.get("objection")

        # Format Document content
        content_parts = [
            "Training Example: Final Expense Call Handling",
            "",
            f"Stage: {stage}"
        ]
        if objection_type:
            content_parts.append(f"Objection Type: {objection_type}")
        content_parts.extend([
            "Prospect says:",
            f'"{user_text}"',
            "",
            "Approved Dana response:",
            f'"{ideal_response}"',
            "",
            "Usage Notes:",
            "- Use this as a reviewed example of how Dana should handle this situation.",
            "- Keep the response short.",
            "- Ask one question maximum.",
            "- Do not quote prices.",
            "- Do not claim approval.",
            "- Do not claim to be licensed.",
            "- Do not claim to be human.",
            "- Transfer only after explicit consent."
        ])

        if bad_response:
            content_parts.extend([
                "",
                "Anti-pattern to avoid:",
                f'"{bad_response}"'
            ])

        content = "\n".join(content_parts)

        # Quality score
        quality_score = None
        quality_val = labels.get("human_style_score") or labels.get("sales_quality_score")
        if quality_val is not None:
            try:
                quality_score = float(quality_val)
            except (ValueError, TypeError):
                pass

        # Compliance priority
        compliance_priority = False
        if stage == "compliance" or labels.get("compliance_priority") is True or labels.get("is_compliance_rule") is True:
            compliance_priority = True

        # Tags
        tags = ["training_example", "approved"]
        if stage:
            tags.append(stage)
        if objection_type:
            tags.append(objection_type)
        tags.append("compliance_safe")

        # Build metadata
        metadata = {
            "training_example_id": example_id,
            "human_review_item_id": labels.get("human_review_item_id"),
            "payload_hash": labels.get("payload_hash"),
            "source_id": source_id,
            "call_id": call_id,
            "stage": stage,
            "objection_type": objection_type,
            "compliance_pass": labels.get("compliance_pass"),
            "compliance_risk": labels.get("compliance_risk"),
            "approved_by": approved_by,
            "approved_at": str(approved_at) if approved_at else None,
            "use_for": use_for,
            "created_from": "training_rag_builder",
            "builder_version": "training-rag-v1",
            "tags": tags
        }

        # Deterministic Document ID
        doc_id = f"training_example:{example_id}"

        return Document(
            id=doc_id,
            content=content,
            source="training_example",
            source_id=source_id,
            source_type="training_example",
            topic=objection_type if objection_type else (stage if stage else "training_example"),
            call_stage=stage,
            doc_type="training_example",
            approved=True,
            quality_score=quality_score,
            compliance_priority=compliance_priority,
            version="training-rag-v1",
            metadata=metadata
        )

    async def build_from_training_example(self, example_id: str, dry_run: bool = False) -> TrainingRagBuildResult:
        """Build and upsert a single training example into the RAG vector store."""
        result = TrainingRagBuildResult()
        result.total_training_examples_scanned = 1

        example_dict = await self.repository.get_training_example(example_id)
        if not example_dict:
            result.skipped_examples = 1
            result.skipped_reasons["not found"] = 1
            return result

        eligible, reason = self.is_example_eligible_for_rag(example_dict)
        if not eligible:
            result.skipped_examples = 1
            result.skipped_reasons[reason] = 1
            return result

        result.eligible_examples = 1

        # Convert to Document
        doc = self.training_example_to_document(example_dict)
        if not doc:
            result.skipped_examples = 1
            result.skipped_reasons["failed to build document"] = 1
            return result

        result.documents_created = 1

        # Generate embedding
        doc.embedding = self.embedding_provider.embed(doc.content)

        if not dry_run:
            # Write to vector store
            self.vector_store.add(doc)
            result.documents_upserted = 1

            # Update labels with RAG metadata
            labels = example_dict.setdefault("labels", {})
            labels["rag_document_id"] = doc.id
            labels["rag_built_at"] = datetime.now(timezone.utc).isoformat()
            labels["rag_builder_version"] = "training-rag-v1"
            labels["rag_doc_type"] = "training_example"
            await self.repository.save_training_example(**example_dict)

        result.document_ids.append(doc.id)

        try:
            result.vector_store_count = self.vector_store.count()
        except Exception:
            pass

        return result

    async def build_from_approved_examples(
        self,
        limit: int | None = None,
        dry_run: bool = False,
        approved_only: bool = True,
    ) -> TrainingRagBuildResult:
        """Scan, filter, and convert approved training examples to RAG documents."""
        result = TrainingRagBuildResult()

        scan_limit = limit if limit is not None else 1000
        examples = await self.repository.list_recent_training_examples(limit=scan_limit)

        result.total_training_examples_scanned = len(examples)

        for example_dict in examples:
            eligible, reason = self.is_example_eligible_for_rag(example_dict)
            
            # If approved_only is True, we enforce that they must be eligible.
            # In either case, we skip if they fail eligibility rules.
            if not eligible:
                result.skipped_examples += 1
                result.skipped_reasons[reason] = result.skipped_reasons.get(reason, 0) + 1
                continue

            result.eligible_examples += 1

            # Convert to Document
            doc = self.training_example_to_document(example_dict)
            if not doc:
                result.skipped_examples += 1
                result.skipped_reasons["failed to build document"] = result.skipped_reasons.get("failed to build document", 0) + 1
                continue

            result.documents_created += 1

            # Generate embedding
            doc.embedding = self.embedding_provider.embed(doc.content)

            if not dry_run:
                # Write to vector store
                self.vector_store.add(doc)
                result.documents_upserted += 1

                # Update labels with RAG metadata
                labels = example_dict.setdefault("labels", {})
                labels["rag_document_id"] = doc.id
                labels["rag_built_at"] = datetime.now(timezone.utc).isoformat()
                labels["rag_builder_version"] = "training-rag-v1"
                labels["rag_doc_type"] = "training_example"
                await self.repository.save_training_example(**example_dict)

            result.document_ids.append(doc.id)

        try:
            result.vector_store_count = self.vector_store.count()
        except Exception:
            pass

        return result
