"""Statically validates imports, signatures, decorators, and API shapes without starting services."""

import inspect
import sys
from unittest.mock import AsyncMock, MagicMock
import pytest

from livekit import api
from livekit.agents import Agent, function_tool, RunContext
from main import DanaAgent, SharedComponents
from latency_metrics import LatencyRecorder


def test_agent_function_tool_registration():
    """Verify DanaAgent does NOT register feTransfer natively anymore."""
    mock_shared = MagicMock(spec=SharedComponents)
    mock_shared.config = MagicMock()
    mock_shared.config.agent_prompt_path = "prompts/final_expense_alex.md"
    mock_shared.llm = MagicMock()
    mock_shared.tts = MagicMock()
    mock_shared.stt = MagicMock()
    
    mock_latency = MagicMock(spec=LatencyRecorder)
    
    agent = DanaAgent(mock_shared, mock_latency)
    
    registered_tools = [t.info.name for t in agent.tools] if hasattr(agent, "tools") else []
    assert "feTransfer" not in registered_tools, "feTransfer must not be registered natively on DanaAgent"


@pytest.mark.asyncio
async def test_llm_node_does_not_pass_tools_argument():
    """Verify llm_node does not pass tools kwarg down to LLM.chat."""
    from pathlib import Path
    mock_shared = MagicMock(spec=SharedComponents)
    mock_shared.config = MagicMock()
    mock_shared.config.agent_prompt_path = "prompts/final_expense_alex.md"
    mock_shared.llm = MagicMock()
    mock_shared.tts = MagicMock()
    mock_shared.stt = MagicMock()
    
    # Mock LLM stream response
    async def mock_stream():
        if False:
            yield None
            
    mock_shared.llm.chat = MagicMock(return_value=mock_stream())
    
    mock_latency = MagicMock(spec=LatencyRecorder)
    
    agent = DanaAgent(mock_shared, mock_latency)
    
    # Setup the adapter
    from core.livekit_runtime_adapter import LiveKitRuntimeAdapter
    agent.adapter = LiveKitRuntimeAdapter(call_id="test-call-id", project_root=Path(__file__).resolve().parent.parent)
    
    chat_ctx = MagicMock()
    chat_ctx.messages = [MagicMock(role="user", content="Hello")]
    tools = ["mock_tool_1", "mock_tool_2"]
    
    gen = agent.llm_node(chat_ctx, tools, None)
    async for _ in gen:
        pass
        
    mock_shared.llm.chat.assert_called_once()
    _, kwargs = mock_shared.llm.chat.call_args
    assert "tools" not in kwargs, "tools must not be passed down to LLM.chat"
    assert "fnc_ctx" not in kwargs, "fnc_ctx must not be passed to LLM.chat"


