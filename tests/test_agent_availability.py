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
    best = await store.get_available_agent("FL")
    assert best is not None
    assert best.agent_id == "agent-E"
    
    # Reserve E
    success = await store.reserve_agent("agent-E", "call-1")
    assert success is True
    
    # Now D should be selected (priority 3, called longer ago than C)
    best = await store.get_available_agent("FL")
    assert best is not None
    assert best.agent_id == "agent-D"
    
    # Reserve D
    success = await store.reserve_agent("agent-D", "call-2")
    assert success is True
    
    # Now C should be selected (priority 3, called recently)
    best = await store.get_available_agent("FL")
    assert best is not None
    assert best.agent_id == "agent-C"
    
    # Reserve C
    success = await store.reserve_agent("agent-C", "call-3")
    assert success is True
    
    # Now B should be selected (priority 2, never called)
    best = await store.get_available_agent("FL")
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
    best = await store.get_available_agent("FL")
    assert best is not None
    assert best.agent_id == "agent-G"
    
    # Match specific state: TX (F should match, let's check F vs G priority. Both priority 1, F call count 0, G call count 0.
    # Recency: both None. Since Frank is specific to TX and Grace is wildcard, let's see. F's name sort or insertion order dictates.
    # In any case, Frank matches. Let's reserve Grace, then get TX agent: Frank must be returned.
    await store.reserve_agent("agent-G", "call-1")
    best = await store.get_available_agent("TX")
    assert best is not None
    assert best.agent_id == "agent-F"
    
    # Clean up Grace
    await store.release_agent("agent-G", "call-1")
    
    # Match no state (None): only wildcard G should match
    best = await store.get_available_agent(None)
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
        # Check and reserve atomically
        agent_avail = await store.get_available_agent("*")
        if agent_avail and agent_avail.agent_id == "agent-H":
            return await store.reserve_agent("agent-H", call_id)
        return False
        
    results = await asyncio.gather(*(try_reserve(f"call-{i}") for i in range(10)))
    
    # Exactly one reservation must succeed
    assert sum(1 for r in results if r is True) == 1
