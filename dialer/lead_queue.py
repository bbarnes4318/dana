"""Lead queue wrapper for managing lead selection, locking, and updates."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from storage.repository import Repository


class LeadQueue:
    """Interface for retrieving and updating leads during outbound campaigns."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    async def get_next_eligible_lead(
        self, campaign_id: str, lock_holder_id: str, now: Optional[datetime] = None
    ) -> Optional[dict[str, Any]]:
        """Atomically select and lock the next eligible lead for dialing."""
        return await self.repository.select_and_lock_next_lead(campaign_id, lock_holder_id, now)

    async def release_lead_on_failure(
        self,
        lead_id: str,
        reason: str,
        retry_after: Optional[datetime] = None,
        status_override: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        """Release the lock on a lead and update its status/retry cooldown according to failure reason."""
        return await self.repository.release_lead_lock(lead_id, reason, retry_after, status_override)

    async def mark_completed(self, lead_id: str, outcome: str = "completed") -> Optional[dict[str, Any]]:
        """Mark a lead as completed (no further campaign dialing needed)."""
        return await self.repository.mark_lead_completed(lead_id, outcome)

    async def mark_dnc(
        self, lead_id: str, phone_e164: str, campaign_id: str, reason: str
    ) -> Optional[dict[str, Any]]:
        """Mark lead as Do Not Call (DNC) and register request."""
        return await self.repository.mark_lead_dnc(lead_id, phone_e164, campaign_id, reason)

    async def mark_wrong_number(
        self,
        lead_id: str,
        phone_e164: Optional[str] = None,
        campaign_id: Optional[str] = None,
        reason: str = "wrong_number"
    ) -> Optional[dict[str, Any]]:
        """Mark lead as wrong number to prevent any future retry attempts."""
        return await self.repository.mark_lead_wrong_number(lead_id, phone_e164, campaign_id, reason)

    async def mark_callback_scheduled(self, lead_id: str, callback_time: datetime) -> Optional[dict[str, Any]]:
        """Schedule a callback time for the lead, clearing locks."""
        return await self.repository.mark_lead_callback(lead_id, callback_time)
