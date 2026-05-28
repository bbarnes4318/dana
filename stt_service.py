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


class SileroVAD:
    """
    Silero VAD v5 wrapper for speech activity detection.
    """
    
    def __init__(self, threshold: float = 0.5, sample_rate: int = 16000):
        self.threshold = threshold
        self.sample_rate = sample_rate
        self._model = None
        self._utils = None
        self._initialized = False
        
    async def initialize(self):
        if self._initialized:
            return
            
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._load_model)
        self._initialized = True
        logger.info("Silero VAD initialized successfully")
        
    def _load_model(self):
        self._model, self._utils = torch.hub.load(
            repo_or_dir='snakers4/silero-vad',
            model='silero_vad',
            force_reload=False,
            trust_repo=True
        )
        self._model.eval()
        
    def detect_speech(self, audio_chunk: np.ndarray) -> float:
        if not self._initialized:
            raise RuntimeError("VAD not initialized. Call initialize() first.")
            
        audio_tensor = torch.from_numpy(audio_chunk).float()
        if audio_tensor.dim() == 1:
            audio_tensor = audio_tensor.unsqueeze(0)
            
        with torch.no_grad():
            speech_prob = self._model(audio_tensor, self.sample_rate)
            
        return float(speech_prob.item())
    
    def is_speech(self, audio_chunk: np.ndarray) -> bool:
        return self.detect_speech(audio_chunk) > self.threshold
    
    def reset(self):
        if self._model is not None:
            self._model.reset_states()


class LocallyHostedSTT(stt.STT):
    """
    Custom STT implementation using faster-whisper.
    """
    
    def __init__(self, config: Optional[STTConfig] = None):
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=True,
                interim_results=True,
            )
        )
        self.config = config or STTConfig()
        self._model: Optional[WhisperModel] = None
        self._vad = SileroVAD(
            threshold=self.config.vad_threshold,
            sample_rate=self.config.sample_rate
        )
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
            
            await self._vad.initialize()
            
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
    
    def stream(self) -> "LocalSTTStream":
        return LocalSTTStream(self)


class LocalSTTStream(stt.SpeechStream):
    """
    Queue/Event-driven streaming STT implementation with VAD speech boundary detection.
    """
    
    def __init__(self, stt_instance: LocallyHostedSTT):
        super().__init__()
        self._stt = stt_instance
        self._audio_buffer: list[rtc.AudioFrame] = []
        self._is_speaking = False
        self._speech_start_time: Optional[float] = None
        self._speech_end_time: Optional[float] = None
        self._silence_frames = 0
        
        # Calculate min silence frames based on ms config (assumes 20ms per frame)
        min_silence_ms = self._stt.config.min_silence_ms
        self._min_silence_frames = max(1, min_silence_ms // 20)
        
        self._queue: asyncio.Queue[Optional[list[rtc.AudioFrame]]] = asyncio.Queue()
        self._closed = False
        
    @property
    def speech_start_time(self) -> Optional[float]:
        return self._speech_start_time

    @property
    def speech_end_time(self) -> Optional[float]:
        return self._speech_end_time
        
    async def _run(self) -> AsyncIterator[SpeechEvent]:
        await self._stt._ensure_initialized()
        
        while not self._closed:
            try:
                audio_frames = await self._queue.get()
                if audio_frames is None:
                    break
                    
                # Speech segment ended, transcribe it
                audio = self._stt._audio_frames_to_numpy(audio_frames)
                
                if len(audio) > self._stt.config.sample_rate * 0.25:  # Min 250ms
                    # Emit interim event
                    yield SpeechEvent(
                        type=SpeechEventType.INTERIM_TRANSCRIPT,
                        alternatives=[SpeechData(text="...", language=self._stt.config.language)]
                    )
                    
                    # Transcribe
                    from speech.local_stt_load import TrackLocalSTTTask
                    with TrackLocalSTTTask():
                        text = await self._stt._transcribe(audio)
                    if text:
                        yield SpeechEvent(
                            type=SpeechEventType.FINAL_TRANSCRIPT,
                            alternatives=[SpeechData(text=text, language=self._stt.config.language)]
                        )
                
                self._stt._vad.reset()
                self._queue.task_done()
            except Exception as e:
                logger.error(f"Error in STT stream run loop: {e}")
                
    async def push_frame(self, frame: rtc.AudioFrame):
        if self._closed:
            return
            
        await self._stt._ensure_initialized()
        
        # Convert frame to numpy for VAD
        audio_data = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) / 32768.0
        
        # Run VAD
        is_speech = self._stt._vad.is_speech(audio_data)
        
        if is_speech:
            if not self._is_speaking:
                self._is_speaking = True
                self._speech_start_time = time.time()
                logger.debug(f"STT: Speech start detected at {self._speech_start_time}")
            
            self._silence_frames = 0
            self._audio_buffer.append(frame)
            
            # Check for max duration
            if self._speech_start_time and (time.time() - self._speech_start_time > self._stt.config.max_speech_duration_s):
                self._is_speaking = False
                self._speech_end_time = time.time()
                logger.debug(f"STT: Speech max duration exceeded, end detected at {self._speech_end_time}")
                await self._queue.put(list(self._audio_buffer))
                self._audio_buffer.clear()
        else:
            if self._is_speaking:
                self._silence_frames += 1
                self._audio_buffer.append(frame)  # Include trailing silence
                
                # End of speech detected
                if self._silence_frames >= self._min_silence_frames:
                    self._is_speaking = False
                    self._speech_end_time = time.time()
                    logger.debug(f"STT: Speech end detected at {self._speech_end_time} after {self._silence_frames} silence frames")
                    await self._queue.put(list(self._audio_buffer))
                    self._audio_buffer.clear()
    
    async def aclose(self):
        self._closed = True
        # Terminate queue worker
        await self._queue.put(None)
        self._audio_buffer.clear()


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
