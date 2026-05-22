"""Tests for :class:`storage.jsonl_store.JsonlStore`.

All tests use pytest's ``tmp_path`` fixture — no external services required.
"""

from __future__ import annotations

import asyncio

import pytest

from storage.jsonl_store import JsonlStore


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    """Return a JsonlStore rooted in a temporary directory."""
    return JsonlStore(data_dir=tmp_path)


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_and_get(store: JsonlStore):
    """save() returns an id that get() can retrieve."""
    record = {"name": "Alice", "age": 65}
    record_id = await store.save("leads", record)

    assert isinstance(record_id, str)
    assert len(record_id) > 0

    fetched = await store.get("leads", record_id)
    assert fetched is not None
    assert fetched["name"] == "Alice"
    assert fetched["age"] == 65
    assert fetched["id"] == record_id


@pytest.mark.asyncio
async def test_save_preserves_existing_id(store: JsonlStore):
    """When the caller supplies an ``id``, it is honoured."""
    record_id = await store.save("leads", {"id": "custom-123", "x": 1})
    assert record_id == "custom-123"

    fetched = await store.get("leads", "custom-123")
    assert fetched is not None
    assert fetched["x"] == 1


@pytest.mark.asyncio
async def test_get_missing_returns_none(store: JsonlStore):
    """get() returns None for a non-existent id."""
    assert await store.get("leads", "no-such-id") is None


@pytest.mark.asyncio
async def test_get_missing_collection_returns_none(store: JsonlStore):
    """get() returns None when the collection file does not exist at all."""
    assert await store.get("nonexistent_collection", "any-id") is None


@pytest.mark.asyncio
async def test_list_recent(store: JsonlStore):
    """list_recent() returns records newest-first, respecting limit."""
    for i in range(10):
        await store.save("items", {"index": i})

    recent = await store.list_recent("items", limit=5)
    assert len(recent) == 5
    # Newest (last written) should come first.
    assert recent[0]["index"] == 9
    assert recent[4]["index"] == 5


@pytest.mark.asyncio
async def test_list_recent_empty_collection(store: JsonlStore):
    """list_recent() returns [] for a missing collection."""
    assert await store.list_recent("empty") == []


@pytest.mark.asyncio
async def test_query_filters(store: JsonlStore):
    """query() returns only records matching all filter keys."""
    await store.save("people", {"name": "Alice", "city": "NYC"})
    await store.save("people", {"name": "Bob", "city": "LA"})
    await store.save("people", {"name": "Carol", "city": "NYC"})

    results = await store.query("people", {"city": "NYC"})
    assert len(results) == 2
    names = {r["name"] for r in results}
    assert names == {"Alice", "Carol"}


@pytest.mark.asyncio
async def test_query_multiple_filters(store: JsonlStore):
    """query() with multiple filter keys requires all to match."""
    await store.save("people", {"name": "Alice", "city": "NYC", "age": 30})
    await store.save("people", {"name": "Bob", "city": "NYC", "age": 25})

    results = await store.query("people", {"city": "NYC", "age": 30})
    assert len(results) == 1
    assert results[0]["name"] == "Alice"


@pytest.mark.asyncio
async def test_query_empty_collection(store: JsonlStore):
    """query() returns [] for a missing collection."""
    assert await store.query("empty", {"key": "val"}) == []


@pytest.mark.asyncio
async def test_creates_directory(tmp_path):
    """JsonlStore creates the data directory if it does not exist."""
    nested = tmp_path / "a" / "b" / "c"
    assert not nested.exists()

    store = JsonlStore(data_dir=nested)
    assert nested.exists()

    # Verify it works end-to-end.
    rid = await store.save("test", {"hello": "world"})
    fetched = await store.get("test", rid)
    assert fetched is not None
    assert fetched["hello"] == "world"


@pytest.mark.asyncio
async def test_concurrent_writes(store: JsonlStore):
    """Multiple concurrent saves should not corrupt the file."""
    num_writes = 50

    async def _write(i: int) -> str:
        return await store.save("concurrent", {"index": i})

    ids = await asyncio.gather(*[_write(i) for i in range(num_writes)])
    assert len(ids) == num_writes
    assert len(set(ids)) == num_writes  # all unique

    # All records should be retrievable.
    all_records = await store.list_recent("concurrent", limit=num_writes + 10)
    assert len(all_records) == num_writes
    indices = {r["index"] for r in all_records}
    assert indices == set(range(num_writes))
