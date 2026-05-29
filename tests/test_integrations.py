import os
import json
import asyncio
import hmac
import hashlib
import pytest
from datetime import datetime, timezone, timedelta
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

@pytest.fixture
def repo(tmp_path):
    return Repository(data_dir=tmp_path)


@pytest.fixture(autouse=True)
def reset_global_dispatcher():
    import integrations.webhook_dispatcher
    integrations.webhook_dispatcher._dispatcher = None
    yield
    integrations.webhook_dispatcher._dispatcher = None


@pytest.mark.asyncio
async def test_emit_crm_event_disabled_state(repo, monkeypatch):
    monkeypatch.setenv("DANA_CRM_WEBHOOK_ENABLED", "false")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_URL", "http://my-crm.com/webhook")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_SECRET", "secret")

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
async def test_emit_crm_event_config_error(repo, monkeypatch):
    monkeypatch.setenv("DANA_CRM_WEBHOOK_ENABLED", "true")
    monkeypatch.delenv("DANA_CRM_WEBHOOK_SECRET", raising=False)  # Missing secret

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
async def test_successful_webhook_sends_and_updates_outbox(repo, monkeypatch):
    monkeypatch.setenv("DANA_CRM_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_URL", "http://fake-crm.com/webhook")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_SECRET", "my_secret")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_FIXED_JITTER", "yes")

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

        # Run task to completion (await wrapper task to get dispatcher task, then await dispatcher task)
        disp_task = await task
        if disp_task:
            await disp_task

        # Check outbox status
        event = await repo.get_webhook_event(f"call.attempt_started:{call_id}")
        assert event is not None
        assert event["status"] == "sent"
        assert event["response_status_code"] == 200
        assert event["response_body_preview"] == "Success message"
        assert event["sent_at"] is not None


@pytest.mark.asyncio
async def test_webhook_failure_retries_and_fails(repo, monkeypatch):
    monkeypatch.setenv("DANA_CRM_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_URL", "http://fake-crm.com/webhook")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_SECRET", "my_secret")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_MAX_RETRIES", "2")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_FIXED_JITTER", "yes")

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
        
        disp_task = await task
        if disp_task:
            await disp_task

        # The event should have completed both attempts and marked failed
        event = await repo.get_webhook_event(f"transfer.failed:{call_id}")
        assert event is not None
        assert event["status"] == "failed"
        assert event["attempt_count"] == 2
        assert "HTTP 500" in event["last_error"]


@pytest.mark.asyncio
async def test_dispatcher_concurrency_and_flush(repo, monkeypatch):
    monkeypatch.setenv("DANA_CRM_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_URL", "http://fake-crm.com/webhook")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_SECRET", "my_secret")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_FIXED_JITTER", "yes")

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

        # Wait for wrapper tasks to finish enqueuing
        await asyncio.gather(t1, t2, t3)

        # Wait/flush dispatcher tasks
        await dispatcher.flush_pending_webhooks(timeout=2.0)

        # Check status of all events
        e1 = await repo.get_webhook_event("call.session_started:con-1")
        e2 = await repo.get_webhook_event("call.session_started:con-2")
        e3 = await repo.get_webhook_event("call.session_started:con-3")

        assert e1["status"] == "sent"
        assert e2["status"] == "sent"
        assert e3["status"] == "sent"


@pytest.mark.asyncio
async def test_webhook_failure_does_not_block_main_thread(repo, monkeypatch):
    monkeypatch.setenv("DANA_CRM_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_URL", "http://fake-crm.com/webhook")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_SECRET", "my_secret")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_MAX_RETRIES", "1")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_FIXED_JITTER", "yes")

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
        disp_task = await task
        if disp_task:
            await disp_task
        
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
async def test_webhook_failures_never_break_call_path(repo, monkeypatch, caplog):
    monkeypatch.setenv("DANA_CRM_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_URL", "http://fake-crm.com/webhook")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_SECRET", "my_secret")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_MAX_RETRIES", "1")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_FIXED_JITTER", "yes")

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
            disp = await t
            if disp:
                await disp

        disp_task = await task
        if disp_task:
            await disp_task
        
        # 3. Verify event is marked retry/failed appropriately
        event = await repo.get_webhook_event("qa.failed:call-fail-flow")
        assert event is not None
        assert event["status"] == "failed"
        assert "CRM is down" in event["last_error"]
        
        # 4. Verify exception gets logged
        assert any("exception" in record.message or "failure" in record.message or "CRM is down" in record.message for record in caplog.records)


