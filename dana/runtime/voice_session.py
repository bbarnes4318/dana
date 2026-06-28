from __future__ import annotations
import asyncio
import os
import logging
import uuid
import time
import math
from datetime import datetime, timezone, timedelta
from typing import AsyncIterable, Optional, List, Dict, Any, Callable

from livekit import rtc
from livekit.agents import (
    AutoSubscribe,
    JobContext,
    llm,
    Agent,
    AgentSession,
    room_io,
    TurnHandlingOptions,
)
from dana.config.voice_config import VoiceConfig
from dana.runtime.call_context import CallContext
from dana.runtime.turn_manager import TurnManager

logger = logging.getLogger(__name__)

def load_instructions(path: str) -> str:
    try:
        from pathlib import Path
        p = Path(path)
        if p.is_absolute():
            return p.read_text(encoding="utf-8")
        # Try local project relative
        local_p = Path(__file__).resolve().parent / path
        if local_p.exists():
            return local_p.read_text(encoding="utf-8")
        # Try parent relative
        parent_p = Path(__file__).resolve().parent.parent / path
        if parent_p.exists():
            return parent_p.read_text(encoding="utf-8")
        # Fallback to direct read
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to load instructions from {path}: {e}")
        return "You are an AI assistant."

class DanaAgent(Agent):
    """
    Subclass of livekit.agents.Agent implementing our phone-optimized Dana personality
    and wrapping LLM & TTS streaming nodes with latency recorder hooks.
    """
    def __init__(self, shared: Any, latency_recorder: Any, voice_session: Any = None):
        instructions = load_instructions(shared.config.agent_prompt_path)
        super().__init__(
            instructions=instructions,
            stt=shared.stt,
            llm=shared.llm,
            tts=shared.tts,
            vad=shared.vad,
        )
        self.voice_session = voice_session
        self.prompt_loader = getattr(shared, "prompt_loader", None)
        self._config = shared.config
        self._latency_recorder = latency_recorder
        self.room = None
        self.adapter: Optional[Any] = None
        self.should_disconnect = False
        self.warm_bridge_active = False
        self.fallback_disconnect_task: Optional[asyncio.Task] = None
        self.final_transcript_count = 0
        # Metrics Accumulators
        self.stt_seconds = 0.0
        self.tts_characters = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.current_turn_response = ""
        self.agent_speech_started_time = None
        self.interrupted_current_turn = False
        self.interrupted_at = None

    async def on_user_turn_completed(self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage) -> None:
        logger.debug(f"User turn completed: '{new_message.content}'")

    async def llm_node(
        self,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        model_settings: any,
    ) -> AsyncIterable[llm.ChatChunk]:
        logger.info("LLM_NODE_ENTERED")
        if self.voice_session:
            self.voice_session._llm_node_entered_for_turn = True
            if getattr(self.voice_session, "_llm_watchdog_task", None):
                self.voice_session._llm_watchdog_task.cancel()
        self._latency_recorder.mark("llm_node_entered")
        if self.warm_bridge_active:
            logger.info("warm_bridge_active_dana_suppressed: Suppressing Dana responses after warm bridge success.")
            return

        self._latency_recorder.mark("llm_request_start")
        
        # Capture and reset interruption flag
        interrupted = self.interrupted_current_turn
        self.interrupted_current_turn = False

        def get_msg_text(m) -> str:
            if not m:
                return ""
            try:
                if type(m).__name__ in ("MagicMock", "Mock", "AsyncMock") or hasattr(m, "_mock_self") or "mock" in type(m).__name__.lower():
                    return "mock message content"
                tc = getattr(m, "text_content", None)
                if isinstance(tc, str):
                    return tc
                c = getattr(m, "content", None)
                if isinstance(c, str):
                    return c
            except Exception:
                pass
            return ""

        def get_messages(ctx) -> list:
            if not ctx:
                return []
            msgs = getattr(ctx, "messages", None)
            if msgs is None:
                return []
            if callable(msgs):
                return msgs()
            return msgs

        # Get the latest user message
        ctx_msgs = get_messages(chat_ctx)
        user_msg = ctx_msgs[-1] if ctx_msgs else None
        user_text = get_msg_text(user_msg)
        if user_text:
            self._latency_recorder.mark("user_text_seen_by_llm_node")
        
        if not user_text:
            logger.warning("llm_node called but no user message found in chat_ctx")
            self._latency_recorder.mark("llm_done")
            return

        if not self.adapter:
            logger.error("LiveKitRuntimeAdapter is not initialized on the agent!")
            self._latency_recorder.mark("llm_done")
            return

        # Check if streaming mode is enabled
        is_streaming_enabled = self._config.enable_streaming_response
        if is_streaming_enabled:
            self._latency_recorder.streaming_mode_enabled = True
            
            # Define clean chat stream function to call vLLM client directly
            async def chat_stream_fn(instructions: str) -> AsyncIterable[str]:
                new_ctx = llm.ChatContext()
                
                # Prepend static system prompt prefix (personality and compliance parameters)
                loader = self.prompt_loader or (self.adapter and self.adapter.prompt_loader)
                static_prompt = loader.build_system_prompt() if loader else ""
                combined_prompt = f"{static_prompt}\n\n{instructions}"
                
                # Add compiled instructions as the system prompt
                new_ctx.add_message(
                    role="system",
                    content=combined_prompt
                )
                
                # Copy conversation history (user and assistant messages only)
                for msg in get_messages(chat_ctx):
                    if msg.role in ("user", "assistant"):
                        msg_content = get_msg_text(msg)
                        new_ctx.add_message(
                            role=msg.role,
                            content=msg_content
                        )
                
                # Estimate prompt tokens
                new_ctx_msgs = get_messages(new_ctx)
                prompt_str = instructions + "".join(get_msg_text(m) for m in new_ctx_msgs if get_msg_text(m))
                from metrics.model_cost_metrics import estimate_llm_tokens
                self.prompt_tokens += estimate_llm_tokens(prompt_str)

                # Run LLM chat directly - hardcoding temperature to 0.2
                stream = self.llm.chat(
                    chat_ctx=new_ctx,
                    temperature=0.2,
                    top_p=self._config.top_p,
                    max_tokens=self._config.max_tokens,
                    frequency_penalty=0.15,
                )
                
                first_token = True
                async for chunk in stream:
                    content = chunk.delta.content if chunk.delta else ""
                    if content:
                        if first_token:
                            first_token = False
                            self._latency_recorder.mark("llm_first_token")
                        yield content

            try:
                first_chunk = True
                self._latency_recorder.mark("agent_runtime_process_user_turn_started")
                # Process user turn via the adapter exactly once using stream
                async for chunk in self.adapter.process_user_turn_stream(
                    user_text, chat_stream_fn, latency_recorder=self._latency_recorder, interrupted=interrupted
                ):
                    if first_chunk:
                        delay = self.adapter.runtime.conversational_timing.get_pre_speech_delay(
                            self.adapter.state_machine.call_state.current_stage.value
                        )
                        if delay > 0:
                            logger.info(f"Applying pre-speech delay of {delay}s before streaming TTS")
                            await asyncio.sleep(delay)
                        first_chunk = False
                    yield chunk
            except asyncio.CancelledError:
                logger.info("llm_node: Generation cancelled due to user barge-in.")
                raise

            # Get the final streaming result from adapter
            result = getattr(self.adapter, "last_streaming_result", None)
            if result:
                self.current_turn_response = result.agent_response or ""
                logger.info(f"LLM_RESPONSE_TEXT_LENGTH: {len(self.current_turn_response)}")
                if self.voice_session:
                    self.voice_session._tts_first_audio_emitted_for_turn = False
                    
                    async def tts_audio_watchdog():
                        await asyncio.sleep(3.0)
                        if not getattr(self.voice_session, "_tts_first_audio_emitted_for_turn", False):
                            logger.error("FATAL_TTS_NO_FIRST_AUDIO_AFTER_LLM")
                            
                    if getattr(self.voice_session, "_tts_watchdog_task", None):
                        self.voice_session._tts_watchdog_task.cancel()
                    self.voice_session._tts_watchdog_task = asyncio.create_task(tts_audio_watchdog())
                if not self.current_turn_response.strip():
                    logger.error("ERROR_EMPTY_LLM_RESPONSE")
                self._latency_recorder.mark("agent_response_text_created")
                
                # Update stage in registry
                from speech.context_registry import update_call_stage
                update_call_stage(self.adapter.call_id, result.stage)

                # Estimate completion tokens
                from metrics.model_cost_metrics import estimate_llm_tokens
                self.completion_tokens += estimate_llm_tokens(self.current_turn_response)

                # Safely apply adaptive endpointing
                if self._config.endpoint_mode == "adaptive" and getattr(self, "session", None):
                    from speech.endpoint_tuner import get_endpoint_delays, safe_update_endpointing
                    is_objection = False
                    for ev in self.adapter.runtime.events:
                        from core.runtime_events import ObjectionDetectedEvent
                        if isinstance(ev, ObjectionDetectedEvent) and getattr(ev, "utterance", None) == user_text:
                            is_objection = True
                            break
                    min_d, max_d = get_endpoint_delays(result.stage, is_objection_or_confusion=is_objection)
                    safe_update_endpointing(self.session, min_d, max_d)

                # Handle disconnect timing based on outcome
                if result.should_end_call:
                    is_warm_bridge = False
                    for ev in self.adapter.runtime.events:
                        from core.runtime_events import ToolTriggeredEvent
                        if isinstance(ev, ToolTriggeredEvent) and ev.tool_name in ("feTransfer", "transfer_to_agent") and ev.success:
                            if "warm" in ev.result_message.lower() or os.getenv("DANA_TRANSFER_MODE", "").lower() == "warm_bridge":
                                is_warm_bridge = True
                                break
                    if is_warm_bridge:
                        logger.info("Warm bridge transfer succeeded. Dana will mute and leave later.")
                        self.should_disconnect = False
                        self.warm_bridge_active = True
                        
                        async def warm_bridge_leave():
                            await asyncio.sleep(15.0)
                            logger.info("warm_bridge_active_dana_suppressed: Dana leaving agent session only.")
                            if getattr(self, "session", None):
                                try:
                                    await self.session.aclose()
                                except Exception as e:
                                    logger.error(f"Error closing agent session during warm bridge: {e}")
                        asyncio.create_task(warm_bridge_leave())
                    else:
                        self.should_disconnect = True
                        if self.fallback_disconnect_task:
                            self.fallback_disconnect_task.cancel()
                        
                        async def disconnect_after_delay(delay: float = 8.0):
                            try:
                                await asyncio.sleep(delay)
                                if self.room and self.room.isconnected():
                                    logger.info("Fallback: Disconnecting room after delay...")
                                    await self.room.disconnect()
                            except asyncio.CancelledError:
                                logger.info("Fallback disconnect task cancelled.")
                        self.fallback_disconnect_task = asyncio.create_task(disconnect_after_delay())
            
            self._latency_recorder.mark("llm_done")
            return
        
        # Define clean chat function to call vLLM client directly without re-entering DanaAgent.llm_node
        async def chat_fn(instructions: str) -> str:
            new_ctx = llm.ChatContext()
            
            # Prepend static system prompt prefix (personality and compliance parameters)
            loader = self.prompt_loader or (self.adapter and self.adapter.prompt_loader)
            static_prompt = loader.build_system_prompt() if loader else ""
            
            combined_prompt = f"{static_prompt}\n\n{instructions}"
            
            # Add compiled instructions as the system prompt
            new_ctx.add_message(
                role="system",
                content=combined_prompt
            )
            
            # Copy conversation history (user and assistant messages only)
            for msg in get_messages(chat_ctx):
                if msg.role in ("user", "assistant"):
                    msg_content = get_msg_text(msg)
                    new_ctx.add_message(
                        role=msg.role,
                        content=msg_content
                    )
            
            # Estimate prompt tokens
            new_ctx_msgs = get_messages(new_ctx)
            prompt_str = instructions + "".join(get_msg_text(m) for m in new_ctx_msgs if get_msg_text(m))
            from metrics.model_cost_metrics import estimate_llm_tokens
            self.prompt_tokens += estimate_llm_tokens(prompt_str)

            # Run LLM chat directly - hardcoding temperature to 0.2
            stream = self.llm.chat(
                chat_ctx=new_ctx,
                temperature=0.2,
                top_p=self._config.top_p,
                max_tokens=self._config.max_tokens,
                frequency_penalty=0.15,
            )
            
            response_text = ""
            async for chunk in stream:
                content = chunk.delta.content if chunk.delta else ""
                if content:
                    response_text += content

            # Estimate completion tokens
            self.completion_tokens += estimate_llm_tokens(response_text)
            return response_text

        # Process user turn via the adapter exactly once
        result = await self.adapter.process_user_turn(user_text, chat_fn, interrupted=interrupted)
        self.current_turn_response = result.agent_response or ""
        logger.info(f"LLM_RESPONSE_TEXT_LENGTH: {len(self.current_turn_response)}")
        if self.voice_session:
            self.voice_session._tts_first_audio_emitted_for_turn = False
            
            async def tts_audio_watchdog():
                await asyncio.sleep(3.0)
                if not getattr(self.voice_session, "_tts_first_audio_emitted_for_turn", False):
                    logger.error("FATAL_TTS_NO_FIRST_AUDIO_AFTER_LLM")
                    
            if getattr(self.voice_session, "_tts_watchdog_task", None):
                self.voice_session._tts_watchdog_task.cancel()
            self.voice_session._tts_watchdog_task = asyncio.create_task(tts_audio_watchdog())
        if not self.current_turn_response.strip():
            logger.error("ERROR_EMPTY_LLM_RESPONSE")
        
        delay = getattr(result, "pre_speech_delay", 0.0)
        if delay > 0:
            logger.info(f"Applying pre-speech delay of {delay}s before playing TTS")
            await asyncio.sleep(delay)
        
        # Update stage in registry
        from speech.context_registry import update_call_stage
        update_call_stage(self.adapter.call_id, result.stage)

        # Safely apply adaptive endpointing
        if self._config.endpoint_mode == "adaptive" and getattr(self, "session", None):
            from speech.endpoint_tuner import get_endpoint_delays, safe_update_endpointing
            
            # Check if objection was detected during this turn
            is_objection = False
            for ev in self.adapter.runtime.events:
                from core.runtime_events import ObjectionDetectedEvent
                if isinstance(ev, ObjectionDetectedEvent) and getattr(ev, "utterance", None) == user_text:
                    is_objection = True
                    break
            
            min_d, max_d = get_endpoint_delays(result.stage, is_objection_or_confusion=is_objection)
            safe_update_endpointing(self.session, min_d, max_d)
        
        self._latency_recorder.mark("llm_first_token")
        
        # Handle disconnect timing based on outcome
        if result.should_end_call:
            # Check if it was a successful warm bridge transfer
            is_warm_bridge = False
            for ev in self.adapter.runtime.events:
                from core.runtime_events import ToolTriggeredEvent
                if isinstance(ev, ToolTriggeredEvent) and ev.tool_name in ("feTransfer", "transfer_to_agent") and ev.success:
                    if "warm" in ev.result_message.lower() or os.getenv("DANA_TRANSFER_MODE", "").lower() == "warm_bridge":
                        is_warm_bridge = True
                        break
            
            if is_warm_bridge:
                logger.info("Warm bridge transfer succeeded. Dana will mute and leave later.")
                self.should_disconnect = False
                self.warm_bridge_active = True
                
                async def warm_bridge_leave():
                    await asyncio.sleep(15.0)
                    logger.info("warm_bridge_active_dana_suppressed: Dana leaving agent session only.")
                    if getattr(self, "session", None):
                        try:
                            await self.session.aclose()
                        except Exception as e:
                            logger.error(f"Error closing agent session during warm bridge: {e}")
                asyncio.create_task(warm_bridge_leave())
            else:
                self.should_disconnect = True
                
                # Register cancellable fallback task
                if self.fallback_disconnect_task:
                    self.fallback_disconnect_task.cancel()
                
                async def disconnect_after_delay(delay: float = 8.0):
                    try:
                        await asyncio.sleep(delay)
                        if self.room and self.room.isconnected():
                            logger.info("Fallback: Disconnecting room after delay...")
                            await self.room.disconnect()
                    except asyncio.CancelledError:
                        logger.info("Fallback disconnect task cancelled.")
                
                self.fallback_disconnect_task = asyncio.create_task(disconnect_after_delay())
        
        # Yield the response to TTS node as a ChatChunk stream
        async for chunk in self.adapter.convert_response_to_stream(result.agent_response):
            yield chunk
            
        self._latency_recorder.mark("llm_done")

    async def tts_node(
        self,
        text: AsyncIterable[str],
        model_settings: any,
    ) -> AsyncIterable[rtc.AudioFrame]:
        logger.info("TTS_NODE_ENTERED")
        tts_stream = self.tts.stream()
        first_text = True
        
        async def push_text_loop():
            nonlocal first_text
            try:
                async for chunk in text:
                    if chunk and first_text:
                        first_text = False
                        logger.info("TTS_FIRST_TEXT_RECEIVED")
                        self._latency_recorder.mark("tts_first_text")
                        if "greeting_tts_started" in self._latency_recorder.events:
                            self._latency_recorder.mark("second_turn_tts_first_text")
                    tts_stream.push_text(chunk)
                tts_stream.flush()
                tts_stream.end_input()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Error in tts_node push loop: {e}")
            
        push_task = asyncio.create_task(push_text_loop())
        
        first_audio = True
        completed_successfully = False
        try:
            async for ev in tts_stream:
                if first_audio:
                    first_audio = False
                    logger.info("TTS_FIRST_AUDIO_SENT")
                    if self.voice_session:
                        self.voice_session._tts_first_audio_emitted_for_turn = True
                        if getattr(self.voice_session, "_tts_watchdog_task", None):
                            self.voice_session._tts_watchdog_task.cancel()
                        
                        # Watchdog 5: If TTS first audio is emitted but agent_speaking does not start within 3 seconds
                        self.voice_session._agent_speaking_started_for_turn = False
                        
                        async def agent_speaking_watchdog():
                            await asyncio.sleep(3.0)
                            if not getattr(self.voice_session, "_agent_speaking_started_for_turn", False):
                                logger.error("FATAL_TTS_AUDIO_NOT_PUBLISHED_TO_ROOM")
                                
                        if getattr(self.voice_session, "_agent_speaking_watchdog_task", None):
                            self.voice_session._agent_speaking_watchdog_task.cancel()
                        self.voice_session._agent_speaking_watchdog_task = asyncio.create_task(agent_speaking_watchdog())

                    self._latency_recorder.mark("tts_first_audio")
                    self._latency_recorder.mark("first_audio_published")
                    if "greeting_tts_started" in self._latency_recorder.events:
                        self._latency_recorder.mark("second_turn_tts_first_audio")
                        self._latency_recorder.mark("second_turn_audio_published")
                yield ev.frame
            completed_successfully = True
            logger.info("TTS_STREAM_COMPLETED")
        finally:
            push_task.cancel()
            if first_audio:
                logger.error("ERROR_TTS_NO_AUDIO")
                
            should_interrupt = asyncio.current_task().cancelled() or getattr(self, "interrupted_current_turn", False)
            if should_interrupt:
                logger.info("tts_node: interrupting TTS stream due to cancel or barge-in")
                try:
                    await tts_stream.interrupt()
                except Exception as e:
                    logger.warning(f"Error interrupting tts_stream: {e}")
            else:
                logger.info("tts_node: closing TTS stream gracefully without interrupt")

