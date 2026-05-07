"""
Sovereign Voice Stack - Custom TTS Service
Ultra-low-latency Text-to-Speech using Kokoro ONNX with streaming synthesis.

This module wraps Kokoro ONNX in a LiveKit-compatible TTS interface,
running entirely in-process for zero network latency. Critically, it
implements streaming synthesis - generating audio phrase-by-phrase
as LLM tokens arrive, not waiting for the full response.
"""

import asyncio
import logging
import re
import time
from typing import AsyncIterator, Optional
from dataclasses import dataclass
import numpy as np

from kokoro_onnx import Kokoro
from livekit import rtc
from livekit.agents import tts

logger = logging.getLogger(__name__)


@dataclass
class TTSConfig:
    """Configuration for the local TTS service."""
    model_name: str = "kokoro-v1.0"
    voice: str = "af_bella"  # American female voice
    speed: float = 1.0
    sample_rate: int = 24000
    # Phrase buffering for streaming
    min_phrase_chars: int = 10  # Minimum chars before synthesis
    sentence_end_chars: str = ".!?;:"  # Characters that end a phrase


class LocallyHostedKokoro(tts.TTS):
    """
    Custom TTS implementation using Kokoro ONNX.
    
    Features:
    - Runs entirely on local GPU (zero network latency)
    - Streaming synthesis: generates audio phrase-by-phrase
    - Yields AudioFrames as soon as phrases are synthesized
    - Does NOT wait for full LLM response
    """
    
    def __init__(self, config: Optional[TTSConfig] = None):
        super().__init__()
        self.config = config or TTSConfig()
        self._model: Optional[Kokoro] = None
        self._initialized = False
        self._lock = asyncio.Lock()
        
    @property
    def sample_rate(self) -> int:
        """Return the sample rate of generated audio."""
        return self.config.sample_rate
    
    async def initialize(self):
        """Initialize the Kokoro TTS model."""
        async with self._lock:
            if self._initialized:
                return
                
            logger.info(f"Loading Kokoro TTS model: {self.config.model_name}...")
            start_time = time.time()
            
            # Load model in executor (blocking operation)
            loop = asyncio.get_event_loop()
            self._model = await loop.run_in_executor(
                None,
                lambda: Kokoro(self.config.model_name)
            )
            
            load_time = time.time() - start_time
            logger.info(f"Kokoro TTS initialized in {load_time:.2f}s")
            self._initialized = True
            
    async def _ensure_initialized(self):
        """Ensure the TTS is initialized before use."""
        if not self._initialized:
            await self.initialize()
    
    def _split_into_phrases(self, text: str) -> list[str]:
        """
        Split text into synthesizable phrases.
        
        Splits on sentence boundaries while respecting minimum length.
        This allows streaming synthesis as text arrives.
        """
        if not text:
            return []
            
        # Split on sentence-ending punctuation
        pattern = f'([{re.escape(self.config.sentence_end_chars)}])'
        parts = re.split(pattern, text)
        
        phrases = []
        current_phrase = ""
        
        for part in parts:
            current_phrase += part
            
            # Check if this is a sentence ender and we have enough text
            if (part in self.config.sentence_end_chars and 
                len(current_phrase.strip()) >= self.config.min_phrase_chars):
                phrases.append(current_phrase.strip())
                current_phrase = ""
        
        # Don't forget remaining text
        if current_phrase.strip():
            phrases.append(current_phrase.strip())
            
        return phrases
    
    async def _synthesize_audio(self, text: str) -> np.ndarray:
        """
        Synthesize audio for given text.
        
        Args:
            text: Text to synthesize
            
        Returns:
            Audio samples as float32 numpy array
        """
        if not text.strip():
            return np.array([], dtype=np.float32)
            
        await self._ensure_initialized()
        
        # Run synthesis in executor (blocking operation)
        loop = asyncio.get_event_loop()
        
        def _run_synthesis():
            audio, _ = self._model.create(
                text=text,
                voice=self.config.voice,
                speed=self.config.speed
            )
            return audio
        
        audio = await loop.run_in_executor(None, _run_synthesis)
        return audio
    
    def _numpy_to_audio_frame(self, audio: np.ndarray) -> rtc.AudioFrame:
        """
        Convert numpy audio array to LiveKit AudioFrame.
        
        Args:
            audio: Float32 audio samples [-1.0, 1.0]
            
        Returns:
            LiveKit AudioFrame
        """
        # Convert float32 to int16
        audio_int16 = (audio * 32767).astype(np.int16)
        
        # Create AudioFrame
        frame = rtc.AudioFrame(
            data=audio_int16.tobytes(),
            sample_rate=self.config.sample_rate,
            num_channels=1,
            samples_per_channel=len(audio_int16)
        )
        
        return frame
    
    async def synthesize(self, text: str) -> AsyncIterator[rtc.AudioFrame]:
        """
        Synthesize text to audio frames (non-streaming).
        
        Yields audio frames for the complete text.
        """
        await self._ensure_initialized()
        
        start_time = time.time()
        audio = await self._synthesize_audio(text)
        
        if len(audio) > 0:
            # Chunk the audio into frames (e.g., 20ms chunks)
            chunk_samples = int(self.config.sample_rate * 0.02)  # 20ms
            
            for i in range(0, len(audio), chunk_samples):
                chunk = audio[i:i + chunk_samples]
                if len(chunk) > 0:
                    yield self._numpy_to_audio_frame(chunk)
                    
        latency = (time.time() - start_time) * 1000
        logger.debug(f"TTS synthesis: {len(text)} chars, {len(audio)/self.config.sample_rate:.2f}s audio (latency: {latency:.0f}ms)")
    
    def stream(self) -> "LocalTTSStream":
        """Create a streaming TTS session."""
        return LocalTTSStream(self)


