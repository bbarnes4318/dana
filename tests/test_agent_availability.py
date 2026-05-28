import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from telephony.agent_availability import LicensedAgent, InMemoryAgentAvailabilityStore


@pytest.mark.asyncio
async def test_agent_sorting_priorities() -> None:
    # Set up agents with varying priorities, load, and recency
    now = datetime.now(timezone.utc)
    
    # 1. Agent A: lower priority (1), never called
    agent_a = LicensedAgent(
        agent_id="agent-A",
        name="Alice",
        phone_number="+15550000001",
        licensed_states=["FL"],
        priority=1,
        last_call_at=None
    )
    
    # 2. Agent B: higher priority (2), never called
    agent_b = LicensedAgent(
        agent_id="agent-B",
        name="Bob",
        phone_number="+15550000002",
        licensed_states=["FL"],
        priority=2,
        last_call_at=None
    )
    
    # 3. Agent C: highest priority (3), called recently
    agent_c = LicensedAgent(
        agent_id="agent-C",
        name="Charlie",
        phone_number="+15550000003",
        licensed_states=["FL"],
        priority=3,
        last_call_at=now - timedelta(minutes=5)
    )
    
    # 4. Agent D: highest priority (3), called longer ago
    agent_d = LicensedAgent(
        agent_id="agent-D",
        name="Diana",
        phone_number="+15550000004",
        licensed_states=["FL"],
        priority=3,
        last_call_at=now - timedelta(hours=2)
    )

    # 5. Agent E: highest priority (3), never called (should be preferred over C and D)
    agent_e = LicensedAgent(
        agent_id="agent-E",
        name="Eve",
        phone_number="+15550000005",
        licensed_states=["FL"],
        priority=3,
        last_call_at=None
    )

    store = InMemoryAgentAvailabilityStore([agent_a, agent_b, agent_c, agent_d, agent_e])
    
    # Verify that E is selected first (highest priority 3, never called)
    best = await store.select_and_reserve_agent("FL", "call-1")
    assert best is not None
    assert best.agent_id == "agent-E"
    
    # Now D should be selected (priority 3, called longer ago than C)
    best = await store.select_and_reserve_agent("FL", "call-2")
    assert best is not None
    assert best.agent_id == "agent-D"
    
    # Now C should be selected (priority 3, called recently)
    best = await store.select_and_reserve_agent("FL", "call-3")
    assert best is not None
    assert best.agent_id == "agent-C"
    
    # Now B should be selected (priority 2, never called)
    best = await store.select_and_reserve_agent("FL", "call-4")
    assert best is not None
    assert best.agent_id == "agent-B"


@pytest.mark.asyncio
async def test_state_matching_rules() -> None:
    # Agent F: licensed in TX only
    agent_f = LicensedAgent(
        agent_id="agent-F",
        name="Frank",
        phone_number="+15550000006",
        licensed_states=["TX"]
    )
    
    # Agent G: licensed globally ("*")
    agent_g = LicensedAgent(
        agent_id="agent-G",
        name="Grace",
        phone_number="+15550000007",
        licensed_states=["*"]
    )
    
    store = InMemoryAgentAvailabilityStore([agent_f, agent_g])
    
    # Match specific state: FL (only G should match)
    best = await store.select_and_reserve_agent("FL", "call-1")
    assert best is not None
    assert best.agent_id == "agent-G"
    
    # Match specific state: TX (F should match, since Grace is busy)
    best = await store.select_and_reserve_agent("TX", "call-2")
    assert best is not None
    assert best.agent_id == "agent-F"
    
    # Clean up Grace
    await store.release_agent("agent-G", "call-1")
    
    # Match no state (None): only wildcard G should match
    best = await store.select_and_reserve_agent(None, "call-3")
    assert best is not None
    assert best.agent_id == "agent-G"


@pytest.mark.asyncio
async def test_atomic_reservation_lock() -> None:
    agent = LicensedAgent(
        agent_id="agent-H",
        name="Hank",
        phone_number="+15550000008",
        licensed_states=["*"],
        max_concurrent_calls=1
    )
    store = InMemoryAgentAvailabilityStore([agent])
    
    # Attempt to reserve the same agent concurrently 10 times
    async def try_reserve(call_id: str) -> bool:
        # Check and reserve atomically in one lock-guarded block
        agent_avail = await store.select_and_reserve_agent("*", call_id)
        return agent_avail is not None
        
    results = await asyncio.gather(*(try_reserve(f"call-{i}") for i in range(10)))
    
    # Exactly one reservation must succeed
    assert sum(1 for r in results if r is True) == 1
