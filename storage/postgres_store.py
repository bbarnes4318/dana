"""PostgreSQL-backed implementation of :class:`storage.base.BaseStore`.

Uses ``asyncpg`` when the ``DATABASE_URL`` environment variable is set.
Tables are created lazily on first use.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any, Optional

import asyncpg
from storage.base import BaseStore

logger = logging.getLogger(__name__)

# Explicit known column schemas for all 15 tables to prevent injection and structure queries
TABLE_COLUMNS: dict[str, set[str]] = {
    "schema_migrations": {"version", "applied_at"},
    "campaigns": {"id", "campaign_id", "name", "status", "config", "created_at", "updated_at"},
    "leads": {"id", "lead_id", "phone_e164", "campaign_id", "consent_artifact_id", "source_vendor", "created_at", "updated_at", "status", "payload"},
    "calls": {"id", "call_id", "lead_id", "campaign_id", "phone_e164", "caller_id", "started_at", "answered_at", "ended_at", "duration_seconds", "outcome", "recording_url", "transcript", "qualification", "compliance_flags", "latency_summary", "qa_score", "created_at", "updated_at"},
    "call_turns": {"id", "call_id", "turn_number", "speaker", "text", "stage", "created_at"},
    "call_events": {"id", "call_id", "event_type", "payload", "created_at"},
    "tool_events": {"id", "call_id", "tool_name", "params", "result", "success", "created_at"},
    "transfers": {"id", "call_id", "lead_id", "transfer_mode", "agent_id", "target_phone", "success", "failure_reason", "provider_call_id", "summary", "created_at"},
    "callbacks": {"id", "call_id", "lead_id", "phone_e164", "callback_time_local", "callback_timezone", "status", "notes", "created_at"},
    "dnc_requests": {"id", "call_id", "lead_id", "phone_e164", "campaign_id", "reason", "requested_at", "created_at"},
    "consent_records": {"id", "consent_artifact_id", "lead_id", "phone_e164", "source_vendor", "consent_text", "consent_timestamp", "landing_page_url", "ip_address", "user_agent", "tcpa_consent_version", "campaign_id", "payload", "created_at"},
    "qa_reports": {"id", "call_id", "overall_score", "grade", "scores", "issues", "recommendations", "created_at"},
    "latency_metrics": {"id", "call_id", "metric_name", "metric_value_ms", "created_at"},
    "agent_availability": {"id", "agent_id", "name", "phone_number", "licensed_states", "status", "priority", "max_concurrent_calls", "current_call_count", "last_call_at", "browser_join_enabled", "created_at", "updated_at"},
    "training_notes": {"id", "source", "topic", "sales_lesson", "good_example", "bad_example", "call_stage", "created_at"}
}

ALLOWLIST_TABLES = set(TABLE_COLUMNS.keys())

# Allowlisted JSONB query paths for secure non-column queries
ALLOWED_JSONB_QUERY_PATHS: dict[tuple[str, str], str] = {
    ("leads", "call_id"): "payload->>'call_id' = ${idx}"
}


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
        self._pool: Optional[asyncpg.Pool] = None
        self._pool_lock = asyncio.Lock()
        self._migrations_checked = False

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
        """Create the asyncpg pool on first call and run migrations lazily."""
        self._require_dsn()
        if self._pool is not None and self._migrations_checked:
            return

        async with self._pool_lock:
            if self._pool is None:
                logger.info("Initializing asyncpg connection pool...")
                self._pool = await asyncpg.create_pool(
                    dsn=self._dsn,
                    min_size=1,
                    max_size=10,
                )
            
            if not self._migrations_checked:
                logger.info("Ensuring database migrations are applied...")
                from storage.migrations import run_migrations
                await run_migrations(self._pool)
                self._migrations_checked = True

    async def close(self) -> None:
        """Close the connection pool cleanly."""
        async with self._pool_lock:
            if self._pool is not None:
                await self._pool.close()
                self._pool = None
                self._migrations_checked = False
                logger.info("asyncpg connection pool closed.")

    async def health_check(self) -> dict[str, Any]:
        """Perform a database health check."""
        if not self._dsn:
            return {
                "backend": "postgres",
                "connected": False,
                "migrations_applied": False,
                "error": "DATABASE_URL is not set"
            }
            
        try:
            await self._ensure_pool()
            assert self._pool is not None
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT version FROM schema_migrations WHERE version = $1;", 
                    "001_initial"
                )
                migrations_applied = row is not None
            return {
                "backend": "postgres",
                "connected": True,
                "migrations_applied": migrations_applied
            }
        except Exception as e:
            return {
                "backend": "postgres",
                "connected": False,
                "migrations_applied": False,
                "error": str(e)
            }

    def _map_record_to_columns(self, table: str, record: dict) -> dict[str, Any]:
        """Map standard dictionary representation to the specific column structures."""
        columns = TABLE_COLUMNS[table]
        mapped: dict[str, Any] = {}
        extra: dict[str, Any] = {}

        # Normalize timestamp to created_at if the target table uses created_at
        record_copy = dict(record)
        if "timestamp" in record_copy:
            if "created_at" in columns and "created_at" not in record_copy:
                record_copy["created_at"] = record_copy["timestamp"]
            if "timestamp" not in columns:
                record_copy.pop("timestamp", None)

        # 1. Simple direct matching & extra partitioning
        for k, v in record_copy.items():
            if k in columns:
                if isinstance(v, (dict, list)):
                    mapped[k] = json.dumps(v)
                else:
                    mapped[k] = v
            else:
                extra[k] = v

        # 2. Custom collection-specific extraction
        if table == "leads":
            lead_profile = record.get("lead_profile") or {}
            mapped["lead_id"] = lead_profile.get("lead_id") or record.get("lead_id")
            mapped["phone_e164"] = (
                lead_profile.get("lead_phone_e164") 
                or lead_profile.get("phone_e164") 
                or record.get("phone_e164")
            )
            mapped["campaign_id"] = lead_profile.get("campaign_id") or record.get("campaign_id")
            mapped["consent_artifact_id"] = lead_profile.get("consent_artifact_id") or record.get("consent_artifact_id")
            mapped["source_vendor"] = lead_profile.get("consent_source") or record.get("source_vendor")
            mapped["status"] = lead_profile.get("status") or record.get("status")
            mapped["payload"] = json.dumps(record)
        elif table == "consent_records":
            mapped["payload"] = json.dumps(record)
        elif table == "campaigns":
            config = record.get("config") or {}
            if isinstance(config, dict):
                config.update(extra)
                mapped["config"] = json.dumps(config)
            else:
                mapped["config"] = json.dumps(extra)
        else:
            if extra:
                raise ValueError(f"Unknown fields {list(extra.keys())} for table {table}")

        # Ensure datetime conversion for time fields
        for k, v in mapped.items():
            if isinstance(v, str) and (
                k.endswith("_at") 
                or k.endswith("_timestamp") 
                or k == "timestamp" 
                or k == "requested_at"
            ):
                try:
                    mapped[k] = datetime.fromisoformat(v.replace("Z", "+00:00"))
                except ValueError:
                    pass

        return mapped

    def _row_to_dict(self, table: str, row: Any) -> dict[str, Any]:
        """Convert a row Record back to the original dictionary representation."""
        row_dict = dict(row)
        
        # Parse JSON/JSONB fields
        for k, v in row_dict.items():
            if v is not None and k in TABLE_COLUMNS[table]:
                if isinstance(v, str) and (v.startswith("{") or v.startswith("[")):
                    try:
                        row_dict[k] = json.loads(v)
                    except json.JSONDecodeError:
                        pass

        # For leads or consent_records, return the original stored payload
        if table in ("leads", "consent_records") and "payload" in row_dict:
            payload = row_dict["payload"]
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    pass
            if isinstance(payload, dict):
                payload["id"] = row_dict.get("id")
                return payload

        return row_dict

    # ------------------------------------------------------------------
    # BaseStore interface
    # ------------------------------------------------------------------

    async def save(self, collection: str, data: dict) -> str:
        """Insert or update *data* into the appropriate Postgres table."""
        self._require_dsn()
        if collection not in ALLOWLIST_TABLES:
            raise ValueError(f"Table name '{collection}' is not allowed.")

        await self._ensure_pool()
        assert self._pool is not None

        record = dict(data)
        if "id" not in record:
            import uuid
            record["id"] = str(uuid.uuid4())
        record_id: str = record["id"]

        mapped = self._map_record_to_columns(collection, record)

        insert_fields = []
        insert_values = []
        placeholders = []
        placeholder_idx = 1

        for col in TABLE_COLUMNS[collection]:
            if col in mapped:
                insert_fields.append(col)
                insert_values.append(mapped[col])
                placeholders.append(f"${placeholder_idx}")
                placeholder_idx += 1

        update_clauses = []
        for field in insert_fields:
            if field != "id":
                update_clauses.append(f"{field} = EXCLUDED.{field}")

        if update_clauses:
            conflict_clause = f"ON CONFLICT (id) DO UPDATE SET {', '.join(update_clauses)}"
        else:
            conflict_clause = "ON CONFLICT (id) DO NOTHING"

        query = f"""
            INSERT INTO {collection} ({', '.join(insert_fields)})
            VALUES ({', '.join(placeholders)})
            {conflict_clause}
        """

        async with self._pool.acquire() as conn:
            await conn.execute(query, *insert_values)

        return record_id

    async def get(self, collection: str, id: str) -> Optional[dict]:
        """Retrieve a record by primary key."""
        self._require_dsn()
        if collection not in ALLOWLIST_TABLES:
            raise ValueError(f"Table name '{collection}' is not allowed.")

        await self._ensure_pool()
        assert self._pool is not None

        query = f"SELECT * FROM {collection} WHERE id = $1 LIMIT 1;"
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(query, id)
            if not row:
                return None
            return self._row_to_dict(collection, row)
        except asyncpg.UndefinedTableError:
            return None

    async def list_recent(self, collection: str, limit: int = 50) -> list[dict]:
        """Return the most recent records, ordered by created_at DESC."""
        self._require_dsn()
        if collection not in ALLOWLIST_TABLES:
            raise ValueError(f"Table name '{collection}' is not allowed.")

        await self._ensure_pool()
        assert self._pool is not None

        query = f"SELECT * FROM {collection} ORDER BY created_at DESC LIMIT $1;"
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(query, limit)
            return [self._row_to_dict(collection, r) for r in rows]
        except asyncpg.UndefinedTableError:
            return []

    async def query(self, collection: str, filters: dict) -> list[dict]:
        """Query records matching the specified filters (simple equality & allowlisted JSONB)."""
        self._require_dsn()
        if collection not in ALLOWLIST_TABLES:
            raise ValueError(f"Table name '{collection}' is not allowed.")

        await self._ensure_pool()
        assert self._pool is not None

        columns = TABLE_COLUMNS[collection]
        where_clauses = []
        params = []
        param_idx = 1

        for k, v in filters.items():
            if k in columns:
                where_clauses.append(f"{k} = ${param_idx}")
                params.append(v)
                param_idx += 1
            elif (collection, k) in ALLOWED_JSONB_QUERY_PATHS:
                sql_pattern = ALLOWED_JSONB_QUERY_PATHS[(collection, k)]
                where_clauses.append(sql_pattern.replace("${idx}", f"${param_idx}"))
                params.append(str(v))
                param_idx += 1
            else:
                raise ValueError(
                    f"Filtering by '{k}' on '{collection}' is not allowed or supported."
                )

        where_sql = ""
        if where_clauses:
            where_sql = f"WHERE {' AND '.join(where_clauses)}"

        query = f"SELECT * FROM {collection} {where_sql} ORDER BY created_at DESC;"
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(query, *params)
            return [self._row_to_dict(collection, r) for r in rows]
        except asyncpg.UndefinedTableError:
            return []