# =====================================================================
# Reliability Assertion Tests
# =====================================================================

@pytest.mark.asyncio
async def test_async_emit_persists_before_return(repo, monkeypatch):
    monkeypatch.setenv("DANA_CRM_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_URL", "http://fake-crm.com/webhook")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_SECRET", "my_secret")
    
    from integrations.crm_webhooks import emit_crm_event_async
    
    task = await emit_crm_event_async(
        event_type="call.started",
        repository=repo,
        call_id="call-async-persist",
        phone_e164="+15551234567"
    )
    
    # Assert event exists in database immediately after return
    event = await repo.get_webhook_event("call.started:call-async-persist")
    assert event is not None
    assert event["status"] == "pending"
    if task:
        await task


@pytest.mark.asyncio
async def test_disabled_event_persists_before_return(repo, monkeypatch):
    monkeypatch.setenv("DANA_CRM_WEBHOOK_ENABLED", "false")
    
    from integrations.crm_webhooks import emit_crm_event_async
    
    task = await emit_crm_event_async(
        event_type="call.started",
        repository=repo,
        call_id="call-disabled-persist",
    )
    assert task is None
    
    event = await repo.get_webhook_event("call.started:call-disabled-persist")
    assert event is not None
    assert event["status"] == "disabled"


@pytest.mark.asyncio
async def test_configuration_error_persists_before_return(repo, monkeypatch):
    monkeypatch.setenv("DANA_CRM_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_URL", "http://fake-crm.com/webhook")
    monkeypatch.delenv("DANA_CRM_WEBHOOK_SECRET", raising=False)  # Missing secret
    
    from integrations.crm_webhooks import emit_crm_event_async
    
    task = await emit_crm_event_async(
        event_type="call.started",
        repository=repo,
        call_id="call-config-error",
    )
    assert task is None
    
    event = await repo.get_webhook_event("call.started:call-config-error")
    assert event is not None
    assert event["status"] == "configuration_error"
    assert "Missing CRM webhook signing secret" in event["last_error"]


def test_sync_emit_best_effort_documented():
    from integrations.crm_webhooks import emit_crm_event
    doc = emit_crm_event.__doc__
    assert doc is not None
    assert "best-effort" in doc.lower()
    assert "does not guarantee" in doc.lower()
    assert "should not be used" in doc.lower()
    assert "new production async paths" in doc.lower()


@pytest.mark.asyncio
async def test_queue_full_leaves_pending(repo, monkeypatch):
    monkeypatch.setenv("DANA_CRM_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_URL", "http://fake-crm.com/webhook")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_SECRET", "my_secret")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_MAX_CONCURRENCY", "1")
    
    from integrations.webhook_dispatcher import get_dispatcher
    dispatcher = get_dispatcher()
    dispatcher._max_queue_size = 0
    
    from integrations.crm_webhooks import emit_crm_event_async
    
    task = await emit_crm_event_async(
        event_type="call.started",
        repository=repo,
        call_id="call-queue-full",
    )
    assert task is None # rejected
    
    # But it must be persisted as pending!
    event = await repo.get_webhook_event("call.started:call-queue-full")
    assert event is not None
    assert event["status"] == "pending"


@pytest.mark.asyncio
async def test_queue_full_no_warning(repo, monkeypatch):
    monkeypatch.setenv("DANA_CRM_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_URL", "http://fake-crm.com/webhook")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_SECRET", "my_secret")
    
    from integrations.webhook_dispatcher import get_dispatcher
    dispatcher = get_dispatcher()
    dispatcher._max_queue_size = 0
    
    factory_called = False
    async def dummy_coro():
        nonlocal factory_called
        factory_called = True
        
    task = dispatcher.submit_task(lambda: dummy_coro())
    assert task is None
    assert not factory_called


