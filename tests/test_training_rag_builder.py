"""Tests for RAG retrieval document builder from approved TrainingExamples."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
import pytest

from storage.repository import Repository
from rag.document import Document
from rag.embeddings import DeterministicFallbackProvider
from rag.vector_store import JsonlVectorStore
from training.rag_builder import TrainingRagDocumentBuilder, TrainingRagBuildResult


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def repo(temp_dir):
    """Return a Repository backed by a temporary JsonlStore."""
    return Repository(data_dir=temp_dir)


@pytest.fixture
def vector_store(temp_dir):
    """Return a JsonlVectorStore in a temporary directory."""
    path = temp_dir / "test_vector_index.jsonl"
    return JsonlVectorStore(path=str(path))


@pytest.fixture
def embedding_provider():
    """Return a DeterministicFallbackProvider."""
    return DeterministicFallbackProvider(dimensions=128)


@pytest.fixture
def builder(repo, vector_store, embedding_provider):
    """Return a TrainingRagDocumentBuilder using temp stores."""
    return TrainingRagDocumentBuilder(
        repository=repo,
        vector_store=vector_store,
        embedding_provider=embedding_provider,
    )


@pytest.mark.asyncio
async def test_converts_approved_training_example_to_document(builder):
    """1. Create approved TrainingExample with use_for containing rag and verify conversion."""
    example = {
        "id": f"ex-{uuid.uuid4()}",
        "source_id": "src-123",
        "call_id": "call-456",
        "stage": "interest_check",
        "user_text": "Is this a real person?",
        "ideal_response": "This is Alex with American Beneficiary.",
        "bad_response": "Yes, I am a real human assistant.",
        "labels": {
            "objection_type": "are_you_real",
            "compliance_pass": True,
            "compliance_risk": "low",
            "human_review_item_id": "review-789",
            "payload_hash": "hash-xyz",
            "human_style_score": 9.5
        },
        "approved_by": "Jimmy",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "use_for": ["prompt", "rag"]
    }

    # Verify eligibility
    eligible, reason = builder.is_example_eligible_for_rag(example)
    assert eligible, f"Example should be eligible, but got: {reason}"

    doc = builder.training_example_to_document(example)
    assert doc is not None
    assert doc.id == f"training_example:{example['id']}"
    assert doc.source == "training_example"
    assert doc.source_id == "src-123"
    assert doc.source_type == "training_example"
    assert doc.topic == "are_you_real"
    assert doc.call_stage == "interest_check"
    assert doc.doc_type == "training_example"
    assert doc.approved is True
    assert doc.quality_score == 9.5
    assert doc.compliance_priority is False
    assert doc.version == "training-rag-v1"

    # Verify content formatting
    assert "Training Example: Final Expense Call Handling" in doc.content
    assert "Stage: interest_check" in doc.content
    assert "Objection Type: are_you_real" in doc.content
    assert "Prospect says:\n\"Is this a real person?\"" in doc.content
    assert "Approved Dana response:\n\"This is Alex with American Beneficiary.\"" in doc.content
    assert "Anti-pattern to avoid:\n\"Yes, I am a real human assistant.\"" in doc.content

    # Verify metadata fields
    meta = doc.metadata
    assert meta["training_example_id"] == example["id"]
    assert meta["human_review_item_id"] == "review-789"
    assert meta["payload_hash"] == "hash-xyz"
    assert meta["stage"] == "interest_check"
    assert meta["objection_type"] == "are_you_real"
    assert meta["approved_by"] == "Jimmy"
    assert meta["created_from"] == "training_rag_builder"
    assert "training_example" in meta["tags"]
    assert "approved" in meta["tags"]
    assert "compliance_safe" in meta["tags"]


@pytest.mark.asyncio
async def test_skips_unapproved_training_example(builder):
    """2. Verify that unapproved training examples are skipped."""
    example = {
        "id": f"ex-{uuid.uuid4()}",
        "source_id": "src-123",
        "stage": "interest_check",
        "user_text": "Is this real?",
        "ideal_response": "Yes it is.",
        "labels": {"compliance_pass": True, "compliance_risk": "low"},
        "use_for": ["rag"],
        "approved_by": None,  # Not approved
        "approved_at": None
    }
    eligible, reason = builder.is_example_eligible_for_rag(example)
    assert not eligible
    assert reason == "not approved"


@pytest.mark.asyncio
async def test_skips_non_rag_use_for(builder):
    """3. Verify that training examples not intended for RAG are skipped."""
    example = {
        "id": f"ex-{uuid.uuid4()}",
        "source_id": "src-123",
        "stage": "interest_check",
        "user_text": "Is this real?",
        "ideal_response": "Yes it is.",
        "labels": {"compliance_pass": True, "compliance_risk": "low"},
        "approved_by": "Jimmy",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "use_for": ["fine_tune"]  # Lacks rag or prompt
    }
    eligible, reason = builder.is_example_eligible_for_rag(example)
    assert not eligible
    assert reason == "use_for does not include rag or prompt"


@pytest.mark.asyncio
async def test_skips_compliance_risk_medium_high_critical(builder):
    """4. Verify that compliance risk levels of medium, high, and critical are skipped."""
    base_example = {
        "id": f"ex-{uuid.uuid4()}",
        "source_id": "src-123",
        "stage": "interest_check",
        "user_text": "Is this real?",
        "ideal_response": "Yes it is.",
        "approved_by": "Jimmy",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "use_for": ["rag"]
    }

    # Medium risk
    ex_medium = dict(base_example)
    ex_medium["labels"] = {"compliance_pass": True, "compliance_risk": "medium"}
    eligible, reason = builder.is_example_eligible_for_rag(ex_medium)
    assert not eligible
    assert "compliance risk medium" in reason

    # High risk
    ex_high = dict(base_example)
    ex_high["labels"] = {"compliance_pass": True, "compliance_risk": "high"}
    eligible, reason = builder.is_example_eligible_for_rag(ex_high)
    assert not eligible
    assert "compliance risk high" in reason

    # Critical risk
    ex_critical = dict(base_example)
    ex_critical["labels"] = {"compliance_pass": True, "compliance_risk": "critical"}
    eligible, reason = builder.is_example_eligible_for_rag(ex_critical)
    assert not eligible
    assert "compliance risk critical" in reason


@pytest.mark.asyncio
async def test_skips_empty_user_text_or_ideal_response(builder):
    """5. Verify empty user_text or ideal_response is skipped."""
    base_example = {
        "id": f"ex-{uuid.uuid4()}",
        "source_id": "src-123",
        "stage": "interest_check",
        "labels": {"compliance_pass": True, "compliance_risk": "low"},
        "approved_by": "Jimmy",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "use_for": ["rag"]
    }

    # Empty user_text
    ex_user = dict(base_example)
    ex_user["user_text"] = ""
    ex_user["ideal_response"] = "Response"
    eligible, reason = builder.is_example_eligible_for_rag(ex_user)
    assert not eligible
    assert "user_text empty" in reason

    # Empty ideal_response
    ex_ideal = dict(base_example)
    ex_ideal["user_text"] = "Query"
    ex_ideal["ideal_response"] = "   "
    eligible, reason = builder.is_example_eligible_for_rag(ex_ideal)
    assert not eligible
    assert "ideal_response empty" in reason


@pytest.mark.asyncio
async def test_skips_sensitive_data(builder):
    """6. Verify that examples containing sensitive data (PII) are skipped."""
    base_example = {
        "id": f"ex-{uuid.uuid4()}",
        "source_id": "src-123",
        "stage": "interest_check",
        "labels": {"compliance_pass": True, "compliance_risk": "low"},
        "approved_by": "Jimmy",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "use_for": ["rag"]
    }

    # Phone number PII
    ex_phone = dict(base_example)
    ex_phone["user_text"] = "My number is 555-123-4567"
    ex_phone["ideal_response"] = "Understood."
    eligible, reason = builder.is_example_eligible_for_rag(ex_phone)
    assert not eligible
    assert "obvious sensitive data" in reason

    # SSN PII
    ex_ssn = dict(base_example)
    ex_ssn["user_text"] = "My query"
    ex_ssn["ideal_response"] = "SSN is 123-45-6789"
    eligible, reason = builder.is_example_eligible_for_rag(ex_ssn)
    assert not eligible
    assert "obvious sensitive data" in reason


@pytest.mark.asyncio
async def test_generates_embedding_and_upserts_jsonl(builder, vector_store):
    """7. Verify that building generates an embedding and upserts to the vector store."""
    example = {
        "id": f"ex-{uuid.uuid4()}",
        "source_id": "src-123",
        "stage": "opening",
        "user_text": "Hello",
        "ideal_response": "Hello, American Beneficiary.",
        "labels": {"compliance_pass": True, "compliance_risk": "low"},
        "approved_by": "Jimmy",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "use_for": ["rag"]
    }

    await builder.repository.save_training_example(**example)
    assert vector_store.count() == 0

    res = await builder.build_from_training_example(example["id"])
    assert res.documents_created == 1
    assert res.documents_upserted == 1
    assert vector_store.count() == 1

    # Verify document contains embedding
    search_res = vector_store.search(query_embedding=[0.1] * 128)
    assert len(search_res) == 1
    assert search_res[0].document.id == f"training_example:{example['id']}"
    assert search_res[0].document.embedding is not None
    assert len(search_res[0].document.embedding) == 128


@pytest.mark.asyncio
async def test_upsert_is_idempotent(builder, vector_store):
    """8. Verify that building the same example twice is idempotent."""
    example = {
        "id": f"ex-{uuid.uuid4()}",
        "source_id": "src-123",
        "stage": "opening",
        "user_text": "Hello",
        "ideal_response": "Hello, American Beneficiary.",
        "labels": {"compliance_pass": True, "compliance_risk": "low"},
        "approved_by": "Jimmy",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "use_for": ["rag"]
    }

    await builder.repository.save_training_example(**example)

    # First run
    res1 = await builder.build_from_training_example(example["id"])
    assert res1.documents_upserted == 1
    assert vector_store.count() == 1

    # Second run
    res2 = await builder.build_from_training_example(example["id"])
    assert res2.documents_upserted == 1
    assert vector_store.count() == 1  # Count should stay 1


@pytest.mark.asyncio
async def test_updates_training_example_labels_with_rag_metadata(builder):
    """9. Verify that building a document updates the source training example labels."""
    example = {
        "id": f"ex-{uuid.uuid4()}",
        "source_id": "src-123",
        "stage": "opening",
        "user_text": "Hello",
        "ideal_response": "Hello, American Beneficiary.",
        "labels": {"compliance_pass": True, "compliance_risk": "low", "custom_tag": "test"},
        "approved_by": "Jimmy",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "use_for": ["rag"]
    }

    await builder.repository.save_training_example(**example)

    # Verify no RAG metadata initially
    init_ex = await builder.repository.get_training_example(example["id"])
    assert "rag_document_id" not in init_ex["labels"]

    await builder.build_from_training_example(example["id"])

    # Verify labels have been updated
    updated_ex = await builder.repository.get_training_example(example["id"])
    labels = updated_ex["labels"]
    assert labels["custom_tag"] == "test"  # Preserved existing labels
    assert labels["rag_document_id"] == f"training_example:{example['id']}"
    assert "rag_built_at" in labels
    assert labels["rag_builder_version"] == "training-rag-v1"
    assert labels["rag_doc_type"] == "training_example"
    # Ensure approved stamps are untouched
    assert updated_ex["approved_by"] == "Jimmy"


@pytest.mark.asyncio
async def test_build_from_training_example(builder, vector_store):
    """10. Verify building one specific example by ID."""
    example = {
        "id": f"ex-{uuid.uuid4()}",
        "source_id": "src-123",
        "stage": "opening",
        "user_text": "Hello",
        "ideal_response": "Hello, American Beneficiary.",
        "labels": {"compliance_pass": True, "compliance_risk": "low"},
        "approved_by": "Jimmy",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "use_for": ["rag"]
    }

    await builder.repository.save_training_example(**example)
    res = await builder.build_from_training_example(example["id"])
    
    assert isinstance(res, TrainingRagBuildResult)
    assert res.total_training_examples_scanned == 1
    assert res.eligible_examples == 1
    assert res.documents_created == 1
    assert res.documents_upserted == 1
    assert f"training_example:{example['id']}" in res.document_ids


@pytest.mark.asyncio
async def test_build_from_approved_examples_limit(builder):
    """11. Verify building multiple approved examples with a scan limit."""
    now_str = datetime.now(timezone.utc).isoformat()
    # Save 3 eligible examples
    for i in range(3):
        ex = {
            "id": f"ex-lim-{i}-{uuid.uuid4()}",
            "source_id": "src-123",
            "stage": "opening",
            "user_text": f"Query {i}",
            "ideal_response": f"Response {i}",
            "labels": {"compliance_pass": True, "compliance_risk": "low"},
            "approved_by": "Jimmy",
            "approved_at": now_str,
            "use_for": ["rag"]
        }
        await builder.repository.save_training_example(**ex)

    # Build with scan limit = 2
    res = await builder.build_from_approved_examples(limit=2)
    assert res.total_training_examples_scanned == 2
    assert res.documents_created == 2


@pytest.mark.asyncio
async def test_dry_run_does_not_write(builder, vector_store):
    """12. Verify that dry_run evaluates eligibility but does not save or update labels."""
    example = {
        "id": f"ex-{uuid.uuid4()}",
        "source_id": "src-123",
        "stage": "opening",
        "user_text": "Hello",
        "ideal_response": "Hello, American Beneficiary.",
        "labels": {"compliance_pass": True, "compliance_risk": "low"},
        "approved_by": "Jimmy",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "use_for": ["rag"]
    }

    await builder.repository.save_training_example(**example)
    assert vector_store.count() == 0

    res = await builder.build_from_training_example(example["id"], dry_run=True)
    assert res.total_training_examples_scanned == 1
    assert res.eligible_examples == 1
    assert res.documents_created == 1
    assert res.documents_upserted == 0  # Did not write to vector store
    assert vector_store.count() == 0

    # Verify labels in repository remain unchanged
    ex = await builder.repository.get_training_example(example["id"])
    assert "rag_document_id" not in ex["labels"]


def test_cli_rebuild_training_rag_all():
    """13. Verify execution of CLI build all mode via subprocess."""
    default_repo = Repository()
    
    unique_id = f"cli_all_{uuid.uuid4()}"
    example = {
        "id": unique_id,
        "source_id": "cli_src",
        "stage": "opening",
        "user_text": "Hello",
        "ideal_response": "Hello, American Beneficiary.",
        "labels": {"compliance_pass": True, "compliance_risk": "low"},
        "approved_by": "Jimmy",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "use_for": ["rag"]
    }
    
    asyncio.run(default_repo.save_training_example(**example))

    try:
        cmd = [
            sys.executable,
            "scripts/rebuild_training_rag.py",
            "--all",
            "--dry-run"  # safe dry-run for CLI integration test
        ]

        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(".").resolve())

        res = subprocess.run(cmd, capture_output=True, text=True, check=True, env=env)

        assert res.returncode == 0
        data = json.loads(res.stdout)
        assert data["total_training_examples_scanned"] > 0
        # The document ID of our newly inserted example must be in the dry-run output
        assert f"training_example:{unique_id}" in data["document_ids"]
    finally:
        pass


def test_cli_rebuild_training_rag_example_id():
    """14. Verify execution of CLI build specific ID mode via subprocess."""
    default_repo = Repository()
    
    unique_id = f"cli_single_{uuid.uuid4()}"
    example = {
        "id": unique_id,
        "source_id": "cli_src",
        "stage": "opening",
        "user_text": "Hello",
        "ideal_response": "Hello, American Beneficiary.",
        "labels": {"compliance_pass": True, "compliance_risk": "low"},
        "approved_by": "Jimmy",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "use_for": ["rag"]
    }
    
    asyncio.run(default_repo.save_training_example(**example))

    try:
        cmd = [
            sys.executable,
            "scripts/rebuild_training_rag.py",
            "--example-id", unique_id,
            "--dry-run"
        ]

        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(".").resolve())

        res = subprocess.run(cmd, capture_output=True, text=True, check=True, env=env)

        assert res.returncode == 0
        data = json.loads(res.stdout)
        assert data["total_training_examples_scanned"] == 1
        assert data["eligible_examples"] == 1
        assert f"training_example:{unique_id}" in data["document_ids"]
    finally:
        pass


@pytest.mark.asyncio
async def test_builder_does_not_create_training_examples_or_review_items(builder):
    """15. Verify that the builder does not create any new TrainingExample or HumanReviewItem records."""
    example = {
        "id": f"ex-{uuid.uuid4()}",
        "source_id": "src-123",
        "stage": "opening",
        "user_text": "Hello",
        "ideal_response": "Hello, American Beneficiary.",
        "labels": {"compliance_pass": True, "compliance_risk": "low"},
        "approved_by": "Jimmy",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "use_for": ["rag"]
    }

    await builder.repository.save_training_example(**example)

    # Initial counts
    ex_list_before = await builder.repository.list_recent_training_examples(limit=100)
    rev_list_before = await builder.repository.list_pending_human_review_items(limit=100)
    ex_count_before = len(ex_list_before)
    rev_count_before = len(rev_list_before)

    # Run the builder
    await builder.build_from_training_example(example["id"])

    # Counts after
    ex_list_after = await builder.repository.list_recent_training_examples(limit=100)
    rev_list_after = await builder.repository.list_pending_human_review_items(limit=100)
    ex_count_after = len(ex_list_after)
    rev_count_after = len(rev_list_after)

    # Verify no new ones are created
    assert ex_count_after == ex_count_before
    assert rev_count_after == rev_count_before
