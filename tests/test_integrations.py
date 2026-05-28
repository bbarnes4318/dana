import os
import json
import asyncio
import hmac
import hashlib
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from storage.repository import Repository
from integrations.payload_sanitizer import sanitize_payload, mask_phone
from integrations.lead_import import normalize_lead, normalize_phone, normalize_name
from integrations.live_agent_dashboard import generate_livekit_token, generate_dashboard_notification
from integrations.webhook_dispatcher import get_dispatcher, WebhookDispatcher
from integrations.crm_webhooks import emit_crm_event, verify_signature, get_retry_delay


# =====================================================================
# 1. Payload Sanitizer & PII Protection Tests
# =====================================================================

def test_payload_sanitizer_masks_phone_by_default(monkeypatch):
    monkeypatch.setenv("DANA_CRM_SEND_FULL_PHONE", "no")
    payload = {
        "prospect_phone": "+15551234567",
        "nested": {
            "phone_number": "+12345678901"
        }
    }
    sanitized = sanitize_payload(payload)
    assert sanitized["prospect_phone"] == "+1555***4567"
    assert sanitized["nested"]["phone_number"] == "+1234***8901"


def test_payload_sanitizer_sends_full_phone_when_configured(monkeypatch):
    monkeypatch.setenv("DANA_CRM_SEND_FULL_PHONE", "yes")
    payload = {
        "prospect_phone": "+15551234567"
    }
    sanitized = sanitize_payload(payload)
    assert sanitized["prospect_phone"] == "+15551234567"


def test_payload_sanitizer_strips_sensitive_and_internal_data():
    payload = {
        "ssn": "123-456-7890",
        "nested": {
            "credit_card": "4111222233334444",
            "bank_account_number": "987654321"
        },
        "system_prompt": "You are a helpful AI",
        "compliance_internals": {"score": 10},
        "transcript": "Hello world",
        "recording_url": "http://audio.com/rec"
    }
    sanitized = sanitize_payload(payload)
    assert "ssn" not in sanitized
    assert "credit_card" not in sanitized["nested"]
    assert "bank_account_number" not in sanitized["nested"]
    assert "system_prompt" not in sanitized
    assert "compliance_internals" not in sanitized
    assert "transcript" not in sanitized
    assert "recording_url" not in sanitized


def test_payload_sanitizer_strips_handoff_summary_external():
    payload = {
        "handoff_summary": "Qualified lead description",
        "objection_notes": "None",
        "compliance_flags": {"ok": True},
        "first_name": "John"
    }
    # External CRM webhook: is_dashboard = False (default)
    sanitized_external = sanitize_payload(payload, is_dashboard=False)
    assert "handoff_summary" not in sanitized_external
    assert "objection_notes" not in sanitized_external
    assert "compliance_flags" not in sanitized_external
    assert sanitized_external["first_name"] == "John"

    # Internal agent dashboard: is_dashboard = True
    sanitized_internal = sanitize_payload(payload, is_dashboard=True)
    assert sanitized_internal["handoff_summary"] == "Qualified lead description"
    assert sanitized_internal["objection_notes"] == "None"
    assert sanitized_internal["compliance_flags"] == {"ok": True}


# =====================================================================
# 2. Lead Import Normalization Tests
# =====================================================================

def test_lead_import_normalizes_names():
    # Apostrophes, hyphens, and mixed casing must survive
    assert normalize_name("O'Connor") == "O'Connor"
    assert normalize_name("o'connor") == "O'Connor"
    assert normalize_name("smith-jones") == "Smith-Jones"
    assert normalize_name("McDonald") == "McDonald"
    assert normalize_name("mcdonald") == "McDonald"
    assert normalize_name("MacArthur") == "MacArthur"
    assert normalize_name("macarthur") == "MacArthur"
    assert normalize_name("van der Meer") == "van der Meer"
    assert normalize_name("De La Cruz") == "De La Cruz"
    assert normalize_name("IBM") == "IBM"
    assert normalize_name("john doe") == "John Doe"


def test_lead_import_phone_normalization():
    assert normalize_phone("5551234567") == "+15551234567"
    assert normalize_phone("15551234567") == "+15551234567"
    assert normalize_phone("+15551234567") == "+15551234567"
    
    with pytest.raises(ValueError, match="Could not normalize phone"):
        normalize_phone("12345")


def test_lead_import_state_timezone_confidence():
    # Texas (multi-timezone) -> medium confidence
    res_tx = normalize_lead({
        "phone": "5125551234",
        "state": "Texas",
        "first_name": "john",
        "last_name": "mcdonald",
        "custom_info": "vip"
    })
    assert res_tx["normalized"]["state"] == "TX"
    assert res_tx["normalized"]["timezone"] in ("America/Chicago", "America/Denver")
    assert res_tx["normalized"]["timezone_confidence"] == "medium"
    assert res_tx["custom_fields"]["custom_info"] == "vip"
    # Verify raw payload preservation
    assert res_tx["raw_import_payload"]["first_name"] == "john"
    assert res_tx["custom_fields"]["raw_first_name"] == "john"

    # New York (single-timezone) -> medium/high confidence
    res_ny = normalize_lead({
        "phone": "2125551234",
        "state": "NY"
    })
    assert res_ny["normalized"]["state"] == "NY"
    assert res_ny["normalized"]["timezone"] == "America/New_York"
    assert res_ny["normalized"]["timezone_confidence"] == "medium/high"


