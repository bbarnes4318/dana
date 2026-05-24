"""
Sovereign Voice Stack - Custom TTS Service
Ultra-low-latency Text-to-Speech using Kokoro ONNX with streaming synthesis.
"""

import asyncio
import logging
import re
import time
from typing import AsyncIterator, Optional, List
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


def normalize_text(text: str) -> str:
    """
    Pronunciation normalizer for phone speech:
    - "AI" -> "A I"
    - "$" -> "dollars"
    - "%" -> "percent"
    - Remove markdown formatting
    - Collapse multiple whitespaces
    """
    if not text:
        return ""
    # Remove markdown characters (*, _, `, #, ~, [, ], (, ))
    text = re.sub(r'[*_`#~\[\]()]', '', text)
    
    # Replace AI with A I
    text = re.sub(r'\bAI\b', 'A I', text)
    
    # Replace $XX with XX dollars (e.g. $5 -> 5 dollars, $10.50 -> 10.50 dollars)
    text = re.sub(r'\$(\d+(?:\.\d+)?)', r'\1 dollars', text)
    text = text.replace("$", " dollars ")
    
    # Replace % with percent
    text = text.replace("%", " percent")
    
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


class FastPhraseChunker:
    """
    FastPhraseChunker flushes text segments as early as possible.
    Rules:
    - Flush on punctuation: . ? ! ; :
    - Flush on clean word boundary when buffer length >= 18
    - Flush after 150ms from first token when buffer length >= 8
    - Never wait for full LLM response
    """
    def __init__(self):
        self.buffer = ""
        self.first_token_time: Optional[float] = None

    def feed(self, text: str) -> List[str]:
        if not text:
            return []
            
        if self.first_token_time is None:
            self.first_token_time = time.perf_counter()
            
        self.buffer += text
        phrases = []
        
        while True:
            # 1. Check for punctuation flush (. ? ! ; :)
            punc_idx = -1
            for i, char in enumerate(self.buffer):
                if char in ".?!;:":
                    punc_idx = i
                    break
                    
            if punc_idx != -1:
                phrase = self.buffer[:punc_idx + 1]
                self.buffer = self.buffer[punc_idx + 1:]
                phrases.append(phrase)
                self.first_token_time = time.perf_counter() if self.buffer else None
                continue
                
            # 2. Check for clean word boundary if buffer length >= 18
            last_space = self.buffer.rfind(" ")
            if last_space == -1:
                last_space = self.buffer.rfind("\n")
            if last_space == -1:
                last_space = self.buffer.rfind("\t")
                
            if last_space != -1 and last_space >= 18:
                phrase = self.buffer[:last_space]
                self.buffer = self.buffer[last_space + 1:]
                phrases.append(phrase)
                self.first_token_time = time.perf_counter() if self.buffer else None
                continue
                
            # 3. Check for timeout flush (150ms elapsed, length >= 8)
            if self.first_token_time is not None:
                elapsed_ms = (time.perf_counter() - self.first_token_time) * 1000.0
                if elapsed_ms > 150.0 and len(self.buffer) >= 8:
                    last_space = self.buffer.rfind(" ")
                    if last_space == -1:
                        last_space = self.buffer.rfind("\n")
                        
                    if last_space != -1:
                        phrase = self.buffer[:last_space]
                        self.buffer = self.buffer[last_space + 1:]
                        if phrase.strip():
                            phrases.append(phrase)
                    else:
                        phrase = self.buffer
                        self.buffer = ""
                        if phrase.strip():
                            phrases.append(phrase)
                    self.first_token_time = time.perf_counter() if self.buffer else None
                    continue
                    
            break
            
        return [p.strip() for p in phrases if p.strip()]

    def flush(self) -> List[str]:
        phrase = self.buffer.strip()
        self.buffer = ""
        self.first_token_time = None
        if phrase:
            return [phrase]
        return []


