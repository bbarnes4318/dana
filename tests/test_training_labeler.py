"""Tests for deterministic transcript labeling system."""

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
from core.objection_classifier import ObjectionClassifier
from training.ingestion import TrainingIngestionService
from training.labeler import TranscriptLabeler, OBJECTION_MAP


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


def test_objection_map_alignment():
    """Verify that all mapped intents in OBJECTION_MAP exist in the runtime YAML."""
    classifier = ObjectionClassifier()
    known = classifier.known_intents
    for labeler_obj, runtime_obj in OBJECTION_MAP.items():
        assert runtime_obj in known, f"Runtime objection intent '{runtime_obj}' is not in known intents: {known}"


# ------------------------------------------------------------------
# Explicit 18 Scenario Tests
# ------------------------------------------------------------------

# Scenario 1: already insured
def test_scenario_already_insured(labeler):
    turn = {"speaker": "prospect", "text": "I already have coverage through work", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.objection_type == "already_insured"
    assert res.label.objection_confidence == 0.8
    assert len(res.label.reasons) > 0
    assert any("already insured" in r.lower() for r in res.label.reasons)
    assert res.label.is_failure_candidate is False


# Scenario 2: price question
def test_scenario_price_question(labeler):
    turn = {"speaker": "prospect", "text": "How much does it cost?", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.objection_type == "price_question"
    assert res.label.objection_confidence == 0.8
    assert len(res.label.reasons) > 0
    assert any("price" in r.lower() for r in res.label.reasons)
    assert res.label.is_failure_candidate is False


# Scenario 3: are you real
def test_scenario_are_you_real(labeler):
    turn = {"speaker": "prospect", "text": "Are you a real person or an AI robot?", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.objection_type == "asks_if_real"
    assert res.label.objection_confidence == 0.8
    assert res.label.sentiment == "suspicious"
    assert res.label.sentiment_confidence == 0.8
    assert len(res.label.reasons) > 0
    assert res.label.is_failure_candidate is False


# Scenario 4: are you licensed
def test_scenario_are_you_licensed(labeler):
    turn = {"speaker": "prospect", "text": "Do you have a licensed agent ID?", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.objection_type == "asks_if_licensed"
    assert res.label.objection_confidence == 0.8
    assert len(res.label.reasons) > 0
    assert res.label.is_failure_candidate is False


# Scenario 5: DNC
def test_scenario_dnc(labeler):
    turn = {"speaker": "prospect", "text": "Take me off your list and stop calling", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.call_stage == "dnc"
    assert res.label.stage_confidence == 0.8
    assert res.label.objection_type == "dnc"
    assert res.label.objection_confidence == 0.8
    assert len(res.label.reasons) > 0
    assert res.label.is_failure_candidate is False


# Scenario 6: wrong number
def test_scenario_wrong_number(labeler):
    turn = {"speaker": "prospect", "text": "You have the wrong person, not me.", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.objection_type == "wrong_number"
    assert res.label.objection_confidence == 0.8
    assert len(res.label.reasons) > 0
    assert res.label.is_failure_candidate is False


# Scenario 7: busy/callback
def test_scenario_busy_callback(labeler):
    turn = {"speaker": "prospect", "text": "I'm currently driving. Call me back tomorrow.", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.call_stage == "callback"
    assert res.label.stage_confidence == 0.8
    assert res.label.objection_type == "busy"
    assert res.label.objection_confidence == 0.8
    assert len(res.label.reasons) > 0
    assert res.label.is_failure_candidate is False


# Scenario 8: nursing home
def test_scenario_nursing_home(labeler):
    turn = {"speaker": "prospect", "text": "I am living in a nursing home right now.", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.call_stage == "disqualified"
    assert res.label.stage_confidence == 0.8
    assert len(res.label.reasons) > 0
    assert res.label.is_failure_candidate is False


# Scenario 9: spouse/decision maker
def test_scenario_spouse_decision_maker(labeler):
    turn = {"speaker": "prospect", "text": "My spouse handles all the financial decisions.", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.call_stage == "decision_maker"
    assert res.label.stage_confidence == 0.8
    assert res.label.objection_type == "spouse"
    assert res.label.objection_confidence == 0.8
    assert len(res.label.reasons) > 0
    assert res.label.is_failure_candidate is False


# Scenario 10: transfer consent
def test_scenario_transfer_consent(labeler):
    turn = {"speaker": "prospect", "text": "Sure, go ahead and connect me to the agent.", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.call_stage == "transfer_consent"
    assert res.label.stage_confidence == 0.8
    assert len(res.label.reasons) > 0
    assert res.label.is_failure_candidate is False


# Scenario 11: hostile
def test_scenario_hostile(labeler):
    turn = {"speaker": "prospect", "text": "Stop harassing me or I will sue you!", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.objection_type == "hostile"
    assert res.label.objection_confidence == 0.8
    assert res.label.sentiment == "hostile"
    assert res.label.sentiment_confidence == 0.8
    assert len(res.label.reasons) > 0
    assert res.label.is_failure_candidate is False


# Scenario 12: agent price quote
def test_scenario_agent_price_quote(labeler):
    turn = {"speaker": "agent", "text": "Your monthly rate is going to be 50 dollars a month.", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.compliance_risk == "critical"
    assert res.label.compliance_confidence == 1.0
    assert len(res.label.reasons) > 0
    assert any("price" in r.lower() for r in res.label.reasons)
    assert res.label.is_failure_candidate is True


# Scenario 13: agent says you qualify
def test_scenario_agent_says_you_qualify(labeler):
    turn = {"speaker": "agent", "text": "You qualify for this american beneficiary option.", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.compliance_risk == "critical"
    assert res.label.compliance_confidence == 1.0
    assert len(res.label.reasons) > 0
    assert any("qualify" in r.lower() for r in res.label.reasons)
    assert res.label.is_failure_candidate is True


# Scenario 14: agent licensed claim
def test_scenario_agent_licensed_claim(labeler):
    turn = {"speaker": "agent", "text": "I am a licensed agent in your state.", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.compliance_risk == "critical"
    assert res.label.compliance_confidence == 1.0
    assert len(res.label.reasons) > 0
    assert any("licensed" in r.lower() for r in res.label.reasons)
    assert res.label.is_failure_candidate is True


# Scenario 15: agent human claim
def test_scenario_agent_human_claim(labeler):
    turn = {"speaker": "agent", "text": "Yes, I am a real person, not a bot.", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.compliance_risk == "high"
    assert res.label.compliance_confidence == 0.8
    assert len(res.label.reasons) > 0
    assert any("human" in r.lower() for r in res.label.reasons)
    assert res.label.is_failure_candidate is True


# Scenario 16: agent multiple questions
def test_scenario_agent_multiple_questions(labeler):
    turn = {"speaker": "agent", "text": "Are you between 40 and 85? Do you live independently?", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.compliance_risk == "medium"
    assert res.label.compliance_confidence == 0.6
    assert len(res.label.reasons) > 0
    assert any("multiple questions" in r.lower() for r in res.label.reasons)
    assert res.label.is_failure_candidate is True


# Scenario 17: agent after DNC
def test_scenario_agent_after_dnc(labeler):
    prev_turns = [
        {"speaker": "prospect", "text": "Do not call me ever again.", "turn_index": 0}
    ]
    turn = {"speaker": "agent", "text": "Hello, but we have great offers.", "turn_index": 1}
    res = labeler.label_turn(turn, prev_turns)
    assert res.label.compliance_risk == "critical"
    assert res.label.compliance_confidence == 1.0
    assert len(res.label.reasons) > 0
    assert any("dnc" in r.lower() for r in res.label.reasons)
    assert res.label.is_failure_candidate is True


# Scenario 18: label_training_source updates metadata
@pytest.mark.asyncio
async def test_scenario_label_training_source_updates_metadata(
    ingestion_service: TrainingIngestionService,
    labeler: TranscriptLabeler,
    repo: Repository
):
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


# ------------------------------------------------------------------
# Additional Context Tests
# ------------------------------------------------------------------

def test_transfer_language_before_consent(labeler):
    """Verify that agent using transfer language before consent is critical compliance risk."""
    turn = {"speaker": "agent", "text": "Okay, let me transfer you to a licensed agent.", "turn_index": 1}
    res = labeler.label_turn(turn, previous_turns=[])
    assert res.label.compliance_risk == "critical"
    assert res.label.compliance_confidence == 1.0
    assert any("transfer language before prospect gave clear consent" in r.lower() for r in res.label.reasons)
    assert res.label.is_failure_candidate is True


def test_no_broad_numeric_matching_for_age(labeler):
    """Verify that age-stage detection is not triggered by arbitrary numbers."""
    turn = {"speaker": "prospect", "text": "I have 3 kids and my house number is 45.", "turn_index": 0}
    res = labeler.label_turn(turn)
    assert res.label.call_stage != "age_range"


def test_agent_speaks_after_wrong_number(labeler):
    """Verify that agent speaking after wrong number is failure candidate and critical risk."""
    prev_turns = [
        {"speaker": "prospect", "text": "No, this is wrong number. Not Jim.", "turn_index": 0}
    ]
    turn = {"speaker": "agent", "text": "Are you sure? We can check your details.", "turn_index": 1}
    res = labeler.label_turn(turn, prev_turns)
    assert res.label.compliance_risk == "critical"
    assert res.label.compliance_confidence == 1.0
    assert any("wrong number" in r.lower() for r in res.label.reasons)
    assert res.label.is_failure_candidate is True


def test_agent_speaks_after_hostile(labeler):
    """Verify that agent speaking after hostile prospect is failure candidate."""
    prev_turns = [
        {"speaker": "prospect", "text": "Get the fuck off my phone!", "turn_index": 0}
    ]
    turn = {"speaker": "agent", "text": "Sorry, let me just explain.", "turn_index": 1}
    res = labeler.label_turn(turn, prev_turns)
    assert res.label.is_failure_candidate is True
    assert any("hostile" in r.lower() for r in res.label.reasons)


def test_repeated_push_after_not_interested(labeler):
    """Verify that agent continuing to push after prospect says not interested is high risk."""
    prev_turns = [
        {"speaker": "prospect", "text": "I am not interested.", "turn_index": 0}
    ]
    turn = {"speaker": "agent", "text": "But this can save your family money.", "turn_index": 1}
    res = labeler.label_turn(turn, prev_turns)
    assert res.label.compliance_risk == "high"
    assert res.label.compliance_confidence == 0.8
    assert any("disinterest" in r.lower() for r in res.label.reasons)


def test_cli_label_training_source():
    """Verify that the CLI labeler runs via subprocess and outputs correct JSON structure."""
    import uuid
    default_repo = Repository()
    
    meta = {
        "content_hash": f"dummyhash-{uuid.uuid4()}",
        "normalized_turns": [
            {"speaker": "agent", "text": "Hello, this is Dana.", "turn_index": 0},
            {"speaker": "prospect", "text": "Not interested", "turn_index": 1}
        ],
        "normalized_turn_count": 2,
        "redaction_count": 0
    }
    
    # Save a test source to default JSONL store to test CLI
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
        # Clean up is optional as it's just local test JSONL data.
        pass
