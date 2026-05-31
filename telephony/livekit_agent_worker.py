import os
import sys
import uuid
import logging
import asyncio
from datetime import datetime, timezone
from typing import Any, Optional, Dict, List, Tuple
from pydantic import BaseModel, Field

from storage.repository import Repository
from core.agent_runtime import AgentRuntime

# Setup standard logging
logger = logging.getLogger("telephony.agent_worker")

class DependencyStatusDict(dict):
    """Dict wrapper that supports unpacking for backwards compatibility: ok, err = check_worker_dependencies()"""
    def __iter__(self):
        return iter([self.get("ready", False), self.get("error")])


class WorkerDependencyStatus(BaseModel):
    """Detailed dependency and environment check status."""
    ready: bool
    status: str  # ready|dependencies_missing|env_missing|provider_missing|runtime_missing|not_enabled
    missing_packages: List[str] = Field(default_factory=list)
    missing_env: List[str] = Field(default_factory=list)
    missing_provider_config: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    next_steps: List[str] = Field(default_factory=list)
    
    # Verification Checklist
    livekit_agents_installed: bool = False
    livekit_plugins_namespace_available: bool = False
    openai_plugin_available: bool = False
    silero_vad_plugin_available: bool = False
    agent_runtime_available: bool = False
    required_env_present: bool = False
    error: Optional[str] = None


class LiveKitAgentWorkerConfig(BaseModel):
    """Configuration settings for the LiveKit agent worker session."""
    livekit_url: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    room_prefix: str = "dana"
    worker_enabled: bool = False
    agent_name: str = "Dana"
    greeting_enabled: bool = True
    greeting_text: Optional[str] = "Hi, this is Dana with American Beneficiary. I’m calling about final expense information you recently requested."
    stt_provider: str = "openai"
    llm_provider: str = "agent_runtime"
    tts_provider: str = "openai"
    vad_provider: str = "silero"
    metadata: Dict[str, Any] = Field(default_factory=dict)


def audit_worker_status() -> WorkerDependencyStatus:
    """Audit all dependencies, required env, and core imports for the voice worker."""
    missing_packages = []
    missing_env = []
    missing_provider_config = []
    warnings = []
    next_steps = []
    
    livekit_agents_installed = False
    livekit_plugins_namespace_available = False
    openai_plugin_available = False
    silero_vad_plugin_available = False
    agent_runtime_available = False
    required_env_present = False
    error_msg = None

    # 1. Package / dependency checks
    try:
        import livekit
        import livekit.agents
        import livekit.api
        livekit_agents_installed = True
    except ImportError as e:
        missing_packages.append("livekit")
        missing_packages.append("livekit-api")
        missing_packages.append("livekit-agents")
        error_msg = str(e) if not error_msg else error_msg

    try:
        import livekit.plugins
        livekit_plugins_namespace_available = True
    except ImportError as e:
        missing_packages.append("livekit.plugins")
        error_msg = str(e) if not error_msg else error_msg

    try:
        import livekit.plugins.openai
        openai_plugin_available = True
    except ImportError as e:
        missing_packages.append("livekit-plugins-openai")
        error_msg = str(e) if not error_msg else error_msg

    try:
        import livekit.plugins.silero
        silero_vad_plugin_available = True
    except ImportError as e:
        missing_packages.append("livekit-plugins-silero")
        error_msg = str(e) if not error_msg else error_msg

    # 2. Runtime import checks
    try:
        from core.agent_runtime import AgentRuntime
        agent_runtime_available = True
    except ImportError as e:
        error_msg = f"AgentRuntime import failed: {e}"

    # 3. Required environment variables checks
    required_keys = ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"]
    for key in required_keys:
        if not os.environ.get(key):
            missing_env.append(key)
    
    required_env_present = len(missing_env) == 0
    worker_enabled = os.environ.get("DANA_AGENT_WORKER_ENABLED") == "true"

    # 4. LLM / STT / TTS Provider credentials check
    # Since we default to OpenAI for LLM/STT/TTS, require OPENAI_API_KEY if using those providers
    openai_key = os.environ.get("OPENAI_API_KEY")
    if not openai_key:
        missing_provider_config.append("OPENAI_API_KEY")

    # 5. Determine overall status and details
    if missing_packages:
        status_str = "dependencies_missing"
        warnings.append(f"Worker dependencies are missing: {error_msg}")
        next_steps.append("Install required agent packages: pip install -r requirements.txt")
    elif not agent_runtime_available:
        status_str = "runtime_missing"
        warnings.append(f"AgentRuntime failed to load: {error_msg}")
        next_steps.append("Ensure core modules and dependencies are importable")
    elif not required_env_present:
        status_str = "env_missing"
        warnings.append(f"Missing required LiveKit connection environment variables: {', '.join(missing_env)}")
        next_steps.append(f"Provide environment values for: {', '.join(missing_env)}")
    elif missing_provider_config:
        status_str = "provider_missing"
        error_msg = "LLM/STT/TTS provider configuration missing."
        warnings.append("LLM/STT/TTS provider configuration missing (OPENAI_API_KEY is not set).")
        next_steps.append("Set OPENAI_API_KEY environment variable")
    elif not worker_enabled:
        status_str = "not_enabled"
        warnings.append("DANA_AGENT_WORKER_ENABLED is not set to 'true'.")
        next_steps.append("Set DANA_AGENT_WORKER_ENABLED=true to enable worker room dispatch")
    else:
        status_str = "ready"
        next_steps.append("Worker is ready to run. Start daemon using python scripts/run_livekit_agent_worker.py")

    ready = (status_str == "ready")

    return WorkerDependencyStatus(
        ready=ready,
        status=status_str,
        missing_packages=missing_packages,
        missing_env=missing_env,
        missing_provider_config=missing_provider_config,
        warnings=warnings,
        next_steps=next_steps,
        livekit_agents_installed=livekit_agents_installed,
        livekit_plugins_namespace_available=livekit_plugins_namespace_available,
        openai_plugin_available=openai_plugin_available,
        silero_vad_plugin_available=silero_vad_plugin_available,
        agent_runtime_available=agent_runtime_available,
        required_env_present=required_env_present,
        error=error_msg
    )


