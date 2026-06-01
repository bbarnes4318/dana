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

import importlib.metadata
from typing import List, Dict, Optional, Tuple
from pydantic import BaseModel, Field
from storage.repository import Repository
from telephony.livekit_adapter import LiveKitOutboundAdapter

class LiveTelephonyReadinessResult(BaseModel):
    """Outbound telephony config readiness audit result."""
    ready: bool
    live_mode_enabled: bool
    required_env: Dict[str, Optional[str]] = Field(default_factory=dict)
    provider_config_ok: bool = False
    outbound_trunk_id_present: bool = False
    caller_id_present: bool = False
    livekit_sdk_available: bool = False
    agent_worker_ready: bool = False
    campaign_ready: Optional[bool] = None
    failures: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    next_steps: List[str] = Field(default_factory=list)
    
    # Unified resolver fields for Prompt 32
    env_loaded: bool = False
    local_llm_ready: bool = False
    local_stt_ready: bool = False
    local_tts_ready: bool = False

    # Provider fields for Prompt 33
    active_provider: Optional[str] = None
    caller_id_source: Optional[str] = None


class LiveTelephonyReadinessChecker:
    """Audits environment and db configurations to certify outbound telephony readiness."""

    def __init__(self, repository: Optional[Repository] = None, adapter: Optional[LiveKitOutboundAdapter] = None) -> None:
        self.repository = repository or Repository()
        self.adapter = adapter or LiveKitOutboundAdapter()

    def check_env(self) -> dict:
        """Collect status of all required/optional environment variables."""
        return self.adapter.required_env_status()

    def check_livekit_sdk(self) -> tuple[bool, Optional[str]]:
        """Verify that the official LiveKit SDK classes can be imported."""
        try:
            from livekit import api
            from livekit.protocol.sip import CreateSIPParticipantRequest
            # Double check presence of expected structures
            if not hasattr(api, "LiveKitAPI"):
                return False, "LiveKitAPI class missing on imported api module"
            return True, None
        except ImportError as e:
            return False, str(e)

    async def check_provider_config(self, provider_config_id: str | None = None) -> dict:
        """Inspect provider settings, either by config ID or environment variables fallback."""
        res = {
            "ok": True,
            "outbound_trunk_id_present": False,
            "caller_id_present": False,
            "failures": [],
            "warnings": [],
            "caller_id": None,
            "caller_id_source": None
        }
        
        env = get_runtime_env()
        trunk_id = env["livekit_sip_outbound_trunk_id"]
        caller_id = env["outbound_caller_id"]
        caller_id_source = env["outbound_caller_id_source"]
        provider = env["active_provider"]

        if provider_config_id:
            config = await self.repository.get_telephony_provider_config(provider_config_id)
            if not config:
                res["ok"] = False
                res["failures"].append(f"Provider config '{provider_config_id}' not found in database.")
            else:
                trunk_id = config.get("livekit_sip_outbound_trunk_id") or trunk_id
                caller_id = config.get("default_caller_id") or caller_id

        # If caller ID is missing from environment/config, query DIDPoolManager
        if not caller_id:
            try:
                from telephony.did_pool import DIDPoolManager
                from storage.schemas import CallerIdSelectionConfig
                pool = DIDPoolManager(self.repository)
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
                    if res_pool.warnings:
                        res["warnings"].extend(res_pool.warnings)
            except Exception as e:
                res["failures"].append(f"Failed to query DIDPoolManager: {str(e)}")

        # Warning for cross-provider caller ID if explicitly allowed
        allow_cross = os.environ.get("DANA_ALLOW_CROSS_PROVIDER_CALLER_ID", "").strip().lower() == "true"
        if allow_cross:
            res["warnings"].append("Cross-provider caller ID may reduce attestation and increase call labeling risk.")

        if trunk_id:
            res["outbound_trunk_id_present"] = True
        else:
            if env.get("telnyx_api_key"):
                res["failures"].append(
                    "Missing LiveKit outbound SIP trunk ID. TELNYX_API_KEY is not the same thing. "
                    "Create/locate the LiveKit outbound trunk and set LIVEKIT_SIP_OUTBOUND_TRUNK_ID."
                )
            else:
                res["failures"].append("No LiveKit SIP Outbound Trunk ID configured (neither in provider config nor in LIVEKIT_SIP_OUTBOUND_TRUNK_ID env).")

        if caller_id:
            res["caller_id_present"] = True
            res["caller_id"] = caller_id
            res["caller_id_source"] = caller_id_source
        else:
            if provider == "telnyx":
                res["failures"].append(
                    "Active provider is telnyx but no Telnyx caller ID was configured. "
                    "Set DANA_OUTBOUND_CALLER_ID, TELNYX_OUTBOUND_CALLER_ID, TELNYX_DIDS, or TELNYX_PHONE_NUMBERS."
                )
            elif provider == "bulkvs":
                res["failures"].append(
                    "Active provider is bulkvs but no BulkVS caller ID was configured. "
                    "Set DANA_OUTBOUND_CALLER_ID, BULKVS_OUTBOUND_CALLER_ID, BULKVS_DIDS, or BULKVS_PHONE_NUMBERS."
                )
            elif provider == "signalwire":
                res["failures"].append(
                    "Active provider is signalwire but no SignalWire caller ID was configured. "
                    "Set DANA_OUTBOUND_CALLER_ID, SIGNALWIRE_OUTBOUND_CALLER_ID, or SIGNALWIRE_DIDS."
                )
            elif provider == "twilio":
                res["failures"].append(
                    "Active provider is twilio but no Twilio caller ID was configured. "
                    "Set DANA_OUTBOUND_CALLER_ID, TWILIO_CALLER_ID, or TWILIO_PHONE_NUMBERS."
                )
            else:
                res["failures"].append("No outbound caller ID configured.")

        if res["failures"]:
            res["ok"] = False

        return res

    async def check_campaign(self, campaign_id: str | None = None) -> dict:
        """Inspect campaign configuration and state."""
        res = {
            "ok": True,
            "status": "not_provided",
            "failures": []
        }
        if campaign_id:
            campaign = await self.repository.get_outbound_campaign(campaign_id)
            if not campaign:
                res["ok"] = False
                res["status"] = "not_found"
                res["failures"].append(f"Campaign '{campaign_id}' not found.")
            else:
                status = campaign.get("status", "draft")
                res["status"] = status
                if status != "running":
                    res["ok"] = False
                    res["failures"].append(f"Campaign is in '{status}' status (must be running for outbound dials).")
                
                # Check lead capacity
                leads = await self.repository.query_campaign_leads({"campaign_id": campaign_id})
                active_leads = [l for l in leads if l.get("status") in ("new", "queued", "callback")]
                if not active_leads:
                    res["ok"] = False
                    res["failures"].append("Campaign has no active/callable leads in its queue.")

        return res

    async def run(self, provider_config_id: str | None = None, campaign_id: str | None = None) -> LiveTelephonyReadinessResult:
        """Run all readiness checks and compile the final report."""
        failures = []
        warnings = []
        next_steps = []

        env = get_runtime_env()
        env_status = self.check_env()
        
        # 1. Check live mode settings
        live_mode_enabled = env["live_call_enabled"]
        
        # Collect failures regarding live modes
        if not live_mode_enabled:
            failures.append("TELEPHONY_LIVE_MODE is not set to 'true'.")
            next_steps.append("Set environment variable TELEPHONY_LIVE_MODE=true")

        # 2. Check general LiveKit secrets
        for k, v in [("LIVEKIT_URL", env["livekit_url"]), ("LIVEKIT_API_KEY", env["livekit_api_key"]), ("LIVEKIT_API_SECRET", env["livekit_api_secret"])]:
            if not v:
                failures.append(f"Missing required secret environment variable: {k}")
                next_steps.append(f"Provide environment variable {k}")

        # 3. Check SDK
        sdk_ok, sdk_err = self.check_livekit_sdk()
        if not sdk_ok:
            failures.append(f"LiveKit Python SDK check failed: {sdk_err}")
            next_steps.append("Install required LiveKit packages (pip install livekit-api livekit-agents)")

        # 4. Check Provider config
        prov_status = await self.check_provider_config(provider_config_id)
        if prov_status.get("warnings"):
            warnings.extend(prov_status["warnings"])
        if not prov_status["ok"]:
            failures.extend(prov_status["failures"])
            if not prov_status["outbound_trunk_id_present"]:
                next_steps.append("Configure LIVEKIT_SIP_OUTBOUND_TRUNK_ID in environment or provider config")
            if not prov_status["caller_id_present"]:
                next_steps.append("Configure DANA_OUTBOUND_CALLER_ID in environment or provider config")

        # 5. Check Campaign
        campaign_status = None
        if campaign_id:
            camp_status = await self.check_campaign(campaign_id)
            campaign_status = camp_status["ok"]
            failures.extend(camp_status["failures"])
            if camp_status["status"] == "not_found":
                next_steps.append(f"Create a valid campaign with ID '{campaign_id}'")
            elif camp_status["status"] in ("draft", "ready", "paused", "stopped"):
                next_steps.append(f"Activate the campaign to running state via UI/CLI")

        # 6. Check Agent Worker
        worker_enabled = env["worker_enabled"]
        if not worker_enabled:
            warnings.append("DANA_AGENT_WORKER_ENABLED is not set to 'true'. Calls might place, but Dana agent worker will not join rooms automatically.")
            next_steps.append("Set environment variable DANA_AGENT_WORKER_ENABLED=true and start scripts/run_livekit_agent_worker.py")

        ready = len(failures) == 0

        # Load environment file load results
        loader_summary = load_environment()
        env_loaded = len(loader_summary.get("loaded_files", [])) > 0

        # Engine checks
        local_llm_ready = env["llm_routing_mode"] == "local" and bool(env["vllm_base_url"])
        local_stt_ready = env["stt_routing_mode"] == "local"
        local_tts_ready = env["tts_routing_mode"] == "local" and bool(env["kokoro_model_path"]) and bool(env["kokoro_voices_path"])

        return LiveTelephonyReadinessResult(
            ready=ready,
            live_mode_enabled=live_mode_enabled,
            required_env=env_status,
            provider_config_ok=prov_status["ok"],
            outbound_trunk_id_present=prov_status["outbound_trunk_id_present"],
            caller_id_present=prov_status["caller_id_present"],
            livekit_sdk_available=sdk_ok,
            agent_worker_ready=worker_enabled and sdk_ok,
            campaign_ready=campaign_status,
            failures=failures,
            warnings=warnings,
            next_steps=next_steps,
            env_loaded=env_loaded,
            local_llm_ready=local_llm_ready,
            local_stt_ready=local_stt_ready,
            local_tts_ready=local_tts_ready,
            active_provider=env["active_provider"],
            caller_id_source=prov_status.get("caller_id_source") or env["outbound_caller_id_source"]
        )
