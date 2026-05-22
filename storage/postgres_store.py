"""PostgreSQL-backed implementation of :class:`storage.base.BaseStore`.

# -----------------------------------------------------------------------
# NOTE — actual implementation deferred.
#
# This file defines the *interface stub* so the rest of the codebase can
# reference PostgresStore today.  The real asyncpg logic (connection pool,
# table creation, JSONB queries) will be filled in once the Postgres
# infrastructure is provisioned.
# -----------------------------------------------------------------------

Uses ``asyncpg`` when the ``DATABASE_URL`` environment variable is set.
Tables are created lazily on first use.
"""

from __future__ import annotations

import os
from typing import Optional

from storage.base import BaseStore


class PostgresStore(BaseStore):
    """Async PostgreSQL store backed by ``asyncpg``.

    Instantiation checks for ``DATABASE_URL`` in the environment.  If the
    variable is missing, every method raises :class:`NotImplementedError`
    with a helpful message.

    Args:
        dsn: Optional explicit DSN.  Falls back to ``DATABASE_URL`` env var.
    """

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn: str | None = dsn or os.environ.get("DATABASE_URL")
        if not self._dsn:
            # We still allow the object to be created so callers can test
            # isinstance(), but every I/O method will fail fast.
            pass
        # Placeholder for the asyncpg connection pool.
        self._pool = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_dsn(self) -> str:
        """Raise if no database DSN is available."""
        if not self._dsn:
            raise NotImplementedError(
                "PostgresStore requires a DATABASE_URL environment variable "
                "(or an explicit dsn).  Set DATABASE_URL to a valid "
                "PostgreSQL connection string, e.g. "
                "'postgresql://user:pass@localhost:5432/dana'."
            )
        return self._dsn

    async def _ensure_pool(self) -> None:
        """Create the asyncpg pool on first call (deferred)."""
        self._require_dsn()
        # TODO: import asyncpg, create pool, run migrations
        raise NotImplementedError(
            "PostgresStore._ensure_pool is not yet implemented. "
            "Use JsonlStore for local development."
        )

    async def _ensure_table(self, collection: str) -> None:
        """Create the table for *collection* if it does not exist (deferred)."""
        self._require_dsn()
        raise NotImplementedError(
            f"PostgresStore._ensure_table('{collection}') is not yet "
            "implemented.  Use JsonlStore for local development."
        )

    # ------------------------------------------------------------------
    # BaseStore interface
    # ------------------------------------------------------------------

    async def save(self, collection: str, data: dict) -> str:
        """Insert *data* into the appropriate Postgres table.

        Raises:
            NotImplementedError: Always, until the real implementation is
                wired up.
        """
        self._require_dsn()
        raise NotImplementedError(
            "PostgresStore.save() is not yet implemented. "
            "Run with JsonlStore for now by unsetting DATABASE_URL."
        )

    async def get(self, collection: str, id: str) -> Optional[dict]:
        """Retrieve a record by primary key.

        Raises:
            NotImplementedError: Always, until the real implementation is
                wired up.
        """
        self._require_dsn()
        raise NotImplementedError(
            "PostgresStore.get() is not yet implemented. "
            "Run with JsonlStore for now by unsetting DATABASE_URL."
        )

    async def list_recent(self, collection: str, limit: int = 50) -> list[dict]:
        """Return the most recent records.

        Raises:
            NotImplementedError: Always, until the real implementation is
                wired up.
        """
        self._require_dsn()
        raise NotImplementedError(
            "PostgresStore.list_recent() is not yet implemented. "
            "Run with JsonlStore for now by unsetting DATABASE_URL."
        )

    async def query(self, collection: str, filters: dict) -> list[dict]:
        """Query records by field equality.

        Raises:
            NotImplementedError: Always, until the real implementation is
                wired up.
        """
        self._require_dsn()
        raise NotImplementedError(
            "PostgresStore.query() is not yet implemented. "
            "Run with JsonlStore for now by unsetting DATABASE_URL."
        )
