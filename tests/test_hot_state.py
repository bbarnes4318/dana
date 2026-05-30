"""Tests for HotStateStore (InMemory and Redis implementations)."""

from __future__ import annotations

import asyncio
from unittest import mock
import pytest

from runtime.hot_state import InMemoryHotStateStore, RedisHotStateStore


@pytest.mark.asyncio
async def test_in_memory_store_counters():
    """Verify in-memory hot state counter operations work correctly."""
    store = InMemoryHotStateStore(key_prefix="test")
    
    # Check default is 0
    assert await store.get_counter("c1") == 0
    
    # Increment
    assert await store.increment_counter("c1", 1) == 1
    assert await store.increment_counter("c1", 5) == 6
    assert await store.get_counter("c1") == 6

    # Decrement
    assert await store.decrement_counter("c1", 2) == 4
    assert await store.get_counter("c1") == 4


@pytest.mark.asyncio
async def test_in_memory_store_json():
    """Verify JSON set and get work in in-memory store."""
    store = InMemoryHotStateStore(key_prefix="test")
    
    payload = {"status": "dialing", "attempts": 2}
    await store.set_json("call:123", payload)
    
    retrieved = await store.get_json("call:123")
    assert retrieved == payload

    # Test delete
    assert await store.delete("call:123") is True
    assert await store.get_json("call:123") is None
    assert await store.delete("call:123") is False


@pytest.mark.asyncio
async def test_in_memory_store_locking():
    """Verify lock acquisition, heartbeats, and releases in in-memory store."""
    store = InMemoryHotStateStore(key_prefix="test")
    
    # Acquire lock
    assert await store.acquire_lock("resource", "owner_a", 10) is True
    
    # Cannot be acquired by owner_b
    assert await store.acquire_lock("resource", "owner_b", 10) is False
    
    # Can renew by owner_a
    assert await store.acquire_lock("resource", "owner_a", 15) is True

    # Heartbeat works for owner_a
    assert await store.heartbeat("resource", "owner_a", 20) is True
    # Heartbeat fails for owner_b
    assert await store.heartbeat("resource", "owner_b", 20) is False

    # Release lock fails for owner_b
    assert await store.release_lock("resource", "owner_b") is False
    # Release lock succeeds for owner_a
    assert await store.release_lock("resource", "owner_a") is True
    
    # Lock is free now
    assert await store.acquire_lock("resource", "owner_b", 10) is True


@pytest.mark.asyncio
async def test_in_memory_store_expiration():
    """Verify expiry removes values in in-memory store."""
    store = InMemoryHotStateStore(key_prefix="test")
    
    await store.set_json("temp", "value", expiry=-1)  # expired immediately
    assert await store.get_json("temp") is None

    await store.increment_counter("temp_c", 1, expiry=-1)
    assert await store.get_counter("temp_c") == 0


@pytest.mark.asyncio
async def test_redis_store_mocked():
    """Verify RedisHotStateStore calls correct redis commands using a mock client."""
    mock_redis = mock.AsyncMock()
    
    # Setup mock returns
    mock_redis.incrby.return_value = 10
    mock_redis.decrby.return_value = 9
    mock_redis.get.return_value = '{"foo": "bar"}'
    mock_redis.set.return_value = True
    mock_redis.delete.return_value = 1
    mock_redis.eval.return_value = 1
    
    with mock.patch("redis.asyncio.Redis.from_url", return_value=mock_redis):
        store = RedisHotStateStore("redis://localhost:6379/0", key_prefix="test")
        
        # Test counter
        assert await store.increment_counter("c1", 10) == 10
        mock_redis.incrby.assert_called_with("test:c1", 10)

        assert await store.decrement_counter("c1", 1) == 9
        mock_redis.decrby.assert_called_with("test:c1", 1)

        # Test JSON
        assert await store.get_json("key") == {"foo": "bar"}
        mock_redis.get.assert_called_with("test:key")

        await store.set_json("key", {"a": 1}, expiry=60)
        mock_redis.set.assert_called_with("test:key", '{"a": 1}', ex=60)

        assert await store.delete("key") is True
        mock_redis.delete.assert_called_with("test:key")

        # Test locks
        mock_redis.set.return_value = True
        assert await store.acquire_lock("res", "owner", 10) is True
        mock_redis.set.assert_called_with("test:lock:res", "owner", nx=True, ex=10)

        assert await store.release_lock("res", "owner") is True
        assert mock_redis.eval.called
