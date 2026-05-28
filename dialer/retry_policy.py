"""Retry policy logic for outbound campaigns."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional


class RetryPolicy:
    """Calculates retry eligibility and next calling times for leads."""

    @staticmethod
    def get_retry_after(
        outcome: str,
        campaign_config: dict[str, Any],
        attempts: int,
        now: datetime,
        callback_time: Optional[datetime] = None
    ) -> Optional[datetime]:
        """Determine when a lead can be retried next.

        Returns:
            A datetime object for when the lead can be called again,
            or None if the lead should not be retried.
        """
        # Ensure now is timezone-aware
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        # 1. Check final outcomes (never retried)
        final_outcomes = {
            "dnc",
            "wrong_number",
            "hostile_refusal",
            "disconnected_bad_number",
            "disconnected",
            "consent_invalid"
        }
        if outcome in final_outcomes:
            return None

        # 2. Callback time overrides standard retry policy (but not final outcomes)
        if callback_time is not None:
            if callback_time.tzinfo is None:
                callback_time = callback_time.replace(tzinfo=timezone.utc)
            return callback_time

        # 3. Check attempts limit
        max_attempts = campaign_config.get("max_attempts", 3)
        if attempts >= max_attempts:
            return None

        # 4. Calculate cooldown based on outcome
        cooldown_seconds = None

        if outcome == "no_answer":
            cooldown_seconds = campaign_config.get("cooldown_no_answer", 7200)  # 2 hours
        elif outcome == "busy":
            cooldown_seconds = campaign_config.get("cooldown_busy", 1800)  # 30 minutes
        elif outcome == "voicemail":
            voicemail_retry = campaign_config.get("voicemail_retry_allowed", False)
            if voicemail_retry:
                cooldown_seconds = campaign_config.get("cooldown_voicemail", 14400)  # 4 hours
            else:
                return None
        elif outcome in ("carrier_failure", "failed_to_place_call"):
            cooldown_seconds = campaign_config.get("cooldown_carrier_failure", 3600)  # 1 hour
        elif outcome == "transient_call_failure":
            cooldown_seconds = campaign_config.get("cooldown_transient_failure", 300)  # 5 minutes
        else:
            # Default fallback cooldown
            cooldown_seconds = campaign_config.get("cooldown_default", 300)

        return now + timedelta(seconds=cooldown_seconds)
