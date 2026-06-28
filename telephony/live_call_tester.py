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

import uuid
import hashlib
from datetime import datetime, timezone
from typing import Any, Optional, Dict, List
from pydantic import BaseModel, Field
from storage.repository import Repository
from telephony.livekit_adapter import LiveKitOutboundAdapter, LiveKitDialConfig, LiveKitDialResult
from telephony.live_telephony_readiness import LiveTelephonyReadinessChecker

class LiveCallTestConfig(BaseModel):
    """Configuration for placing a single manual test call."""
    phone_number: str
    campaign_id: Optional[str] = None
    provider_config_id: Optional[str] = None
    caller_id: Optional[str] = None
    room_name: Optional[str] = None
    live_mode: bool = True
    wait_until_answered: bool = True
    krisp_enabled: bool = True
    operator: str
    export_to_training: bool = False


class LiveCallTestResult(BaseModel):
    """The result of placing a live test call."""
    success: bool
    attempted_live_call: bool = False
    call_attempt_id: Optional[str] = None
    room_name: Optional[str] = None
    participant_identity: Optional[str] = None
    livekit_participant_id: Optional[str] = None
    livekit_sip_call_id: Optional[str] = None
    provider_call_id: Optional[str] = None
    sip_call_status: Optional[str] = None
    answered: bool = False
    message: str
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    caller_id_source: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)


