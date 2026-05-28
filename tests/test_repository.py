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


@pytest.mark.asyncio
async def test_save_and_query_call(repo: Repository):
    """save_call and get_recent_calls/get_call work as expected."""
    call_id = await repo.save_call(
        call_id="call-x-123",
        lead_id="lead-123",
        campaign_id="camp-123",
        phone_e164="+13055550199",
        caller_id="+13055550100",
        outcome="completed",
        duration_seconds=45.5,
        qa_score=0.92
    )
    assert isinstance(call_id, str)

    recent = await repo.get_recent_calls(limit=10)
    assert len(recent) == 1
    assert recent[0]["call_id"] == "call-x-123"
    assert recent[0]["outcome"] == "completed"
    assert recent[0]["qa_score"] == 0.92


@pytest.mark.asyncio
async def test_save_transfer(repo: Repository):
    """save_transfer validates and persists a transfer record."""
    rid = await repo.save_transfer(
        call_id="call-x-123",
        lead_id="lead-123",
        transfer_mode="warm_bridge",
        agent_id="agent-9",
        target_phone="+13055550111",
        success=True,
        summary={"notes": "Successful handoff"}
    )
    assert isinstance(rid, str)

    transfers = await repo.store.query("transfers", {"call_id": "call-x-123"})
    assert len(transfers) == 1
    assert transfers[0]["agent_id"] == "agent-9"
    assert transfers[0]["success"] is True


@pytest.mark.asyncio
async def test_save_callback(repo: Repository):
    """save_callback validates and persists a callback scheduling."""
    rid = await repo.save_callback(
        call_id="call-x-123",
        lead_id="lead-123",
        phone_e164="+13055550199",
        callback_time_local="2026-05-30T10:00:00",
        callback_timezone="America/New_York",
        status="pending",
        notes="Customer wants to discuss rates"
    )
    assert isinstance(rid, str)

    callbacks = await repo.store.query("callbacks", {"phone_e164": "+13055550199"})
    assert len(callbacks) == 1
    assert callbacks[0]["callback_timezone"] == "America/New_York"
    assert callbacks[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_save_dnc_request(repo: Repository):
    """save_dnc_request validates and persists a DNC request."""
    rid = await repo.save_dnc_request(
        call_id="call-x-123",
        lead_id="lead-123",
        phone_e164="+13055550199",
        campaign_id="camp-123",
        reason="Requested during interest check"
    )
    assert isinstance(rid, str)

    dnc = await repo.store.query("dnc_requests", {"phone_e164": "+13055550199"})
    assert len(dnc) == 1
    assert dnc[0]["reason"] == "Requested during interest check"


@pytest.mark.asyncio
async def test_save_consent_record(repo: Repository):
    """save_consent_record validates and persists a TCPA consent record."""
    from datetime import datetime, timezone
    rid = await repo.save_consent_record(
        consent_artifact_id="art-999",
        lead_id="lead-123",
        phone_e164="+13055550199",
        source_vendor="leads_r_us",
        consent_text="I agree to receive automated marketing calls...",
        consent_timestamp=datetime.now(timezone.utc),
        landing_page_url="https://leadsrus.com/consent"
    )
    assert isinstance(rid, str)

    records = await repo.store.query("consent_records", {"phone_e164": "+13055550199"})
    assert len(records) == 1
    assert records[0]["consent_artifact_id"] == "art-999"
    assert records[0]["source_vendor"] == "leads_r_us"


@pytest.mark.asyncio
async def test_save_latency_metric(repo: Repository):
    """save_latency_metric validates and persists a latency reading."""
    rid = await repo.save_latency_metric(
        call_id="call-x-123",
        metric_name="tts_generation_ms",
        metric_value_ms=180.5
    )
    assert isinstance(rid, str)

    metrics = await repo.store.query("latency_metrics", {"call_id": "call-x-123"})
    assert len(metrics) == 1
    assert metrics[0]["metric_name"] == "tts_generation_ms"
    assert float(metrics[0]["metric_value_ms"]) == 180.5


@pytest.mark.asyncio
async def test_save_campaign(repo: Repository):
    """save_campaign validates and persists campaign configs."""
    rid = await repo.save_campaign(
        campaign_id="camp-abc-123",
        name="Outbound Lead Gen",
        status="active",
        config={"max_attempts": 5}
    )
    assert isinstance(rid, str)

    camps = await repo.store.query("campaigns", {"campaign_id": "camp-abc-123"})
    assert len(camps) == 1
    assert camps[0]["name"] == "Outbound Lead Gen"
    assert camps[0]["config"]["max_attempts"] == 5


@pytest.mark.asyncio
async def test_get_lead_by_phone(repo: Repository):
    """get_lead_by_phone successfully finds lead by phone number."""
    await repo.save_lead_snapshot(
        call_id="call-lead-1",
        lead_profile={
            "lead_id": "prospect-77",
            "lead_phone_e164": "+13055559999",
            "campaign_id": "camp-123"
        },
        stage="opening"
    )

    lead = await repo.get_lead_by_phone("+13055559999")
    assert lead is not None
    assert lead["lead_profile"]["lead_id"] == "prospect-77"


@pytest.mark.asyncio
async def test_get_campaign_metrics(repo: Repository):
    """get_campaign_metrics calculates aggregates correctly."""
    # Save a few calls for campaign-metrics-test
    await repo.save_call(
        call_id="call-metric-1",
        campaign_id="camp-metrics-test",
        outcome="completed",
        started_at="2026-05-28T10:00:00Z",
        answered_at="2026-05-28T10:00:05Z",
        ended_at="2026-05-28T10:01:05Z",
        duration_seconds=60.0,
        qa_score=0.90
    )
    await repo.save_call(
        call_id="call-metric-2",
        campaign_id="camp-metrics-test",
        outcome="no-answer",
        started_at="2026-05-28T10:05:00Z",
        duration_seconds=None,
        qa_score=None
    )
    await repo.save_call(
        call_id="call-metric-3",
        campaign_id="camp-metrics-test",
        outcome="completed",
        started_at="2026-05-28T10:10:00Z",
        answered_at="2026-05-28T10:10:03Z",
        ended_at="2026-05-28T10:10:43Z",
        duration_seconds=40.0,
        qa_score=0.80
    )

    metrics = await repo.get_campaign_metrics("camp-metrics-test")
    assert metrics["campaign_id"] == "camp-metrics-test"
    assert metrics["total_calls"] == 3
    assert metrics["answered_calls"] == 2
    assert metrics["completed_calls"] == 2
    assert metrics["total_duration_seconds"] == 100.0
    assert metrics["average_duration_seconds"] == 50.0
    assert metrics["average_qa_score"] == pytest.approx(0.85)
    assert metrics["outcomes"]["completed"] == 2
    assert metrics["outcomes"]["no-answer"] == 1


@pytest.mark.asyncio
async def test_repository_health_check_and_close(repo: Repository):
    """health_check and close run cleanly."""
    hc = await repo.health_check()
    assert isinstance(hc, dict)
    assert hc["connected"] is True

    # Close should not raise
    await repo.close()