def check_worker_dependencies() -> dict:
    """Verify that worker dependencies are installed. Returns a dict compatible with backward unpacking."""
    status = audit_worker_status()
    return DependencyStatusDict(status.model_dump())


def build_worker_config_from_env() -> LiveKitAgentWorkerConfig:
    """Build LiveKitAgentWorkerConfig from environment variables with safe defaults."""
    prefix = os.environ.get("DANA_LIVEKIT_ROOM_PREFIX") or os.environ.get("LIVEKIT_ROOM_PREFIX") or "dana"
    enabled = os.environ.get("DANA_AGENT_WORKER_ENABLED") == "true"
    agent_name = os.environ.get("DANA_AGENT_NAME") or "Dana"
    greeting_text = os.environ.get("DANA_OPENING_LINE") or "Hi, this is Dana with American Beneficiary. I’m calling about final expense information you recently requested."
    
    # STT/LLM/TTS Providers
    stt_p = os.environ.get("DANA_STT_PROVIDER") or "openai"
    llm_p = os.environ.get("DANA_LLM_PROVIDER") or "agent_runtime"
    tts_p = os.environ.get("DANA_TTS_VOICE")
    if tts_p and ("openai" in tts_p.lower() or "alloy" in tts_p.lower() or "shimmer" in tts_p.lower()):
        tts_p = "openai"
    else:
        tts_p = "openai"  # default standard fallback for cloud worker

    return LiveKitAgentWorkerConfig(
        livekit_url=os.environ.get("LIVEKIT_URL"),
        api_key=os.environ.get("LIVEKIT_API_KEY"),
        api_secret=os.environ.get("LIVEKIT_API_SECRET"),
        room_prefix=prefix,
        worker_enabled=enabled,
        agent_name=agent_name,
        greeting_text=greeting_text,
        stt_provider=stt_p,
        llm_provider=llm_p,
        tts_provider=tts_p,
        vad_provider="silero"
    )


def build_initial_session_state(room_name: str, participant_identity: str | None = None) -> dict:
    """Build initial dict state for tracking conversation turns and durations."""
    # Try resolving call_id from room name suffix
    call_id = str(uuid.uuid4())
    if room_name and "-" in room_name:
        parts = room_name.split("-")
        if len(parts[-1]) >= 8:
            # Suffix from room creation
            call_id = parts[-1]
    
    return {
        "room_name": room_name,
        "participant_identity": participant_identity,
        "call_id": call_id,
        "turns": [],
        "stage": "OPENING",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "ended_at": None,
        "duration_seconds": 0.0,
        "outcome": "ended"
    }


async def log_agent_turn(session_state: dict, text: str, repository: Repository) -> None:
    """Log the agent's turn to both memory state and the repository database."""
    timestamp = datetime.now(timezone.utc).isoformat()
    turn_num = len(session_state["turns"]) + 1
    turn = {
        "speaker": "agent",
        "text": text,
        "timestamp": timestamp,
        "turn_number": turn_num,
        "stage": session_state.get("stage", "OPENING")
    }
    session_state["turns"].append(turn)
    
    call_id = session_state.get("call_id")
    if call_id:
        try:
            await repository.save_call_turn(
                call_id=call_id,
                turn_number=turn_num,
                speaker="agent",
                text=text,
                stage=session_state.get("stage", "OPENING")
            )
        except Exception as e:
            logger.error(f"Failed to log agent turn to database: {e}")


