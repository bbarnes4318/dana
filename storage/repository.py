"""High-level repository that wraps a raw store with schema validation.

The :class:`Repository` auto-selects :class:`~storage.jsonl_store.JsonlStore`
or :class:`~storage.postgres_store.PostgresStore` based on whether the
``DATABASE_URL`` environment variable is set, then exposes typed helper
methods for each record type.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Optional

from storage.base import BaseStore
from storage.jsonl_store import JsonlStore
from storage.postgres_store import PostgresStore
from storage.schemas import (
    CallTurn,
    LeadSnapshot,
    QAReport,
    ToolEvent,
    TrainingNote,
)


# Collection name constants
_LEADS = "leads"
_CALL_TURNS = "call_turns"
_TOOL_EVENTS = "tool_events"
_QA_REPORTS = "qa_reports"
_TRAINING_NOTES = "training_notes"


class Repository:
    """Schema-validated facade over a :class:`BaseStore`.

    Args:
        store: An explicit store instance.  When ``None`` (the default), one
            is chosen automatically: :class:`PostgresStore` if
            ``DATABASE_URL`` is set, otherwise :class:`JsonlStore` writing
            to ``./data``.
        data_dir: Directory for the JSONL backend (ignored when using
            Postgres).
    """

    def __init__(
        self,
        store: BaseStore | None = None,
        data_dir: str | Path = "data",
    ) -> None:
        if store is not None:
            self._store = store
        elif os.environ.get("DATABASE_URL"):
            self._store = PostgresStore()
        else:
            self._store = JsonlStore(data_dir=data_dir)

    @property
    def store(self) -> BaseStore:
        """The underlying store instance."""
        return self._store

    # ------------------------------------------------------------------
    # Typed save helpers
    # ------------------------------------------------------------------

    async def save_lead_snapshot(self, **kwargs: Any) -> str:
        """Validate and persist a :class:`LeadSnapshot`.

        Keyword arguments are forwarded to the Pydantic model constructor.

        Returns:
            The ``id`` of the saved record.
        """
        snapshot = LeadSnapshot(**kwargs)
        data = snapshot.model_dump(mode="json")
        data.setdefault("id", str(uuid.uuid4()))
        return await self._store.save(_LEADS, data)

    async def save_call_turn(self, **kwargs: Any) -> str:
        """Validate and persist a :class:`CallTurn`.

        Returns:
            The ``id`` of the saved record.
        """
        turn = CallTurn(**kwargs)
        data = turn.model_dump(mode="json")
        data.setdefault("id", str(uuid.uuid4()))
        return await self._store.save(_CALL_TURNS, data)

    async def save_tool_event(self, **kwargs: Any) -> str:
        """Validate and persist a :class:`ToolEvent`.

        Returns:
            The ``id`` of the saved record.
        """
        event = ToolEvent(**kwargs)
        data = event.model_dump(mode="json")
        data.setdefault("id", str(uuid.uuid4()))
        return await self._store.save(_TOOL_EVENTS, data)

    async def save_qa_report(self, **kwargs: Any) -> str:
        """Validate and persist a :class:`QAReport`.

        Returns:
            The ``id`` of the saved record.
        """
        report = QAReport(**kwargs)
        data = report.model_dump(mode="json")
        data.setdefault("id", str(uuid.uuid4()))
        return await self._store.save(_QA_REPORTS, data)

    async def save_training_note(self, **kwargs: Any) -> str:
        """Validate and persist a :class:`TrainingNote`.

        Returns:
            The ``id`` of the saved record.
        """
        note = TrainingNote(**kwargs)
        data = note.model_dump(mode="json")
        data.setdefault("id", str(uuid.uuid4()))
        return await self._store.save(_TRAINING_NOTES, data)

    # ------------------------------------------------------------------
    # Typed query helpers
    # ------------------------------------------------------------------

    async def get_call(self, call_id: str) -> Optional[dict]:
        """Return the lead snapshot for *call_id*, if it exists."""
        results = await self._store.query(_LEADS, {"call_id": call_id})
        return results[0] if results else None

    async def list_recent_calls(self, limit: int = 50) -> list[dict]:
        """Return the most recent lead snapshots."""
        return await self._store.list_recent(_LEADS, limit=limit)
