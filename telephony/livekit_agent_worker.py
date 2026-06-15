import os
import sys

# Safety fallback loading
try:
    from config.env_loader import load_environment
    from config.runtime_env import get_runtime_env
    load_environment()
except ImportError:
    from pathlib import Path
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from config.env_loader import load_environment
    from config.runtime_env import get_runtime_env
    load_environment()

import uuid
import logging
import asyncio
from livekit import rtc
from datetime import datetime, timezone, timedelta
from typing import Any, Optional, Dict, List, Tuple
from pydantic import BaseModel, Field

from storage.repository import Repository
from core.agent_runtime import AgentRuntime

try:
    from livekit.plugins import openai as lk_openai
    from livekit.plugins import silero
    from livekit.plugins import deepgram
except ImportError:
    lk_openai = None
    silero = None
    deepgram = None

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
    greeting_text: Optional[str] = "Hello?"
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
        missing_packages.extend(["livekit", "livekit-api", "livekit-agents"])
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

    # 3. Required environment variables checks via centralized resolver
    env = get_runtime_env()
    
    if not env["livekit_url"]:
        missing_env.append("LIVEKIT_URL")
    if not env["livekit_api_key"]:
        missing_env.append("LIVEKIT_API_KEY")
    if not env["livekit_api_secret"]:
        missing_env.append("LIVEKIT_API_SECRET")
    
    required_env_present = len(missing_env) == 0
    worker_enabled = env["worker_enabled"]

    # 4. LLM / STT / TTS Provider credentials check
    llm_is_cloud = env["llm_routing_mode"] == "cloud"
    tts_is_cloud = env["tts_routing_mode"] == "cloud"
    fallback_enabled = env["allow_cloud_llm_fallback"] or env["allow_cloud_tts_fallback"]
    
    needs_openai = llm_is_cloud or tts_is_cloud or fallback_enabled
    if needs_openai and not os.environ.get("OPENAI_API_KEY"):
        missing_provider_config.append("OPENAI_API_KEY")

    # Cloud STT check
    stt_is_cloud = env["stt_routing_mode"] == "cloud"
    cloud_stt_on_failure = env["cloud_stt_on_failure"]
    allow_cloud_stt_poor_line = (os.environ.get("DANA_ALLOW_CLOUD_STT_FOR_POOR_LINE", "").lower() == "true")
    needs_deepgram = stt_is_cloud or cloud_stt_on_failure or allow_cloud_stt_poor_line
    if needs_deepgram and not os.environ.get("DEEPGRAM_API_KEY"):
        missing_provider_config.append("DEEPGRAM_API_KEY")

    # Local LLM base URL check
    if env["llm_routing_mode"] == "local" and not env["vllm_base_url"]:
        missing_provider_config.append("VLLM_BASE_URL")

    # Local TTS checks
    if env["tts_routing_mode"] == "local":
        if not env["kokoro_model_path"]:
            missing_provider_config.append("KOKORO_MODEL_PATH")
        if not env["kokoro_voices_path"]:
            missing_provider_config.append("KOKORO_VOICES_PATH")

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
        error_msg = f"LLM/STT/TTS provider configuration missing: {', '.join(missing_provider_config)}"
        warnings.append(f"LLM/STT/TTS provider configuration missing ({', '.join(missing_provider_config)} is not set).")
        next_steps.append(f"Set environment variables: {', '.join(missing_provider_config)}")
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
    env = get_runtime_env()
    prefix = os.environ.get("DANA_LIVEKIT_ROOM_PREFIX") or os.environ.get("LIVEKIT_ROOM_PREFIX") or "dana"
    enabled = env["worker_enabled"]
    agent_name = os.environ.get("DANA_AGENT_NAME") or "Dana"
    greeting_text = os.environ.get("DANA_OPENING_LINE") or "Hello?"
    
    # STT/LLM/TTS Providers
    stt_p = env["stt_routing_mode"]
    llm_p = env["llm_routing_mode"]
    if llm_p == "local":
        llm_p = "agent_runtime"
    tts_p = env["tts_routing_mode"]

    return LiveKitAgentWorkerConfig(
        livekit_url=env["livekit_url"],
        api_key=env["livekit_api_key"],
        api_secret=env["livekit_api_secret"],
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


async def log_agent_turn(
    session_state: dict,
    text: str,
    repository: Repository,
    compliance_warnings: Optional[List[str]] = None,
    latency_metrics: Optional[Dict[str, Any]] = None
) -> None:
    """Log the agent's turn to both memory state and the repository database."""
    timestamp = datetime.now(timezone.utc).isoformat()
    turn_num = len(session_state["turns"]) + 1
    stage = session_state.get("stage", "OPENING")
    
    turn = {
        "speaker": "agent",
        "text": text,
        "timestamp": timestamp,
        "turn_number": turn_num,
        "stage": stage,
        "call_attempt_id": session_state.get("attempt_id"),
        "campaign_id": session_state.get("campaign_id"),
        "lead_id": session_state.get("lead_id"),
        "livekit_room_name": session_state.get("room_name"),
        "participant_id": session_state.get("participant_id"),
        "compliance_warnings": compliance_warnings or [],
        "latency_metrics": latency_metrics or {},
        "selected_did": session_state.get("selected_did"),
        "caller_id_source": session_state.get("caller_id_source")
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
                stage=stage,
                call_attempt_id=session_state.get("attempt_id"),
                campaign_id=session_state.get("campaign_id"),
                lead_id=session_state.get("lead_id"),
                livekit_room_name=session_state.get("room_name"),
                participant_id=session_state.get("participant_id"),
                compliance_warnings=compliance_warnings or [],
                latency_metrics=latency_metrics or {},
                selected_did=session_state.get("selected_did"),
                caller_id_source=session_state.get("caller_id_source")
            )
        except Exception as e:
            logger.error(f"Failed to log agent turn to database: {e}")


async def log_user_turn(session_state: dict, text: str, repository: Repository) -> None:
    """Log the user's turn to both memory state and the repository database."""
    timestamp = datetime.now(timezone.utc).isoformat()
    turn_num = len(session_state["turns"]) + 1
    stage = session_state.get("stage", "OPENING")
    
    turn = {
        "speaker": "prospect",
        "text": text,
        "timestamp": timestamp,
        "turn_number": turn_num,
        "stage": stage,
        "call_attempt_id": session_state.get("attempt_id"),
        "campaign_id": session_state.get("campaign_id"),
        "lead_id": session_state.get("lead_id"),
        "livekit_room_name": session_state.get("room_name"),
        "participant_id": session_state.get("participant_id"),
        "compliance_warnings": [],
        "latency_metrics": {},
        "selected_did": session_state.get("selected_did"),
        "caller_id_source": session_state.get("caller_id_source")
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
                stage=stage,
                call_attempt_id=session_state.get("attempt_id"),
                campaign_id=session_state.get("campaign_id"),
                lead_id=session_state.get("lead_id"),
                livekit_room_name=session_state.get("room_name"),
                participant_id=session_state.get("participant_id"),
                compliance_warnings=[],
                latency_metrics={},
                selected_did=session_state.get("selected_did"),
                caller_id_source=session_state.get("caller_id_source")
            )
        except Exception as e:
            logger.error(f"Failed to log user turn to database: {e}")


async def generate_agent_response(user_text: str, session_state: dict, runtime: AgentRuntime) -> str:
    """Submit user utterance to AgentRuntime with compliance filter checking."""
    
    def is_user_input_divergent(text: str) -> bool:
        text_lower = text.lower().strip()
        relevant_keywords = [
            "insurance", "expense", "funeral", "burial", "benefit", "coverage", "policy", "premium", "cost", "pay",
            "hello", "hi", "hey", "yes", "no", "ok", "sure", "correct", "wrong", "right", "state", "live", "age",
            "year", "old", "born", "date", "birth", "month", "day", "cov", "die", "death", "health", "illness",
            "medical", "history", "qualify", "program", "plan", "rate", "quote", "price", "dollars", "american",
            "beneficiary", "senior", "elderly", "pension", "retire", "medicare", "medicaid", "social security", "ssi",
            "alex", "dana", "who", "what", "why", "how", "where", "when", "tell", "show", "get", "give", "help"
        ]
        divergent_keywords = [
            "weather", "rain", "sunny", "temperature", "sport", "game", "yankees", "baseball", "football",
            "color", "food", "movie", "song", "joke", "marry", "love", "date", "sing", "dance", "bot", "robot",
            "ai", "computer", "hack", "trump", "biden", "election", "politics", "president", "news", "stock",
            "market", "crypto", "bitcoin", "dog", "cat", "pet", "hobby", "hobbies", "holiday", "vacation"
        ]
        for word in divergent_keywords:
            if word in text_lower:
                return True
        words = text_lower.split()
        if len(words) > 2:
            has_relevant = any(w in text_lower for w in relevant_keywords)
            if not has_relevant:
                return True
        return False

    # 1. Provide an OpenAI-based LLM chat dispatcher function
    async def chat_fn(instructions: str) -> str:
        try:
            from livekit.plugins import openai as lk_openai
            llm = lk_openai.LLM()
            chat_ctx = llm.ChatContext()
            
            # System instructions prefixed with static prompt loader context
            static_prompt = runtime.prompt_loader.build_system_prompt()
            
            # Check for irrelevant or divergent topics
            is_divergent = is_user_input_divergent(user_text)
            if is_divergent:
                combined_prompt = (
                    f"{static_prompt}\n\n"
                    f"### SYSTEM CONTEXT ENFORCEMENT WARNING ###\n"
                    f"The user has introduced an irrelevant or divergent topic. You must NOT discuss this topic.\n"
                    f"Use this explicit internal logic pathway: Acknowledge politely, do not engage in the divergent topic, "
                    f"and gently but firmly redirect the user back to the primary qualifying data points (age, state of residence, "
                    f"and current coverage status).\n"
                    f"Example: 'I hear you, but let's get back to the final expense benefits. To see if you qualify, how old are you?'\n"
                    f"##########################################\n\n"
                    f"{instructions}"
                )
            else:
                combined_prompt = f"{static_prompt}\n\n{instructions}"
                
            chat_ctx.messages.append(llm.ChatMessage(role="system", content=combined_prompt))
            
            # Dialogue history
            for t in session_state.get("turns", []):
                role = "user" if t["speaker"] == "prospect" else "assistant"
                chat_ctx.messages.append(llm.ChatMessage(role=role, content=t["text"]))
            
            # Append current turn
            chat_ctx.messages.append(llm.ChatMessage(role="user", content=user_text))
            
            stream = llm.chat(chat_ctx=chat_ctx, temperature=0.2)
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
    try:
        from training.post_call_exporter import PostCallExporter, PostCallExportConfig
        
        call_id = session_state.get("call_id")
        room_name = session_state.get("room_name")
        turns = session_state.get("turns", [])
        
        # Determine ended_at/duration if not set
        ended_at_str = session_state.get("ended_at")
        if not ended_at_str:
            ended_at_str = datetime.now(timezone.utc).isoformat()
            
        started_at_str = session_state.get("started_at")
        if not started_at_str:
            started_at_str = datetime.now(timezone.utc).isoformat()
            
        payload = {
            "call_id": call_id,
            "started_at": started_at_str,
            "ended_at": ended_at_str,
            "direction": session_state.get("direction", "outbound"),
            "campaign": session_state.get("campaign_id"),
            "prospect_phone": session_state.get("participant_identity") or session_state.get("prospect_phone"),
            "outcome": session_state.get("outcome", "unknown"),
            "transfer_consent": session_state.get("transfer_consent", False),
            "turns": turns,
            "tool_events": session_state.get("tool_events", []),
            "qa": {},
            "metadata": session_state.get("metadata", {
                "exported_by": "worker",
                "exported_at": datetime.now(timezone.utc).isoformat(),
            })
        }
        
        run_intake = (
            os.environ.get("DANA_ENABLE_POST_CALL_TRAINING_EXPORT") == "true"
            or session_state.get("run_intake_after_export")
        )
        run_sync = (
            os.environ.get("DANA_RUN_SYNC_TRAINING_INTAKE") == "true"
            or session_state.get("run_intake_after_export")
        )
        
        config = PostCallExportConfig(
            enabled=True,
            run_intake_after_export=run_intake,
            intake_sync=run_sync,
            fail_silently=False,
        )
        
        exporter = PostCallExporter(repository=repository)
        await exporter.safe_export_completed_call(payload, config)
    except Exception as e:
        logger.error(f"Failed to export completed session: {e}")


async def run_amd_worker(track: rtc.Track, session: any, agent: any, room: rtc.Room):
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
            
            if speech_duration >= 2.5:
                recorder = getattr(agent, "_latency_recorder", None) or getattr(agent, "latency_recorder", None) or getattr(session, "latency_recorder", None)
                markers = list(recorder.events.keys()) if recorder else []
                logger.info("AMD: Voicemail detected (speech_duration=%.2fs >= 2.5s). Reason: continuous speech exceeded voicemail threshold. Current latency markers: %s", speech_duration, markers)
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


async def run_room_session(ctx: Any, config: LiveKitAgentWorkerConfig) -> None:
    """Low-level dispatch method managing room connection, greeting, VAD, and transcription."""
    from livekit.agents import AutoSubscribe, AgentSession, room_io, TurnHandlingOptions
    from livekit.plugins import openai as lk_openai
    from livekit.plugins import silero
    
    logger.info(f"Connecting to room: name={ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    
    # Track listener for AMD
    def start_amd_for_track(track):
        controlled_live = os.getenv("DANA_CONTROLLED_LIVE_TEST", "false").lower() in ("true", "1", "yes")
        enable_amd = os.getenv("DANA_ENABLE_AMD_WORKER", "false").lower() in ("true", "1", "yes")
        if controlled_live and not enable_amd:
            logger.info("Bypassing AMD parallel worker for track %s (controlled live test enabled and DANA_ENABLE_AMD_WORKER is not true)", track.sid)
            return
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            if not getattr(agent_instance, "_amd_started", False):
                agent_instance._amd_started = True
                logger.info("Starting AMD parallel worker for track: %s", track.sid)
                asyncio.create_task(run_amd_worker(track, session, agent_instance, ctx.room))

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track: rtc.Track, publication: rtc.TrackPublication, participant: rtc.RemoteParticipant):
        start_amd_for_track(track)
    
    # Wait for the caller participant
    participant = await ctx.wait_for_participant()
    logger.info(f"Participant joined identity={participant.identity}")

    for publication in participant.track_publications.values():
        if publication.track:
            start_amd_for_track(publication.track)
    
    # Initialise shared components and repository
    from voice_config import VoiceConfig
    from main import SharedComponents
    
    vcfg = VoiceConfig()
    shared = SharedComponents(vcfg)
    await shared.initialize()
    
    # Parse room name to resolve ids
    campaign_id = None
    lead_id = None
    attempt_id = None
    selected_did = None
    caller_id_source = None
    
    room_name = ctx.room.name
    if room_name and room_name.startswith("dana-"):
        parts = room_name.split("-")
        if len(parts) >= 4:
            campaign_id = parts[1]
            lead_id = parts[2]
            attempt_id = parts[3]
            
    # Lookup attempt if not resolved or to get DIDs
    attempt = None
    if attempt_id:
        try:
            attempt = await shared.repository.get_call_attempt(attempt_id)
        except Exception as e:
            logger.error(f"Failed to fetch CallAttempt {attempt_id}: {e}")
            
    if not attempt and room_name:
        try:
            attempts = await shared.repository.query_call_attempts({"livekit_room_name": room_name})
            if attempts:
                attempt = attempts[0]
                attempt_id = attempt.get("id")
                campaign_id = attempt.get("campaign_id")
                lead_id = attempt.get("lead_id")
        except Exception as e:
            logger.error(f"Failed to query CallAttempts: {e}")
            
    if attempt:
        meta = attempt.get("metadata", {})
        selected_did = meta.get("selected_caller_id") or attempt.get("caller_id")
        caller_id_source = meta.get("caller_id_source")
        
    session_state = build_initial_session_state(room_name, participant.identity)
    session_state["campaign_id"] = campaign_id
    session_state["lead_id"] = lead_id
    session_state["attempt_id"] = attempt_id
    session_state["selected_did"] = selected_did
    session_state["caller_id_source"] = caller_id_source
    session_state["livekit_room_name"] = room_name
    session_state["participant_id"] = participant.sid if hasattr(participant, "sid") else participant.identity
    
    # Initialize latency recorder
    from latency_metrics import LatencyRecorder
    latency_recorder = LatencyRecorder(session_state["call_id"])
    latency_recorder.mark("call_start")
    latency_recorder.mark("participant_joined")
    latency_recorder.mark("room_joined")

    # Set up runtime
    from core.state_machine import StateMachine
    from core.lead_profile import LeadProfile
    from core.call_state import CallState
    
    lead_prof = LeadProfile(
        call_id=session_state["call_id"],
        lead_phone_e164=participant.identity or "unknown",
        campaign_id=campaign_id
    )
    state_machine = StateMachine(call_state=CallState(), lead_profile=lead_prof)
    
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
    
    # Wire latency and transcription events
    @session.on("user_state_changed")
    def on_user_state_changed(ev):
        state_str = str(ev.new_state).lower()
        old_state_str = str(ev.old_state).lower()
        if "speaking" in state_str:
            latency_recorder.mark("stt_speech_start_hook_called")
            latency_recorder.mark("user_speech_start")
        elif "listening" in state_str or "idle" in state_str:
            if "speaking" in old_state_str:
                latency_recorder.mark("stt_speech_end_hook_called")
                latency_recorder.mark("user_speech_end")

    @session.on("agent_state_changed")
    def on_agent_state_changed(ev):
        state_str = str(ev.new_state).lower()
        old_state_str = str(ev.old_state).lower()
        import time
        if "speaking" in state_str:
            latency_recorder.mark("agent_speech_started")
            agent_instance.agent_speech_started_time = time.perf_counter()
        elif "speaking" in old_state_str:
            latency_recorder.mark("agent_speech_stopped")
            agent_instance.interrupted_current_turn = False
            agent_instance.current_turn_response = ""
            agent_instance.agent_speech_started_time = None

    @session.on("user_input_transcribed")
    def on_user_input_transcribed(event):
        if event.is_final:
            latency_recorder.mark("stt_final_transcript")
            latency_recorder.mark("transcript_final")
        else:
            latency_recorder.mark("stt_interim_transcript")

    # Define minimal dummy agent class compatible with SDK requirements
    from livekit.agents import Agent as LkAgent
    class SimpleAgent(LkAgent):
        def __init__(self):
            super().__init__(
                instructions="",
                llm=shared.llm,
                tts=shared.tts,
                stt=shared.stt
            )
            self.current_turn_response = ""
            self.agent_speech_started_time = None
            self.interrupted_current_turn = False
            self.interrupted_at = None

        async def llm_node(self, chat_ctx, tools, model_settings):
            latency_recorder.mark("llm_node_entered")
            # Read last turn and process it
            user_msg = chat_ctx.messages[-1] if chat_ctx.messages else None
            user_text = user_msg.content if user_msg else ""
            if user_text:
                latency_recorder.mark("user_text_seen_by_llm_node")
                latency_recorder.mark("llm_request_start")
                await log_user_turn(session_state, user_text, shared.repository)
                latency_recorder.mark("agent_runtime_process_user_turn_started")
                agent_resp = await generate_agent_response(user_text, session_state, runtime)
                self.current_turn_response = agent_resp
                latency_recorder.mark("agent_response_text_created")
                
                # Fetch compliance warnings from runtime events
                compliance_warnings = []
                from core.runtime_events import ValidationFailedEvent
                for event in runtime.events:
                    if isinstance(event, ValidationFailedEvent):
                        compliance_warnings.extend(event.issues)
                
                if compliance_warnings:
                    session_state.setdefault("compliance_warnings", [])
                    session_state["compliance_warnings"].extend(compliance_warnings)
                
                latency_recorder.mark("llm_done")
                lat_dict = latency_recorder.to_dict().get("durations", {})
                
                await log_agent_turn(
                    session_state=session_state,
                    text=agent_resp,
                    repository=shared.repository,
                    compliance_warnings=compliance_warnings,
                    latency_metrics=lat_dict
                )
                
                # yield to TTS
                from livekit.agents.llm import ChatChunk, Choice, ChoiceDelta
                yield ChatChunk(choices=[Choice(delta=ChoiceDelta(content=agent_resp))])

    agent_instance = SimpleAgent()
    if hasattr(shared.stt, "bind"):
        session._stt = shared.stt.bind(session, agent_instance)
    session._vad = shared.vad.bind(session, agent_instance)
    await session.start(
        room=ctx.room,
        agent=agent_instance,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(),
            audio_output=room_io.AudioOutputOptions(),
            video_input=False,
            text_input=False,
        )
    )

    session.repository = shared.repository
    session.session_state = session_state

    # Store the audio source for the direct FFI background playback
    if hasattr(session, "_room_io") and session._room_io:
        audio_output = session._room_io.audio_output
        if hasattr(audio_output, "_audio_source"):
            import tts_service
            tts_service.active_audio_source = audio_output._audio_source
            audio_output._bypass_main_loop = True
            logger.info("Direct audio source registered in tts_service and main-loop bypass enabled.")

    # Speak Greeting if enabled
    if config.greeting_enabled and config.greeting_text:
        logger.info(f"Greeting participant with text: '{config.greeting_text}'")
        latency_recorder.mark("greeting_tts_started")
        await session.say(config.greeting_text)
        latency_recorder.mark("greeting_audio_published")
        await log_agent_turn(session_state, config.greeting_text, shared.repository)

    try:
        # Loop until disconnect
        while ctx.room.isconnected():
            await asyncio.sleep(1.0)
    finally:
        logger.info(f"Room session closed for call {session_state['call_id']}")
        try:
            from telephony.fe_transfer import release_call_agent
            await release_call_agent(session_state["call_id"])
        except Exception as ra_err:
            logger.error("Failed to release call agent: %s", ra_err)
        
        ended_at = datetime.now(timezone.utc)
        started_at = datetime.fromisoformat(session_state["started_at"])
        duration_seconds = int((ended_at - started_at).total_seconds())
        
        # Determine outcome
        outcome = "answered"
        lead_prof = runtime.state_machine.lead
        
        turns = session_state.get("turns", [])
        agent_turns = sum(1 for t in turns if t["speaker"] == "agent")
        prospect_turns = sum(1 for t in turns if t["speaker"] == "prospect")
        
        # Check tool execution results
        transfer_successful = False
        from core.runtime_events import ToolTriggeredEvent
        for event in runtime.events:
            if isinstance(event, ToolTriggeredEvent):
                if event.tool_name in ("feTransfer", "transfer_to_agent") and event.success:
                    transfer_successful = True
                    
        if getattr(agent_instance, "is_voicemail", False):
            outcome = "voicemail"
        elif lead_prof.do_not_call_requested:
            outcome = "dnc"
        elif lead_prof.callback_requested:
            outcome = "callback"
        elif transfer_successful or lead_prof.is_qualified():
            outcome = "transferred"
        elif lead_prof.disqualified_reason:
            outcome = "completed"
        elif prospect_turns >= 1:
            outcome = "completed"
        elif agent_turns >= 1:
            outcome = "answered"
        else:
            outcome = "unknown"
            
        session_state["ended_at"] = ended_at.isoformat()
        session_state["duration_seconds"] = duration_seconds
        session_state["outcome"] = outcome
        
        transcript_lines = []
        for t in turns:
            speaker_label = "Dana" if t["speaker"] == "agent" else "Prospect"
            transcript_lines.append(f"{speaker_label}: {t['text']}")
        transcript_summary = " | ".join(transcript_lines)
        
        # 1. Update LiveCallSession
        try:
            live_session_record = None
            if attempt_id:
                sessions = await shared.repository.query_live_call_sessions({"attempt_id": attempt_id})
                if sessions:
                    live_session_record = sessions[0]
            if not live_session_record:
                sessions = await shared.repository.query_live_call_sessions({"call_id": session_state["call_id"]})
                if sessions:
                    live_session_record = sessions[0]
                    
            if live_session_record:
                live_session_record["status"] = "ended"
                live_session_record["ended_at"] = ended_at.isoformat()
                live_session_record["outcome"] = outcome
                live_session_record["current_stage"] = runtime.state_machine.call_state.current_stage.value
                live_session_record["latest_transcript"] = transcript_summary
                live_session_record["compliance_warnings"] = session_state.get("compliance_warnings", [])
                await shared.repository.save_live_call_session(**live_session_record)
        except Exception as e:
            logger.error(f"Failed to close LiveCallSession in db: {e}")
            
        # 2. Update CallAttempt
        attempt_record = None
        try:
            if attempt_id:
                attempt_record = await shared.repository.get_call_attempt(attempt_id)
                if attempt_record:
                    attempt_record["status"] = "completed"
                    attempt_record["ended_at"] = ended_at.isoformat()
                    attempt_record["duration_seconds"] = duration_seconds
                    attempt_record["outcome"] = outcome
                    attempt_record.setdefault("metadata", {})
                    attempt_record["metadata"]["transcript_summary"] = transcript_summary
                    attempt_record["metadata"]["compliance_warnings"] = session_state.get("compliance_warnings", [])
                    attempt_record["metadata"]["turn_count"] = len(turns)
                    attempt_record["metadata"]["agent_turn_count"] = agent_turns
                    attempt_record["metadata"]["prospect_turn_count"] = prospect_turns
                    
                    await shared.repository.save_call_attempt(**attempt_record)
        except Exception as e:
            logger.error(f"Failed to update CallAttempt in db: {e}")

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
                    cb_time_str = getattr(lead_prof, "callback_time_local", None) or getattr(lead_prof, "callback_time", None)
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
            
        # 3. Handle post-call export and training intake
        export_enabled = (
            os.environ.get("DANA_ENABLE_POST_CALL_TRAINING_EXPORT") == "true"
            or (attempt_record and attempt_record.get("metadata", {}).get("require_post_call_export"))
        )
        run_intake = (
            os.environ.get("DANA_ENABLE_POST_CALL_TRAINING_EXPORT") == "true"
            or (attempt_record and attempt_record.get("metadata", {}).get("run_intake_after_export"))
        )
        
        if export_enabled:
            if not turns:
                error_msg = "Post-call export skipped because no transcript turns were captured."
                logger.warning(error_msg)
                if attempt_record:
                    attempt_record.setdefault("metadata", {})
                    attempt_record["metadata"]["post_call_export_error"] = error_msg
                    await shared.repository.save_call_attempt(**attempt_record)
            else:
                try:
                    from training.post_call_exporter import PostCallExporter, PostCallExportConfig
                    
                    lead_phone = None
                    if lead_id:
                        lead = await shared.repository.get_campaign_lead(lead_id)
                        if lead:
                            lead_phone = lead.get("phone_number")
                            
                    tool_events = []
                    for event in runtime.events:
                        if getattr(event, "event_type", None) == "tool_triggered":
                            tool_events.append({
                                "tool_name": getattr(event, "tool_name", ""),
                                "success": getattr(event, "success", True),
                                "result": getattr(event, "result_message", ""),
                                "timestamp": getattr(event, "timestamp", datetime.now(timezone.utc)).isoformat() if hasattr(event, "timestamp") else None
                            })
                            
                    payload = {
                        "call_id": attempt_id or session_state["call_id"],
                        "started_at": session_state["started_at"],
                        "ended_at": ended_at.isoformat(),
                        "direction": "outbound",
                        "campaign": campaign_id,
                        "prospect_phone": lead_phone or participant.identity,
                        "outcome": outcome,
                        "transfer_consent": bool(lead_prof.transfer_consent_confirmed),
                        "turns": turns,
                        "tool_events": tool_events,
                        "qa": {},
                        "metadata": {
                            "lead_id": lead_id,
                            "exported_by": "worker",
                            "exported_at": datetime.now(timezone.utc).isoformat(),
                        }
                    }
                    
                    run_sync = (
                        os.environ.get("DANA_RUN_SYNC_TRAINING_INTAKE") == "true"
                        or (attempt_record and attempt_record.get("metadata", {}).get("run_intake_after_export"))
                    )
                    
                    config = PostCallExportConfig(
                        enabled=True,
                        run_intake_after_export=run_intake,
                        intake_sync=run_sync,
                        fail_silently=False,
                    )
                    
                    exporter = PostCallExporter(repository=shared.repository)
                    export_res = await exporter.export_completed_call(payload, config)
                    
                    if export_res.exported and export_res.output_path:
                        session_state["post_call_export_path"] = export_res.output_path
                        if attempt_record:
                            attempt_record["post_call_export_path"] = export_res.output_path
                            attempt_record.setdefault("metadata", {})
                            attempt_record["metadata"]["post_call_export_success"] = True
                            attempt_record["metadata"]["intake_run"] = export_res.intake_ran
                            if export_res.intake_result:
                                attempt_record["metadata"]["intake_result"] = export_res.intake_result
                            await shared.repository.save_call_attempt(**attempt_record)
                    else:
                        logger.error(f"Post-call export failed: {export_res.error}")
                except Exception as ex:
                    logger.error(f"Failed to run post-call exporter: {ex}")
                    if attempt_record:
                        attempt_record.setdefault("metadata", {})
                        attempt_record["metadata"]["post_call_export_error"] = str(ex)
                        await shared.repository.save_call_attempt(**attempt_record)



