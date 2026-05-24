"""
Dana Voice Agent — Main Entry Point
Ultra-low-latency Voice AI using LiveKit Agents Framework.

Orchestrator responsibilities:
1. Load agent instructions from DANA_AGENT_PROMPT_PATH (file on disk).
2. Connect to LiveKit room.
3. Use local faster-whisper for STT (zero network latency).
4. Use local Kokoro ONNX for TTS (zero network latency).
5. Use vLLM for LLM via OpenAI-compatible API.
6. Respect DANA_OPENING_MODE (wait_for_user | immediate).
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    AutoSubscribe,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
    llm,
)
from livekit.agents.voice_assistant import VoiceAssistant
from livekit.plugins import openai as lk_openai

from stt_service import LocallyHostedSTT, STTConfig
from tts_service import LocallyHostedKokoro, TTSConfig, StreamingTTSAdapter
from voice_config import VoiceConfig

# Load environment variables
load_dotenv()

# Build global config once
CONFIG = VoiceConfig()

# Configure logging
logging.basicConfig(
    level=getattr(logging, CONFIG.log_level, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ---- Default fallback prompt (used when file is missing) --------------------
_DEFAULT_INSTRUCTIONS = (
    "You are a helpful, friendly, and professional AI voice assistant. "
    "You are having a natural phone conversation with a human. "
    "Keep responses concise (1-3 sentences). Be warm and personable. "
    "Never use markdown or formatting — speak naturally."
)


# =============================================================================
# Instruction Loader
# =============================================================================

def load_instructions(path: str) -> str:
    """Load agent instructions from a file path.

    Falls back to ``_DEFAULT_INSTRUCTIONS`` if the file is missing, empty,
    or unreadable — the agent will still work, just without the custom prompt.
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


# =============================================================================
# Context Manager (sliding-window)
# =============================================================================

