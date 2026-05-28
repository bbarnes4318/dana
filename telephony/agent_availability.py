"""
Agent Availability Management for Licensed Insurance Agents
Tracks agent status, licensed states, concurrency limits, and handles atomic reservations.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional


@dataclass
class LicensedAgent:
    """Represents a licensed insurance agent available to take call transfers."""
    agent_id: str
    name: str
    phone_number: str
    licensed_states: List[str]  # e.g., ["FL", "TX"], or ["*"] for wildcard/all states
    status: str = "available"  # "available", "busy", "offline"
    priority: int = 1  # Higher priority value gets routed first
    max_concurrent_calls: int = 1
    current_call_count: int = 0
    last_call_at: Optional[datetime] = None
    browser_join_enabled: bool = False


class AgentAvailabilityStore:
    """Abstract interface for managing and querying licensed agent availability."""

    async def get_available_agent(self, state: Optional[str]) -> Optional[LicensedAgent]:
        """Find the best available agent for a given state and atomically reserve them if possible."""
        raise NotImplementedError

    async def update_agent_status(self, agent_id: str, status: str) -> None:
        """Update the status of an agent (e.g. available, busy, offline)."""
        raise NotImplementedError

    async def increment_call_count(self, agent_id: str) -> None:
        """Increment the current call count for an agent."""
        raise NotImplementedError

    async def decrement_call_count(self, agent_id: str) -> None:
        """Decrement the current call count for an agent."""
        raise NotImplementedError

    async def reserve_agent(self, agent_id: str, call_id: str) -> bool:
        """Atomically reserve an agent for a specific call, updating their load and status."""
        raise NotImplementedError

    async def release_agent(self, agent_id: str, call_id: str) -> None:
        """Atomically release an agent's reservation, updating their load and status."""
        raise NotImplementedError


class InMemoryAgentAvailabilityStore(AgentAvailabilityStore):
    """In-memory implementation of AgentAvailabilityStore with asyncio.Lock for atomicity."""

    def __init__(self, agents: Optional[List[LicensedAgent]] = None) -> None:
        self._agents: dict[str, LicensedAgent] = {}
        self._lock = asyncio.Lock()
        
        if agents:
            for agent in agents:
                self._agents[agent.agent_id] = agent

    def add_agent(self, agent: LicensedAgent) -> None:
        """Add an agent to the store (primarily for tests/dev configuration)."""
        self._agents[agent.agent_id] = agent

    async def get_available_agent(self, state: Optional[str]) -> Optional[LicensedAgent]:
        """Find the best available agent for a given state. Does NOT reserve them automatically.
        
        To avoid race conditions, call reserve_agent on the returned agent's ID.
        """
        async with self._lock:
            matched_agents: List[LicensedAgent] = []
            
            # Normalize state for matching
            target_state = state.strip().upper() if state else None
            
            for agent in self._agents.values():
                # 1. Filter out offline/busy agents
                if agent.status != "available":
                    continue
                    
                # 2. Filter out agents at capacity
                if agent.current_call_count >= agent.max_concurrent_calls:
                    continue
                    
                # 3. Check licensed states
                licensed_upper = [s.upper() for s in agent.licensed_states]
                
                # If state is provided, agent must have specific state or wildcard "*"
                if target_state:
                    if target_state in licensed_upper or "*" in licensed_upper:
                        matched_agents.append(agent)
                else:
                    # If state is not provided, agent must be wildcard licensed
                    if "*" in licensed_upper:
                        matched_agents.append(agent)
            
            if not matched_agents:
                return None
                
            # Sort matched agents based on rules:
            # 1. Higher priority first (descending)
            # 2. Lower current call count first (ascending)
            # 3. Oldest last_call_at first (ascending), treating None as oldest (never called)
            def sort_key(a: LicensedAgent) -> tuple[int, int, float]:
                # Priority: higher is better, so negate it for ascending sort
                priority_key = -a.priority
                
                # Load: lower is better
                load_key = a.current_call_count
                
                # Recency: older timestamp is better
                if a.last_call_at is None:
                    # Treat None as oldest: use timestamp 0 (beginning of epoch)
                    timestamp_key = 0.0
                else:
                    timestamp_key = a.last_call_at.replace(tzinfo=timezone.utc).timestamp()
                    
                return (priority_key, load_key, timestamp_key)
                
            matched_agents.sort(key=sort_key)
            return matched_agents[0]

    async def update_agent_status(self, agent_id: str, status: str) -> None:
        async with self._lock:
            if agent_id in self._agents:
                self._agents[agent_id].status = status

    async def increment_call_count(self, agent_id: str) -> None:
        async with self._lock:
            if agent_id in self._agents:
                agent = self._agents[agent_id]
                agent.current_call_count += 1
                if agent.current_call_count >= agent.max_concurrent_calls:
                    agent.status = "busy"
                agent.last_call_at = datetime.now(timezone.utc)

    async def decrement_call_count(self, agent_id: str) -> None:
        async with self._lock:
            if agent_id in self._agents:
                agent = self._agents[agent_id]
                agent.current_call_count = max(0, agent.current_call_count - 1)
                if agent.current_call_count < agent.max_concurrent_calls:
                    agent.status = "available"

    async def reserve_agent(self, agent_id: str, call_id: str) -> bool:
        """Atomically reserve an agent by ID if they are available."""
        async with self._lock:
            if agent_id not in self._agents:
                return False
                
            agent = self._agents[agent_id]
            if agent.status != "available" or agent.current_call_count >= agent.max_concurrent_calls:
                return False
                
            agent.current_call_count += 1
            if agent.current_call_count >= agent.max_concurrent_calls:
                agent.status = "busy"
            agent.last_call_at = datetime.now(timezone.utc)
            return True

    async def release_agent(self, agent_id: str, call_id: str) -> None:
        """Atomically release an agent's reservation."""
        async with self._lock:
            if agent_id in self._agents:
                agent = self._agents[agent_id]
                agent.current_call_count = max(0, agent.current_call_count - 1)
                if agent.current_call_count < agent.max_concurrent_calls:
                    agent.status = "available"