class LiveCallTester:
    """Manages placing a single outbound test call and recording database state."""

    def __init__(self, repository: Optional[Repository] = None, adapter: Optional[LiveKitOutboundAdapter] = None) -> None:
        self.repository = repository or Repository()
        self.adapter = adapter or LiveKitOutboundAdapter()
        self.checker = LiveTelephonyReadinessChecker(repository=self.repository, adapter=self.adapter)

    async def place_test_call(self, config: LiveCallTestConfig) -> LiveCallTestResult:
        """Place a single test call, verifying readiness first."""
        # 1. Require operator
        if not config.operator or not config.operator.strip():
            return LiveCallTestResult(
                success=False,
                message="An operator ID is required to run a manual test call.",
                error="OPERATOR_REQUIRED"
            )

        # 2. Require live_mode=true
        if not config.live_mode:
            return LiveCallTestResult(
                success=False,
                message="tester only supports live calling mode (live_mode=True). Use dry-run/mock paths elsewhere.",
                error="LIVE_MODE_REQUIRED"
            )

        # Resolve campaign ID if not explicitly provided
        campaign_id = config.campaign_id
        if not campaign_id:
            campaigns = await self.repository.store.query("outbound_campaigns", {})
            if campaigns:
                active_camps = [c for c in campaigns if c.get("status") == "running"]
                if active_camps:
                    campaign_id = active_camps[0]["id"]
                else:
                    campaign_id = campaigns[0]["id"]
            else:
                campaign_id = "manual_test_campaign"
        print(f"DEBUG RESOLVED CAMPAIGN ID: {campaign_id}", flush=True)

        # 3. Check DNC lists if campaign or lead exist
        if campaign_id:
            # Check DNC registry
            from compliance.dnc_registry import DatabaseDNCRegistry
            dnc_registry = DatabaseDNCRegistry(self.repository)
            on_dnc = await dnc_registry.contains(config.phone_number, campaign_id=campaign_id)
            if on_dnc:
                return LiveCallTestResult(
                    success=False,
                    message=f"Phone number {config.phone_number} is on DNC list for campaign {campaign_id}.",
                    error="BLOCKED_BY_DNC"
                )

        # 4. Check readiness
        readiness = await self.checker.run(
            provider_config_id=config.provider_config_id,
            campaign_id=campaign_id,
            is_test_call=True
        )
        if not readiness.ready:
            return LiveCallTestResult(
                success=False,
                message="Live telephony is not ready. Fix failing readiness audits first.",
                warnings=readiness.warnings,
                error="READINESS_AUDIT_FAILED",
                data={"readiness_failures": readiness.failures}
            )

        # 5. Build call metadata
        attempt_id = str(uuid.uuid4())
        room_name = config.room_name or f"dana-test-call-{uuid.uuid4().hex[:8]}"
        participant_identity = self.adapter.build_participant_identity("manual-test", attempt_id)
        
        now = datetime.now(timezone.utc)
        phone_redacted = config.phone_number[:-4] + "****" if len(config.phone_number) > 4 else config.phone_number
        phone_hash = hashlib.sha256(config.phone_number.encode("utf-8")).hexdigest()

        # Build metadata dict
        meta_dict = {
            "manual_test_call": True,
            "operator": config.operator,
            "initiated_at": now.isoformat()
        }
        if campaign_id:
            meta_dict["campaign_id"] = campaign_id

        # 6. Save CallAttempt before dialing
        attempt = {
            "id": attempt_id,
            "campaign_id": campaign_id,
            "lead_id": "manual-test",
            "provider_config_id": config.provider_config_id,
            "status": "dialing",
            "phone_number_redacted": phone_redacted,
            "phone_number_hash": phone_hash,
            "livekit_room_name": room_name,
            "started_at": now.isoformat(),
            "created_at": now,
            "updated_at": now,
        }
        await self.repository.save_call_attempt(**attempt)

        # Determine outbound trunk ID and caller ID from provider config or env fallbacks
        env = get_runtime_env()
        trunk_id = env["livekit_sip_outbound_trunk_id"]
        
        caller_id = config.caller_id
        caller_id_source = "explicit_override" if config.caller_id else None
        
        if not caller_id:
            caller_id = env["outbound_caller_id"]
            caller_id_source = env["outbound_caller_id_source"]
        
        if config.provider_config_id:
            provider_config = await self.repository.get_telephony_provider_config(config.provider_config_id)
            if provider_config:
                trunk_id = provider_config.get("livekit_sip_outbound_trunk_id") or trunk_id
                if not config.caller_id:
                    caller_id = provider_config.get("default_caller_id") or caller_id
                    if provider_config.get("default_caller_id"):
                        caller_id_source = "provider_config"

        # If caller ID is still missing, query DIDPoolManager
        if not caller_id:
            try:
                from telephony.did_pool import DIDPoolManager
                from storage.schemas import CallerIdSelectionConfig
                pool = DIDPoolManager(self.repository)
                provider = env["active_provider"]
                allow_cross = os.environ.get("DANA_ALLOW_CROSS_PROVIDER_CALLER_ID", "").strip().lower() == "true"
                selection_config = CallerIdSelectionConfig(
                    provider=provider,
                    strategy="health_weighted",
                    allow_cross_provider=allow_cross
                )
                res_pool = await pool.select_caller_id(selection_config)
                if res_pool.success:
                    caller_id = res_pool.phone_number
                    caller_id_source = f"pool:{res_pool.source}"
            except Exception:
                pass

        # Update metadata in attempt
        attempt["metadata"] = {
            "selected_caller_id": caller_id,
            "caller_id_source": caller_id_source
        }

        # 7. Execute dialing via LiveKit SDK Outbound adapter
        dial_config = LiveKitDialConfig(
            live_mode=True,
            livekit_url=env["livekit_url"],
            api_key=env["livekit_api_key"],
            api_secret=env["livekit_api_secret"],
            outbound_trunk_id=trunk_id,
            room_name=room_name,
            phone_number=config.phone_number,
            caller_id=caller_id,
            participant_identity=participant_identity,
            participant_name="Dana Outbound Test Call",
            wait_until_answered=config.wait_until_answered,
            krisp_enabled=config.krisp_enabled,
            metadata=meta_dict,
        )

        dial_res = await self.adapter.dial(dial_config)

        # 8. Update CallAttempt and save LiveCallSession on success
        if dial_res.success:
            attempt["status"] = "in_progress"
            attempt["livekit_participant_id"] = dial_res.livekit_participant_id
            attempt["livekit_sip_call_id"] = dial_res.livekit_sip_call_id
            attempt["provider_call_id"] = dial_res.provider_call_id
            attempt["updated_at"] = datetime.now(timezone.utc)
            await self.repository.save_call_attempt(**attempt)

            # Create LiveCallSession
            session_id = str(uuid.uuid4())
            await self.repository.save_live_call_session(
                id=session_id,
                campaign_id=config.campaign_id or "manual_test_campaign",
                lead_id="manual-test",
                attempt_id=attempt_id,
                call_id=attempt_id, # Link call_id to attempt_id
                status="active",
                current_stage="OPENING",
                livekit_room_name=room_name,
                participant_identity=participant_identity,
                started_at=now,
                updated_at=now,
            )

            # Log campaign control event for test call
            await self.repository.save_campaign_control_event(
                campaign_id=config.campaign_id or "manual_test_campaign",
                event_type="test_call",
                operator=config.operator,
                reason=f"Manual test call placed to redacted number {phone_redacted}",
                metadata={"attempt_id": attempt_id, "room_name": room_name}
            )

            return LiveCallTestResult(
                success=True,
                attempted_live_call=True,
                call_attempt_id=attempt_id,
                room_name=room_name,
                participant_identity=participant_identity,
                livekit_participant_id=dial_res.livekit_participant_id,
                livekit_sip_call_id=dial_res.livekit_sip_call_id,
                provider_call_id=dial_res.provider_call_id,
                sip_call_status=dial_res.sip_call_status,
                answered=dial_res.answered,
                message="Outbound test call placed successfully.",
                caller_id_source=caller_id_source
            )
        else:
            # Mark attempt failed
            attempt["status"] = "failed"
            attempt["failure_reason"] = dial_res.message
            attempt["ended_at"] = datetime.now(timezone.utc).isoformat()
            attempt["updated_at"] = datetime.now(timezone.utc)
            await self.repository.save_call_attempt(**attempt)

            return LiveCallTestResult(
                success=False,
                attempted_live_call=False,
                call_attempt_id=attempt_id,
                room_name=room_name,
                participant_identity=participant_identity,
                message=f"Live test call failed to place: {dial_res.message}",
                error=dial_res.error or "CALL_PLACEMENT_FAILED",
                sip_call_status=dial_res.sip_call_status,
                caller_id_source=caller_id_source,
                data=dial_res.data
            )
