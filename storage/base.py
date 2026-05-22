"""Abstract base class for storage backends.

All storage implementations (JSONL, Postgres, etc.) implement this interface
so the rest of the application is backend-agnostic.
"""

from __future__ import annotations

import abc
from typing import Optional


class BaseStore(abc.ABC):
    """Async key-value / document store interface.

    Each logical entity type lives in its own *collection* (e.g. ``"leads"``,
    ``"call_turns"``).  Concrete subclasses decide how a collection maps to
    the underlying storage (file, table, etc.).
    """

    @abc.abstractmethod
    async def save(self, collection: str, data: dict) -> str:
        """Persist *data* and return its unique id.

        If ``data`` does not already contain an ``"id"`` key, the store must
        generate one.

        Args:
            collection: Logical grouping (e.g. ``"leads"``).
            data: Serialisable dictionary to store.

        Returns:
            The ``id`` string assigned to the record.
        """

    @abc.abstractmethod
    async def get(self, collection: str, id: str) -> Optional[dict]:
        """Retrieve a single record by *id*, or ``None`` if not found.

        Args:
            collection: Logical grouping to search within.
            id: Unique identifier of the record.

        Returns:
            The stored dictionary, or ``None``.
        """

    @abc.abstractmethod
    async def list_recent(self, collection: str, limit: int = 50) -> list[dict]:
        """Return the most recent records from *collection*.

        Records are returned newest-first.

        Args:
            collection: Logical grouping to list from.
            limit: Maximum number of records to return (default 50).

        Returns:
            A list of stored dictionaries, up to *limit* items.
        """

    @abc.abstractmethod
    async def query(self, collection: str, filters: dict) -> list[dict]:
        """Return all records whose fields match *filters*.

        Each key in *filters* is compared with equality against the
        corresponding field in each record.

        Args:
            collection: Logical grouping to search within.
            filters: Key-value pairs that must all match.

        Returns:
            A list of matching dictionaries.
        """