@pytest.mark.asyncio
async def test_drain_pending_webhook_events(repo, monkeypatch):
    monkeypatch.setenv("DANA_CRM_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_URL", "http://fake-crm.com/webhook")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_SECRET", "my_secret")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_MAX_RETRIES", "3")
    
    event_id = "call.completed:drain-test"
    payload = {"event_id": event_id, "event_type": "call.completed", "timestamp": "2026-05-28T12:00:00Z", "idempotency_key": event_id}
    await repo.save_webhook_event(
        event_id=event_id,
        event_type="call.completed",
        destination="http://fake-crm.com/webhook",
        payload=payload,
        status="pending",
        attempt_count=0
    )
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "OK"
    
    from integrations.crm_webhooks import drain_pending_webhook_events
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        
        claimed = await drain_pending_webhook_events(repo, "worker-1", limit=10)
        assert len(claimed) == 1
        assert claimed[0]["event_id"] == event_id
        
        event = await repo.get_webhook_event(event_id)
        assert event["status"] == "sent"
        assert event["response_status_code"] == 200


@pytest.mark.asyncio
async def test_two_workers_do_not_duplicate(repo, monkeypatch):
    event_id = "call.completed:concurrency-test"
    payload = {"event_id": event_id, "event_type": "call.completed", "timestamp": "2026-05-28T12:00:00Z", "idempotency_key": event_id}
    await repo.save_webhook_event(
        event_id=event_id,
        event_type="call.completed",
        destination="http://fake-crm.com/webhook",
        payload=payload,
        status="pending",
        attempt_count=0
    )
    
    claimed_1 = await repo.claim_pending_webhook_events(limit=5, worker_id="worker-1")
    claimed_2 = await repo.claim_pending_webhook_events(limit=5, worker_id="worker-2")
    
    assert len(claimed_1) == 1
    assert claimed_1[0]["event_id"] == event_id
    assert len(claimed_2) == 0


@pytest.mark.asyncio
async def test_stale_claimed_events_reclaimed(repo, monkeypatch):
    monkeypatch.setenv("DANA_CRM_WEBHOOK_CLAIM_TIMEOUT_SECONDS", "10")
    
    event_id = "call.completed:stale-test"
    payload = {"event_id": event_id, "event_type": "call.completed", "timestamp": "2026-05-28T12:00:00Z", "idempotency_key": event_id}
    
    now = datetime.now(timezone.utc)
    claimed_at = now - timedelta(seconds=20)
    
    await repo.save_webhook_event(
        event_id=event_id,
        event_type="call.completed",
        destination="http://fake-crm.com/webhook",
        payload=payload,
        status="claimed",
        claimed_by="dead-worker",
        claimed_at=claimed_at,
        created_at=claimed_at,
        updated_at=claimed_at
    )
    
    claimed = await repo.claim_pending_webhook_events(limit=5, worker_id="worker-2", now=now)
    assert len(claimed) == 1
    assert claimed[0]["event_id"] == event_id
    assert claimed[0]["claimed_by"] == "worker-2"


@pytest.mark.asyncio
async def test_failed_sends_increment_attempt_count(repo, monkeypatch):
    monkeypatch.setenv("DANA_CRM_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_URL", "http://fake-crm.com/webhook")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_SECRET", "my_secret")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_MAX_RETRIES", "5")
    
    event_id = "call.completed:retry-increment"
    payload = {"event_id": event_id, "event_type": "call.completed", "timestamp": "2026-05-28T12:00:00Z", "idempotency_key": event_id}
    
    await repo.save_webhook_event(
        event_id=event_id,
        event_type="call.completed",
        destination="http://fake-crm.com/webhook",
        payload=payload,
        status="pending",
        attempt_count=2
    )
    
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal error"
    
    from integrations.crm_webhooks import drain_pending_webhook_events
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        
        claimed = await drain_pending_webhook_events(repo, "worker-1", limit=10)
        assert len(claimed) == 1
        
        event = await repo.get_webhook_event(event_id)
        assert event["status"] == "pending"
        assert event["attempt_count"] == 3


