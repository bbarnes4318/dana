import asyncio
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from storage.repository import Repository
from main import graceful_startup_integrations, graceful_shutdown
import integrations.crm_webhooks as crm_webhooks
from integrations.crm_webhooks import (
    start_webhook_outbox_worker,
    stop_webhook_outbox_worker,
    flush_pending_webhooks,
)
from runtime.hot_state import get_hot_state_store


@pytest.fixture(autouse=True)
def force_in_memory_hot_store():
    """Force clean InMemoryHotStateStore to be shared by test and background worker tasks."""
    old_val = os.environ.get("DANA_USE_REDIS_HOT_STATE")
    os.environ["DANA_USE_REDIS_HOT_STATE"] = "false"
    import runtime.hot_state as hs
    old_instance = hs._store_instance
    hs._store_instance = None
    yield
    hs._store_instance = old_instance
    if old_val is not None:
        os.environ["DANA_USE_REDIS_HOT_STATE"] = old_val
    else:
        os.environ.pop("DANA_USE_REDIS_HOT_STATE", None)

@pytest.fixture(autouse=True)
def cleanup_worker_global():
    """Ensure the global worker task is cleaned up after each test."""
    yield
    stop_webhook_outbox_worker()


@pytest.mark.asyncio
async def test_webhook_outbox_worker_lifecycle_and_idempotency(tmp_path):
    """
    Test starting the webhook worker, verifying that only one worker drains
    the queue by acquiring the lease lock, and that shutdown cancels the loop.
    """
    repo = Repository(data_dir=tmp_path)
    
    # Reset hot store lock
    hot_store = await get_hot_state_store()
    await hot_store.release_lock("webhook_outbox_lock", "worker-1")
    await hot_store.release_lock("webhook_outbox_lock", "worker-2")

    drain_mock = AsyncMock(return_value=[])

    # 1. Start worker-1, it should acquire the lock and call drain
    with patch("integrations.crm_webhooks.drain_pending_webhook_events", drain_mock):
        start_webhook_outbox_worker(repo, poll_interval=0.01, worker_id="worker-1")
        
        # Give it a moment to run at least one loop iteration
        await asyncio.sleep(0.05)
        
        # Verify drain was called for worker-1
        assert drain_mock.called
        assert drain_mock.call_args[0][1] == "worker-1"
        
        # Verify worker-1 holds the lock in hot store
        lock_val = await hot_store.get_json("lock:webhook_outbox_lock")
        assert lock_val == "worker-1"
        
        # Stop worker-1
        stop_webhook_outbox_worker()
        await asyncio.sleep(0.02)
        assert crm_webhooks._outbox_worker_task is None


@pytest.mark.asyncio
async def test_webhook_outbox_worker_duplicate_prevention(tmp_path):
    """
    Verify that if worker-1 holds the lock, worker-2 does NOT drain the outbox.
    """
    repo = Repository(data_dir=tmp_path)
    
    # Reset/Pre-lock by worker-1
    hot_store = await get_hot_state_store()
    await hot_store.acquire_lock("webhook_outbox_lock", "worker-1", 30)

    drain_mock = AsyncMock(return_value=[])

    with patch("integrations.crm_webhooks.drain_pending_webhook_events", drain_mock):
        # Start worker-2
        start_webhook_outbox_worker(repo, poll_interval=0.01, worker_id="worker-2")
        
        # Let it run
        await asyncio.sleep(0.05)
        
        # Assert drain_mock was NOT called for worker-2
        called_workers = [args[0][1] for args in drain_mock.call_args_list]
        assert "worker-2" not in called_workers
        
        # Stop worker-2
        stop_webhook_outbox_worker()


@pytest.mark.asyncio
async def test_graceful_startup_and_shutdown_integration_lifecycle(tmp_path):
    """
    Test main.py's graceful_startup_integrations and graceful_shutdown hooks.
    """
    repo = Repository(data_dir=tmp_path)
    
    # 1. Test Startup
    with patch("integrations.crm_webhooks.start_webhook_outbox_worker") as mock_start:
        graceful_startup_integrations(repo, poll_interval=5.0)
        mock_start.assert_called_once_with(repo, poll_interval=5.0)
        
    # 2. Test Shutdown
    mock_flush = AsyncMock()
    with patch("integrations.crm_webhooks.stop_webhook_outbox_worker") as mock_stop, \
         patch("integrations.crm_webhooks.flush_pending_webhooks", mock_flush):
         
        await graceful_shutdown(repo, timeout=2.0)
        mock_stop.assert_called_once()
        mock_flush.assert_called_once_with(timeout=2.0)
