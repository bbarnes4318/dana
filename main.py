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

from dana.config.voice_config import VoiceConfig
from latency_metrics import LatencyRecorder

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
if os.getenv("DANA_ENABLE_EXPERIMENTAL_AUDIO_MONKEYPATCH", "false").strip().lower() == "true":
    try:
        import livekit.agents.voice.room_io._output as room_io_output
        import time
        
        original_forward_audio = room_io_output._ParticipantAudioOutput._forward_audio
        original_wait_for_playout = room_io_output._ParticipantAudioOutput._wait_for_playout
        
        async def patched_forward_audio(self):
            if getattr(self, "_bypass_main_loop", False):
                self._playing = True
                try:
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
                finally:
                    self._playing = False
            else:
                await original_forward_audio(self)

        async def patched_wait_for_playout(self):
            if getattr(self, "_bypass_main_loop", False):
                wait_for_interruption = asyncio.create_task(self._interrupted_event.wait())

                async def _wait_buffered_audio() -> None:
                    while not self._audio_buf.empty() or getattr(self, "_playing", False):
                        if not self._playback_enabled.is_set():
                            await self._playback_enabled.wait()
                        await asyncio.sleep(0.02)

                wait_for_playout = asyncio.create_task(_wait_buffered_audio())
                await asyncio.wait(
                    [wait_for_playout, wait_for_interruption],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                interrupted = self._interrupted_event.is_set()
                pushed_duration = self._pushed_duration

                if interrupted:
                    queued_duration = 0
                    while not self._audio_buf.empty():
                        queued_duration += self._audio_buf.recv_nowait().duration

                    pushed_duration = max(pushed_duration - queued_duration, 0)
                    wait_for_playout.cancel()
                else:
                    wait_for_interruption.cancel()

                self._pushed_duration = 0
                self._interrupted_event.clear()
                self._first_frame_event.clear()
                self.on_playback_finished(playback_position=pushed_duration, interrupted=interrupted)
            else:
                await original_wait_for_playout(self)
                
        room_io_output._ParticipantAudioOutput._forward_audio = patched_forward_audio
        room_io_output._ParticipantAudioOutput._wait_for_playout = patched_wait_for_playout
        logger.info("Successfully monkeypatched _ParticipantAudioOutput._forward_audio and _wait_for_playout for event-loop bypass.")
    except Exception as e:
        logger.error(f"Failed to monkeypatch _ParticipantAudioOutput: {e}")
else:
    logger.info("LiveKit audio monkeypatch is disabled by default.")

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
    Falls back to `_DEFAULT_INSTRUCTIONS` if the file is missing, empty, or unreadable.
    """
    if not path:
        logger.warning("No DANA_AGENT_PROMPT_PATH configured — using default instructions")
        return _DEFAULT_INSTRUCTIONS

    resolved = Path(path)
    if not resolved.is_absolute():
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
        from dana.providers.provider_registry import registry as provider_registry
        from dana.providers.routing import RoutingEngine
        
        self.repository = Repository()
        global _active_repository
        _active_repository = self.repository
        graceful_startup_integrations(self.repository)

        # 1. Resolve prompt loader and other runtime requirements
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

        # 2. Evaluate and select active provider stack using the routing engine
        self.routing_engine = RoutingEngine(self.config, provider_registry)
        self.active_stack = await self.routing_engine.select_provider_stack()

        # Extract and assign active LiveKit-compatible provider instances
        await self.reinitialize_for_job()
        self.telephony = self.active_stack["telephony"]

    async def reinitialize_for_job(self):
        """Re-create client wrapper instances for the current event loop/job context."""
        logger.info("Re-initializing client wrappers for the current job context...")
        self.llm = self.active_stack["llm"].create_client()
        self.tts = self.active_stack["tts"].synthesize_stream()
        self.stt = self.active_stack["stt"].transcribe_stream()
        self.vad = self.active_stack["vad"].create_detector()

        if hasattr(self.stt, "initialize"):
            try:
                res = self.stt.initialize()
                if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                    await res
            except TypeError:
                pass
        if hasattr(self.tts, "initialize"):
            try:
                res = self.tts.initialize()
                if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                    await res
            except TypeError:
                pass

        # 3. Log the active provider stack exactly as required on startup
        logger.info(f"ACTIVE_PROVIDER_MODE: {self.active_stack['mode'].upper()}")
        logger.info(f"ACTIVE_LLM_PROVIDER: {self.active_stack['llm'].name.upper()}")
        logger.info(f"ACTIVE_LLM_MODEL: {self.config.llm_model}")
        logger.info(f"ACTIVE_TTS_PROVIDER: {self.active_stack['tts'].name.upper()}")
        logger.info(f"ACTIVE_TTS_VOICE: {self.config.tts_voice}")
        logger.info(f"ACTIVE_STT_PROVIDER: {self.active_stack['stt'].name.upper()}")
        logger.info(f"ACTIVE_STT_MODEL: {self.config.stt_model}")
        logger.info(f"ACTIVE_VAD_PROVIDER: {self.active_stack['vad'].name.upper()}")
        logger.info(f"ACTIVE_TELEPHONY_PROVIDER: {self.active_stack['telephony'].name.upper()}")
        logger.info(f"LLM_HEALTH_STATUS: {str(self.active_stack['health']['llm']).upper()}")
        logger.info(f"TTS_HEALTH_STATUS: {str(self.active_stack['health']['tts']).upper()}")
        logger.info(f"STT_HEALTH_STATUS: {str(self.active_stack['health']['stt']).upper()}")
        logger.info(f"VAD_HEALTH_STATUS: {str(self.active_stack['health']['vad']).upper()}")
        logger.info(f"TELEPHONY_HEALTH_STATUS: {str(self.active_stack['health']['telephony']).upper()}")
        logger.info(f"ESTIMATED_COST_PER_CONNECTED_MINUTE: {self.active_stack['estimated_cost_per_minute']:.6f}")

        logger.info("All shared components initialized successfully")

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
    else:
        await shared.reinitialize_for_job()

    # Connect to room (audio only)
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    logger.info("CALL_ROOM_CONNECTED")
    
    # Wait for participant
    participant = await ctx.wait_for_participant()
    logger.info("CALL_PARTICIPANT_JOINED")
    logger.info(f"Participant joined: {participant.identity}")

    # Delegate call session logic entirely to VoiceSession
    from dana.runtime.voice_session import VoiceSession
    session = VoiceSession(ctx, shared)
    try:
        await session.run(participant)
    finally:
        from ops.worker_capacity import WorkerCapacity
        WorkerCapacity.decrement_calls()

def graceful_startup_integrations(repository: Repository, poll_interval: float = 10.0) -> None:
    """Graceful startup hook to start integrations outbox worker."""
    logger.info("Graceful startup initiated. Starting background outbox drain worker...")
    from integrations.crm_webhooks import start_webhook_outbox_worker
    start_webhook_outbox_worker(repository, poll_interval=poll_interval)

async def graceful_shutdown(repository: Optional[Repository] = None, timeout: float = 10.0) -> None:
    """Graceful shutdown hook for Integrations and webhook dispatcher."""
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
    agent_port = int(os.getenv("LIVEKIT_AGENT_PORT", "8085"))
    logger.info(f"Starting agent worker with num_idle_processes={num_idle} port={agent_port}")
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            request_fnc=request_fnc,
            num_idle_processes=num_idle,
            initialize_process_timeout=60,
            port=agent_port,
        ),
    )
