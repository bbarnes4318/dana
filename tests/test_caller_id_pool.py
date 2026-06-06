"""Tests for CallerIdPool logic."""

from datetime import datetime, timedelta, timezone
import pytest
from dialer.caller_id_pool import CallerIdPool


class MockRepository:
    """Mock repository for CallerIdPool tests."""

    def __init__(self, caller_ids):
        self.caller_ids = {c["caller_id"]: c for c in caller_ids}
        self.used_calls = []

    async def list_caller_ids(self, campaign_id):
        return list(self.caller_ids.values())

    async def get_caller_id(self, caller_id, campaign_id):
        return self.caller_ids.get(caller_id)

    async def mark_caller_id_used(self, caller_id, campaign_id, now=None):
        cid = self.caller_ids.get(caller_id)
        if cid:
            cid["daily_call_count"] = cid.get("daily_call_count", 0) + 1
            cid["total_calls"] = cid.get("total_calls", 0) + 1
            cid["last_used_at"] = now or datetime.now(timezone.utc)

    async def update_caller_id_metrics(self, caller_id, campaign_id, outcome):
        cid = self.caller_ids.get(caller_id)
        if cid:
            is_ans = 1 if outcome == "human_answered" else 0
            is_dnc = 1 if outcome == "dnc" else 0
            cid["total_calls"] = cid.get("total_calls", 0) + 1
            cid["total_answers"] = cid.get("total_answers", 0) + is_ans
            cid["total_dncs"] = cid.get("total_dncs", 0) + is_dnc
            cid["answer_rate"] = cid["total_answers"] / cid["total_calls"]
            cid["dnc_rate"] = cid["total_dncs"] / cid["total_calls"]

    async def set_caller_id_cooldown(self, caller_id, campaign_id, cooldown_until, reason):
        cid = self.caller_ids.get(caller_id)
        if cid:
            cid["cooldown_until"] = cooldown_until
            cid["cooldown_reason"] = reason


@pytest.mark.asyncio
async def test_caller_id_pool_lru_rotation():
    cids = [
        {"caller_id": "+15550001", "status": "active", "last_used_at": datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)},
        {"caller_id": "+15550002", "status": "active", "last_used_at": datetime(2026, 6, 6, 11, 0, tzinfo=timezone.utc)},
        {"caller_id": "+15550003", "status": "active", "last_used_at": None},
    ]
    repo = MockRepository(cids)
    pool = CallerIdPool(repo)
    config = {"caller_id_daily_limit": 200}
    now = datetime(2026, 6, 6, 13, 0, tzinfo=timezone.utc)

    # 1. First choice should be "+15550003" because last_used_at is None (never used)
    next_cid = await pool.get_next_caller_id("c1", config, now)
    assert next_cid == "+15550003"

    # 2. If $+15550003$ is excluded or we update them, next should be the oldest last_used_at (+15550002)
    cids[2]["last_used_at"] = datetime(2026, 6, 6, 12, 30, tzinfo=timezone.utc)
    next_cid = await pool.get_next_caller_id("c1", config, now)
    assert next_cid == "+15550002"


@pytest.mark.asyncio
async def test_caller_id_pool_skips_cooldown_and_limit():
    cids = [
        {
            "caller_id": "+15550001",
            "status": "active",
            "cooldown_until": datetime(2026, 6, 6, 14, 0, tzinfo=timezone.utc)
        },
        {
            "caller_id": "+15550002",
            "status": "active",
            "daily_call_count": 200
        },
        {
            "caller_id": "+15550003",
            "status": "active",
            "daily_call_count": 10,
            "last_used_at": None
        },
    ]
    repo = MockRepository(cids)
    pool = CallerIdPool(repo)
    config = {"caller_id_daily_limit": 200}
    now = datetime(2026, 6, 6, 13, 0, tzinfo=timezone.utc)

    # "+15550001" is in cooldown (until 14:00 vs 13:00 now)
    # "+15550002" reached daily limit (200)
    # Only "+15550003" is eligible
    next_cid = await pool.get_next_caller_id("c1", config, now)
    assert next_cid == "+15550003"


@pytest.mark.asyncio
async def test_caller_id_pool_updates_metrics_and_triggers_cooldown():
    # Number with 9 calls. Adding 1 more dnc makes it 10 calls, triggering DNC check.
    cid = {
        "caller_id": "+15550001",
        "status": "active",
        "total_calls": 9,
        "total_answers": 2,
        "total_dncs": 0,
        "dnc_rate": 0.0,
        "answer_rate": 2/9,
        "cooldown_until": None
    }
    repo = MockRepository([cid])
    pool = CallerIdPool(repo)
    config = {
        "caller_id_min_calls_threshold": 10,
        "caller_id_min_answer_rate": 0.05,
        "caller_id_max_dnc_rate": 0.08,
        "caller_id_cooldown_duration_seconds": 3600
    }
    now = datetime(2026, 6, 6, 13, 0, tzinfo=timezone.utc)

    # 1. Update outcome with DNC. New total calls = 10, total DNCs = 1.
    # DNC rate = 1/10 = 10% > 8% max_dnc_rate threshold -> should trigger cooldown
    await pool.update_metrics_and_cooldown("+15550001", "c1", config, "dnc", now)

    updated_cid = await repo.get_caller_id("+15550001", "c1")
    assert updated_cid["cooldown_until"] is not None
    assert updated_cid["cooldown_until"] == now + timedelta(seconds=3600)