async def log_user_turn(session_state: dict, text: str, repository: Repository) -> None:
    """Log the user's turn to both memory state and the repository database."""
    timestamp = datetime.now(timezone.utc).isoformat()
    turn_num = len(session_state["turns"]) + 1
    turn = {
        "speaker": "prospect",
        "text": text,
        "timestamp": timestamp,
        "turn_number": turn_num,
        "stage": session_state.get("stage", "OPENING")
    }
    session_state["turns"].append(turn)
    
    call_id = session_state.get("call_id")
    if call_id:
        try:
            await repository.save_call_turn(
                call_id=call_id,
                turn_number=turn_num,
                speaker="prospect",
                text=text,
                stage=session_state.get("stage", "OPENING")
            )
        except Exception as e:
            logger.error(f"Failed to log user turn to database: {e}")


async def generate_agent_response(user_text: str, session_state: dict, runtime: AgentRuntime) -> str:
    """Submit user utterance to AgentRuntime with compliance filter checking."""
    
    # 1. Provide an OpenAI-based LLM chat dispatcher function
    async def chat_fn(instructions: str) -> str:
        try:
            from livekit.plugins import openai as lk_openai
            llm = lk_openai.LLM()
            chat_ctx = llm.ChatContext()
            
            # System instructions
            chat_ctx.messages.append(llm.ChatMessage(role="system", content=instructions))
            
            # Dialogue history
            for t in session_state.get("turns", []):
                role = "user" if t["speaker"] == "prospect" else "assistant"
                chat_ctx.messages.append(llm.ChatMessage(role=role, content=t["text"]))
            
            # Append current turn
            chat_ctx.messages.append(llm.ChatMessage(role="user", content=user_text))
            
            stream = llm.chat(chat_ctx=chat_ctx)
            response_text = ""
            async for chunk in stream:
                content = chunk.choices[0].delta.content if chunk.choices else ""
                if content:
                    response_text += content
            return response_text
        except Exception as e:
            logger.error(f"vLLM/OpenAI client chat failed: {e}")
            return "Got it. Let me think about that."

    # 2. Run turn inside the runtime pipeline (which runs compliance filter)
    res = await runtime.process_turn(user_text, chat_fn)
    
    # Update stage in state
    session_state["stage"] = res.stage
    if res.should_end_call:
        session_state["outcome"] = "ended"
        
    return res.agent_response


async def export_completed_session_if_possible(session_state: dict, repository: Repository) -> None:
    """Submit completed call session turns payload to post-call exporter."""
    if os.environ.get("DANA_ENABLE_POST_CALL_TRAINING_EXPORT") != "true":
        return
    if not session_state.get("turns"):
        return
        
    try:
        from training.post_call_exporter import PostCallExporter, PostCallExportConfig
        
        ended = datetime.now(timezone.utc)
        started = datetime.fromisoformat(session_state["started_at"])
        duration = (ended - started).total_seconds()
        
        payload = {
            "call_id": session_state.get("call_id"),
            "room_name": session_state.get("room_name"),
            "participant_identity": session_state.get("participant_identity"),
            "turns": session_state.get("turns"),
            "started_at": session_state["started_at"],
            "ended_at": ended.isoformat(),
            "duration_seconds": duration,
            "outcome": session_state.get("outcome", "ended")
        }
        
        run_sync = os.environ.get("DANA_RUN_SYNC_TRAINING_INTAKE") == "true"
        config = PostCallExportConfig(
            enabled=True,
            run_intake_after_export=True,
            intake_sync=run_sync,
            fail_silently=True,
        )
        
        exporter = PostCallExporter(repository=repository)
        if run_sync:
            await exporter.safe_export_completed_call(payload, config)
        else:
            asyncio.create_task(exporter.safe_export_completed_call(payload, config))
    except Exception as e:
        logger.error(f"Failed to export completed session: {e}")


