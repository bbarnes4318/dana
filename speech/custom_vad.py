from __future__ import annotations
import asyncio
import time
import logging
import os
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


async def execute_emergency_flush(session, agent) -> None:
    """
    Perform instant clearing of the outbound WebRTC buffer,
    cancel any active LLM generation/TTS synthesis,
    and truncate the conversation memory log to the spoken words.
    """
    logger.info("Executing emergency track flush and aborting active generation tasks...")
    
    recorder = getattr(agent, "_latency_recorder", None)
    config = getattr(agent, "_config", None)
    record_telemetry = getattr(config, "record_interruption_telemetry", True) if config else True

    # 1. Clear outbound WebRTC playback buffer
    if recorder and record_telemetry:
        recorder.mark("audio_output_flush_requested")

    audio_output = getattr(session, "output", None) and getattr(session.output, "audio", None)
    if audio_output:
        try:
            audio_output.clear_buffer()
            audio_output.flush()
            logger.info("Called clear_buffer() and flush() on session.output.audio")
        except Exception as e:
            logger.error(f"Error calling clear_buffer/flush on audio_output: {e}")
            
    # Also explicitly clear queue on the active audio source
    import legacy.tts_service as tts_service
    if tts_service.active_audio_source:
        try:
            tts_service.active_audio_source.clear_queue()
            logger.info("Cleared tts_service.active_audio_source queue")
        except Exception as e:
            logger.error(f"Error clearing active_audio_source: {e}")
            
    if audio_output and hasattr(audio_output, "_audio_source") and audio_output._audio_source:
        try:
            audio_output._audio_source.clear_queue()
            logger.info("Cleared audio_output._audio_source queue")
        except Exception as e:
            logger.error(f"Error clearing audio_output._audio_source queue: {e}")

    if recorder and record_telemetry:
        recorder.mark("audio_output_flush_completed")

    # 2. Abort TTS synthesis
    if recorder and record_telemetry:
        recorder.mark("tts_cancel_requested")

    if getattr(tts_service, "active_tts_stream", None):
        try:
            await tts_service.active_tts_stream.interrupt()
            logger.info("Interrupted active_tts_stream")
        except Exception as e:
            logger.error(f"Error interrupting active_tts_stream: {e}")

    if recorder and record_telemetry:
        recorder.mark("tts_cancel_completed")

    # 3. Call session.interrupt() to abort LLM and LiveKit speech handles
    if recorder and record_telemetry:
        recorder.mark("session_interrupt_called")

    try:
        if asyncio.iscoroutinefunction(session.interrupt):
            await session.interrupt()
        else:
            session.interrupt()
        logger.info("Called session.interrupt()")
    except Exception as e:
        logger.error(f"Error calling session.interrupt(): {e}")

    if recorder and record_telemetry:
        recorder.mark("session_interrupt_completed")

    # 4. Truncate conversation memory log to the exact word index spoken
    start_time = getattr(agent, "agent_speech_started_time", None)
    interrupted_at = getattr(agent, "interrupted_at", None) or time.perf_counter()
    if start_time is not None:
        dur = max(0.0, interrupted_at - start_time)
        speed = 1.0
        if getattr(tts_service, "active_tts_stream", None) and hasattr(tts_service.active_tts_stream, "_tts"):
            speed = getattr(tts_service.active_tts_stream._tts.config, "speed", 1.0)
        
        words_per_second = 2.5 * speed
        word_index = int(dur * words_per_second)
        
        orig_text = getattr(agent, "current_turn_response", "") or ""
        words = orig_text.split()
        if words and word_index < len(words):
            truncated_text = " ".join(words[:word_index])
            agent.current_turn_response = truncated_text
            logger.info(f"Truncated response text based on {dur:.2f}s playback: '{truncated_text}' (word index={word_index})")
            
            # Update session state turns (in livekit_agent_worker.py context)
            session_state = getattr(session, "session_state", None)
            if session_state:
                turns = session_state.setdefault("turns", [])
                agent_turns = [t for t in turns if t["speaker"] == "agent"]
                if agent_turns:
                    agent_turns[-1]["text"] = truncated_text
                    
            # Update ChatContext history
            for ctx_obj in [getattr(session, "_chat_ctx", None), getattr(agent, "_chat_ctx", None)]:
                if ctx_obj and hasattr(ctx_obj, "messages"):
                    for msg in reversed(ctx_obj.messages):
                        if msg.role == "assistant":
                            if isinstance(msg.content, str):
                                msg.content = truncated_text
                            break
                            
            # Update database repository
            repository = (
                getattr(session, "repository", None) or 
                getattr(agent, "repository", None) or 
                (getattr(agent, "adapter", None) and getattr(agent.adapter, "repository", None))
            )
            call_id = (
                (session_state and session_state.get("call_id")) or 
                (getattr(agent, "adapter", None) and getattr(agent.adapter, "call_id", None))
            )
            
            if repository and call_id:
                turn_number = None
                if session_state:
                    agent_turns = [t for t in session_state["turns"] if t["speaker"] == "agent"]
                    if agent_turns:
                        turn_number = agent_turns[-1].get("turn_number")
                
                if turn_number is None and getattr(agent, "adapter", None) and getattr(agent.adapter, "runtime", None):
                    turn_number = getattr(agent.adapter.runtime.state_machine.call_state, "turn_count", 0) * 2
                    
                if turn_number is not None:
                    try:
                        stage = "OPENING"
                        if session_state:
                            stage = session_state.get("stage", "OPENING")
                        elif getattr(agent, "adapter", None):
                            stage = getattr(agent.adapter.runtime.state_machine.current_stage, "value", "OPENING")
                            
                        await repository.save_call_turn(
                            call_id=call_id,
                            turn_number=turn_number,
                            speaker="agent",
                            text=truncated_text,
                            stage=stage
                        )
                        logger.info(f"Updated database turn {turn_number} with truncated response")
                    except Exception as e:
                        logger.error(f"Failed to update database turn: {e}")


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

    def update_profile(self, profile: Any) -> None:
        """Update VAD options on all active streams using an InterruptionProfile."""
        logger.info(f"Updating VAD streams to profile: {profile.name}")
        if not hasattr(self, "_streams"):
            self._streams = set()
        for stream in list(self._streams):
            try:
                stream.update_options(
                    min_speech_duration=profile.min_speech_duration,
                    min_silence_duration=profile.min_silence_duration,
                    activation_threshold=profile.activation_threshold,
                    deactivation_threshold=profile.deactivation_threshold
                )
                stream._interruption_speech_threshold = profile.interruption_speech_threshold
            except Exception as e:
                logger.error(f"Failed to update VAD stream options: {e}")

    def bind(self, session, agent) -> ElderlySileroVAD:
        """
        Return a copy or wrapper of this VAD bound to a specific session and agent context.
        """
        import copy
        bound = copy.copy(self)
        bound._session = session
        bound._agent = agent
        return bound

    def stream(self, session=None, agent=None) -> ElderlySileroVADStream:
        """
        Create a new ElderlySileroVADStream for processing audio data.
        """
        sess = session or getattr(self, "_session", None)
        agt = agent or getattr(self, "_agent", None)
        stream = ElderlySileroVADStream(
            self,
            self._opts,
            onnx_model.OnnxModel(
                onnx_session=self._onnx_session, sample_rate=self._opts.sample_rate
            ),
            session=sess,
            agent=agt,
        )
        self._streams.add(stream)
        return stream


