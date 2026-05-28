import pytest
from telephony.agent_availability import LicensedAgent, InMemoryAgentAvailabilityStore
from telephony.handoff_summary import build_handoff_summary
from telephony.transfer_router import TransferRouter


@pytest.mark.asyncio
async def test_router_warm_bridge_preferred(monkeypatch) -> None:
    # 1. Warm bridge agent is available for FL
    agent = LicensedAgent(
        agent_id="agent-123",
        name="Sam",
        phone_number="+15550000001",
        licensed_states=["FL"],
        status="available"
    )
    store = InMemoryAgentAvailabilityStore([agent])
    router = TransferRouter(store)

    monkeypatch.setenv("DANA_TRANSFER_MODE", "auto")
    monkeypatch.setenv("DANA_COLD_TRANSFER_ENABLED", "true")
    monkeypatch.setenv("DANA_COLD_TRANSFER_PHONE_NUMBER", "+15559999999")

    # Lead has state FL
    lead = {"call_id": "call-123", "lead_state": "FL"}
    decision = await router.route_transfer(
        lead_state="FL",
        call_id="call-123",
        lead_profile=lead
    )

    assert decision.success is True
    assert decision.transfer_mode == "warm_bridge"
    assert decision.agent is not None
    assert decision.agent.agent_id == "agent-123"
    assert decision.phone_number == "+15550000001"
    
    # Confirm agent call count increased (reserved)
    assert agent.current_call_count == 1
    assert agent.status == "busy"  # because max_concurrent_calls defaults to 1


@pytest.mark.asyncio
async def test_router_cold_transfer_fallback(monkeypatch) -> None:
    # No agent available for FL
    store = InMemoryAgentAvailabilityStore([])
    router = TransferRouter(store)

    monkeypatch.setenv("DANA_TRANSFER_MODE", "auto")
    monkeypatch.setenv("DANA_COLD_TRANSFER_ENABLED", "true")
    monkeypatch.setenv("DANA_COLD_TRANSFER_PHONE_NUMBER", "+15559999999")

    # Lead has state FL
    lead = {"call_id": "call-123", "lead_state": "FL"}
    decision = await router.route_transfer(
        lead_state="FL",
        call_id="call-123",
        lead_profile=lead
    )

    assert decision.success is True
    assert decision.transfer_mode == "cold_transfer"
    assert decision.phone_number == "+15559999999"


@pytest.mark.asyncio
async def test_router_callback_fallback(monkeypatch) -> None:
    # No agents, cold transfer disabled
    store = InMemoryAgentAvailabilityStore([])
    router = TransferRouter(store)

    monkeypatch.setenv("DANA_TRANSFER_MODE", "auto")
    monkeypatch.setenv("DANA_COLD_TRANSFER_ENABLED", "false")

    # Lead has state FL
    lead = {"call_id": "call-123", "lead_state": "FL"}
    decision = await router.route_transfer(
        lead_state="FL",
        call_id="call-123",
        lead_profile=lead
    )

    assert decision.success is False
    assert decision.transfer_mode == "callback_required"
    assert decision.reason == "no_agent_available"


@pytest.mark.asyncio
async def test_router_missing_state_routing(monkeypatch) -> None:
    # Wildcard agent is available
    agent_wildcard = LicensedAgent(
        agent_id="agent-wild",
        name="Wildcard Agent",
        phone_number="+15550000002",
        licensed_states=["*"],
        status="available"
    )
    store = InMemoryAgentAvailabilityStore([agent_wildcard])
    router = TransferRouter(store)

    monkeypatch.setenv("DANA_TRANSFER_MODE", "warm_bridge")

    # 1. Lead has NO state (None) -> should route to wildcard agent
    lead_no_state = {"call_id": "call-123", "lead_state": None}
    decision = await router.route_transfer(
        lead_state=None,
        call_id="call-123",
        lead_profile=lead_no_state
    )
    assert decision.success is True
    assert decision.transfer_mode == "warm_bridge"
    assert decision.agent.agent_id == "agent-wild"

    # Reset agent call count
    await store.release_agent("agent-wild", "call-123")

    # 2. If wildcard agent is busy, and lead has NO state -> should fail to callback_required with missing_state_for_licensed_routing
    agent_wildcard.status = "busy"
    decision2 = await router.route_transfer(
        lead_state=None,
        call_id="call-123",
        lead_profile=lead_no_state
    )
    assert decision2.success is False
    assert decision2.transfer_mode == "callback_required"
    assert decision2.reason == "missing_state_for_licensed_routing"


def test_handoff_summary_formatting() -> None:
    lead = {
        "open_to_review": True,
        "age_range_confirmed": True,
        "living_independently": True,
        "financial_decision_maker": True,
        "callback_requested": False,
        "notes": ["Pricing objection handled", "Nursing home check passed"],
    }
    
    summary = build_handoff_summary(lead)
    
    assert "DO NOT SPEAK TO PROSPECT" in summary
    assert "- Open to Review: True" in summary
    assert "- Age Range Confirmed: True" in summary
    assert "- Living Independently: True" in summary
    assert "- Financial Decision Maker: True" in summary
    assert "Pricing objection handled" in summary
    assert "Nursing home check passed" in summary
    assert "- Callback Preference: None" in summary
