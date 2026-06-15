"""
Sovereign Voice Stack - Main Agent Entry Point
Ultra-low-latency Voice AI using LiveKit Agents Framework.
"""

import asyncio
import logging
import time
import os
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import AsyncIterable, Optional

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    AutoSubscribe,
    JobContext,
    JobProcess,
    JobRequest,
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

_active_repository: Optional[Repository] = None

def register_signal_handlers():
    import signal
    
    def handle_exit(sig, frame):
        logger.info(f"Signal {sig} received. Initiating graceful shutdown...")
        global _active_repository
        
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(graceful_shutdown(_active_repository))
                return
        except RuntimeError:
            pass
            
        asyncio.run(graceful_shutdown(_active_repository))

    try:
        signal.signal(signal.SIGINT, handle_exit)
        signal.signal(signal.SIGTERM, handle_exit)
        logger.info("Signal handlers registered successfully.")
    except Exception as e:
        logger.warning(f"Could not register signal handlers: {e}")

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
    config = None
    stt = None
    tts = None
    llm = None
    vad = None
    router = None
    prompt_loader = None
    objection_classifier = None
    objection_policy = None
    context_builder = None
    action_policy = None
    tool_registry = None
    compliance_filter = None
    output_validator = None
    pii_redactor = None
    repository = None

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
        # Premium live mode startup safety validations
        if self.config.voice_mode == "premium_live":
            if not self.config.enable_streaming_response:
                raise RuntimeError("premium_live voice mode requires DANA_ENABLE_STREAMING_RESPONSE=true")
            if self.config.enable_audio_filters:
                raise RuntimeError("premium_live voice mode requires DANA_ENABLE_AUDIO_FILTERS=false")
            if self.config.allow_mock_tts:
                raise RuntimeError("premium_live voice mode requires DANA_ALLOW_MOCK_TTS=false")
            
            # Check credentials & provider config
            tts_provider = self.config.tts_provider.strip().lower()
            if tts_provider == "elevenlabs":
                el_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
                if not el_key or el_key.lower() in ("replace_me", "replace-me", ""):
                    raise RuntimeError("premium_live with elevenlabs requires ELEVENLABS_API_KEY to be set")
                el_voice = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
                if not el_voice or el_voice.lower() in ("replace_me", "replace-me", ""):
                    raise RuntimeError("premium_live with elevenlabs requires ELEVENLABS_VOICE_ID to be set")
            elif tts_provider == "openai":
                oa_key = os.getenv("OPENAI_API_KEY", "").strip()
                if not oa_key or oa_key.lower() in ("replace_me", "replace-me", ""):
                    raise RuntimeError("premium_live with openai requires OPENAI_API_KEY to be set")
                oa_voice = os.getenv("OPENAI_TTS_VOICE", "").strip()
                if not oa_voice or oa_voice.lower() in ("replace_me", "replace-me", ""):
                    raise RuntimeError("premium_live with openai requires OPENAI_TTS_VOICE to be set")
            else:
                raise RuntimeError(f"premium_live requires a cloud provider (elevenlabs or openai), got '{self.config.tts_provider}'")

            llm_routing = self.config.llm_routing_mode.strip().lower()
            if llm_routing == "cloud":
                oa_key = os.getenv("OPENAI_API_KEY", "").strip()
                if not oa_key or oa_key.lower() in ("replace_me", "replace-me", ""):
                    raise RuntimeError("DANA_LLM_ROUTING_MODE=cloud requires OPENAI_API_KEY to be set")

            stt_routing = self.config.stt_routing_mode.strip().lower()
            if stt_routing == "cloud":
                dg_key = os.getenv("DEEPGRAM_API_KEY", "").strip()
                if not dg_key or dg_key.lower() in ("replace_me", "replace-me", ""):
                    raise RuntimeError("premium_live with stt_routing_mode=cloud requires DEEPGRAM_API_KEY to be set")

        from routing.model_router import ModelRouter
        self.router = ModelRouter(self.config)

        # 1. Initialize STT
        self.stt = create_stt(self.config)
        if hasattr(self.stt, "initialize"):
            await self.stt.initialize()
            
        # 2. Initialize local TTS & run healthcheck
        from tts_service import LocallyHostedKokoro, TTSConfig
        from voice.tts_healthcheck import run_tts_healthcheck
        from config.runtime_env import is_production, get_runtime_env, allow_mock_tts
        from voice.voice_provider_registry import get_voice_profile
        
        tts_config = TTSConfig(
            voice=self.config.tts_voice,
            speed=self.config.tts_speed,
        )
        local_tts = LocallyHostedKokoro(tts_config)
        
        local_tts_healthy = False
        health_error = None
        try:
            await local_tts.initialize()
            health_result = await run_tts_healthcheck(local_tts)
            if health_result.is_healthy:
                local_tts_healthy = True
            else:
                health_error = health_result.error_message
        except Exception as e:
            health_error = str(e)
        
        # Initialize cloud TTS lazily
        cloud_tts = None
        cloud_tts_required = self.config.tts_routing_mode == "cloud"
        cloud_tts_allowed = self.config.tts_routing_mode != "local" or self.config.allow_cloud_tts_fallback
        
        # Determine cloud provider based on config
        cloud_provider = self.config.tts_provider.strip().lower()
        if cloud_provider == "local":
            voice_lower = self.config.tts_voice.lower()
            if "openai" in voice_lower:
                cloud_provider = "openai"
            else:
                cloud_provider = "elevenlabs"

        has_cloud_tts_creds = False
        if cloud_provider == "openai":
            has_cloud_tts_creds = bool(os.getenv("OPENAI_API_KEY"))
        elif cloud_provider == "elevenlabs":
            has_cloud_tts_creds = bool(os.getenv("ELEVENLABS_API_KEY"))
            
        if cloud_tts_required and not has_cloud_tts_creds:
            raise RuntimeError(f"Cloud TTS mode requested but credentials for provider '{cloud_provider}' are missing.")
            
        if cloud_tts_allowed and has_cloud_tts_creds:
            try:
                if cloud_provider == "openai":
                    from livekit.plugins.openai import TTS as OpenAI_TTS
                    openai_voice = os.getenv("OPENAI_TTS_VOICE", "alloy").strip()
                    openai_model = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts").strip()
                    logger.info(f"Initializing OpenAI TTS with voice={openai_voice}, model={openai_model}")
                    cloud_tts = OpenAI_TTS(voice=openai_voice, model=openai_model)
                else:
                    from livekit.plugins import elevenlabs
                    el_voice_id = os.getenv("ELEVENLABS_VOICE_ID", "hpp4J3VqNfWAUOO0d1Us").strip()
                    el_model_id = os.getenv("ELEVENLABS_MODEL_ID", "eleven_turbo_v2_5").strip()
                    logger.info(f"Initializing ElevenLabs TTS with voice_id={el_voice_id}, model_id={el_model_id}")
                    cloud_tts = elevenlabs.TTS(
                        voice_id=el_voice_id,
                        model=el_model_id,
                        api_key=os.getenv("ELEVENLABS_API_KEY")
                    )
            except Exception as e:
                logger.error(f"Failed to initialize cloud TTS provider {cloud_provider}: {e}")
                if cloud_tts_required:
                    raise RuntimeError(f"Cloud {cloud_provider} TTS requested but failed to load: {e}")

        # Post-healthcheck routing safety logic
        if not local_tts_healthy:
            if is_production():
                if cloud_tts is not None:
                    logger.warning(f"Local TTS failed healthcheck: {health_error}. Routing to cloud TTS fallback.")
                    self.router.local_tts_available = False
                else:
                    logger.error(f"FATAL: Local TTS failed healthcheck: {health_error}. No cloud TTS configured/available.")
                    raise RuntimeError(f"Local TTS healthcheck failed and cloud TTS is not available: {health_error}")
            else:
                logger.warning(f"Local TTS healthcheck failed in non-production mode: {health_error}. Proceeding with fallback mode.")
                if cloud_tts is not None:
                    self.router.local_tts_available = False

        # Get voice profile info
        profile = get_voice_profile(self.config.voice_profile)
        profile_name = self.config.voice_profile
        
        # Determine active TTS mode for logs
        if not local_tts_healthy:
            active_tts_mode = "cloud (fallback)" if cloud_tts else "failed"
        else:
            active_tts_mode = self.config.tts_routing_mode
            
        # Production safety status for logs
        env = get_runtime_env()
        if env == "production":
            if allow_mock_tts():
                safety_status = "WARNING: Mock TTS allowed in production"
            else:
                safety_status = "production-safe (strict)"
        else:
            safety_status = f"development/test bypass ({env})"

        # ACTIVE_TTS_PROVIDER resolution
        active_tts_provider = "local"
        if not local_tts_healthy:
            if cloud_tts:
                active_tts_provider = cloud_provider
            else:
                active_tts_provider = "failed"
        else:
            if self.config.tts_routing_mode == "cloud" and cloud_tts:
                active_tts_provider = cloud_provider

        from tts_service import MockKokoroModel, MockKokoro
        mock_tts_active = isinstance(local_tts._model, (MockKokoroModel, MockKokoro))

        logger.info(f"LOCAL_TTS_AVAILABLE={'true' if local_tts_healthy else 'false'}")
        logger.info(f"CLOUD_TTS_AVAILABLE={'true' if cloud_tts is not None else 'false'}")
        logger.info(f"ACTIVE_TTS_PROVIDER={active_tts_provider}")
        logger.info(f"MOCK_TTS_ACTIVE={'true' if mock_tts_active else 'false'}")

        logger.info(
            f"\n"
            f"============================================================\n"
            f"DANA WORKER STARTUP - TTS STACK STATUS\n"
            f"------------------------------------------------------------\n"
            f"  Environment:             {env}\n"
            f"  Safety Status:           {safety_status}\n"
            f"  Active TTS Mode:         {active_tts_mode}\n"
            f"  Voice Profile Name:      {profile_name}\n"
            f"  Profile Provider:        {profile.provider if profile else 'unknown'}\n"
            f"  Profile Voice Name:      {profile.voice_name if profile else 'unknown'}\n"
            f"  Profile Quality:         {profile.quality_tier if profile else 'unknown'}\n"
            f"  Profile Cost:            {profile.estimated_cost_tier if profile else 'unknown'}\n"
            f"  Allowed in Production:   {profile.allowed_in_production if profile else 'unknown'}\n"
            f"  Local TTS Status:        {'HEALTHY' if local_tts_healthy else 'FAILED'}\n"
            f"============================================================"
        )

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
        global _active_repository
        _active_repository = self.repository
        graceful_startup_integrations(self.repository)

        logger.info("All shared components initialized successfully")


