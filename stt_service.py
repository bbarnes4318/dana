"""
Sovereign Voice Stack - Custom STT Service
Ultra-low-latency Speech-to-Text using faster-whisper with VAD-triggered segmenting.

This module wraps faster-whisper in a LiveKit-compatible STT interface,
running entirely in-process on the local GPU for zero network latency.
"""

import asyncio
import logging
import time
from typing import AsyncIterator, Optional
from dataclasses import dataclass
import numpy as np
import torch

from faster_whisper import WhisperModel
from livekit import rtc
from livekit.agents import stt, utils
from livekit.agents.stt import SpeechEvent, SpeechEventType, SpeechData

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


class SileroVAD:
    """
    Silero VAD v5 wrapper for speech activity detection.
    Detects when the user starts/stops speaking for efficient transcription.
    """
    
    def __init__(self, threshold: float = 0.5, sample_rate: int = 16000):
        self.threshold = threshold
        self.sample_rate = sample_rate
        self._model = None
        self._utils = None
        self._initialized = False
        
    async def initialize(self):
        """Load Silero VAD model asynchronously."""
        if self._initialized:
            return
            
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._load_model)
        self._initialized = True
        logger.info("Silero VAD initialized successfully")
        
    def _load_model(self):
        """Load the Silero VAD model (blocking)."""
        self._model, self._utils = torch.hub.load(
            repo_or_dir='snakers4/silero-vad',
            model='silero_vad',
            force_reload=False,
            trust_repo=True
        )
        self._model.eval()
        
    def detect_speech(self, audio_chunk: np.ndarray) -> float:
        """
        Detect speech probability in audio chunk.
        
        Args:
            audio_chunk: Audio samples as float32 numpy array
            
        Returns:
            Speech probability (0.0 to 1.0)
        """
        if not self._initialized:
            raise RuntimeError("VAD not initialized. Call initialize() first.")
            
        # Convert to torch tensor
        audio_tensor = torch.from_numpy(audio_chunk).float()
        
        # Ensure correct shape
        if audio_tensor.dim() == 1:
            audio_tensor = audio_tensor.unsqueeze(0)
            
        # Run VAD
        with torch.no_grad():
            speech_prob = self._model(audio_tensor, self.sample_rate)
            
        return float(speech_prob.item())
    
    def is_speech(self, audio_chunk: np.ndarray) -> bool:
        """Check if audio chunk contains speech."""
        return self.detect_speech(audio_chunk) > self.threshold
    
    def reset(self):
        """Reset VAD state between utterances."""
        if self._model is not None:
            self._model.reset_states()


class LocallyHostedSTT(stt.STT):
    """
    Custom STT implementation using faster-whisper.
    
    Features:
    - Runs entirely on local GPU (zero network latency)
    - VAD-triggered transcription (only processes speech)
    - Streaming audio accumulation with chunked transcription
    - Optimized for ultra-low-latency response
    """
    
    def __init__(self, config: Optional[STTConfig] = None):
        super().__init__()
        self.config = config or STTConfig()
        self._model: Optional[WhisperModel] = None
        self._vad = SileroVAD(
            threshold=self.config.vad_threshold,
            sample_rate=self.config.sample_rate
        )
        self._initialized = False
        self._lock = asyncio.Lock()
        
    async def initialize(self):
        """Initialize STT model and VAD."""
        async with self._lock:
            if self._initialized:
                return
                
            logger.info(f"Loading Whisper {self.config.model_size} with {self.config.compute_type}...")
            start_time = time.time()
            
            # Load Whisper model in executor (blocking operation)
            loop = asyncio.get_event_loop()
            self._model = await loop.run_in_executor(
                None,
                lambda: WhisperModel(
                    self.config.model_size,
                    device=self.config.device,
                    compute_type=self.config.compute_type
                )
            )
            
            # Initialize VAD
            await self._vad.initialize()
            
            load_time = time.time() - start_time
            logger.info(f"STT initialized in {load_time:.2f}s")
            self._initialized = True
            
    async def _ensure_initialized(self):
        """Ensure the STT is initialized before use."""
        if not self._initialized:
            await self.initialize()
    
    def _audio_frames_to_numpy(self, frames: list[rtc.AudioFrame]) -> np.ndarray:
        """Convert LiveKit audio frames to numpy array."""
        if not frames:
            return np.array([], dtype=np.float32)
            
        # Concatenate all frame data
        audio_data = b''.join(frame.data for frame in frames)
        
        # Convert to numpy (assuming 16-bit PCM)
        audio_int16 = np.frombuffer(audio_data, dtype=np.int16)
        
        # Normalize to float32 [-1.0, 1.0]
        audio_float32 = audio_int16.astype(np.float32) / 32768.0
        
        return audio_float32
    
    async def _transcribe(self, audio: np.ndarray) -> str:
        """
        Transcribe audio using faster-whisper.
        
        Args:
            audio: Float32 audio array
            
        Returns:
            Transcribed text
        """
        if len(audio) == 0:
            return ""
            
        await self._ensure_initialized()
        
        # Run transcription in executor (blocking operation)
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
                without_timestamps=True,  # Faster without timestamps
                condition_on_previous_text=False  # Don't condition on previous
            )
            
            # Collect all segment texts
            return " ".join(segment.text.strip() for segment in segments)
        
        transcription = await loop.run_in_executor(None, _run_transcription)
        return transcription.strip()
    
    async def recognize(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: Optional[str] = None
    ) -> stt.SpeechEvent:
        """
        Recognize speech from audio buffer (non-streaming).
        
        This is called by LiveKit when it has collected enough audio.
        """
        await self._ensure_initialized()
        
        # Convert buffer to numpy
        audio = self._audio_frames_to_numpy(list(buffer))
        
        # Transcribe
        start_time = time.time()
        text = await self._transcribe(audio)
        latency = (time.time() - start_time) * 1000
        
        logger.debug(f"STT transcription: '{text}' (latency: {latency:.0f}ms)")
        
        return SpeechEvent(
            type=SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[SpeechData(text=text, language=language or self.config.language)]
        )
    
    def stream(self) -> "LocalSTTStream":
        """Create a streaming STT session."""
        return LocalSTTStream(self)


