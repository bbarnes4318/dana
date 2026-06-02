"""
Sovereign Voice Stack - Main Agent Entry Point
Ultra-low-latency Voice AI using LiveKit Agents Framework.
"""

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import AsyncIterable, Optional

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    AutoSubscribe,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
    llm,
    Agent,
    AgentSession,
    room_io,
    TurnHandlingOptions,
)
from livekit.plugins import openai as lk_openai
from livekit.plugins import silero
from speech.custom_vad import ElderlySileroVAD

from voice_config import VoiceConfig
from latency_metrics import LatencyRecorder
from stt_service import create_stt

from core.prompt_loader import PromptLoader
from core.objection_classifier import ObjectionClassifier
from core.objection_response_policy import ObjectionResponsePolicy
from rag.context_builder import ContextBuilder
from core.action_policy import ActionPolicy
from tools.tool_registry import ToolRegistry
from safety.compliance_filter import ComplianceFilter
from safety.output_validator import OutputValidator
from safety.pii_redaction import PIIRedactor
from storage.repository import Repository
from core.livekit_runtime_adapter import LiveKitRuntimeAdapter

# Load environment variables
load_dotenv()

# Build temporary config to set log level
_temp_config = VoiceConfig()

# Configure logging
logging.basicConfig(
    level=getattr(logging, _temp_config.log_level, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Monkeypatch _ParticipantAudioOutput to support event-loop bypass for direct FFI playback
try:
    import livekit.agents.voice.room_io._output as room_io_output
    import time
    
    original_forward_audio = room_io_output._ParticipantAudioOutput._forward_audio
    
    async def patched_forward_audio(self):
        if getattr(self, "_bypass_main_loop", False):
            # Bypass native push to self._audio_source and sleep instead to simulate playback pacing
            # This maintains all events and callbacks on the main loop without blocking/scheduling native writes.
            async for frame in self._audio_buf:
                if not self._playback_enabled.is_set():
                    await self._playback_enabled.wait()
                    
                if self._interrupted_event.is_set() or self._pushed_duration == 0:
                    if self._interrupted_event.is_set() and self._flush_task:
                        await self._flush_task
                    continue
                    
                if not self._first_frame_event.is_set():
                    self._first_frame_event.set()
                    self.on_playback_started(created_at=time.time())
                
                await asyncio.sleep(frame.duration)
        else:
            await original_forward_audio(self)
            
    room_io_output._ParticipantAudioOutput._forward_audio = patched_forward_audio
    logger.info("Successfully monkeypatched _ParticipantAudioOutput._forward_audio for event-loop bypass.")
except Exception as e:
    logger.error(f"Failed to monkeypatch _ParticipantAudioOutput: {e}")

# ---- Default fallback prompt (used when file is missing) --------------------
_DEFAULT_INSTRUCTIONS = (
    "You are Alex with American Beneficiary, an outbound final-expense screening coordinator. "
    "You are having a natural phone conversation. "
    "Follow these rules strictly:\n"
    "- Keep responses very brief: respond in 1 sentence by default.\n"
    "- Ask only one question at a time.\n"
    "- Use natural, spoken language and short acknowledgment phrases (like 'Okay', 'Gotcha', 'Understood').\n"
    "- Use contractions naturally (e.g. I'm, you're, we'll).\n"
    "- NEVER use markdown formatting, bullet points, or lists.\n"
    "- NEVER quote premiums/prices or make approval claims or guarantees.\n"
    "- You are not licensed, never claim to be licensed or an agent.\n"
    "- Do not proactively announce AI/bot/automation, and do not claim to be human if asked.\n"
    "- Do not ask for name, state, phone type, text capability, budget, beneficiary, or exact age.\n"
    "- Only confirm open_to_review, age forty to eighty-five, living independently, and financial decision-making before requesting explicit transfer consent.\n"
    "- Speak in a friendly, conversational tone."
)


def load_instructions(path: str) -> str:
    """Load agent instructions from a file path.

    Falls back to `_DEFAULT_INSTRUCTIONS` if the file is missing, empty,
    or unreadable.
    """
    if not path:
        logger.warning("No DANA_AGENT_PROMPT_PATH configured — using default instructions")
        return _DEFAULT_INSTRUCTIONS

    resolved = Path(path)
    if not resolved.is_absolute():
        # Relative paths are resolved from the app working directory
        resolved = Path.cwd() / resolved

    try:
        content = resolved.read_text(encoding="utf-8").strip()
        if not content:
            logger.warning("Prompt file %s is empty — using default instructions", resolved)
            return _DEFAULT_INSTRUCTIONS
        logger.info("Loaded agent instructions from %s (%d chars)", resolved, len(content))
        return content
    except FileNotFoundError:
        logger.warning("Prompt file %s not found — using default instructions", resolved)
        return _DEFAULT_INSTRUCTIONS
    except Exception as exc:
        logger.warning("Could not read prompt file %s: %s — using default instructions", resolved, exc)
        return _DEFAULT_INSTRUCTIONS


class SharedComponents:
    """Heavyweight models and configs cached in the process userdata."""
    def __init__(self, config: VoiceConfig):
        self.config = config
        self.stt = None
        self.tts = None
        self.llm = None
        self.vad = None
        self.router = None
        # AgentRuntime shared components
        self.prompt_loader = None
        self.objection_classifier = None
        self.objection_policy = None
        self.context_builder = None
        self.action_policy = None
        self.tool_registry = None
        self.compliance_filter = None
        self.output_validator = None
        self.pii_redactor = None
        self.repository = None

    async def initialize(self):
        from routing.model_router import ModelRouter
        self.router = ModelRouter(self.config)

        # 1. Initialize STT
        self.stt = create_stt(self.config)
        if hasattr(self.stt, "initialize"):
            await self.stt.initialize()
            
        # 2. Initialize local TTS
        from tts_service import LocallyHostedKokoro, TTSConfig
        tts_config = TTSConfig(
            voice=self.config.tts_voice,
            speed=self.config.tts_speed,
        )
        local_tts = LocallyHostedKokoro(tts_config)
        await local_tts.initialize()
        
        # Initialize cloud TTS lazily
        cloud_tts = None
        cloud_tts_required = self.config.tts_routing_mode == "cloud"
        cloud_tts_allowed = self.config.tts_routing_mode != "local" or self.config.allow_cloud_tts_fallback
        
        has_cloud_tts_creds = False
        voice_lower = self.config.tts_voice.lower()
        if "openai" in voice_lower:
            has_cloud_tts_creds = bool(os.getenv("OPENAI_API_KEY"))
        else:
            has_cloud_tts_creds = bool(os.getenv("ELEVENLABS_API_KEY"))
            
        if cloud_tts_required and not has_cloud_tts_creds:
            raise RuntimeError("Cloud TTS mode requested but credentials (OPENAI_API_KEY/ELEVENLABS_API_KEY) are missing.")
            
        if cloud_tts_allowed and has_cloud_tts_creds:
            try:
                if "openai" in voice_lower:
                    from livekit.plugins.openai import TTS as OpenAI_TTS
                    cloud_tts = OpenAI_TTS(voice="alloy")
                else:
                    from livekit.plugins import elevenlabs
                    cloud_tts = elevenlabs.TTS()
            except Exception as e:
                logger.error(f"Failed to initialize cloud TTS provider: {e}")
                if cloud_tts_required:
                    raise RuntimeError(f"Cloud TTS requested but failed to load: {e}")

        # 3. Initialize local LLM
        local_llm = lk_openai.LLM(
            model=self.config.llm_model,
            base_url=self.config.vllm_base_url,
            api_key="not-needed",
        )
        
        # Initialize cloud LLM lazily
        cloud_llm = None
        cloud_llm_required = self.config.llm_routing_mode == "cloud"
        cloud_llm_allowed = self.config.llm_routing_mode != "local" or self.config.allow_cloud_llm_fallback
        
        has_cloud_llm_creds = bool(os.getenv("OPENAI_API_KEY"))
        if cloud_llm_required and not has_cloud_llm_creds:
            raise RuntimeError("Cloud LLM mode requested but OPENAI_API_KEY is missing.")
            
        if cloud_llm_allowed and has_cloud_llm_creds:
            try:
                cloud_llm = lk_openai.LLM(
                    model="gpt-4o-mini",
                    api_key=os.getenv("OPENAI_API_KEY")
                )
            except Exception as e:
                logger.error(f"Failed to initialize cloud LLM provider: {e}")
                if cloud_llm_required:
                    raise RuntimeError(f"Cloud LLM requested but failed to load: {e}")

        # Wrap with Routed LLM and TTS
        from routing.routed_llm import RoutedLLM
        from routing.routed_tts import RoutedTTS
        
        self.llm = RoutedLLM(local_llm, cloud_llm, self.router)
        self.tts = RoutedTTS(local_tts, cloud_tts, self.router)
        
        # 4. Initialize VAD (Elderly-Demographic Optimized Silero VAD)
        loop = asyncio.get_event_loop()
        self.vad = await loop.run_in_executor(None, ElderlySileroVAD.load)

        # 5. Initialize stateless AgentRuntime components
        project_root = Path(__file__).resolve().parent
        self.prompt_loader = PromptLoader(project_root=project_root)
        self.objection_classifier = ObjectionClassifier()
        self.objection_policy = ObjectionResponsePolicy()
        self.context_builder = ContextBuilder()
        self.action_policy = ActionPolicy()
        self.tool_registry = ToolRegistry()
        self.compliance_filter = ComplianceFilter()
        self.output_validator = OutputValidator()
        self.pii_redactor = PIIRedactor()
        self.repository = Repository()

        logger.info("All shared components initialized successfully")


class DanaAgent(Agent):
    """
    Subclass of livekit.agents.Agent implementing our phone-optimized Dana personality
    and wrapping LLM & TTS streaming nodes with latency recorder hooks.
    """
    def __init__(self, shared: SharedComponents, latency_recorder: LatencyRecorder):
        instructions = load_instructions(shared.config.agent_prompt_path)
        super().__init__(instructions=instructions)
        self.llm = shared.llm
        self.tts = shared.tts
        self.stt = shared.stt
        self._config = shared.config
        self._latency_recorder = latency_recorder
        self.room = None
        self.adapter: Optional[LiveKitRuntimeAdapter] = None
        self.should_disconnect = False
        self.warm_bridge_active = False
        self.fallback_disconnect_task: Optional[asyncio.Task] = None
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
        if self.warm_bridge_active:
            logger.info("warm_bridge_active_dana_suppressed: Suppressing Dana responses after warm bridge success.")
            return

        self._latency_recorder.mark("llm_request_start")
        
        # Get the latest user message
        user_msg = chat_ctx.messages[-1] if chat_ctx.messages else None
        user_text = user_msg.content if user_msg else ""
        
        if not user_text:
            logger.warning("llm_node called but no user message found in chat_ctx")
            self._latency_recorder.mark("llm_done")
            return

        if not self.adapter:
            logger.error("LiveKitRuntimeAdapter is not initialized on the agent!")
            self._latency_recorder.mark("llm_done")
            return
        
        # Define clean chat function to call vLLM client directly without re-entering DanaAgent.llm_node
        async def chat_fn(instructions: str) -> str:
            new_ctx = llm.ChatContext()
            
            # Prepend static system prompt prefix (personality and compliance parameters)
            static_prompt = self.prompt_loader.build_system_prompt()
            combined_prompt = f"{static_prompt}\n\n{instructions}"
            
            # Add compiled instructions as the system prompt
            new_ctx.messages.append(llm.ChatMessage(
                role="system",
                content=combined_prompt
            ))
            
            # Copy conversation history (user and assistant messages only)
            for msg in chat_ctx.messages:
                if msg.role in ("user", "assistant"):
                    new_ctx.messages.append(llm.ChatMessage(
                        role=msg.role,
                        content=msg.content
                    ))
            
            # Estimate prompt tokens
            prompt_str = instructions + "".join(m.content for m in new_ctx.messages if m.content)
            from metrics.model_cost_metrics import estimate_llm_tokens
            self.prompt_tokens += estimate_llm_tokens(prompt_str)

            # Run LLM chat directly
            stream = self.llm.chat(
                chat_ctx=new_ctx,
                temperature=self._config.temperature,
                top_p=self._config.top_p,
                max_tokens=self._config.max_tokens,
                frequency_penalty=0.15,
            )
            
            response_text = ""
            async for chunk in stream:
                content = chunk.choices[0].delta.content if chunk.choices else ""
                if content:
                    response_text += content

            # Estimate completion tokens
            self.completion_tokens += estimate_llm_tokens(response_text)
            return response_text

        # Process user turn via the adapter exactly once
        result = await self.adapter.process_user_turn(user_text, chat_fn)
        self.current_turn_response = result.agent_response or ""
        
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
                # DNC, disqualified, wrong number, callback scheduled, or cold transfer -> disconnect after TTS finishes speaking
                self.should_disconnect = True
                
                # Register cancellable fallback task
                if self.fallback_disconnect_task:
                    self.fallback_disconnect_task.cancel()
                
                async def disconnect_after_delay(delay: float = 8.0):
                    try:
                        await asyncio.sleep(delay)
                        if self.room and self.room.is_connected():
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
        tts_stream = self.tts.stream()
        first_text = True
        
        async def push_text_loop():
            nonlocal first_text
            try:
                async for chunk in text:
                    if chunk and first_text:
                        first_text = False
                        self._latency_recorder.mark("tts_first_text")
                    await tts_stream.push_text(chunk)
                await tts_stream.flush()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Error in tts_node push loop: {e}")
            
        push_task = asyncio.create_task(push_text_loop())
        
        first_audio = True
        try:
            async for frame in tts_stream:
                if first_audio:
                    first_audio = False
                    self._latency_recorder.mark("tts_first_audio")
                    self._latency_recorder.mark("first_audio_published")
                yield frame
        finally:
            await tts_stream.interrupt()
            await tts_stream.aclose()
            push_task.cancel()


async def run_amd_worker(track: rtc.Track, session: AgentSession, agent: any, room: rtc.Room):
    import array
    import math
    logger.info("AMD classification worker started for call_id=%s", getattr(agent, "call_id", "unknown"))
    
    audio_stream = rtc.AudioStream(track, sample_rate=16000, num_channels=1)
    
    # RMS Energy threshold (RMS) = 300.0 (detects speech component)
    # ZCR frequency range = 0.01 to 0.55 (voiced speech components)
    rms_threshold = 300.0
    zcr_min = 0.01
    zcr_max = 0.55
    
    speech_duration = 0.0
    silence_duration = 0.0
    is_speaking = False
    
    try:
        async for event in audio_stream:
            if not room.is_connected() or getattr(agent, "is_voicemail", False):
                break
                
            frame = event.frame
            if not frame.data:
                continue
                
            samples = array.array('h', frame.data)
            num_samples = len(samples)
            if num_samples == 0:
                continue
                
            frame_duration = frame.duration
            
            # Root Mean Square (RMS) energy
            sum_squares = sum(s * s for s in samples)
            rms = math.sqrt(sum_squares / num_samples)
            
            # Zero Crossing Rate (ZCR)
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
            
            if speech_duration >= 1.5:
                logger.info("AMD: Voicemail greeting detected (speech_duration=%.2fs >= 1.5s). Triggering MachineDetected teardown.", speech_duration)
                agent.is_voicemail = True
                
                # Switch LLM state machine to voicemail/end stage
                if hasattr(agent, "adapter") and agent.adapter and agent.adapter.state_machine:
                    from core.call_state import CallStage
                    agent.adapter.state_machine.call_state.transition_to(CallStage.END)
                
                # Free up outbound port immediately by disconnecting room
                asyncio.create_task(room.disconnect())
                break
                
    except asyncio.CancelledError:
        logger.debug("AMD worker task cancelled")
    except Exception as e:
        logger.error("Error in AMD parallel worker: %s", e, exc_info=True)
    finally:
        await audio_stream.aclose()
        logger.info("AMD classification worker finished")


async def entrypoint(ctx: JobContext):
    logger.info(f"New connection: room={ctx.room.name}")
    
    # Retrieve prewarmed components
    shared = ctx.proc.userdata.get("shared_components")
    if not shared:
        logger.warning("Shared components not found in process userdata. Initializing now...")
        config = VoiceConfig()
        shared = SharedComponents(config)
        await shared.initialize()
        ctx.proc.userdata["shared_components"] = shared
        
    call_id = str(uuid.uuid4())
    latency_recorder = LatencyRecorder(call_id)
    latency_recorder.mark("call_start")
    
    # Configure low-latency turn handling
    turn_handling = TurnHandlingOptions(
        turn_detection="vad",
        endpointing={
            "mode": "fixed",
            "min_delay": shared.config.turn_min_delay,
            "max_delay": shared.config.turn_max_delay,
        },
        interruption={
            "enabled": True,
            "mode": "adaptive",
            "resume_false_interruption": True,
            "false_interruption_timeout": 1.0,
        },
        preemptive_generation=shared.config.preemptive_generation,
    )
    
    # Initialize the AgentSession
    session = AgentSession(
        stt=shared.stt,
        llm=shared.llm,
        tts=shared.tts,
        vad=shared.vad,
        turn_handling=turn_handling,
    )
    
    agent = DanaAgent(shared, latency_recorder)
    agent.room = ctx.room
    
    # Set up session event hooks
    @session.on("user_state_changed")
    def on_user_state_changed(ev):
        state_str = str(ev.new_state).lower()
        old_state_str = str(ev.old_state).lower()
        if "speaking" in state_str:
            logger.info("User speaking started")
            latency_recorder.mark("user_speech_start")
            
            # Check for barge-in interruption
            if session.agent_state == "speaking" or getattr(session.agent_state, "value", None) == "speaking":
                latency_recorder.mark("barge_in_detected")
                logger.info("Barge-in detected - interrupting agent response")
                agent.interrupted_current_turn = True
                import time
                agent.interrupted_at = time.perf_counter()
                
                # Interrupt the session
                if asyncio.iscoroutinefunction(session.interrupt):
                    asyncio.create_task(session.interrupt())
                else:
                    session.interrupt()
                latency_recorder.mark("barge_in_stopped_audio")
                
            # Cancellable fallback task cancellation on barge-in
            if getattr(agent, "fallback_disconnect_task", None):
                agent.fallback_disconnect_task.cancel()
                agent.fallback_disconnect_task = None
            agent.should_disconnect = False
                
        elif "listening" in state_str or "idle" in state_str:
            if "speaking" in old_state_str:
                logger.info("User speaking stopped")
                latency_recorder.mark("user_speech_end")
                dur = latency_recorder.duration("user_speech_start", "user_speech_end")
                if dur is not None:
                    agent.stt_seconds += (dur / 1000.0)
                
    @session.on("agent_state_changed")
    def on_agent_state_changed(ev):
        state_str = str(ev.new_state).lower()
        old_state_str = str(ev.old_state).lower()
        import time
        if "speaking" in state_str:
            latency_recorder.mark("agent_speech_started")
            agent.agent_speech_started_time = time.perf_counter()
        elif "speaking" in old_state_str:
            latency_recorder.mark("agent_speech_stopped")
            if getattr(agent, "interrupted_current_turn", False):
                # Calculate portion spoken before interruption
                start_time = getattr(agent, "agent_speech_started_time", None)
                stop_time = getattr(agent, "interrupted_at", None) or time.perf_counter()
                if start_time:
                    dur = stop_time - start_time
                    # 15 characters per second is a good standard speech speed
                    chars_spoken = min(len(getattr(agent, "current_turn_response", "")), int(dur * 15.0))
                    agent.tts_characters += max(0, chars_spoken)
            else:
                # Fully spoken without interruption
                agent.tts_characters += len(getattr(agent, "current_turn_response", ""))
            
            # Reset flags
            agent.interrupted_current_turn = False
            agent.current_turn_response = ""

            if getattr(agent, "should_disconnect", False):
                logger.info("Agent stopped speaking and should_disconnect is True. Disconnecting...")
                if getattr(agent, "fallback_disconnect_task", None):
                    agent.fallback_disconnect_task.cancel()
                    agent.fallback_disconnect_task = None
                asyncio.create_task(ctx.room.disconnect())
            
    @session.on("user_input_transcribed")
    def on_user_input_transcribed(event):
        if event.is_final:
            latency_recorder.mark("transcript_final")
            
    # Connect to room (audio only)
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    
    # Track listener for AMD
    def start_amd_for_track(track):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            if not getattr(agent, "_amd_started", False):
                agent._amd_started = True
                logger.info("Starting AMD parallel worker for track: %s", track.sid)
                asyncio.create_task(run_amd_worker(track, session, agent, ctx.room))

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track: rtc.Track, publication: rtc.TrackPublication, participant: rtc.RemoteParticipant):
        start_amd_for_track(track)
    
    # Wait for participant
    participant = await ctx.wait_for_participant()
    latency_recorder.mark("participant_joined")
    logger.info(f"Participant joined: {participant.identity}")

    for publication in participant.track_publications.values():
        if publication.track:
            start_amd_for_track(publication.track)
    
    # Resolve campaign_id from room metadata, participant metadata, or lead profile in database
    campaign_id = None
    import json
    if ctx.room and ctx.room.metadata:
        try:
            data = json.loads(ctx.room.metadata)
            if isinstance(data, dict):
                campaign_id = data.get("campaign_id") or data.get("campaignId")
        except Exception:
            pass

    if not campaign_id and participant and participant.metadata:
        try:
            data = json.loads(participant.metadata)
            if isinstance(data, dict):
                campaign_id = data.get("campaign_id") or data.get("campaignId")
        except Exception:
            pass

    if not campaign_id and participant.identity:
        try:
            lead_data = await shared.repository.get_lead_by_phone(participant.identity)
            if lead_data:
                campaign_id = lead_data.get("campaign_id")
        except Exception as e:
            logger.error(f"Failed to fetch lead campaign_id: {e}")

    # Register call in context registry
    from speech.context_registry import register_call, update_call_stage
    register_call(call_id, campaign_id)
    update_call_stage(call_id, "OPENING")

    # Update endpointing options if adaptive mode is enabled
    if shared.config.endpoint_mode == "adaptive":
        from speech.endpoint_tuner import get_endpoint_delays, safe_update_endpointing
        min_d, max_d = get_endpoint_delays("OPENING")
        safe_update_endpointing(session, min_d, max_d)

    # Attach session to agent
    agent.session = session
    
    # Instantiate per-call adapter freshly
    agent.adapter = LiveKitRuntimeAdapter(
        call_id=call_id,
        phone_number=participant.identity or "unknown",
        project_root=Path(__file__).resolve().parent,
        prompt_loader=shared.prompt_loader,
        objection_classifier=shared.objection_classifier,
        objection_policy=shared.objection_policy,
        context_builder=shared.context_builder,
        action_policy=shared.action_policy,
        tool_registry=shared.tool_registry,
        compliance_filter=shared.compliance_filter,
        output_validator=shared.output_validator,
        pii_redactor=shared.pii_redactor,
        repository=shared.repository,
    )
    
    # Start AgentSession with RoomOptions
    await session.start(
        room=ctx.room,
        agent=agent,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(enabled=True),
            audio_output=room_io.AudioOutputOptions(enabled=True),
            video_input=room_io.VideoInputOptions(enabled=False),
            text_input=room_io.TextInputOptions(enabled=False),
        ),
    )
    
    # Store the audio source for the direct FFI background playback
    if hasattr(session, "_room_io") and session._room_io:
        audio_output = session._room_io.audio_output
        if hasattr(audio_output, "_audio_source"):
            import tts_service
            tts_service.active_audio_source = audio_output._audio_source
            audio_output._bypass_main_loop = True
            logger.info("Direct audio source registered in tts_service and main-loop bypass enabled.")
            
    # Emit call.session_started event
    from integrations.crm_webhooks import emit_crm_event_async
    lead_prof = agent.adapter.state_machine.lead.to_summary_dict() if agent.adapter else None
    await emit_crm_event_async(
        "call.session_started",
        repository=shared.repository,
        call_id=call_id,
        lead_id=lead_prof.get("lead_id") if lead_prof else None,
        campaign_id=campaign_id,
        phone_e164=participant.identity,
        lead_profile=lead_prof
    )

    # Speak greeting depending on opening_mode
    if shared.config.opening_mode == "immediate" and shared.config.opening_line:
        latency_recorder.mark("greeting_started")
        logger.info(f"Speaking opening line: {shared.config.opening_line}")
        await session.say(shared.config.opening_line)
        from core.call_state import CallStage
        agent.adapter.state_machine.call_state.transition_to(CallStage.INTEREST_CHECK)
    elif shared.config.opening_mode == "wait_for_user":
        logger.info("Opening mode: wait_for_user — agent will not speak first")
    else:
        logger.info(f"Opening mode: {shared.config.opening_mode} (opening line empty) — agent is silent")
    
    try:
        # Loop until room disconnected
        while ctx.room.is_connected():
            await asyncio.sleep(1.0)
    finally:
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
            repository=shared.repository,
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
            raw_turns = await shared.repository._store.query("call_turns", {"call_id": call_id})
            raw_turns.sort(key=lambda t: t.get("turn_number", 0))
            
            turns_data = []
            for t in raw_turns:
                turns_data.append(QACallTurn(
                    speaker="agent" if t.get("speaker") == "agent" else "prospect",
                    text=t.get("text", ""),
                    stage=t.get("stage", ""),
                    timestamp=parse_dt(t.get("timestamp") or t.get("created_at")) or datetime.now(timezone.utc)
                ))
            
            # Load tools
            raw_tools = await shared.repository._store.query("tool_events", {"call_id": call_id})
            tool_events_data = [dict(t) for t in raw_tools]

            # Reconstruct CallRecord
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
            await shared.repository.save_qa_report(
                call_id=call_id,
                overall_score=scorecard.overall_score,
                grade=scorecard.grade,
                scores=scorecard.scores,
                issues=scorecard.issues,
                recommendations=[]
            )

            # If score is too low or grade is F, emit qa.failed
            if scorecard.overall_score < 7.0 or scorecard.grade == "F":
                await emit_crm_event_async(
                    "qa.failed",
                    repository=shared.repository,
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

            # Retrieve existing call record to preserve fields (like dry_run)
            existing_call = await shared.repository.get_call_record(call_id)
            is_dry_run = existing_call.get("dry_run", False) if existing_call else False

            await shared.repository.save_call(
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
            voice_lower = shared.config.tts_voice.lower()
            if "eleven" in voice_lower:
                tts_prov = "elevenlabs"
            elif "openai" in voice_lower:
                tts_prov = "openai"
                
            await calculate_and_save_costs(
                repository=shared.repository,
                call_id=call_id,
                campaign_id=campaign_id,
                stt_provider=shared.config.stt_provider,
                stt_seconds=agent.stt_seconds,
                llm_model=shared.config.llm_model,
                prompt_tokens=agent.prompt_tokens,
                completion_tokens=agent.completion_tokens,
                tts_provider=tts_prov,
                tts_characters=agent.tts_characters,
                telephony_provider="telnyx",
                telephony_seconds=duration,
                dry_run=is_dry_run,
                llm_tokens_estimated=True
            )
            await save_outcome_for_call(shared.repository, call_id, campaign_id, outcome, cost=0.0)
            
            # Cleanup call routing context/health state
            try:
                from routing.model_router import ModelRouter
                ModelRouter.cleanup_call_routing(call_id)
            except Exception as re:
                logger.error(f"Failed to cleanup routing state: {re}")

            # Mark call finished in CampaignPacer
            try:
                from dialer.pacing import CampaignPacer
                pacer = CampaignPacer(shared.repository)
                await pacer.mark_call_finished(campaign_id, call_id)
            except Exception as pe:
                logger.error(f"Failed to mark call finished in campaign pacer: {pe}")
        except Exception as ce:
            logger.error(f"Failed to update call record or save metrics: {ce}")

        # Update lead queue status in database based on actual outcome
        try:
            if lead_id and shared.repository:
                from datetime import datetime, timezone, timedelta
                from dialer.retry_policy import RetryPolicy
                
                if outcome == "voicemail":
                    campaign_rec = await shared.repository.get_campaign(campaign_id)
                    lead_rec = await shared.repository.get_lead(lead_id)
                    if campaign_rec and lead_rec:
                        attempts = lead_rec.get("attempts", 1)
                        retry_after = RetryPolicy.get_retry_after("voicemail", campaign_rec, attempts, datetime.now(timezone.utc))
                        await shared.repository.release_lead_lock(
                            lead_id=lead_id,
                            reason="transient_call_failure",
                            retry_after=retry_after,
                            status_override="failed"
                        )
                elif outcome == "dnc":
                    lead_rec = await shared.repository.get_lead(lead_id)
                    phone = lead_rec.get("phone_number") or lead_rec.get("phone_e164") or participant.identity
                    await shared.repository.mark_lead_dnc(
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
                    await shared.repository.mark_lead_callback(
                        lead_id=lead_id,
                        callback_time=cb_time
                    )
        except Exception as ue:
            logger.error(f"Failed to update lead status at session end: {ue}")

        # Emit call.completed exactly once at the very end of the call cycle
        await emit_crm_event_async(
            "call.completed",
            repository=shared.repository,
            call_id=call_id,
            lead_id=lead_id,
            campaign_id=campaign_id,
            phone_e164=participant.identity,
            outcome=outcome,
            lead_profile=lead_prof
        )


def graceful_startup_integrations(repository: Repository, poll_interval: float = 10.0) -> None:
    """Graceful startup hook to start integrations outbox worker.
    
    TODO: Call this upon daemon startup / LiveKit entrypoint initialization
    to ensure the restart-safe outbox worker is running.
    """
    logger.info("Graceful startup initiated. Starting background outbox drain worker...")
    from integrations.crm_webhooks import start_webhook_outbox_worker
    start_webhook_outbox_worker(repository, poll_interval=poll_interval)


async def graceful_shutdown(repository: Optional[Repository] = None, timeout: float = 10.0) -> None:
    """Graceful shutdown hook for Integrations and webhook dispatcher.
    
    TODO: Wire this into the process signal handlers or LiveKit worker shutdown callbacks
    to ensure webhooks are cleanly flushed and drained on daemon exit.
    """
    logger.info("Graceful shutdown initiated. Stopping outbox worker and draining webhook dispatcher...")
    from integrations.crm_webhooks import stop_webhook_outbox_worker, flush_pending_webhooks
    stop_webhook_outbox_worker()
    await flush_pending_webhooks(timeout=timeout)
    if repository is not None:
        await repository.close()



def prewarm(proc: JobProcess):
    """
    Prewarm the worker process:
    Called once when the worker starts to pre-load all models into GPU memory.
    """
    logger.info("Prewarming worker process - loading STT, TTS, and VAD...")
    
    async def _prewarm():
        config = VoiceConfig()
        shared = SharedComponents(config)
        await shared.initialize()
        proc.userdata["shared_components"] = shared
        logger.info("Prewarm complete - components cached")
        
    asyncio.run(_prewarm())


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        ),
    )
