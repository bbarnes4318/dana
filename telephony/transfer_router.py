"""
Transfer Router
Coordinates transfer routing decisions based on configured mode, safety gates, and agent availability.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

from telephony.agent_availability import AgentAvailabilityStore, LicensedAgent


@dataclass
class TransferRouteDecision:
    """Represents the routing choice made by the TransferRouter."""
    success: bool
    transfer_mode: str  # "warm_bridge" | "cold_transfer" | "callback_required"
    agent: Optional[LicensedAgent] = None
    phone_number: Optional[str] = None
    reason: Optional[str] = None


class TransferRouter:
    """Decides how to route a qualified lead transfer request."""

    def __init__(self, store: AgentAvailabilityStore) -> None:
        self.store = store

    async def route_transfer(
        self,
        lead_profile: dict[str, Any]
    ) -> TransferRouteDecision:
        """Analyze lead profile and system config, select the routing method, and reserve agent if needed.
        
        Args:
            lead_profile: Lead details dictionary (contains lead_state, call_id, etc.)
            
        Returns:
            A TransferRouteDecision indicating the resolved destination.
        """
        call_id = lead_profile.get("call_id", "unknown-call")
        state = lead_profile.get("lead_state")
        
        # Load environment settings
        transfer_mode_config = os.getenv("DANA_TRANSFER_MODE", "auto").strip().lower()
        cold_transfer_enabled = os.getenv("DANA_COLD_TRANSFER_ENABLED", "false").strip().lower() == "true"
        cold_transfer_num = os.getenv("DANA_COLD_TRANSFER_PHONE_NUMBER")

        # 1. Warm Bridge Route
        if transfer_mode_config in ("warm_bridge", "auto"):
            # Attempt to find an available licensed agent
            agent = await self.store.get_available_agent(state)
            if agent:
                # Atomically reserve the agent
                reserved = await self.store.reserve_agent(agent.agent_id, call_id)
                if reserved:
                    return TransferRouteDecision(
                        success=True,
                        transfer_mode="warm_bridge",
                        agent=agent,
                        phone_number=agent.phone_number
                    )
            
            # If warm_bridge is the ONLY configured option, fail directly to callback
            if transfer_mode_config == "warm_bridge":
                reason = "no_agent_available" if state else "missing_state_for_licensed_routing"
                return TransferRouteDecision(
                    success=False,
                    transfer_mode="callback_required",
                    reason=reason
                )

        # 2. Cold Transfer Route
        if transfer_mode_config in ("cold_transfer", "auto"):
            if cold_transfer_enabled and cold_transfer_num:
                return TransferRouteDecision(
                    success=True,
                    transfer_mode="cold_transfer",
                    phone_number=cold_transfer_num
                )
            
            # If cold_transfer was specifically requested but unavailable
            if transfer_mode_config == "cold_transfer":
                return TransferRouteDecision(
                    success=False,
                    transfer_mode="callback_required",
                    reason="cold_transfer_disabled_or_unconfigured"
                )

        # 3. Callback Fallback Route (when both warm and cold routing fails)
        reason = "no_agent_available" if state else "missing_state_for_licensed_routing"
        return TransferRouteDecision(
            success=False,
            transfer_mode="callback_required",
            reason=reason
        )