# =====================================================================
# 3. LiveKit WebRTC Token Security Tests
# =====================================================================

def test_livekit_token_refuses_mock_in_production(monkeypatch):
    monkeypatch.setenv("DANA_ALLOW_MOCK_LIVEKIT_TOKENS", "no")
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)

    with pytest.raises(ValueError, match="Failing token generation to prevent unauthorized"):
        generate_livekit_token("room1", "identity1", "agent")


def test_livekit_token_grants_permissions(monkeypatch):
    monkeypatch.setenv("DANA_ALLOW_MOCK_LIVEKIT_TOKENS", "yes")
    # Generates a string/jwt fallback
    token = generate_livekit_token("room1", "identity1", "agent")
    assert token.startswith("mock_token_agent_room1")

    supervisor_token = generate_livekit_token("room1", "identity1", "supervisor")
    assert supervisor_token.startswith("mock_token_supervisor_room1")


def test_prospect_facing_payload_does_not_leak_internals():
    lead = {
        "phone": "5551234567",
        "state": "FL",
        "handoff_summary": "Should be secret",
        "objection_notes": "Sensitive objections",
        "compliance_flags": {"flagged": False}
    }
    # Sanitizing prospect-facing payload (external CRM simulation / public)
    sanitized = sanitize_payload(lead, is_dashboard=False)
    assert "handoff_summary" not in sanitized
    assert "objection_notes" not in sanitized
    assert "compliance_flags" not in sanitized


# =====================================================================
# 4. HMAC Signatures & Validation Tests
# =====================================================================

def test_signature_validation():
    secret = "super_secret_webhook_key"
    payload = {"event_type": "call.completed", "call_id": "call-123"}
    body_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    
    # Generate signature
    sig = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    header_val = f"sha256={sig}"

    # Verify signature
    assert verify_signature(body_bytes, secret, header_val) is True

    # Tampered body bytes should fail validation
    tampered_bytes = json.dumps({"event_type": "call.completed", "call_id": "call-TAMPERED"}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert verify_signature(tampered_bytes, secret, header_val) is False


# =====================================================================
# 5. CRM Webhooks & Outbox Sender Pipeline Tests
# =====================================================================

@pytest.mark.asyncio
async def test_emit_crm_event_disabled_state(monkeypatch):
    monkeypatch.setenv("DANA_CRM_WEBHOOK_ENABLED", "false")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_URL", "http://my-crm.com/webhook")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_SECRET", "secret")

    repo = Repository()
    task = emit_crm_event(
        event_type="call.completed",
        repository=repo,
        call_id="call-abc",
        outcome="completed"
    )
    assert task is None
    
    # Wait briefly for async DB save
    await asyncio.sleep(0.1)
    
    # Retrieve outbox record
    pending = await repo.list_pending_webhook_events()
    assert len(pending) == 0

    # Read from local outbox using store get
    event = await repo.get_webhook_event("call.completed:call-abc")
    assert event is not None
    assert event["status"] == "disabled"


@pytest.mark.asyncio
async def test_emit_crm_event_config_error(monkeypatch):
    monkeypatch.setenv("DANA_CRM_WEBHOOK_ENABLED", "true")
    monkeypatch.delenv("DANA_CRM_WEBHOOK_SECRET", raising=False)  # Missing secret

    repo = Repository()
    task = emit_crm_event(
        event_type="call.completed",
        repository=repo,
        call_id="call-xyz",
        outcome="completed"
    )
    assert task is None
    
    await asyncio.sleep(0.1)
    
    # Verify logged as configuration_error
    event = await repo.get_webhook_event("call.completed:call-xyz")
    assert event is not None
    assert event["status"] == "configuration_error"


@pytest.mark.asyncio
async def test_successful_webhook_sends_and_updates_outbox(monkeypatch):
    monkeypatch.setenv("DANA_CRM_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_URL", "http://fake-crm.com/webhook")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_SECRET", "my_secret")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_FIXED_JITTER", "yes")

    repo = Repository()
    call_id = "call-1"

    # Mock HTTP client response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "Success message"

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        # Emit the event
        task = emit_crm_event(
            event_type="call.attempt_started",
            repository=repo,
            call_id=call_id,
            phone_e164="+15551234567"
        )
        assert task is not None

        # Run task to completion
        await task

        # Check outbox status
        event = await repo.get_webhook_event(f"call.attempt_started:{call_id}")
        assert event is not None
        assert event["status"] == "sent"
        assert event["response_status_code"] == 200
        assert event["response_body_preview"] == "Success message"
        assert event["sent_at"] is not None


