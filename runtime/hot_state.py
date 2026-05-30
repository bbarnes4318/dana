"""Hot state storage interface and implementations (Redis and In-Memory fallback)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# Try importing redis
try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False


class BaseHotStateStore(ABC):
    """Abstract base class for hot-state storage backends."""

    @abstractmethod
    async def increment_counter(self, key: str, amount: int = 1, expiry: Optional[int] = None) -> int:
        """Increment a counter key by amount, optionally setting an expiry in seconds."""
        ...

    @abstractmethod
    async def decrement_counter(self, key: str, amount: int = 1) -> int:
        """Decrement a counter key by amount."""
        ...

    @abstractmethod
    async def get_counter(self, key: str) -> int:
        """Get the current integer value of a counter key."""
        ...

    @abstractmethod
    async def set_json(self, key: str, value: Any, expiry: Optional[int] = None) -> None:
        """Serialize value to JSON and store it under key, optionally setting an expiry in seconds."""
        ...

    @abstractmethod
    async def get_json(self, key: str) -> Any:
        """Retrieve key, parsing its contents as JSON. Returns None if missing."""
        ...

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete a key. Returns True if deleted, False if key did not exist."""
        ...

    @abstractmethod
    async def acquire_lock(self, name: str, owner: str, expiry: int) -> bool:
        """Acquire a lock with a given owner ID and expiry in seconds. Returns True if acquired."""
        ...

    @abstractmethod
    async def release_lock(self, name: str, owner: str) -> bool:
        """Release a lock if it belongs to the owner. Returns True if successfully released."""
        ...

    @abstractmethod
    async def heartbeat(self, name: str, owner: str, expiry: int) -> bool:
        """Refresh a lock's expiry if held by the owner. Returns True if refreshed."""
        ...

    @abstractmethod
    async def list_active(self, pattern: str) -> List[str]:
        """List all active keys matching a pattern."""
        ...

    @abstractmethod
    async def is_healthy(self) -> bool:
        """Check if the store connection is healthy."""
        ...


