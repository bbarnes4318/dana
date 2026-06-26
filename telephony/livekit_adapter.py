import os
import sys

# Safety fallback loading
try:
    from config.env_loader import load_environment
    from config.runtime_env import get_runtime_env
    load_environment()
except ImportError:
    from pathlib import Path
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from config.env_loader import load_environment
    from config.runtime_env import get_runtime_env
    load_environment()

import json
import uuid
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
    participant_name: str = "Dana Outbound Call"
    wait_until_answered: bool = True
    krisp_enabled: bool = True
    metadata: dict = Field(default_factory=dict)
    max_wait_seconds: Optional[int] = 45


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
    sip_call_status: Optional[str] = None
    sip_status_code: Optional[int] = None
    sip_status: Optional[str] = None
    answered: bool = False
    message: str
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)


class LiveKitOutboundAdapter:
    """Interface to LiveKit server API for placing outbound SIP/PSTN calls."""

    def live_mode_enabled(self) -> bool:
        """Check if live outbound dialing is enabled via environment variables."""
        env = get_runtime_env()
        return env["live_call_enabled"]

    def required_env_status(self) -> dict:
        """Get status of required environment variables for live telephony."""
        env = get_runtime_env()
        return {
            "TELEPHONY_LIVE_MODE": "true" if env["live_call_enabled"] else "false",
            "DANA_ENABLE_OUTBOUND_DIALER": "true" if env["live_call_enabled"] else "false",
            "LIVEKIT_URL": env["livekit_url"],
            "LIVEKIT_API_KEY": env["livekit_api_key"],
            "LIVEKIT_API_SECRET": env["livekit_api_secret"],
            "LIVEKIT_SIP_OUTBOUND_TRUNK_ID": env["livekit_sip_outbound_trunk_id"],
            "DANA_OUTBOUND_CALLER_ID": env["outbound_caller_id"],
            "DANA_AGENT_WORKER_ENABLED": "true" if env["worker_enabled"] else "false",
            "DANA_TELEPHONY_PROVIDER": env["active_provider"],
        }

    def validate_live_config(self, config: LiveKitDialConfig) -> tuple[bool, list[str]]:
        """Validate that all required credentials and variables are present for live calls."""
        warnings = []
        env = get_runtime_env()
        
        # Check config fields
        url = config.livekit_url or env["livekit_url"]
        key = config.api_key or env["livekit_api_key"]
        secret = config.api_secret or env["livekit_api_secret"]
        trunk_id = config.outbound_trunk_id or env["livekit_sip_outbound_trunk_id"]
        
        if not url:
            warnings.append("Missing livekit_url config or LIVEKIT_URL env")
        if not key:
            warnings.append("Missing api_key config or LIVEKIT_API_KEY env")
        if not secret:
            warnings.append("Missing api_secret config or LIVEKIT_API_SECRET env")
        if not trunk_id:
            warnings.append("Missing outbound_trunk_id config or LIVEKIT_SIP_OUTBOUND_TRUNK_ID env")

        # Check required env variables directly (as mandated by prompt)
        if not env["livekit_url"]:
            warnings.append("Missing LIVEKIT_URL environment variable")
        if not env["livekit_api_key"]:
            warnings.append("Missing LIVEKIT_API_KEY environment variable")
        if not env["livekit_api_secret"]:
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
                sip_call_status="active",
                sip_status_code=200,
                sip_status="OK",
                answered=True,
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

        # 3. Call CreateSIPParticipant
        return await self.create_sip_participant(config)

    async def create_sip_participant(self, config: LiveKitDialConfig) -> LiveKitDialResult:
        """Place live call via LiveKit SDK."""
        # Dynamic imports as required
        try:
            from livekit import api
            from livekit.protocol.sip import CreateSIPParticipantRequest
        except ImportError as e:
            return LiveKitDialResult(
                success=False,
                dry_run=False,
                live_mode=True,
                room_name=config.room_name,
                message=f"livekit-api library is not installed: {e}",
                error="SDK_IMPORT_ERROR",
            )

        env = get_runtime_env()
        url = config.livekit_url or env["livekit_url"]
        api_key = config.api_key or env["livekit_api_key"]
        api_secret = config.api_secret or env["livekit_api_secret"]
        trunk_id = config.outbound_trunk_id or env["livekit_sip_outbound_trunk_id"]

        lk_api = None
        try:
            lk_api = api.LiveKitAPI(url, api_key, api_secret)
            
            # Map request
            request = CreateSIPParticipantRequest(
                sip_trunk_id=trunk_id,
                sip_call_to=config.phone_number,
                room_name=config.room_name,
                participant_identity=config.participant_identity,
                participant_name=config.participant_name,
                krisp_enabled=config.krisp_enabled,
                wait_until_answered=config.wait_until_answered,
            )
            
            # Place SIP participant creation request
            sip_participant = await lk_api.sip.create_sip_participant(request)
            
            # Extract fields
            extracted = self.extract_sip_result_fields(sip_participant)
            
            return LiveKitDialResult(
                success=True,
                dry_run=False,
                live_mode=True,
                room_name=config.room_name,
                participant_identity=config.participant_identity,
                livekit_participant_id=extracted.get("participant_id"),
                livekit_sip_call_id=extracted.get("sip_call_id"),
                provider_call_id=extracted.get("sip_call_id"),
                sip_call_status="active",
                sip_status_code=200,
                sip_status="OK",
                answered=True,
                message="LiveKit SIP outbound participant created successfully.",
                data={"sip_participant": str(sip_participant)}
            )
        except Exception as e:
            err_details = self.extract_twirp_sip_error(e)
            return LiveKitDialResult(
                success=False,
                dry_run=False,
                live_mode=True,
                room_name=config.room_name,
                message=f"LiveKit API CreateSIPParticipant call failed: {e}",
                error=err_details.get("twirp_code") or "SIP_DIAL_FAILED",
                sip_call_status=err_details.get("sip_call_status"),
                sip_status_code=err_details.get("sip_status_code"),
                sip_status=err_details.get("sip_status"),
                data={"error_details": err_details}
            )
        finally:
            if lk_api:
                aclose_func = getattr(lk_api, "aclose", None)
                if aclose_func:
                    try:
                        await aclose_func()
                    except TypeError:
                        pass

    def extract_sip_result_fields(self, participant: Any) -> dict:
        """Safely extract details from LiveKit SIP participant response object."""
        return {
            "participant_id": getattr(participant, "participant_id", None) or getattr(participant, "identity", None),
            "sip_call_id": getattr(participant, "sip_call_id", None),
        }

    def extract_twirp_sip_error(self, exc: Exception) -> dict:
        """Extract Twirp SIP details from exception metadata if available."""
        res = {
            "sip_call_status": "failed",
            "sip_status_code": 500,
            "sip_status": "Internal Error",
            "error_message": str(exc)
        }
        if hasattr(exc, "code"):
            res["twirp_code"] = str(getattr(exc, "code"))
        if hasattr(exc, "message"):
            res["twirp_message"] = str(getattr(exc, "message"))
        elif hasattr(exc, "msg"):
            res["twirp_message"] = str(getattr(exc, "msg"))

        meta = getattr(exc, "meta", None)
        if isinstance(meta, dict):
            res["twirp_meta"] = meta
            if "sip_status_code" in meta:
                try:
                    res["sip_status_code"] = int(meta["sip_status_code"])
                except ValueError:
                    pass
            if "sip_status" in meta:
                res["sip_status"] = meta["sip_status"]
                
        return res
