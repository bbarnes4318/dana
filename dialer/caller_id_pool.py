"""Caller ID pool rotation and metric management."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from storage.repository import Repository


class CallerIdPool:
    """Manages caller ID pools, rotation, limit enforcement, and performance cooldowns."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    async def get_next_caller_id(
        self, campaign_id: str, campaign_config: dict[str, Any], now: Optional[datetime] = None
    ) -> Optional[str]:
        """Select the next eligible rotated caller ID using Least-Recently-Used (LRU).

        Enforces daily call limits and cooldown restrictions.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        caller_ids = await self.repository.list_caller_ids(campaign_id)
        if not caller_ids:
            return None

        daily_limit = campaign_config.get("caller_id_daily_limit", 200)

        eligible_cids = []
        for cid in caller_ids:
            # 1. Skip inactive status
            if cid.get("status") == "inactive":
                continue

            # 2. Check cooldown status
            cooldown_until = cid.get("cooldown_until")
            if cooldown_until:
                if isinstance(cooldown_until, str):
                    try:
                        cooldown_until = datetime.fromisoformat(cooldown_until.replace("Z", "+00:00"))
                    except ValueError:
                        pass
                if isinstance(cooldown_until, datetime):
                    if cooldown_until.tzinfo is None:
                        cooldown_until = cooldown_until.replace(tzinfo=timezone.utc)
                    if cooldown_until > now:
                        # Still in cooldown
                        continue

            # 3. Check daily limits
            if cid.get("daily_call_count", 0) >= daily_limit:
                continue

            eligible_cids.append(cid)

        if not eligible_cids:
            return None

        # Sort by last_used_at ASC (None/empty first)
        def get_sort_key(c: dict[str, Any]) -> tuple[int, datetime]:
            last_used = c.get("last_used_at")
            if not last_used:
                # Never used - prioritised
                return 0, datetime.min.replace(tzinfo=timezone.utc)
            if isinstance(last_used, str):
                try:
                    last_used = datetime.fromisoformat(last_used.replace("Z", "+00:00"))
                except ValueError:
                    pass
            if isinstance(last_used, datetime):
                if last_used.tzinfo is None:
                    last_used = last_used.replace(tzinfo=timezone.utc)
                return 1, last_used
            return 1, datetime.min.replace(tzinfo=timezone.utc)

        eligible_cids.sort(key=get_sort_key)
        selected_cid = eligible_cids[0]["caller_id"]
        return selected_cid

    async def mark_used(self, caller_id: str, campaign_id: str, now: Optional[datetime] = None) -> None:
        """Mark a caller ID as used, incrementing its usage counters."""
        await self.repository.mark_caller_id_used(caller_id, campaign_id, now)

    async def update_metrics_and_cooldown(
        self, caller_id: str, campaign_id: str, campaign_config: dict[str, Any], outcome: str, now: Optional[datetime] = None
    ) -> None:
        """Update metrics for caller ID and trigger auto-cooldown if performance is low."""
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        # Update metrics (answers, dncs, and rate calculation)
        await self.repository.update_caller_id_metrics(caller_id, campaign_id, outcome)

        # Fetch latest state to check threshold and cooldowns
        cid = await self.repository.get_caller_id(caller_id, campaign_id)
        if not cid:
            return

        min_calls = campaign_config.get("caller_id_min_calls_threshold", 10)
        min_answer_rate = campaign_config.get("caller_id_min_answer_rate", 0.05)  # 5%
        max_dnc_rate = campaign_config.get("caller_id_max_dnc_rate", 0.10)        # 10%
        cooldown_duration = campaign_config.get("caller_id_cooldown_duration_seconds", 3600)  # 1 hour

        total_calls = cid.get("total_calls", 0)
        if total_calls >= min_calls:
            answer_rate = cid.get("answer_rate", 0.0)
            dnc_rate = cid.get("dnc_rate", 0.0)

            reason = None
            if answer_rate < min_answer_rate:
                reason = f"Answer rate too low ({answer_rate:.2%} < {min_answer_rate:.2%})"
            elif dnc_rate > max_dnc_rate:
                reason = f"DNC rate too high ({dnc_rate:.2%} > {max_dnc_rate:.2%})"

            if reason:
                cooldown_until = now + timedelta(seconds=cooldown_duration)
                await self.repository.set_caller_id_cooldown(caller_id, campaign_id, cooldown_until, reason)