class InMemoryHotStateStore(BaseHotStateStore):
    """Local, thread-safe, in-memory implementation of the hot-state store.

    Useful for development, testing, and degraded fallback modes.
    """

    def __init__(self, key_prefix: str = "dana") -> None:
        self._key_prefix = key_prefix
        self._data: Dict[str, Any] = {}
        self._expirations: Dict[str, float] = {}
        self._lock = asyncio.Lock()

    def _prefix_key(self, key: str) -> str:
        if key.startswith(f"{self._key_prefix}:"):
            return key
        return f"{self._key_prefix}:{key}"

    def _is_expired(self, prefixed_key: str) -> bool:
        if prefixed_key not in self._expirations:
            return False
        if time.time() > self._expirations[prefixed_key]:
            # Clean up on access
            self._data.pop(prefixed_key, None)
            self._expirations.pop(prefixed_key, None)
            return True
        return False

    async def increment_counter(self, key: str, amount: int = 1, expiry: Optional[int] = None) -> int:
        prefixed = self._prefix_key(key)
        async with self._lock:
            self._is_expired(prefixed)
            val = self._data.get(prefixed, 0)
            if not isinstance(val, int):
                try:
                    val = int(val)
                except (ValueError, TypeError):
                    val = 0
            new_val = val + amount
            self._data[prefixed] = new_val
            if expiry is not None:
                self._expirations[prefixed] = time.time() + expiry
            return new_val

    async def decrement_counter(self, key: str, amount: int = 1) -> int:
        prefixed = self._prefix_key(key)
        async with self._lock:
            self._is_expired(prefixed)
            val = self._data.get(prefixed, 0)
            if not isinstance(val, int):
                try:
                    val = int(val)
                except (ValueError, TypeError):
                    val = 0
            new_val = val - amount
            self._data[prefixed] = new_val
            return new_val

    async def get_counter(self, key: str) -> int:
        prefixed = self._prefix_key(key)
        async with self._lock:
            if self._is_expired(prefixed):
                return 0
            val = self._data.get(prefixed, 0)
            try:
                return int(val)
            except (ValueError, TypeError):
                return 0

    async def set_json(self, key: str, value: Any, expiry: Optional[int] = None) -> None:
        prefixed = self._prefix_key(key)
        # Store as JSON string to simulate serialization behavior
        serialized = json.dumps(value)
        async with self._lock:
            self._data[prefixed] = serialized
            if expiry is not None:
                self._expirations[prefixed] = time.time() + expiry
            else:
                self._expirations.pop(prefixed, None)

    async def get_json(self, key: str) -> Any:
        prefixed = self._prefix_key(key)
        async with self._lock:
            if self._is_expired(prefixed):
                return None
            val = self._data.get(prefixed, None)
            if val is None:
                return None
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                return val

    async def delete(self, key: str) -> bool:
        prefixed = self._prefix_key(key)
        async with self._lock:
            self._expirations.pop(prefixed, None)
            if prefixed in self._data:
                self._data.pop(prefixed)
                return True
            return False

    async def acquire_lock(self, name: str, owner: str, expiry: int) -> bool:
        prefixed = self._prefix_key(f"lock:{name}")
        async with self._lock:
            is_exp = self._is_expired(prefixed)
            current_owner = self._data.get(prefixed)
            if current_owner is None or is_exp:
                # Lock is free or expired
                self._data[prefixed] = owner
                self._expirations[prefixed] = time.time() + expiry
                return True
            if current_owner == owner:
                # Already owned by same caller, extend it
                self._expirations[prefixed] = time.time() + expiry
                return True
            return False

    async def release_lock(self, name: str, owner: str) -> bool:
        prefixed = self._prefix_key(f"lock:{name}")
        async with self._lock:
            if self._is_expired(prefixed):
                return False
            current_owner = self._data.get(prefixed)
            if current_owner == owner:
                self._data.pop(prefixed, None)
                self._expirations.pop(prefixed, None)
                return True
            return False

    async def heartbeat(self, name: str, owner: str, expiry: int) -> bool:
        prefixed = self._prefix_key(f"lock:{name}")
        async with self._lock:
            if self._is_expired(prefixed):
                return False
            current_owner = self._data.get(prefixed)
            if current_owner == owner:
                self._expirations[prefixed] = time.time() + expiry
                return True
            return False

    async def list_active(self, pattern: str) -> List[str]:
        # Simple glob-like pattern matching (converting * to wildcard search)
        import fnmatch
        prefixed_pattern = self._prefix_key(pattern)
        async with self._lock:
            # Filter expired keys first
            active_keys = []
            for k in list(self._data.keys()):
                if not self._is_expired(k):
                    active_keys.append(k)
            # Match pattern
            matched = fnmatch.filter(active_keys, prefixed_pattern)
            # Remove prefixes when returning keys
            prefix_len = len(self._key_prefix) + 1
            return [k[prefix_len:] for k in matched]

    async def is_healthy(self) -> bool:
        return True