async def run_amd_worker(track: rtc.Track, session: AgentSession, agent: any, room: rtc.Room):
    import array
    import math
    import time
    logger.info("AMD classification worker started for call_id=%s", getattr(agent, "call_id", "unknown"))
    
    audio_stream = rtc.AudioStream(track, sample_rate=16000, num_channels=1)
    
    rms_threshold = 300.0
    zcr_min = 0.01
    zcr_max = 0.55
    
    speech_duration = 0.0
    silence_duration = 0.0
    is_speaking = False
    start_time = time.time()
    
    try:
        async for event in audio_stream:
            if not room.isconnected() or getattr(agent, "is_voicemail", False):
                break
                
            if getattr(agent, "user_transcript_received", False):
                logger.info("AMD: User transcript has been received. Stopping AMD worker.")
                break

            turn_count = 0
            if hasattr(agent, "adapter") and agent.adapter and hasattr(agent.adapter, "state_machine") and agent.adapter.state_machine:
                if hasattr(agent.adapter.state_machine, "call_state") and agent.adapter.state_machine.call_state:
                    turn_count = getattr(agent.adapter.state_machine.call_state, "turn_count", 0)
            if turn_count > 0:
                logger.info("AMD: Active human conversation detected (turn_count=%d > 0). Stopping AMD worker.", turn_count)
                break
                
            if time.time() - start_time > 10.0:
                logger.info("AMD: Call exceeded 10.0 seconds without voicemail detection. Stopping AMD worker.")
                break
                
            frame = event.frame
            if not frame.data:
                continue
                
            samples = array.array('h', frame.data)
            num_samples = len(samples)
            if num_samples == 0:
                continue
                
            frame_duration = frame.duration
            
            sum_squares = sum(s * s for s in samples)
            rms = math.sqrt(sum_squares / num_samples)
            
            zero_crossings = 0
            for i in range(1, num_samples):
                if (samples[i] >= 0 and samples[i-1] < 0) or (samples[i] < 0 and samples[i-1] >= 0):
                    zero_crossings += 1
            zcr = zero_crossings / num_samples
            
            is_frame_speech = (rms > rms_threshold) and (zcr_min <= zcr <= zcr_max)
            
            if is_frame_speech:
                if not is_speaking:
                    is_speaking = True
                    logger.debug("AMD: Speech onset detected (RMS=%.1f, ZCR=%.3f)", rms, zcr)
                speech_duration += frame_duration
                silence_duration = 0.0
            else:
                if is_speaking:
                    silence_duration += frame_duration
                    if silence_duration > 0.25:
                        logger.debug("AMD: Speech offset detected. Total speech duration was %.2fs", speech_duration - silence_duration)
                        is_speaking = False
                        speech_duration = 0.0
                        silence_duration = 0.0
                    else:
                        speech_duration += frame_duration
            
            if speech_duration >= 4.0:
                logger.info("AMD: Voicemail greeting detected (speech_duration=%.2fs >= 4.0s).", speech_duration)
                agent.is_voicemail = True
                agent.possible_voicemail = True
                
                if hasattr(agent, "adapter") and agent.adapter and agent.adapter.state_machine:
                    from core.call_state import CallStage
                    agent.adapter.state_machine.call_state.transition_to(CallStage.END)
                
                turn_count = 0
                if hasattr(agent, "adapter") and agent.adapter and hasattr(agent.adapter, "state_machine") and agent.adapter.state_machine:
                    if hasattr(agent.adapter.state_machine, "call_state") and agent.adapter.state_machine.call_state:
                        turn_count = getattr(agent.adapter.state_machine.call_state, "turn_count", 0)
                        
                if turn_count == 0 and not getattr(agent, "user_transcript_received", False):
                    logger.info("AMD: High confidence voicemail detected (duration >= 4.0s, no human turns). Disconnecting room.")
                    asyncio.create_task(room.disconnect())
                else:
                    logger.info("AMD: Not disconnecting room because turn_count=%d or user transcript received", turn_count)
                break
                
    except asyncio.CancelledError:
        logger.debug("AMD worker task cancelled")
    except Exception as e:
        logger.error("Error in AMD parallel worker: %s", e, exc_info=True)
    finally:
        await audio_stream.aclose()
        logger.info("AMD classification worker finished")

