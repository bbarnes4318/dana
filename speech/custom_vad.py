from __future__ import annotations
import asyncio
import time
import logging
from typing import Literal
import numpy as np

from livekit import rtc
from livekit.agents import vad, utils
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.utils import is_given

from livekit.plugins.silero import onnx_model
from livekit.plugins import silero
from livekit.plugins.silero.vad import _VADOptions

logger = logging.getLogger(__name__)


class ElderlySileroVAD(silero.VAD):
    """
    Production-grade Silero VAD wrapper optimized for an elderly demographic.
    Subclasses silero.VAD directly to leverage official configurations.
    Uses zero-allocation buffers on the frame-processing hot path.
    """
    
    @classmethod
    def load(
        cls,
        *,
        min_speech_duration: float = 0.05,
        min_silence_duration: float = 0.3,      # Tailored for elderly speakers: 300ms
        prefix_padding_duration: float = 0.1,   # speech_pad_ms: 100ms
        max_buffered_speech: float = 60.0,
        activation_threshold: float = 0.4,      # threshold: 0.4
        sample_rate: Literal[8000, 16000] = 16000,
        force_cpu: bool = True,
        onnx_file_path: NotGivenOr[str] = NOT_GIVEN,
        deactivation_threshold: NotGivenOr[float] = NOT_GIVEN,
    ) -> ElderlySileroVAD:
        """
        Load the Silero VAD model with custom thresholds.
        """
        if sample_rate not in [8000, 16000]:
            raise ValueError("Silero VAD only supports 8KHz and 16KHz sample rates")
            
        session = onnx_model.new_inference_session(force_cpu, onnx_file_path=onnx_file_path or None)
        opts = _VADOptions(
            min_speech_duration=min_speech_duration,
            min_silence_duration=min_silence_duration,
            prefix_padding_duration=prefix_padding_duration,
            max_buffered_speech=max_buffered_speech,
            activation_threshold=activation_threshold,
            deactivation_threshold=deactivation_threshold if is_given(deactivation_threshold) else max(activation_threshold - 0.15, 0.01),
            sample_rate=sample_rate,
        )
        return cls(session=session, opts=opts)

    def stream(self) -> ElderlySileroVADStream:
        """
        Create a new ElderlySileroVADStream for processing audio data.
        """
        stream = ElderlySileroVADStream(
            self,
            self._opts,
            onnx_model.OnnxModel(
                onnx_session=self._onnx_session, sample_rate=self._opts.sample_rate
            ),
        )
        self._streams.add(stream)
        return stream