class ElderlySileroVADStream(silero.VADStream):
    """
    Highly optimized VADStream subclass that handles zero-allocation PCM chunking,
    resampling, and VAD inference evaluation.
    """
    
    def __init__(
        self,
        vad: ElderlySileroVAD,
        opts: _VADOptions,
        model: onnx_model.OnnxModel,
        session=None,
        agent=None
    ) -> None:
        super().__init__(vad, opts, model)
        self._session = session
        self._agent = agent
        
        self._allow_agent_barge_in = os.getenv("DANA_ALLOW_AGENT_BARGE_IN", "false").strip().lower() == "true"
        self._enable_fast_interruption = os.getenv("DANA_ENABLE_FAST_INTERRUPTION", "false").strip().lower() == "true"
        try:
            self._interruption_speech_threshold = float(os.getenv("DANA_INTERRUPTION_SPEECH_THRESHOLD", "0.65"))
        except ValueError:
            self._interruption_speech_threshold = 0.65
        
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
                
            recorder = getattr(self._agent, "_latency_recorder", None)
            if recorder:
                if "inbound_audio_frame_received" not in recorder.events:
                    recorder.mark("inbound_audio_frame_received")
                
                samples = np.frombuffer(input_frame.data, dtype=np.int16)
                if len(samples) > 0:
                    rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2))
                    logger.info(f"VAD INBOUND AUDIO: rms={rms:.2f} samples={len(samples)}")
                    
                    # Print raw data characteristics
                    samples_int16 = np.frombuffer(input_frame.data, dtype=np.int16)
                    samples_float32 = np.frombuffer(input_frame.data, dtype=np.float32)
                    logger.info(f"VAD DEBUG: raw_bytes_len={len(input_frame.data)} "
                                f"int16_min={samples_int16.min() if len(samples_int16) else 0} "
                                f"int16_max={samples_int16.max() if len(samples_int16) else 0} "
                                f"float32_min={samples_float32.min() if len(samples_float32) else 0} "
                                f"float32_max={samples_float32.max() if len(samples_float32) else 0} "
                                f"int16_head={list(samples_int16[:10])}")
                                
                    if rms > 0.0 and "inbound_audio_rms_nonzero" not in recorder.events:
                        recorder.mark("inbound_audio_rms_nonzero")
                
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
                    logger.info(f"VAD INFERENCE: p={p:.4f}")
                    
                    recorder = getattr(self._agent, "_latency_recorder", None)
                    if recorder and "vad_inference_done" not in recorder.events:
                        recorder.mark("vad_inference_done")
                    
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
                        
                    # Instantiated as immutable AudioFrame to comply with LiveKit API
                    inf_frame = rtc.AudioFrame(
                        sample_rate=16000,
                        num_channels=1,
                        samples_per_channel=512,
                        data=self._input_buffer[:512].tobytes()
                    )
                    
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
                                
                                # Notify active STT stream for this call
                                recorder = getattr(self._agent, "_latency_recorder", None)
                                if recorder and "vad_start_of_speech" not in recorder.events:
                                    recorder.mark("vad_start_of_speech")

                                stt_stream = None
                                if self._session and hasattr(self._session, "_active_stt_stream"):
                                    stt_stream = self._session._active_stt_stream
                                if stt_stream and hasattr(stt_stream, "active_stream"):
                                    stt_stream = stt_stream.active_stream
                                    
                                if not stt_stream:
                                    call_id = None
                                    if self._session:
                                        if hasattr(self._session, "session_state") and self._session.session_state:
                                            call_id = self._session.session_state.get("call_id")
                                    if not call_id:
                                        from speech.context_registry import get_current_call_id
                                        call_id = get_current_call_id() or "default"
                                    
                                    from legacy.stt_service import LocallyHostedSTT
                                    stt_stream = LocallyHostedSTT._active_streams.get(call_id)
                                
                                if stt_stream:
                                    logger.info(f"VAD notifying active STT stream of speech start: {stt_stream}")
                                    stt_stream.on_speech_start()
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
                            
                            # Notify active STT stream for this call
                            recorder = getattr(self._agent, "_latency_recorder", None)
                            if recorder and "vad_end_of_speech" not in recorder.events:
                                recorder.mark("vad_end_of_speech")

                            stt_stream = None
                            if self._session and hasattr(self._session, "_active_stt_stream"):
                                stt_stream = self._session._active_stt_stream
                            if stt_stream and hasattr(stt_stream, "active_stream"):
                                stt_stream = stt_stream.active_stream
                                
                            if not stt_stream:
                                call_id = None
                                if self._session:
                                    if hasattr(self._session, "session_state") and self._session.session_state:
                                        call_id = self._session.session_state.get("call_id")
                                if not call_id:
                                    from speech.context_registry import get_current_call_id
                                    call_id = get_current_call_id() or "default"
                                
                                from legacy.stt_service import LocallyHostedSTT
                                stt_stream = LocallyHostedSTT._active_streams.get(call_id)
                                
                            if stt_stream:
                                logger.info(f"VAD notifying active STT stream of speech end: {stt_stream}")
                                stt_stream.on_speech_end()
                            
                    # Interruption check
                    if self._session:
                        agent_state = getattr(self._session, "agent_state", None)
                        agent_speaking = agent_state == "speaking" or getattr(agent_state, "value", None) == "speaking"
                        if agent_speaking:
                            if self._allow_agent_barge_in and self._enable_fast_interruption:
                                speech_duration = pub_speech_duration if pub_speaking else speech_threshold_duration
                                threshold = self._interruption_speech_threshold
                                if speech_duration >= threshold and p >= self._opts.activation_threshold:
                                    agent = self._agent
                                    if agent and not getattr(agent, "interrupted_current_turn", False):
                                        agent.interrupted_current_turn = True
                                        agent.interrupted_at = time.perf_counter()
                                        asyncio.create_task(execute_emergency_flush(self._session, agent))

                    # In-place shift input_buffer by 512 samples (zero-allocation)
                    self._input_buffer[: self._input_len - 512] = self._input_buffer[512 : self._input_len]
                    self._input_len -= 512
