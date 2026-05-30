"""Campaign pacing implementation using HotStateStore."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from storage.repository import Repository
from runtime.hot_state import BaseHotStateStore, get_hot_state_store

logger = logging.getLogger(__name__)


class CampaignPacer:
    """Manages and enforces pacing limits (concurrent calls and calls per minute) for campaigns."""

    def __init__(self, repository: Repository, hot_state_store: Optional[BaseHotStateStore] = None) -> None:
        self.repository = repository
        self._hot_state_store = hot_state_store

    async def _get_store(self) -> BaseHotStateStore:
        if self._hot_state_store is None:
            self._hot_state_store = await get_hot_state_store()
        return self._hot_state_store

    def _active_calls_key(self, campaign_id: str) -> str:
        return f"pacing:{campaign_id}:active_calls"

    def _started_calls_key(self, campaign_id: str) -> str:
        return f"pacing:{campaign_id}:started_calls"

    async def get_active_calls(self, campaign_id: str) -> List[str]:
        """Return list of active call IDs for a campaign."""
        store = await self._get_store()
        active = await store.get_json(self._active_calls_key(campaign_id))
        return list(active) if active else []

    async def get_calls_started_last_minute(self, campaign_id: str) -> int:
        """Return the count of calls started in the last 60 seconds."""
        store = await self._get_store()
        key = self._started_calls_key(campaign_id)
        timestamps = await store.get_json(key)
        if not timestamps:
            return 0
        
        now = time.time()
        cutoff = now - 60.0
        valid = [t for t in timestamps if t > cutoff]
        
        # If the list changed (expired items removed), write it back to save space
        if len(valid) != len(timestamps):
            await store.set_json(key, valid, expiry=120)
            
        return len(valid)

    async def can_start_call(self, campaign_id: str) -> bool:
        """Check if starting a new call is allowed under the campaign pacing limits."""
        campaign = await self.repository.get_campaign(campaign_id)
        if not campaign:
            logger.warning("Pacing check failed: Campaign %s not found.", campaign_id)
            return False

        max_concurrent = campaign.get("max_concurrent_calls", 5)
        cpm = campaign.get("calls_per_minute", 20)

        active_calls = await self.get_active_calls(campaign_id)
        started_last_minute = await self.get_calls_started_last_minute(campaign_id)

        if len(active_calls) >= max_concurrent:
            logger.debug(
                "Pacing block for campaign %s: Active calls (%d) >= max_concurrent (%d)",
                campaign_id,
                len(active_calls),
                max_concurrent,
            )
            return False

        if started_last_minute >= cpm:
            logger.debug(
                "Pacing block for campaign %s: Calls started in last minute (%d) >= cpm (%d)",
                campaign_id,
                started_last_minute,
                cpm,
            )
            return False

        return True

    async def mark_call_started(self, campaign_id: str, call_id: str) -> None:
        """Record that a new call has started."""
        store = await self._get_store()
        now = time.time()

        # 1. Add to active calls list
        active_key = self._active_calls_key(campaign_id)
        active = await store.get_json(active_key) or []
        if call_id not in active:
            active.append(call_id)
            await store.set_json(active_key, active)

        # 2. Add to started calls rolling window
        started_key = self._started_calls_key(campaign_id)
        timestamps = await store.get_json(started_key) or []
        cutoff = now - 60.0
        timestamps = [t for t in timestamps if t > cutoff]
        timestamps.append(now)
        await store.set_json(started_key, timestamps, expiry=120)

    async def mark_call_finished(self, campaign_id: str, call_id: str) -> None:
        """Record that a call has finished."""
        store = await self._get_store()
        
        # Remove from active list
        active_key = self._active_calls_key(campaign_id)
        active = await store.get_json(active_key)
        if active and call_id in active:
            active.remove(call_id)
            await store.set_json(active_key, active)
