"""Tests for training example miner."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
import pytest

from storage.repository import Repository
from training.ingestion import TrainingIngestionService
from training.labeler import TranscriptLabeler
from training.example_miner import TrainingExampleMiner


@pytest.fixture
def repo(tmp_path):
    """Return a Repository backed by a temporary JsonlStore."""
    return Repository(data_dir=tmp_path)


@pytest.fixture
def labeler(repo):
    """Return a TranscriptLabeler using a temporary Repository."""
    return TranscriptLabeler(repository=repo)


@pytest.fixture
def ingestion_service(repo):
    """Return a TrainingIngestionService using a temporary Repository."""
    return TrainingIngestionService(repository=repo)


@pytest.fixture
def miner(repo):
    """Return a TrainingExampleMiner using a temporary Repository."""
    return TrainingExampleMiner(repository=repo)


@pytest.mark.asyncio
async def test_requires_labeled_source(ingestion_service, miner):
    """Verify that mining raises ValueError on an unlabeled source."""
    transcript = (
        "Agent: Hello, this is Alex checking on your burial options.\n"
        "Prospect: How much does it cost?\n"
        "Agent: It depends on your age, are you between 40 and 85?"
    )

    ingest_res = await ingestion_service.ingest_source(
        source_type="call_transcript",
        title="Unlabeled Source Test",
        content=transcript
    )

    with pytest.raises(ValueError, match="must be labeled before mining"):
        await miner.mine_source(ingest_res.source_id)


@pytest.mark.asyncio
async def test_creates_training_example_from_good_agent_turn(
    ingestion_service, labeler, miner, repo
):
    """Verify positive training example generation."""
    transcript = (
        "Prospect: Hello?\n"
        "Agent: Hello, this is Dana. I'm calling to check if you're still open to final expense options."
    )

    ingest_res = await ingestion_service.ingest_source(
        source_type="call_transcript",
        title="Good Agent Turn Test",
        content=transcript
    )

    # Label first
    await labeler.label_training_source(ingest_res.source_id)

    # Mine
    mine_res = await miner.mine_source(ingest_res.source_id)
    assert mine_res.training_example_candidates == 1

    # Verify review items in repository
    items = await repo.list_pending_human_review_items()
    assert len(items) >= 1

    # Check that item_type is training_example
    te_item = next(item for item in items if item["item_type"] == "training_example")
    assert te_item["status"] == "pending"
    assert te_item["reviewer"] is None
    assert te_item["payload"]["source_id"] == ingest_res.source_id
    assert te_item["payload"]["candidate_ideal_response"] == "Hello, this is Dana. I'm calling to check if you're still open to final expense options."
    assert te_item["payload"]["user_text"] == "Hello?"

    # Ensure no TrainingExample record was created in the database yet
    examples = await repo.query_training_examples({})
    assert len(examples) == 0


@pytest.mark.asyncio
async def test_creates_failure_example_for_price_quote(
    ingestion_service, labeler, miner, repo
):
    """Verify failure candidate generation when agent quotes price."""
    transcript = (
        "Prospect: How much is it?\n"
        "Agent: Your monthly rate is going to be 50 dollars a month."
    )

    ingest_res = await ingestion_service.ingest_source(
        source_type="call_transcript",
        title="Price Quote Failure Test",
        content=transcript
    )

    await labeler.label_training_source(ingest_res.source_id)
    mine_res = await miner.mine_source(ingest_res.source_id)

    assert mine_res.failure_candidates >= 1

    items = await repo.list_pending_human_review_items()

    # Assert failure_example is created
    fail_item = next(item for item in items if item["item_type"] == "failure_example")
    assert fail_item["payload"]["bad_response"] == "Your monthly rate is going to be 50 dollars a month."

    # Assert compliance_review is created
    comp_item = next(item for item in items if item["item_type"] == "compliance_review")
    assert comp_item["payload"]["compliance_risk"] == "critical"

    # Assert eval_case candidate is created
    eval_item = next(item for item in items if item["item_type"] == "eval_case")
    assert eval_item["payload"]["severity"] == "critical"
    assert "quotes price" in eval_item["payload"]["why_this_matters"].lower()


@pytest.mark.asyncio
async def test_creates_compliance_review_for_you_qualify(
    ingestion_service, labeler, miner, repo
):
    """Verify critical compliance review for 'you qualify' claim."""
    transcript = (
        "Agent: You qualify for this american beneficiary option."
    )

    ingest_res = await ingestion_service.ingest_source(
        source_type="call_transcript",
        title="You Qualify Compliance Test",
        content=transcript
    )

    await labeler.label_training_source(ingest_res.source_id)
    mine_res = await miner.mine_source(ingest_res.source_id)
    assert mine_res.compliance_review_items >= 1

    items = await repo.list_pending_human_review_items()
    comp_item = next(item for item in items if item["item_type"] == "compliance_review")
    assert comp_item["payload"]["compliance_risk"] == "critical"
    assert comp_item["payload"]["severity"] == "critical"


@pytest.mark.asyncio
async def test_creates_eval_case_for_dnc(
    ingestion_service, labeler, miner, repo
):
    """Verify eval_case candidate is created when prospect requests DNC."""
    transcript = (
        "Prospect: Stop calling, do not call me again."
    )

    ingest_res = await ingestion_service.ingest_source(
        source_type="call_transcript",
        title="DNC Eval Case Test",
        content=transcript
    )

    await labeler.label_training_source(ingest_res.source_id)
    mine_res = await miner.mine_source(ingest_res.source_id)
    assert mine_res.eval_case_candidates >= 1

    items = await repo.list_pending_human_review_items()
    eval_item = next(item for item in items if item["item_type"] == "eval_case")
    assert "dnc_handling" in eval_item["payload"]["why_this_matters"].lower() or "dnc" in eval_item["payload"]["why_this_matters"].lower()
    assert eval_item["payload"]["severity"] == "high"


@pytest.mark.asyncio
async def test_creates_eval_case_for_wrong_number(
    ingestion_service, labeler, miner, repo
):
    """Verify eval_case candidate is created when prospect flags wrong number."""
    transcript = (
        "Prospect: This is the wrong number."
    )

    ingest_res = await ingestion_service.ingest_source(
        source_type="call_transcript",
        title="Wrong Number Test",
        content=transcript
    )

    await labeler.label_training_source(ingest_res.source_id)
    mine_res = await miner.mine_source(ingest_res.source_id)
    assert mine_res.eval_case_candidates >= 1

    items = await repo.list_pending_human_review_items()
    eval_item = next(item for item in items if item["item_type"] == "eval_case")
    assert "wrong number" in eval_item["payload"]["why_this_matters"].lower()


@pytest.mark.asyncio
async def test_transfer_before_consent_creates_critical_items(
    ingestion_service, labeler, miner, repo
):
    """Verify agent transfer before consent creates compliance, failure, and eval items."""
    transcript = (
        "Agent: Let me transfer you now to get you set up."
    )

    ingest_res = await ingestion_service.ingest_source(
        source_type="call_transcript",
        title="Transfer Before Consent Test",
        content=transcript
    )

    await labeler.label_training_source(ingest_res.source_id)
    mine_res = await miner.mine_source(ingest_res.source_id)

    items = await repo.list_pending_human_review_items()

    # Assert compliance review
    comp_item = next(item for item in items if item["item_type"] == "compliance_review")
    assert comp_item["payload"]["severity"] == "critical"

    # Assert failure example
    fail_item = next(item for item in items if item["item_type"] == "failure_example")
    assert fail_item["payload"]["bad_response"] == "Let me transfer you now to get you set up."

    # Assert eval case candidate
    eval_item = next(item for item in items if item["item_type"] == "eval_case")
    assert "transfer" in eval_item["payload"]["why_this_matters"].lower()
    assert eval_item["payload"]["severity"] == "critical"


@pytest.mark.asyncio
async def test_dedupes_review_items(
    ingestion_service, labeler, miner, repo
):
    """Verify that running the miner twice on the same source skips duplicates."""
    transcript = (
        "Prospect: How much does it cost?\n"
        "Agent: The licensed agent has to go over that because it depends on your age and health. I just check the basics before they review the options."
    )

    ingest_res = await ingestion_service.ingest_source(
        source_type="call_transcript",
        title="Deduplication Test",
        content=transcript
    )

    await labeler.label_training_source(ingest_res.source_id)

    # First mine run
    res1 = await miner.mine_source(ingest_res.source_id)
    assert res1.candidates_created > 0
    assert res1.skipped_candidates == 0

    # Second mine run
    res2 = await miner.mine_source(ingest_res.source_id)
    assert res2.candidates_created == 0
    assert res2.skipped_candidates == res1.candidates_created


@pytest.mark.asyncio
async def test_mining_updates_training_source_metadata(
    ingestion_service, labeler, miner, repo
):
    """Verify that mining updates the training source's metadata fields."""
    transcript = (
        "Prospect: How much does it cost?\n"
        "Agent: The licensed agent has to go over that because it depends on your age and health. I just check the basics before they review the options."
    )

    ingest_res = await ingestion_service.ingest_source(
        source_type="call_transcript",
        title="Metadata Update Test",
        content=transcript
    )

    await labeler.label_training_source(ingest_res.source_id)
    await miner.mine_source(ingest_res.source_id)

    source = await repo.get_training_source(ingest_res.source_id)
    meta = source["metadata"]
    assert meta["mining_version"] == "1.0.0"
    assert "mined_at" in meta
    assert "mining_summary" in meta
    assert meta["mining_summary"]["candidates_created"] > 0


