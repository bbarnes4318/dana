import os
import json
from typing import Any, Optional, Dict, List, Tuple
from pydantic import BaseModel, Field


class LiveKitDialConfig(BaseModel):
    """Configuration for dialing a lead via LiveKit SIP."""

    live_mode: bool = False
    livekit_url: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    outbound_trunk_id: Optional[str] = None
    room_name: str
    phone_number: str
    caller_id: Optional[str] = None
    participant_identity: str
    metadata: dict = Field(default_factory=dict)
    krisp_enabled: bool = True


class LiveKitDialResult(BaseModel):
    """The outcome of a LiveKit dialing action."""

    success: bool
    dry_run: bool
    live_mode: bool
    room_name: str
    participant_identity: Optional[str] = None
    livekit_participant_id: Optional[str] = None
    livekit_sip_call_id: Optional[str] = None
    provider_call_id: Optional[str] = None
    message: str
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)


class LiveKitOutboundAdapter:
    """Interface to LiveKit server API for placing outbound SIP/PSTN calls."""

    def live_mode_enabled(self) -> bool:
        """Check if live outbound dialing is enabled via environment variables."""
        return (
            os.environ.get("TELEPHONY_LIVE_MODE") == "true"
            and os.environ.get("DANA_ENABLE_OUTBOUND_DIALER") == "true"
        )

    def validate_live_config(self, config: LiveKitDialConfig) -> tuple[bool, list[str]]:
        """Validate that all required credentials and variables are present for live calls."""
        warnings = []
        # Check config fields
        if not config.livekit_url:
            warnings.append("Missing livekit_url config")
        if not config.api_key:
            warnings.append("Missing api_key config")
        if not config.api_secret:
            warnings.append("Missing api_secret config")
        if not config.outbound_trunk_id:
            warnings.append("Missing outbound_trunk_id config")

        # Check env variables
        if not os.environ.get("LIVEKIT_URL"):
            warnings.append("Missing LIVEKIT_URL environment variable")
        if not os.environ.get("LIVEKIT_API_KEY"):
            warnings.append("Missing LIVEKIT_API_KEY environment variable")
        if not os.environ.get("LIVEKIT_API_SECRET"):
            warnings.append("Missing LIVEKIT_API_SECRET environment variable")

        return len(warnings) == 0, warnings

    def build_room_name(self, campaign_id: str, lead_id: str, attempt_id: str, template: str) -> str:
        """Construct a unique LiveKit room name based on template."""
        return template.format(
            campaign_id=campaign_id,
            lead_id=lead_id,
            attempt_id=attempt_id
        )

    def build_participant_identity(self, lead_id: str, attempt_id: str) -> str:
        """Construct a unique LiveKit participant identity for the SIP caller."""
        return f"outbound-{lead_id}-{attempt_id}"

    async def dial(self, config: LiveKitDialConfig) -> LiveKitDialResult:
        """Initiate outbound dial via LiveKit SIP participant creation."""
        # 1. Check if live dialing is disabled (default/local mode)
        if not config.live_mode or not self.live_mode_enabled():
            import uuid
            # Return simulated mock result
            mock_participant_id = f"part_{uuid.uuid4().hex[:8]}"
            mock_sip_call_id = f"sip_{uuid.uuid4().hex[:12]}"
            mock_provider_call_id = f"telnyx_{uuid.uuid4().hex[:12]}"
            
            return LiveKitDialResult(
                success=True,
                dry_run=True,
                live_mode=False,
                room_name=config.room_name,
                participant_identity=config.participant_identity,
                livekit_participant_id=mock_participant_id,
                livekit_sip_call_id=mock_sip_call_id,
                provider_call_id=mock_provider_call_id,
                message="mock dial completed successfully (dry-run mode).",
                warnings=["Dialer is in mock/dry-run mode."],
            )

        # 2. Validate live configuration
        ok, warnings = self.validate_live_config(config)
        if not ok:
            return LiveKitDialResult(
                success=False,
                dry_run=False,
                live_mode=True,
                room_name=config.room_name,
                message="LiveKit adapter configuration validation failed.",
                warnings=warnings,
                error="CONFIG_VALIDATION_FAILED",
            )

        # 3. Dynamic import of LiveKit API to avoid requiring it in unit tests
        try:
            from livekit.api import LiveKitAPI, CreateSIPParticipantRequest
        except ImportError:
            return LiveKitDialResult(
                success=False,
                dry_run=False,
                live_mode=True,
                room_name=config.room_name,
                message="livekit-api library is not installed in this environment.",
                error="SDK_IMPORT_ERROR",
            )

        # 4. Invoke LiveKit API
        try:
            url = config.livekit_url or os.environ.get("LIVEKIT_URL")
            api_key = config.api_key or os.environ.get("LIVEKIT_API_KEY")
            api_secret = config.api_secret or os.environ.get("LIVEKIT_API_SECRET")

            lk_api = LiveKitAPI(url, api_key, api_secret)
            
            # Map request
            request = CreateSIPParticipantRequest(
                sip_trunk_id=config.outbound_trunk_id,
                sip_call_to=config.phone_number,
                room_name=config.room_name,
                participant_identity=config.participant_identity,
                participant_metadata=json.dumps(config.metadata),
                display_name=config.caller_id or "Dana Voicebot"
            )
            
            # Place SIP participant creation request
            sip_participant = await lk_api.sip.create_sip_participant(request)
            await lk_api.aclose()

            return LiveKitDialResult(
                success=True,
                dry_run=False,
                live_mode=True,
                room_name=config.room_name,
                participant_identity=config.participant_identity,
                livekit_participant_id=getattr(sip_participant, "participant_id", None) or getattr(sip_participant, "identity", None),
                livekit_sip_call_id=getattr(sip_participant, "sip_call_id", None),
                provider_call_id=getattr(sip_participant, "sip_call_id", None),  # LiveKit SIP ID maps to provider ID here
                message="LiveKit SIP outbound participant created successfully.",
                data={"sip_participant": str(sip_participant)}
            )
        except Exception as e:
            return LiveKitDialResult(
                success=False,
                dry_run=False,
                live_mode=True,
                room_name=config.room_name,
                message=f"LiveKit API CreateSIPParticipant call failed: {e}",
                error=str(e),
            )