class LocallyHostedKokoro(tts.TTS):
    """
    Custom TTS implementation using Kokoro ONNX.
    """
    
    def __init__(self, config: Optional[TTSConfig] = None):
        super().__init__()
        self.config = config or TTSConfig()
        self._model: Optional[Kokoro] = None
        self._initialized = False
        self._lock = asyncio.Lock()
        self._active_stream: Optional["LocalTTSStream"] = None
        
    @property
    def sample_rate(self) -> int:
        return self.config.sample_rate
    
    async def initialize(self):
        async with self._lock:
            if self._initialized:
                return
                
            logger.info(f"Loading Kokoro TTS model: {self.config.model_name}...")
            start_time = time.time()
            
            loop = asyncio.get_event_loop()
            self._model = await loop.run_in_executor(
                None,
                lambda: Kokoro(self.config.model_name)
            )
            
            load_time = time.time() - start_time
            logger.info(f"Kokoro TTS initialized in {load_time:.2f}s")
            self._initialized = True
            
    async def _ensure_initialized(self):
        if not self._initialized:
            await self.initialize()
    
    async def _synthesize_audio(self, text: str) -> np.ndarray:
        if not text.strip():
            return np.array([], dtype=np.float32)
            
        await self._ensure_initialized()
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
        audio_int16 = (audio * 32767).astype(np.int16)
        frame = rtc.AudioFrame(
            data=audio_int16.tobytes(),
            sample_rate=self.config.sample_rate,
            num_channels=1,
            samples_per_channel=len(audio_int16)
        )
        return frame
    
    async def synthesize(self, text: str) -> AsyncIterator[rtc.AudioFrame]:
        await self._ensure_initialized()
        audio = await self._synthesize_audio(text)
        
        if len(audio) > 0:
            chunk_samples = int(self.config.sample_rate * 0.02)  # 20ms
            for i in range(0, len(audio), chunk_samples):
                chunk = audio[i:i + chunk_samples]
                if len(chunk) > 0:
                    yield self._numpy_to_audio_frame(chunk)
    
    def stream(self) -> "LocalTTSStream":
        stream = LocalTTSStream(self)
        self._active_stream = stream
        return stream


class LocalTTSStream(tts.TTSStream):
    """
    Streaming TTS implementation for phrase-by-phrase synthesis.
    """
    
    def __init__(self, tts_instance: LocallyHostedKokoro):
        super().__init__()
        self._tts = tts_instance
        self._chunker = FastPhraseChunker()
        self._audio_queue: asyncio.Queue[Optional[rtc.AudioFrame]] = asyncio.Queue()
        self._phrase_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
        self._synthesis_task: Optional[asyncio.Task] = None
        self._process_loop_task: Optional[asyncio.Task] = None
        self._closed = False
        self._interrupted = False
        
        # Start background phrase processing loop
        self._process_loop_task = asyncio.create_task(self._process_phrases_loop())
        
    async def _process_phrases_loop(self):
        try:
            while not self._interrupted and not self._closed:
                phrase = await self._phrase_queue.get()
                if phrase is None:
                    await self._audio_queue.put(None)
                    break
                    
                await self._synthesize_phrase(phrase)
                self._phrase_queue.task_done()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in TTS process loop: {e}")
            
    async def push_text(self, text: str):
        if self._closed or self._interrupted:
            return
            
        phrases = self._chunker.feed(text)
        for phrase in phrases:
            normalized = normalize_text(phrase)
            if normalized:
                await self._phrase_queue.put(normalized)
    
    async def _synthesize_phrase(self, phrase: str):
        if self._interrupted:
            return
            
        try:
            start_time = time.time()
            self._synthesis_task = asyncio.create_task(self._tts._synthesize_audio(phrase))
            audio = await self._synthesis_task
            
            if len(audio) > 0 and not self._interrupted:
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
            
        except asyncio.CancelledError:
            logger.debug("TTS phrase synthesis was cancelled")
        except Exception as e:
            logger.error(f"Synthesis error: {e}")
    
    async def flush(self):
        if self._closed or self._interrupted:
            return
            
        phrases = self._chunker.flush()
        for phrase in phrases:
            normalized = normalize_text(phrase)
            if normalized:
                await self._phrase_queue.put(normalized)
                
        await self._phrase_queue.put(None)
    
    async def __anext__(self) -> rtc.AudioFrame:
        if self._interrupted:
            raise StopAsyncIteration
            
        frame = await self._audio_queue.get()
        if frame is None or self._interrupted:
            raise StopAsyncIteration
        return frame
    
    def __aiter__(self):
        return self
    
    async def interrupt(self):
        logger.info("TTS interrupted - clearing audio and phrase buffers")
        self._interrupted = True
        
        # Cancel the loops
        if self._process_loop_task and not self._process_loop_task.done():
            self._process_loop_task.cancel()
            
        if self._synthesis_task and not self._synthesis_task.done():
            self._synthesis_task.cancel()
            
        # Clear queues
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
                
        while not self._phrase_queue.empty():
            try:
                self._phrase_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
                
    async def aclose(self):
        self._closed = True
        await self.flush()
        if self._tts._active_stream is self:
            self._tts._active_stream = None
