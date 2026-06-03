"""
Sovereign Voice Stack - Custom TTS Service
Ultra-low-latency Text-to-Speech using Kokoro ONNX with streaming synthesis.
"""

import asyncio
import logging
import re
import time
import uuid
import os
import queue
import threading
from typing import AsyncIterator, Optional, List, Any
from dataclasses import dataclass
import numpy as np

from kokoro_onnx import Kokoro
from livekit import rtc
from livekit.agents import tts, APIConnectOptions

logger = logging.getLogger(__name__)

import scipy.signal

# Active Audio Source reference for background thread direct FFI push
active_audio_source: Optional[rtc.AudioSource] = None
# Active TTS Stream reference for emergency interruption abort
active_tts_stream: Optional["LocalTTSStream"] = None

# Design digital filters for senior hearing profile at 16kHz
# 1. 5th order Butterworth low-pass filter with a cutoff of 3400Hz
# fs = 16000, Nyquist = 8000. Cutoff = 3400Hz.
LP_SOS = scipy.signal.butter(5, 3400, btype='low', fs=16000, output='sos')

# 2. 2nd order Butterworth bandpass filter for 150Hz - 500Hz register
# fs = 16000, registers = [150, 500]
BP_SOS = scipy.signal.butter(2, [150, 500], btype='bandpass', fs=16000, output='sos')


def resample_to_16k(audio: np.ndarray, orig_fs: int = 24000) -> np.ndarray:
    """Resamples the audio from 24000Hz to 16000Hz using scipy.signal.resample_poly."""
    if audio.size == 0:
        return audio
    if orig_fs == 16000:
        return audio
    try:
        # Use polyphase resampling for speed and quality (24000 -> 16000 is 3 -> 2 decimation)
        gcd = np.gcd(orig_fs, 16000)
        up = 16000 // gcd
        down = orig_fs // gcd
        return scipy.signal.resample_poly(audio, up, down)
    except Exception as e:
        logger.error(f"Failed polyphase resampling: {e}. Falling back to scipy.signal.resample.")
        # Fallback to standard resample
        duration = len(audio) / orig_fs
        new_len = int(duration * 16000)
        return scipy.signal.resample(audio, new_len)


def apply_senior_audio_filters(audio: np.ndarray) -> np.ndarray:
    """
    Applies post-synthesis digital audio filtering to enforce tone compliance
    for an older hearing profile over PSTN lines:
    1. Low-pass filter rolling off above 3400Hz.
    2. Boost of the mid-to-low register (150Hz - 500Hz) by +3dB (1.4125 gain, meaning +0.4125 * bandpass).
    """
    if audio.size == 0:
        return audio
        
    # Apply low-pass filter
    audio_lp = scipy.signal.sosfilt(LP_SOS, audio)
    
    # Extract the bandpass register (150Hz - 500Hz)
    bp_filtered = scipy.signal.sosfilt(BP_SOS, audio_lp)
    
    # Boost by adding the bandpass filtered component with 0.4125 coefficient (corresponding to +3dB peak boost)
    # y = x + 0.4125 * bp_filtered
    equalized = audio_lp + 0.4125 * bp_filtered
    
    # Soft clip/limiter to prevent any digital clipping after boosting
    peak = np.max(np.abs(equalized))
    if peak > 0.95:
        equalized = np.clip(equalized, -1.0, 1.0)
        
    return equalized


