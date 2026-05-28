from __future__ import annotations

import os
import pytest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from core.livekit_runtime_adapter import LiveKitRuntimeAdapter
from core.call_state import CallStage
from core.agent_runtime import RuntimeResult
from livekit.agents import llm
from main import DanaAgent, SharedComponents, VoiceConfig


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


@pytest.mark.asyncio
async def test_adapter_dnc_does_not_hit_llm(project_root: Path) -> None:
    """If a prospect says 'do not call me again', DNC should trigger immediately without calling the LLM."""
    adapter = LiveKitRuntimeAdapter(call_id="call-dnc-123", project_root=project_root)
    
    # Mock chat_fn to detect if LLM is called
    chat_called = False
    async def mock_chat_fn(instructions: str) -> str:
        nonlocal chat_called
        chat_called = True
        return "Unused LLM response"

    # Process turn with DNC phrase
    result = await adapter.process_user_turn("Please take me off your list and do not call again.", mock_chat_fn)
    
    # 1. LLM should NOT have been called
    assert chat_called is False
    # 2. Results should indicate immediate stop and end call
    assert result.should_end_call is True
    assert result.stage == "dnc"
    assert "remove your number" in result.agent_response.lower()
    # 3. mark_dnc tool should have been executed
    assert any("mark_dnc" in res or "marked as dnc" in res.lower() for res in result.tool_results)


@pytest.mark.asyncio
async def test_adapter_wrong_number_does_not_hit_llm(project_root: Path) -> None:
    """If a prospect says 'wrong number', it should terminate politely without calling the LLM."""
    adapter = LiveKitRuntimeAdapter(call_id="call-wrong-123", project_root=project_root)
    
    chat_called = False
    async def mock_chat_fn(instructions: str) -> str:
        nonlocal chat_called
        chat_called = True
        return "Unused LLM response"

    result = await adapter.process_user_turn("Sorry, you have the wrong number.", mock_chat_fn)
    
    assert chat_called is False
    assert result.should_end_call is True
    assert "goodbye" in result.agent_response.lower() or "thank you for your time" in result.agent_response.lower()


@pytest.mark.asyncio
async def test_agent_fetransfer_not_directly_callable() -> None:
    """DanaAgent must NOT register feTransfer as a native LLM decorator function anymore."""
    # Build shared config and components
    config = VoiceConfig()
    shared = SharedComponents(config)
    
    # Instantiate DanaAgent
    from latency_metrics import LatencyRecorder
    agent = DanaAgent(shared, LatencyRecorder("test-call"))
    
    # Native LiveKit agent tools list should NOT contain feTransfer
    registered_tools = [t.info.name for t in agent.tools] if hasattr(agent, "tools") else []
    assert "feTransfer" not in registered_tools, "feTransfer tool is registered as a native LLM decorator tool!"


@pytest.mark.asyncio
async def test_adapter_happy_path_qualifies_and_transfers(project_root: Path, monkeypatch) -> None:
    """A qualified prospect (age, independent living, financial decision maker) triggers transfer."""
    monkeypatch.setenv("LICENSED_AGENT_PHONE_NUMBER", "+15551234567")
    monkeypatch.setenv("DANA_CONFIRM_TRANSFER_CALL", "yes")

    adapter = LiveKitRuntimeAdapter(call_id="call-happy-123", project_root=project_root)
    
    # 1. Start call (Opening)
    # 2. Say yes to opening -> Transitions to PERMISSION stage
    async def chat_fn_1(inst: str) -> str:
        return "Can I ask you a few quick questions?"
    res1 = await adapter.process_user_turn("Yes, I am interested.", chat_fn_1)
    assert res1.stage == "permission"

    # 3. Say yes to permission -> Transitions to AGE stage
    async def chat_fn_permission(inst: str) -> str:
        return "Okay. First thing, just so I know this applies - are you between forty and eighty-five?"
    res_perm = await adapter.process_user_turn("Yes", chat_fn_permission)
    assert res_perm.stage == "age"

    # Let's bypass the rest of the qualification stages and force stage to TRANSFER_READY to test transfer triggering.
    adapter.state_machine.call_state.transition_to(CallStage.TRANSFER_READY)
    
    # Mock successful feTransfer execution
    # Ensure fe_transfer mock returns success
    from telephony.fe_transfer import FeTransferResult
    async def mock_fe_transfer(*args, **kwargs):
        return FeTransferResult(
            success=True,
            reason="success",
            transfer_mode="cold_transfer",
            call_summary="Qualified lead summary"
        )
    from tools import fe_transfer as tools_fe_transfer
    monkeypatch.setattr(tools_fe_transfer, "fe_transfer", mock_fe_transfer)

    async def chat_fn_2(inst: str) -> str:
        return "Perfect. Stay right there for me."
    
    res2 = await adapter.process_user_turn("Yes, connect me.", chat_fn_2)
    
    # Check that transfer was triggered deterministically
    assert res2.stage == "end"
    assert res2.should_end_call is True
    assert any("success=True" in log or "cold_transfer" in log or "Transfer logged" in log or "Transfer execution" in log for log in res2.tool_results)