class ContextManager:
    """Manages conversation context with sliding-window truncation.

    Prevents token overflow by keeping only recent conversation turns
    when context exceeds the maximum length.
    """

    def __init__(self, max_tokens: int = 6000, keep_turns: int = 10):
        self.max_tokens = max_tokens
        self.keep_turns = keep_turns
        self._messages: list[llm.ChatMessage] = []
        self._system_message: Optional[llm.ChatMessage] = None

    def set_system_message(self, content: str):
        """Set the system message (always retained)."""
        self._system_message = llm.ChatMessage(role="system", content=content)

    def add_message(self, role: str, content: str):
        """Add a message to the conversation history."""
        self._messages.append(llm.ChatMessage(role=role, content=content))
        self._maybe_truncate()

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimation (4 chars per token average)."""
        return len(text) // 4

    def _maybe_truncate(self):
        """Truncate conversation if exceeding max tokens."""
        total_tokens = sum(self._estimate_tokens(m.content or "") for m in self._messages)
        if self._system_message:
            total_tokens += self._estimate_tokens(self._system_message.content or "")

        if total_tokens > self.max_tokens:
            if len(self._messages) > self.keep_turns * 2:
                self._messages = self._messages[-(self.keep_turns * 2):]
                logger.info("Context truncated to %d messages", len(self._messages))

    def get_messages(self) -> list[llm.ChatMessage]:
        """Get all messages including system message."""
        messages = []
        if self._system_message:
            messages.append(self._system_message)
        messages.extend(self._messages)
        return messages

    def clear(self):
        """Clear conversation history (keeps system message)."""
        self._messages.clear()


# =============================================================================
# Dana Agent
# =============================================================================

class DanaAgent:
    """The Dana voice agent.

    Orchestrates STT, LLM, and TTS with aggressive latency optimisation
    and barge-in handling.
    """

    def __init__(self, config: VoiceConfig, instructions: str):
        self.config = config
        self.instructions = instructions

        # Components (initialised lazily)
        self._stt: Optional[LocallyHostedSTT] = None
        self._tts: Optional[LocallyHostedKokoro] = None
        self._tts_adapter: Optional[StreamingTTSAdapter] = None
        self._llm: Optional[lk_openai.LLM] = None
        self._assistant: Optional[VoiceAssistant] = None

        # Context management
        self._context = ContextManager(max_tokens=6000, keep_turns=10)
        self._context.set_system_message(self.instructions)

        # State
        self._is_speaking = False
        self._is_processing = False
        self._current_speech_handle = None

    async def initialize(self):
        """Initialise all AI components."""
        logger.info("Initialising Dana Voice Agent…")

        # STT
        self._stt = LocallyHostedSTT(STTConfig(
            model_size=self.config.stt_model,
            compute_type=self.config.stt_compute_type,
            device="cuda",
            language="en",
            beam_size=1,
            vad_threshold=self.config.vad_threshold,
        ))
        await self._stt.initialize()

        # TTS
        self._tts = LocallyHostedKokoro(TTSConfig(
            model_name="kokoro-v1.0",
            voice=self.config.tts_voice,
            speed=self.config.tts_speed,
        ))
        await self._tts.initialize()
        self._tts_adapter = StreamingTTSAdapter(self._tts)

        # LLM via vLLM
        self._llm = lk_openai.LLM(
            model=self.config.llm_model,
            base_url=self.config.vllm_base_url,
            api_key="not-needed",  # vLLM doesn't require an API key
        )

        logger.info("All components initialised successfully")

    async def handle_user_speech(self, text: str) -> str:
        """Handle transcribed user speech and generate response."""
        if not text.strip():
            return ""

        logger.info("User said: %s", text)
        self._context.add_message("user", text)

        self._is_processing = True
        try:
            response = await self._llm.chat(
                messages=self._context.get_messages(),
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )

            assistant_text = response.choices[0].message.content or ""
            self._context.add_message("assistant", assistant_text)
            logger.info("Assistant response: %s", assistant_text)
            return assistant_text
        finally:
            self._is_processing = False

    async def interrupt_speech(self):
        """Handle barge-in by immediately stopping all audio output."""
        logger.info("Barge-in detected — interrupting speech")

        if self._tts_adapter:
            await self._tts_adapter.interrupt()

        if self._current_speech_handle:
            try:
                self._current_speech_handle.interrupt()
            except Exception as e:
                logger.warning("Error interrupting speech handle: %s", e)

        self._is_speaking = False


# =============================================================================
# LiveKit Entry-point
# =============================================================================

async def entrypoint(ctx: JobContext):
    """LiveKit Agent entrypoint — called for each new participant connection."""
    logger.info("New connection: room=%s", ctx.room.name)

    # Load instructions from file
    instructions = load_instructions(CONFIG.agent_prompt_path)

    # Build the agent
    agent = DanaAgent(config=CONFIG, instructions=instructions)
    await agent.initialize()

    # Build the VoiceAssistant
    min_endpointing = CONFIG.turn_min_delay
    assistant = VoiceAssistant(
        stt=agent._stt,
        tts=agent._tts,
        llm=agent._llm,
        chat_ctx=llm.ChatContext().append(
            role="system",
            text=instructions,
        ),
        min_endpointing_delay=min_endpointing,
        allow_interruptions=True,
    )

    # ---- Event handlers ----
    @assistant.on("user_started_speaking")
    async def on_user_started_speaking():
        if agent._is_speaking:
            logger.info("User started speaking during assistant speech — interrupting")
            await agent.interrupt_speech()

    @assistant.on("agent_started_speaking")
    async def on_agent_started_speaking():
        agent._is_speaking = True
        logger.debug("Agent started speaking")

    @assistant.on("agent_stopped_speaking")
    async def on_agent_stopped_speaking():
        agent._is_speaking = False
        logger.debug("Agent stopped speaking")

    # ---- Connect ----
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    participant = await ctx.wait_for_participant()
    logger.info("Participant joined: %s", participant.identity)

    # ---- Start ----
    assistant.start(ctx.room, participant)

    # ---- Opening behaviour ----
    if CONFIG.opening_mode == "immediate" and CONFIG.opening_line:
        await assistant.say(CONFIG.opening_line, allow_interruptions=True)
    elif CONFIG.opening_mode == "wait_for_user":
        # Do nothing — the agent will respond only after the user speaks.
        logger.info("Opening mode: wait_for_user — agent will not speak first")
    else:
        # Unknown mode or immediate with no line — stay silent.
        logger.info("Opening mode: %s (no opening line) — agent is silent", CONFIG.opening_mode)


def prewarm(proc: JobProcess):
    """Prewarm the worker process — pre-load models for fast first response."""
    logger.info("Prewarming worker process…")

    async def _prewarm():
        instructions = load_instructions(CONFIG.agent_prompt_path)
        agent = DanaAgent(config=CONFIG, instructions=instructions)
        await agent.initialize()
        logger.info("Prewarm complete — models loaded")

    asyncio.run(_prewarm())


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        ),
    )
