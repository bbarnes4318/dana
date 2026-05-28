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

# Global concurrency tracker for local STT tasks
_active_local_tasks: int = 0
# Track local failures per call_id
_local_failures: Dict[str, int] = {}
# Track last decision reason per call_id
_last_decision_reason: Dict[str, str] = {}


class TrackLocalSTTTask:
    """Context manager to track active local Whisper tasks safely in the asyncio loop."""

    async def __aenter__(self) -> None:
        global _active_local_tasks
        _active_local_tasks += 1

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        global _active_local_tasks
        _active_local_tasks = max(0, _active_local_tasks - 1)


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
        "active_local_stt_tasks": _active_local_tasks,
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

    def select_provider(self, call_id: Optional[str] = None, campaign_id: Optional[str] = None) -> str:
        """Select STT provider based on configured modes, overload, line quality, or failures."""
        active_cid = call_id or get_current_call_id() or "unknown"
        active_camp = campaign_id or get_current_campaign_id()
        
        mode = self.config.stt_routing_mode.lower()
        if mode == "local":
            _last_decision_reason[active_cid] = "local:forced"
            return "local"

        if mode == "cloud":
            if not self.deepgram_stt:
                _last_decision_reason[active_cid] = "cloud_unavailable:missing_credentials"
                return "cloud_unavailable"
            _last_decision_reason[active_cid] = "deepgram:forced"
            return "deepgram"

        # Hybrid routing mode
        # 1. Fall back to local if Deepgram credentials/package are missing
        if not self.deepgram_stt:
            _last_decision_reason[active_cid] = "local:deepgram_not_configured"
            return "local"

        # 2. Check if local has failed previously in the call
        if self.config.cloud_stt_on_failure and _local_failures.get(active_cid, 0) > 0:
            _last_decision_reason[active_cid] = "deepgram:local_stt_failure"
            return "deepgram"

        # 3. Check for premium campaign config
        if active_camp and self.config.premium_stt_campaigns:
            premium_list = [c.strip() for c in self.config.premium_stt_campaigns.split(",") if c.strip()]
            if active_camp in premium_list:
                _last_decision_reason[active_cid] = "deepgram:premium_campaign"
                return "deepgram"

        # 4. Check for GPU/CPU task concurrency overload
        if _active_local_tasks >= self.config.local_stt_max_concurrent_tasks:
            _last_decision_reason[active_cid] = "deepgram:concurrency_overload"
            return "deepgram"

        # 5. Check for poor line quality (sustained clipping/noise)
        if self.config.allow_cloud_stt_for_poor_line:
            line_quality = get_current_line_quality()
            if line_quality < 0.6:
                _last_decision_reason[active_cid] = "deepgram:poor_line_quality"
                return "deepgram"

        _last_decision_reason[active_cid] = "local:normal"
        return "local"

    def log_decision(self, provider: str, reason: str, call_id: Optional[str] = None, campaign_id: Optional[str] = None) -> None:
        """Log provider decisions with metadata only, completely omitting transcript/audio content."""
        active_cid = call_id or get_current_call_id() or "unknown"
        active_camp = campaign_id or get_current_campaign_id()
        
        logger.info(
            f"[STT ROUTER] call_id={active_cid} campaign_id={active_camp} "
            f"provider_selected={provider} reason={reason} "
            f"line_quality_score={get_current_line_quality():.2f} "
            f"active_local_tasks={_active_local_tasks} "
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

        # Use Local STT with concurrency tracking
        async with TrackLocalSTTTask():
            return await self.local_stt._recognize_impl(buffer, language=language)

    def stream(self) -> HybridSTTStream:
        return HybridSTTStream(self)


class HybridSTTStream(stt.SpeechStream):
    """Audio stream forwarding frame chunks to the dynamically selected STT stream delegate."""

    def __init__(self, router: HybridSTTRouter) -> None:
        super().__init__()
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

        self.active_stream = self.delegate_stt.stream()
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
            # We track concurrency dynamically if local is active
            if self.provider == "local":
                async with TrackLocalSTTTask():
                    await self.active_stream.push_frame(frame)
            else:
                await self.active_stream.push_frame(frame)
        except Exception as e:
            logger.error(f"STT stream push frame failed on ({self.provider}): {e}")
            
            # Switch to fallback only at turn boundaries if local fails
            if self.provider == "local" and self.router.config.cloud_stt_on_failure:
                _local_failures[self.call_id] = _local_failures.get(self.call_id, 0) + 1
                dg = self.router.deepgram_stt
                if dg:
                    logger.info("Local STT failed during push. Swapped to Deepgram fallback.")
                    self.provider = "deepgram"
                    self.router.log_decision(
                        "deepgram",
                        reason="local_failure_fallback",
                        call_id=self.call_id,
                        campaign_id=self.campaign_id,
                    )
                    await self.active_stream.aclose()
                    self.active_stream = dg.stream()
                    await self.active_stream.push_frame(frame)

    async def _run(self) -> AsyncIterator[SpeechEvent]:
        """Iterates and yields transcript speech events safely, supporting delegate hot-swapping."""
        queue: asyncio.Queue[Optional[SpeechEvent]] = asyncio.Queue()
        forward_task = None
        current_delegate = None

        async def forward_loop(stream: Any, q: asyncio.Queue[Optional[SpeechEvent]]) -> None:
            try:
                async for event in stream:
                    await q.put(event)
            except Exception as ex:
                logger.error(f"Error in STT stream forwarder: {ex}")
            finally:
                await q.put(None)

        try:
            while not self._closed:
                if current_delegate != self.active_stream:
                    if forward_task:
                        forward_task.cancel()
                    current_delegate = self.active_stream
                    forward_task = asyncio.create_task(forward_loop(current_delegate, queue))

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.1)
                    if event is None:
                        if current_delegate == self.active_stream:
                            break
                        continue
                    yield event
                    queue.task_done()
                except asyncio.TimeoutError:
                    continue
        finally:
            if forward_task:
                forward_task.cancel()

    async def aclose(self, *, wait: bool = True) -> None:
        self._closed = True
        await self.active_stream.aclose(wait=wait)
        # Clear preprocessor cache for the call
        if self._preprocessor:
            self._preprocessor.cleanup_call(self.call_id)