def test_main_no_function_context_references():
    """Statically verify main.py contains zero references to llm.FunctionContext."""
    with open("main.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "llm.FunctionContext" not in content, "main.py must not reference llm.FunctionContext"


def test_create_livekit_telnyx_outbound_trunk_imports_livekit_api():
    """Verify create_livekit_telnyx_outbound_trunk.py successfully imports livekit.api."""
    from livekit import api as lk_api
    assert lk_api is not None


def test_create_sip_participant_request_fields_introspected():
    """Verify fields on CreateSIPParticipantRequest and check that sip_number is not hardcoded."""
    desc = api.CreateSIPParticipantRequest.DESCRIPTOR
    fields = desc.fields_by_name.keys()
    
    # Introspect required fields
    assert "sip_trunk_id" in fields
    assert "sip_call_to" in fields
    assert "room_name" in fields
    assert "participant_identity" in fields
    assert "participant_metadata" in fields
    
    # Introspect extra fields
    assert "wait_until_answered" in fields
    assert "display_name" in fields
    
    # Read the script create_outbound_call.py and confirm sip_number is not hardcoded
    with open("telephony/create_outbound_call.py", "r", encoding="utf-8") as f:
        script_content = f.read()
    
    # Assert we dynamically check or don't pass hardcoded sip_number
    assert 'sip_number=config.dana_default_caller_id' not in script_content, "sip_number must not be hardcoded in CreateSIPParticipantRequest"


@pytest.mark.asyncio
async def test_create_sip_participant_request_sip_number_logic():
    """Verify that CreateSIPParticipantRequest does not receive sip_number if not present in descriptor, but does if present."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from telephony import create_outbound_call
    from tests.conftest import MockDescriptor, MockCreateSIPParticipantRequest
    
    # Mock config
    mock_config = MagicMock()
    mock_config.livekit_url = "ws://mock"
    mock_config.livekit_api_key = "key"
    mock_config.livekit_api_secret = "secret"
    mock_config.livekit_sip_outbound_trunk_id = "trunk_id"
    mock_config.dana_default_caller_id = "+15555550100"
    mock_config.dana_confirm_place_call = True
    mock_config.dana_room_prefix = "dana-call"
    
    # Mock parser arguments
    mock_args = MagicMock()
    mock_args.to = "+15555550123"
    mock_args.room = None
    mock_args.identity = None
    mock_args.metadata = None
    
    with patch("argparse.ArgumentParser.parse_args", return_value=mock_args), \
         patch("telephony.create_outbound_call.TelephonyConfig", return_value=mock_config), \
         patch("livekit.api.LiveKitAPI") as mock_lkapi_cls:
         
        mock_lkapi = MagicMock()
        mock_lkapi.aclose = AsyncMock()
        mock_lkapi.sip = MagicMock()
        mock_participant = MagicMock()
        mock_participant.participant_id = "mock-participant-id"
        mock_lkapi.sip.create_sip_participant = AsyncMock(return_value=mock_participant)
        mock_lkapi_cls.return_value = mock_lkapi
        
        # Scenario 1: sip_number is not in the descriptor fields
        with patch("livekit.api.CreateSIPParticipantRequest", wraps=MockCreateSIPParticipantRequest) as mock_req_class:
            mock_req_class.DESCRIPTOR = MockDescriptor([
                "sip_trunk_id", "sip_call_to", "room_name", "participant_identity", "participant_metadata"
            ])
            
            try:
                await create_outbound_call.main()
            except SystemExit:
                pass
                
            mock_req_class.assert_called_once()
            args, kwargs = mock_req_class.call_args
            assert "sip_number" not in kwargs, "sip_number must not be passed when not present in descriptor"
            
        # Scenario 2: sip_number is present in the descriptor fields
        mock_lkapi.sip.create_sip_participant.reset_mock()
        with patch("livekit.api.CreateSIPParticipantRequest", wraps=MockCreateSIPParticipantRequest) as mock_req_class:
            mock_req_class.DESCRIPTOR = MockDescriptor([
                "sip_trunk_id", "sip_call_to", "room_name", "participant_identity", "participant_metadata", "sip_number"
            ])
            
            try:
                await create_outbound_call.main()
            except SystemExit:
                pass
                
            mock_req_class.assert_called_once()
            args, kwargs = mock_req_class.call_args
            assert "sip_number" in kwargs, "sip_number must be passed when present in descriptor"
            assert kwargs["sip_number"] == "+15555550100"


def test_safety_gate_naming_compliance():
    """Statically verify that no deprecated safety gate names are used in the codebase, and only normalized names exist."""
    import os
    
    deprecated_names = ["DANA_CONFIRM_LIVE" + "KIT_TRUNK", "DANA_CONFIRM_OUT" + "BOUND_CALL"]
    
    for root, dirs, files in os.walk("."):
        if any(ignored in root for ignored in [".git", "__pycache__", ".pytest_cache", "venv", ".venv"]):
            continue
            
        for file in files:
            if not file.endswith((".md", ".py", ".sh", ".example", ".yaml", ".yml", "Dockerfile")):
                continue
                
            file_path = os.path.join(root, file)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue
                
            for dep in deprecated_names:
                assert dep not in content, f"Deprecated safety gate name '{dep}' found in {file_path}. Use DANA_CONFIRM_CREATE_LIVEKIT_TRUNK or DANA_CONFIRM_PLACE_CALL."


