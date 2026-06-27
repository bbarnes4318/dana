from __future__ import annotations

import os
import pytest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from core.livekit_runtime_adapter import LiveKitRuntimeAdapter
from core.call_state import CallStage
from core.agent_runtime import RuntimeResult
from main import SharedComponents, VoiceConfig
from dana.runtime.voice_session import DanaAgent


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
    assert "note of that" in result.agent_response.lower() or "take care" in result.agent_response.lower()
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
    assert "note of that" in result.agent_response.lower() or "take care" in result.agent_response.lower()


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
    """A qualified prospect progresses naturally through all stages and triggers transfer."""
    monkeypatch.setenv("LICENSED_AGENT_PHONE_NUMBER", "+15551234567")
    monkeypatch.setenv("DANA_CONFIRM_TRANSFER_CALL", "yes")

    adapter = LiveKitRuntimeAdapter(call_id="call-happy-123", project_root=project_root)
    
    # 1. Start call in OPENING stage (wait_for_user)
    # User says Hello -> transitions to INTEREST_CHECK
    async def chat_greeting(inst: str) -> str:
        return "Hey, this is Alex. I'm getting back with you about final expense burial options. Are you still open to looking at those?"
    res = await adapter.process_user_turn("Hello?", chat_greeting)
    assert res.stage == "interest_check"

    # 2. Interest Check -> transitions to AGE_RANGE
    async def chat_interest(inst: str) -> str:
        return "Okay. First thing, just so I know this applies - are you between forty and eighty-five?"
    res = await adapter.process_user_turn("Yes, I'm open to that.", chat_interest)
    assert res.stage == "age_range"
    assert adapter.state_machine.lead.open_to_review is True

    # 3. Age Check -> transitions to LIVING_SITUATION
    async def chat_age(inst: str) -> str:
        return "Okay. And you're living independently, right? Not in a nursing home or assisted living?"
    res = await adapter.process_user_turn("Yeah, I'm 65.", chat_age)
    assert res.stage == "living_situation"
    assert adapter.state_machine.lead.age_range_confirmed is True

    # 4. Living Situation -> transitions to DECISION_MAKER
    async def chat_living(inst: str) -> str:
        return "Great. And you handle your own financial decisions, correct?"
    res = await adapter.process_user_turn("Correct, I live on my own.", chat_living)
    assert res.stage == "decision_maker"
    assert adapter.state_machine.lead.living_independently is True

    # 5. Decision Maker -> transitions to TRANSFER_CONSENT
    async def chat_dm(inst: str) -> str:
        return "Perfect. Hold the line for me, okay?"
    res = await adapter.process_user_turn("Yes, I handle my own finances.", chat_dm)
    assert res.stage == "transfer_consent"
    assert adapter.state_machine.lead.financial_decision_maker is True

    # Mock successful feTransfer execution
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

    # 6. Transfer Consent -> transitions to TRANSFER_READY (which triggers transfer)
    async def chat_consent(inst: str) -> str:
        return "Perfect. Stay right there for me."
    res = await adapter.process_user_turn("Yes, sure, connect me.", chat_consent)
    
    # Check that transfer was triggered in the same turn
    assert res.stage == "transfer_ready"
    assert adapter.state_machine.lead.transfer_consent_confirmed is True
    assert any("success=True" in log or "cold_transfer" in log or "Transfer execution" in log for log in res.tool_results)

    # 7. Final turn in TRANSFER_READY -> transitions to END
    res = await adapter.process_user_turn("Okay", chat_consent)
    assert res.stage == "end"
    assert res.should_end_call is True


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
    
    # Qualify the lead profile
    adapter.state_machine.lead.open_to_review = True
    adapter.state_machine.lead.age_range_confirmed = True
    adapter.state_machine.lead.living_independently = True
    adapter.state_machine.lead.financial_decision_maker = True
    adapter.state_machine.lead.transfer_consent_confirmed = True
    
    adapter.state_machine.call_state.transition_to(CallStage.TRANSFER_READY)
    
    # Process turn agreeing to transfer
    async def mock_chat(inst: str) -> str:
        return "Connecting you now."
    result = await adapter.process_user_turn("Yes, bring them on.", mock_chat)
    
    # 1. State machine should transition to CALLBACK stage
    assert result.stage == "callback"
    assert adapter.state_machine.call_state.current_stage == CallStage.CALLBACK
    # 2. Agent response should offer a callback
    assert "get the licensed agent" in result.agent_response.lower()
    assert "later today or tomorrow" in result.agent_response.lower()
    # 3. It should NOT end the call immediately
    assert result.should_end_call is False