async def run_room_session(ctx: Any, config: LiveKitAgentWorkerConfig) -> None:
    """Low-level dispatch method managing room connection, greeting, VAD, and transcription."""
    from livekit.agents import AutoSubscribe, AgentSession, room_io, TurnHandlingOptions
    from livekit.plugins import openai as lk_openai
    from livekit.plugins import silero
    
    logger.info(f"Connecting to room: name={ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    
    # Wait for the caller participant
    participant = await ctx.wait_for_participant()
    logger.info(f"Participant joined identity={participant.identity}")
    
    # Initialise shared components and repository
    from voice_config import VoiceConfig
    from main import SharedComponents
    
    vcfg = VoiceConfig()
    shared = SharedComponents(vcfg)
    await shared.initialize()
    
    session_state = build_initial_session_state(ctx.room.name, participant.identity)
    
    # Resolve campaign_id
    campaign_id = None
    try:
        lead_data = await shared.repository.get_lead_by_phone(participant.identity)
        if lead_data:
            campaign_id = lead_data.get("campaign_id")
            session_state["campaign_id"] = campaign_id
    except Exception as e:
        logger.error(f"Failed to lookup lead campaign_id: {e}")

    # Set up runtime
    from core.state_machine import StateMachine
    from core.lead_profile import LeadProfile
    from core.call_state import CallState
    
    lead_prof = LeadProfile(
        call_id=session_state["call_id"],
        lead_phone_e164=participant.identity or "unknown",
        campaign_id=campaign_id
    )
    state_machine = StateMachine(lead_prof, CallState())
    
    from safety.call_stop_policy import CallStopPolicy
    call_stop_policy = CallStopPolicy()
    
    runtime = AgentRuntime(
        prompt_loader=shared.prompt_loader,
        state_machine=state_machine,
        objection_classifier=shared.objection_classifier,
        objection_policy=shared.objection_policy,
        context_builder=shared.context_builder,
        action_policy=shared.action_policy,
        tool_registry=shared.tool_registry,
        compliance_filter=shared.compliance_filter,
        output_validator=shared.output_validator,
        call_stop_policy=call_stop_policy,
        pii_redactor=shared.pii_redactor,
        repository=shared.repository
    )

    # Initialize livekit agents components
    turn_handling = TurnHandlingOptions(
        turn_detection="vad",
        endpointing={
            "mode": "fixed",
            "min_delay": vcfg.turn_min_delay,
            "max_delay": vcfg.turn_max_delay,
        },
        interruption={
            "enabled": True,
            "mode": "adaptive",
            "resume_false_interruption": True,
            "false_interruption_timeout": 1.0,
        }
    )

    session = AgentSession(
        stt=shared.stt,
        llm=shared.llm,
        tts=shared.tts,
        vad=shared.vad,
        turn_handling=turn_handling
    )

    # Define minimal dummy agent class compatible with SDK requirements
    from livekit.agents import Agent as LkAgent
    class SimpleAgent(LkAgent):
        def __init__(self):
            super().__init__(instructions="")
            self.llm = shared.llm
            self.tts = shared.tts
            self.stt = shared.stt

        async def llm_node(self, chat_ctx, tools, model_settings):
            # Read last turn and process it
            user_msg = chat_ctx.messages[-1] if chat_ctx.messages else None
            user_text = user_msg.content if user_msg else ""
            if user_text:
                await log_user_turn(session_state, user_text, shared.repository)
                agent_resp = await generate_agent_response(user_text, session_state, runtime)
                await log_agent_turn(session_state, agent_resp, shared.repository)
                
                # yield to TTS
                from livekit.agents.llm import ChatChunk, Choice, ChoiceDelta
                yield ChatChunk(choices=[Choice(delta=ChoiceDelta(content=agent_resp))])

    agent_instance = SimpleAgent()
    await session.start(
        room=ctx.room,
        agent=agent_instance,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(enabled=True),
            audio_output=room_io.AudioOutputOptions(enabled=True),
            video_input=room_io.VideoInputOptions(enabled=False),
            text_input=room_io.TextInputOptions(enabled=False),
        )
    )

    # Speak Greeting if enabled
    if config.greeting_enabled and config.greeting_text:
        logger.info(f"Greeting participant with text: '{config.greeting_text}'")
        await session.say(config.greeting_text)
        await log_agent_turn(session_state, config.greeting_text, shared.repository)

    try:
        # Loop until disconnect
        while ctx.room.is_connected():
            await asyncio.sleep(1.0)
    finally:
        logger.info(f"Room session closed for call {session_state['call_id']}")
        await export_completed_session_if_possible(session_state, shared.repository)


async def start_worker(config: LiveKitAgentWorkerConfig) -> None:
    """Check configuration and start the job worker loop."""
    from livekit.agents import WorkerOptions, cli
    
    # Validate status on startup
    status = audit_worker_status()
    if not status.ready:
        raise RuntimeError(f"Cannot start worker. Check failed: {status.status}. Errors: {status.error}")

    logger.info("Starting LiveKit Agent Worker Job Dispatch...")
    
    async def entrypoint_cb(ctx: Any):
        await run_room_session(ctx, config)

    opts = WorkerOptions(
        entrypoint_fnc=entrypoint_cb,
    )
    
    if len(sys.argv) == 1:
        sys.argv.append("dev")
        
    cli.run_app(opts)


def run_worker():
    """Fallback run method called by scripts/run_livekit_agent_worker.py"""
    config = build_worker_config_from_env()
    asyncio.run(start_worker(config))


if __name__ == "__main__":
    run_worker()
