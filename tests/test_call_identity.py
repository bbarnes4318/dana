import json
import uuid
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import main
from dialer.call_service import CallService
from main import entrypoint, SharedComponents
from voice_config import VoiceConfig
from storage.repository import Repository


@pytest.mark.asyncio
async def test_call_identity_correlation_between_dialer_and_entrypoint(tmp_path):
    """
    Test that CallService.place_call() sets the correct metadata,
    and main.entrypoint() resolves the identical call_id, lead_id, and campaign_id from it.
    """
    # Initialize repository
    repo = Repository(data_dir=tmp_path)
    
    # 1. Place the call via CallService
    lead = {
        "id": "lead-test-123",
        "lead_id": "lead-test-123",
        "phone_e164": "+13055550199",
        "campaign_id": "camp-test-456",
        "status": "pending",
        "attempts": 0,
    }
    call_id = f"call-{uuid.uuid4().hex[:8]}"
    caller_id = "+18005550199"
    room_name = "test-room-name-789"
    
    # We mock LiveKitAPI to capture the request arguments
    mock_lkapi_instance = MagicMock()
    mock_lkapi_instance.sip = AsyncMock()
    mock_lkapi_instance.aclose = AsyncMock()
    
    mock_participant = MagicMock()
    mock_participant.participant_id = "sip-part-abc"
    mock_lkapi_instance.sip.create_sip_participant = AsyncMock(return_value=mock_participant)
    
    # We patch LiveKitAPI and env vars
    with patch("livekit.api.LiveKitAPI", return_value=mock_lkapi_instance), \
         patch.dict(os.environ, {
             "DANA_CONFIRM_PLACE_CALL": "yes",
             "LIVEKIT_SIP_OUTBOUND_TRUNK_ID": "trunk-test",
             "LIVEKIT_URL": "http://mock-lk",
             "LIVEKIT_API_KEY": "key",
             "LIVEKIT_API_SECRET": "secret"
         }):
        
        call_service = CallService()
        result = await call_service.place_call(
            lead=lead,
            call_id=call_id,
            caller_id=caller_id,
            room_name=room_name,
            repository=repo
        )
        
        # Verify call record was saved to repo
        call_rec = await repo.get_call_record(call_id)
        assert call_rec is not None
        assert call_rec.get("call_id") == call_id
        assert call_rec.get("lead_id") == "lead-test-123"
        assert call_rec.get("campaign_id") == "camp-test-456"
        assert call_rec.get("room_name") == room_name
        assert call_rec.get("sip_participant_id") == "sip-part-abc"
        
        # Capture participant metadata sent to LiveKit
        called_args, called_kwargs = mock_lkapi_instance.sip.create_sip_participant.call_args
        request = called_args[0]
        
        # Assert metadata matches
        participant_metadata = request.kwargs.get("participant_metadata")
        assert participant_metadata is not None
        meta_dict = json.loads(participant_metadata)
        assert meta_dict["call_id"] == call_id
        assert meta_dict["lead_id"] == "lead-test-123"
        assert meta_dict["campaign_id"] == "camp-test-456"

    # 2. Simulate entrypoint(ctx) and verify it extracts the identical metadata
    ctx = MagicMock()
    ctx.room.name = room_name
    ctx.room.metadata = json.dumps({
        "call_id": call_id,
        "lead_id": "lead-test-123",
        "campaign_id": "camp-test-456",
    })
    ctx.proc.userdata = {}
    
    ctx.connect = AsyncMock()
    mock_joined_participant = MagicMock()
    mock_joined_participant.identity = "+13055550199"
    # Ensure participant.metadata matches too
    mock_joined_participant.metadata = participant_metadata
    mock_joined_participant.track_publications = {}
    ctx.wait_for_participant = AsyncMock(return_value=mock_joined_participant)
    
    # Configure prewarmed SharedComponents
    config = VoiceConfig()
    shared = SharedComponents(config)
    
    # Set mock models to prevent actual model loads
    shared.stt = MagicMock()
    shared.llm = MagicMock()
    shared.tts = MagicMock()
    shared.vad = MagicMock()
    shared.repository = repo
    shared.prompt_loader = MagicMock()
    shared.prompt_loader.load_prompt = MagicMock(return_value="instructions")
    shared.objection_classifier = MagicMock()
    shared.objection_policy = MagicMock()
    shared.context_builder = MagicMock()
    shared.action_policy = MagicMock()
    shared.tool_registry = MagicMock()
    shared.compliance_filter = MagicMock()
    shared.output_validator = MagicMock()
    shared.pii_redactor = MagicMock()
    shared.reinitialize_for_job = AsyncMock()
    
    ctx.proc.userdata["shared_components"] = shared
    
    # Mock AgentSession and session.start inside entrypoint
    mock_session = MagicMock()
    mock_session.start = AsyncMock(side_effect=RuntimeError("stop_agent_execution"))
    
    active_agents = []
    
    from dana.runtime.voice_session import DanaAgent as RealDanaAgent
    class CapturedDanaAgent(RealDanaAgent):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            active_agents.append(self)
            
    # Patch AgentSession, DanaAgent, and LatencyRecorder in voice_session
    with patch("dana.runtime.voice_session.AgentSession", return_value=mock_session), \
         patch("dana.runtime.voice_session.DanaAgent", new=CapturedDanaAgent), \
         patch("dana.runtime.call_context.LatencyRecorder") as mock_latency_recorder_cls:
         
        # Run entrypoint until AgentSession starts and raises stop_agent_execution
        with pytest.raises(RuntimeError, match="stop_agent_execution"):
            await entrypoint(ctx)
            
        # Assert LatencyRecorder was instantiated with the correct call_id
        mock_latency_recorder_cls.assert_called_once_with(call_id)
        
        # Verify DanaAgent captured the proper metadata on adapter initialization
        assert len(active_agents) == 1
        agent = active_agents[0]
        assert agent.adapter is not None
        assert agent.adapter.call_id == call_id
        assert agent.adapter.lead.lead_id == "lead-test-123"
        assert agent.adapter.lead.campaign_id == "camp-test-456"
        assert agent.adapter.lead.lead_phone_e164 == "+13055550199"
        
        # Assert that room and participant metadata were parsed correctly
        assert ctx.connect.called
        assert ctx.wait_for_participant.called
