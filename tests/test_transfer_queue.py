"""Tests for TransferQueue logic."""

from datetime import datetime, timedelta, timezone
import pytest
from dialer.transfer_queue import TransferQueue
from telephony.agent_availability import InMemoryAgentAvailabilityStore, LicensedAgent


class MockRepository:
    """Mock repository for TransferQueue tests."""

    def __init__(self):
        self.callbacks = {}
        self.transfers = []

    async def mark_lead_callback(self, lead_id, callback_time):
        self.callbacks[lead_id] = callback_time

    async def save_transfer(self, **kwargs):
        self.transfers.append(kwargs)


@pytest.mark.asyncio
async def test_transfer_queue_enqueue_and_sort():
    tq = TransferQueue()
    tq.enqueue("call1", "lead1", "camp1", priority=1)
    tq.enqueue("call2", "lead2", "camp1", priority=3)  # Higher priority
    tq.enqueue("call3", "lead3", "camp1", priority=1)

    items = tq.get_queue_items()
    assert len(items) == 3
    # First should be call2 (priority 3)
    assert items[0].call_id == "call2"
    # Second should be call1 (priority 1, entered first)
    assert items[1].call_id == "call1"
    # Third should be call3 (priority 1, entered later)
    assert items[2].call_id == "call3"


@pytest.mark.asyncio
async def test_transfer_queue_route_success():
    tq = TransferQueue()
    tq.enqueue("call1", "lead1", "camp1", priority=1, warm_bridge=True)
    
    agent = LicensedAgent(
        agent_id="agent1",
        name="Alex Smith",
        phone_number="+15559999",
        licensed_states=["FL"],
        status="available"
    )
    agent_store = InMemoryAgentAvailabilityStore([agent])
    repo = MockRepository()

    # Route transfer for Florida lead
    decision = await tq.route_transfer("call1", "FL", agent_store, repo)
    
    assert decision["status"] == "success"
    assert decision["action"] == "bridge"
    assert decision["agent_id"] == "agent1"
    assert decision["mode"] == "warm"

    # Agent should now be busy/reserved
    assert agent.current_call_count == 1
    assert agent.status == "busy"
    
    # Check transfer was saved
    assert len(repo.transfers) == 1
    assert repo.transfers[0]["agent_id"] == "agent1"
    assert repo.transfers[0]["success"] is True


@pytest.mark.asyncio
async def test_transfer_queue_route_fallback():
    tq = TransferQueue()
    tq.enqueue("call1", "lead1", "camp1", priority=1)
    
    # Store with no agents
    agent_store = InMemoryAgentAvailabilityStore([])
    repo = MockRepository()

    decision = await tq.route_transfer("call1", "FL", agent_store, repo)
    
    assert decision["status"] == "fallback"
    assert decision["action"] == "schedule_callback"

    # Handle fallback execution
    now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
    callback_time = await tq.handle_transfer_failure("call1", repo, fallback_delay_minutes=30, now=now)
    
    assert callback_time == now + timedelta(minutes=30)
    assert repo.callbacks["lead1"] == callback_time
    assert len(tq.get_queue_items()) == 0  # Should be removed from queue
