"""Routed TTS Wrapper.

Subclasses livekit.agents.tts.TTS and dynamically routes Text-to-Speech requests
to either local Kokoro ONNX or cloud fallback options (ElevenLabs/OpenAI TTS).
Supports automatic local retries and failover.
"""

from __future__ import annotations
import logging
import asyncio
import uuid
from typing import AsyncIterable, Optional, Any

from livekit import rtc
from livekit.agents import tts
from routing.model_router import ModelRouter, TrackLocalTTSTask
from routing.provider_health import record_failure

logger = logging.getLogger(__name__)

class RoutedTTS(tts.TTS):
    """Wrapper for TTS services conforming to LiveKit's tts.TTS interface."""

    def __init__(
        self,
        local_tts: tts.TTS,
        cloud_tts: Optional[tts.TTS],
        router: ModelRouter
    ) -> None:
        # Initialize base class using properties from local_tts
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True),
            sample_rate=local_tts.sample_rate,
            num_channels=1
        )
        self.local_tts = local_tts
        self.cloud_tts = cloud_tts
        self.router = router

    def synthesize(
        self,
        text: str,
        *,
        conn_options: Optional[Any] = None,
    ) -> tts.ChunkedStream:
        """Fallback to simple chunked synthesis. For routing simplicity, we stream."""
        # LiveKit TTS requires synthesize returning ChunkedStream
        # In our case, we can delegate to the currently active provider
        from speech.context_registry import get_current_call_id, get_current_campaign_id
        call_id = get_current_call_id() or "unknown"
        campaign_id = get_current_campaign_id()
        
        provider = self.router.select_provider(
            component="tts",
            call_id=call_id,
            campaign_id=campaign_id
        )
        
        if provider == "local":
            return self.local_tts.synthesize(text, conn_options=conn_options)
        else:
            if not self.cloud_tts:
                raise RuntimeError("Cloud TTS requested but not configured.")
            return self.cloud_tts.synthesize(text, conn_options=conn_options)

    def stream(
        self,
        *,
        conn_options: Optional[Any] = None,
    ) -> RoutedTTSStream:
        """Return a dynamic routed TTS stream."""
        return RoutedTTSStream(
            routed_tts=self,
            conn_options=conn_options
        )