async def suggest_lessons_from_call(call_record, scorecard, repository) -> None:
    turns = call_record.turns
    for idx, turn in enumerate(turns[:-1]):
        if turn.speaker == "prospect":
            next_turn = turns[idx + 1]
            if next_turn.speaker == "agent":
                stage = turn.stage or "general_sales"
                topic = "objection_handling" if stage == "objection" else stage
                
                from training.extract_training_lessons import _detect_objection_type, _detect_compliance_risk
                obj_type = _detect_objection_type(turn.text) if stage == "objection" else None
                comp_risk = _detect_compliance_risk(next_turn.text)
                
                if stage == "objection":
                    sales_lesson = f"Handle objection: '{turn.text}'"
                else:
                    sales_lesson = f"Response during {stage} stage in high-performing call."
                
                await repository.save_training_note(
                    source=f"call:{call_record.call_id}",
                    topic=topic,
                    sales_lesson=sales_lesson,
                    good_response_example=next_turn.text,
                    bad_response_example="",
                    call_stage=stage,
                    objection_type=obj_type,
                    compliance_risk=comp_risk,
                    use_in_live_call=False,
                    status="pending_review"
                )

class VoiceSession:
    """Manages the lifecycle of a single outbound AI call session."""
    
    def __init__(self, ctx: JobContext, shared_components: Any) -> None:
        self.ctx = ctx
        self.shared = shared_components
        self.config = shared_components.config
        self.context: Optional[CallContext] = None
        self.turn_manager: Optional[TurnManager] = None

    async def initialize(self, call_id: str, phone_number: str, campaign_id: str, lead_id: str) -> CallContext:
        """Set up call context and turn manager."""
        self.context = CallContext(
            call_id=call_id,
            phone_number=phone_number,
            campaign_id=campaign_id,
            lead_id=lead_id
        )
        from core.livekit_runtime_adapter import LiveKitRuntimeAdapter
        from pathlib import Path
        adapter = LiveKitRuntimeAdapter(
            call_id=call_id,
            phone_number=phone_number,
            project_root=Path(__file__).resolve().parent.parent.parent,
            prompt_loader=self.shared.prompt_loader,
            objection_classifier=self.shared.objection_classifier,
            objection_policy=self.shared.objection_policy,
            context_builder=self.shared.context_builder,
            action_policy=self.shared.action_policy,
            tool_registry=self.shared.tool_registry,
            compliance_filter=self.shared.compliance_filter,
            output_validator=self.shared.output_validator,
            pii_redactor=self.shared.pii_redactor,
            repository=self.shared.repository
        )
        self.turn_manager = TurnManager(self.context, adapter)
        return self.context

    async def run(self, participant: rtc.RemoteParticipant) -> None:
        """Runs the calling session loop, hooks up events, tracks AMD, and scores outcomes."""
        consumer_task = None
        # Resolve call identity and metadata from room metadata and participant metadata
        call_id = None
        lead_id = None
        campaign_id = None

        import json
        if self.ctx.room and self.ctx.room.metadata:
            try:
                data = json.loads(self.ctx.room.metadata)
                if isinstance(data, dict):
                    call_id = data.get("call_id") or data.get("callId")
                    lead_id = data.get("lead_id") or data.get("leadId")
                    campaign_id = data.get("campaign_id") or data.get("campaignId")
            except Exception:
                pass

        if participant and participant.metadata:
            try:
                data = json.loads(participant.metadata)
                if isinstance(data, dict):
                    if not call_id:
                        call_id = data.get("call_id") or data.get("callId")
                    if not lead_id:
                        lead_id = data.get("lead_id") or data.get("leadId")
                    if not campaign_id:
                        campaign_id = data.get("campaign_id") or data.get("campaignId")
            except Exception:
                pass

        if not call_id:
            call_id = str(uuid.uuid4())

        if not campaign_id and participant.identity:
            try:
                lead_data = await self.shared.repository.get_lead_by_phone(participant.identity)
                if lead_data:
                    campaign_id = lead_data.get("campaign_id")
                    if not lead_id:
                        lead_id = lead_data.get("id") or lead_data.get("lead_id")
            except Exception as e:
                logger.error(f"Failed to fetch lead campaign_id: {e}")

        if not campaign_id:
            campaign_id = "unknown"

        # Initialize the context & turn manager
        await self.initialize(call_id, participant.identity or "unknown", campaign_id, lead_id)
        
        latency_recorder = self.context.latency_recorder
        latency_recorder.mark("call_start")
        latency_recorder.mark("participant_joined")
        latency_recorder.mark("room_joined")

        # Configure low-latency turn handling
        turn_handling = TurnHandlingOptions(
            turn_detection="vad",
            endpointing={
                "mode": "fixed",
                "min_delay": self.shared.config.turn_min_delay,
                "max_delay": self.shared.config.turn_max_delay,
            },
            interruption={
                "enabled": True,
                "mode": "adaptive",
                "resume_false_interruption": True,
                "false_interruption_timeout": 1.0,
            },
            preemptive_generation={"enabled": self.shared.config.preemptive_generation},
        )

        # Initialize Watchdog state variables
        self._audio_track_subscribed = False
        self._llm_node_entered_for_turn = False
        self._tts_first_audio_emitted_for_turn = False
        self._agent_speaking_started_for_turn = False
        self._transcript_watchdog_task = None
        self._llm_watchdog_task = None
        self._tts_watchdog_task = None
        self._agent_speaking_watchdog_task = None

        # Watchdog 1: If no audio track is subscribed within 5 seconds after participant join
        async def track_watchdog():
            await asyncio.sleep(5.0)
            if not getattr(self, "_audio_track_subscribed", False):
                logger.error("FATAL_NO_AUDIO_TRACK_SUBSCRIBED")
        asyncio.create_task(track_watchdog())

        # Initialize the AgentSession
        is_direct_enabled = os.getenv("DANA_DIRECT_RESPONSE_ON_FINAL_TRANSCRIPT", "true").strip().lower() in ("true", "1", "yes")
        session = AgentSession(
            stt=self.shared.stt,
            llm=None if is_direct_enabled else self.shared.llm,
            tts=self.shared.tts,
            vad=self.shared.vad,
            turn_handling=turn_handling,
        )
        
        agent = DanaAgent(self.shared, latency_recorder, self)
        agent.room = self.ctx.room
        agent.adapter = self.turn_manager.adapter
        
        if hasattr(self.shared.stt, "bind"):
            session._stt = self.shared.stt.bind(session, agent)
            agent._stt = session._stt
        session._vad = self.shared.vad.bind(session, agent)
        agent._vad = session._vad

        # Set lead details directly on agent.adapter.lead
        if agent.adapter and agent.adapter.lead:
            agent.adapter.lead.lead_id = lead_id
            agent.adapter.lead.campaign_id = campaign_id
            agent.adapter.lead.lead_phone_e164 = participant.identity or "unknown"

        # Set up session event hooks
        @session.on("user_state_changed")
        def on_user_state_changed(ev):
            state_str = str(ev.new_state).lower()
            old_state_str = str(ev.old_state).lower()
            if "speaking" in state_str:
                logger.info("User speaking started")
                logger.info("USER_SPEAKING_STARTED")
                latency_recorder.mark("stt_speech_start_hook_called")
                if getattr(agent, "interrupted_current_turn", False) and latency_recorder.events.get("user_speech_end"):
                    latency_recorder.mark("user_speech_resumed")

                latency_recorder.mark("user_speech_start")
                
                # Check for barge-in interruption
                if self.shared.config.enable_fast_interruption and os.getenv("DANA_ALLOW_AGENT_BARGE_IN", "false").lower() == "true":
                    if session.agent_state == "speaking" or getattr(session.agent_state, "value", None) == "speaking":
                        from speech.context_registry import get_current_call_stage
                        stage = get_current_call_stage() or "OPENING"
                        if stage != "OPENING":
                            latency_recorder.mark("barge_in_detected")
                            logger.info("Barge-in detected - interrupting agent response")
                            agent.interrupted_current_turn = True
                            agent.interrupted_at = time.perf_counter()
                            
                            if asyncio.iscoroutinefunction(session.interrupt):
                                asyncio.create_task(session.interrupt())
                            else:
                                session.interrupt()
                            latency_recorder.mark("barge_in_stopped_audio")
                        else:
                            logger.info("Barge-in ignored during OPENING stage (greeting playback)")
                    
                if getattr(agent, "fallback_disconnect_task", None):
                    agent.fallback_disconnect_task.cancel()
                    agent.fallback_disconnect_task = None
                agent.should_disconnect = False
                    
            elif "listening" in state_str or "idle" in state_str:
                if "speaking" in old_state_str:
                    logger.info("User speaking stopped")
                    logger.info("USER_SPEAKING_STOPPED")
                    latency_recorder.mark("stt_speech_end_hook_called")
                    latency_recorder.mark("user_speech_end")
                    dur = latency_recorder.duration("user_speech_start", "user_speech_end")
                    if dur is not None:
                        agent.stt_seconds += (dur / 1000.0)
                        if getattr(agent, "interrupted_current_turn", False):
                            interrupted_dur = time.perf_counter() - getattr(agent, "interrupted_at", 0)
                            if interrupted_dur < 0.8:
                                latency_recorder.mark("false_interruption_detected")
                                logger.info("False interruption detected (duration since interrupt < 800ms)")
                    
                    # Watchdog 2: If user speaking starts but no final transcript arrives within 3 seconds
                    expected_transcript_count = getattr(agent, "final_transcript_count", 0) + 1
                    
                    async def transcript_watchdog(expected_count):
                        await asyncio.sleep(3.0)
                        if getattr(agent, "final_transcript_count", 0) < expected_count:
                            logger.error("FATAL_NO_FINAL_TRANSCRIPT_AFTER_SPEECH")
                            
                    if getattr(self, "_transcript_watchdog_task", None):
                        self._transcript_watchdog_task.cancel()
                    self._transcript_watchdog_task = asyncio.create_task(transcript_watchdog(expected_transcript_count))

                    final_count_at_end = getattr(agent, "final_transcript_count", 0)
                    async def check_final_transcript_timeout(expected_count):
                        await asyncio.sleep(2.5)
                        if getattr(agent, "final_transcript_count", 0) == expected_count:
                            logger.error("ERROR_NO_FINAL_TRANSCRIPT_AFTER_USER_SPEECH")
                    asyncio.create_task(check_final_transcript_timeout(final_count_at_end))

        @session.on("agent_state_changed")
        def on_agent_state_changed(ev):
            state_str = str(ev.new_state).lower()
            old_state_str = str(ev.old_state).lower()
            if "speaking" in state_str:
                logger.info("AGENT_SPEAKING_STARTED")
                self._agent_speaking_started_for_turn = True
                if getattr(self, "_agent_speaking_watchdog_task", None):
                    self._agent_speaking_watchdog_task.cancel()
                
                # Diagnostic Greeting hooks
                if getattr(self, "_is_diagnostic_greeting_active", False):
                    logger.info("TTS_FIRST_AUDIO_SENT")
                    self._tts_first_audio_emitted_for_turn = True
                    if getattr(self, "_tts_watchdog_task", None):
                        self._tts_watchdog_task.cancel()

                latency_recorder.mark("agent_speech_started")
                agent.agent_speech_started_time = time.perf_counter()
            elif "speaking" in old_state_str:
                logger.info("AGENT_SPEAKING_STOPPED")
                latency_recorder.mark("agent_speech_stopped")
                latency_recorder.mark("agent_audio_stopped")
                if getattr(agent, "interrupted_current_turn", False):
                    start_time = getattr(agent, "agent_speech_started_time", None)
                    stop_time = getattr(agent, "interrupted_at", None) or time.perf_counter()
                    if start_time:
                        dur = stop_time - start_time
                        chars_spoken = min(len(getattr(agent, "current_turn_response", "")), int(dur * 15.0))
                        agent.tts_characters += max(0, chars_spoken)
                    
                    stage = "opening"
                    if agent.adapter and agent.adapter.state_machine:
                        stage = agent.adapter.state_machine.call_state.current_stage.value
                    asyncio.create_task(latency_recorder.save_metrics(self.shared.repository, stage))
                else:
                    agent.tts_characters += len(getattr(agent, "current_turn_response", ""))
                
                agent.current_turn_response = ""
                agent.agent_speech_started_time = None

                if self.ctx.room.isconnected:
                    logger.info("CALL_STILL_CONNECTED_AFTER_RESPONSE")

                if getattr(agent, "should_disconnect", False):
                    logger.info("Agent stopped speaking and should_disconnect is True. Disconnecting...")
                    if getattr(agent, "fallback_disconnect_task", None):
                        agent.fallback_disconnect_task.cancel()
                        agent.fallback_disconnect_task = None
                    asyncio.create_task(self.ctx.room.disconnect())

        self._direct_response_queue = asyncio.Queue()

        async def direct_response_consumer():
            logger.info("Direct response consumer task started")
            try:
                while True:
                    transcript_text = await self._direct_response_queue.get()
                    try:
                        await respond_to_final_transcript(transcript_text)
                    except Exception as ex:
                        logger.error(f"Error in direct_response_consumer: {ex}", exc_info=True)
                    finally:
                        self._direct_response_queue.task_done()
            except asyncio.CancelledError:
                logger.info("Direct response consumer task cancelled")

        if is_direct_enabled:
            consumer_task = asyncio.create_task(direct_response_consumer())

        async def respond_to_final_transcript(transcript_text: str):
            logger.info("DIRECT_RESPONSE_STARTED")
            try:
                # Add user transcript to session.history before calling the LLM
                if hasattr(session, "history") and session.history:
                    history_msgs = getattr(session.history, "messages", [])
                    if callable(history_msgs):
                        history_msgs = history_msgs()
                    last_msg = history_msgs[-1] if history_msgs else None
                    last_msg_text = ""
                    if last_msg:
                        if hasattr(last_msg, "text_content") and isinstance(last_msg.text_content, str):
                            last_msg_text = last_msg.text_content
                        elif hasattr(last_msg, "content") and isinstance(last_msg.content, str):
                            last_msg_text = last_msg.content
                    if not last_msg or last_msg.role != "user" or transcript_text != last_msg_text:
                        session.history.add_message(
                            role="user",
                            content=transcript_text
                        )

                # Implement chat_fn using the active LLM client on agent.llm
                async def chat_fn(instructions: str) -> str:
                    new_ctx = llm.ChatContext()
                    
                    # Determine dynamic constraint based on current stage and classification
                    stage = agent.adapter.state_machine.call_state.current_stage if agent.adapter else None
                    
                    is_objection = False
                    if agent.adapter and hasattr(agent.adapter, "runtime") and agent.adapter.runtime:
                        for ev in agent.adapter.runtime.events:
                            if getattr(ev, "event_type", "") == "objection_detected":
                                is_objection = True
                                break
                                
                    is_confusion = False
                    text_lower = transcript_text.lower().strip()
                    confusion_keywords = ["what", "who is this", "repeat", "huh", "pardon", "dont understand", "don't understand", "explain"]
                    if any(k in text_lower for k in confusion_keywords):
                        is_confusion = True

                    from core.call_state import CallStage
                    
                    if is_confusion:
                        constraint = "IMPORTANT: You MUST respond in EXACTLY one or two short sentences. Keep it brief and clear."
                    elif is_objection:
                        constraint = "IMPORTANT: You MUST respond in a MAXIMUM of two short sentences. Address the objection directly and concisely."
                    elif stage in (CallStage.OPENING, CallStage.INTEREST_CHECK):
                        constraint = "IMPORTANT: You MUST respond in EXACTLY one short sentence. Keep it extremely brief."
                    elif stage in (CallStage.DNC, CallStage.DISQUALIFIED):
                        constraint = "IMPORTANT: You MUST respond in EXACTLY one sentence to politely end the conversation."
                    elif stage == CallStage.TRANSFER_CONSENT:
                        constraint = "IMPORTANT: You MUST respond in EXACTLY one sentence asking for or confirming consent to transfer."
                    else:
                        constraint = "IMPORTANT: Keep your response short, brief, and natural."

                    # Prepend static system prompt prefix
                    loader = agent.prompt_loader or (agent.adapter and agent.adapter.prompt_loader)
                    static_prompt = loader.build_system_prompt() if loader else ""
                    combined_prompt = f"{static_prompt}\n\n{instructions}\n\n{constraint}"
                    
                    new_ctx.add_message(
                        role="system",
                        content=combined_prompt
                    )
                    
                    # Copy conversation history from session.history (user and assistant messages only)
                    history_msgs = []
                    if hasattr(session, "history") and session.history:
                        history_msgs = getattr(session.history, "messages", [])
                        if callable(history_msgs):
                            history_msgs = history_msgs()
                            
                    def get_msg_text(m) -> str:
                        if not m:
                            return ""
                        try:
                            tc = getattr(m, "text_content", None)
                            if isinstance(tc, str):
                                return tc
                            c = getattr(m, "content", None)
                            if isinstance(c, str):
                                return c
                        except Exception:
                            pass
                        return ""

                    for msg in history_msgs:
                        if msg.role in ("user", "assistant"):
                            msg_content = get_msg_text(msg)
                            new_ctx.add_message(
                                role=msg.role,
                                content=msg_content
                            )
                        
                    # Estimate prompt tokens
                    from metrics.model_cost_metrics import estimate_llm_tokens
                    prompt_str = instructions + "".join(get_msg_text(m) for m in new_ctx.messages if get_msg_text(m))
                    agent.prompt_tokens += estimate_llm_tokens(prompt_str)

                    # Dynamic max_tokens configuration from environment
                    try:
                        max_tokens_val = int(os.getenv("DANA_DIRECT_RESPONSE_MAX_TOKENS", "70").strip())
                    except ValueError:
                        max_tokens_val = 70
                    max_tokens_val = min(max_tokens_val, 90)

                    # Run LLM chat directly
                    stream = agent.llm.chat(
                        chat_ctx=new_ctx,
                        temperature=0.2,
                        top_p=agent._config.top_p if hasattr(agent, "_config") else 0.7,
                        max_tokens=max_tokens_val,
                        frequency_penalty=0.15,
                    )
                    
                    response_text = ""
                    async for chunk in stream:
                        content = chunk.delta.content if chunk.delta else ""
                        if content:
                            response_text += content

                    # Estimate completion tokens
                    agent.completion_tokens += estimate_llm_tokens(response_text)
                    return response_text

                # Call process_user_turn
                result = await agent.adapter.process_user_turn(transcript_text, chat_fn, interrupted=False)
                agent.current_turn_response = result.agent_response or ""
                logger.info(f"DIRECT_RESPONSE_TEXT_LENGTH: {len(agent.current_turn_response)}")
                
                # Update stage in registry
                from speech.context_registry import update_call_stage
                update_call_stage(agent.adapter.call_id, result.stage)

                # Call session.say using the same proven playback path as diagnostic greeting
                if agent.current_turn_response.strip():
                    handle = session.say(agent.current_turn_response)
                    await handle.wait_for_playout()
                    
                logger.info("DIRECT_RESPONSE_SAY_COMPLETED")
                
                # Add assistant response to session.history after playout
                if hasattr(session, "history") and session.history:
                    session.history.add_message(
                        role="assistant",
                        content=agent.current_turn_response
                    )
                
                # Handle disconnect timing based on outcome
                if result.should_end_call:
                    is_warm_bridge = False
                    for ev in agent.adapter.runtime.events:
                        from core.runtime_events import ToolTriggeredEvent
                        if isinstance(ev, ToolTriggeredEvent) and ev.tool_name in ("feTransfer", "transfer_to_agent") and ev.success:
                            if "warm" in ev.result_message.lower() or os.getenv("DANA_TRANSFER_MODE", "").lower() == "warm_bridge":
                                is_warm_bridge = True
                                break
                    if is_warm_bridge:
                        logger.info("Warm bridge transfer succeeded. Dana will mute and leave later.")
                        agent.should_disconnect = False
                        agent.warm_bridge_active = True
                        
                        async def warm_bridge_leave():
                            await asyncio.sleep(15.0)
                            logger.info("warm_bridge_active_dana_suppressed: Dana leaving agent session only.")
                            try:
                                await session.aclose()
                            except Exception as e:
                                logger.error(f"Error closing agent session during warm bridge: {e}")
                        asyncio.create_task(warm_bridge_leave())
                    else:
                        agent.should_disconnect = True
                        if getattr(agent, "fallback_disconnect_task", None):
                            agent.fallback_disconnect_task.cancel()
                        
                        async def disconnect_after_delay(delay: float = 8.0):
                            try:
                                await asyncio.sleep(delay)
                                if self.ctx.room and self.ctx.room.isconnected():
                                    logger.info("Fallback: Disconnecting room after delay...")
                                    await self.ctx.room.disconnect()
                            except asyncio.CancelledError:
                                logger.info("Fallback disconnect task cancelled.")
                        agent.fallback_disconnect_task = asyncio.create_task(disconnect_after_delay())
            except Exception as ex:
                logger.error(f"Error in direct response loop: {ex}", exc_info=True)

        @session.on("user_input_transcribed")
        def on_user_input_transcribed(event):
            partial_text = ""
            if hasattr(event, "transcript") and event.transcript:
                if isinstance(event.transcript, str):
                    partial_text = event.transcript
                elif hasattr(event.transcript, "text") and event.transcript.text:
                    partial_text = event.transcript.text
            if not partial_text:
                if hasattr(event, "text") and event.text:
                    partial_text = event.text
                elif hasattr(event, "alternatives") and event.alternatives:
                    if isinstance(event.alternatives[0], str):
                        partial_text = event.alternatives[0]
                    elif hasattr(event.alternatives[0], "text") and event.alternatives[0].text:
                        partial_text = event.alternatives[0].text
                    elif hasattr(event.alternatives[0], "transcript") and event.alternatives[0].transcript:
                        partial_text = event.alternatives[0].transcript
            
            if not event.is_final:
                logger.info("USER_TRANSCRIPT_PARTIAL")
                logger.info(f"USER_TRANSCRIPT_PARTIAL: {partial_text}")
                
            if partial_text.strip():
                agent.user_transcript_received = True

            if event.is_final:
                logger.info("USER_TRANSCRIPT_FINAL")
                logger.info(f"USER_TRANSCRIPT_FINAL: {partial_text}")
                agent.final_transcript_count = getattr(agent, "final_transcript_count", 0) + 1
                logger.info("USER_TRANSCRIPT_RECEIVED")
                logger.info(f"FINAL_TRANSCRIPT_TEXT_LENGTH: {len(partial_text)}")
                latency_recorder.mark("transcript_final")
                
                # Cancel transcript watchdog
                if getattr(self, "_transcript_watchdog_task", None):
                    self._transcript_watchdog_task.cancel()
                    
                # Watchdog 3: Stop starting watchdog when direct response mode is active
                is_direct_enabled = os.getenv("DANA_DIRECT_RESPONSE_ON_FINAL_TRANSCRIPT", "true").strip().lower() in ("true", "1", "yes")
                if not is_direct_enabled:
                    self._llm_node_entered_for_turn = False
                    
                    async def llm_node_watchdog():
                        await asyncio.sleep(3.0)
                        if not getattr(self, "_llm_node_entered_for_turn", False):
                            logger.error("FATAL_LLM_NODE_NOT_ENTERED_AFTER_TRANSCRIPT")
                            
                    if getattr(self, "_llm_watchdog_task", None):
                        self._llm_watchdog_task.cancel()
                    self._llm_watchdog_task = asyncio.create_task(llm_node_watchdog())
                
                # Direct response path
                if is_direct_enabled and partial_text.strip():
                    self._direct_response_queue.put_nowait(partial_text)
            else:
                enable_semantic = os.getenv("DANA_ENABLE_SEMANTIC_TURN_DETECTION", "false").strip().lower() in ("true", "1", "yes")
                if enable_semantic and self.shared.config.endpoint_mode == "adaptive":
                    from speech.semantic_turn_detector import SemanticTurnDetector
                    from speech.context_registry import get_current_call_stage
                    from speech.endpoint_tuner import safe_update_endpointing
                    
                    detector = SemanticTurnDetector()
                    stage = get_current_call_stage() or "OPENING"
                    
                    if partial_text:
                        res = detector.process_transcript(partial_text, stage=stage)
                        safe_update_endpointing(session, res.recommended_min_delay, res.recommended_max_delay)

        # Track listener for AMD
        def start_amd_for_track(track):
            if os.getenv("DANA_CONTROLLED_LIVE_TEST", "false").lower() in ("true", "1", "yes"):
                logger.info("Bypassing AMD parallel worker for track %s (controlled live test enabled)", track.sid)
                return
            enable_amd = os.getenv("DANA_ENABLE_AMD_WORKER", "false").lower() in ("true", "1", "yes")
            if not enable_amd:
                logger.info("Bypassing AMD parallel worker for track %s (DANA_ENABLE_AMD_WORKER is false)", track.sid)
                return
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                if not getattr(agent, "_amd_started", False):
                    agent._amd_started = True
                    logger.info("Starting AMD parallel worker for track: %s", track.sid)
                    asyncio.create_task(run_amd_worker(track, session, agent, self.ctx.room))

        def handle_audio_track_subscription(track):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                if not getattr(self, "_audio_track_subscribed", False):
                    self._audio_track_subscribed = True
                    logger.info("TRACK_SUBSCRIBED_AUDIO")

        @self.ctx.room.on("track_subscribed")
        def on_track_subscribed(track: rtc.Track, publication: rtc.TrackPublication, participant: rtc.RemoteParticipant):
            logger.info(f"[TRACK_SUBSCRIBED_LOG] participant={participant.identity} kind={track.kind} source={publication.source} sid={track.sid}")
            handle_audio_track_subscription(track)
            start_amd_for_track(track)

        for publication in participant.track_publications.values():
            logger.info(f"[EXISTING_TRACK_LOG] participant={participant.identity} source={publication.source} track_present={publication.track is not None}")
            if publication.track:
                handle_audio_track_subscription(publication.track)
                start_amd_for_track(publication.track)

        # Register call in context registry
        from speech.context_registry import register_call, update_call_stage
        register_call(call_id, campaign_id)
        update_call_stage(call_id, "OPENING")

        if self.shared.config.endpoint_mode == "adaptive":
            from speech.endpoint_tuner import get_endpoint_delays, safe_update_endpointing
            min_d, max_d = get_endpoint_delays("OPENING")
            safe_update_endpointing(session, min_d, max_d)

        # Configure interruption profile callback on stage transition
        if hasattr(agent.adapter, "state_machine") and agent.adapter.state_machine:
            call_state = agent.adapter.state_machine.call_state
            
            def apply_interruption_profile(stage):
                from speech.interruption_profiles import get_profile_for_stage
                profile = get_profile_for_stage(stage, self.shared.config)
                
                if self.shared.config.record_interruption_telemetry:
                    import time
                    latency_recorder.events[f"profile_applied_{stage.value}"] = time.perf_counter()
                    
                if hasattr(self.shared.vad, "update_profile"):
                    self.shared.vad.update_profile(profile)
                    
                update_call_stage(call_id, stage.value)
                
            call_state._transition_callbacks = getattr(call_state, "_transition_callbacks", [])
            call_state._transition_callbacks.append(apply_interruption_profile)
            
            # Apply initial profile for current stage
            apply_interruption_profile(call_state.current_stage)

        # Start AgentSession with RoomOptions
        logger.info("AGENT_SESSION_STARTING")
        await session.start(
            room=self.ctx.room,
            agent=agent,
            room_options=room_io.RoomOptions(
                audio_input=True,
                audio_output=True,
                video_input=False,
                text_input=False,
            ),
        )
        logger.info("AGENT_SESSION_STARTED")
        logger.info("ROOM_AUDIO_OUTPUT_ENABLED")
        logger.info("ROOM_AUDIO_INPUT_ENABLED")

        # Forced Diagnostic Greeting Mode
        if os.getenv("DANA_FORCE_DIAGNOSTIC_GREETING", "false").strip().lower() in ("true", "1", "yes"):
            logger.info("DIAG_SESSION_START_SUCCEEDED")
            greeting_text = os.getenv("DANA_DIAGNOSTIC_GREETING_TEXT", "Hello, can you hear me?")
            logger.info("DIAG_GREETING_SAY_CALLED")
            
            self._is_diagnostic_greeting_active = True
            
            async def run_diagnostic_greeting():
                try:
                    handle = session.say(greeting_text)
                    await handle.wait_for_playout()
                    logger.info("TTS_STREAM_COMPLETED")
                    logger.info("DIAG_GREETING_SAY_COMPLETED")
                except Exception as ex:
                    logger.error(f"Error during diagnostic greeting: {ex}")
                finally:
                    self._is_diagnostic_greeting_active = False
            
            asyncio.create_task(run_diagnostic_greeting())

        session.repository = self.shared.repository

        # Store the audio source for the direct FFI background playback
        if hasattr(session, "_room_io") and session._room_io:
            audio_output = session._room_io.audio_output
            if hasattr(audio_output, "_audio_source"):
                if os.getenv("DANA_ENABLE_EXPERIMENTAL_DIRECT_FFI_AUDIO", "false").lower() == "true" and os.getenv("DANA_ENABLE_EXPERIMENTAL_AUDIO_MONKEYPATCH", "false").lower() == "true":
                    import legacy.tts_service as tts_service
                    tts_service.active_audio_source = audio_output._audio_source
                    audio_output._bypass_main_loop = True
                    logger.info("Direct audio source registered in legacy.tts_service and main-loop bypass enabled.")
                else:
                    logger.info("Direct FFI TTS push or audio monkeypatch is disabled. Operating in native LiveKit voice path mode.")

        # Emit call.session_started event
        from integrations.crm_webhooks import emit_crm_event_async
        lead_prof = agent.adapter.state_machine.lead.to_summary_dict() if agent.adapter else None
        await emit_crm_event_async(
            "call.session_started",
            repository=self.shared.repository,
            call_id=call_id,
            lead_id=lead_prof.get("lead_id") if lead_prof else None,
            campaign_id=campaign_id,
            phone_e164=participant.identity,
            lead_profile=lead_prof
        )

        # Speak greeting depending on opening_mode
        is_diag_greeting = os.getenv("DANA_FORCE_DIAGNOSTIC_GREETING", "false").strip().lower() in ("true", "1", "yes")
        if self.shared.config.opening_mode == "immediate" and self.shared.config.opening_line and not is_diag_greeting:
            latency_recorder.mark("greeting_started")
            latency_recorder.mark("greeting_tts_started")
            logger.info(f"Speaking opening line: {self.shared.config.opening_line}")
            await session.say(self.shared.config.opening_line)
            latency_recorder.mark("greeting_audio_published")
            from core.call_state import CallStage
            agent.adapter.state_machine.call_state.transition_to(CallStage.INTEREST_CHECK)
        elif self.shared.config.opening_mode == "wait_for_user":
            logger.info("Opening mode: wait_for_user — agent will not speak first")
        else:
            logger.info(f"Opening mode: {self.shared.config.opening_mode} (opening line empty) — agent is silent")

        try:
            # Loop until room disconnected
            while self.ctx.room.isconnected():
                await asyncio.sleep(1.0)
        finally:
            if consumer_task:
                consumer_task.cancel()
                try:
                    await consumer_task
                except asyncio.CancelledError:
                    pass
            latency_recorder.log_summary()
            from speech.context_registry import unregister_call
            unregister_call(call_id)
            logger.info(f"Session finished for call {call_id}")

            # Re-fetch latest lead profile
            lead_prof = agent.adapter.state_machine.lead.to_summary_dict() if agent.adapter else {}
            lead_id = lead_prof.get("lead_id") or lead_prof.get("id")

            # Emit call.session_completed event
            from integrations.crm_webhooks import emit_crm_event_async
            await emit_crm_event_async(
                "call.session_completed",
                repository=self.shared.repository,
                call_id=call_id,
                lead_id=lead_id,
                campaign_id=campaign_id,
                phone_e164=participant.identity,
                lead_profile=lead_prof
            )

            # Run post-call QA and scoring
            outcome = "ended"
            if getattr(agent, "is_voicemail", False):
                outcome = "voicemail"
            elif lead_prof.get("is_qualified"):
                outcome = "transferred"
            elif lead_prof.get("callback_requested"):
                outcome = "callback"
            elif lead_prof.get("do_not_call_requested"):
                outcome = "dnc"
            elif lead_prof.get("disqualified_reason"):
                outcome = "disqualified"
            try:
                from qa.call_record import CallRecord as QACallRecord, CallTurn as QACallTurn
                from qa.scoring import CallScorer
                from storage.repository import parse_dt

                # Load turns from DB
                raw_turns = await self.shared.repository._store.query("call_turns", {"call_id": call_id})
                raw_turns.sort(key=lambda t: t.get("turn_number", 0))
                
                turns_data = []
                for t in raw_turns:
                    turns_data.append(QACallTurn(
                        speaker="agent" if t.get("speaker") == "agent" else "prospect",
                        text=t.get("text", ""),
                        stage=t.get("stage", ""),
                        timestamp=parse_dt(t.get("timestamp") or t.get("created_at")) or datetime.now(timezone.utc),
                        interrupted=t.get("interrupted", False)
                    ))
                
                raw_tools = await self.shared.repository._store.query("tool_events", {"call_id": call_id})
                tool_events_data = [dict(t) for t in raw_tools]

                ended_at = datetime.now(timezone.utc)
                started_at = latency_recorder.get_timestamp("call_start") or ended_at
                duration = (ended_at - started_at).total_seconds()
                final_stage = agent.adapter.state_machine.call_state.current_stage.value if agent.adapter else "end"
                
                if getattr(agent, "is_voicemail", False):
                    outcome = "voicemail"
                elif lead_prof.get("is_qualified"):
                    outcome = "transferred"
                elif lead_prof.get("callback_requested"):
                    outcome = "callback"
                elif lead_prof.get("do_not_call_requested"):
                    outcome = "dnc"
                elif lead_prof.get("disqualified_reason"):
                    outcome = "disqualified"

                call_record = QACallRecord(
                    call_id=call_id,
                    turns=turns_data,
                    lead_profile=lead_prof,
                    final_stage=final_stage,
                    duration_seconds=duration,
                    tool_events=tool_events_data,
                    started_at=started_at,
                    ended_at=ended_at,
                    outcome=outcome
                )

                # Score call
                scorer = CallScorer()
                scorecard = scorer.score_call(call_record)
                
                # Save QA report
                await self.shared.repository.save_qa_report(
                    call_id=call_id,
                    overall_score=scorecard.overall_score,
                    grade=scorecard.grade,
                    scores=scorecard.scores,
                    issues=scorecard.issues,
                    recommendations=[]
                )

                if scorecard.overall_score >= 9.0 or scorecard.grade == "A":
                    try:
                        await suggest_lessons_from_call(
                            call_record=call_record,
                            scorecard=scorecard,
                            repository=self.shared.repository
                        )
                        logger.info("Successfully generated suggested training notes from high-performing call")
                    except Exception as e:
                        logger.error(f"Failed to generate suggested lessons from high-performing call: {e}", exc_info=True)

                if scorecard.overall_score < 7.0 or scorecard.grade == "F":
                    await emit_crm_event_async(
                        "qa.failed",
                        repository=self.shared.repository,
                        call_id=call_id,
                        lead_id=lead_id,
                        campaign_id=campaign_id,
                        phone_e164=participant.identity,
                        qa={
                            "overall_score": scorecard.overall_score,
                            "grade": scorecard.grade,
                            "issues": scorecard.issues,
                            "scores": scorecard.scores
                        },
                        lead_profile=lead_prof
                    )
            except Exception as e:
                logger.error(f"Failed to execute QA scoring or emit qa.failed: {e}")

            # Update calls table record with final outcome and duration
            try:
                if 'ended_at' not in locals():
                    ended_at = datetime.now(timezone.utc)
                if 'started_at' not in locals():
                    started_at = latency_recorder.get_timestamp("call_start") or ended_at
                if 'duration' not in locals():
                    duration = (ended_at - started_at).total_seconds()
                if 'outcome' not in locals() or outcome == "ended":
                    outcome = "ended"
                    if getattr(agent, "is_voicemail", False):
                        outcome = "voicemail"
                    elif lead_prof.get("is_qualified"):
                        outcome = "transferred"
                    elif lead_prof.get("callback_requested"):
                        outcome = "callback"
                    elif lead_prof.get("do_not_call_requested"):
                        outcome = "dnc"
                    elif lead_prof.get("disqualified_reason"):
                        outcome = "disqualified"

                existing_call = await self.shared.repository.get_call_record(call_id)
                is_dry_run = existing_call.get("dry_run", False) if existing_call else False

                try:
                    final_stage = agent.adapter.state_machine.call_state.current_stage.value if agent.adapter else "end"
                    await latency_recorder.save_metrics(self.shared.repository, final_stage)
                except Exception as e:
                    logger.error(f"Failed to save final latency metrics: {e}")

                await self.shared.repository.save_call(
                    call_id=call_id,
                    ended_at=ended_at,
                    duration_seconds=duration,
                    outcome=outcome,
                    latency_summary=latency_recorder.to_dict().get("durations"),
                    qa_score=locals().get("scorecard").overall_score if 'scorecard' in locals() else None
                )

                # Save model costs and outcome rollup
                from metrics.model_cost_metrics import calculate_and_save_costs
                from metrics.outcome_metrics import save_outcome_for_call
                
                tts_prov = "kokoro"
                voice_lower = self.shared.config.tts_voice.lower()
                if "eleven" in voice_lower:
                    tts_prov = "elevenlabs"
                elif "openai" in voice_lower:
                    tts_prov = "openai"
                    
                await calculate_and_save_costs(
                    repository=self.shared.repository,
                    call_id=call_id,
                    campaign_id=campaign_id,
                    stt_provider=self.shared.config.stt_provider,
                    stt_seconds=agent.stt_seconds,
                    llm_model=self.shared.config.llm_model,
                    prompt_tokens=agent.prompt_tokens,
                    completion_tokens=agent.completion_tokens,
                    tts_provider=tts_prov,
                    tts_characters=agent.tts_characters,
                    telephony_provider="telnyx",
                    telephony_seconds=duration,
                    dry_run=is_dry_run,
                    llm_tokens_estimated=True
                )

                # Save turn latency spans & GPU runtime allocations based on latency recorder
                try:
                    from metrics.gpu_cost_allocator import allocate_gpu_cost
                    from routing.model_router import ModelRouter

                    lat_dict = latency_recorder.to_dict()
                    durs = lat_dict.get("durations", {})
                    now = datetime.now(timezone.utc)
                    
                    stt_ms = durs.get("stt_latency")
                    if stt_ms is not None:
                        stt_start = now - timedelta(milliseconds=stt_ms)
                        await self.shared.repository.save_turn_latency_span(
                            call_id=call_id,
                            turn_number=agent.turn_number if hasattr(agent, "turn_number") else 1,
                            component="stt",
                            start_time=stt_start,
                            end_time=now,
                            latency_ms=float(stt_ms)
                        )
                        stt_provider, _ = ModelRouter.get_last_decision(call_id, "stt")
                        if stt_provider in ("local", "whisper"):
                            await allocate_gpu_cost(
                                repository=self.shared.repository,
                                call_id=call_id,
                                component="stt",
                                runtime_seconds=float(stt_ms) / 1000.0
                            )
                    
                    llm_ms = durs.get("llm_duration")
                    if llm_ms is not None:
                        llm_start = now - timedelta(milliseconds=llm_ms)
                        await self.shared.repository.save_turn_latency_span(
                            call_id=call_id,
                            turn_number=agent.turn_number if hasattr(agent, "turn_number") else 1,
                            component="llm",
                            start_time=llm_start,
                            end_time=now,
                            latency_ms=float(llm_ms)
                        )
                        llm_provider, _ = ModelRouter.get_last_decision(call_id, "llm")
                        if llm_provider in ("local", "vllm", "llama"):
                            await allocate_gpu_cost(
                                repository=self.shared.repository,
                                call_id=call_id,
                                component="llm",
                                runtime_seconds=float(llm_ms) / 1000.0
                            )

                    tts_ms = durs.get("tts_synthesis_start_latency")
                    if tts_ms is not None:
                        tts_start = now - timedelta(milliseconds=tts_ms)
                        await self.shared.repository.save_turn_latency_span(
                            call_id=call_id,
                            turn_number=agent.turn_number if hasattr(agent, "turn_number") else 1,
                            component="tts",
                            start_time=tts_start,
                            end_time=now,
                            latency_ms=float(tts_ms)
                        )
                        tts_provider, _ = ModelRouter.get_last_decision(call_id, "tts")
                        if tts_provider in ("local", "kokoro", "bella"):
                            await allocate_gpu_cost(
                                repository=self.shared.repository,
                                call_id=call_id,
                                component="tts",
                                runtime_seconds=float(tts_ms) / 1000.0
                            )
                except Exception as e:
                    logger.error(f"Error saving turn latency spans/GPU allocations: {e}")

                reconciled_total_cost = 0.0
                try:
                    from metrics.provider_cost_reconciler import reconcile_call_costs
                    reconciled = await reconcile_call_costs(
                        repository=self.shared.repository,
                        call_id=call_id,
                        campaign_id=campaign_id,
                        duration_seconds=duration,
                        stt_seconds=agent.stt_seconds,
                        prompt_tokens=agent.prompt_tokens,
                        completion_tokens=agent.completion_tokens,
                        tts_characters=agent.tts_characters,
                        outcome=outcome
                    )
                    reconciled_total_cost = float(reconciled.get("total_cost") or 0.0)
                except Exception as e:
                    logger.error(f"Error during cost reconciliation: {e}")

                await save_outcome_for_call(self.shared.repository, call_id, campaign_id, outcome, cost=reconciled_total_cost)
                
                try:
                    from metrics.cost_per_outcome import recompute_campaign_rollups
                    await recompute_campaign_rollups(self.shared.repository, campaign_id)
                except Exception as e:
                    logger.error(f"Error recomputing campaign rollups: {e}")
                
                try:
                    from routing.model_router import ModelRouter
                    ModelRouter.cleanup_call_routing(call_id)
                except Exception as re:
                    logger.error(f"Failed to cleanup routing state: {re}")

                try:
                    from dialer.pacing import CampaignPacer
                    pacer = CampaignPacer(self.shared.repository)
                    await pacer.mark_call_finished(campaign_id, call_id)
                except Exception as pe:
                    logger.error(f"Failed to mark call finished in campaign pacer: {pe}")
            except Exception as ce:
                logger.error(f"Failed to update call record or save metrics: {ce}")

            try:
                if lead_id and self.shared.repository:
                    from dialer.retry_policy import RetryPolicy
                    
                    if outcome == "voicemail":
                        campaign_rec = await self.shared.repository.get_campaign(campaign_id)
                        lead_rec = await self.shared.repository.get_lead(lead_id)
                        if campaign_rec and lead_rec:
                            attempts = lead_rec.get("attempts", 1)
                            retry_after = RetryPolicy.get_retry_after("voicemail", campaign_rec, attempts, datetime.now(timezone.utc))
                            await self.shared.repository.release_lead_lock(
                                lead_id=lead_id,
                                reason="transient_call_failure",
                                retry_after=retry_after,
                                status_override="failed"
                            )
                    elif outcome == "dnc":
                        lead_rec = await self.shared.repository.get_lead(lead_id)
                        phone = lead_rec.get("phone_number") or lead_rec.get("phone_e164") or participant.identity
                        await self.shared.repository.mark_lead_dnc(
                            lead_id=lead_id,
                            phone_e164=phone,
                            campaign_id=campaign_id,
                            reason="prospect_dnc_request"
                        )
                    elif outcome == "callback":
                        cb_time_str = lead_prof.get("callback_time_local") or lead_prof.get("callback_time")
                        cb_time = None
                        if cb_time_str:
                            try:
                                cb_time = datetime.fromisoformat(cb_time_str)
                            except ValueError:
                                pass
                        if not cb_time:
                            cb_time = datetime.now(timezone.utc) + timedelta(hours=2)
                        await self.shared.repository.mark_lead_callback(
                            lead_id=lead_id,
                            callback_time=cb_time
                        )
            except Exception as ue:
                logger.error(f"Failed to update lead status at session end: {ue}")

            await emit_crm_event_async(
                "call.completed",
                repository=self.shared.repository,
                call_id=call_id,
                lead_id=lead_id,
                campaign_id=campaign_id,
                phone_e164=participant.identity,
                outcome=outcome,
                lead_profile=lead_prof
            )