@pytest.mark.asyncio
async def test_adapter_under_age_disqualification(project_root: Path) -> None:
    """Underage prospect triggers confirmation gate then gets disqualified."""
    adapter = LiveKitRuntimeAdapter(call_id="call-age-dq-123", project_root=project_root)
    adapter.state_machine.call_state.transition_to(CallStage.AGE_RANGE)
    
    # 1. Say "I am 25" -> should stay in age_range and add note
    async def mock_chat(inst: str) -> str:
        return "Are you not between forty and eighty-five, correct?"
    res1 = await adapter.process_user_turn("I am twenty five.", mock_chat)
    assert res1.stage == "age_range"
    assert "confirming_age" in adapter.state_machine.lead.notes
    assert res1.should_end_call is False
    
    # 2. Say "Yes, correct" -> should disqualify
    res2 = await adapter.process_user_turn("Yes, correct.", mock_chat)
    assert res2.stage == "disqualified"
    assert res2.should_end_call is True
    assert adapter.state_machine.lead.disqualified_reason == "Not between 40 and 85 years old"


@pytest.mark.asyncio
async def test_adapter_nursing_home_disqualification(project_root: Path) -> None:
    """Prospect in nursing home triggers confirmation gate then gets disqualified."""
    adapter = LiveKitRuntimeAdapter(call_id="call-care-dq-123", project_root=project_root)
    adapter.state_machine.call_state.transition_to(CallStage.LIVING_SITUATION)
    
    # 1. Say "No, I live in a home" -> should stay in living_situation and add note
    async def mock_chat(inst: str) -> str:
        return "Just to confirm, you are in a care facility, correct?"
    res1 = await adapter.process_user_turn("No, I live in a nursing home.", mock_chat)
    assert res1.stage == "living_situation"
    assert "confirming_care" in adapter.state_machine.lead.notes
    assert res1.should_end_call is False
    
    # 2. Say "Yes, correct" -> should disqualify
    res2 = await adapter.process_user_turn("Yes, that is correct.", mock_chat)
    assert res2.stage == "disqualified"
    assert res2.should_end_call is True
    assert adapter.state_machine.lead.disqualified_reason == "In care facility / care home"


@pytest.mark.asyncio
async def test_adapter_not_decision_maker_disqualification(project_root: Path) -> None:
    """Non financial decision maker triggers confirmation gate then gets disqualified."""
    adapter = LiveKitRuntimeAdapter(call_id="call-dm-dq-123", project_root=project_root)
    adapter.state_machine.call_state.transition_to(CallStage.DECISION_MAKER)
    
    # 1. Say "No, my daughter does" -> should stay in decision_maker and add note
    async def mock_chat(inst: str) -> str:
        return "Just to confirm, someone else handles decisions, correct?"
    res1 = await adapter.process_user_turn("No, my daughter handles all that.", mock_chat)
    assert res1.stage == "decision_maker"
    assert "confirming_decision_maker" in adapter.state_machine.lead.notes
    assert res1.should_end_call is False
    
    # 2. Say "Yes" -> should disqualify
    res2 = await adapter.process_user_turn("Yes, she does.", mock_chat)
    assert res2.stage == "disqualified"
    assert res2.should_end_call is True
    assert adapter.state_machine.lead.disqualified_reason == "Someone else handles financial decisions"


@pytest.mark.asyncio
async def test_adapter_spouse_joint_decision_maker(project_root: Path) -> None:
    """Spouse joint decision maker passes qualification without confirmation gate."""
    adapter = LiveKitRuntimeAdapter(call_id="call-joint-123", project_root=project_root)
    adapter.state_machine.call_state.transition_to(CallStage.DECISION_MAKER)
    
    async def mock_chat(inst: str) -> str:
        return "Hold the line."
    res = await adapter.process_user_turn("My spouse and I make decisions together.", mock_chat)
    assert res.stage == "transfer_consent"
    assert adapter.state_machine.lead.financial_decision_maker is True
    assert "confirming_decision_maker" not in adapter.state_machine.lead.notes


@pytest.mark.asyncio
async def test_adapter_spouse_joint_callback(project_root: Path) -> None:
    """Talk to spouse first transitions to callback."""
    adapter = LiveKitRuntimeAdapter(call_id="call-spouse-cb-123", project_root=project_root)
    adapter.state_machine.call_state.transition_to(CallStage.DECISION_MAKER)
    
    async def mock_chat(inst: str) -> str:
        return "Let's call you back."
    res = await adapter.process_user_turn("I need to discuss this with my husband first.", mock_chat)
    assert res.stage == "callback"
    assert adapter.state_machine.lead.callback_requested is True


@pytest.mark.asyncio
async def test_adapter_not_interested_ends_call(project_root: Path) -> None:
    """Not interested at interest check ends the call."""
    adapter = LiveKitRuntimeAdapter(call_id="call-no-interest-123", project_root=project_root)
    adapter.state_machine.call_state.transition_to(CallStage.INTEREST_CHECK)
    
    async def mock_chat(inst: str) -> str:
        return "Goodbye."
    res = await adapter.process_user_turn("No, I'm not interested.", mock_chat)
    assert res.stage == "end"
    assert res.should_end_call is True


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

