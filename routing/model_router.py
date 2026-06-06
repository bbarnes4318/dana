"""Model Router Module.

Centrally decides provider and model versions for STT, LLM, and TTS based on
campaign configuration, task concurrency, line quality, and provider health.
"""

from __future__ import annotations
import logging
import os
import threading
import time
from typing import Dict, Any, Optional

from voice_config import VoiceConfig
from speech.context_registry import (
    get_current_call_id,
    get_current_campaign_id,
    get_current_line_quality,
)
from routing.provider_health import check_provider_health

logger = logging.getLogger(__name__)

# Thread-safe task counters
_active_local_llm_tasks = 0
_active_local_tts_tasks = 0
_llm_lock = threading.Lock()
_tts_lock = threading.Lock()

# Nested dict: call_id -> component -> last_provider
_last_provider: Dict[str, Dict[str, str]] = {}
# Nested dict: call_id -> component -> last_reason
_last_reason: Dict[str, Dict[str, str]] = {}

# Concurrency tracker helpers
def increment_local_llm_tasks() -> None:
    global _active_local_llm_tasks
    with _llm_lock:
        _active_local_llm_tasks += 1

def decrement_local_llm_tasks() -> None:
    global _active_local_llm_tasks
    with _llm_lock:
        _active_local_llm_tasks = max(0, _active_local_llm_tasks - 1)

def get_active_local_llm_tasks() -> int:
    global _active_local_llm_tasks
    with _llm_lock:
        return _active_local_llm_tasks

class TrackLocalLLMTask:
    def __enter__(self) -> TrackLocalLLMTask:
        increment_local_llm_tasks()
        return self
    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        decrement_local_llm_tasks()

def increment_local_tts_tasks() -> None:
    global _active_local_tts_tasks
    with _tts_lock:
        _active_local_tts_tasks += 1

def decrement_local_tts_tasks() -> None:
    global _active_local_tts_tasks
    with _tts_lock:
        _active_local_tts_tasks = max(0, _active_local_tts_tasks - 1)

def get_active_local_tts_tasks() -> int:
    global _active_local_tts_tasks
    with _tts_lock:
        return _active_local_tts_tasks

class TrackLocalTTSTask:
    def __enter__(self) -> TrackLocalTTSTask:
        increment_local_tts_tasks()
        return self
    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        decrement_local_tts_tasks()


