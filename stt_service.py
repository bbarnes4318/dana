"""
Sovereign Voice Stack - Custom STT Service
Ultra-low-latency Speech-to-Text using faster-whisper with VAD-triggered segmenting.
"""

import asyncio
import logging
import os
import time
from typing import AsyncIterator, Optional
from dataclasses import dataclass
import numpy as np
import torch

from faster_whisper import WhisperModel
from livekit import rtc
from livekit.agents import stt, utils
from livekit.agents.stt import SpeechEvent, SpeechEventType, SpeechData
from livekit.agents.types import TimedString
from voice_config import VoiceConfig

logger = logging.getLogger(__name__)


@dataclass
class STTConfig:
    """Configuration for the local STT service."""
    model_size: str = "large-v3-turbo"
    compute_type: str = "float16"
    device: str = "cuda"
    language: str = "en"
    beam_size: int = 1  # Greedy decoding for speed
    vad_filter: bool = True
    vad_threshold: float = 0.5
    min_speech_duration_ms: int = 250
    max_speech_duration_s: float = 30.0
    sample_rate: int = 16000
    min_silence_ms: int = 200





class LocallyHostedSTT(stt.STT):
    """
    Custom STT implementation using faster-whisper.
    """
    _active_streams: dict[str, 'LocalSTTStream'] = {}
    
    def __init__(self, config: Optional[STTConfig] = None):
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=True,
                interim_results=True,
            )
        )
        self.config = config or STTConfig()
        self._model: Optional[WhisperModel] = None
        self._initialized = False
        self._lock = asyncio.Lock()
        
    async def initialize(self):
        async with self._lock:
            if self._initialized:
                return
                
            logger.info(f"Loading Whisper {self.config.model_size} with {self.config.compute_type}...")
            start_time = time.time()
            
            loop = asyncio.get_event_loop()
            self._model = await loop.run_in_executor(
                None,
                lambda: WhisperModel(
                    self.config.model_size,
                    device=self.config.device,
                    compute_type=self.config.compute_type
                )
            )
            
            load_time = time.time() - start_time
            logger.info(f"STT initialized in {load_time:.2f}s")
            self._initialized = True
            
    async def _ensure_initialized(self):
        if not self._initialized:
            await self.initialize()
    
    def _audio_frames_to_numpy(self, frames: list[rtc.AudioFrame]) -> np.ndarray:
        if not frames:
            return np.array([], dtype=np.float32)
            
        audio_data = b''.join(frame.data for frame in frames)
        audio_int16 = np.frombuffer(audio_data, dtype=np.int16)
        audio_float32 = audio_int16.astype(np.float32) / 32768.0
        return audio_float32
    
    async def _transcribe(self, audio: np.ndarray) -> str:
        if len(audio) == 0:
            return ""
            
        await self._ensure_initialized()
        loop = asyncio.get_event_loop()
        
        def _run_transcription():
            segments, info = self._model.transcribe(
                audio,
                language=self.config.language,
                beam_size=self.config.beam_size,
                vad_filter=self.config.vad_filter,
                vad_parameters=dict(
                    min_speech_duration_ms=self.config.min_speech_duration_ms,
                    threshold=self.config.vad_threshold
                ),
                without_timestamps=True,
                condition_on_previous_text=False
            )
            return " ".join(segment.text.strip() for segment in segments)
        
        transcription = await loop.run_in_executor(None, _run_transcription)
        return transcription.strip()
    
    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: Optional[str] = None
    ) -> stt.SpeechEvent:
        await self._ensure_initialized()
        audio = self._audio_frames_to_numpy(list(buffer))
        
        start_time = time.time()
        text = await self._transcribe(audio)
        latency = (time.time() - start_time) * 1000
        
        logger.debug(f"STT transcription: '{text}' (latency: {latency:.0f}ms)")
        
        return SpeechEvent(
            type=SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[SpeechData(text=text, language=language or self.config.language)]
        )
    
    def stream(self, *, conn_options=None, **kwargs) -> "LocalSTTStream":
        return LocalSTTStream(self, conn_options=conn_options, **kwargs)