class DanaAgent(Agent):
    """
    Subclass of livekit.agents.Agent implementing our phone-optimized Dana personality
    and wrapping LLM & TTS streaming nodes with latency recorder hooks.
    """
    def __init__(self, shared: SharedComponents, latency_recorder: LatencyRecorder):
        instructions = load_instructions(shared.config.agent_prompt_path)
        super().__init__(
            instructions=instructions,
            stt=shared.stt,
            llm=shared.llm,
            tts=shared.tts,
            vad=shared.vad,
        )
        self.prompt_loader = getattr(shared, "prompt_loader", None)
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
                # DNC, disqualified, wrong number, callback scheduled, or cold transfer -> disconnect after TTS finishes speaking
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
        tts_stream = self.tts.stream()
        first_text = True
        
        async def push_text_loop():
            nonlocal first_text
            try:
                async for chunk in text:
                    if chunk and first_text:
                        first_text = False
                        self._latency_recorder.mark("tts_first_text")
                        if "greeting_tts_started" in self._latency_recorder.events:
                            self._latency_recorder.mark("second_turn_tts_first_text")
                    tts_stream.push_text(chunk)
                tts_stream.flush()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Error in tts_node push loop: {e}")
            
        push_task = asyncio.create_task(push_text_loop())
        
        first_audio = True
        try:
            async for ev in tts_stream:
                if first_audio:
                    first_audio = False
                    self._latency_recorder.mark("tts_first_audio")
                    self._latency_recorder.mark("first_audio_published")
                    if "greeting_tts_started" in self._latency_recorder.events:
                        self._latency_recorder.mark("second_turn_tts_first_audio")
                        self._latency_recorder.mark("second_turn_audio_published")
                yield ev.frame
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
            if not room.isconnected() or getattr(agent, "is_voicemail", False):
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