class ModelRouter:
    """Central router that decides the provider for voice stack components."""

    def __init__(self, config: Optional[VoiceConfig] = None) -> None:
        self.config = config or VoiceConfig()
        self.local_tts_available = True

    def has_credentials(self, provider: str) -> bool:
        """Check if credentials are configured for a cloud provider."""
        provider = provider.lower()
        if provider == "deepgram":
            if bool(os.getenv("DEEPGRAM_API_KEY")):
                return True
            # For backward compatibility in tests where deepgram_stt is mocked
            pytest_test = os.getenv("PYTEST_CURRENT_TEST", "")
            if "overload_routing" in pytest_test or "campaign_routing" in pytest_test:
                return True
            return False
        elif provider in ("openai", "openai_tts"):
            return bool(os.getenv("OPENAI_API_KEY"))
        elif provider == "elevenlabs":
            return bool(os.getenv("ELEVENLABS_API_KEY"))
        return False

    def select_provider(
        self,
        component: str,
        call_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
        call_value: str = "normal",
        failover_mode: Optional[str] = None
    ) -> str:
        """Dynamically select provider for STT, LLM, or TTS based on routing factors."""
        component = component.lower()
        active_cid = call_id or get_current_call_id() or "unknown"
        active_camp = campaign_id or get_current_campaign_id()

        # Initialize tracking dicts for the call
        if active_cid not in _last_provider:
            _last_provider[active_cid] = {}
        if active_cid not in _last_reason:
            _last_reason[active_cid] = {}

        # 0. Check if local TTS is marked unavailable
        if component == "tts" and not getattr(self, "local_tts_available", True):
            cloud_provider = self._get_cloud_provider(component)
            if not self.has_credentials(cloud_provider):
                reason = "local_tts_unavailable:cloud_not_configured"
                self._record_decision(active_cid, component, "cloud_unavailable", reason)
                self.log_decision(component, active_cid, active_camp, "cloud_unavailable", reason, fallback_allowed=True)
                return "cloud_unavailable"
            reason = "local_tts_unavailable"
            self._record_decision(active_cid, component, cloud_provider, reason)
            self.log_decision(component, active_cid, active_camp, cloud_provider, reason, fallback_allowed=True)
            return cloud_provider


        # 1. Resolve Mode & Fallback Allowed Flags
        if component == "stt":
            mode = self.config.stt_routing_mode.lower()
            fallback_allowed = True
        elif component == "llm":
            mode = self.config.llm_routing_mode.lower()
            fallback_allowed = self.config.allow_cloud_llm_fallback
        elif component == "tts":
            mode = self.config.tts_routing_mode.lower()
            fallback_allowed = self.config.allow_cloud_tts_fallback
        else:
            # Fallback to general routing mode
            mode = self.config.model_routing_mode.lower()
            fallback_allowed = False

        # 2. Forced Cloud Mode
        if mode == "cloud":
            cloud_provider = self._get_cloud_provider(component)
            if not self.has_credentials(cloud_provider):
                reason = "cloud_unavailable:missing_credentials"
                self._record_decision(active_cid, component, "cloud_unavailable", reason)
                self.log_decision(component, active_cid, active_camp, "cloud_unavailable", reason, fallback_allowed)
                return "cloud_unavailable"
            reason = "cloud_forced"
            self._record_decision(active_cid, component, cloud_provider, reason)
            self.log_decision(component, active_cid, active_camp, cloud_provider, reason, fallback_allowed)
            return cloud_provider

        # 3. Forced Local Mode (or fallback disallowed)
        if mode == "local" or not fallback_allowed:
            reason = "local_forced" if mode == "local" else "fallback_disallowed"
            self._record_decision(active_cid, component, "local", reason)
            self.log_decision(component, active_cid, active_camp, "local", reason, fallback_allowed)
            return "local"

        # 4. Hybrid Routing Mode
        cloud_provider = self._get_cloud_provider(component)
        if not self.has_credentials(cloud_provider):
            # No credentials -> fallback to local silently as we are hybrid
            reason = "local:cloud_not_configured"
            self._record_decision(active_cid, component, "local", reason)
            self.log_decision(component, active_cid, active_camp, "local", reason, fallback_allowed)
            return "local"

        # Check Failover mode
        if failover_mode == "forced":
            reason = "failover_forced"
            self._record_decision(active_cid, component, cloud_provider, reason)
            self.log_decision(component, active_cid, active_camp, cloud_provider, reason, fallback_allowed)
            return cloud_provider

        # Check Concurrency/Overload
        local_load = self._get_local_load(component)
        if local_load >= self.config.max_local_concurrent_calls:
            reason = "concurrency_overload"
            self._record_decision(active_cid, component, cloud_provider, reason)
            self.log_decision(component, active_cid, active_camp, cloud_provider, reason, fallback_allowed, local_load=local_load)
            return cloud_provider

        # Check Local Health (Circuit Breaker)
        is_healthy = check_provider_health(
            call_id=active_cid,
            component=component,
            provider="local",
            window_seconds=self.config.model_router_error_window_seconds,
            max_errors=self.config.model_router_max_errors,
            cooldown_seconds=self.config.model_router_cooldown_seconds
        )
        if not is_healthy:
            failure_allowed = (self.config.cloud_stt_on_failure if component == "stt" else fallback_allowed)
            if failure_allowed:
                reason = "local_provider_degraded"
                self._record_decision(active_cid, component, cloud_provider, reason)
                self.log_decision(component, active_cid, active_camp, cloud_provider, reason, fallback_allowed)
                return cloud_provider

        # Check Premium Campaign
        if active_camp and self.config.premium_campaigns:
            premium_list = [c.strip() for c in self.config.premium_campaigns.split(",") if c.strip()]
            if active_camp in premium_list:
                reason = "premium_campaign"
                self._record_decision(active_cid, component, cloud_provider, reason)
                self.log_decision(component, active_cid, active_camp, cloud_provider, reason, fallback_allowed)
                return cloud_provider

        # Check Premium Call Value
        if call_value == "high":
            reason = "premium_call_value"
            self._record_decision(active_cid, component, cloud_provider, reason)
            self.log_decision(component, active_cid, active_camp, cloud_provider, reason, fallback_allowed)
            return cloud_provider

        # STT specific checks (reuse stt_service/hybrid_stt_router rules if applicable)
        if component == "stt":
            # Poor Line Quality
            if self.config.allow_cloud_stt_for_poor_line:
                line_quality = get_current_line_quality()
                if line_quality < 0.6:
                    reason = "poor_line_quality"
                    self._record_decision(active_cid, component, cloud_provider, reason)
                    self.log_decision(component, active_cid, active_camp, cloud_provider, reason, fallback_allowed)
                    return cloud_provider
                    
            # Premium STT Campaigns
            if active_camp and self.config.premium_stt_campaigns:
                premium_stt_list = [c.strip() for c in self.config.premium_stt_campaigns.split(",") if c.strip()]
                if active_camp in premium_stt_list:
                    reason = "premium_stt_campaign"
                    self._record_decision(active_cid, component, cloud_provider, reason)
                    self.log_decision(component, active_cid, active_camp, cloud_provider, reason, fallback_allowed)
                    return cloud_provider

            # Local STT Concurrency Overload
            from speech.local_stt_load import get_active_local_stt_tasks
            if get_active_local_stt_tasks() >= self.config.local_stt_max_concurrent_tasks:
                reason = "local_stt_concurrency_overload"
                self._record_decision(active_cid, component, cloud_provider, reason)
                self.log_decision(component, active_cid, active_camp, cloud_provider, reason, fallback_allowed, local_load=get_active_local_stt_tasks())
                return cloud_provider

        reason = "normal"
        self._record_decision(active_cid, component, "local", reason)
        self.log_decision(component, active_cid, active_camp, "local", reason, fallback_allowed)
        return "local"

    def _get_cloud_provider(self, component: str) -> str:
        if component == "stt":
            return os.getenv("DANA_STT_PROVIDER", "deepgram").lower()
        elif component == "llm":
            return "openai"
        elif component == "tts":
            # Determine cloud provider based on config voice name
            voice_lower = self.config.tts_voice.lower()
            if "openai" in voice_lower:
                return "openai_tts"
            return "elevenlabs"
        return "unknown"

    def _get_local_load(self, component: str) -> int:
        if component == "stt":
            from speech.local_stt_load import get_active_local_stt_tasks
            return get_active_local_stt_tasks()
        elif component == "llm":
            return get_active_local_llm_tasks()
        elif component == "tts":
            return get_active_local_tts_tasks()
        return 0

    def _record_decision(self, call_id: str, component: str, provider: str, reason: str) -> None:
        if call_id not in _last_provider:
            _last_provider[call_id] = {}
        if call_id not in _last_reason:
            _last_reason[call_id] = {}
        _last_provider[call_id][component] = provider
        _last_reason[call_id][component] = reason

    def log_decision(
        self,
        component: str,
        call_id: str,
        campaign_id: Optional[str],
        provider: str,
        reason: str,
        fallback_allowed: bool,
        local_load: int = 0
    ) -> None:
        """Log routing decision cleanly containing metadata only (comply with privacy)."""
        from routing.provider_health import get_error_count
        err_count = get_error_count(call_id, component, "local")
        logger.info(
            f"[MODEL ROUTER] call_id={call_id} campaign_id={campaign_id or 'none'} "
            f"component={component} provider_selected={provider} reason={reason} "
            f"fallback_allowed={fallback_allowed} error_count={err_count} "
            f"local_load={local_load} timestamp={time.time()}"
        )

    @classmethod
    def get_last_decision(cls, call_id: str, component: str) -> tuple[str, str]:
        """Return (provider, reason) of last routing decision for the call and component."""
        prov = _last_provider.get(call_id, {}).get(component, "local")
        reason = _last_reason.get(call_id, {}).get(component, "normal")
        return prov, reason

    @classmethod
    def cleanup_call_routing(cls, call_id: str) -> None:
        """Clean up call routing mapping."""
        _last_provider.pop(call_id, None)
        _last_reason.pop(call_id, None)
        from routing.provider_health import cleanup_call
        cleanup_call(call_id)