class LocalSTTStream(stt.STTStream):
    """
    Streaming STT implementation with VAD-triggered transcription.
    
    Accumulates audio frames, runs VAD to detect speech boundaries,
    and transcribes only when speech segments are complete.
    """
    
    def __init__(self, stt_instance: LocallyHostedSTT):
        super().__init__()
        self._stt = stt_instance
        self._audio_buffer: list[rtc.AudioFrame] = []
        self._is_speaking = False
        self._speech_start_time: Optional[float] = None
        self._silence_frames = 0
        self._min_silence_frames = 8  # ~250ms at typical frame rates
        self._closed = False
        
    async def _run(self) -> AsyncIterator[SpeechEvent]:
        """
        Process incoming audio frames and yield transcription events.
        
        This generator:
        1. Receives audio frames via push_frame()
        2. Runs VAD to detect speech boundaries
        3. Transcribes complete speech segments
        4. Yields SpeechEvent objects
        """
        await self._stt._ensure_initialized()
        
        while not self._closed:
            # Check if we have accumulated speech to process
            if self._audio_buffer and not self._is_speaking:
                # Speech segment ended, transcribe it
                audio = self._stt._audio_frames_to_numpy(self._audio_buffer)
                
                if len(audio) > self._stt.config.sample_rate * 0.25:  # Min 250ms
                    # Emit interim event
                    yield SpeechEvent(
                        type=SpeechEventType.INTERIM_TRANSCRIPT,
                        alternatives=[SpeechData(text="...", language=self._stt.config.language)]
                    )
                    
                    # Transcribe
                    text = await self._stt._transcribe(audio)
                    
                    if text:
                        yield SpeechEvent(
                            type=SpeechEventType.FINAL_TRANSCRIPT,
                            alternatives=[SpeechData(text=text, language=self._stt.config.language)]
                        )
                
                # Clear buffer
                self._audio_buffer.clear()
                self._stt._vad.reset()
                
            await asyncio.sleep(0.01)  # Small yield to prevent busy loop
    
    async def push_frame(self, frame: rtc.AudioFrame):
        """
        Push an audio frame for processing.
        
        Runs VAD on the frame and accumulates speech audio.
        """
        if self._closed:
            return
            
        await self._stt._ensure_initialized()
        
        # Convert frame to numpy for VAD
        audio_data = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) / 32768.0
        
        # Run VAD
        is_speech = self._stt._vad.is_speech(audio_data)
        
        if is_speech:
            self._is_speaking = True
            self._speech_start_time = self._speech_start_time or time.time()
            self._silence_frames = 0
            self._audio_buffer.append(frame)
            
            # Check for max duration
            if time.time() - self._speech_start_time > self._stt.config.max_speech_duration_s:
                self._is_speaking = False
                self._speech_start_time = None
        else:
            if self._is_speaking:
                self._silence_frames += 1
                self._audio_buffer.append(frame)  # Include trailing silence
                
                # End of speech detected
                if self._silence_frames >= self._min_silence_frames:
                    self._is_speaking = False
                    self._speech_start_time = None
    
    async def aclose(self):
        """Close the streaming session."""
        self._closed = True
        
        # Process any remaining audio
        if self._audio_buffer:
            audio = self._stt._audio_frames_to_numpy(self._audio_buffer)
            if len(audio) > self._stt.config.sample_rate * 0.25:
                text = await self._stt._transcribe(audio)
                if text:
                    # Note: In a real implementation, you'd yield this via a queue
                    logger.info(f"Final transcription on close: {text}")
        
        self._audio_buffer.clear()
