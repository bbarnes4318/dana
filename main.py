"""
Sovereign Voice Stack - Main Agent Entry Point
Ultra-low-latency Voice AI using LiveKit Agents Framework.

This is the main orchestrator that:
1. Connects to LiveKit room
2. Uses local faster-whisper for STT (zero network latency)
3. Uses local Kokoro ONNX for TTS (zero network latency)
4. Uses vLLM for LLM via OpenAI-compatible API
5. Implements aggressive barge-in with buffer clearing
"""

import asyncio
import logging
import os
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

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    """Configuration for the Voice Agent."""
    # vLLM Configuration
    vllm_base_url: str = field(default_factory=lambda: os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1"))
    llm_model: str = "meta-llama/Llama-3.1-8B-Instruct"
    
    # Context Management
    max_context_tokens: int = 6000  # Leave headroom in 8192 context
    sliding_window_keep_turns: int = 10  # Keep last N turns when truncating
    
    # System Prompt
    system_prompt: str = """You are a helpful, friendly, and professional AI voice assistant. 
You are having a natural phone conversation with a human.

Guidelines:
- Keep responses concise and conversational (1-3 sentences typically)
- Be warm and personable while remaining professional
- Ask clarifying questions when needed
- If you don't know something, say so honestly
- Use natural speech patterns and occasional filler words like "well" or "so"
- Never use markdown, bullet points, or formatting - speak naturally
- Respond quickly and avoid long pauses"""

    # Latency Optimization
    enable_barge_in: bool = True
    min_endpointing_delay_ms: int = 200  # Minimum silence before considering speech ended


class ContextManager:
    """
    Manages conversation context with sliding window truncation.
    
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
            # Keep only the most recent turns
            if len(self._messages) > self.keep_turns * 2:
                self._messages = self._messages[-(self.keep_turns * 2):]
                logger.info(f"Context truncated to {len(self._messages)} messages")
    
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


class SovereignVoiceAgent:
    """
    The main Sovereign Voice Agent.
    
    Orchestrates STT, LLM, and TTS with aggressive latency optimization
    and barge-in handling.
    """
    
    def __init__(self, config: Optional[AgentConfig] = None):
        self.config = config or AgentConfig()
        
        # Initialize components
        self._stt: Optional[LocallyHostedSTT] = None
        self._tts: Optional[LocallyHostedKokoro] = None
        self._tts_adapter: Optional[StreamingTTSAdapter] = None
        self._llm: Optional[lk_openai.LLM] = None
        self._assistant: Optional[VoiceAssistant] = None
        
        # Context management
        self._context = ContextManager(
            max_tokens=self.config.max_context_tokens,
            keep_turns=self.config.sliding_window_keep_turns
        )
        self._context.set_system_message(self.config.system_prompt)
        
        # State tracking
        self._is_speaking = False
        self._is_processing = False
        self._current_speech_handle = None
        
    async def initialize(self):
        """Initialize all AI components."""
        logger.info("Initializing Sovereign Voice Agent...")
        
        # Initialize STT (faster-whisper)
        self._stt = LocallyHostedSTT(STTConfig(
            model_size="large-v3-turbo",
            compute_type="float16",
            device="cuda",
            language="en",
            beam_size=1,  # Greedy for speed
        ))
        await self._stt.initialize()
        
        # Initialize TTS (Kokoro ONNX)
        self._tts = LocallyHostedKokoro(TTSConfig(
            model_name="kokoro-v1.0",
            voice="af_bella",
            speed=1.0,
        ))
        await self._tts.initialize()
        self._tts_adapter = StreamingTTSAdapter(self._tts)
        
        # Initialize LLM (vLLM via OpenAI-compatible API)
        self._llm = lk_openai.LLM(
            model=self.config.llm_model,
            base_url=self.config.vllm_base_url,
            api_key="not-needed",  # vLLM doesn't require API key
        )
        
        logger.info("All components initialized successfully")
        
    async def handle_user_speech(self, text: str) -> str:
        """
        Handle transcribed user speech and generate response.
        
        Args:
            text: Transcribed user speech
            
        Returns:
            LLM response text
        """
        if not text.strip():
            return ""
            
        logger.info(f"User said: {text}")
        self._context.add_message("user", text)
        
        # Generate response via vLLM
        self._is_processing = True
        try:
            response = await self._llm.chat(
                messages=self._context.get_messages(),
                temperature=0.7,
                max_tokens=150,  # Keep responses concise for voice
            )
            
            assistant_text = response.choices[0].message.content or ""
            self._context.add_message("assistant", assistant_text)
            
            logger.info(f"Assistant response: {assistant_text}")
            return assistant_text
            
        finally:
            self._is_processing = False
    
    async def interrupt_speech(self):
        """
        Handle barge-in by immediately stopping all audio output.
        
        This is called when the user starts speaking while the assistant
        is still talking. We need to:
        1. Stop audio playback immediately
        2. Clear TTS buffer
        3. Cancel any ongoing LLM generation
        """
        logger.info("Barge-in detected - interrupting speech")
        
        # Interrupt TTS
        if self._tts_adapter:
            await self._tts_adapter.interrupt()
            
        # Interrupt voice assistant speech
        if self._current_speech_handle:
            try:
                self._current_speech_handle.interrupt()
            except Exception as e:
                logger.warning(f"Error interrupting speech handle: {e}")
                
        self._is_speaking = False


async def entrypoint(ctx: JobContext):
    """
    LiveKit Agent entrypoint.
    
    This function is called for each new participant connection.
    Sets up the voice assistant with our local STT/TTS.
    """
    logger.info(f"New connection: room={ctx.room.name}")
    
    # Initialize our agent
    agent = SovereignVoiceAgent()
    await agent.initialize()
    
    # Create the voice assistant with our local components
    assistant = VoiceAssistant(
        stt=agent._stt,
        tts=agent._tts,
        llm=agent._llm,
        chat_ctx=llm.ChatContext().append(
            role="system",
            text=agent.config.system_prompt
        ),
        # Latency optimization settings
        min_endpointing_delay=agent.config.min_endpointing_delay_ms / 1000,
        allow_interruptions=agent.config.enable_barge_in,
    )
    
    # Set up event handlers for barge-in
    @assistant.on("user_started_speaking")
    async def on_user_started_speaking():
        """Handle barge-in when user starts speaking."""
        if agent._is_speaking:
            logger.info("User started speaking during assistant speech - interrupting")
            await agent.interrupt_speech()
    
    @assistant.on("agent_started_speaking")
    async def on_agent_started_speaking():
        """Track when assistant starts speaking."""
        agent._is_speaking = True
        logger.debug("Agent started speaking")
    
    @assistant.on("agent_stopped_speaking")
    async def on_agent_stopped_speaking():
        """Track when assistant stops speaking."""
        agent._is_speaking = False
        logger.debug("Agent stopped speaking")
    
    # Connect to the room
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    
    # Wait for a participant to join
    participant = await ctx.wait_for_participant()
    logger.info(f"Participant joined: {participant.identity}")
    
    # Start the voice assistant
    assistant.start(ctx.room, participant)
    
    # Send initial greeting
    await assistant.say("Hello! How can I help you today?", allow_interruptions=True)


def prewarm(proc: JobProcess):
    """
    Prewarm the worker process.
    
    Called once when the worker starts. We use this to pre-load
    models so they're ready when connections arrive.
    """
    logger.info("Prewarming worker process...")
    
    # Pre-load models
    async def _prewarm():
        agent = SovereignVoiceAgent()
        await agent.initialize()
        logger.info("Prewarm complete - models loaded")
    
    asyncio.run(_prewarm())


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        ),
    )