class LocalSTTStream(stt.SpeechStream):
    """
    Queue/Event-driven streaming STT implementation with VAD speech boundary detection.
    Optimized for low latency, zero-allocation rolling buffer ingestion.
    """
    
    def __init__(self, stt_instance: LocallyHostedSTT, *, conn_options=None, **kwargs):
        super().__init__(stt=stt_instance, conn_options=conn_options)
        self._stt = stt_instance
        self._is_speaking = False
        self._speech_start_time: Optional[float] = None
        self._speech_end_time: Optional[float] = None
        self._silence_frames = 0
        
        # Register in the active streams
        from speech.context_registry import get_current_call_id
        self._call_id = get_current_call_id() or "default"
        LocallyHostedSTT._active_streams[self._call_id] = self

        # Buffer sizes: 30 seconds at 16kHz
        self._buffer_size = int(self._stt.config.sample_rate * self._stt.config.max_speech_duration_s)
        self._rolling_buffer = np.zeros(self._buffer_size, dtype=np.float32)
        self._inference_buffer = np.zeros(self._buffer_size, dtype=np.float32)
        self._write_cursor = 0
        self._speech_finalized = False
        self._early_emitted = False
        self._data_event = asyncio.Event()
        self._closed = False

    def on_speech_start(self):
        if not self._is_speaking:
            self._is_speaking = True
            self._speech_finalized = False
            self._early_emitted = False
            self._speech_start_time = time.time()
            logger.info(f"STT: Speech start hook triggered at {self._speech_start_time}, write_cursor={self._write_cursor}")
            self._data_event.set()

    def on_speech_end(self):
        if self._is_speaking:
            self._is_speaking = False
            self._speech_finalized = True
            self._speech_end_time = time.time()
            logger.info(f"STT: Speech end hook triggered at {self._speech_end_time}")
            self._data_event.set()

    @property
    def speech_start_time(self) -> Optional[float]:
        return self._speech_start_time

    @property
    def speech_end_time(self) -> Optional[float]:
        return self._speech_end_time
        
    def _run_whisper(self, audio_data: np.ndarray):
        # Runs in executor (background thread)
        segments, info = self._stt._model.transcribe(
            audio_data,
            language=self._stt.config.language,
            beam_size=self._stt.config.beam_size,
            vad_filter=self._stt.config.vad_filter,
            vad_parameters=dict(
                min_speech_duration_ms=self._stt.config.min_speech_duration_ms,
                threshold=self._stt.config.vad_threshold
            ),
            word_timestamps=True,
            hotwords="yes yeah no nope hello who is this insurance funeral burial cost price senior medicare age",
            condition_on_previous_text=False
        )
        results = []
        for segment in segments:
            segment_words = []
            if segment.words:
                for w in segment.words:
                    segment_words.append({
                        "word": w.word,
                        "start": w.start,
                        "end": w.end,
                        "probability": w.probability
                    })
            results.append({
                "text": segment.text,
                "words": segment_words
            })
        return results

    async def _run(self) -> AsyncIterator[SpeechEvent]:
        await self._stt._ensure_initialized()
        
        while not self._closed:
            try:
                # Wait for data event or a timeout/sleep (throttling transcription to at most once per 100ms)
                try:
                    await asyncio.wait_for(self._data_event.wait(), timeout=0.1)
                    self._data_event.clear()
                except asyncio.TimeoutError:
                    pass
                
                if self._closed:
                    break
                    
                current_cursor = self._write_cursor
                if current_cursor == 0:
                    continue
                    
                if self._early_emitted:
                    if self._speech_finalized:
                        self._speech_finalized = False
                        self._write_cursor = 0
                        self._early_emitted = False
                    continue
                
                # Double-buffering copy to avoid concurrent write issues with background executor thread
                self._inference_buffer[:current_cursor] = self._rolling_buffer[:current_cursor]
                self._inference_buffer[current_cursor:] = 0.0
                
                # Check minimum length for Whisper (min 250ms or 4000 samples)
                if current_cursor < 4000:
                    continue
                    
                # Run transcription in executor
                loop = asyncio.get_event_loop()
                from speech.local_stt_load import TrackLocalSTTTask
                with TrackLocalSTTTask():
                    results = await loop.run_in_executor(None, self._run_whisper, self._inference_buffer)
                
                full_text = " ".join(seg["text"].strip() for seg in results).strip()
                
                # Construct words list
                words = []
                for seg in results:
                    for w in seg["words"]:
                        words.append(TimedString(
                            w["word"].strip(),
                            start_time=w["start"],
                            end_time=w["end"],
                            confidence=w["probability"]
                        ))
                
                if not words and not full_text:
                    if self._speech_finalized:
                        self._speech_finalized = False
                        self._write_cursor = 0
                    continue
                
                # Check for early affirmation/negation token (confidence > 85%)
                AFFIRMATION_NEGATION_TOKENS = {"yes", "yeah", "no", "nope"}
                if words and not self._early_emitted:
                    first_word = words[0]
                    first_word_text = first_word.lower().strip(".,?!;:")
                    if first_word_text in AFFIRMATION_NEGATION_TOKENS and first_word.confidence > 0.85:
                        logger.info(f"STT early emit triggered for token: '{first_word_text}' (confidence: {first_word.confidence:.2f})")
                        self._early_emitted = True
                        
                        yield SpeechEvent(
                            type=SpeechEventType.FINAL_TRANSCRIPT,
                            alternatives=[SpeechData(
                                language=self._stt.config.language,
                                text=first_word_text,
                                start_time=first_word.start_time,
                                end_time=first_word.end_time,
                                confidence=first_word.confidence,
                                words=[first_word]
                            )]
                        )
                        
                        self._speech_finalized = False
                        self._write_cursor = 0
                        continue
                
                if self._speech_finalized:
                    self._speech_finalized = False
                    self._write_cursor = 0
                    
                    yield SpeechEvent(
                        type=SpeechEventType.FINAL_TRANSCRIPT,
                        alternatives=[SpeechData(
                            language=self._stt.config.language,
                            text=full_text,
                            start_time=words[0].start_time if words else 0.0,
                            end_time=words[-1].end_time if words else 0.0,
                            confidence=sum(w.confidence for w in words) / len(words) if words else 0.0,
                            words=words
                        )]
                    )
                else:
                    yield SpeechEvent(
                        type=SpeechEventType.INTERIM_TRANSCRIPT,
                        alternatives=[SpeechData(
                            language=self._stt.config.language,
                            text=full_text,
                            start_time=words[0].start_time if words else 0.0,
                            end_time=words[-1].end_time if words else 0.0,
                            confidence=sum(w.confidence for w in words) / len(words) if words else 0.0,
                            words=words
                        )]
                    )
            except Exception as e:
                logger.error(f"Error in STT stream run loop: {e}", exc_info=True)

    async def push_frame(self, frame: rtc.AudioFrame):
        if self._closed:
            return
            
        await self._stt._ensure_initialized()
        
        samples = np.frombuffer(frame.data, dtype=np.int16)
        n_samples = len(samples)
        if n_samples == 0:
            return
            
        # Zero-allocation normalization and copy to rolling buffer
        write_len = min(n_samples, self._buffer_size - self._write_cursor)
        if write_len > 0:
            if not self._is_speaking:
                # Keep only the last 300ms (4800 samples) of audio history as prefix padding
                keep_samples = 4800
                if self._write_cursor + write_len > keep_samples:
                    # Shift buffer to make room
                    shift = (self._write_cursor + write_len) - keep_samples
                    self._rolling_buffer[:keep_samples - write_len] = self._rolling_buffer[shift : self._write_cursor]
                    self._write_cursor = keep_samples - write_len
            
            np.divide(samples[:write_len], 32768.0, out=self._rolling_buffer[self._write_cursor : self._write_cursor + write_len])
            self._write_cursor += write_len
            
            if self._is_speaking:
                # Check if maximum speech duration is exceeded
                if self._speech_start_time and (time.time() - self._speech_start_time > self._stt.config.max_speech_duration_s):
                    self._is_speaking = False
                    self._speech_finalized = True
                    self._speech_end_time = time.time()
                    logger.debug(f"STT: Speech max duration exceeded, end detected at {self._speech_end_time}")
                
                self._data_event.set()
    
    async def aclose(self):
        self._closed = True
        self._data_event.set()
        # Clean up from active_streams
        LocallyHostedSTT._active_streams.pop(self._call_id, None)


def create_stt(config: VoiceConfig) -> stt.STT:
    """
    Factory function for STT:
    Allows hot-swapping Deepgram STT, local Whisper STT, or Hybrid routing based on configuration.
    """
    stt_config = STTConfig(
        model_size=config.stt_model,
        compute_type=config.stt_compute_type,
        vad_threshold=config.vad_threshold,
        min_silence_ms=config.min_silence_ms,
    )
    local_stt = LocallyHostedSTT(stt_config)

    provider = os.getenv("DANA_STT_PROVIDER", "local").lower()
    mode = config.stt_routing_mode.lower()
    
    if provider == "deepgram" or mode == "cloud":
        from speech.hybrid_stt_router import HybridSTTRouter
        # Force cloud mode
        config.stt_routing_mode = "cloud"
        router = HybridSTTRouter(config, local_stt)
        if not router.deepgram_stt:
            raise RuntimeError("Cloud STT requested but Deepgram provider is not configured.")
        return router
        
    if mode == "hybrid":
        from speech.hybrid_stt_router import HybridSTTRouter
        return HybridSTTRouter(config, local_stt)

    return local_stt
