"""Transfer queue implementation for live agent routing with callback fallbacks."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from dialer.schemas import TransferQueueItem
from telephony.agent_availability import AgentAvailabilityStore, LicensedAgent

logger = logging.getLogger(__name__)


class TransferQueue:
    """Manages queueing, prioritization, and routing of live transfers to human agents."""

    def __init__(self) -> None:
        self._queue: Dict[str, TransferQueueItem] = {}

    def get_queue_items(self) -> List[TransferQueueItem]:
        """Return all items currently in the transfer queue, sorted by priority (descending) and time (ascending)."""
        items = list(self._queue.values())
        # Sort priority descending, entered_at ascending
        items.sort(key=lambda x: (-x.priority, x.entered_at))
        return items

    def enqueue(
        self,
        call_id: str,
        lead_id: str,
        campaign_id: str,
        priority: int = 0,
        warm_bridge: bool = False
    ) -> TransferQueueItem:
        """Enqueue a new transfer request."""
        item = TransferQueueItem(
            call_id=call_id,
            lead_id=lead_id,
            campaign_id=campaign_id,
            priority=priority,
            entered_at=datetime.now(timezone.utc),
            status="pending",
            warm_bridge=warm_bridge
        )
        self._queue[call_id] = item
        return item

    async def check_agent_availability(
        self,
        state: Optional[str],
        agent_availability_store: AgentAvailabilityStore
    ) -> bool:
        """Check if any licensed agent is currently available for the given state."""
        agent = await agent_availability_store.get_available_agent(state)
        return agent is not None

    async def route_transfer(
        self,
        call_id: str,
        state: Optional[str],
        agent_availability_store: AgentAvailabilityStore,
        repository: Optional[Any] = None
    ) -> Dict[str, Any]:
        """Route a queued call to an available agent.
        
        If an agent is found:
        - Atomically reserves the agent
        - Returns a routing decision dict specifying warm or cold bridge
        
        If no agent is found:
        - Returns a fallback action dict
        """
        item = self._queue.get(call_id)
        if not item:
            return {"status": "error", "reason": "item_not_found"}

        item.attempts += 1
        agent = await agent_availability_store.select_and_reserve_agent(state, call_id)
        
        if agent:
            item.status = "transferring"
            item.agent_id = agent.agent_id
            
            # Record transfer status in repository if provided
            if repository:
                try:
                    await repository.save_transfer(
                        call_id=call_id,
                        lead_id=item.lead_id,
                        transfer_mode="warm" if item.warm_bridge else "cold",
                        agent_id=agent.agent_id,
                        target_phone=agent.phone_number,
                        success=True
                    )
                except Exception as e:
                    logger.error("Failed to record transfer in repository: %s", e)

            return {
                "status": "success",
                "action": "bridge",
                "agent_id": agent.agent_id,
                "agent_name": agent.name,
                "phone_number": agent.phone_number,
                "mode": "warm" if item.warm_bridge else "cold"
            }

        # Fallback callback logic (when no agents are available)
        item.status = "failed"
        return {
            "status": "fallback",
            "action": "schedule_callback",
            "reason": "no_agents_available"
        }

    async def handle_transfer_failure(
        self,
        call_id: str,
        repository: Any,
        fallback_delay_minutes: int = 30,
        now: Optional[datetime] = None
    ) -> datetime:
        """Process transfer fallback, scheduling a callback for the lead and clearing from queue."""
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        item = self._queue.pop(call_id, None)
        callback_time = now + timedelta(minutes=fallback_delay_minutes)

        if item:
            item.status = "callback_scheduled"
            # Mark lead as callback in repository
            try:
                await repository.mark_lead_callback(item.lead_id, callback_time)
                
                # Also log transfer failure event in repository
                await repository.save_transfer(
                    call_id=call_id,
                    lead_id=item.lead_id,
                    transfer_mode="cold",
                    success=False,
                    failure_reason="no_agent_available"
                )
            except Exception as e:
                logger.error("Failed to update database for transfer failure fallback: %s", e)

        return callback_time