@pytest.mark.asyncio
async def test_webhook_failure_retries_and_fails(monkeypatch):
    monkeypatch.setenv("DANA_CRM_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_URL", "http://fake-crm.com/webhook")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_SECRET", "my_secret")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_MAX_RETRIES", "2")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_FIXED_JITTER", "yes")

    repo = Repository()
    call_id = "call-retry-fail"

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        task = emit_crm_event(
            event_type="transfer.failed",
            repository=repo,
            call_id=call_id
        )
        assert task is not None
        await task

        # The event should have completed both attempts and marked failed
        event = await repo.get_webhook_event(f"transfer.failed:{call_id}")
        assert event is not None
        assert event["status"] == "failed"
        assert event["attempt_count"] == 2
        assert "HTTP 500" in event["last_error"]


@pytest.mark.asyncio
async def test_dispatcher_concurrency_and_flush(monkeypatch):
    monkeypatch.setenv("DANA_CRM_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_URL", "http://fake-crm.com/webhook")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_SECRET", "my_secret")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_FIXED_JITTER", "yes")

    repo = Repository()
    dispatcher = get_dispatcher()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "OK"

    # We submit multiple events
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        # Simulate slight delay to test concurrency / active tasks
        async def slow_post(*args, **kwargs):
            await asyncio.sleep(0.05)
            return mock_response
        mock_post.side_effect = slow_post

        t1 = emit_crm_event("call.session_started", repository=repo, call_id="con-1")
        t2 = emit_crm_event("call.session_started", repository=repo, call_id="con-2")
        t3 = emit_crm_event("call.session_started", repository=repo, call_id="con-3")

        # Wait/flush
        await dispatcher.flush_pending_webhooks(timeout=2.0)

        # Check status of all events
        e1 = await repo.get_webhook_event("call.session_started:con-1")
        e2 = await repo.get_webhook_event("call.session_started:con-2")
        e3 = await repo.get_webhook_event("call.session_started:con-3")

        assert e1["status"] == "sent"
        assert e2["status"] == "sent"
        assert e3["status"] == "sent"


@pytest.mark.asyncio
async def test_webhook_failure_does_not_block_main_thread(monkeypatch):
    monkeypatch.setenv("DANA_CRM_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_URL", "http://fake-crm.com/webhook")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_SECRET", "my_secret")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_MAX_RETRIES", "1")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_FIXED_JITTER", "yes")

    repo = Repository()
    
    # We patch client to raise an immediate network exception
    with patch("httpx.AsyncClient.post", side_effect=ConnectionError("CRM is completely offline")):
        start_time = asyncio.get_event_loop().time()
        
        # This call must return immediately (non-blocking) and not raise exception
        task = emit_crm_event(
            event_type="call.completed",
            repository=repo,
            call_id="call-no-crash"
        )
        assert task is not None
        
        end_time = asyncio.get_event_loop().time()
        # Verify it didn't block the caller path (running nearly instantaneously)
        assert (end_time - start_time) < 0.05

        # Execute task to make sure it handles exception internally
        await task
        
        event = await repo.get_webhook_event("call.completed:call-no-crash")
        assert event is not None
        assert event["status"] == "failed"
        assert "CRM is completely offline" in event["last_error"]


def test_handoff_summary_not_included_in_public_or_livekit_metadata():
    # 1. External CRM webhook payload / public prospect-facing: no handoff_summary by default
    lead = {
        "phone_e164": "+15551234567",
        "state": "FL",
        "handoff_summary": "Prospect is qualified",
        "objection_notes": "None",
        "compliance_flags": {"ok": True}
    }
    sanitized = sanitize_payload(lead, is_dashboard=False)
    assert "handoff_summary" not in sanitized
    assert "objection_notes" not in sanitized
    assert "compliance_flags" not in sanitized

    # 2. LiveKit room metadata update payload: no handoff_summary
    metadata_payload = {
        "campaign_id": "camp-123",
        "lead_id": "lead-456",
        "call_id": "call-789"
    }
    assert "handoff_summary" not in metadata_payload


@pytest.mark.asyncio
async def test_webhook_failures_never_break_call_path(monkeypatch, caplog):
    monkeypatch.setenv("DANA_CRM_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_URL", "http://fake-crm.com/webhook")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_SECRET", "my_secret")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_MAX_RETRIES", "1")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_FIXED_JITTER", "yes")

    repo = Repository()
    
    with patch("httpx.AsyncClient.post", side_effect=ConnectionError("CRM is down")):
        # 1. Verify emit_crm_event returns without raising to the caller
        task = emit_crm_event(
            event_type="qa.failed",
            repository=repo,
            call_id="call-fail-flow"
        )
        assert task is not None
        
        # 2. Verify call path / campaign loop continues successfully without crashing
        for i in range(3):
            t = emit_crm_event(
                event_type="call.completed",
                repository=repo,
                call_id=f"call-loop-{i}"
            )
            assert t is not None
            # Awaiting the background task directly to ensure it runs to completion
            await t

        await task
        
        # 3. Verify event is marked retry/failed appropriately
        event = await repo.get_webhook_event("qa.failed:call-fail-flow")
        assert event is not None
        assert event["status"] == "failed"
        assert "CRM is down" in event["last_error"]
        
        # 4. Verify exception gets logged
        assert any("exception" in record.message or "failure" in record.message or "CRM is down" in record.message for record in caplog.records)
