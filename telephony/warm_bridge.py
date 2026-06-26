"""
LiveKit Warm Bridge Provider
Places an outbound call to the licensed agent and bridges them with the prospect in a LiveKit room.
Includes safety gates, credentials check, and the Dana Leave Strategy to remove Dana without disconnecting the call.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from telephony.agent_availability import LicensedAgent

logger = logging.getLogger(__name__)


@dataclass
class WarmBridgeResult:
    """Outcome of attempting a warm bridge transfer."""
    success: bool
    reason: str
    provider_call_id: Optional[str] = None
    transfer_mode: Optional[str] = None  # e.g., "dry_run", "warm_bridge", "failed"


class DanaLeaveStrategy:
    """Defines how the voice agent leaves the room after bridging is complete."""

    async def execute_leave(self, room_name: str, agent_identity: str) -> bool:
        """Remove Dana's participant or mute audio so the humans can talk privately.
        
        Real implementation would query the LiveKitAPI room participant list and call:
        await lkapi.room.remove_participant(room=room_name, identity=agent_identity)
        """
        logger.info("Executing DanaLeaveStrategy: Removing agent participant '%s' from room '%s'", agent_identity, room_name)
        # Safe stub: simulate success
        return True


class WarmBridgeProvider:
    """Abstract interface for warm bridging prospect and agent."""

    async def initiate_warm_bridge(
        self,
        room_name: str,
        agent: LicensedAgent,
        summary: str,
        dana_identity: str = "dana_voice_agent"
    ) -> WarmBridgeResult:
        """Place outbound call to agent, play summary, and bridge with prospect."""
        raise NotImplementedError


class LiveKitWarmBridgeProvider(WarmBridgeProvider):
    """Integrates with LiveKit SIP Outbound Call API to execute a warm bridge."""

    def __init__(self, leave_strategy: Optional[DanaLeaveStrategy] = None) -> None:
        self.leave_strategy = leave_strategy or DanaLeaveStrategy()

    async def initiate_warm_bridge(
        self,
        room_name: str,
        agent: LicensedAgent,
        summary: str,
        dana_identity: str = "dana_voice_agent"
    ) -> WarmBridgeResult:
        # 1. Safety Gate Check
        confirm_transfer = os.getenv("DANA_CONFIRM_TRANSFER_CALL", "no").strip().lower() == "yes"
        if not confirm_transfer:
            logger.warning("Warm bridge transfer BLOCKED by safety gate (DANA_CONFIRM_TRANSFER_CALL != yes)")
            return WarmBridgeResult(
                success=False,
                reason="transfer_not_confirmed",
                provider_call_id=None,
                transfer_mode="dry_run"
            )

        # 2. Check Provider Credentials
        livekit_url = os.getenv("LIVEKIT_URL")
        api_key = os.getenv("LIVEKIT_API_KEY")
        api_secret = os.getenv("LIVEKIT_API_SECRET")
        trunk_id = os.getenv("LIVEKIT_SIP_OUTBOUND_TRUNK_ID")

        if not (livekit_url and api_key and api_secret and trunk_id):
            logger.error("LiveKit warm bridge provider is missing credentials or trunk configuration.")
            return WarmBridgeResult(
                success=False,
                reason="provider_not_configured",
                provider_call_id=None,
                transfer_mode="failed"
            )

        # 3. Connect to LiveKit API
        try:
            from livekit import api
        except ImportError:
            logger.error("LiveKit SDK (livekit-api) is not installed.")
            return WarmBridgeResult(
                success=False,
                reason="provider_not_configured",
                provider_call_id=None,
                transfer_mode="failed"
            )

        logger.info("Placing LiveKit SIP outbound call to agent: %s", agent.name)
        lkapi = api.LiveKitAPI(
            url=livekit_url,
            api_key=api_key,
            api_secret=api_secret
        )

        try:
            # Check if SIP API exists on the client
            if not hasattr(lkapi, "sip") or not hasattr(lkapi.sip, "create_sip_participant"):
                logger.error("Installed LiveKit SDK does not support create_sip_participant.")
                return WarmBridgeResult(
                    success=False,
                    reason="provider_call_failed",
                    provider_call_id=None,
                    transfer_mode="failed"
                )

            # Build SIP Request
            request = api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk_id,
                sip_call_to=agent.phone_number,
                room_name=room_name,
                participant_identity=f"agent-{agent.agent_id}",
                participant_metadata=summary
            )

            # Call the LiveKit API
            participant = await lkapi.sip.create_sip_participant(request)
            participant_id = getattr(participant, "participant_id", "unknown-participant-id")
            
            logger.info("LiveKit SIP Outbound call successfully initiated. Participant ID: %s", participant_id)

            # Whisper Summary & Execute Dana leave strategy
            # Note: Whisper audio playback would be triggered here in production.
            # Then execute leave strategy to remove Dana from room.
            await self.leave_strategy.execute_leave(room_name, dana_identity)

            return WarmBridgeResult(
                success=True,
                reason="success",
                provider_call_id=participant_id,
                transfer_mode="warm_bridge"
            )

        except Exception as e:
            logger.exception("LiveKit Warm Bridge API call failed: %s", e)
            return WarmBridgeResult(
                success=False,
                reason="provider_call_failed",
                provider_call_id=None,
                transfer_mode="failed"
            )
        finally:
            await lkapi.aclose()