def test_cli_mine_source():
    """Verify execution of the CLI script via subprocess."""
    import uuid
    default_repo = Repository()

    meta = {
        "content_hash": f"cli-mine-{uuid.uuid4()}",
        "normalized_turns": [
            {"speaker": "agent", "text": "Hello, this is Dana.", "turn_index": 0},
            {"speaker": "prospect", "text": "Do not call", "turn_index": 1}
        ],
        "normalized_turn_count": 2,
        "redaction_count": 0,
        "labels": {
            "total_turns": 2,
            "labeled_turns": 2,
            "objection_counts": {"dnc": 1},
            "stage_counts": {"opening": 1, "dnc": 1},
            "compliance_risk_counts": {"none": 2},
            "good_example_candidates": 0,
            "failure_candidates": 0,
            "turns": [
                {
                    "speaker": "agent",
                    "text": "Hello, this is Dana.",
                    "turn_index": 0,
                    "label": {
                        "call_stage": "opening",
                        "stage_confidence": 0.8,
                        "objection_type": "none",
                        "objection_confidence": 0.3,
                        "sentiment": "neutral",
                        "sentiment_confidence": 0.3,
                        "compliance_risk": "none",
                        "compliance_confidence": 0.0,
                        "is_good_example_candidate": False,
                        "is_failure_candidate": False,
                        "reasons": []
                    }
                },
                {
                    "speaker": "prospect",
                    "text": "Do not call",
                    "turn_index": 1,
                    "label": {
                        "call_stage": "dnc",
                        "stage_confidence": 0.8,
                        "objection_type": "dnc",
                        "objection_confidence": 0.8,
                        "sentiment": "neutral",
                        "sentiment_confidence": 0.3,
                        "compliance_risk": "medium",
                        "compliance_confidence": 0.8,
                        "is_good_example_candidate": False,
                        "is_failure_candidate": False,
                        "reasons": []
                    }
                }
            ]
        }
    }

    # Save directly to default store so subprocess CLI can access it
    source_id = asyncio.run(default_repo.save_training_source(
        source_type="manager_note",
        source_uri=f"test://cli-mine-{uuid.uuid4()}",
        title="CLI Mine Test Source",
        status="raw",
        metadata=meta
    ))

    try:
        cmd = [
            sys.executable,
            "scripts/mine_training_examples.py",
            "--source-id", source_id
        ]

        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(".").resolve())

        res = subprocess.run(cmd, capture_output=True, text=True, check=True, env=env)

        assert res.returncode == 0
        data = json.loads(res.stdout)
        assert data["source_id"] == source_id
        assert "candidates_created" in data
        assert "skipped_candidates" in data
        assert "compliance_review_items" in data
        assert "eval_case_candidates" in data
        assert "training_example_candidates" in data
        assert "failure_candidates" in data
    finally:
        pass
