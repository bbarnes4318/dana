import os
import uuid
import hmac
import hashlib
import json
import asyncio
import logging
import random
from datetime import datetime, timezone, timedelta
from typing import Any, Optional, Tuple, Callable

import httpx
from storage.repository import Repository
from integrations.webhook_dispatcher import get_dispatcher
from integrations.payload_sanitizer import sanitize_payload

logger = logging.getLogger(__name__)

def get_retry_delay(attempt: int, base_delay: float = 1.0, factor: float = 2.0, use_fixed: bool = False) -> float:
    """Calculate the retry delay using exponential backoff with deterministic or random jitter."""
    delay = base_delay * (factor ** attempt)
    if use_fixed:
        return delay
    # Production: Add random jitter between 0.5 * delay and 1.5 * delay
    jitter = random.uniform(0.5, 1.5)
    return delay * jitter

def verify_signature(body_bytes: bytes, secret: str, signature_header: str) -> bool:
    """Verify that an HMAC-SHA256 signature matches the raw request body bytes."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected_signature = signature_header.split("sha256=")[1]
    actual_signature = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_signature, actual_signature)

async def emit_crm_event_async(
    event_type: str,
    repository: Optional[Repository] = None,
    call_id: Optional[str] = None,
    lead_id: Optional[str] = None,
    campaign_id: Optional[str] = None,
    phone_e164: Optional[str] = None,
    outcome: Optional[str] = None,
    stage: Optional[str] = None,
    lead_profile: Optional[dict] = None,
    transfer: Optional[dict] = None,
    callback: Optional[dict] = None,
    qa: Optional[dict] = None,
    compliance_flags: Optional[dict] = None,
    event_id: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> Optional[asyncio.Task]:
    """Emit a CRM event by persisting it in the outbox and enqueuing it for delivery.
    
    Awaits database persistence before returning.
    """
    webhook_enabled = os.getenv("DANA_CRM_WEBHOOK_ENABLED", "false").lower() == "true"
    destination_url = os.getenv("DANA_CRM_WEBHOOK_URL", "")
    webhook_secret = os.getenv("DANA_CRM_WEBHOOK_SECRET", "")
    max_retries = int(os.getenv("DANA_CRM_WEBHOOK_MAX_RETRIES", "5"))
    timeout_seconds = float(os.getenv("DANA_CRM_WEBHOOK_TIMEOUT_SECONDS", "5"))

    resolved_event_id = event_id or f"{event_type}:{call_id or uuid.uuid4()}"
    resolved_timestamp = timestamp or datetime.now(timezone.utc).isoformat()
    idempotency_key = f"{event_type}:{call_id or resolved_event_id}"

    raw_envelope = {
        "event_id": resolved_event_id,
        "event_type": event_type,
        "idempotency_key": idempotency_key,
        "call_id": call_id,
        "lead_id": lead_id,
        "campaign_id": campaign_id,
        "phone_e164": phone_e164,
        "timestamp": resolved_timestamp,
        "schema_version": "1.0",
        "outcome": outcome,
        "stage": stage,
        "lead_profile": lead_profile,
        "transfer": transfer,
        "callback": callback,
        "qa": qa,
        "compliance_flags": compliance_flags,
    }

    sanitized_envelope = sanitize_payload(raw_envelope, is_dashboard=False)
    repo = repository or Repository()

    try:
        if not webhook_enabled:
            logger.info("CRM webhooks are disabled. Logging event %s locally.", resolved_event_id)
            await repo.save_webhook_event(
                event_id=resolved_event_id,
                event_type=event_type,
                destination=destination_url,
                payload=sanitized_envelope,
                status="disabled",
                last_error="CRM webhooks are disabled (DANA_CRM_WEBHOOK_ENABLED=false)"
            )
            return None

        if not destination_url:
            logger.error("DANA_CRM_WEBHOOK_ENABLED=true but DANA_CRM_WEBHOOK_URL is missing. Mark config_error.")
            await repo.save_webhook_event(
                event_id=resolved_event_id,
                event_type=event_type,
                destination="",
                payload=sanitized_envelope,
                status="configuration_error",
                last_error="Missing CRM webhook URL (DANA_CRM_WEBHOOK_URL)"
            )
            return None

        if not webhook_secret:
            logger.error("DANA_CRM_WEBHOOK_ENABLED=true but DANA_CRM_WEBHOOK_SECRET is missing. Rejecting unsigned webhook.")
            await repo.save_webhook_event(
                event_id=resolved_event_id,
                event_type=event_type,
                destination=destination_url,
                payload=sanitized_envelope,
                status="configuration_error",
                last_error="Missing CRM webhook signing secret (DANA_CRM_WEBHOOK_SECRET)"
            )
            return None

        # Save event in 'pending' state inside outbox (awaited for persistence guarantee!)
        await repo.save_webhook_event(
            event_id=resolved_event_id,
            event_type=event_type,
            destination=destination_url,
            payload=sanitized_envelope,
            status="pending"
        )

        # Enqueue background task via supervised dispatcher
        dispatcher = get_dispatcher()
        task = dispatcher.submit_task(
            lambda: _send_webhook_task(
                event_id=resolved_event_id,
                payload=sanitized_envelope,
                destination=destination_url,
                secret=webhook_secret,
                repo=repo,
                max_retries=max_retries,
                timeout=timeout_seconds
            )
        )
        return task

    except Exception as e:
        logger.exception("Failed to queue CRM webhook event %s: %s", resolved_event_id, e)
        return None

def emit_crm_event(
    event_type: str,
    repository: Optional[Repository] = None,
    call_id: Optional[str] = None,
    lead_id: Optional[str] = None,
    campaign_id: Optional[str] = None,
    phone_e164: Optional[str] = None,
    outcome: Optional[str] = None,
    stage: Optional[str] = None,
    lead_profile: Optional[dict] = None,
    transfer: Optional[dict] = None,
    callback: Optional[dict] = None,
    qa: Optional[dict] = None,
    compliance_flags: Optional[dict] = None,
    event_id: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> Optional[asyncio.Task]:
    """Best-effort compatibility wrapper.
    
    WARNING: Does not guarantee persistence before returning. Should not be used
    in new production async paths. Use emit_crm_event_async instead.
    """
    webhook_enabled = os.getenv("DANA_CRM_WEBHOOK_ENABLED", "false").lower() == "true"
    destination_url = os.getenv("DANA_CRM_WEBHOOK_URL", "")
    webhook_secret = os.getenv("DANA_CRM_WEBHOOK_SECRET", "")
    
    try:
        task = asyncio.create_task(
            emit_crm_event_async(
                event_type=event_type,
                repository=repository,
                call_id=call_id,
                lead_id=lead_id,
                campaign_id=campaign_id,
                phone_e164=phone_e164,
                outcome=outcome,
                stage=stage,
                lead_profile=lead_profile,
                transfer=transfer,
                callback=callback,
                qa=qa,
                compliance_flags=compliance_flags,
                event_id=event_id,
                timestamp=timestamp
            )
        )
        if not webhook_enabled or not destination_url or not webhook_secret:
            return None
        return task
    except Exception as e:
        logger.exception("Failed to schedule best-effort crm event: %s", e)
        return None

async def _send_webhook_task(
    event_id: str,
    payload: dict,
    destination: str,
    secret: str,
    repo: Repository,
    max_retries: int,
    timeout: float
) -> None:
    """Coroutine to execute HTTP POST with exponential backoff and update outbox status."""
    event = await repo.get_webhook_event(event_id)
    current_attempt = event.get("attempt_count") or 0 if event else 0
    fixed_jitter = os.getenv("DANA_CRM_WEBHOOK_FIXED_JITTER", "no").lower() == "yes"

    body_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    
    headers = {
        "Content-Type": "application/json",
        "X-Dana-Event-Id": payload["event_id"],
        "X-Dana-Event-Type": payload["event_type"],
        "X-Dana-Idempotency-Key": payload["idempotency_key"],
        "X-Dana-Signature": f"sha256={signature}",
        "X-Dana-Timestamp": payload["timestamp"]
    }

    while current_attempt < max_retries:
        current_attempt += 1
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    destination,
                    content=body_bytes,
                    headers=headers,
                    timeout=timeout
                )
                
            body_preview = response.text[:200]
            if 200 <= response.status_code < 300:
                await repo.mark_webhook_event_sent(
                    event_id=event_id,
                    delivered_at=datetime.now(timezone.utc),
                    response_status_code=response.status_code,
                    response_body_preview=body_preview
                )
                return
            else:
                error_msg = f"HTTP {response.status_code}: {body_preview}"
                logger.warning("CRM Webhook HTTP failure (attempt %d/%d): %s", current_attempt, max_retries, error_msg)
                
                if current_attempt < max_retries:
                    delay = get_retry_delay(current_attempt - 1, use_fixed=fixed_jitter)
                    next_attempt = datetime.now(timezone.utc) + timedelta(seconds=delay)
                    await repo.mark_webhook_event_retry(
                        event_id=event_id,
                        attempt_count=current_attempt,
                        next_attempt_at=next_attempt,
                        last_error=error_msg
                    )
                    await asyncio.sleep(delay)
                else:
                    await repo.mark_webhook_event_failed(event_id, error_msg, attempt_count=current_attempt)
                    return
        except Exception as e:
            error_msg = f"Network request exception: {str(e)}"
            logger.warning("CRM Webhook network exception (attempt %d/%d): %s", current_attempt, max_retries, error_msg)
            
            if current_attempt < max_retries:
                delay = get_retry_delay(current_attempt - 1, use_fixed=fixed_jitter)
                next_attempt = datetime.now(timezone.utc) + timedelta(seconds=delay)
                await repo.mark_webhook_event_retry(
                    event_id=event_id,
                    attempt_count=current_attempt,
                    next_attempt_at=next_attempt,
                    last_error=error_msg
                )
                await asyncio.sleep(delay)
            else:
                await repo.mark_webhook_event_failed(event_id, error_msg, attempt_count=current_attempt)
                return

async def drain_pending_webhook_events(repository: Repository, worker_id: str, limit: int = 50) -> list[dict]:
    """Claim and send pending integration events from outbox."""
    claimed = await repository.claim_pending_webhook_events(limit, worker_id)
    if not claimed:
        return []

    webhook_secret = os.getenv("DANA_CRM_WEBHOOK_SECRET", "")
    max_retries = int(os.getenv("DANA_CRM_WEBHOOK_MAX_RETRIES", "5"))
    timeout_seconds = float(os.getenv("DANA_CRM_WEBHOOK_TIMEOUT_SECONDS", "5"))

    for event in claimed:
        event_id = event["event_id"]
        destination = event["destination"]
        payload = event["payload"]
        current_attempt = event.get("attempt_count") or 0
        
        if current_attempt >= max_retries:
            await repository.mark_webhook_event_failed(
                event_id,
                f"Drained event had attempt_count ({current_attempt}) >= max_retries ({max_retries}).",
                attempt_count=current_attempt
            )
            continue
            
        if not destination or not webhook_secret:
            await repository.mark_webhook_event_failed(
                event_id,
                "Missing webhook URL or signing secret during outbox drain.",
                attempt_count=current_attempt
            )
            continue

        attempt = current_attempt + 1
        body_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(webhook_secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
        
        headers = {
            "Content-Type": "application/json",
            "X-Dana-Event-Id": payload.get("event_id", event_id),
            "X-Dana-Event-Type": payload.get("event_type", event.get("event_type")),
            "X-Dana-Idempotency-Key": payload.get("idempotency_key", event_id),
            "X-Dana-Signature": f"sha256={signature}",
            "X-Dana-Timestamp": payload.get("timestamp", datetime.now(timezone.utc).isoformat())
        }

        fixed_jitter = os.getenv("DANA_CRM_WEBHOOK_FIXED_JITTER", "no").lower() == "yes"

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    destination,
                    content=body_bytes,
                    headers=headers,
                    timeout=timeout_seconds
                )
            
            body_preview = response.text[:200]
            if 200 <= response.status_code < 300:
                await repository.mark_webhook_event_sent(
                    event_id=event_id,
                    delivered_at=datetime.now(timezone.utc),
                    response_status_code=response.status_code,
                    response_body_preview=body_preview
                )
            else:
                error_msg = f"HTTP {response.status_code}: {body_preview}"
                logger.warning("Drain worker HTTP failure for event %s (attempt %d/%d): %s", event_id, attempt, max_retries, error_msg)
                
                if attempt < max_retries:
                    delay = get_retry_delay(attempt - 1, use_fixed=fixed_jitter)
                    next_attempt = datetime.now(timezone.utc) + timedelta(seconds=delay)
                    await repository.mark_webhook_event_retry(
                        event_id=event_id,
                        attempt_count=attempt,
                        next_attempt_at=next_attempt,
                        last_error=error_msg
                    )
                else:
                    await repository.mark_webhook_event_failed(event_id, error_msg, attempt_count=attempt)
        except Exception as e:
            error_msg = f"Drain worker network exception: {str(e)}"
            logger.warning("Drain worker exception for event %s (attempt %d/%d): %s", event_id, attempt, max_retries, error_msg)
            
            if attempt < max_retries:
                delay = get_retry_delay(attempt - 1, use_fixed=fixed_jitter)
                next_attempt = datetime.now(timezone.utc) + timedelta(seconds=delay)
                await repository.mark_webhook_event_retry(
                    event_id=event_id,
                    attempt_count=attempt,
                    next_attempt_at=next_attempt,
                    last_error=error_msg
                )
            else:
                await repository.mark_webhook_event_failed(event_id, error_msg, attempt_count=attempt)
                
    return claimed

_outbox_worker_task: Optional[asyncio.Task] = None

def start_webhook_outbox_worker(repository: Repository, poll_interval: float = 10.0, worker_id: Optional[str] = None) -> None:
    """Start the background outbox drain worker loop."""
    global _outbox_worker_task
    if _outbox_worker_task is not None and not _outbox_worker_task.done():
        logger.info("Outbox worker is already running.")
        return
        
    resolved_worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
    logger.info("Starting outbox worker %s with poll interval %.1fs", resolved_worker_id, poll_interval)
    
    async def _worker_loop():
        from runtime.hot_state import get_hot_state_store
        hot_store = await get_hot_state_store()
        lock_name = "webhook_outbox_lock"
        lock_expiry = 30
        
        while True:
            try:
                # Try to acquire or refresh lock
                has_lock = await hot_store.acquire_lock(lock_name, resolved_worker_id, lock_expiry)
                if not has_lock:
                    # Maybe we already have it, let's try heartbeat
                    has_lock = await hot_store.heartbeat(lock_name, resolved_worker_id, lock_expiry)
                
                if has_lock:
                    await drain_pending_webhook_events(repository, resolved_worker_id)
                else:
                    logger.debug("Another worker holds the webhook outbox lock. Skipping drain.")
            except asyncio.CancelledError:
                logger.info("Outbox worker loop cancelled.")
                try:
                    await hot_store.release_lock(lock_name, resolved_worker_id)
                except Exception:
                    pass
                raise
            except Exception as e:
                logger.exception("Outbox worker encountered error: %s", e)
            await asyncio.sleep(poll_interval)
            
    _outbox_worker_task = asyncio.create_task(_worker_loop())

def stop_webhook_outbox_worker() -> None:
    """Stop the background outbox drain worker loop."""
    global _outbox_worker_task
    if _outbox_worker_task is not None:
        logger.info("Stopping outbox worker loop...")
        _outbox_worker_task.cancel()
        _outbox_worker_task = None

async def flush_pending_webhooks(timeout: float = 10.0) -> None:
    """Flush the global dispatcher queue."""
    dispatcher = get_dispatcher()
    await dispatcher.flush_pending_webhooks(timeout=timeout)