def initialize_process(job_proc: Any) -> None:
    """Pre-import and register plugins on the main thread of the job process."""
    logger.info("Initializing job process: pre-importing plugins on main thread")
    try:
        from livekit.plugins import openai as lk_openai
        from livekit.plugins import silero
        from livekit.plugins import deepgram
    except ImportError as e:
        logger.warning(f"Failed to pre-import plugins in job process: {e}")


async def entrypoint_cb(ctx: Any):
    config = build_worker_config_from_env()
    await run_room_session(ctx, config)


def start_worker(config: LiveKitAgentWorkerConfig) -> None:
    """Check configuration and start the job worker loop."""
    from livekit.agents import WorkerOptions, cli
    
    # Validate status on startup
    status = audit_worker_status()
    if not status.ready:
        raise RuntimeError(f"Cannot start worker. Check failed: {status.status}. Errors: {status.error}")

    logger.info("Starting LiveKit Agent Worker Job Dispatch...")
    
    opts = WorkerOptions(
        entrypoint_fnc=entrypoint_cb,
        prewarm_fnc=initialize_process,
    )
    
    if len(sys.argv) == 1:
        sys.argv.append("dev")
        
    cli.run_app(opts)


def run_worker():
    """Fallback run method called by scripts/run_livekit_agent_worker.py"""
    config = build_worker_config_from_env()
    start_worker(config)


if __name__ == "__main__":
    run_worker()
