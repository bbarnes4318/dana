from __future__ import annotations
import os
import logging
from typing import Any, Optional
from dana.providers.base import TelephonyProvider
from telephony.livekit_adapter import LiveKitOutboundAdapter, LiveKitDialConfig
from telephony.warm_bridge import LiveKitWarmBridgeProvider
from telephony.agent_availability import LicensedAgent

logger = logging.getLogger(__name__)

class LiveKitSIPTelephonyProvider(TelephonyProvider):
    def __init__(self) -> None:
        self._adapter = LiveKitOutboundAdapter()
        self._warm_bridge = LiveKitWarmBridgeProvider()

    async def health_check(self) -> bool:
        trunk_id = os.getenv("LIVEKIT_SIP_OUTBOUND_TRUNK_ID")
        return bool(trunk_id and trunk_id != "replace_me")

    @property
    def name(self) -> str:
        return "livekit_sip"

    @property
    def supports_outbound(self) -> bool:
        return True

    @property
    def supports_transfer(self) -> bool:
        return True

    @property
    def supports_recording(self) -> bool:
        return True

    @property
    def supports_warm_bridge(self) -> bool:
        return True

    async def originate_call(self, destination: str, **kwargs) -> Any:
        room_name = kwargs.get("room_name") or f"dana-call-{os.urandom(4).hex()}"
        identity = kwargs.get("participant_identity") or f"prospect-{os.urandom(4).hex()}"
        config = LiveKitDialConfig(
            room_name=room_name,
            phone_number=destination,
            participant_identity=identity,
            live_mode=self._adapter.live_mode_enabled(),
            caller_id=kwargs.get("caller_id") or os.getenv("DANA_OUTBOUND_CALLER_ID"),
            metadata=kwargs.get("metadata") or {}
        )
        return await self._adapter.dial(config)

    async def end_call(self, call_id: str) -> bool:
        # End call locally and close room
        try:
            from storage.repository import Repository
            from telephony.call_control import TelephonyCallControl
            control = TelephonyCallControl(Repository())
            res = await control.end_call(call_id, operator="system", reason="call_completed")
            return res.success
        except Exception as e:
            logger.error(f"Error ending LiveKit SIP call: {e}")
            return False

    async def transfer_call(self, call_id: str, destination: str, warm: bool = False) -> bool:
        if warm:
            agent = LicensedAgent(
                agent_id="transfer-agent",
                name="Licensed Agent",
                phone_number=destination,
                licensed_states=["*"],
                status="available"
            )
            res = await self._warm_bridge.initiate_warm_bridge(
                room_name=call_id,  # Room name is typically call_id
                agent=agent,
                summary="Qualified final expense lead"
            )
            return res.success
        else:
            # Cold transfer
            try:
                from telephony.cold_transfer import TelnyxColdTransferProvider
                cold = TelnyxColdTransferProvider()
                res = await cold.initiate_cold_transfer(
                    call_id=call_id,
                    agent_phone_number=destination
                )
                return res.success
            except Exception as e:
                logger.error(f"Error executing cold transfer: {e}")
                return False
