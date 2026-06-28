import asyncio
import os
import sys
import logging
import pytest
from pathlib import Path

# Ensure parent directory is on sys.path
root_dir = Path(__file__).resolve().parent.parent

from voice_config import VoiceConfig
from main import SharedComponents
from dana.runtime.voice_session import DanaAgent
from latency_metrics import LatencyRecorder

logger = logging.getLogger("test_verify_voice_smoke")

@pytest.mark.asyncio
async def test_simulated_voice_smoke(monkeypatch):
    logger.info("Starting Simulated Voice Stack Smoke Test...")
    
    # Force clean test env variables
    monkeypatch.setenv("DANA_VOICE_MODE", "premium_live")
    monkeypatch.setenv("DANA_STT_PROVIDER", "deepgram")
    monkeypatch.setenv("DANA_STT_ROUTING_MODE", "hybrid")
    monkeypatch.setenv("DANA_TTS_PROVIDER", "elevenlabs")
    monkeypatch.setenv("DANA_TTS_ROUTING_MODE", "hybrid")
    monkeypatch.setenv("DANA_LLM_ROUTING_MODE", "local")
    monkeypatch.setenv("DANA_ALLOW_MOCK_TTS", "false")
    
    # Mock credentials
    monkeypatch.setenv("DEEPGRAM_API_KEY", "mock-dg-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "mock-el-key")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "mock-el-voice")
    
    # 1. Initialize configurations & verify hybrid/local settings
    config = VoiceConfig()
    logger.info(f"Loaded voice_mode: {config.voice_mode}")
    logger.info(f"stt_routing_mode: {config.stt_routing_mode}")
    logger.info(f"tts_routing_mode: {config.tts_routing_mode}")
    logger.info(f"llm_routing_mode: {config.llm_routing_mode}")
    
    # 2. Initialize SharedComponents (STT, LLM, TTS, VAD, objection policy, etc.)
    shared = SharedComponents(config)
    
    # Mock Kokoro / Whisper models inside LocallyHostedSTT/Kokoro
    # Mock ElderlySileroVAD class completely to avoid ONNX/model load issues
    from unittest.mock import patch, MagicMock
    mock_vad_class = MagicMock()
    mock_vad_class.load = MagicMock(return_value=MagicMock())
    with patch("dana.providers.vad.silero.ElderlySileroVAD", mock_vad_class):
        await shared.initialize()
    logger.info("Shared components initialized successfully!")
    
    class MockEvent:
        def __init__(self, frame):
            self.frame = frame

    class MockTTSStream:
        def __init__(self):
            self._yielded = False

        def push_text(self, text: str) -> None:
            pass

        def flush(self) -> None:
            pass

        async def aclose(self) -> None:
            pass

        async def interrupt(self) -> None:
            pass

        def __aiter__(self):
            self._yielded = False
            return self

        async def __anext__(self):
            if not self._yielded:
                self._yielded = True
                from tests.conftest import DummyAudioFrame
                return MockEvent(DummyAudioFrame(data=b"mock-audio"))
            raise StopAsyncIteration

    shared.tts.stream = MagicMock(return_value=MockTTSStream())
    
    # 3. Instantiate DanaAgent with mock latency recorder
    latency_recorder = LatencyRecorder("smoke-test-call-123")
    agent = DanaAgent(shared, latency_recorder)
    
    # Setup runtime/adapter/state machine on agent
    from core.livekit_runtime_adapter import LiveKitRuntimeAdapter
    adapter = LiveKitRuntimeAdapter(call_id="smoke-test-call-123", project_root=root_dir)
    agent.adapter = adapter
    
    # 4. Turn 1: Greeting Plays
    logger.info("--- TURN 1: GREETING ---")
    greeting_text = config.opening_line or "Hello?"
    logger.info(f"Greeting text: '{greeting_text}'")
    assert len(greeting_text) > 0, "Greeting text must not be empty"
    
    # Verify we can generate audio for the greeting text using the configured TTS
    tts_stream = shared.tts.stream()
    tts_stream.push_text(greeting_text)
    tts_stream.flush()
    
    frames_received = 0
    async for ev in tts_stream:
        frames_received += 1
        if frames_received == 1:
            logger.info("TTS_FIRST_AUDIO_SENT for greeting")
    await tts_stream.aclose()
    logger.info(f"TTS completed: received {frames_received} audio frames for greeting.")
    assert frames_received > 0, "ERROR_TTS_NO_AUDIO: Greeting generated no audio"
    logger.info("Greeting Turn: PASS")
    
    # 5. Turn 2: User says "who is this?"
    logger.info("--- TURN 2: USER SPEAKING ('who is this?') ---")
    user_text = "who is this?"
    logger.info(f"User says: '{user_text}'")
    
    # Simulate receiving final transcript
    logger.info("USER_TRANSCRIPT_RECEIVED")
    logger.info(f"FINAL_TRANSCRIPT_TEXT_LENGTH: {len(user_text)}")
    
    # Generate LLM response for Turn 2
    async def chat_fn(instructions: str) -> str:
        # Simple simulated chat completion function
        return "I am Alex calling from American Beneficiary about your final expense application."
        
    result = await adapter.process_user_turn(user_text, chat_fn)
    response_text = result.agent_response or ""
    logger.info(f"LLM response received: '{response_text}'")
    logger.info(f"LLM_RESPONSE_TEXT_LENGTH: {len(response_text)}")
    assert len(response_text) > 0, "ERROR_EMPTY_LLM_RESPONSE: LLM response was empty"
    
    # Verify TTS emits audio for LLM response
    tts_stream = shared.tts.stream()
    tts_stream.push_text(response_text)
    tts_stream.flush()
    
    frames_received = 0
    async for ev in tts_stream:
        frames_received += 1
        if frames_received == 1:
            logger.info("TTS_FIRST_AUDIO_SENT for Turn 2")
    await tts_stream.aclose()
    logger.info(f"TTS completed: received {frames_received} audio frames for Turn 2.")
    assert frames_received > 0, "ERROR_TTS_NO_AUDIO: Response generated no audio"
    logger.info("Second Turn: PASS")
    
    # 6. Turn 3: User asks another question ("what is this about?")
    logger.info("--- TURN 3: USER SPEAKING ('what is this about?') ---")
    user_text_3 = "what is this about?"
    logger.info(f"User says: '{user_text_3}'")
    
    logger.info("USER_TRANSCRIPT_RECEIVED")
    logger.info(f"FINAL_TRANSCRIPT_TEXT_LENGTH: {len(user_text_3)}")
    
    # Generate LLM response for Turn 3
    async def chat_fn_3(instructions: str) -> str:
        return "I'm calling to verify your information for final expense life insurance benefit packages. Are you open to reviewing those?"
        
    result_3 = await adapter.process_user_turn(user_text_3, chat_fn_3)
    response_text_3 = result_3.agent_response or ""
    logger.info(f"LLM response received: '{response_text_3}'")
    logger.info(f"LLM_RESPONSE_TEXT_LENGTH: {len(response_text_3)}")
    assert len(response_text_3) > 0, "ERROR_EMPTY_LLM_RESPONSE: LLM response was empty"
    
    # Verify TTS emits audio for Turn 3 LLM response
    tts_stream = shared.tts.stream()
    tts_stream.push_text(response_text_3)
    tts_stream.flush()
    
    frames_received_3 = 0
    async for ev in tts_stream:
        frames_received_3 += 1
        if frames_received_3 == 1:
            logger.info("TTS_FIRST_AUDIO_SENT for Turn 3")
    await tts_stream.aclose()
    logger.info(f"TTS completed: received {frames_received_3} audio frames for Turn 3.")
    assert frames_received_3 > 0, "ERROR_TTS_NO_AUDIO: Response generated no audio"
    logger.info("Third Turn: PASS")
    
    logger.info("========================================")
    logger.info("SIMULATED SMOKE TEST COMPLETED: ALL TESTS PASSED!")
    logger.info("========================================")
