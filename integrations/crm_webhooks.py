import os
import uuid
import hmac
import hashlib
import json
import asyncio
import logging
import random
from datetime import datetime, timezone, timedelta
from typing import Any, Optional, Tuple

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
    """Emit a CRM event by persisting it in the outbox and queuing it for delivery.
    
    This function is non-blocking and returns immediately, spawning a background task.
    """
    # 1. Enforce strict configuration checks
    webhook_enabled = os.getenv("DANA_CRM_WEBHOOK_ENABLED", "false").lower() == "true"
    destination_url = os.getenv("DANA_CRM_WEBHOOK_URL", "")
    webhook_secret = os.getenv("DANA_CRM_WEBHOOK_SECRET", "")
    max_retries = int(os.getenv("DANA_CRM_WEBHOOK_MAX_RETRIES", "5"))
    timeout_seconds = float(os.getenv("DANA_CRM_WEBHOOK_TIMEOUT_SECONDS", "5"))

    resolved_event_id = event_id or f"{event_type}:{call_id or uuid.uuid4()}"
    resolved_timestamp = timestamp or datetime.now(timezone.utc).isoformat()
    idempotency_key = f"{event_type}:{call_id or resolved_event_id}"

    # Build raw inner envelope
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

    # 2. Deep sanitize the payload to hide PII / internal data
    sanitized_envelope = sanitize_payload(raw_envelope, is_dashboard=False)

    # Initialize repository
    repo = repository or Repository()

    try:
        # Check webhook status and determine target state
        if not webhook_enabled:
            logger.info("CRM webhooks are disabled. Logging event %s locally.", resolved_event_id)
            dispatcher = get_dispatcher()
            dispatcher.submit_task(repo.save_webhook_event(
                event_id=resolved_event_id,
                event_type=event_type,
                destination=destination_url,
                payload=sanitized_envelope,
                status="disabled",
                last_error="CRM webhooks are disabled (DANA_CRM_WEBHOOK_ENABLED=false)"
            ))
            return None

        if not destination_url:
            logger.error("DANA_CRM_WEBHOOK_ENABLED=true but DANA_CRM_WEBHOOK_URL is missing. Mark config_error.")
            dispatcher = get_dispatcher()
            dispatcher.submit_task(repo.save_webhook_event(
                event_id=resolved_event_id,
                event_type=event_type,
                destination="",
                payload=sanitized_envelope,
                status="configuration_error",
                last_error="Missing CRM webhook URL (DANA_CRM_WEBHOOK_URL)"
            ))
            return None

        if not webhook_secret:
            logger.error("DANA_CRM_WEBHOOK_ENABLED=true but DANA_CRM_WEBHOOK_SECRET is missing. Rejecting unsigned webhook.")
            dispatcher = get_dispatcher()
            dispatcher.submit_task(repo.save_webhook_event(
                event_id=resolved_event_id,
                event_type=event_type,
                destination=destination_url,
                payload=sanitized_envelope,
                status="configuration_error",
                last_error="Missing CRM webhook signing secret (DANA_CRM_WEBHOOK_SECRET)"
            ))
            return None

        # Active path: save and send under a single supervised wrapper task
        dispatcher = get_dispatcher()
        async def _save_and_send_webhook_task():
            await repo.save_webhook_event(
                event_id=resolved_event_id,
                event_type=event_type,
                destination=destination_url,
                payload=sanitized_envelope,
                status="pending"
            )
            await _send_webhook_task(
                event_id=resolved_event_id,
                payload=sanitized_envelope,
                destination=destination_url,
                secret=webhook_secret,
                repo=repo,
                max_retries=max_retries,
                timeout=timeout_seconds
            )

        task = dispatcher.submit_task(_save_and_send_webhook_task())
        return task

    except Exception as e:
        logger.exception("Failed to queue CRM webhook event %s: %s", resolved_event_id, e)
        # Fail gracefully: do not raise to caller so the call is not interrupted
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
    # Serialize exact JSON bytes to sign
    body_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    
    # Sign body bytes using HMAC-SHA256
    signature = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    
    headers = {
        "Content-Type": "application/json",
        "X-Dana-Event-Id": payload["event_id"],
        "X-Dana-Event-Type": payload["event_type"],
        "X-Dana-Idempotency-Key": payload["idempotency_key"],
        "X-Dana-Signature": f"sha256={signature}",
        "X-Dana-Timestamp": payload["timestamp"]
    }

    attempt = 0
    fixed_jitter = os.getenv("DANA_CRM_WEBHOOK_FIXED_JITTER", "no").lower() == "yes"

    while attempt < max_retries:
        attempt += 1
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
                logger.warning("CRM Webhook HTTP failure (attempt %d/%d): %s", attempt, max_retries, error_msg)
                
                if attempt < max_retries:
                    delay = get_retry_delay(attempt - 1, use_fixed=fixed_jitter)
                    next_attempt = datetime.now(timezone.utc) + timedelta(seconds=delay)
                    await repo.mark_webhook_event_retry(
                        event_id=event_id,
                        attempt_count=attempt,
                        next_attempt_at=next_attempt,
                        last_error=error_msg
                    )
                    await asyncio.sleep(delay)
                else:
                    await repo.mark_webhook_event_failed(event_id, error_msg, attempt_count=attempt)
                    
        except Exception as e:
            error_msg = f"Network request exception: {str(e)}"
            logger.warning("CRM Webhook network exception (attempt %d/%d): %s", attempt, max_retries, error_msg)
            
            if attempt < max_retries:
                delay = get_retry_delay(attempt - 1, use_fixed=fixed_jitter)
                next_attempt = datetime.now(timezone.utc) + timedelta(seconds=delay)
                await repo.mark_webhook_event_retry(
                    event_id=event_id,
                    attempt_count=attempt,
                    next_attempt_at=next_attempt,
                    last_error=error_msg
                )
                await asyncio.sleep(delay)
            else:
                await repo.mark_webhook_event_failed(event_id, error_msg, attempt_count=attempt)
