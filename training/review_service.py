"""Human Review Service for Dana's continuous training workflow.

Exposes listing, approval, rejection, and change request mechanisms
for pending human review items, and generates downstream assets.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from pydantic import BaseModel, Field

from storage.repository import Repository


class ReviewActionResult(BaseModel):
    """Structured result of a review action."""

    item_id: str
    item_type: str
    previous_status: str
    new_status: str
    reviewer: str
    reviewed_at: str
    created_record_type: Optional[str] = None
    created_record_id: Optional[str] = None
    message: str
    warnings: list[str] = Field(default_factory=list)


class HumanReviewService:
    """Service handling the approval gate for training examples, compliance items, and eval cases."""

    def __init__(self, repository: Optional[Repository] = None) -> None:
        self.repository = repository

    async def list_pending_review_items(self, item_type: Optional[str] = None, limit: int = 50) -> list[dict]:
        """List pending human review items, optionally filtered by type."""
        if self.repository is None:
            raise ValueError("Repository is required.")

        if item_type:
            items = await self.repository.query_human_review_items({"status": "pending", "item_type": item_type})
            items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
            return items[:limit]
        else:
            return await self.repository.list_pending_human_review_items(limit=limit)

    async def get_review_item(self, item_id: str) -> dict:
        """Fetch a specific review item by its unique ID."""
        if self.repository is None:
            raise ValueError("Repository is required.")

        item = await self.repository.get_human_review_item(item_id)
        if not item:
            raise ValueError(f"HumanReviewItem not found: {item_id}")
        return item

    async def approve_review_item(self, item_id: str, reviewer: str, review_notes: Optional[str] = None) -> ReviewActionResult:
        """Approves a pending/needs_changes item, creating the relevant downstream assets."""
        if self.repository is None:
            raise ValueError("Repository is required.")

        if not reviewer or not reviewer.strip():
            raise ValueError("Reviewer is required.")

        item = await self.get_review_item(item_id)
        prev_status = item.get("status", "pending")

        if prev_status == "approved":
            raise ValueError("Item is already approved.")
        if prev_status == "rejected":
            raise ValueError("Cannot approve a rejected item.")
        if prev_status not in ("pending", "needs_changes"):
            raise ValueError(f"Cannot approve item with status: {prev_status}")

        item_type = item.get("item_type", "")
        payload = item.get("payload") or {}
        reviewed_at = datetime.now(timezone.utc).isoformat()

        created_record_type = None
        created_record_id = None
        message = f"Item of type '{item_type}' approved successfully."
        warnings = []

        if item_type == "training_example":
            ideal = payload.get("candidate_ideal_response")
            user = payload.get("user_text")
            if not ideal or not ideal.strip():
                raise ValueError("candidate_ideal_response must not be empty.")
            if not user or not user.strip():
                raise ValueError("user_text must not be empty.")

            labels = payload.get("labels") or {}
            comp_risk = labels.get("compliance_risk", "none")
            if comp_risk in ("medium", "high", "critical"):
                raise ValueError(f"Compliance risk is '{comp_risk}'; approval is refused.")

            # Filter recommended use cases for fine-tuning
            use_for = payload.get("recommended_use_for") or ["prompt", "rag", "eval"]
            if "fine_tune" in use_for:
                if not labels.get("fine_tune_eligible") is True:
                    use_for = [u for u in use_for if u != "fine_tune"]

            # Create TrainingExample record
            merged_labels = dict(labels)
            merged_labels["human_review_item_id"] = item_id
            merged_labels["payload_hash"] = payload.get("payload_hash")
            if review_notes:
                merged_labels["approved_review_notes"] = review_notes

            example_id = await self.repository.save_training_example(
                source_id=payload.get("source_id"),
                call_id=payload.get("call_id"),
                stage=payload.get("stage", "unknown"),
                user_text=user,
                ideal_response=ideal,
                bad_response=payload.get("bad_response"),
                labels=merged_labels,
                approved_by=reviewer,
                approved_at=reviewed_at,
                use_for=use_for,
            )

            payload["approved_training_example_id"] = example_id
            payload["approved_at"] = reviewed_at
            payload["approved_by"] = reviewer
            created_record_type = "training_example"
            created_record_id = example_id

        elif item_type == "eval_case":
            prospect = payload.get("prospect_utterance")
            behavior = payload.get("expected_behavior")
            severity = payload.get("severity")
            if not prospect or not prospect.strip():
                raise ValueError("prospect_utterance must not be empty.")
            if not behavior or not behavior.strip():
                raise ValueError("expected_behavior must not be empty.")
            if severity not in ("low", "medium", "high", "critical"):
                raise ValueError(f"Severity must be one of low, medium, high, critical. Got: {severity}")

            # Create EvalCase record
            case_id = await self.repository.save_eval_case(
                stage=payload.get("stage", "unknown"),
                prospect_utterance=prospect,
                expected_behavior=behavior,
                must_include=payload.get("must_include") or [],
                must_not_include=payload.get("must_not_include") or [],
                expected_tool=payload.get("expected_tool"),
                severity=severity,
            )

            payload["approved_eval_case_id"] = case_id
            payload["approved_at"] = reviewed_at
            payload["approved_by"] = reviewer
            created_record_type = "eval_case"
            created_record_id = case_id

        elif item_type == "failure_example":
            payload["failure_confirmed"] = True
            payload["approved_at"] = reviewed_at
            payload["approved_by"] = reviewer
            message = "Failure pattern approved for future evaluation/prompt analysis; no downstream record created."

        elif item_type == "compliance_review":
            payload["compliance_confirmed"] = True
            payload["approved_at"] = reviewed_at
            payload["approved_by"] = reviewer
            if review_notes:
                payload["reviewer_action_taken"] = review_notes

        elif item_type == "rag_doc":
            payload["rag_doc_approved"] = True
            payload["approved_at"] = reviewed_at
            payload["approved_by"] = reviewer
            message = "RAG document approved for future rebuild. No index rebuild performed."

        elif item_type == "prompt_patch":
            payload["prompt_patch_approved"] = True
            payload["approved_at"] = reviewed_at
            payload["approved_by"] = reviewer
            message = "Prompt patch approved for later application. Prompt file was not modified."

        else:
            # Unknown item_type approved as review-only
            warnings.append("Unknown item_type approved as review-only item; no downstream record created.")
            payload["approved_at"] = reviewed_at
            payload["approved_by"] = reviewer

        # Append to audit history
        history = payload.setdefault("review_history", [])
        history.append({
            "action": "approved",
            "reviewer": reviewer,
            "review_notes": review_notes or "",
            "reviewed_at": reviewed_at,
            "previous_status": prev_status,
            "new_status": "approved"
        })

        # Update HumanReviewItem
        await self.repository.save_human_review_item(
            id=item["id"],
            item_type=item_type,
            payload=payload,
            status="approved",
            reviewer=reviewer,
            review_notes=review_notes,
            created_at=item["created_at"],
            reviewed_at=reviewed_at
        )

        return ReviewActionResult(
            item_id=item_id,
            item_type=item_type,
            previous_status=prev_status,
            new_status="approved",
            reviewer=reviewer,
            reviewed_at=reviewed_at,
            created_record_type=created_record_type,
            created_record_id=created_record_id,
            message=message,
            warnings=warnings
        )

    async def reject_review_item(self, item_id: str, reviewer: str, review_notes: str) -> ReviewActionResult:
        """Rejects an item, setting its status to rejected and saving reviewer reason."""
        if self.repository is None:
            raise ValueError("Repository is required.")

        if not reviewer or not reviewer.strip():
            raise ValueError("Reviewer is required.")

        if not review_notes or not review_notes.strip():
            raise ValueError("Review notes are required for rejection or changes requested.")

        item = await self.get_review_item(item_id)
        prev_status = item.get("status", "pending")

        if prev_status == "approved":
            raise ValueError("Cannot reject an approved item.")
        if prev_status == "rejected":
            raise ValueError("Item is already rejected.")
        if prev_status not in ("pending", "needs_changes"):
            raise ValueError(f"Cannot reject item with status: {prev_status}")

        payload = item.get("payload") or {}
        reviewed_at = datetime.now(timezone.utc).isoformat()

        payload["rejected_at"] = reviewed_at
        payload["rejected_by"] = reviewer
        payload["rejection_reason"] = review_notes

        # Append to audit history
        history = payload.setdefault("review_history", [])
        history.append({
            "action": "rejected",
            "reviewer": reviewer,
            "review_notes": review_notes,
            "reviewed_at": reviewed_at,
            "previous_status": prev_status,
            "new_status": "rejected"
        })

        await self.repository.save_human_review_item(
            id=item["id"],
            item_type=item.get("item_type", ""),
            payload=payload,
            status="rejected",
            reviewer=reviewer,
            review_notes=review_notes,
            created_at=item["created_at"],
            reviewed_at=reviewed_at
        )

        return ReviewActionResult(
            item_id=item_id,
            item_type=item.get("item_type", ""),
            previous_status=prev_status,
            new_status="rejected",
            reviewer=reviewer,
            reviewed_at=reviewed_at,
            message="Item rejected successfully.",
            warnings=[]
        )

    async def request_changes(self, item_id: str, reviewer: str, review_notes: str) -> ReviewActionResult:
        """Moves a pending item to needs_changes status with instructions on what to fix."""
        if self.repository is None:
            raise ValueError("Repository is required.")

        if not reviewer or not reviewer.strip():
            raise ValueError("Reviewer is required.")

        if not review_notes or not review_notes.strip():
            raise ValueError("Review notes are required for rejection or changes requested.")

        item = await self.get_review_item(item_id)
        prev_status = item.get("status", "pending")

        if prev_status == "approved" or prev_status == "rejected":
            raise ValueError(f"Cannot request changes on item with status: {prev_status}")
        if prev_status != "pending":
            raise ValueError(f"Cannot request changes on item with status: {prev_status}")

        payload = item.get("payload") or {}
        reviewed_at = datetime.now(timezone.utc).isoformat()

        payload["needs_changes_at"] = reviewed_at
        payload["needs_changes_by"] = reviewer
        payload["change_request_notes"] = review_notes

        # Append to audit history
        history = payload.setdefault("review_history", [])
        history.append({
            "action": "needs_changes",
            "reviewer": reviewer,
            "review_notes": review_notes,
            "reviewed_at": reviewed_at,
            "previous_status": prev_status,
            "new_status": "needs_changes"
        })

        await self.repository.save_human_review_item(
            id=item["id"],
            item_type=item.get("item_type", ""),
            payload=payload,
            status="needs_changes",
            reviewer=reviewer,
            review_notes=review_notes,
            created_at=item["created_at"],
            reviewed_at=reviewed_at
        )

        return ReviewActionResult(
            item_id=item_id,
            item_type=item.get("item_type", ""),
            previous_status=prev_status,
            new_status="needs_changes",
            reviewer=reviewer,
            reviewed_at=reviewed_at,
            message="Item marked as needing changes.",
            warnings=[]
        )
