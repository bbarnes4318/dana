"""Tests for human review approval service and CLI review script."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
import pytest

from storage.repository import Repository
from training.review_service import HumanReviewService, ReviewActionResult


@pytest.fixture
def repo(tmp_path):
    """Return a Repository backed by a temporary JsonlStore."""
    return Repository(data_dir=tmp_path)


@pytest.fixture
def service(repo):
    """Return a HumanReviewService using a temporary Repository."""
    return HumanReviewService(repository=repo)


@pytest.mark.asyncio
async def test_list_pending_review_items(service, repo):
    """Verify listing and filtering of pending human review items."""
    item1_id = await repo.save_human_review_item(
        item_type="training_example",
        payload={"why_this_matters": "Item 1"},
        status="pending"
    )
    item2_id = await repo.save_human_review_item(
        item_type="eval_case",
        payload={"why_this_matters": "Item 2"},
        status="pending"
    )
    item3_id = await repo.save_human_review_item(
        item_type="training_example",
        payload={"why_this_matters": "Item 3"},
        status="approved"
    )

    pending = await service.list_pending_review_items()
    assert len(pending) == 2
    assert pending[0]["id"] == item2_id
    assert pending[1]["id"] == item1_id

    te_pending = await service.list_pending_review_items(item_type="training_example")
    assert len(te_pending) == 1
    assert te_pending[0]["id"] == item1_id

    limited = await service.list_pending_review_items(limit=1)
    assert len(limited) == 1
    assert limited[0]["id"] == item2_id


@pytest.mark.asyncio
async def test_approve_training_example_creates_training_example(service, repo):
    """Verify that approving a training_example creates a TrainingExample record."""
    item_id = await repo.save_human_review_item(
        item_type="training_example",
        payload={
            "source_id": "source_123",
            "call_id": "call_456",
            "stage": "interest_check",
            "user_text": "Is this a real person?",
            "candidate_ideal_response": "This is Alex with American Beneficiary.",
            "bad_response": None,
            "labels": {"compliance_risk": "none", "sentiment": "neutral"},
            "recommended_use_for": ["prompt", "rag"],
            "payload_hash": "hash_abc123"
        },
        status="pending"
    )

    res = await service.approve_review_item(item_id, reviewer="Jimmy", review_notes="Looks very good.")
    
    assert isinstance(res, ReviewActionResult)
    assert res.new_status == "approved"
    assert res.created_record_type == "training_example"
    assert res.created_record_id is not None

    item = await service.get_review_item(item_id)
    assert item["status"] == "approved"
    assert item["reviewer"] == "Jimmy"
    assert item["review_notes"] == "Looks very good."
    assert item["payload"]["approved_training_example_id"] == res.created_record_id
    assert "approved_at" in item["payload"]
    assert item["payload"]["approved_by"] == "Jimmy"

    example = await repo.get_training_example(res.created_record_id)
    assert example is not None
    assert example["source_id"] == "source_123"
    assert example["call_id"] == "call_456"
    assert example["stage"] == "interest_check"
    assert example["user_text"] == "Is this a real person?"
    assert example["ideal_response"] == "This is Alex with American Beneficiary."
    assert example["bad_response"] is None
    assert example["approved_by"] == "Jimmy"
    assert example["use_for"] == ["prompt", "rag"]
    assert example["labels"]["human_review_item_id"] == item_id
    assert example["labels"]["payload_hash"] == "hash_abc123"
    assert example["labels"]["approved_review_notes"] == "Looks very good."

    with pytest.raises(ValueError, match="already approved"):
        await service.approve_review_item(item_id, reviewer="Jimmy")


@pytest.mark.asyncio
async def test_approve_training_example_rejects_compliance_risk(service, repo):
    """Verify that approving a training_example with high compliance risk raises ValueError."""
    item_id = await repo.save_human_review_item(
        item_type="training_example",
        payload={
            "source_id": "source_123",
            "stage": "interest_check",
            "user_text": "Are you licensed?",
            "candidate_ideal_response": "Yes, I am a licensed final expense agent.",
            "labels": {"compliance_risk": "critical"},
            "payload_hash": "hash_risk"
        },
        status="pending"
    )

    with pytest.raises(ValueError, match="[Cc]ompliance risk"):
        await service.approve_review_item(item_id, reviewer="Jimmy")

    examples = await repo.query_training_examples({})
    assert len(examples) == 0


@pytest.mark.asyncio
async def test_approve_training_example_removes_fine_tune_unless_eligible(service, repo):
    """Verify fine_tune is removed from use_for list unless fine_tune_eligible is true."""
    # Scenario A: fine_tune_eligible is missing
    item_id_a = await repo.save_human_review_item(
        item_type="training_example",
        payload={
            "source_id": "source_123",
            "stage": "opening",
            "user_text": "Hi",
            "candidate_ideal_response": "Hi, this is Alex.",
            "labels": {"compliance_risk": "none"},
            "recommended_use_for": ["prompt", "fine_tune"],
            "payload_hash": "hash_a"
        },
        status="pending"
    )

    res_a = await service.approve_review_item(item_id_a, reviewer="Jimmy")
    example_a = await repo.get_training_example(res_a.created_record_id)
    assert "fine_tune" not in example_a["use_for"]

    # Scenario B: fine_tune_eligible is explicitly true
    item_id_b = await repo.save_human_review_item(
        item_type="training_example",
        payload={
            "source_id": "source_123",
            "stage": "opening",
            "user_text": "Hi",
            "candidate_ideal_response": "Hi, this is Alex.",
            "labels": {"compliance_risk": "none", "fine_tune_eligible": True},
            "recommended_use_for": ["prompt", "fine_tune"],
            "payload_hash": "hash_b"
        },
        status="pending"
    )

    res_b = await service.approve_review_item(item_id_b, reviewer="Jimmy")
    example_b = await repo.get_training_example(res_b.created_record_id)
    assert "fine_tune" in example_b["use_for"]


@pytest.mark.asyncio
async def test_approve_eval_case_creates_eval_case(service, repo):
    """Verify that approving an eval_case creates an EvalCase record."""
    item_id = await repo.save_human_review_item(
        item_type="eval_case",
        payload={
            "stage": "dnc",
            "prospect_utterance": "Don't call me again.",
            "expected_behavior": "Say goodbye politely and end call.",
            "must_include": ["goodbye", "apologize"],
            "must_not_include": ["licensed"],
            "expected_tool": None,
            "severity": "high"
        },
        status="pending"
    )

    res = await service.approve_review_item(item_id, reviewer="Jimmy")
    assert res.new_status == "approved"
    assert res.created_record_type == "eval_case"

    item = await service.get_review_item(item_id)
    assert item["status"] == "approved"
    assert item["payload"]["approved_eval_case_id"] == res.created_record_id

    case = await repo.get_eval_case(res.created_record_id)
    assert case is not None
    assert case["stage"] == "dnc"
    assert case["prospect_utterance"] == "Don't call me again."
    assert case["expected_behavior"] == "Say goodbye politely and end call."
    assert case["must_include"] == ["goodbye", "apologize"]
    assert case["must_not_include"] == ["licensed"]
    assert case["expected_tool"] is None
    assert case["severity"] == "high"


@pytest.mark.asyncio
async def test_approve_failure_example_review_only(service, repo):
    """Verify that failure_examples are approved as review-only items."""
    item_id = await repo.save_human_review_item(
        item_type="failure_example",
        payload={
            "source_id": "source_123",
            "user_text": "How much is it?",
            "bad_response": "It only costs 50 dollars a month."
        },
        status="pending"
    )

    res = await service.approve_review_item(item_id, reviewer="Jimmy")
    assert res.new_status == "approved"
    assert res.created_record_type is None
    assert res.created_record_id is None
    assert "Failure pattern approved" in res.message

    item = await service.get_review_item(item_id)
    assert item["status"] == "approved"
    assert item["payload"]["failure_confirmed"] is True

    examples = await repo.query_training_examples({})
    cases = await repo.query_eval_cases({})
    assert len(examples) == 0
    assert len(cases) == 0


@pytest.mark.asyncio
async def test_approve_compliance_review_review_only(service, repo):
    """Verify that compliance_reviews are approved as review-only items."""
    item_id = await repo.save_human_review_item(
        item_type="compliance_review",
        payload={
            "source_id": "source_123",
            "compliance_risk": "high"
        },
        status="pending"
    )

    res = await service.approve_review_item(item_id, reviewer="Jimmy", review_notes="Action taken.")
    assert res.new_status == "approved"
    assert res.created_record_type is None

    item = await service.get_review_item(item_id)
    assert item["status"] == "approved"
    assert item["payload"]["compliance_confirmed"] is True
    assert item["payload"]["reviewer_action_taken"] == "Action taken."


@pytest.mark.asyncio
async def test_reject_item_requires_notes(service, repo):
    """Verify rejection raises ValueError if notes are empty."""
    item_id = await repo.save_human_review_item(
        item_type="training_example",
        payload={},
        status="pending"
    )

    with pytest.raises(ValueError, match="Review notes are required"):
        await service.reject_review_item(item_id, reviewer="Jimmy", review_notes="")


@pytest.mark.asyncio
async def test_reject_item_sets_rejected_status(service, repo):
    """Verify reject transitions status and sets payload metadata."""
    item_id = await repo.save_human_review_item(
        item_type="training_example",
        payload={},
        status="pending"
    )

    res = await service.reject_review_item(item_id, reviewer="Jimmy", review_notes="Too long.")
    assert res.new_status == "rejected"

    item = await service.get_review_item(item_id)
    assert item["status"] == "rejected"
    assert item["reviewer"] == "Jimmy"
    assert item["review_notes"] == "Too long."
    assert item["payload"]["rejected_by"] == "Jimmy"
    assert item["payload"]["rejection_reason"] == "Too long."
    
    # Assert review_history is appended
    history = item["payload"].get("review_history") or []
    assert len(history) == 1
    assert history[0]["action"] == "rejected"
    assert history[0]["reviewer"] == "Jimmy"
    assert history[0]["review_notes"] == "Too long."


@pytest.mark.asyncio
async def test_request_changes_sets_needs_changes(service, repo):
    """Verify request_changes transitions status and sets payload metadata."""
    item_id = await repo.save_human_review_item(
        item_type="training_example",
        payload={},
        status="pending"
    )

    res = await service.request_changes(item_id, reviewer="Jimmy", review_notes="Fix typo.")
    assert res.new_status == "needs_changes"

    item = await service.get_review_item(item_id)
    assert item["status"] == "needs_changes"
    assert item["payload"]["needs_changes_by"] == "Jimmy"
    assert item["payload"]["change_request_notes"] == "Fix typo."

    # Assert review_history is appended
    history = item["payload"].get("review_history") or []
    assert len(history) == 1
    assert history[0]["action"] == "needs_changes"
    assert history[0]["reviewer"] == "Jimmy"


@pytest.mark.asyncio
async def test_cannot_approve_rejected_item(service, repo):
    """Verify that already rejected items cannot be approved."""
    item_id = await repo.save_human_review_item(
        item_type="training_example",
        payload={},
        status="rejected"
    )

    with pytest.raises(ValueError, match="already rejected|rejected"):
        await service.approve_review_item(item_id, reviewer="Jimmy")


@pytest.mark.asyncio
async def test_cannot_reject_approved_item(service, repo):
    """Verify that already approved items cannot be rejected."""
    item_id = await repo.save_human_review_item(
        item_type="training_example",
        payload={},
        status="approved"
    )

    with pytest.raises(ValueError, match="already approved|approved"):
        await service.reject_review_item(item_id, reviewer="Jimmy", review_notes="Reject it.")


@pytest.mark.asyncio
async def test_approve_prompt_patch_does_not_modify_prompt(service, repo):
    """Verify that prompt_patch approval is review-only and does not touch files."""
    prompt_path = Path("prompts/final_expense_alex.md")
    if prompt_path.exists():
        content_before = prompt_path.read_text(encoding="utf-8")
    else:
        content_before = "mock content"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(content_before, encoding="utf-8")

    item_id = await repo.save_human_review_item(
        item_type="prompt_patch",
        payload={"patch": "some diff", "prompt_file": str(prompt_path)},
        status="pending"
    )

    res = await service.approve_review_item(item_id, reviewer="Jimmy")
    assert res.new_status == "approved"
    assert "Prompt file was not modified" in res.message

    item = await service.get_review_item(item_id)
    assert item["status"] == "approved"
    assert item["payload"]["prompt_patch_approved"] is True

    # Assert prompt file unchanged
    content_after = prompt_path.read_text(encoding="utf-8")
    assert content_before == content_after


@pytest.mark.asyncio
async def test_approve_rag_doc_does_not_rebuild_rag(service, repo):
    """Verify that rag_doc approval is review-only and does not trigger index rebuilds."""
    item_id = await repo.save_human_review_item(
        item_type="rag_doc",
        payload={"doc_content": "some text"},
        status="pending"
    )

    res = await service.approve_review_item(item_id, reviewer="Jimmy")
    assert res.new_status == "approved"
    assert "No index rebuild performed" in res.message

    item = await service.get_review_item(item_id)
    assert item["status"] == "approved"
    assert item["payload"]["rag_doc_approved"] is True


@pytest.mark.asyncio
async def test_review_history_is_appended(service, repo):
    """Verify that multiple review actions append to the audit history correctly."""
    item_id = await repo.save_human_review_item(
        item_type="training_example",
        payload={
            "source_id": "source_123",
            "stage": "opening",
            "user_text": "Hi",
            "candidate_ideal_response": "Hi, this is Alex.",
            "labels": {"compliance_risk": "none"},
            "payload_hash": "hash_history"
        },
        status="pending"
    )

    # Action 1: request changes
    await service.request_changes(item_id, reviewer="Jimmy", review_notes="Fix typo.")
    
    # Action 2: approve
    await service.approve_review_item(item_id, reviewer="Sarah", review_notes="Looks great now.")

    item = await service.get_review_item(item_id)
    assert item["status"] == "approved"
    
    history = item["payload"].get("review_history") or []
    assert len(history) == 2

    # Verify Action 1
    assert history[0]["action"] == "needs_changes"
    assert history[0]["reviewer"] == "Jimmy"
    assert history[0]["review_notes"] == "Fix typo."
    assert history[0]["previous_status"] == "pending"
    assert history[0]["new_status"] == "needs_changes"
    assert "reviewed_at" in history[0]

    # Verify Action 2
    assert history[1]["action"] == "approved"
    assert history[1]["reviewer"] == "Sarah"
    assert history[1]["review_notes"] == "Looks great now."
    assert history[1]["previous_status"] == "needs_changes"
    assert history[1]["new_status"] == "approved"
    assert "reviewed_at" in history[1]


def test_cli_list_pending():
    """Verify execution of the CLI list pending mode via subprocess."""
    default_repo = Repository()
    
    unique_matters = f"CLI_List_Test_{uuid.uuid4()}"
    item_id = asyncio.run(default_repo.save_human_review_item(
        item_type="training_example",
        payload={"why_this_matters": unique_matters},
        status="pending"
    ))

    try:
        cmd = [
            sys.executable,
            "scripts/review_training_items.py",
            "--list"
        ]

        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(".").resolve())

        res = subprocess.run(cmd, capture_output=True, text=True, check=True, env=env)

        assert res.returncode == 0
        data = json.loads(res.stdout)
        assert "count" in data
        assert "items" in data
        
        found = False
        for item in data["items"]:
            if item["id"] == item_id:
                found = True
                assert item["item_type"] == "training_example"
                assert item["status"] == "pending"
                assert item["short summary"] == unique_matters
                assert item["short_summary"] == unique_matters
                break
        assert found, f"Item {item_id} not found in CLI list output"
    finally:
        pass


def test_cli_approve_training_example():
    """Verify execution of the CLI approve mode via subprocess."""
    default_repo = Repository()
    
    item_id = asyncio.run(default_repo.save_human_review_item(
        item_type="training_example",
        payload={
            "source_id": "cli_src",
            "stage": "opening",
            "user_text": "Hi",
            "candidate_ideal_response": "Hi back",
            "labels": {"compliance_risk": "none"}
        },
        status="pending"
    ))

    try:
        cmd = [
            sys.executable,
            "scripts/review_training_items.py",
            "--approve", item_id,
            "--reviewer", "CLI_Tester",
            "--notes", "CLI Notes"
        ]

        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(".").resolve())

        res = subprocess.run(cmd, capture_output=True, text=True, check=True, env=env)

        assert res.returncode == 0
        data = json.loads(res.stdout)
        assert data["item_id"] == item_id
        assert data["new_status"] == "approved"
        assert data["created_record_type"] == "training_example"

        db_item = asyncio.run(default_repo.get_human_review_item(item_id))
        assert db_item["status"] == "approved"
        assert db_item["reviewer"] == "CLI_Tester"
        assert db_item["review_notes"] == "CLI Notes"
    finally:
        pass
