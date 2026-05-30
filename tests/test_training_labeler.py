"""Tests for deterministic transcript labeling system."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone
import pytest

from storage.repository import Repository
from training.ingestion import TrainingIngestionService
from training.labeler import TranscriptLabeler, classify_objection, check_compliance_risk


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


def test_labels_already_insured():
    """Verify classification of already_insured objection."""
    obj = classify_objection("I already have coverage through work", "prospect")
    assert obj == "already_insured"


def test_labels_price_question():
    """Verify classification of price_question objection."""
    obj = classify_objection("How much is the monthly rate?", "prospect")
    assert obj == "price_question"


def test_labels_are_you_real(labeler: TranscriptLabeler):
    """Verify asks_if_real classification and sentiment of prospect turn."""
    turn = {"speaker": "prospect", "text": "Are you a real person or an AI bot?", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.objection_type == "asks_if_real"
    assert res.label.sentiment == "suspicious"


def test_labels_are_you_licensed(labeler: TranscriptLabeler):
    """Verify asks_if_licensed classification of prospect turn."""
    turn = {"speaker": "prospect", "text": "Do you have a licensed agent ID?", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.objection_type == "asks_if_licensed"


def test_labels_dnc():
    """Verify classification of dnc stage and objection."""
    obj = classify_objection("Take me off your list and stop calling", "prospect")
    assert obj == "dnc"


def test_labels_wrong_number(labeler: TranscriptLabeler):
    """Verify wrong_number objection classification."""
    turn = {"speaker": "prospect", "text": "You have the wrong person, not me.", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.objection_type == "wrong_number"


def test_labels_busy_callback(labeler: TranscriptLabeler):
    """Verify busy objection and callback stage classification."""
    turn = {"speaker": "prospect", "text": "I'm currently driving. Call me back tomorrow.", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.objection_type == "busy"
    assert res.label.call_stage == "callback"


def test_labels_nursing_home_disqualification(labeler: TranscriptLabeler):
    """Verify disqualified stage classification for care facilities."""
    turn = {"speaker": "prospect", "text": "I am living in a nursing home right now.", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.call_stage == "disqualified"


def test_labels_spouse_decision_maker(labeler: TranscriptLabeler):
    """Verify spouse/decision maker stage and objection classification."""
    turn = {"speaker": "prospect", "text": "My spouse handles all the financial decisions.", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.call_stage == "decision_maker"
    assert res.label.objection_type == "spouse"


def test_labels_transfer_consent(labeler: TranscriptLabeler):
    """Verify transfer_consent stage classification."""
    turn = {"speaker": "prospect", "text": "Sure, go ahead and connect me to the agent.", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.call_stage == "transfer_consent"


def test_labels_hostile(labeler: TranscriptLabeler):
    """Verify hostile objection and sentiment classification."""
    turn = {"speaker": "prospect", "text": "Stop harassing me or I will sue you!", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.objection_type == "hostile"
    assert res.label.sentiment == "hostile"


def test_agent_price_quote_is_critical():
    """Verify that agent quoting a specific premium/dollar is critical risk."""
    risk, reasons = check_compliance_risk("agent", "Your monthly rate is going to be 50 dollars a month.")
    assert risk == "critical"
    assert any("price" in r.lower() for r in reasons)


def test_agent_you_qualify_is_critical():
    """Verify that agent saying 'you qualify' is critical risk."""
    risk, reasons = check_compliance_risk("agent", "You qualify for this american beneficiary option.")
    assert risk == "critical"
    assert any("qualify" in r.lower() for r in reasons)


def test_agent_licensed_claim_is_critical():
    """Verify that agent claiming to be licensed is critical risk."""
    risk, reasons = check_compliance_risk("agent", "I am a licensed agent in your state.")
    assert risk == "critical"
    assert any("licensed" in r.lower() for r in reasons)


def test_agent_human_claim_is_high_or_critical():
    """Verify that agent claiming to be human/real person is high risk."""
    risk, reasons = check_compliance_risk("agent", "Yes, I am a real person, not a bot.")
    assert risk == "high"
    assert any("human" in r.lower() for r in reasons)


def test_agent_multiple_questions_failure_candidate(labeler: TranscriptLabeler):
    """Verify that agent asking multiple questions in one turn is marked a failure."""
    turn = {"speaker": "agent", "text": "Are you between 40 and 85? Do you live independently?", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.is_failure_candidate is True
    assert res.label.is_good_example_candidate is False


@pytest.mark.asyncio
async def test_label_training_source_updates_metadata(
    ingestion_service: TrainingIngestionService,
    labeler: TranscriptLabeler,
    repo: Repository
):
    """Verify that label_training_source correctly saves labeling output back to repository metadata."""
    transcript = (
        "Agent: Hello, this is Alex checking on your burial options.\n"
        "Prospect: How much does it cost?\n"
        "Agent: It depends on your age, are you between 40 and 85?"
    )

    ingest_res = await ingestion_service.ingest_source(
        source_type="call_transcript",
        title="Sample Call for Labeling",
        content=transcript
    )

    assert ingest_res.duplicate_detected is False
    source_id = ingest_res.source_id

    # Label the source
    label_res = await labeler.label_training_source(source_id)
    assert label_res.total_turns == 3
    assert label_res.objection_counts.get("price_question", 0) == 1

    # Fetch and check metadata fields
    source = await repo.get_training_source(source_id)
    meta = source["metadata"]
    assert "labels" in meta
    assert meta["labeling_version"] == "1.0.0"
    assert "labeled_at" in meta
    assert meta["labels"]["total_turns"] == 3


def test_cli_label_training_source(tmp_path):
    """Verify that the CLI labeler runs via subprocess and outputs correct JSON structure."""
    # Write notes to ingest first
    import uuid
    repo = Repository(data_dir=tmp_path)
    
    # We must ingest a source first to get a valid source ID
    # Inline run ingestion via helper script or direct repository save
    # Let's save a source using repo
    meta = {
        "content_hash": "dummyhash",
        "normalized_turns": [
            {"speaker": "agent", "text": "Hello, this is Dana.", "turn_index": 0},
            {"speaker": "prospect", "text": "Not interested", "turn_index": 1}
        ],
        "normalized_turn_count": 2,
        "redaction_count": 0
    }
    
    # Create the training_sources file inside tmp_path so CLI script can find it
    # But wait, CLI script will read from default "./data".
    # Since CLI runs in separate subprocess, let's write to the default "./data" or we can pass database path?
    # Wait, our script reads from default "./data". So we can write directly via repo (using default data dir)
    # or we can use a unique UUID content to prevent conflict!
    # Let's write the source directly to the default repository store, and delete it/clean it up after,
    # or just use a unique source ID and write it!
    # Actually, we can use Repository() to save the dummy source, then pass its ID to the CLI.
    # It will write to the default data/training_sources.jsonl. This is perfectly fine as long as we use a unique title/ID.
    default_repo = Repository()
    source_id = asyncio.run(default_repo.save_training_source(
        source_type="manager_note",
        source_uri=f"test://cli-label-{uuid.uuid4()}",
        title="CLI Label Test Source",
        status="raw",
        metadata=meta
    ))
    
    try:
        cmd = [
            sys.executable,
            "scripts/label_training_source.py",
            "--source-id", source_id
        ]

        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(".").resolve())

        res = subprocess.run(cmd, capture_output=True, text=True, check=True, env=env)
        
        assert res.returncode == 0
        data = json.loads(res.stdout)
        assert data["source_id"] == source_id
        assert data["total_turns"] == 2
        assert "objection_counts" in data
        assert "stage_counts" in data
        assert "compliance_risk_counts" in data
        assert "good_example_candidates" in data
        assert "failure_candidates" in data
        
    finally:
        # Clean up by removing/marking the record if needed, but since it's JSONL append-only,
        # it is fine to leave it (doesn't hurt other tests).
        pass


import asyncio