class ElderlySileroVADStream(silero.VADStream):
    """
    Highly optimized VADStream subclass that handles zero-allocation PCM chunking,
    resampling, and VAD inference evaluation.
    """
    
    def __init__(self, vad: ElderlySileroVAD, opts: _VADOptions, model: onnx_model.OnnxModel) -> None:
        super().__init__(vad, opts, model)
        
        # Pre-allocate frames pool (no allocations on hot path)
        self._pool_size = 32
        self._frame_pool = [
            rtc.AudioFrame(
                data=bytearray(960),  # 30ms of 16kHz mono 16-bit PCM = 960 bytes
                sample_rate=16000,
                num_channels=1,
                samples_per_channel=480
            )
            for _ in range(self._pool_size)
        ]
        self._pool_index = 0

        self._inf_frame_pool = [
            rtc.AudioFrame(
                data=bytearray(1024),  # 32ms (512 samples) of 16kHz mono 16-bit PCM = 1024 bytes
                sample_rate=16000,
                num_channels=1,
                samples_per_channel=512
            )
            for _ in range(self._pool_size)
        ]
        self._inf_pool_index = 0

        # Pre-allocate input byte buffer for chunking
        self._raw_input_buffer = bytearray(65536)
        self._raw_input_len = 0
        
        # Pre-allocate numpy arrays for VAD evaluation
        self._input_buffer = np.zeros(16000 * 10, dtype=np.int16)  # 10s buffer capacity
        self._input_len = 0
        self._inference_f32_data = np.empty(512, dtype=np.float32)

        # Pre-allocated speech buffer for START/END of speech events
        self._max_speech_samples = int(self._opts.max_buffered_speech * 16000)
        self._speech_buffer = np.zeros(
            self._max_speech_samples + int(self._opts.prefix_padding_duration * 16000),
            dtype=np.int16
        )
        self._speech_buffer_len = 0
        self._speech_buffer_max_reached = False
        
        # Resampler for input sample rates other than 16kHz or stereo
        self._resampler = None
        self._last_input_sample_rate = 0
        self._last_input_channels = 0
        
        self._exp_filter = utils.ExpFilter(alpha=0.35)

    def update_options(
        self,
        *,
        min_speech_duration: NotGivenOr[float] = NOT_GIVEN,
        min_silence_duration: NotGivenOr[float] = NOT_GIVEN,
        prefix_padding_duration: NotGivenOr[float] = NOT_GIVEN,
        max_buffered_speech: NotGivenOr[float] = NOT_GIVEN,
        activation_threshold: NotGivenOr[float] = NOT_GIVEN,
        deactivation_threshold: NotGivenOr[float] = NOT_GIVEN,
    ) -> None:
        """
        Dynamically update VAD thresholds and resize speech buffer if necessary.
        """
        super().update_options(
            min_speech_duration=min_speech_duration,
            min_silence_duration=min_silence_duration,
            prefix_padding_duration=prefix_padding_duration,
            max_buffered_speech=max_buffered_speech,
            activation_threshold=activation_threshold,
            deactivation_threshold=deactivation_threshold,
        )

        self._max_speech_samples = int(self._opts.max_buffered_speech * 16000)
        prefix_padding_samples = int(self._opts.prefix_padding_duration * 16000)
        
        new_size = self._max_speech_samples + prefix_padding_samples
        if len(self._speech_buffer) != new_size:
            new_buf = np.zeros(new_size, dtype=np.int16)
            copy_len = min(self._speech_buffer_len, new_size)
            new_buf[:copy_len] = self._speech_buffer[:copy_len]
            self._speech_buffer = new_buf

    @utils.log_exceptions(logger=logger)
    async def _main_task(self) -> None:
        """
        Asynchronous processing loop reading from self._input_ch and feeding VAD.
        Optimized to be allocation-free on the 30ms evaluation path.
        """
        pub_speaking = False
        pub_speech_duration = 0.0
        pub_silence_duration = 0.0
        pub_current_sample = 0
        pub_timestamp = 0.0
        
        speech_threshold_duration = 0.0
        silence_threshold_duration = 0.0
        
        prefix_padding_samples = int(self._opts.prefix_padding_duration * 16000)
        
        async for input_frame in self._input_ch:
            if isinstance(input_frame, self._FlushSentinel):
                # Reset VAD State
                self._model.reset()
                self._exp_filter = utils.ExpFilter(alpha=0.35)
                self._input_len = 0
                self._raw_input_len = 0
                self._speech_buffer.fill(0)
                self._speech_buffer_len = 0
                self._speech_buffer_max_reached = False
                
                pub_speaking = False
                pub_speech_duration = 0.0
                pub_silence_duration = 0.0
                pub_current_sample = 0
                pub_timestamp = 0.0
                speech_threshold_duration = 0.0
                silence_threshold_duration = 0.0
                continue
                
            if not isinstance(input_frame, rtc.AudioFrame):
                continue
                
            # Handle resampling/channel conversion if frame formats mismatch
            if (self._last_input_sample_rate != input_frame.sample_rate or 
                self._last_input_channels != input_frame.num_channels):
                self._last_input_sample_rate = input_frame.sample_rate
                self._last_input_channels = input_frame.num_channels
                if input_frame.sample_rate != 16000 or input_frame.num_channels != 1:
                    self._resampler = rtc.AudioResampler(
                        input_rate=input_frame.sample_rate,
                        output_rate=16000,
                        num_channels=input_frame.num_channels,
                        quality=rtc.AudioResamplerQuality.QUICK
                    )
                else:
                    self._resampler = None
                    
            # 1. Ingest Raw PCM bytes into raw_input_buffer
            frames_to_process = []
            if self._resampler:
                resampled_frames = self._resampler.push(input_frame)
                frames_to_process.extend(resampled_frames)
            else:
                frames_to_process.append(input_frame)
                
            for frame in frames_to_process:
                data_len = len(frame.data)
                if self._raw_input_len + data_len > len(self._raw_input_buffer):
                    new_size = len(self._raw_input_buffer) * 2 + data_len
                    self._raw_input_buffer.extend(bytearray(new_size - len(self._raw_input_buffer)))
                self._raw_input_buffer[self._raw_input_len : self._raw_input_len + data_len] = frame.data
                self._raw_input_len += data_len
                
            # 2. Chunk ingested raw PCM bytes into precise 30ms chunks (960 bytes / 480 samples)
            while self._raw_input_len >= 960:
                chunk_int16 = np.frombuffer(self._raw_input_buffer[:960], dtype=np.int16)
                
                n = len(chunk_int16)
                if self._input_len + n > len(self._input_buffer):
                    new_buf = np.zeros(len(self._input_buffer) * 2 + n, dtype=np.int16)
                    new_buf[:self._input_len] = self._input_buffer[:self._input_len]
                    self._input_buffer = new_buf
                    
                self._input_buffer[self._input_len : self._input_len + n] = chunk_int16
                self._input_len += n
                
                # In-place shift raw_input_buffer (zero-allocation)
                self._raw_input_buffer[: self._raw_input_len - 960] = self._raw_input_buffer[960 : self._raw_input_len]
                self._raw_input_len -= 960
                
                # 3. Evaluate 512-sample inference windows (32ms) using Silero
                while self._input_len >= 512:
                    start_time = time.perf_counter()
                    
                    # Convert to float32 without allocating new array
                    np.divide(self._input_buffer[:512], 32768.0, out=self._inference_f32_data, dtype=np.float32)
                    
                    # Run inference on the actual ONNX session
                    p = await self._loop.run_in_executor(None, self._model, self._inference_f32_data)
                    p = self._exp_filter.apply(exp=1.0, sample=p)
                    
                    window_duration = 512 / 16000  # 32ms
                    pub_current_sample += 512
                    pub_timestamp += window_duration
                    
                    # Accumulate speech frames
                    available_space = len(self._speech_buffer) - self._speech_buffer_len
                    to_copy = min(512, available_space)
                    if to_copy > 0:
                        self._speech_buffer[self._speech_buffer_len : self._speech_buffer_len + to_copy] = self._input_buffer[:to_copy]
                        self._speech_buffer_len += to_copy
                    elif not self._speech_buffer_max_reached:
                        self._speech_buffer_max_reached = True
                        logger.warning("VAD max_buffered_speech reached, discarding extra frames")
                        
                    inference_duration = time.perf_counter() - start_time
                    
                    def _reset_write_cursor() -> None:
                        if self._speech_buffer_len <= prefix_padding_samples:
                            return
                        padding_data = self._speech_buffer[self._speech_buffer_len - prefix_padding_samples : self._speech_buffer_len]
                        self._speech_buffer_max_reached = False
                        self._speech_buffer[:prefix_padding_samples] = padding_data
                        self._speech_buffer_len = prefix_padding_samples
                        
                    def _copy_speech_buffer() -> rtc.AudioFrame:
                        # Copy the data from speech_buffer to an immutable AudioFrame
                        # Exposes the finalized speech chunk to STT/listeners
                        speech_data = self._speech_buffer[:self._speech_buffer_len].tobytes()
                        return rtc.AudioFrame(
                            sample_rate=16000,
                            num_channels=1,
                            samples_per_channel=self._speech_buffer_len,
                            data=speech_data
                        )
                        
                    if pub_speaking:
                        pub_speech_duration += window_duration
                    else:
                        pub_silence_duration += window_duration
                        
                    # Retrieve a frame from the pre-allocated inference frame pool (zero-allocation)
                    inf_frame = self._inf_frame_pool[self._inf_pool_index]
                    self._inf_pool_index = (self._inf_pool_index + 1) % len(self._inf_frame_pool)
                    inf_frame.data[:1024] = memoryview(self._input_buffer[:512]).cast('B')
                    
                    self._event_ch.send_nowait(
                        vad.VADEvent(
                            type=vad.VADEventType.INFERENCE_DONE,
                            samples_index=pub_current_sample,
                            timestamp=pub_timestamp,
                            silence_duration=pub_silence_duration,
                            speech_duration=pub_speech_duration,
                            probability=p,
                            inference_duration=inference_duration,
                            frames=[inf_frame],
                            speaking=pub_speaking,
                            raw_accumulated_silence=silence_threshold_duration,
                            raw_accumulated_speech=speech_threshold_duration
                        )
                    )
                    
                    # VAD Speech and Silence state machine transitions
                    if p >= self._opts.activation_threshold or (
                        pub_speaking and p > self._opts.deactivation_threshold
                    ):
                        speech_threshold_duration += window_duration
                        silence_threshold_duration = 0.0
                        
                        if not pub_speaking:
                            if speech_threshold_duration >= self._opts.min_speech_duration:
                                pub_speaking = True
                                pub_silence_duration = 0.0
                                pub_speech_duration = speech_threshold_duration
                                
                                self._event_ch.send_nowait(
                                    vad.VADEvent(
                                        type=vad.VADEventType.START_OF_SPEECH,
                                        samples_index=pub_current_sample,
                                        timestamp=pub_timestamp,
                                        silence_duration=pub_silence_duration,
                                        speech_duration=pub_speech_duration,
                                        frames=[_copy_speech_buffer()],
                                        speaking=True
                                    )
                                )
                    else:
                        silence_threshold_duration += window_duration
                        speech_threshold_duration = 0.0
                        
                        if not pub_speaking:
                            _reset_write_cursor()
                            
                        if pub_speaking and silence_threshold_duration >= self._opts.min_silence_duration:
                            pub_speaking = False
                            pub_silence_duration = silence_threshold_duration
                            
                            self._event_ch.send_nowait(
                                vad.VADEvent(
                                    type=vad.VADEventType.END_OF_SPEECH,
                                    samples_index=pub_current_sample,
                                    timestamp=pub_timestamp,
                                    silence_duration=pub_silence_duration,
                                    speech_duration=max(0.0, pub_speech_duration - silence_threshold_duration),
                                    frames=[_copy_speech_buffer()],
                                    speaking=False
                                )
                            )
                            pub_speech_duration = 0.0
                            _reset_write_cursor()
                            
                    # In-place shift input_buffer by 512 samples (zero-allocation)
                    self._input_buffer[: self._input_len - 512] = self._input_buffer[512 : self._input_len]
                    self._input_len -= 512