class LocalTTSStream(tts.TTSStream):
    """
    Streaming TTS implementation for phrase-by-phrase synthesis.
    
    This is the KEY to low latency: rather than waiting for the complete
    LLM response, we synthesize audio for each phrase/sentence as it
    arrives. This dramatically reduces time-to-first-audio-byte.
    """
    
    def __init__(self, tts_instance: LocallyHostedKokoro):
        super().__init__()
        self._tts = tts_instance
        self._text_buffer = ""
        self._audio_queue: asyncio.Queue[Optional[rtc.AudioFrame]] = asyncio.Queue()
        self._synthesis_task: Optional[asyncio.Task] = None
        self._closed = False
        self._interrupted = False
        
    async def push_text(self, text: str):
        """
        Push text chunk for streaming synthesis.
        
        Called as LLM tokens arrive. Buffers text until a complete
        phrase/sentence is ready, then synthesizes immediately.
        """
        if self._closed or self._interrupted:
            return
            
        self._text_buffer += text
        
        # Check for complete phrases
        phrases = self._tts._split_into_phrases(self._text_buffer)
        
        if len(phrases) > 1:
            # We have at least one complete phrase - synthesize it
            complete_phrase = phrases[0]
            self._text_buffer = "".join(phrases[1:])
            
            # Start synthesis task if not already running
            await self._synthesize_phrase(complete_phrase)
    
    async def _synthesize_phrase(self, phrase: str):
        """Synthesize a single phrase and queue the audio frames."""
        if self._interrupted:
            return
            
        try:
            start_time = time.time()
            audio = await self._tts._synthesize_audio(phrase)
            
            if len(audio) > 0 and not self._interrupted:
                # Chunk into frames
                chunk_samples = int(self._tts.config.sample_rate * 0.02)
                
                for i in range(0, len(audio), chunk_samples):
                    if self._interrupted:
                        break
                    chunk = audio[i:i + chunk_samples]
                    if len(chunk) > 0:
                        frame = self._tts._numpy_to_audio_frame(chunk)
                        await self._audio_queue.put(frame)
                        
            latency = (time.time() - start_time) * 1000
            logger.debug(f"Phrase synthesized: '{phrase[:30]}...' ({latency:.0f}ms)")
            
        except Exception as e:
            logger.error(f"Synthesis error: {e}")
    
    async def flush(self):
        """
        Flush remaining buffered text.
        
        Called when LLM response is complete to ensure any
        remaining text is synthesized.
        """
        if self._text_buffer.strip() and not self._interrupted:
            await self._synthesize_phrase(self._text_buffer.strip())
            self._text_buffer = ""
            
        # Signal end of stream
        await self._audio_queue.put(None)
    
    async def __anext__(self) -> rtc.AudioFrame:
        """Get the next audio frame from the queue."""
        frame = await self._audio_queue.get()
        if frame is None:
            raise StopAsyncIteration
        return frame
    
    def __aiter__(self):
        """Make this stream async iterable."""
        return self
    
    async def interrupt(self):
        """
        Interrupt the current synthesis.
        
        Called when user starts speaking (barge-in). Immediately
        stops synthesis and clears all queued audio.
        """
        logger.info("TTS interrupted - clearing audio buffer")
        self._interrupted = True
        
        # Clear the audio queue
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
                
        # Clear text buffer
        self._text_buffer = ""
        
        # Cancel any ongoing synthesis
        if self._synthesis_task and not self._synthesis_task.done():
            self._synthesis_task.cancel()
    
    async def aclose(self):
        """Close the streaming session."""
        self._closed = True
        await self.flush()


class StreamingTTSAdapter:
    """
    Adapter for integrating streaming TTS with LiveKit's VoiceAssistant.
    
    This class bridges the gap between our streaming TTS implementation
    and LiveKit's expected interface, handling:
    - Token buffering from LLM
    - Phrase detection and synthesis
    - Audio frame yielding
    - Interruption handling
    """
    
    def __init__(self, tts: LocallyHostedKokoro):
        self._tts = tts
        self._current_stream: Optional[LocalTTSStream] = None
        
    async def synthesize_stream(
        self,
        text_stream: AsyncIterator[str]
    ) -> AsyncIterator[rtc.AudioFrame]:
        """
        Synthesize streaming text to audio frames.
        
        Takes an async iterator of text chunks (from LLM) and yields
        audio frames as phrases are synthesized.
        """
        self._current_stream = self._tts.stream()
        
        async def process_text():
            async for text_chunk in text_stream:
                await self._current_stream.push_text(text_chunk)
            await self._current_stream.flush()
        
        # Start text processing in background
        text_task = asyncio.create_task(process_text())
        
        try:
            # Yield audio frames as they're synthesized
            async for frame in self._current_stream:
                yield frame
        finally:
            text_task.cancel()
            try:
                await text_task
            except asyncio.CancelledError:
                pass
    
    async def interrupt(self):
        """Interrupt current synthesis."""
        if self._current_stream:
            await self._current_stream.interrupt()
