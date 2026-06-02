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
import os
import uuid

from kokoro_onnx import Kokoro
from livekit import rtc
from livekit.agents import tts, APIConnectOptions

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


class MockKokoro:
    def __init__(self, model_path: str, voices_path: str):
        self.model_path = model_path
        self.voices_path = voices_path

    def create(self, text: str, voice: str, speed: float):
        # 0.5s of silence at 24000Hz
        audio = np.zeros(12000, dtype=np.float32)
        return audio, None


class LocallyHostedKokoro(tts.TTS):
    """
    Custom TTS implementation using Kokoro ONNX.
    """
    
    def __init__(self, config: Optional[TTSConfig] = None):
        self.config = config or TTSConfig()
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True),
            sample_rate=self.config.sample_rate,
            num_channels=1,
        )
        self._model: Optional[Any] = None
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
                
            model_path = os.environ.get("KOKORO_MODEL_PATH", "/root/.cache/kokoro/kokoro-v1.0.onnx")
            voices_path = os.environ.get("KOKORO_VOICES_PATH", "/root/.cache/kokoro/voices-v1.0.bin")
            
            # Local fallback for tests
            if not os.path.exists(model_path):
                if os.path.exists("models/kokoro-v1.0.onnx"):
                    model_path = "models/kokoro-v1.0.onnx"
                    voices_path = "models/voices-v1.0.bin"
                else:
                    model_path = self.config.model_name
                    voices_path = "voices.bin"
                    
            logger.info(f"Loading Kokoro TTS model: {model_path} with voices: {voices_path}...")
            start_time = time.time()
            
            loop = asyncio.get_event_loop()
            try:
                self._model = await loop.run_in_executor(
                    None,
                    lambda: Kokoro(model_path, voices_path)
                )
                load_time = time.time() - start_time
                logger.info(f"Kokoro TTS initialized in {load_time:.2f}s")
            except (FileNotFoundError, Exception) as e:
                logger.warning(f"Failed to load Kokoro ONNX model ({e}). Falling back to MockKokoro.")
                self._model = MockKokoro(model_path, voices_path)
                
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
    
    def synthesize(
        self,
        text: str,
        *,
        conn_options: Optional[APIConnectOptions] = None,
    ) -> tts.ChunkedStream:
        conn_options = conn_options or APIConnectOptions(
            max_retry=3, retry_interval=2.0, timeout=10.0
        )
        return LocalChunkedStream(
            tts=self,
            text=text,
            conn_options=conn_options,
        )
    
    def stream(
        self,
        *,
        conn_options: Optional[APIConnectOptions] = None,
    ) -> "LocalTTSStream":
        conn_options = conn_options or APIConnectOptions(
            max_retry=3, retry_interval=2.0, timeout=10.0
        )
        stream = LocalTTSStream(tts=self, conn_options=conn_options)
        self._active_stream = stream
        return stream


class LocalTTSStream(tts.SynthesizeStream):
    """
    Streaming TTS implementation for phrase-by-phrase synthesis.
    """
    
    def __init__(self, *, tts: LocallyHostedKokoro, conn_options: APIConnectOptions):
        super().__init__(tts=tts, conn_options=conn_options)
        self._tts = tts
        self._chunker = FastPhraseChunker()

    @property
    def sample_rate(self) -> int:
        return self._tts.sample_rate

    @property
    def num_channels(self) -> int:
        return 1
        
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        request_id = str(uuid.uuid4())
        output_emitter.initialize(
            request_id=request_id,
            sample_rate=self._tts.config.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
            stream=True,
        )

        segment_id = None

        async def _process_phrase(phrase: str):
            nonlocal segment_id
            normalized = normalize_text(phrase)
            if not normalized:
                return
            
            if segment_id is None:
                segment_id = str(uuid.uuid4())
                output_emitter.start_segment(segment_id=segment_id)

            # Synthesize audio to numpy array
            audio = await self._tts._synthesize_audio(normalized)
            if len(audio) > 0:
                audio_int16 = (audio * 32767).astype(np.int16)
                output_emitter.push(audio_int16.tobytes())

        try:
            async for input_data in self._input_ch:
                if isinstance(input_data, str):
                    phrases = self._chunker.feed(input_data)
                    for p in phrases:
                        await _process_phrase(p)
                elif isinstance(input_data, self._FlushSentinel):
                    phrases = self._chunker.flush()
                    for p in phrases:
                        await _process_phrase(p)
                    if segment_id is not None:
                        output_emitter.end_segment()
                        segment_id = None
            
            # Input channel closed (EOF)
            phrases = self._chunker.flush()
            for p in phrases:
                await _process_phrase(p)
            if segment_id is not None:
                output_emitter.end_segment()
                segment_id = None

        except asyncio.CancelledError:
            logger.debug("TTS stream _run cancelled")
            raise
        except Exception as e:
            logger.error(f"Error in TTS stream _run: {e}")
            raise

    async def aclose(self) -> None:
        await super().aclose()
        if self._tts._active_stream is self:
            self._tts._active_stream = None


class LocalChunkedStream(tts.ChunkedStream):
    """
    Chunked TTS implementation for synthesizing a full block of text.
    """
    def __init__(self, *, tts: LocallyHostedKokoro, text: str, conn_options: APIConnectOptions):
        super().__init__(tts=tts, text=text, conn_options=conn_options)
        self._tts = tts
        self._text = text

    @property
    def sample_rate(self) -> int:
        return self._tts.sample_rate

    @property
    def num_channels(self) -> int:
        return 1

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        request_id = str(uuid.uuid4())
        output_emitter.initialize(
            request_id=request_id,
            sample_rate=self._tts.config.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
            stream=False,
        )

        try:
            normalized = normalize_text(self._text)
            if normalized:
                audio = await self._tts._synthesize_audio(normalized)
                if len(audio) > 0:
                    audio_int16 = (audio * 32767).astype(np.int16)
                    output_emitter.push(audio_int16.tobytes())
        except asyncio.CancelledError:
            logger.debug("TTS chunked stream _run cancelled")
            raise
        except Exception as e:
            logger.error(f"Error in TTS chunked stream _run: {e}")
            raise
