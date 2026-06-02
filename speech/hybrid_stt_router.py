"""Hybrid STT Router.

Routes Speech-to-Text requests to local faster-whisper or Deepgram fallback
based on config, GPU/CTranslate2 overload, premium campaigns, and line quality.
Logs metadata-only decision metrics for cost and health auditing.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import AsyncIterator, Optional, Dict, Any

from livekit import rtc
from livekit.agents import stt, utils
from livekit.agents.stt import SpeechEvent, SpeechEventType, SpeechData
from voice_config import VoiceConfig
from stt_service import LocallyHostedSTT

from speech.context_registry import (
    get_current_call_id,
    get_current_campaign_id,
    get_current_line_quality,
)

logger = logging.getLogger(__name__)

from speech.local_stt_load import (
    get_active_local_stt_tasks,
    AsyncTrackLocalSTTTask,
)

# Track local failures per call_id
_local_failures: Dict[str, int] = {}
# Track last decision reason per call_id
_last_decision_reason: Dict[str, str] = {}


def get_speech_health_report(call_id: Optional[str] = None) -> Dict[str, Any]:
    """Report health and status parameters for speech services."""
    active_cid = call_id or get_current_call_id() or "unknown"
    
    # Check deepgram availability
    try:
        from livekit.plugins import deepgram
        dg_installed = True
    except ImportError:
        dg_installed = False

    dg_configured = dg_installed and bool(os.getenv("DEEPGRAM_API_KEY"))

    # Config parameters (temporary fetch)
    cfg = VoiceConfig()

    return {
        "stt_routing_mode": cfg.stt_routing_mode,
        "selected_provider": _last_decision_reason.get(active_cid, "local").split(":")[0],
        "fallback_reason": _last_decision_reason.get(active_cid, "none"),
        "active_local_stt_tasks": get_active_local_stt_tasks(),
        "cloud_available": dg_configured,
        "preprocessing_enabled": cfg.enable_audio_preprocessing,
        "endpoint_mode": cfg.endpoint_mode,
    }


class HybridSTTRouter(stt.STT):
    """Router STT service gating local faster-whisper and Deepgram fallbacks."""

    def __init__(self, config: VoiceConfig, local_stt: LocallyHostedSTT) -> None:
        capabilities = stt.STTCapabilities(
            streaming=True,
            interim_results=True,
        )
        super().__init__(capabilities=capabilities)
        self.config = config
        self.local_stt = local_stt
        self._deepgram_stt: Optional[stt.STT] = None
        self._openai_stt: Optional[stt.STT] = None
        self._preprocessor = None

        if config.enable_audio_preprocessing:
            from speech.phone_audio_preprocessor import PhoneAudioPreprocessor
            self._preprocessor = PhoneAudioPreprocessor(
                enable_noise_gate=True,
                enable_pstn_bandpass=config.enable_pstn_bandpass,
            )

    @property
    def deepgram_stt(self) -> Optional[stt.STT]:
        """Lazy load Deepgram STT only when configured and requested."""
        if self._deepgram_stt is not None:
            return self._deepgram_stt

        api_key = os.getenv("DEEPGRAM_API_KEY")
        if not api_key:
            logger.warning("DEEPGRAM_API_KEY is missing. Deepgram provider not configured.")
            return None

        try:
            from livekit.plugins import deepgram
            model = os.getenv("DEEPGRAM_MODEL", "nova-3")
            self._deepgram_stt = deepgram.STT(
                model=model,
                language="en",
            )
            return self._deepgram_stt
        except ImportError:
            logger.warning("livekit-plugins-deepgram package is not installed.")
            return None
        except Exception as e:
            logger.error(f"Failed to instantiate Deepgram STT: {e}")
            return None

    @property
    def openai_stt(self) -> Optional[stt.STT]:
        """Lazy load OpenAI STT only when configured and requested."""
        if self._openai_stt is not None:
            return self._openai_stt

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OPENAI_API_KEY is missing. OpenAI STT provider not configured.")
            return None

        try:
            from livekit.plugins import openai
            self._openai_stt = openai.STT()
            return self._openai_stt
        except ImportError:
            logger.warning("livekit-plugins-openai package is not installed.")
            return None
        except Exception as e:
            logger.error(f"Failed to instantiate OpenAI STT: {e}")
            return None

    def select_provider(self, call_id: Optional[str] = None, campaign_id: Optional[str] = None) -> str:
        """Select STT provider based on configured modes, overload, line quality, or failures."""
        active_cid = call_id or get_current_call_id() or "unknown"
        active_camp = campaign_id or get_current_campaign_id()
        
        from routing.model_router import ModelRouter
        router = ModelRouter(self.config)
        provider = router.select_provider("stt", active_cid, active_camp)
        
        # Keep _last_decision_reason updated for test compatibility
        _, reason = router.get_last_decision(active_cid, "stt")
        _last_decision_reason[active_cid] = reason
        
        # Synchronize local failures count to health tracker if there are any
        if _local_failures.get(active_cid, 0) > 0:
            from routing.provider_health import record_failure
            # Ensure the tracker matches
            from routing.provider_health import get_error_count
            curr_health_errors = get_error_count(active_cid, "stt", "local")
            if curr_health_errors < _local_failures[active_cid]:
                for _ in range(_local_failures[active_cid] - curr_health_errors):
                    record_failure(active_cid, "stt", "local")
        else:
            # If health tracker has errors, sync back to local failures for test compatibility
            from routing.provider_health import get_error_count
            health_errors = get_error_count(active_cid, "stt", "local")
            if health_errors > 0:
                _local_failures[active_cid] = health_errors
        
        return provider

    def log_decision(self, provider: str, reason: str, call_id: Optional[str] = None, campaign_id: Optional[str] = None) -> None:
        """Log provider decisions with metadata only, completely omitting transcript/audio content."""
        active_cid = call_id or get_current_call_id() or "unknown"
        active_camp = campaign_id or get_current_campaign_id()
        
        logger.info(
            f"[STT ROUTER] call_id={active_cid} campaign_id={active_camp} "
            f"provider_selected={provider} reason={reason} "
            f"line_quality_score={get_current_line_quality():.2f} "
            f"active_local_tasks={get_active_local_stt_tasks()} "
            f"fallback_enabled={self.config.cloud_stt_on_failure} timestamp={time.time()}"
        )

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: Optional[str] = None
    ) -> stt.SpeechEvent:
        call_id = get_current_call_id() or "unknown"
        campaign_id = get_current_campaign_id()

        # Run preprocessing on the raw audio buffer if enabled
        if self._preprocessor:
            try:
                # Extract frames and preprocess
                frames = list(buffer)
                preprocessed_frames = [self._preprocessor.preprocess_frame(f, call_id=call_id) for f in frames]
                buffer = utils.AudioBuffer(preprocessed_frames)
            except Exception as e:
                logger.error(f"Recognize audio preprocessing failed: {e}")

        provider = self.select_provider(call_id, campaign_id)
        if provider == "cloud_unavailable":
            logger.error("STT recognize failed: Cloud STT requested but provider not configured.")
            raise RuntimeError("Cloud STT requested but provider not configured.")

        self.log_decision(provider, reason="recognize", call_id=call_id, campaign_id=campaign_id)

        if provider == "deepgram":
            dg = self.deepgram_stt
            if dg:
                try:
                    return await dg._recognize_impl(buffer, language=language)
                except Exception as e:
                    logger.error(f"Deepgram recognize failed: {e}. Falling back to Local STT.")
                    _local_failures[call_id] = _local_failures.get(call_id, 0) + 1
        elif provider == "openai":
            oa = self.openai_stt
            if oa:
                try:
                    return await oa._recognize_impl(buffer, language=language)
                except Exception as e:
                    logger.error(f"OpenAI recognize failed: {e}. Falling back to Local STT.")
                    _local_failures[call_id] = _local_failures.get(call_id, 0) + 1

        # Use Local STT with concurrency tracking
        async with AsyncTrackLocalSTTTask():
            return await self.local_stt._recognize_impl(buffer, language=language)

    def stream(self, *args, **kwargs) -> HybridSTTStream:
        return HybridSTTStream(self, *args, **kwargs)


class HybridSTTStream(stt.SpeechStream):
    """Audio stream forwarding frame chunks to the dynamically selected STT stream delegate."""

    def __init__(self, router: HybridSTTRouter, *args, **kwargs) -> None:
        stt_val = kwargs.pop("stt", router)
        conn_options = kwargs.get("conn_options")
        super().__init__(stt=stt_val, conn_options=conn_options)
        self.router = router
        self._preprocessor = router._preprocessor

        self.call_id = get_current_call_id() or "unknown"
        self.campaign_id = get_current_campaign_id()

        self.provider = self.router.select_provider(self.call_id, self.campaign_id)
        if self.provider == "cloud_unavailable":
            # Direct cloud mode with missing credentials raises runtime error
            raise RuntimeError("Cloud STT requested but provider not configured.")

        self.router.log_decision(
            self.provider,
            reason="stream_init",
            call_id=self.call_id,
            campaign_id=self.campaign_id,
        )

        # Create active stream delegate
        self.delegate_stt = self.router.local_stt
        if self.provider == "deepgram":
            dg = self.router.deepgram_stt
            if dg:
                self.delegate_stt = dg
            else:
                logger.warning("Deepgram unavailable. Falling back to Local STT at stream start.")
                self.provider = "local"
                self.router.log_decision(
                    "local",
                    reason="fallback_deepgram_unavailable",
                    call_id=self.call_id,
                    campaign_id=self.campaign_id,
                )
                self.delegate_stt = self.router.local_stt
        elif self.provider == "openai":
            oa = self.router.openai_stt
            if oa:
                self.delegate_stt = oa
            else:
                logger.warning("OpenAI STT unavailable. Falling back to Local STT at stream start.")
                self.provider = "local"
                self.router.log_decision(
                    "local",
                    reason="fallback_openai_unavailable",
                    call_id=self.call_id,
                    campaign_id=self.campaign_id,
                )
                self.delegate_stt = self.router.local_stt

        self.active_stream = self.delegate_stt.stream(*args, **kwargs)
        self._closed = False

    async def push_frame(self, frame: rtc.AudioFrame) -> None:
        if self._closed:
            return

        if self._preprocessor:
            try:
                frame = self._preprocessor.preprocess_frame(frame, call_id=self.call_id)
            except Exception as e:
                logger.error(f"Streaming preprocessor failed: {e}")

        try:
            await self.active_stream.push_frame(frame)
        except Exception as e:
            logger.error(f"STT stream push frame failed on ({self.provider}): {e}")
            
            # Switch to fallback only at stream/session boundary or clean utterance boundary.
            # Do NOT hot-swap providers mid-utterance.
            if self.provider == "local":
                _local_failures[self.call_id] = _local_failures.get(self.call_id, 0) + 1
                self.router.log_decision(
                    "local",
                    reason=f"local_stream_push_failed: {e}",
                    call_id=self.call_id,
                    campaign_id=self.campaign_id,
                )
            self._closed = True
            try:
                await self.active_stream.aclose()
            except Exception:
                pass
            raise e

    async def _run(self) -> None:
        """Forward speech events from the active stream delegate to our local event channel."""
        try:
            async for event in self.active_stream:
                self._event_ch.send_nowait(event)
        except Exception as e:
            logger.error(f"Error in STT stream forwarding run loop: {e}")
            raise e

    async def aclose(self, *, wait: bool = True) -> None:
        self._closed = True
        try:
            await self.active_stream.aclose()
        except Exception as e:
            logger.warning(f"Error closing delegate STT stream: {e}")
        # Clear preprocessor cache for the call
        if self._preprocessor:
            self._preprocessor.cleanup_call(self.call_id)
        await super().aclose()

