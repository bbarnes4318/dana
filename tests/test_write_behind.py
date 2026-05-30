"""Tests for WriteBehindQueue and asynchronous persistence."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest import mock
import pytest

from storage.base import BaseStore
from storage.write_behind import WriteBehindQueue


class MockStore(BaseStore):
    """Simple in-memory store for testing write-behind queue."""
    def __init__(self):
        self.records = []
        self.should_fail = False

    async def save(self, collection: str, data: dict) -> str:
        if self.should_fail:
            raise RuntimeError("Database connection failure")
        record_id = data.get("id", "temp-id")
        self.records.append((collection, data))
        return record_id

    async def get(self, collection: str, id: str) -> dict | None:
        return None

    async def list_recent(self, collection: str, limit: int = 50) -> list[dict]:
        return []

    async def query(self, collection: str, filters: dict) -> list[dict]:
        return []


@pytest.fixture
def mock_store():
    return MockStore()


@pytest.fixture
def dead_letter_file(tmp_path):
    return tmp_path / "dead_letter.jsonl"


@pytest.mark.asyncio
async def test_write_behind_enqueue_and_flush(mock_store, dead_letter_file):
    """Verify that records can be enqueued and flushed successfully."""
    queue = WriteBehindQueue(
        store=mock_store,
        enabled=True,
        max_queue_size=5,
        flush_interval_ms=100,
        batch_size=2,
        dead_letter_path=str(dead_letter_file),
    )

    # Enqueue a few items
    queue.enqueue("table_a", {"id": "1", "data": "first"})
    queue.enqueue("table_b", {"id": "2", "data": "second"})
    
    # Verify they aren't written to store yet
    assert len(mock_store.records) == 0

    # Flush 1 item
    written = await queue.flush(limit=1)
    assert written == 1
    assert len(mock_store.records) == 1
    assert mock_store.records[0] == ("table_a", {"id": "1", "data": "first"})

    # Flush remaining items
    written = await queue.flush(limit=10)
    assert written == 1
    assert len(mock_store.records) == 2
    assert mock_store.records[1] == ("table_b", {"id": "2", "data": "second"})


@pytest.mark.asyncio
async def test_write_behind_queue_full_drops_low_priority(mock_store, dead_letter_file):
    """Verify that low-priority events are dropped when the queue is full, but normal ones are kept."""
    queue = WriteBehindQueue(
        store=mock_store,
        enabled=True,
        max_queue_size=2,
        flush_interval_ms=100,
        batch_size=5,
        dead_letter_path=str(dead_letter_file),
    )

    # Fill queue to capacity (size = 2)
    assert queue.enqueue("t1", {"id": "1"}, priority="normal") is True
    assert queue.enqueue("t2", {"id": "2"}, priority="normal") is True

    # Try enqueuing low-priority event (should be dropped)
    assert queue.enqueue("t3", {"id": "3"}, priority="low") is False

    # Try enqueuing normal-priority event (should be accepted despite size)
    assert queue.enqueue("t4", {"id": "4"}, priority="normal") is True

    # Clean up and flush
    await queue.flush(limit=10)
    assert len(mock_store.records) == 3
    # Check that t3 was dropped and t4 made it through
    ids = [rec[1]["id"] for rec in mock_store.records]
    assert "1" in ids
    assert "2" in ids
    assert "3" not in ids
    assert "4" in ids


@pytest.mark.asyncio
async def test_write_behind_failed_writes_go_to_dead_letter(mock_store, dead_letter_file):
    """Verify that failed database writes append data to the dead-letter JSONL file."""
    queue = WriteBehindQueue(
        store=mock_store,
        enabled=True,
        max_queue_size=5,
        flush_interval_ms=100,
        batch_size=5,
        dead_letter_path=str(dead_letter_file),
    )

    # Force mock store to raise exception on save
    mock_store.should_fail = True

    # Enqueue record
    queue.enqueue("table_fail", {"id": "999", "message": "unwritten"})
    
    # Flush (causes failure)
    await queue.flush(limit=10)

    # Verify dead-letter file was written
    assert dead_letter_file.exists()
    
    with open(dead_letter_file, "r", encoding="utf-8") as f:
        line = f.readline()
        entry = json.loads(line)
        assert entry["table"] == "table_fail"
        assert entry["payload"] == {"id": "999", "message": "unwritten"}
        assert "Database connection failure" in entry["error"]


@pytest.mark.asyncio
async def test_write_behind_shutdown_flushes_remaining(mock_store, dead_letter_file):
    """Verify that calling shutdown flushes and drains the queue cleanly."""
    queue = WriteBehindQueue(
        store=mock_store,
        enabled=True,
        max_queue_size=5,
        flush_interval_ms=100,
        batch_size=5,
        dead_letter_path=str(dead_letter_file),
    )
    
    queue.start()

    # Enqueue a few items
    queue.enqueue("table_shutdown", {"id": "s1"})
    queue.enqueue("table_shutdown", {"id": "s2"})

    # Trigger shutdown
    await queue.shutdown(timeout=2.0)

    # Verify all items were flushed before worker stopped
    assert len(mock_store.records) == 2
    assert queue._queue.empty() is True
