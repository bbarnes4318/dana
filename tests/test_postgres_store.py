"""Tests for :class:`storage.postgres_store.PostgresStore`.

Covers fallback logic, SQL injection guards, and integration CRUD flows.
"""

from __future__ import annotations

import os
from unittest import mock

import pytest
import asyncpg

from storage.postgres_store import PostgresStore
from storage.jsonl_store import JsonlStore
from storage.repository import Repository

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.asyncio


# ------------------------------------------------------------------
# Unit/Mock Tests
# ------------------------------------------------------------------

async def test_fallback_when_url_unset():
    """Repository defaults to JsonlStore when DATABASE_URL is unset."""
    with mock.patch.dict(os.environ, {}, clear=True):
        os.environ.pop("DATABASE_URL", None)
        repo = Repository()
        assert isinstance(repo.store, JsonlStore)
        
        # Health check returns jsonl
        hc = await repo.health_check()
        assert hc["backend"] == "jsonl"
        assert hc["connected"] is True
        assert hc["migrations_applied"] is True


async def test_postgres_when_url_set():
    """Repository chooses PostgresStore when DATABASE_URL is set."""
    with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://localhost/test"}):
        repo = Repository()
        assert isinstance(repo.store, PostgresStore)
        assert repo.store._dsn == "postgresql://localhost/test"


async def test_postgres_unavailable_raises():
    """PostgresStore raises an exception if connection fails when DATABASE_URL is set."""
    store = PostgresStore(dsn="postgresql://invalid_user:invalid_pass@localhost:54321/invalid_db")
    # Trying to save or get should raise a hard error, not silently fall back
    with pytest.raises(Exception):
        await store.save("leads", {"id": "123"})


async def test_sql_injection_table_guard():
    """PostgresStore rejects invalid table names to prevent injection."""
    store = PostgresStore(dsn="postgresql://localhost/test")
    # Mock pool to avoid database connections
    store._pool = mock.AsyncMock()
    store._migrations_checked = True
    
    with pytest.raises(ValueError, match="Table name 'invalid_table' is not allowed"):
        await store.save("invalid_table", {"id": "1"})
        
    with pytest.raises(ValueError, match="Table name 'invalid_table' is not allowed"):
        await store.get("invalid_table", "1")
        
    with pytest.raises(ValueError, match="Table name 'invalid_table' is not allowed"):
        await store.list_recent("invalid_table")
        
    with pytest.raises(ValueError, match="Table name 'invalid_table' is not allowed"):
        await store.query("invalid_table", {"id": "1"})


async def test_sql_injection_column_guard():
    """PostgresStore rejects invalid column names on save to prevent injection."""
    store = PostgresStore(dsn="postgresql://localhost/test")
    store._pool = mock.AsyncMock()
    store._migrations_checked = True
    
    with pytest.raises(ValueError, match="Unknown fields"):
        await store.save("call_turns", {"id": "1", "invalid_col": "val"})


async def test_sql_injection_query_guard():
    """PostgresStore rejects invalid query filters to prevent injection."""
    store = PostgresStore(dsn="postgresql://localhost/test")
    store._pool = mock.AsyncMock()
    store._migrations_checked = True
    
    with pytest.raises(ValueError, match="Filtering by 'invalid_col' on 'call_turns' is not allowed"):
        await store.query("call_turns", {"invalid_col": "val"})


# ------------------------------------------------------------------
# Integration Tests (Run only when Postgres is available)
# ------------------------------------------------------------------

async def test_postgres_integration_flow():
    """Run full CRUD integration tests against the actual database if reachable."""
    if not DATABASE_URL:
        pytest.skip("DATABASE_URL is not set. Skipping integration tests.")
        
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.close()
    except Exception as e:
        pytest.skip(f"PostgreSQL is not reachable: {e}. Skipping integration tests.")
        
    store = PostgresStore(DATABASE_URL)
    
    # 1. Save campaigns
    camp_id = await store.save("campaigns", {
        "id": "camp-test-123",
        "campaign_id": "camp-test",
        "name": "Integration Test Campaign",
        "status": "active",
        "config": {"max_attempts": 3}
    })
    assert camp_id == "camp-test-123"
    
    # 2. Get campaign
    camp = await store.get("campaigns", "camp-test-123")
    assert camp is not None
    assert camp["campaign_id"] == "camp-test"
    assert camp["config"]["max_attempts"] == 3
    
    # 3. Save lead snapshot
    lead_id = await store.save("leads", {
        "id": "lead-test-123",
        "call_id": "call-test-abc",
        "lead_profile": {
            "lead_id": "lead-prospect-1",
            "lead_phone_e164": "+13055550199",
            "campaign_id": "camp-test"
        },
        "stage": "opening"
    })
    assert lead_id == "lead-test-123"
    
    # 4. Query lead using allowlisted JSONB query path
    results = await store.query("leads", {"call_id": "call-test-abc"})
    assert len(results) == 1
    assert results[0]["id"] == "lead-test-123"
    
    # 5. Query lead using direct column
    results2 = await store.query("leads", {"phone_e164": "+13055550199"})
    assert len(results2) >= 1
    assert results2[0]["id"] == "lead-test-123"
    
    # 6. List recent
    recent = await store.list_recent("campaigns", limit=10)
    assert len(recent) >= 1
    assert any(c["id"] == "camp-test-123" for c in recent)
    
    # 7. Health check
    hc = await store.health_check()
    assert hc["connected"] is True
    assert hc["migrations_applied"] is True
    
    # 8. Close connection pool
    await store.close()

