"""Do Not Call (DNC) Registry interface and in-memory implementation."""

from __future__ import annotations

import asyncio
from typing import Dict, List, Optional


class InternalDNCRegistry:
    """Interface for querying and updating the Do Not Call registry."""

    async def contains(self, phone_e164: str, campaign_id: Optional[str] = None) -> bool:
        """Check if a phone number is registered on either the global or campaign DNC list."""
        raise NotImplementedError

    async def add(
        self,
        phone_e164: str,
        reason: str,
        campaign_id: Optional[str] = None,
        call_id: Optional[str] = None
    ) -> None:
        """Add a phone number to the DNC registry."""
        raise NotImplementedError


class InMemoryDNCRegistry(InternalDNCRegistry):
    """In-memory thread-safe implementation of InternalDNCRegistry using asyncio.Lock."""

    def __init__(self) -> None:
        # Maps phone_e164 to a list of dicts: {"campaign_id": ..., "reason": ..., "call_id": ...}
        # If campaign_id is None, it is a global DNC registration.
        self._registry: Dict[str, List[Dict[str, Optional[str]]]] = {}
        self._lock = asyncio.Lock()

    async def contains(self, phone_e164: str, campaign_id: Optional[str] = None) -> bool:
        async with self._lock:
            entries = self._registry.get(phone_e164)
            if not entries:
                return False
            
            for entry in entries:
                entry_campaign = entry.get("campaign_id")
                # 1. Global DNC entries (campaign_id is None) block all calls
                if entry_campaign is None:
                    return True
                # 2. Campaign-specific DNC entries block calls for that specific campaign
                if campaign_id is not None and entry_campaign == campaign_id:
                    return True
            
            return False

    async def add(
        self,
        phone_e164: str,
        reason: str,
        campaign_id: Optional[str] = None,
        call_id: Optional[str] = None
    ) -> None:
        async with self._lock:
            if phone_e164 not in self._registry:
                self._registry[phone_e164] = []
            
            # Prevent duplicate identical entries
            for entry in self._registry[phone_e164]:
                if entry.get("campaign_id") == campaign_id:
                    return

            self._registry[phone_e164].append({
                "campaign_id": campaign_id,
                "reason": reason,
                "call_id": call_id
            })
