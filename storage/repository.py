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
    Call,
    CallTurn,
    Callback,
    Campaign,
    ConsentRecord,
    DncRequest,
    LatencyMetric,
    LeadSnapshot,
    QAReport,
    ToolEvent,
    TrainingNote,
    Transfer,
)

# Collection name constants
_LEADS = "leads"
_CALL_TURNS = "call_turns"
_TOOL_EVENTS = "tool_events"
_QA_REPORTS = "qa_reports"
_TRAINING_NOTES = "training_notes"
_CALLS = "calls"
_TRANSFERS = "transfers"
_CALLBACKS = "callbacks"
_DNC_REQUESTS = "dnc_requests"
_CONSENT_RECORDS = "consent_records"
_LATENCY_METRICS = "latency_metrics"
_CAMPAIGNS = "campaigns"


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

    async def close(self) -> None:
        """Close connection pools cleanly if applicable."""
        if hasattr(self._store, "close"):
            await self._store.close()

    async def health_check(self) -> dict[str, Any]:
        """Verify storage backend connectivity and migration status."""
        if hasattr(self._store, "health_check"):
            return await self._store.health_check()
        return {
            "backend": "jsonl",
            "connected": True,
            "migrations_applied": True
        }

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
        # Extract flat fields for direct query support
        lead_profile = snapshot.lead_profile or {}
        data["lead_id"] = lead_profile.get("lead_id")
        data["phone_e164"] = (
            lead_profile.get("lead_phone_e164") 
            or lead_profile.get("phone_e164")
        )
        data["campaign_id"] = lead_profile.get("campaign_id")
        data["consent_artifact_id"] = lead_profile.get("consent_artifact_id")
        data["source_vendor"] = lead_profile.get("consent_source")
        data["status"] = lead_profile.get("status")
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

    async def save_call(self, **kwargs: Any) -> str:
        """Validate and persist a :class:`Call`.

        Returns:
            The ``id`` of the saved record.
        """
        model = Call(**kwargs)
        data = model.model_dump(mode="json")
        data.setdefault("id", str(uuid.uuid4()))
        return await self._store.save(_CALLS, data)

    async def save_transfer(self, **kwargs: Any) -> str:
        """Validate and persist a :class:`Transfer`.

        Returns:
            The ``id`` of the saved record.
        """
        model = Transfer(**kwargs)
        data = model.model_dump(mode="json")
        data.setdefault("id", str(uuid.uuid4()))
        return await self._store.save(_TRANSFERS, data)

    async def save_callback(self, **kwargs: Any) -> str:
        """Validate and persist a :class:`Callback`.

        Returns:
            The ``id`` of the saved record.
        """
        model = Callback(**kwargs)
        data = model.model_dump(mode="json")
        data.setdefault("id", str(uuid.uuid4()))
        return await self._store.save(_CALLBACKS, data)

    async def save_dnc_request(self, **kwargs: Any) -> str:
        """Validate and persist a :class:`DncRequest`.

        Returns:
            The ``id`` of the saved record.
        """
        model = DncRequest(**kwargs)
        data = model.model_dump(mode="json")
        data.setdefault("id", str(uuid.uuid4()))
        return await self._store.save(_DNC_REQUESTS, data)

    async def save_consent_record(self, **kwargs: Any) -> str:
        """Validate and persist a :class:`ConsentRecord`.

        Returns:
            The ``id`` of the saved record.
        """
        model = ConsentRecord(**kwargs)
        data = model.model_dump(mode="json")
        data.setdefault("id", str(uuid.uuid4()))
        return await self._store.save(_CONSENT_RECORDS, data)

    async def save_latency_metric(self, **kwargs: Any) -> str:
        """Validate and persist a :class:`LatencyMetric`.

        Returns:
            The ``id`` of the saved record.
        """
        model = LatencyMetric(**kwargs)
        data = model.model_dump(mode="json")
        data.setdefault("id", str(uuid.uuid4()))
        return await self._store.save(_LATENCY_METRICS, data)

    async def save_campaign(self, **kwargs: Any) -> str:
        """Validate and persist a :class:`Campaign`.

        Returns:
            The ``id`` of the saved record.
        """
        model = Campaign(**kwargs)
        data = model.model_dump(mode="json")
        data.setdefault("id", str(uuid.uuid4()))
        return await self._store.save(_CAMPAIGNS, data)

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

    async def get_lead_by_phone(self, phone_e164: str) -> Optional[dict]:
        """Return the most recent lead snapshot matching *phone_e164*, if it exists."""
        results = await self._store.query(_LEADS, {"phone_e164": phone_e164})
        return results[0] if results else None

    async def get_recent_calls(self, limit: int = 50) -> list[dict]:
        """Return the most recent calls."""
        return await self._store.list_recent(_CALLS, limit=limit)

    async def get_campaign_metrics(self, campaign_id: str) -> dict[str, Any]:
        """Calculate performance metrics for a specific campaign."""
        calls = await self._store.query(_CALLS, {"campaign_id": campaign_id})
        
        total = len(calls)
        answered = sum(1 for c in calls if c.get("answered_at") is not None)
        completed = sum(1 for c in calls if c.get("ended_at") is not None)
        
        durations = [
            c.get("duration_seconds") 
            for c in calls 
            if c.get("duration_seconds") is not None
        ]
        total_duration = sum(durations) if durations else 0.0
        avg_duration = total_duration / len(durations) if durations else 0.0
        
        qa_scores = [
            c.get("qa_score") 
            for c in calls 
            if c.get("qa_score") is not None
        ]
        avg_qa = sum(qa_scores) / len(qa_scores) if qa_scores else 0.0
        
        outcomes: dict[str, int] = {}
        for c in calls:
            outcome = c.get("outcome") or "unknown"
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            
        return {
            "campaign_id": campaign_id,
            "total_calls": total,
            "answered_calls": answered,
            "completed_calls": completed,
            "total_duration_seconds": total_duration,
            "average_duration_seconds": avg_duration,
            "average_qa_score": avg_qa,
            "outcomes": outcomes,
        }
