"""Tests for :class:`storage.repository.Repository`.

All tests use pytest's ``tmp_path`` fixture and the JSONL backend —
no Postgres required.
"""

from __future__ import annotations

import os
from unittest import mock

import pytest

from storage.jsonl_store import JsonlStore
from storage.repository import Repository


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def repo(tmp_path):
    """Return a Repository backed by a tmp_path JsonlStore."""
    return Repository(data_dir=tmp_path)


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_lead_snapshot(repo: Repository):
    """save_lead_snapshot validates and persists the record."""
    rid = await repo.save_lead_snapshot(
        call_id="call-001",
        lead_profile={"first_name": "Alice", "age": 72},
        stage="opening",
    )
    assert isinstance(rid, str)

    # Should be queryable.
    result = await repo.get_call("call-001")
    assert result is not None
    assert result["call_id"] == "call-001"
    assert result["lead_profile"]["first_name"] == "Alice"
    assert result["stage"] == "opening"


@pytest.mark.asyncio
async def test_save_lead_snapshot_validation_error(repo: Repository):
    """Missing required fields should raise a validation error."""
    with pytest.raises(Exception):
        await repo.save_lead_snapshot(
            # Missing call_id, stage
            lead_profile={"first_name": "Bob"},
        )


@pytest.mark.asyncio
async def test_save_call_turn(repo: Repository):
    """save_call_turn validates and persists a turn record."""
    rid = await repo.save_call_turn(
        call_id="call-001",
        turn_number=1,
        speaker="user",
        text="Hello, is this about insurance?",
        stage="opening",
    )
    assert isinstance(rid, str)

    # Query it back via the underlying store.
    turns = await repo.store.query("call_turns", {"call_id": "call-001"})
    assert len(turns) == 1
    assert turns[0]["speaker"] == "user"
    assert turns[0]["turn_number"] == 1


@pytest.mark.asyncio
async def test_save_call_turn_bad_data(repo: Repository):
    """Validation rejects missing required fields on CallTurn."""
    with pytest.raises(Exception):
        await repo.save_call_turn(
            call_id="call-001",
            # Missing turn_number, speaker, text, stage
        )


@pytest.mark.asyncio
async def test_list_recent_calls(repo: Repository):
    """list_recent_calls returns lead snapshots newest-first."""
    for i in range(5):
        await repo.save_lead_snapshot(
            call_id=f"call-{i:03d}",
            lead_profile={"index": i},
            stage="opening",
        )

    recent = await repo.list_recent_calls(limit=3)
    assert len(recent) == 3
    # Most recent first.
    assert recent[0]["call_id"] == "call-004"
    assert recent[2]["call_id"] == "call-002"


@pytest.mark.asyncio
async def test_defaults_to_jsonl(tmp_path):
    """When DATABASE_URL is unset, Repository defaults to JsonlStore."""
    with mock.patch.dict(os.environ, {}, clear=True):
        # Explicitly remove DATABASE_URL if present.
        os.environ.pop("DATABASE_URL", None)
        repo = Repository(data_dir=tmp_path)
        assert isinstance(repo.store, JsonlStore)


@pytest.mark.asyncio
async def test_save_tool_event(repo: Repository):
    """save_tool_event validates and persists a tool event."""
    rid = await repo.save_tool_event(
        call_id="call-001",
        tool_name="transfer_to_agent",
        params={"agent_id": "agent-5"},
        result={"status": "ok"},
    )
    assert isinstance(rid, str)

    events = await repo.store.query("tool_events", {"call_id": "call-001"})
    assert len(events) == 1
    assert events[0]["tool_name"] == "transfer_to_agent"


@pytest.mark.asyncio
async def test_save_qa_report(repo: Repository):
    """save_qa_report validates and persists a QA report."""
    rid = await repo.save_qa_report(
        call_id="call-001",
        scores={"compliance": 0.95, "empathy": 0.8},
        issues=["Missed objection handling"],
        recommendations=["Use more empathy phrases"],
    )
    assert isinstance(rid, str)

    reports = await repo.store.query("qa_reports", {"call_id": "call-001"})
    assert len(reports) == 1
    assert reports[0]["scores"]["compliance"] == 0.95


@pytest.mark.asyncio
async def test_save_training_note(repo: Repository):
    """save_training_note validates and persists a training note."""
    rid = await repo.save_training_note(
        source="call-review-2024-01",
        topic="objection-handling",
        sales_lesson="Acknowledge before redirecting",
        good_example="I understand your concern...",
        bad_example="But you need this insurance.",
        call_stage="objection",
    )
    assert isinstance(rid, str)

    notes = await repo.store.query(
        "training_notes", {"topic": "objection-handling"}
    )
    assert len(notes) == 1
    assert "Acknowledge" in notes[0]["sales_lesson"]


@pytest.mark.asyncio
async def test_get_call_not_found(repo: Repository):
    """get_call returns None for a non-existent call_id."""
    result = await repo.get_call("nonexistent-call")
    assert result is None