@pytest.mark.asyncio
async def test_adapter_transfer_failure_offers_callback(project_root: Path, monkeypatch) -> None:
    """If transfer fails (stub/not implemented), state transitions to callback and offers callback."""
    # Force transfer tool failure
    from telephony.fe_transfer import FeTransferResult
    async def mock_fe_transfer_fail(*args, **kwargs):
        return FeTransferResult(
            success=False,
            reason="fe_transfer_not_implemented",
            transfer_mode="failed"
        )
    from tools import fe_transfer as tools_fe_transfer
    monkeypatch.setattr(tools_fe_transfer, "fe_transfer", mock_fe_transfer_fail)

    adapter = LiveKitRuntimeAdapter(call_id="call-fail-123", project_root=project_root)
    adapter.state_machine.call_state.transition_to(CallStage.TRANSFER_READY)
    
    # Process turn agreeing to transfer
    async def mock_chat(inst: str) -> str:
        return "Connecting you now."
    result = await adapter.process_user_turn("Yes, bring them on.", mock_chat)
    
    # 1. State machine should transition to CALLBACK stage
    assert result.stage == "callback"
    assert adapter.state_machine.call_state.current_stage == CallStage.CALLBACK
    # 2. Agent response should offer a callback
    assert "unable to connect" in result.agent_response.lower()
    # 3. It should NOT end the call immediately
    assert result.should_end_call is False


@pytest.mark.asyncio
async def test_call_stop_policy_refusal_isolation(project_root: Path) -> None:
    """Two independent adapter instances have separate CallStopPolicies, so refusal counts do not leak."""
    adapter_a = LiveKitRuntimeAdapter(call_id="call-A", project_root=project_root)
    adapter_b = LiveKitRuntimeAdapter(call_id="call-B", project_root=project_root)

    # 1. Prospect A refuses twice
    res_a1 = adapter_a.call_stop_policy.should_stop("No.", adapter_a.state_machine.call_state)
    res_a2 = adapter_a.call_stop_policy.should_stop("No.", adapter_a.state_machine.call_state)
    assert res_a1.should_stop is False
    assert res_a2.should_stop is False
    assert adapter_a.call_stop_policy._consecutive_refusals == 2

    # 2. Prospect B refuses once
    res_b1 = adapter_b.call_stop_policy.should_stop("No.", adapter_b.state_machine.call_state)
    assert res_b1.should_stop is False
    # Check that B's count is exactly 1, meaning it did NOT inherit A's count of 2!
    assert adapter_b.call_stop_policy._consecutive_refusals == 1

    # 3. Prospect A refuses a third time -> triggers stop
    res_a3 = adapter_a.call_stop_policy.should_stop("No.", adapter_a.state_machine.call_state)
    assert res_a3.should_stop is True
    assert res_a3.stop_type == "repeated_refusal"

    # 4. Prospect B is still active and doesn't stop
    assert adapter_b.call_stop_policy._consecutive_refusals == 1
    res_b2 = adapter_b.call_stop_policy.should_stop("No.", adapter_b.state_machine.call_state)
    assert res_b2.should_stop is False
