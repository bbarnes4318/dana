"""Async write-behind queue for non-blocking database persistence."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from storage.base import BaseStore

logger = logging.getLogger(__name__)


class WriteBehindQueue:
    """Queue for asynchronous out-of-band persistence of non-critical database records."""

    def __init__(
        self,
        store: BaseStore,
        enabled: bool = True,
        max_queue_size: int = 10000,
        flush_interval_ms: int = 250,
        batch_size: int = 100,
        dead_letter_path: str = "data/dead_letter.jsonl",
    ) -> None:
        self.store = store
        self.enabled = enabled
        self.max_queue_size = max_queue_size
        self.flush_interval = flush_interval_ms / 1000.0
        self.batch_size = batch_size
        self.dead_letter_path = Path(dead_letter_path)

        self._queue: asyncio.Queue[tuple[str, dict, str]] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        self._dropped_count = 0
        self._failed_count = 0

    def start(self) -> None:
        """Start the background flusher worker."""
        if not self.enabled:
            logger.info("Write-behind queue is disabled. Writes will execute synchronously.")
            return
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self.worker_loop())
        logger.info("Write-behind queue worker started (flush interval: %dms).", int(self.flush_interval * 1000))

    def enqueue(self, table: str, payload: dict, priority: str = "normal") -> bool:
        """Enqueue a record for database write.
        
        Returns True if enqueued, False if dropped due to queue limit.
        """
        if not self.enabled:
            # If write-behind is disabled, the repository will perform sync write.
            return False

        qsize = self._queue.qsize()
        if qsize >= self.max_queue_size:
            if priority == "low":
                self._dropped_count += 1
                logger.warning(
                    "Write-behind queue full (%d/%d). Dropping low-priority event for table %s.",
                    qsize,
                    self.max_queue_size,
                    table,
                )
                return False
            else:
                # Normal or high priority cannot be dropped, allow it to enqueue but warn
                logger.warning(
                    "Write-behind queue exceeded max size (%d/%d). Enqueuing critical table %s.",
                    qsize,
                    self.max_queue_size,
                    table,
                )

        self._queue.put_nowait((table, payload, priority))
        return True

    async def flush(self, limit: int = 100) -> int:
        """Synchronously drain and write up to `limit` records from the queue."""
        batch: List[tuple[str, dict, str]] = []
        for _ in range(limit):
            if self._queue.empty():
                break
            batch.append(self._queue.get_nowait())

        if not batch:
            return 0

        # Group by table for potential batch writes, but since BaseStore only exposes .save(),
        # we will iterate and write them. If .save() fails, we write them to the dead-letter file.
        written_count = 0
        for table, payload, priority in batch:
            try:
                await self.store.save(table, payload)
                written_count += 1
            except Exception as e:
                self._failed_count += 1
                logger.error("Failed write-behind write to table %s: %s", table, e)
                self._write_to_dead_letter(table, payload, str(e))
            finally:
                self._queue.task_done()

        return written_count

    def _write_to_dead_letter(self, table: str, payload: dict, error_msg: str) -> None:
        try:
            self.dead_letter_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "timestamp": time.time(),
                "table": table,
                "payload": payload,
                "error": error_msg,
            }
            with open(self.dead_letter_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as e:
            logger.critical("Failed to write to dead-letter JSONL: %s", e)

    async def worker_loop(self) -> None:
        """Background loop running while queue is active."""
        while self._running:
            try:
                await asyncio.sleep(self.flush_interval)
                if not self._queue.empty():
                    await self.flush(self.batch_size)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in write-behind worker loop: %s", e, exc_info=True)

    async def shutdown(self, timeout: float = 10.0) -> None:
        """Drains the queue, flushes all remaining records, and stops the worker."""
        logger.info("Shutting down write-behind queue...")
        self._running = False
        
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

        # Final drain of remaining items
        start_time = time.time()
        while not self._queue.empty():
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                logger.critical(
                    "Shutdown timeout reached. %d items remaining in write-behind queue will be dumped to dead letter.",
                    self._queue.qsize(),
                )
                # Dump remainder to dead letter
                while not self._queue.empty():
                    table, payload, priority = self._queue.get_nowait()
                    self._write_to_dead_letter(table, payload, "shutdown_timeout")
                    self._queue.task_done()
                break

            await self.flush(self.batch_size)
            await asyncio.sleep(0.05)

        logger.info("Write-behind queue shutdown complete.")


# Global singleton access
_write_behind_queue: Optional[WriteBehindQueue] = None
_wb_lock = asyncio.Lock()


async def get_write_behind_queue(store: Optional[BaseStore] = None) -> Optional[WriteBehindQueue]:
    """Retrieve the global WriteBehindQueue instance."""
    global _write_behind_queue
    if _write_behind_queue is not None:
        return _write_behind_queue

    async with _wb_lock:
        if _write_behind_queue is not None:
            return _write_behind_queue

        enabled = os.environ.get("DANA_WRITE_BEHIND_ENABLED", "true").lower() == "true"
        max_size = int(os.environ.get("DANA_WRITE_BEHIND_MAX_QUEUE_SIZE", "10000"))
        interval = int(os.environ.get("DANA_WRITE_BEHIND_FLUSH_INTERVAL_MS", "250"))
        batch = int(os.environ.get("DANA_WRITE_BEHIND_BATCH_SIZE", "100"))

        if store is None:
            # Import repository to access default store
            from storage.repository import Repository
            repo = Repository()
            store = repo.store

        _write_behind_queue = WriteBehindQueue(
            store=store,
            enabled=enabled,
            max_queue_size=max_size,
            flush_interval_ms=interval,
            batch_size=batch,
        )
        if enabled:
            _write_behind_queue.start()

        return _write_behind_queue