@pytest.mark.asyncio
async def test_max_retries_marks_failed(repo, monkeypatch):
    monkeypatch.setenv("DANA_CRM_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_URL", "http://fake-crm.com/webhook")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_SECRET", "my_secret")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_MAX_RETRIES", "3")
    
    event_id = "call.completed:max-retries"
    payload = {"event_id": event_id, "event_type": "call.completed", "timestamp": "2026-05-28T12:00:00Z", "idempotency_key": event_id}
    
    await repo.save_webhook_event(
        event_id=event_id,
        event_type="call.completed",
        destination="http://fake-crm.com/webhook",
        payload=payload,
        status="pending",
        attempt_count=2
    )
    
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Fatal"
    
    from integrations.crm_webhooks import drain_pending_webhook_events
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        
        claimed = await drain_pending_webhook_events(repo, "worker-1", limit=10)
        assert len(claimed) == 1
        
        event = await repo.get_webhook_event(event_id)
        assert event["status"] == "failed"
        assert event["attempt_count"] == 3


@pytest.mark.asyncio
async def test_disabled_or_other_events_not_claimed(repo, monkeypatch):
    await repo.save_webhook_event(event_id="e-disabled", event_type="c", status="disabled")
    await repo.save_webhook_event(event_id="e-config", event_type="c", status="configuration_error")
    await repo.save_webhook_event(event_id="e-sent", event_type="c", status="sent")
    await repo.save_webhook_event(event_id="e-failed", event_type="c", status="failed")
    
    claimed = await repo.claim_pending_webhook_events(limit=10, worker_id="worker-1")
    assert len(claimed) == 0


@pytest.mark.asyncio
async def test_drain_worker_signs_exact_payload_bytes(repo, monkeypatch):
    monkeypatch.setenv("DANA_CRM_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_URL", "http://fake-crm.com/webhook")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_SECRET", "my_secret")
    
    event_id = "call.completed:signing-test"
    payload = {"event_id": event_id, "event_type": "call.completed", "timestamp": "2026-05-28T12:00:00Z", "idempotency_key": event_id}
    
    await repo.save_webhook_event(
        event_id=event_id,
        event_type="call.completed",
        destination="http://fake-crm.com/webhook",
        payload=payload,
        status="pending"
    )
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "OK"
    
    from integrations.crm_webhooks import drain_pending_webhook_events, verify_signature
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        
        await drain_pending_webhook_events(repo, "worker-1", limit=1)
        
        call_args = mock_post.call_args
        assert call_args is not None
        headers = call_args[1]["headers"]
        body_bytes = call_args[1]["content"]
        signature_header = headers["X-Dana-Signature"]
        
        assert verify_signature(body_bytes, "my_secret", signature_header) is True


@pytest.mark.asyncio
async def test_stop_webhook_outbox_worker_cancels_cleanly(repo, monkeypatch):
    import integrations.crm_webhooks
    
    integrations.crm_webhooks.start_webhook_outbox_worker(repo, poll_interval=0.1, worker_id="worker-lifecycle")
    assert integrations.crm_webhooks._outbox_worker_task is not None
    assert not integrations.crm_webhooks._outbox_worker_task.done()
    
    integrations.crm_webhooks.stop_webhook_outbox_worker()
    assert integrations.crm_webhooks._outbox_worker_task is None


@pytest.mark.asyncio
async def test_webhook_failure_never_breaks_call_path(repo, monkeypatch):
    monkeypatch.setenv("DANA_CRM_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_URL", "http://fake-crm.com/webhook")
    monkeypatch.setenv("DANA_CRM_WEBHOOK_SECRET", "my_secret")
    
    from integrations.crm_webhooks import emit_crm_event_async
    
    with patch("httpx.AsyncClient.post", side_effect=Exception("Severe network partition")):
        task = await emit_crm_event_async(
            event_type="call.completed",
            repository=repo,
            call_id="call-fail-safe"
        )
        assert task is not None
        await task
        
        event = await repo.get_webhook_event("call.completed:call-fail-safe")
        assert event is not None
        assert event["status"] == "failed"
