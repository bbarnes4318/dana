import asyncio
import logging
import os
from typing import Set, Awaitable, Optional

logger = logging.getLogger(__name__)

class WebhookDispatcher:
    """Supervised dispatcher for background webhook tasks.
    
    Prevents silent background task failures, locks down concurrency,
    provides safe shutdown/drain capabilities, and exposes a flush interface.
    """
    
    def __init__(self, max_concurrency: int = 5, max_queue_size: int = 100):
        self._max_concurrency = max_concurrency
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._active_tasks: Set[asyncio.Task] = set()
        self._max_queue_size = max_queue_size
        self._shutting_down = False

    def get_active_tasks_count(self) -> int:
        return len(self._active_tasks)

    def submit_task(self, coro: Awaitable[None]) -> Optional[asyncio.Task]:
        """Submits a webhook sending coroutine to run in the background."""
        if self._shutting_down:
            logger.warning("Dispatcher is shutting down. Rejecting new task submission.")
            return None

        if len(self._active_tasks) >= self._max_queue_size:
            logger.error(
                "Webhook background queue limit reached (%d tasks). Rejecting new task.",
                self._max_queue_size
            )
            return None

        # Create task
        task = asyncio.create_task(self._run_supervised(coro))
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)
        return task

    async def _run_supervised(self, coro: Awaitable[None]) -> None:
        async with self._semaphore:
            try:
                await coro
            except asyncio.CancelledError:
                logger.info("Webhook background task cancelled.")
                raise
            except Exception as e:
                logger.exception("Supervised webhook background task failed: %s", e)

    async def flush_pending_webhooks(self, timeout: float = 10.0) -> None:
        """Wait/drain all currently active tasks with a timeout."""
        if not self._active_tasks:
            return
        
        logger.info("Flushing %d pending webhook background tasks...", len(self._active_tasks))
        to_wait = list(self._active_tasks)
        try:
            await asyncio.wait_for(
                asyncio.gather(*to_wait, return_exceptions=True),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.warning("Timeout reached while flushing pending webhooks. Some tasks are still active.")

    async def shutdown(self, timeout: float = 10.0) -> None:
        """Gracefully drain the active tasks, then force cancel any remaining."""
        self._shutting_down = True
        logger.info("Shutting down WebhookDispatcher (draining active tasks)...")
        await self.flush_pending_webhooks(timeout)
        
        if self._active_tasks:
            logger.warning("Cancelling %d remaining active webhook tasks...", len(self._active_tasks))
            for task in list(self._active_tasks):
                task.cancel()
            await asyncio.gather(*self._active_tasks, return_exceptions=True)
            self._active_tasks.clear()

# Global Singleton Dispatcher instance
_dispatcher: Optional[WebhookDispatcher] = None

def get_dispatcher() -> WebhookDispatcher:
    global _dispatcher
    if _dispatcher is None:
        max_con = int(os.getenv("DANA_CRM_WEBHOOK_MAX_CONCURRENCY", "5"))
        # Standard safety: make queue size a multiple of concurrency limit
        max_queue = max_con * 20
        _dispatcher = WebhookDispatcher(max_concurrency=max_con, max_queue_size=max_queue)
    return _dispatcher
