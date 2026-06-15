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

    async def execute_leave(
        self,
        room_name: str,
        agent_identity: str,
        lkapi: Optional[Any] = None
    ) -> bool:
        """Remove Dana's participant or mute audio so the humans can talk privately."""
        logger.info("Executing DanaLeaveStrategy: Removing agent participant '%s' from room '%s'", agent_identity, room_name)
        
        is_production_or_live = (
            os.getenv("DANA_RUNTIME_ENV") == "production"
            or os.getenv("DANA_CONTROLLED_LIVE_TEST", "").strip().lower() == "true"
        )
        
        if lkapi:
            try:
                import inspect
                from livekit.api import RoomParticipantIdentity
                remove_req = RoomParticipantIdentity(room=room_name, identity=agent_identity)
                res = lkapi.room.remove_participant(remove=remove_req)
                if inspect.isawaitable(res):
                    await res
                logger.info("DANA_LEFT_OR_MUTED_AFTER_BRIDGE: Successfully removed Dana's participant '%s' from room '%s'", agent_identity, room_name)
                return True
            except Exception as e:
                logger.exception("Failed to remove Dana's participant via LiveKit API: %s", e)
                if is_production_or_live:
                    return False
        else:
            if is_production_or_live:
                logger.error("No lkapi provided for DanaLeaveStrategy in production/controlled live mode. Cannot proceed.")
                return False
        
        logger.info("DANA_LEFT_OR_MUTED_AFTER_BRIDGE: Simulating leave/mute for agent participant '%s' in room '%s'", agent_identity, room_name)
        return True


class WarmBridgeProvider:
    """Abstract interface for warm bridging prospect and agent."""

    async def initiate_warm_bridge(
        self,
        room_name: str,
        agent: LicensedAgent,
        summary: str,
        dana_identity: str = "dana_voice_agent",
        call_id: Optional[str] = None,
        prospect_identity: Optional[str] = None
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
        dana_identity: str = "dana_voice_agent",
        call_id: Optional[str] = None,
        prospect_identity: Optional[str] = None
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

        logger.info("DANA_TRANSFER_BRIDGE_STARTED: Initiating warm bridge transfer for room '%s' to agent '%s'", room_name, agent.name)

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
            
            logger.info("LICENSED_AGENT_SIP_PARTICIPANT_CREATED: SIP participant created successfully. Participant ID: %s", participant_id)

            # Whisper Summary & Execute Dana leave strategy
            # Note: Whisper audio playback would be triggered here in production.
            # Then execute leave strategy to remove Dana from room.
            leave_success = await self.leave_strategy.execute_leave(room_name, dana_identity, lkapi=lkapi)
            if not leave_success:
                logger.error("Dana leave strategy failed.")
                return WarmBridgeResult(
                    success=False,
                    reason="dana_leave_failed",
                    provider_call_id=participant_id,
                    transfer_mode="failed"
                )

            logger.info("TRANSFER_READY_FOR_HUMAN_AGENT: Warm bridge completed. Prospect and agent are now connected in room '%s'", room_name)

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
