"""
Sovereign Voice Stack - Main Agent Entry Point
Ultra-low-latency Voice AI using LiveKit Agents Framework.
"""

import asyncio
import logging
import os
import uuid
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

from voice_config import VoiceConfig
from latency_metrics import LatencyRecorder
from stt_service import create_stt

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class SharedComponents:
    """Heavyweight models and configs cached in the process userdata."""
    def __init__(self, config: VoiceConfig):
        self.config = config
        self.stt = None
        self.tts = None
        self.llm = None
        self.vad = None

    async def initialize(self):
        # 1. Initialize STT
        self.stt = create_stt(self.config)
        if hasattr(self.stt, "initialize"):
            await self.stt.initialize()
            
        # 2. Initialize TTS
        from tts_service import LocallyHostedKokoro, TTSConfig
        tts_config = TTSConfig(
            voice=self.config.tts_voice,
            speed=self.config.tts_speed,
        )
        self.tts = LocallyHostedKokoro(tts_config)
        await self.tts.initialize()
        
        # 3. Initialize LLM (vLLM OpenAI compatible)
        self.llm = lk_openai.LLM(
            model=self.config.llm_model,
            base_url=self.config.vllm_base_url,
            api_key="not-needed",
        )
        
        # 4. Initialize VAD (Silero VAD)
        loop = asyncio.get_event_loop()
        self.vad = await loop.run_in_executor(None, silero.VAD.load)
        logger.info("All shared components initialized successfully")


class DanaAgent(Agent):
    """
    Subclass of livekit.agents.Agent implementing our phone-optimized Dana personality
    and wrapping LLM & TTS streaming nodes with latency recorder hooks.
    """
    def __init__(self, shared: SharedComponents, latency_recorder: LatencyRecorder):
        instructions = (
            "You are Dana, a warm, professional outbound voice AI. "
            "You are having a natural phone conversation. "
            "Follow these rules strictly:\n"
            "- Keep responses very brief: respond in 1 sentence by default.\n"
            "- Ask only one question at a time.\n"
            "- Use natural, spoken language and short acknowledgment phrases (like 'Right', 'Got it', 'Okay').\n"
            "- Use contractions naturally (e.g. I'm, you're, we'll).\n"
            "- NEVER use markdown formatting, bullet points, or lists.\n"
            "- Speak in a friendly, conversational tone."
        )
        super().__init__(instructions=instructions)
        self.llm = shared.llm
        self.tts = shared.tts
        self.stt = shared.stt
        self._config = shared.config
        self._latency_recorder = latency_recorder

    async def on_user_turn_completed(self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage) -> None:
        logger.debug(f"User turn completed: '{new_message.content}'")

    async def llm_node(
        self,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        model_settings: any,
    ) -> AsyncIterable[llm.ChatChunk]:
        self._latency_recorder.mark("llm_request_start")
        
        # Stream response token-by-token using vLLM
        stream = self.llm.chat(
            chat_ctx=chat_ctx,
            temperature=self._config.temperature,
            top_p=self._config.top_p,
            max_tokens=self._config.max_tokens,
            frequency_penalty=0.15,
        )
        
        first_token = True
        async for chunk in stream:
            if first_token:
                first_token = False
                self._latency_recorder.mark("llm_first_token")
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
                
                # Interrupt the session
                if asyncio.iscoroutinefunction(session.interrupt):
                    asyncio.create_task(session.interrupt())
                else:
                    session.interrupt()
                latency_recorder.mark("barge_in_stopped_audio")
                
        elif "listening" in state_str or "idle" in state_str:
            if "speaking" in old_state_str:
                logger.info("User speaking stopped")
                latency_recorder.mark("user_speech_end")
                
    @session.on("agent_state_changed")
    def on_agent_state_changed(ev):
        state_str = str(ev.new_state).lower()
        old_state_str = str(ev.old_state).lower()
        if "speaking" in state_str:
            latency_recorder.mark("agent_speech_started")
        elif "speaking" in old_state_str:
            latency_recorder.mark("agent_speech_stopped")
            
    @session.on("user_input_transcribed")
    def on_user_input_transcribed(event):
        if event.is_final:
            latency_recorder.mark("transcript_final")
            
    # Connect to room (audio only)
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    
    # Wait for participant
    participant = await ctx.wait_for_participant()
    latency_recorder.mark("participant_joined")
    logger.info(f"Participant joined: {participant.identity}")
    
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
    
    # Speak greeting immediately after join
    latency_recorder.mark("greeting_started")
    logger.info(f"Speaking opening line: {shared.config.opening_line}")
    await session.say(shared.config.opening_line)
    
    try:
        # Loop until room disconnected
        while ctx.room.is_connected():
            await asyncio.sleep(1.0)
    finally:
        latency_recorder.log_summary()
        logger.info(f"Session finished for call {call_id}")


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
