"""Tests for the continuous training ingestion service and CLI tool."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone
import pytest

from storage.repository import Repository
from training.ingestion import TrainingIngestionService, redact_text


@pytest.fixture
def repo(tmp_path):
    """Return a Repository backed by a temporary JsonlStore."""
    return Repository(data_dir=tmp_path)


@pytest.fixture
def service(repo):
    """Return a TrainingIngestionService using a temporary Repository."""
    return TrainingIngestionService(repository=repo)


@pytest.mark.asyncio
async def test_ingest_plain_text_youtube_transcript(service: TrainingIngestionService, repo: Repository):
    """YouTube transcript without speaker lines is stored as one turn with speaker 'unknown'."""
    result = await service.ingest_source(
        source_type="youtube",
        title="FE Objection Strategies",
        content="In this video we talk about final expense objections. Make sure to close every lead.",
        source_uri="https://youtube.com/watch?v=123"
    )

    assert result.duplicate_detected is False
    assert result.normalized_turn_count == 1
    assert result.redaction_count == 0

    source = await repo.get_training_source(result.source_id)
    assert source is not None
    turns = source["metadata"]["normalized_turns"]
    assert len(turns) == 1
    assert turns[0]["speaker"] == "unknown"
    assert "objections" in turns[0]["text"]


@pytest.mark.asyncio
async def test_ingest_json_turns_call_transcript(service: TrainingIngestionService, repo: Repository, tmp_path):
    """JSON structure containing turn objects is parsed and mapped correctly."""
    json_data = {
        "turns": [
            {"role": "Prospect", "content": "How much is the cost?"},
            {"speaker": "rep", "text": "It depends on your age."}
        ]
    }
    
    file_path = tmp_path / "call.json"
    file_path.write_text(json.dumps(json_data), encoding="utf-8")

    result = await service.ingest_source(
        source_type="call_transcript",
        title="Call 123",
        file_path=file_path
    )

    assert result.normalized_turn_count == 2
    
    source = await repo.get_training_source(result.source_id)
    turns = source["metadata"]["normalized_turns"]
    assert turns[0]["speaker"] == "prospect"
    assert turns[0]["text"] == "How much is the cost?"
    assert turns[1]["speaker"] == "agent"
    assert turns[1]["text"] == "It depends on your age."


@pytest.mark.asyncio
async def test_ingest_speaker_line_text(service: TrainingIngestionService, repo: Repository):
    """Text with colons like 'Agent: text' is parsed into separate turns."""
    plain_text = (
        "Agent: Hello, this is Dana.\n"
        "Prospect: No thanks.\n"
        "Rep: But we have great options."
    )

    result = await service.ingest_source(
        source_type="licensed_agent_feedback",
        title="Agent Feedback Text",
        content=plain_text
    )

    assert result.normalized_turn_count == 3
    source = await repo.get_training_source(result.source_id)
    turns = source["metadata"]["normalized_turns"]
    assert turns[0]["speaker"] == "agent"
    assert turns[0]["text"] == "Hello, this is Dana."
    assert turns[1]["speaker"] == "prospect"
    assert turns[1]["text"] == "No thanks."
    assert turns[2]["speaker"] == "agent"
    assert turns[2]["text"] == "But we have great options."


def test_redacts_email_and_phone():
    """Emails and telephone formats are redacted."""
    text = "My email is support@dana-ai.com and my phone number is 1-800-555-0199."
    redacted, count = redact_text(text)
    assert "[REDACTED_EMAIL]" in redacted
    assert "[REDACTED_PHONE]" in redacted
    assert count == 2


def test_redacts_sensitive_numbers():
    """SSNs, Cards, and Accounts/Routing numbers are redacted."""
    text = "SSN is 000-12-3456, Card is 4111 2222 3333 4444, and Routing is RTN: 123456789."
    redacted, count = redact_text(text)
    assert "[REDACTED_SSN]" in redacted
    assert "[REDACTED_CARD]" in redacted
    assert "[REDACTED_ACCOUNT]" in redacted
    assert count == 3


def test_does_not_redact_age():
    """Common ages (65, 72, 80, 85) are preserved."""
    text = "The applicant is 65 years old, or maybe 72, or 80. Definitely not 85."
    redacted, count = redact_text(text)
    assert "65" in redacted
    assert "72" in redacted
    assert "80" in redacted
    assert "85" in redacted
    assert count == 0


@pytest.mark.asyncio
async def test_duplicate_detection(service: TrainingIngestionService, repo: Repository):
    """Skipped saving and marks duplicate_detected = True on identical content."""
    content = "Agent: Hello\nProspect: Goodbye"
    
    res1 = await service.ingest_source(
        source_type="call_transcript",
        title="Call A",
        content=content
    )
    assert res1.duplicate_detected is False

    res2 = await service.ingest_source(
        source_type="call_transcript",
        title="Call B (Duplicate)",
        content=content
    )
    assert res2.duplicate_detected is True
    assert res2.source_id == res1.source_id


def test_cli_ingest_file(tmp_path):
    """Run CLI ingestion via subprocess and verify output structure."""
    import uuid
    test_file = tmp_path / "notes.txt"
    unique_text = f"Agent: Welcome to our final expense check. cli_test_{uuid.uuid4().hex}"
    test_file.write_text(unique_text, encoding="utf-8")

    # Call the script
    cmd = [
        sys.executable,
        "scripts/ingest_training_source.py",
        "--type", "manager_note",
        "--title", "CLI Note Ingest Test",
        "--file", str(test_file)
    ]

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())

    res = subprocess.run(cmd, capture_output=True, text=True, check=True, env=env)
    
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert "source_id" in data
    assert data["duplicate_detected"] is False
    assert data["normalized_turn_count"] == 1
    assert data["redaction_count"] == 0


def test_plain_9_digits_no_redaction():
    """Plain 9 digit numbers without context are not redacted."""
    text = "The sequence is 123456789. Also another code 999887777."
    redacted, count = redact_text(text)
    assert "123456789" in redacted
    assert "999887777" in redacted
    assert count == 0

    # With context, the plain 9 digit number should be redacted as SSN
    text_with_context = "My SSN is 999887777."
    redacted_context, count_context = redact_text(text_with_context)
    assert "[REDACTED_SSN]" in redacted_context
    assert count_context == 1