class RoutedTTSStream(tts.SynthesizeStream):
    """A synthesize stream that routes audio production dynamically and handles failover."""

    def __init__(self, *, routed_tts: RoutedTTS, conn_options: Any) -> None:
        super().__init__(tts=routed_tts, conn_options=conn_options)
        self.routed_tts = routed_tts
        self.conn_options = conn_options
        self.active_stream = None
        self.provider = "local"
        self.call_id = "unknown"
        self.campaign_id = None
        self._concurrency_context = None
        self._forward_task = None

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        from speech.context_registry import get_current_call_id, get_current_campaign_id
        self.call_id = get_current_call_id() or "unknown"
        self.campaign_id = get_current_campaign_id()

        # Initial provider selection
        self.provider = self.routed_tts.router.select_provider(
            component="tts",
            call_id=self.call_id,
            campaign_id=self.campaign_id
        )

        attempts = 0
        current_provider = self.provider

        while True:
            attempts += 1
            try:
                if current_provider == "cloud_unavailable":
                    raise RuntimeError("Cloud TTS provider has missing credentials.")

                if current_provider == "local":
                    self._concurrency_context = TrackLocalTTSTask()
                    self._concurrency_context.__enter__()
                    delegate_tts = self.routed_tts.local_tts
                else:
                    if not self.routed_tts.cloud_tts:
                        raise RuntimeError("Cloud TTS is not configured.")
                    delegate_tts = self.routed_tts.cloud_tts

                self.active_stream = delegate_tts.stream(conn_options=self.conn_options)
                self.provider = current_provider
                break

            except Exception as e:
                logger.error(
                    f"TTS stream initialization failed on '{current_provider}' "
                    f"(attempt {attempts}): {e}"
                )

                if self._concurrency_context:
                    self._concurrency_context.__exit__(None, None, None)
                    self._concurrency_context = None

                # Record failure
                record_failure(self.call_id, "tts", current_provider)

                # Cooldown / Fallback check
                fallback_allowed = self.routed_tts.router.config.allow_cloud_tts_fallback
                cloud_prov = self.routed_tts.router._get_cloud_provider("tts")
                has_cloud_creds = self.routed_tts.router.has_credentials(cloud_prov)

                if current_provider == "local" and fallback_allowed and has_cloud_creds:
                    logger.info(f"Failing over from local TTS to cloud fallback ({cloud_prov}).")
                    current_provider = cloud_prov
                    self.routed_tts.router.log_decision(
                        component="tts",
                        call_id=self.call_id,
                        campaign_id=self.campaign_id,
                        provider=cloud_prov,
                        reason=f"local_init_failure: {e}",
                        fallback_allowed=True
                    )
                    continue

                # Local retry check
                max_retries = self.routed_tts.router.config.tts_local_max_retries
                if current_provider == "local" and attempts <= max_retries:
                    logger.info(f"Retrying local TTS stream (attempt {attempts} of {max_retries})...")
                    await asyncio.sleep(0.5)
                    continue

                logger.error("All TTS stream attempts/failovers failed.")
                raise RuntimeError("tts_unavailable")

        # Start background task to feed text/flushes into the active stream
        async def forward_input():
            try:
                async for input_data in self._input_ch:
                    if isinstance(input_data, str):
                        self.active_stream.push_text(input_data)
                    elif isinstance(input_data, self._FlushSentinel):
                        self.active_stream.flush()
                self.active_stream.end_input()
            except Exception as fe:
                logger.error(f"Error forwarding input to active TTS stream: {fe}")

        self._forward_task = asyncio.create_task(forward_input())

        # Initialize the output emitter mirroring active stream settings
        request_id = str(uuid.uuid4())
        active_sample_rate = getattr(self.active_stream, "sample_rate", getattr(self.active_stream, "_tts", self.routed_tts).sample_rate)
        active_num_channels = getattr(self.active_stream, "num_channels", getattr(getattr(self.active_stream, "_tts", self.routed_tts), "num_channels", 1))
        output_emitter.initialize(
            request_id=request_id,
            sample_rate=active_sample_rate,
            num_channels=active_num_channels,
            mime_type="audio/pcm",
            stream=True,
        )

        segment_id = None
        # Pull generated audio events/frames from the active stream
        try:
            async for chunk in self.active_stream:
                # Handle segment transitions if present (typical in SynthesizedAudio)
                chunk_segment_id = getattr(chunk, "segment_id", None)
                if chunk_segment_id != segment_id:
                    if segment_id is not None:
                        output_emitter.end_segment()
                    segment_id = chunk_segment_id
                    if segment_id is not None:
                        output_emitter.start_segment(segment_id=segment_id)

                # Push the audio bytes to the outer output_emitter
                if isinstance(chunk, rtc.AudioFrame):
                    data_bytes = bytes(chunk.data)
                    output_emitter.push(data_bytes)
                elif hasattr(chunk, "frame") and chunk.frame:
                    data_bytes = bytes(chunk.frame.data)
                    output_emitter.push(data_bytes)
                elif hasattr(chunk, "data") and chunk.data:
                    data_bytes = bytes(chunk.data)
                    output_emitter.push(data_bytes)
                    
            if segment_id is not None:
                output_emitter.end_segment()
                
        except Exception as ex:
            logger.error(f"Error pulling audio from active stream: {ex}")
            raise ex

    async def aclose(self) -> None:
        """Clean up streaming resources."""
        if self._concurrency_context:
            self._concurrency_context.__exit__(None, None, None)
            self._concurrency_context = None
        if self._forward_task:
            self._forward_task.cancel()
        if self.active_stream:
            await self.active_stream.aclose()
        await super().aclose()

    async def interrupt(self) -> None:
        """Forward interrupt to active stream delegate."""
        if self.active_stream:
            await self.active_stream.interrupt()
        await super().interrupt()
