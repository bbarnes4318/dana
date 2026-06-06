import pytest
import os
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from main import entrypoint
from voice_config import VoiceConfig
from livekit.agents.stt import SpeechEvent, SpeechEventType, SpeechData
from speech.context_registry import register_call, update_call_stage, get_current_call_id

class MockSession:
    def __init__(self):
        self.start = AsyncMock()
        self.update_options_calls = []
        self.handlers = {}

    def on(self, event_name):
        def decorator(func):
            self.handlers[event_name] = func
            return func
        return decorator

    def update_options(self, min_endpointing_delay: float = 0.0, max_endpointing_delay: float = 0.0):
        self.update_options_calls.append((min_endpointing_delay, max_endpointing_delay))


@pytest.mark.asyncio
async def test_endpoint_tuner_semantic_integration_updates_delays():
    # Setup mocks
    ctx = MagicMock()
    ctx.room.name = "test-room"
    ctx.proc.userdata = {}
    
    # Mock connection and participant
    ctx.connect = AsyncMock()
    mock_participant = MagicMock()
    mock_participant.identity = "+15555550123"
    ctx.wait_for_participant = AsyncMock(return_value=mock_participant)
    
    # Mock SharedComponents
    shared_mock = MagicMock()
    shared_mock.config = VoiceConfig(endpoint_mode="adaptive")
    shared_mock.stt = MagicMock()
    shared_mock.llm = MagicMock()
    shared_mock.tts = MagicMock()
    shared_mock.vad = MagicMock()
    shared_mock.repository = MagicMock()
    shared_mock.repository.get_lead_by_phone = AsyncMock(return_value=None)
    
    ctx.proc.userdata["shared_components"] = shared_mock
    
    # We mock AgentSession using our custom MockSession
    session_mock = MockSession()
    
    # Patch AgentSession inside main
    with patch("main.AgentSession", return_value=session_mock), \
         patch.dict(os.environ, {"DANA_ENABLE_SEMANTIC_TURN_DETECTION": "true"}):
         
        session_mock.start.side_effect = RuntimeError("session_start_interrupted")
        
        with pytest.raises(RuntimeError, match="session_start_interrupted"):
            await entrypoint(ctx)
            
        assert "user_input_transcribed" in session_mock.handlers
        
        # Mock registration in context registry
        call_id = "test-call-id-123"
        register_call(call_id, "test-campaign")
        update_call_stage(call_id, "INTEREST_CHECK")
        
        # Create a mock SpeechEvent with a DNC phrase
        event = SpeechEvent(
            type=SpeechEventType.INTERIM_TRANSCRIPT,
            alternatives=[SpeechData(text="stop calling me", language="en")]
        )
        event.is_final = False
        event.text = "stop calling me"
        
        # Patch get_current_call_id to return our registered call_id
        with patch("speech.context_registry.get_current_call_id", return_value=call_id):
            handler = session_mock.handlers["user_input_transcribed"]
            handler(event)
            
        # Verify that session_mock.update_options was called with immediate delays
        assert len(session_mock.update_options_calls) > 0
        # The last call should be the update from the semantic turn detector
        last_call = session_mock.update_options_calls[-1]
        assert last_call == (0.01, 0.05)
