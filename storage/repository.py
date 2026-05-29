"""High-level repository that wraps a raw store with schema validation.

The :class:`Repository` auto-selects :class:`~storage.jsonl_store.JsonlStore`
or :class:`~storage.postgres_store.PostgresStore` based on whether the
``DATABASE_URL`` environment variable is set, then exposes typed helper
methods for each record type.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

def parse_dt(val: Any) -> Optional[datetime]:
    if not val:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return val
    if isinstance(val, str):
        try:
            val = val.replace("Z", "+00:00")
            if " " in val:
                parts = val.split(" ")
                if len(parts) >= 2 and len(parts[0]) == 10:
                    val = parts[0] + "T" + " ".join(parts[1:])
            dt = datetime.fromisoformat(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None
    return None

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
_WEBHOOK_EVENTS = "webhook_events"


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
        self._claim_lock = asyncio.Lock()

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
        record_id = kwargs.pop("id", None)
        call_id = kwargs.get("call_id")
        
        # If call_id is provided, try to find the existing call to merge/preserve fields
        existing_data = {}
        if call_id:
            existing = await self.get_call_record(call_id)
            if existing:
                existing_data = dict(existing)
                if not record_id:
                    record_id = existing.get("id")
        
        # Build kwargs with existing data as fallback for fields not explicitly passed
        full_kwargs = {}
        for field_name in Call.model_fields.keys():
            if field_name in kwargs:
                full_kwargs[field_name] = kwargs[field_name]
            elif field_name in existing_data:
                full_kwargs[field_name] = existing_data[field_name]
                
        model = Call(**full_kwargs)
        data = model.model_dump(mode="json")
        if record_id:
            data["id"] = record_id
        else:
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
        """Validate and persist a :class:`Campaign`."""
        schema_fields = {"id", "campaign_id", "name", "status", "config", "created_at", "updated_at"}
        
        campaign_data = {}
        config_data = dict(kwargs.get("config") or {})
        
        for k, v in kwargs.items():
            if k in schema_fields:
                campaign_data[k] = v
            else:
                config_data[k] = v
        
        campaign_data["config"] = config_data
        
        model = Campaign(**campaign_data)
        data = model.model_dump(mode="json")
        data["id"] = kwargs.get("id") or f"campaign:{campaign_data['campaign_id']}"
        return await self._store.save(_CAMPAIGNS, data)

    async def save_lead(self, data: dict) -> str:
        """Save a lead to the leads collection/table."""
        lead_id = data.get("id") or data.get("lead_id")
        if not lead_id:
            lead_id = str(uuid.uuid4())
            data["id"] = lead_id
            data["lead_id"] = lead_id
        return await self._store.save(_LEADS, data)

    async def get_lead(self, lead_id: str) -> Optional[dict]:
        """Retrieve a lead by primary key."""
        return await self._store.get(_LEADS, lead_id)

    async def get_campaign(self, campaign_id: str) -> Optional[dict]:
        """Retrieve a campaign by campaign_id or id, merging config into top-level."""
        raw_campaign = None
        if isinstance(self._store, PostgresStore):
            await self._store._ensure_pool()
            query = "SELECT * FROM campaigns WHERE campaign_id = $1 OR id = $1 OR id = $2 LIMIT 1;"
            async with self._store._pool.acquire() as conn:
                row = await conn.fetchrow(query, campaign_id, f"campaign:{campaign_id}")
                if row:
                    raw_campaign = self._store._row_to_dict("campaigns", row)
        else:
            results = await self._store.query(_CAMPAIGNS, {"campaign_id": campaign_id})
            if results:
                raw_campaign = results[0]
            else:
                res = await self._store.get(_CAMPAIGNS, campaign_id)
                if res:
                    raw_campaign = res
                else:
                    res = await self._store.get(_CAMPAIGNS, f"campaign:{campaign_id}")
                    if res:
                        raw_campaign = res
        
        if raw_campaign:
            # Merge config fields to top-level for convenience
            config = raw_campaign.get("config")
            if isinstance(config, dict):
                for k, v in config.items():
                    raw_campaign.setdefault(k, v)
            return raw_campaign
        return None



    # ------------------------------------------------------------------
    # Typed query helpers
    # ------------------------------------------------------------------

    async def get_call(self, call_id: str) -> Optional[dict]:
        """Return the lead snapshot for *call_id*, if it exists."""
        results = await self._store.query(_LEADS, {"call_id": call_id})
        return results[0] if results else None

    async def get_call_record(self, call_id: str) -> Optional[dict]:
        """Return the call details matching *call_id* from calls table, if it exists."""
        results = await self._store.query(_CALLS, {"call_id": call_id})
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

    # ------------------------------------------------------------------
    # Dialer & Campaign Runner Helper Methods
    # ------------------------------------------------------------------

    async def save_caller_id(self, **kwargs: Any) -> str:
        """Validate and persist a caller_id."""
        data = dict(kwargs)
        caller_id = data.get("caller_id")
        campaign_id = data.get("campaign_id")
        if not caller_id or not campaign_id:
            raise ValueError("caller_id and campaign_id are required")
        
        data.setdefault("status", "active")
        data.setdefault("daily_call_count", 0)
        data.setdefault("answer_rate", 0.0)
        data.setdefault("dnc_rate", 0.0)
        data.setdefault("complaint_rate", 0.0)
        data.setdefault("total_calls", 0)
        data.setdefault("total_answers", 0)
        data.setdefault("total_dncs", 0)
        data.setdefault("total_complaints", 0)
        
        if isinstance(self._store, PostgresStore):
            await self._store._ensure_pool()
            from storage.postgres_store import TABLE_COLUMNS
            columns = TABLE_COLUMNS["caller_ids"]
            insert_fields = []
            insert_values = []
            placeholders = []
            placeholder_idx = 1
            for col in columns:
                if col in data:
                    insert_fields.append(col)
                    v = data[col]
                    if isinstance(v, (dict, list)):
                        import json
                        v = json.dumps(v)
                    elif isinstance(v, str) and (col.endswith("_at") or col == "cooldown_until"):
                        from datetime import datetime
                        try:
                            v = datetime.fromisoformat(v.replace("Z", "+00:00"))
                        except ValueError:
                            pass
                    insert_values.append(v)
                    placeholders.append(f"${placeholder_idx}")
                    placeholder_idx += 1
            
            update_clauses = []
            for field in insert_fields:
                if field not in ("caller_id", "campaign_id"):
                    update_clauses.append(f"{field} = EXCLUDED.{field}")
            
            conflict_clause = ""
            if update_clauses:
                conflict_clause = f"ON CONFLICT (caller_id, campaign_id) DO UPDATE SET {', '.join(update_clauses)}"
            else:
                conflict_clause = "ON CONFLICT (caller_id, campaign_id) DO NOTHING"
                
            query = f"""
                INSERT INTO caller_ids ({', '.join(insert_fields)})
                VALUES ({', '.join(placeholders)})
                {conflict_clause}
            """
            async with self._store._pool.acquire() as conn:
                await conn.execute(query, *insert_values)
            return f"{caller_id}:{campaign_id}"
        else:
            data.setdefault("id", f"{caller_id}:{campaign_id}")
            return await self._store.save("caller_ids", data)

    async def get_caller_id(self, caller_id: str, campaign_id: str) -> Optional[dict]:
        """Retrieve a caller ID by composite key."""
        if isinstance(self._store, PostgresStore):
            await self._store._ensure_pool()
            query = "SELECT * FROM caller_ids WHERE caller_id = $1 AND campaign_id = $2 LIMIT 1;"
            async with self._store._pool.acquire() as conn:
                row = await conn.fetchrow(query, caller_id, campaign_id)
                if row:
                    return self._store._row_to_dict("caller_ids", row)
                return None
        else:
            results = await self._store.query("caller_ids", {"id": f"{caller_id}:{campaign_id}"})
            return results[0] if results else None

    async def list_caller_ids(self, campaign_id: str) -> list[dict]:
        """List all caller IDs for a campaign."""
        if isinstance(self._store, PostgresStore):
            await self._store._ensure_pool()
            query = "SELECT * FROM caller_ids WHERE campaign_id = $1;"
            async with self._store._pool.acquire() as conn:
                rows = await conn.fetch(query, campaign_id)
                return [self._store._row_to_dict("caller_ids", r) for r in rows]
        else:
            return await self._store.query("caller_ids", {"campaign_id": campaign_id})

    async def mark_caller_id_used(self, caller_id: str, campaign_id: str, now: Optional[datetime] = None) -> Optional[dict]:
        """Mark a caller ID as used, updating metrics."""
        from datetime import datetime
        now_dt = now or datetime.utcnow()
        if isinstance(self._store, PostgresStore):
            await self._store._ensure_pool()
            query = """
                UPDATE caller_ids
                SET daily_call_count = daily_call_count + 1,
                    total_calls = total_calls + 1,
                    last_used_at = $3,
                    updated_at = $3
                WHERE caller_id = $1 AND campaign_id = $2
                RETURNING *;
            """
            async with self._store._pool.acquire() as conn:
                row = await conn.fetchrow(query, caller_id, campaign_id, now_dt)
                if row:
                    return self._store._row_to_dict("caller_ids", row)
                return None
        else:
            cid = await self.get_caller_id(caller_id, campaign_id)
            if cid:
                cid["daily_call_count"] = cid.get("daily_call_count", 0) + 1
                cid["total_calls"] = cid.get("total_calls", 0) + 1
                cid["last_used_at"] = now_dt.isoformat()
                cid["updated_at"] = now_dt.isoformat()
                await self._store.save("caller_ids", cid)
                return cid
            return None

    async def update_caller_id_metrics(self, caller_id: str, campaign_id: str, outcome: str) -> Optional[dict]:
        """Update caller ID metrics based on call outcome."""
        is_answer = 1 if outcome == "human_answered" else 0
        is_dnc = 1 if outcome == "dnc" else 0
        
        if isinstance(self._store, PostgresStore):
            await self._store._ensure_pool()
            query = """
                UPDATE caller_ids
                SET total_answers = total_answers + $3,
                    total_dncs = total_dncs + $4,
                    updated_at = NOW()
                WHERE caller_id = $1 AND campaign_id = $2
                RETURNING *;
            """
            async with self._store._pool.acquire() as conn:
                row = await conn.fetchrow(query, caller_id, campaign_id, is_answer, is_dnc)
                if row:
                    cid = self._store._row_to_dict("caller_ids", row)
                    total = cid.get("total_calls", 0)
                    if total > 0:
                        ans_rate = cid.get("total_answers", 0) / total
                        dnc_rate = cid.get("total_dncs", 0) / total
                        rate_query = """
                            UPDATE caller_ids
                            SET answer_rate = $3, dnc_rate = $4
                            WHERE caller_id = $1 AND campaign_id = $2
                            RETURNING *;
                        """
                        row = await conn.fetchrow(rate_query, caller_id, campaign_id, ans_rate, dnc_rate)
                        if row:
                            return self._store._row_to_dict("caller_ids", row)
                    return cid
                return None
        else:
            cid = await self.get_caller_id(caller_id, campaign_id)
            if cid:
                cid["total_answers"] = cid.get("total_answers", 0) + is_answer
                cid["total_dncs"] = cid.get("total_dncs", 0) + is_dnc
                total = cid.get("total_calls", 0)
                if total > 0:
                    cid["answer_rate"] = cid["total_answers"] / total
                    cid["dnc_rate"] = cid["total_dncs"] / total
                from datetime import datetime
                cid["updated_at"] = datetime.utcnow().isoformat()
                await self._store.save("caller_ids", cid)
                return cid
            return None

    async def set_caller_id_cooldown(self, caller_id: str, campaign_id: str, cooldown_until: datetime, reason: str) -> Optional[dict]:
        """Put caller ID on cooldown."""
        if isinstance(self._store, PostgresStore):
            await self._store._ensure_pool()
            query = """
                UPDATE caller_ids
                SET cooldown_until = $3,
                    status = 'cooldown',
                    updated_at = NOW()
                WHERE caller_id = $1 AND campaign_id = $2
                RETURNING *;
            """
            async with self._store._pool.acquire() as conn:
                row = await conn.fetchrow(query, caller_id, campaign_id, cooldown_until)
                if row:
                    return self._store._row_to_dict("caller_ids", row)
                return None
        else:
            cid = await self.get_caller_id(caller_id, campaign_id)
            if cid:
                cid["cooldown_until"] = cooldown_until.isoformat() if hasattr(cooldown_until, "isoformat") else cooldown_until
                cid["status"] = "cooldown"
                from datetime import datetime
                cid["updated_at"] = datetime.utcnow().isoformat()
                await self._store.save("caller_ids", cid)
                return cid
            return None

    async def select_and_lock_next_lead(self, campaign_id: str, lock_holder_id: str, now: Optional[datetime] = None) -> Optional[dict]:
        """Select eligible lead and lock it using row-level locking."""
        from datetime import datetime, timezone
        now_dt = now or datetime.utcnow()
        if isinstance(now_dt, datetime) and now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
        if isinstance(self._store, PostgresStore):
            await self._store._ensure_pool()
            query = """
                UPDATE leads
                SET lock_holder_id = $1, locked_at = $2
                WHERE id = (
                    SELECT id FROM leads
                    WHERE campaign_id = $3
                      AND (
                          (status IN ('pending', 'failed') AND (retry_after IS NULL OR retry_after <= $2))
                          OR (status = 'callback' AND callback_time IS NOT NULL AND callback_time <= $2)
                      )
                      AND lock_holder_id IS NULL
                    ORDER BY priority DESC, created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING *;
            """
            async with self._store._pool.acquire() as conn:
                row = await conn.fetchrow(query, lock_holder_id, now_dt, campaign_id)
                if row:
                    return self._store._row_to_dict("leads", row)
                return None
        else:
            import json
            lock = self._store._lock_for(_LEADS)
            async with lock:
                path = self._store._path_for(_LEADS)
                if not path.exists():
                    return None
                
                leads = []
                locked_lead = None
                
                with path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        lead = json.loads(line)
                        leads.append(lead)
                
                eligible_leads = []
                for lead in leads:
                    campaign_match = lead.get("campaign_id") == campaign_id
                    locked_match = lead.get("lock_holder_id") is None
                    
                    status_match = False
                    if lead.get("status") in ("pending", "failed"):
                        retry_after_str = lead.get("retry_after")
                        retry_after = parse_dt(retry_after_str) if retry_after_str else None
                        status_match = (not retry_after) or (retry_after <= now_dt)
                    elif lead.get("status") == "callback":
                        callback_time_str = lead.get("callback_time")
                        callback_time = parse_dt(callback_time_str) if callback_time_str else None
                        status_match = bool(callback_time and (callback_time <= now_dt))
                    
                    if campaign_match and status_match and locked_match:
                         eligible_leads.append(lead)
                
                if eligible_leads:
                    def sort_key(l):
                        priority = l.get("priority", 0)
                        created_at = l.get("created_at", "")
                        return (-priority, created_at)
                    
                    eligible_leads.sort(key=sort_key)
                    locked_lead = eligible_leads[0]
                    locked_lead["lock_holder_id"] = lock_holder_id
                    locked_lead["locked_at"] = now_dt.isoformat()
                    
                    with path.open("w", encoding="utf-8") as fh:
                        for lead in leads:
                            if lead.get("id") == locked_lead.get("id"):
                                fh.write(json.dumps(locked_lead, default=str) + "\n")
                            else:
                                fh.write(json.dumps(lead, default=str) + "\n")
                                
                return locked_lead

    async def release_lead_lock(
        self,
        lead_id: str,
        reason: str,
        retry_after: Optional[datetime] = None,
        status_override: Optional[str] = None
    ) -> Optional[dict]:
        """Release lead lock, mapping the release reason to the correct lead status."""
        if status_override is not None:
            status_update = status_override
        else:
            status_update = None
            if reason == "outside_calling_window":
                status_update = "pending"
            elif reason == "missing_consent_record":
                status_update = "consent_missing"
            elif reason == "caller_id_inactive":
                status_update = "pending"
            elif reason == "no_agent_available_for_live_transfer":
                status_update = "pending"
            elif reason == "missing_state_for_transfer_routing":
                status_update = "pending"
            elif reason in ("transient_call_failure", "carrier_failure"):
                status_update = "failed"
            elif reason == "hostile_refusal":
                status_update = "hostile_refusal"
            elif reason == "disconnected_bad_number":
                status_update = "disconnected"
            elif reason == "consent_invalid":
                status_update = "consent_invalid"
            
        if isinstance(self._store, PostgresStore):
            await self._store._ensure_pool()
            status_clause = ""
            params = [lead_id]
            param_idx = 2
            if status_update is not None:
                status_clause = f", status = ${param_idx}"
                params.append(status_update)
                param_idx += 1
            
            retry_clause = ""
            if retry_after is not None:
                retry_clause = f", retry_after = ${param_idx}"
                params.append(retry_after)
                param_idx += 1
            else:
                # Explicitly clear retry_after if no retry
                retry_clause = f", retry_after = NULL"
            
            query = f"""
                UPDATE leads
                SET lock_holder_id = NULL, locked_at = NULL {status_clause} {retry_clause}
                WHERE id = $1
                RETURNING *;
            """
            async with self._store._pool.acquire() as conn:
                row = await conn.fetchrow(query, *params)
                if row:
                    return self._store._row_to_dict("leads", row)
                return None
        else:
            lock = self._store._lock_for(_LEADS)
            async with lock:
                path = self._store._path_for(_LEADS)
                if not path.exists():
                    return None
                
                leads = []
                updated_lead = None
                with path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        lead = json.loads(line)
                        if lead.get("id") == lead_id:
                            lead["lock_holder_id"] = None
                            lead["locked_at"] = None
                            if status_update is not None:
                                lead["status"] = status_update
                                if lead.get("lead_profile"):
                                    lead["lead_profile"]["status"] = status_update
                            lead["retry_after"] = (
                                retry_after.isoformat() if hasattr(retry_after, "isoformat")
                                else (retry_after if retry_after is not None else None)
                            )
                            updated_lead = lead
                        leads.append(lead)
                
                if updated_lead:
                    with path.open("w", encoding="utf-8") as fh:
                        for lead in leads:
                            fh.write(json.dumps(lead, default=str) + "\n")
                return updated_lead

    async def get_consent_record_for_lead(self, lead_id: str, phone_e164: str, campaign_id: str) -> Optional[dict]:
        """Fetch marketing/TCPA consent record matching hierarchy."""
        if isinstance(self._store, PostgresStore):
            await self._store._ensure_pool()
            query = """
                SELECT * FROM consent_records
                WHERE (lead_id = $1)
                   OR (phone_e164 = $2 AND campaign_id = $3)
                   OR (phone_e164 = $2)
                ORDER BY consent_timestamp DESC LIMIT 1;
            """
            async with self._store._pool.acquire() as conn:
                row = await conn.fetchrow(query, lead_id, phone_e164, campaign_id)
                if row:
                    return self._store._row_to_dict("consent_records", row)
                return None
        else:
            records = await self._store.query("consent_records", {})
            matches = []
            for r in records:
                if r.get("lead_id") == lead_id:
                    matches.append(r)
                elif r.get("phone_e164") == phone_e164 and r.get("campaign_id") == campaign_id:
                    matches.append(r)
                elif r.get("phone_e164") == phone_e164:
                    matches.append(r)
            if matches:
                matches.sort(key=lambda x: x.get("consent_timestamp", ""), reverse=True)
                return matches[0]
            return None

    async def mark_lead_attempted(self, lead_id: str, call_id: str, caller_id: str, now: Optional[datetime] = None) -> Optional[dict]:
        """Increment lead attempts and set last attempt time and dialing status, clearing callback_time."""
        from datetime import datetime
        now_dt = now or datetime.utcnow()
        if isinstance(self._store, PostgresStore):
            await self._store._ensure_pool()
            query = """
                UPDATE leads
                SET attempts = attempts + 1,
                    last_attempt_at = $2,
                    status = 'dialing',
                    callback_time = NULL
                WHERE id = $1
                RETURNING *;
            """
            async with self._store._pool.acquire() as conn:
                row = await conn.fetchrow(query, lead_id, now_dt)
                if row:
                    return self._store._row_to_dict("leads", row)
                return None
        else:
            lead = await self._store.get(_LEADS, lead_id)
            if lead:
                lead["attempts"] = lead.get("attempts", 0) + 1
                lead["last_attempt_at"] = now_dt.isoformat()
                lead["status"] = "dialing"
                lead["callback_time"] = None
                if lead.get("lead_profile"):
                    lead["lead_profile"]["status"] = "dialing"
                await self._store.save(_LEADS, lead)
                return lead
            return None

    async def mark_lead_completed(self, lead_id: str, outcome: str) -> Optional[dict]:
        """Mark lead as completed."""
        if isinstance(self._store, PostgresStore):
            await self._store._ensure_pool()
            query = """
                UPDATE leads
                SET status = 'completed',
                    lock_holder_id = NULL,
                    locked_at = NULL,
                    retry_after = NULL
                WHERE id = $1
                RETURNING *;
            """
            async with self._store._pool.acquire() as conn:
                row = await conn.fetchrow(query, lead_id)
                if row:
                    return self._store._row_to_dict("leads", row)
                return None
        else:
            lead = await self._store.get(_LEADS, lead_id)
            if lead:
                lead["status"] = "completed"
                lead["lock_holder_id"] = None
                lead["locked_at"] = None
                lead["retry_after"] = None
                if lead.get("lead_profile"):
                    lead["lead_profile"]["status"] = "completed"
                await self._store.save(_LEADS, lead)
                return lead
            return None

    async def mark_lead_wrong_number(self, lead_id: str, phone_e164: Optional[str] = None, campaign_id: Optional[str] = None, reason: str = "wrong_number") -> Optional[dict]:
        """Mark lead as wrong number."""
        if isinstance(self._store, PostgresStore):
            await self._store._ensure_pool()
            query = """
                UPDATE leads
                SET status = 'wrong_number',
                    lock_holder_id = NULL,
                    locked_at = NULL,
                    retry_after = NULL
                WHERE id = $1
                RETURNING *;
            """
            async with self._store._pool.acquire() as conn:
                row = await conn.fetchrow(query, lead_id)
                if row:
                    return self._store._row_to_dict("leads", row)
                return None
        else:
            lead = await self._store.get(_LEADS, lead_id)
            if lead:
                lead["status"] = "wrong_number"
                lead["lock_holder_id"] = None
                lead["locked_at"] = None
                lead["retry_after"] = None
                if lead.get("lead_profile"):
                    lead["lead_profile"]["status"] = "wrong_number"
                await self._store.save(_LEADS, lead)
                return lead
            return None

    async def mark_lead_dnc(self, lead_id: str, phone_e164: str, campaign_id: str, reason: str) -> Optional[dict]:
        """Add lead to DNC registry and mark lead status as dnc."""
        from datetime import datetime
        await self.save_dnc_request(
            lead_id=lead_id,
            phone_e164=phone_e164,
            campaign_id=campaign_id,
            reason=reason,
            requested_at=datetime.utcnow().isoformat()
        )
        if isinstance(self._store, PostgresStore):
            await self._store._ensure_pool()
            query = """
                UPDATE leads
                SET status = 'dnc',
                    lock_holder_id = NULL,
                    locked_at = NULL,
                    retry_after = NULL
                WHERE id = $1
                RETURNING *;
            """
            async with self._store._pool.acquire() as conn:
                row = await conn.fetchrow(query, lead_id)
                if row:
                    return self._store._row_to_dict("leads", row)
                return None
        else:
            lead = await self._store.get(_LEADS, lead_id)
            if lead:
                lead["status"] = "dnc"
                lead["lock_holder_id"] = None
                lead["locked_at"] = None
                lead["retry_after"] = None
                if lead.get("lead_profile"):
                    lead["lead_profile"]["status"] = "dnc"
                await self._store.save(_LEADS, lead)
                return lead
            return None

    async def mark_lead_callback(self, lead_id: str, callback_time: datetime) -> Optional[dict]:
        """Schedule callback for lead, clearing locks."""
        if isinstance(self._store, PostgresStore):
            await self._store._ensure_pool()
            query = """
                UPDATE leads
                SET status = 'callback',
                    callback_time = $2,
                    lock_holder_id = NULL,
                    locked_at = NULL,
                    retry_after = NULL
                WHERE id = $1
                RETURNING *;
            """
            async with self._store._pool.acquire() as conn:
                row = await conn.fetchrow(query, lead_id, callback_time)
                if row:
                    return self._store._row_to_dict("leads", row)
                return None
        else:
            lead = await self._store.get(_LEADS, lead_id)
            if lead:
                lead["status"] = "callback"
                lead["callback_time"] = callback_time.isoformat() if hasattr(callback_time, "isoformat") else callback_time
                lead["lock_holder_id"] = None
                lead["locked_at"] = None
                lead["retry_after"] = None
                if lead.get("lead_profile"):
                    lead["lead_profile"]["status"] = "callback"
                await self._store.save(_LEADS, lead)
                return lead
            return None

    async def save_call_disposition(self, call_id: str, lead_id: str, campaign_id: str, outcome: str, amd_result: Optional[str], retry_after: Optional[datetime], caller_id: str, dry_run: bool = False) -> str:
        """Create and save call record with outcomes."""
        from datetime import datetime
        return await self.save_call(
            call_id=call_id,
            lead_id=lead_id,
            campaign_id=campaign_id,
            caller_id=caller_id,
            outcome=outcome,
            amd_result=amd_result,
            retry_after=retry_after,
            dry_run=dry_run,
            started_at=datetime.utcnow()
        )

    # ------------------------------------------------------------------
    # Webhook Outbox helpers
    # ------------------------------------------------------------------

    async def save_webhook_event(self, event_dict: Optional[dict] = None, **kwargs: Any) -> str:
        """Save a webhook event to the outbox (database or JSONL)."""
        event_data = dict(event_dict) if event_dict is not None else {}
        event_data.update(kwargs)
        if "id" not in event_data:
            event_data["id"] = event_data.get("event_id") or str(uuid.uuid4())
        
        # Parse/format datetime objects
        for field in ("next_attempt_at", "sent_at", "claimed_at", "created_at", "updated_at"):
            if field in event_data and event_data[field]:
                val = event_data[field]
                if isinstance(val, datetime):
                    event_data[field] = val.isoformat()
        
        return await self._store.save(_WEBHOOK_EVENTS, event_data)

    async def get_webhook_event(self, event_id: str) -> Optional[dict]:
        """Retrieve a webhook event by its event_id or primary key."""
        res = await self._store.get(_WEBHOOK_EVENTS, event_id)
        if not res:
            results = await self._store.query(_WEBHOOK_EVENTS, {"event_id": event_id})
            if results:
                res = results[0]
        return res

    async def list_pending_webhook_events(self) -> list[dict]:
        """List webhook events in pending status whose next_attempt_at has passed or is unset."""
        now = datetime.now(timezone.utc)
        if isinstance(self._store, PostgresStore):
            await self._store._ensure_pool()
            query = """
            SELECT * FROM webhook_events
            WHERE status = 'pending'
              AND (next_attempt_at IS NULL OR next_attempt_at <= $1)
            ORDER BY created_at ASC;
            """
            async with self._store._pool.acquire() as conn:
                rows = await conn.fetch(query, now)
                return [self._store._row_to_dict("webhook_events", r) for r in rows]
        else:
            results = await self._store.query(_WEBHOOK_EVENTS, {"status": "pending"})
            filtered = []
            for r in results:
                next_attempt = r.get("next_attempt_at")
                if not next_attempt:
                    filtered.append(r)
                else:
                    dt = parse_dt(next_attempt)
                    if dt and dt <= now:
                        filtered.append(r)
            return filtered

    async def mark_webhook_event_sent(self, event_id: str, delivered_at: datetime, response_status_code: int = 200, response_body_preview: Optional[str] = None) -> None:
        """Mark a webhook event as successfully sent."""
        event = await self.get_webhook_event(event_id)
        if event:
            event["status"] = "sent"
            event["sent_at"] = delivered_at.isoformat() if hasattr(delivered_at, "isoformat") else delivered_at
            event["response_status_code"] = response_status_code
            event["response_body_preview"] = response_body_preview
            event["updated_at"] = datetime.now(timezone.utc).isoformat()
            await self.save_webhook_event(**event)

    async def mark_webhook_event_retry(self, event_id: str, attempt_count: int, next_attempt_at: datetime, last_error: str) -> None:
        """Record a webhook sending failure and schedule a retry by putting it back to pending."""
        event = await self.get_webhook_event(event_id)
        if event:
            event["status"] = "pending"
            event["attempt_count"] = attempt_count
            event["next_attempt_at"] = next_attempt_at.isoformat() if hasattr(next_attempt_at, "isoformat") else next_attempt_at
            event["last_error"] = last_error
            event["updated_at"] = datetime.now(timezone.utc).isoformat()
            await self.save_webhook_event(**event)

    async def mark_webhook_event_failed(self, event_id: str, last_error: str, attempt_count: Optional[int] = None) -> None:
        """Mark a webhook event as permanently failed after max retries are exceeded."""
        event = await self.get_webhook_event(event_id)
        if event:
            event["status"] = "failed"
            event["last_error"] = last_error
            if attempt_count is not None:
                event["attempt_count"] = attempt_count
            event["updated_at"] = datetime.now(timezone.utc).isoformat()
            await self.save_webhook_event(**event)

    async def claim_pending_webhook_events(self, limit: int, worker_id: str, now: Optional[datetime] = None) -> list[dict]:
        """Claim pending webhook events atomically using row-level locking to avoid duplicates."""
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        claim_timeout = float(os.getenv("DANA_CRM_WEBHOOK_CLAIM_TIMEOUT_SECONDS", "300"))
        stale_cutoff = now - timedelta(seconds=claim_timeout)
            
        if isinstance(self._store, PostgresStore):
            await self._store._ensure_pool()
            # Atomic update using row lock FOR UPDATE SKIP LOCKED
            query = """
            UPDATE webhook_events
            SET status = 'claimed',
                claimed_by = $1,
                claimed_at = $2,
                updated_at = $2
            WHERE id IN (
                SELECT id FROM webhook_events
                WHERE (status = 'pending' AND (next_attempt_at IS NULL OR next_attempt_at <= $2))
                   OR (status = 'claimed' AND claimed_at < $3)
                ORDER BY created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT $4
            )
            RETURNING *;
            """
            async with self._store._pool.acquire() as conn:
                rows = await conn.fetch(query, worker_id, now, stale_cutoff, limit)
                return [self._store._row_to_dict("webhook_events", r) for r in rows]
        else:
            # Local lock for JSONL mode to prevent duplicate concurrent claims
            async with self._claim_lock:
                results_pending = await self._store.query(_WEBHOOK_EVENTS, {"status": "pending"})
                results_claimed = await self._store.query(_WEBHOOK_EVENTS, {"status": "claimed"})
                results = results_pending + results_claimed
                results.sort(key=lambda x: x.get("created_at") or "")

                claimed = []
                for r in results:
                    if len(claimed) >= limit:
                        break

                    status = r.get("status")
                    if status == "pending":
                        next_attempt = r.get("next_attempt_at")
                        if not next_attempt:
                            eligible = True
                        else:
                            dt = parse_dt(next_attempt)
                            eligible = dt and dt <= now
                    elif status == "claimed":
                        claimed_at_str = r.get("claimed_at")
                        if claimed_at_str:
                            claimed_at_dt = parse_dt(claimed_at_str)
                            eligible = claimed_at_dt and claimed_at_dt < stale_cutoff
                        else:
                            eligible = True
                    else:
                        eligible = False

                    if eligible:
                        r["status"] = "claimed"
                        r["claimed_by"] = worker_id
                        r["claimed_at"] = now.isoformat()
                        r["updated_at"] = now.isoformat()
                        await self.save_webhook_event(**r)
                        claimed.append(r)
                return claimed