class RedisHotStateStore(BaseHotStateStore):
    """Redis-backed implementation of the hot-state store for production.

    Uses `redis.asyncio` for non-blocking I/O.
    """

    def __init__(self, redis_url: str, key_prefix: str = "dana") -> None:
        if not REDIS_AVAILABLE:
            raise ImportError("The 'redis' package is required but not installed.")
        self._redis_url = redis_url
        self._key_prefix = key_prefix
        self._client: Optional[aioredis.Redis] = None
        self._connect_lock = asyncio.Lock()

    async def _get_client(self) -> aioredis.Redis:
        if self._client is not None:
            return self._client
        async with self._connect_lock:
            if self._client is None:
                logger.info("Initializing async Redis client with URL: %s", self._redis_url)
                self._client = aioredis.Redis.from_url(
                    self._redis_url, 
                    decode_responses=True,
                    socket_timeout=2.0,
                    socket_connect_timeout=2.0
                )
            return self._client

    def _prefix_key(self, key: str) -> str:
        if key.startswith(f"{self._key_prefix}:"):
            return key
        return f"{self._key_prefix}:{key}"

    async def increment_counter(self, key: str, amount: int = 1, expiry: Optional[int] = None) -> int:
        client = await self._get_client()
        prefixed = self._prefix_key(key)
        val = await client.incrby(prefixed, amount)
        if expiry is not None:
            await client.expire(prefixed, expiry)
        return val

    async def decrement_counter(self, key: str, amount: int = 1) -> int:
        client = await self._get_client()
        prefixed = self._prefix_key(key)
        return await client.decrby(prefixed, amount)

    async def get_counter(self, key: str) -> int:
        client = await self._get_client()
        prefixed = self._prefix_key(key)
        val = await client.get(prefixed)
        if val is None:
            return 0
        try:
            return int(val)
        except (ValueError, TypeError):
            return 0

    async def set_json(self, key: str, value: Any, expiry: Optional[int] = None) -> None:
        client = await self._get_client()
        prefixed = self._prefix_key(key)
        serialized = json.dumps(value)
        if expiry is not None:
            await client.set(prefixed, serialized, ex=expiry)
        else:
            await client.set(prefixed, serialized)

    async def get_json(self, key: str) -> Any:
        client = await self._get_client()
        prefixed = self._prefix_key(key)
        val = await client.get(prefixed)
        if val is None:
            return None
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val

    async def delete(self, key: str) -> bool:
        client = await self._get_client()
        prefixed = self._prefix_key(key)
        deleted_count = await client.delete(prefixed)
        return deleted_count > 0

    async def acquire_lock(self, name: str, owner: str, expiry: int) -> bool:
        client = await self._get_client()
        prefixed = self._prefix_key(f"lock:{name}")
        # NX = set only if key does not exist, EX = set expiry in seconds
        acquired = await client.set(prefixed, owner, nx=True, ex=expiry)
        if acquired:
            return True
        # Check if already owned by same owner
        val = await client.get(prefixed)
        if val == owner:
            # Renew the lock
            await client.expire(prefixed, expiry)
            return True
        return False

    async def release_lock(self, name: str, owner: str) -> bool:
        client = await self._get_client()
        prefixed = self._prefix_key(f"lock:{name}")
        # Lua script to release lock atomically only if the owner matches
        lua_release = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        result = await client.eval(lua_release, 1, prefixed, owner)
        return result == 1

    async def heartbeat(self, name: str, owner: str, expiry: int) -> bool:
        client = await self._get_client()
        prefixed = self._prefix_key(f"lock:{name}")
        # Lua script to atomically extend expiry only if the owner matches
        lua_heartbeat = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("expire", KEYS[1], ARGV[2])
        else
            return 0
        end
        """
        result = await client.eval(lua_heartbeat, 1, prefixed, owner, expiry)
        return result == 1

    async def list_active(self, pattern: str) -> List[str]:
        client = await self._get_client()
        prefixed_pattern = self._prefix_key(pattern)
        # Scan matching keys
        cursor = 0
        keys = []
        while True:
            cursor, batch = await client.scan(cursor=cursor, match=prefixed_pattern, count=100)
            keys.extend(batch)
            if cursor == 0:
                break
        
        # Remove prefixes
        prefix_len = len(self._key_prefix) + 1
        return [k[prefix_len:] for k in keys]

    async def is_healthy(self) -> bool:
        try:
            client = await self._get_client()
            return await client.ping()
        except Exception:
            return False

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# Global store instance accessor
_store_instance: Optional[BaseHotStateStore] = None
_store_lock = asyncio.Lock()
_degraded_mode = False


async def get_hot_state_store() -> BaseHotStateStore:
    """Retrieve the configured HotStateStore instance."""
    global _store_instance, _degraded_mode
    if _store_instance is not None:
        return _store_instance

    async with _store_lock:
        if _store_instance is not None:
            return _store_instance

        use_redis = os.environ.get("DANA_USE_REDIS_HOT_STATE", "false").lower() == "true"
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        prefix = os.environ.get("DANA_REDIS_KEY_PREFIX", "dana")

        if use_redis:
            if not REDIS_AVAILABLE:
                logger.warning("Redis is enabled but the 'redis' python module is not available. Falling back to InMemoryHotStateStore.")
                _degraded_mode = True
                _store_instance = InMemoryHotStateStore(key_prefix=prefix)
            else:
                try:
                    store = RedisHotStateStore(redis_url=redis_url, key_prefix=prefix)
                    # Test connection
                    if await store.is_healthy():
                        _store_instance = store
                        _degraded_mode = False
                        logger.info("Successfully connected to production RedisHotStateStore.")
                    else:
                        logger.error("Failed to ping Redis server. Falling back to InMemoryHotStateStore (DEGRADED mode).")
                        _degraded_mode = True
                        _store_instance = InMemoryHotStateStore(key_prefix=prefix)
                except Exception as e:
                    logger.error("Error connecting to Redis: %s. Falling back to InMemoryHotStateStore (DEGRADED mode).", e)
                    _degraded_mode = True
                    _store_instance = InMemoryHotStateStore(key_prefix=prefix)
        else:
            _store_instance = InMemoryHotStateStore(key_prefix=prefix)
            _degraded_mode = False

        return _store_instance


def is_degraded_mode() -> bool:
    """Returns True if the system fell back to InMemoryHotStateStore due to Redis failure."""
    return _degraded_mode