@dataclass
class TTSConfig:
    """Configuration for the local TTS service."""
    model_name: str = "kokoro-v1.0"
    voice: str = "af_bella"  # American female voice
    speed: float = 1.0
    sample_rate: int = 16000
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
    Optimized for 3 to 5 tokens (words) to allow immediate streaming to TTS.
    """
    def __init__(self, min_tokens: int = 3, max_tokens: int = 5):
        self.buffer = ""
        self.min_tokens = min_tokens
        self.max_tokens = max_tokens

    def feed(self, text: str) -> List[str]:
        if not text:
            return []
            
        self.buffer += text
        phrases = []
        
        while True:
            # Split by whitespace to find words
            words = self.buffer.split()
            if not words:
                break
                
            # If buffer ends with space, then all words in the buffer are completed.
            # Otherwise, the last word might still be incomplete.
            ends_with_space = self.buffer and self.buffer[-1].isspace()
            completed_count = len(words) if ends_with_space else len(words) - 1
            
            if completed_count >= self.min_tokens:
                chunk_len = min(completed_count, self.max_tokens)
                chunk_words = words[:chunk_len]
                phrase = " ".join(chunk_words)
                phrases.append(phrase)
                
                # Reconstruct the remaining buffer
                idx = 0
                for w in chunk_words:
                    idx = self.buffer.find(w, idx) + len(w)
                self.buffer = self.buffer[idx:].lstrip()
            else:
                break
                
        return phrases

    def flush(self) -> List[str]:
        phrase = self.buffer.strip()
        self.buffer = ""
        if phrase:
            return [phrase]
        return []


def crossfade_audio(prev_audio: np.ndarray, next_audio: np.ndarray, fade_samples: int = 120) -> np.ndarray:
    """Cross-fades the end of previous audio with start of next audio over fade_samples."""
    if len(prev_audio) == 0:
        return next_audio
    if len(next_audio) == 0:
        return prev_audio
        
    fade_samples = min(fade_samples, len(prev_audio), len(next_audio))
    if fade_samples <= 0:
        return np.concatenate([prev_audio, next_audio])
        
    prev_fade = prev_audio[-fade_samples:]
    next_fade = next_audio[:fade_samples]
    
    t = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
    blended = prev_fade * (1.0 - t) + next_fade * t
    
    combined = np.concatenate([
        prev_audio[:-fade_samples],
        blended,
        next_audio[fade_samples:]
    ])
    return combined


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
            audio_resampled = resample_to_16k(audio, orig_fs=24000)
            audio_filtered = apply_senior_audio_filters(audio_resampled)
            return audio_filtered
        
        audio = await loop.run_in_executor(None, _run_synthesis)
        return audio

    def _synthesize_audio_sync(self, text: str) -> np.ndarray:
        """Synchronous synthesis interface for background worker thread."""
        if not text.strip():
            return np.array([], dtype=np.float32)
            
        if not self._initialized:
            model_path = os.environ.get("KOKORO_MODEL_PATH", "/root/.cache/kokoro/kokoro-v1.0.onnx")
            voices_path = os.environ.get("KOKORO_VOICES_PATH", "/root/.cache/kokoro/voices-v1.0.bin")
            if not os.path.exists(model_path):
                if os.path.exists("models/kokoro-v1.0.onnx"):
                    model_path = "models/kokoro-v1.0.onnx"
                    voices_path = "models/voices-v1.0.bin"
                else:
                    model_path = self.config.model_name
                    voices_path = "voices.bin"
            logger.info(f"Sync-loading Kokoro model: {model_path}...")
            try:
                self._model = Kokoro(model_path, voices_path)
            except Exception as e:
                logger.warning(f"Failed sync-load of Kokoro ({e}). Using MockKokoro.")
                self._model = MockKokoro(model_path, voices_path)
            self._initialized = True
            
        audio, _ = self._model.create(
            text=text,
            voice=self.config.voice,
            speed=self.config.speed
        )
        audio_resampled = resample_to_16k(audio, orig_fs=24000)
        audio_filtered = apply_senior_audio_filters(audio_resampled)
        return audio_filtered
    
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
    Streaming TTS implementation using a dedicated background thread to bypass event loop GIL bottlenecks.
    """
    
    def __init__(self, *, tts: LocallyHostedKokoro, conn_options: APIConnectOptions):
        super().__init__(tts=tts, conn_options=conn_options)
        self._tts = tts
        self._chunker = FastPhraseChunker(min_tokens=3, max_tokens=5)
        
        self._loop = asyncio.get_event_loop()
        self._text_queue = queue.Queue()
        self._main_thread_audio_queue = asyncio.Queue()
        
        self._stop_event = threading.Event()
        self._interrupt_event = threading.Event()
        self._flush_event = threading.Event()
        self._input_ended_flag = False
        
        self._request_id = str(uuid.uuid4())
        self._segment_id = str(uuid.uuid4())
        
        # Spawn the background worker thread
        self._worker_thread = threading.Thread(
            target=self._background_worker,
            name=f"TTS-Worker-{self._request_id}",
            daemon=True
        )
        self._worker_thread.start()

        global active_tts_stream
        active_tts_stream = self

    @property
    def sample_rate(self) -> int:
        return self._tts.sample_rate

    @property
    def num_channels(self) -> int:
        return 1
        
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        try:
            async for input_data in self._input_ch:
                if isinstance(input_data, str):
                    phrases = self._chunker.feed(input_data)
                    for p in phrases:
                        self._text_queue.put(p)
                elif isinstance(input_data, self._FlushSentinel):
                    phrases = self._chunker.flush()
                    for p in phrases:
                        self._text_queue.put(p)
                    self._flush_event.set()
            
            # Input channel EOF
            phrases = self._chunker.flush()
            for p in phrases:
                self._text_queue.put(p)
            self._input_ended_flag = True
            self._flush_event.set()
            
        except asyncio.CancelledError:
            self._interrupt_event.set()
            raise
        except Exception as e:
            logger.error(f"Error in TTS stream _run: {e}")
            self._interrupt_event.set()
            raise

    def _clear_main_queue(self) -> None:
        while not self._main_thread_audio_queue.empty():
            try:
                self._main_thread_audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _background_worker(self) -> None:
        import time
        from livekit.rtc._ffi_client import FfiClient
        from livekit.rtc._proto import ffi_pb2 as proto_ffi
        
        prev_time = time.perf_counter()
        sample_rate = self.sample_rate
        frame_duration = 0.020 # 20ms
        samples_per_frame = int(sample_rate * frame_duration)
        num_channels = 1
        
        remaining_audio = np.array([], dtype=np.float32)
        
        while not self._stop_event.is_set():
            if self._interrupt_event.is_set():
                remaining_audio = np.array([], dtype=np.float32)
                # Clear text queue
                with self._text_queue.mutex:
                    self._text_queue.queue.clear()
                # Clear main thread audio queue
                self._loop.call_soon_threadsafe(self._clear_main_queue)
                self._interrupt_event.clear()
                self._flush_event.clear()
                continue
                
            # 1. Pull new text phrase if any
            try:
                phrase = self._text_queue.get_nowait()
                normalized = normalize_text(phrase)
                if normalized:
                    new_audio = self._tts._synthesize_audio_sync(normalized)
                    if len(new_audio) > 0:
                        remaining_audio = crossfade_audio(remaining_audio, new_audio)
            except queue.Empty:
                pass
                
            # 2. If we have enough samples, pop and push
            if len(remaining_audio) >= samples_per_frame:
                block = remaining_audio[:samples_per_frame]
                remaining_audio = remaining_audio[samples_per_frame:]
                
                # Convert to int16 PCM
                block_int16 = (block * 32767).astype(np.int16)
                pcm_bytes = block_int16.tobytes()
                
                # Direct FFI push
                active_src = active_audio_source
                if active_src and not active_src._ffi_handle.disposed:
                    frame = rtc.AudioFrame(pcm_bytes, sample_rate, num_channels, samples_per_frame)
                    req = proto_ffi.FfiRequest()
                    req.capture_audio_frame.source_handle = active_src._ffi_handle.handle
                    req.capture_audio_frame.buffer.CopyFrom(frame._proto_info())
                    FfiClient.instance.request(req)
                    
                # Main thread queue push
                frame = rtc.AudioFrame(pcm_bytes, sample_rate, num_channels, samples_per_frame)
                sa = tts.SynthesizedAudio(
                    frame=frame,
                    request_id=self._request_id,
                    segment_id=self._segment_id or "",
                    is_final=False,
                )
                self._loop.call_soon_threadsafe(self._main_thread_audio_queue.put_nowait, sa)
                
                # Sleep/pace
                sleep_time = frame_duration - (time.perf_counter() - prev_time)
                if sleep_time > 0:
                    time.sleep(sleep_time)
                prev_time = time.perf_counter()
                continue
                
            # 3. If we don't have enough samples, but flush or end is requested
            if self._flush_event.is_set() or self._input_ended_flag:
                # If we have some audio, pad it to 20ms and push
                if len(remaining_audio) > 0:
                    block = np.zeros(samples_per_frame, dtype=np.float32)
                    block[:len(remaining_audio)] = remaining_audio
                    remaining_audio = np.array([], dtype=np.float32)
                    
                    block_int16 = (block * 32767).astype(np.int16)
                    pcm_bytes = block_int16.tobytes()
                    
                    active_src = active_audio_source
                    if active_src and not active_src._ffi_handle.disposed:
                        frame = rtc.AudioFrame(pcm_bytes, sample_rate, num_channels, samples_per_frame)
                        req = proto_ffi.FfiRequest()
                        req.capture_audio_frame.source_handle = active_src._ffi_handle.handle
                        req.capture_audio_frame.buffer.CopyFrom(frame._proto_info())
                        FfiClient.instance.request(req)
                        
                    frame = rtc.AudioFrame(pcm_bytes, sample_rate, num_channels, samples_per_frame)
                    sa = tts.SynthesizedAudio(
                        frame=frame,
                        request_id=self._request_id,
                        segment_id=self._segment_id or "",
                        is_final=False,
                    )
                    self._loop.call_soon_threadsafe(self._main_thread_audio_queue.put_nowait, sa)
                    
                    sleep_time = frame_duration - (time.perf_counter() - prev_time)
                    if sleep_time > 0:
                        time.sleep(sleep_time)
                    prev_time = time.perf_counter()
                
                self._flush_event.clear()
                if self._input_ended_flag and self._text_queue.empty() and len(remaining_audio) == 0:
                    # Put sentinel to end iteration
                    self._loop.call_soon_threadsafe(self._main_thread_audio_queue.put_nowait, None)
                    break
                continue
                
            # Nothing to do, sleep briefly
            time.sleep(0.005)

    async def __anext__(self) -> tts.SynthesizedAudio:
        try:
            val = await self._main_thread_audio_queue.get()
            if val is None:
                raise StopAsyncIteration
            return val
        except asyncio.CancelledError:
            raise StopAsyncIteration

    def __aiter__(self) -> "LocalTTSStream":
        return self

    async def interrupt(self) -> None:
        self._interrupt_event.set()
        self._clear_main_queue()
        
    async def aclose(self) -> None:
        self._stop_event.set()
        self._interrupt_event.set()
        await super().aclose()
        if self._tts._active_stream is self:
            self._tts._active_stream = None
        global active_tts_stream
        if active_tts_stream is self:
            active_tts_stream = None


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