async def suggest_lessons_from_call(call_record, scorecard, repository) -> None:
    """Extract suggested lessons (TrainingNotes) from high-performing call records."""
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


async def entrypoint(ctx: JobContext):
    from ops.worker_capacity import WorkerCapacity
    WorkerCapacity.increment_calls()
    logger.info(f"New connection: room={ctx.room.name}")
    
    # Retrieve prewarmed components
    shared = ctx.proc.userdata.get("shared_components")
    if not shared:
        logger.warning("Shared components not found in process userdata. Initializing now...")
        config = VoiceConfig()
        shared = SharedComponents(config)
        await shared.initialize()
        ctx.proc.userdata["shared_components"] = shared

    # Connect to room (audio only)
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    
    # Wait for participant
    participant = await ctx.wait_for_participant()
    logger.info(f"Participant joined: {participant.identity}")

    # Resolve call identity and metadata from room metadata and participant metadata
    call_id = None
    lead_id = None
    campaign_id = None

    import json
    if ctx.room and ctx.room.metadata:
        try:
            data = json.loads(ctx.room.metadata)
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
            lead_data = await shared.repository.get_lead_by_phone(participant.identity)
            if lead_data:
                campaign_id = lead_data.get("campaign_id")
                if not lead_id:
                    lead_id = lead_data.get("id") or lead_data.get("lead_id")
        except Exception as e:
            logger.error(f"Failed to fetch lead campaign_id: {e}")

    if not campaign_id:
        campaign_id = "unknown"

    latency_recorder = LatencyRecorder(call_id)
    latency_recorder.mark("call_start")
    latency_recorder.mark("participant_joined")
    latency_recorder.mark("room_joined")
    
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
        preemptive_generation={"enabled": shared.config.preemptive_generation},
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
    if hasattr(shared.stt, "bind"):
        session._stt = shared.stt.bind(session, agent)
    session._vad = shared.vad.bind(session, agent)
    
    # Set up session event hooks
    @session.on("user_state_changed")
    def on_user_state_changed(ev):
        state_str = str(ev.new_state).lower()
        old_state_str = str(ev.old_state).lower()
        if "speaking" in state_str:
            logger.info("User speaking started")
            latency_recorder.mark("stt_speech_start_hook_called")
            # Mark user_speech_resumed if user speaks again after interruption before agent starts speaking
            if getattr(agent, "interrupted_current_turn", False) and latency_recorder.events.get("user_speech_end"):
                latency_recorder.mark("user_speech_resumed")

            latency_recorder.mark("user_speech_start")
            
            # Check for barge-in interruption
            if session.agent_state == "speaking" or getattr(session.agent_state, "value", None) == "speaking":
                # Do not allow interruption during the OPENING stage
                from speech.context_registry import get_current_call_stage
                stage = get_current_call_stage() or "OPENING"
                if stage != "OPENING":
                    latency_recorder.mark("barge_in_detected")
                    logger.info("Barge-in detected - interrupting agent response")
                    agent.interrupted_current_turn = True
                    agent.interrupted_at = time.perf_counter()
                    
                    # Interrupt the session
                    if asyncio.iscoroutinefunction(session.interrupt):
                        asyncio.create_task(session.interrupt())
                    else:
                        session.interrupt()
                    latency_recorder.mark("barge_in_stopped_audio")
                else:
                    logger.info("Barge-in ignored during OPENING stage (greeting playback)")
                
            # Cancellable fallback task cancellation on barge-in
            if getattr(agent, "fallback_disconnect_task", None):
                agent.fallback_disconnect_task.cancel()
                agent.fallback_disconnect_task = None
            agent.should_disconnect = False
                
        elif "listening" in state_str or "idle" in state_str:
            if "speaking" in old_state_str:
                logger.info("User speaking stopped")
                latency_recorder.mark("stt_speech_end_hook_called")
                latency_recorder.mark("user_speech_end")
                dur = latency_recorder.duration("user_speech_start", "user_speech_end")
                if dur is not None:
                    agent.stt_seconds += (dur / 1000.0)
                    # Detect false interruption
                    if getattr(agent, "interrupted_current_turn", False):
                        interrupted_dur = time.perf_counter() - getattr(agent, "interrupted_at", 0)
                        if interrupted_dur < 0.8:
                            latency_recorder.mark("false_interruption_detected")
                            logger.info("False interruption detected (duration since interrupt < 800ms)")
                
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
            latency_recorder.mark("agent_audio_stopped")
            if getattr(agent, "interrupted_current_turn", False):
                # Calculate portion spoken before interruption
                start_time = getattr(agent, "agent_speech_started_time", None)
                stop_time = getattr(agent, "interrupted_at", None) or time.perf_counter()
                if start_time:
                    dur = stop_time - start_time
                    # 15 characters per second is a good standard speech speed
                    chars_spoken = min(len(getattr(agent, "current_turn_response", "")), int(dur * 15.0))
                    agent.tts_characters += max(0, chars_spoken)
                
                # Save turn interruption metrics to DB
                stage = "opening"
                if agent.adapter and agent.adapter.state_machine:
                    stage = agent.adapter.state_machine.call_state.current_stage.value
                asyncio.create_task(latency_recorder.save_metrics(shared.repository, stage))
            else:
                # Fully spoken without interruption
                agent.tts_characters += len(getattr(agent, "current_turn_response", ""))
            
            # Reset flags
            agent.current_turn_response = ""
            agent.agent_speech_started_time = None

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
        else:
            enable_semantic = os.getenv("DANA_ENABLE_SEMANTIC_TURN_DETECTION", "false").strip().lower() in ("true", "1", "yes")
            if enable_semantic and shared.config.endpoint_mode == "adaptive":
                from speech.semantic_turn_detector import SemanticTurnDetector
                from speech.context_registry import get_current_call_stage
                from speech.endpoint_tuner import safe_update_endpointing
                
                detector = SemanticTurnDetector()
                stage = get_current_call_stage() or "OPENING"
                partial_text = ""
                if hasattr(event, "text") and event.text:
                    partial_text = event.text
                elif hasattr(event, "alternatives") and event.alternatives:
                    partial_text = event.alternatives[0].text
                
                if partial_text:
                    res = detector.process_transcript(partial_text, stage=stage)
                    safe_update_endpointing(session, res.recommended_min_delay, res.recommended_max_delay)
            
    # Track listener for AMD
    def start_amd_for_track(track):
        if os.getenv("DANA_CONTROLLED_LIVE_TEST", "false").lower() in ("true", "1", "yes"):
            logger.info("Bypassing AMD parallel worker for track %s (controlled live test enabled)", track.sid)
            return
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            if not getattr(agent, "_amd_started", False):
                agent._amd_started = True
                logger.info("Starting AMD parallel worker for track: %s", track.sid)
                asyncio.create_task(run_amd_worker(track, session, agent, ctx.room))

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track: rtc.Track, publication: rtc.TrackPublication, participant: rtc.RemoteParticipant):
        logger.info(f"[TRACK_SUBSCRIBED_LOG] participant={participant.identity} kind={track.kind} source={publication.source} sid={track.sid}")
        start_amd_for_track(track)

    for publication in participant.track_publications.values():
        logger.info(f"[EXISTING_TRACK_LOG] participant={participant.identity} source={publication.source} track_present={publication.track is not None}")
        if publication.track:
            start_amd_for_track(publication.track)
    
    # Register call in context registry
    from speech.context_registry import register_call, update_call_stage
    register_call(call_id, campaign_id)
    update_call_stage(call_id, "OPENING")

    # Update endpointing options if adaptive mode is enabled
    if shared.config.endpoint_mode == "adaptive":
        from speech.endpoint_tuner import get_endpoint_delays, safe_update_endpointing
        min_d, max_d = get_endpoint_delays("OPENING")
        safe_update_endpointing(session, min_d, max_d)

    # Attach session to agent (handled automatically by session.start in LiveKit Agents v1.5.x)
    # agent.session = session
    
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

    # Set lead details directly on agent.adapter.lead
    if agent.adapter and agent.adapter.lead:
        agent.adapter.lead.lead_id = lead_id
        agent.adapter.lead.campaign_id = campaign_id
        agent.adapter.lead.lead_phone_e164 = participant.identity or "unknown"
    
    # Configure interruption profile callback on stage transition
    if hasattr(agent.adapter, "state_machine") and agent.adapter.state_machine:
        call_state = agent.adapter.state_machine.call_state
        
        def apply_interruption_profile(stage):
            from speech.interruption_profiles import get_profile_for_stage
            profile = get_profile_for_stage(stage, shared.config)
            
            # Update telemetry event if enabled
            if shared.config.record_interruption_telemetry:
                import time
                latency_recorder.events[f"profile_applied_{stage.value}"] = time.perf_counter()
                
            # Update VAD streams
            if hasattr(shared.vad, "update_profile"):
                shared.vad.update_profile(profile)
                
            # Update context registry
            update_call_stage(call_id, stage.value)
            
        call_state._transition_callbacks = getattr(call_state, "_transition_callbacks", [])
        call_state._transition_callbacks.append(apply_interruption_profile)
        
        # Apply initial profile for current stage
        apply_interruption_profile(call_state.current_stage)
    
    # Start AgentSession with RoomOptions
    await session.start(
        room=ctx.room,
        agent=agent,
        room_options=room_io.RoomOptions(
            audio_input=True,
            audio_output=True,
            video_input=False,
            text_input=False,
        ),
    )
    
    session.repository = shared.repository
    
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
        latency_recorder.mark("greeting_tts_started")
        logger.info(f"Speaking opening line: {shared.config.opening_line}")
        await session.say(shared.config.opening_line)
        latency_recorder.mark("greeting_audio_published")
        from core.call_state import CallStage
        agent.adapter.state_machine.call_state.transition_to(CallStage.INTEREST_CHECK)
    elif shared.config.opening_mode == "wait_for_user":
        logger.info("Opening mode: wait_for_user — agent will not speak first")
    else:
        logger.info(f"Opening mode: {shared.config.opening_mode} (opening line empty) — agent is silent")
    
    try:
        # Loop until room disconnected
        while ctx.room.isconnected():
            await asyncio.sleep(1.0)
    finally:
        from ops.worker_capacity import WorkerCapacity
        WorkerCapacity.decrement_calls()
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
                    timestamp=parse_dt(t.get("timestamp") or t.get("created_at")) or datetime.now(timezone.utc),
                    interrupted=t.get("interrupted", False)
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

            # Generate suggested training notes/lessons from high-performing calls
            if scorecard.overall_score >= 9.0 or scorecard.grade == "A":
                try:
                    await suggest_lessons_from_call(
                        call_record=call_record,
                        scorecard=scorecard,
                        repository=shared.repository
                    )
                    logger.info("Successfully generated suggested training notes from high-performing call")
                except Exception as e:
                    logger.error(f"Failed to generate suggested lessons from high-performing call: {e}", exc_info=True)

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

            # Save final latency metrics, including counters and rates
            try:
                final_stage = agent.adapter.state_machine.call_state.current_stage.value if agent.adapter else "end"
                await latency_recorder.save_metrics(shared.repository, final_stage)
            except Exception as e:
                logger.error(f"Failed to save final latency metrics: {e}")

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
                
            # Save model costs (legacy call_costs records)
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

            # Save turn latency spans & GPU runtime allocations based on latency recorder
            try:
                from metrics.gpu_cost_allocator import allocate_gpu_cost
                from routing.model_router import ModelRouter

                lat_dict = latency_recorder.to_dict()
                durs = lat_dict.get("durations", {})
                now = datetime.now(timezone.utc)
                
                # STT latency span & GPU allocation
                stt_ms = durs.get("stt_latency")
                if stt_ms is not None:
                    stt_start = now - timedelta(milliseconds=stt_ms)
                    await shared.repository.save_turn_latency_span(
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
                            repository=shared.repository,
                            call_id=call_id,
                            component="stt",
                            runtime_seconds=float(stt_ms) / 1000.0
                        )
                
                # LLM latency span & GPU allocation
                llm_ms = durs.get("llm_duration")
                if llm_ms is not None:
                    llm_start = now - timedelta(milliseconds=llm_ms)
                    await shared.repository.save_turn_latency_span(
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
                            repository=shared.repository,
                            call_id=call_id,
                            component="llm",
                            runtime_seconds=float(llm_ms) / 1000.0
                        )

                # TTS latency span & GPU allocation
                tts_ms = durs.get("tts_synthesis_start_latency")
                if tts_ms is not None:
                    tts_start = now - timedelta(milliseconds=tts_ms)
                    await shared.repository.save_turn_latency_span(
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
                            repository=shared.repository,
                            call_id=call_id,
                            component="tts",
                            runtime_seconds=float(tts_ms) / 1000.0
                        )
            except Exception as e:
                logger.error(f"Error saving turn latency spans/GPU allocations: {e}")

            # Reconcile all call costs and compute breakdown
            reconciled_total_cost = 0.0
            try:
                from metrics.provider_cost_reconciler import reconcile_call_costs
                reconciled = await reconcile_call_costs(
                    repository=shared.repository,
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

            await save_outcome_for_call(shared.repository, call_id, campaign_id, outcome, cost=reconciled_total_cost)
            
            # Rollup campaign costs
            try:
                from metrics.cost_per_outcome import recompute_campaign_rollups
                await recompute_campaign_rollups(shared.repository, campaign_id)
            except Exception as e:
                logger.error(f"Error recomputing campaign rollups: {e}")
            
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


async def request_fnc(req: JobRequest) -> None:
    logger.info(f"Received job request {req.id}")
    from ops.worker_capacity import WorkerCapacity
    if not WorkerCapacity.has_capacity():
        logger.warning(f"Rejecting job request {req.id} due to capacity limits")
        await req.reject()
    else:
        logger.info(f"Accepting job request {req.id}")
        await req.accept()


if __name__ == "__main__":
    import os
    register_signal_handlers()
    num_idle = int(os.getenv("DANA_NUM_IDLE_PROCESSES", "1"))
    logger.info(f"Starting agent worker with num_idle_processes={num_idle}")
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            request_fnc=request_fnc,
            num_idle_processes=num_idle,
        ),
    )
