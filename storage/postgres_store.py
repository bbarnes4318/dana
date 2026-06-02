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

# Explicit known column schemas for all 16 tables to prevent injection and structure queries
TABLE_COLUMNS: dict[str, set[str]] = {
    "schema_migrations": {"version", "applied_at"},
    "campaigns": {"id", "campaign_id", "name", "status", "config", "created_at", "updated_at"},
    "leads": {"id", "lead_id", "phone_e164", "campaign_id", "consent_artifact_id", "source_vendor", "created_at", "updated_at", "status", "payload", "attempts", "last_attempt_at", "retry_after", "lock_holder_id", "locked_at", "callback_time", "priority"},
    "calls": {"id", "call_id", "lead_id", "campaign_id", "phone_e164", "caller_id", "started_at", "answered_at", "ended_at", "duration_seconds", "outcome", "recording_url", "transcript", "qualification", "compliance_flags", "latency_summary", "qa_score", "created_at", "updated_at", "amd_result", "retry_after", "dry_run"},
    "call_turns": {
        "id", "call_id", "turn_number", "speaker", "text", "stage", "created_at",
        "call_attempt_id", "campaign_id", "lead_id", "livekit_room_name", "participant_id",
        "compliance_warnings", "latency_metrics", "selected_did", "caller_id_source"
    },
    "call_events": {"id", "call_id", "event_type", "payload", "created_at"},
    "tool_events": {"id", "call_id", "tool_name", "params", "result", "success", "created_at"},
    "transfers": {"id", "call_id", "lead_id", "transfer_mode", "agent_id", "target_phone", "success", "failure_reason", "provider_call_id", "summary", "created_at"},
    "callbacks": {"id", "call_id", "lead_id", "phone_e164", "callback_time_local", "callback_timezone", "status", "notes", "created_at"},
    "dnc_requests": {"id", "call_id", "lead_id", "phone_e164", "campaign_id", "reason", "requested_at", "created_at"},
    "consent_records": {"id", "consent_artifact_id", "lead_id", "phone_e164", "source_vendor", "consent_text", "consent_timestamp", "landing_page_url", "ip_address", "user_agent", "tcpa_consent_version", "campaign_id", "payload", "created_at"},
    "qa_reports": {"id", "call_id", "overall_score", "grade", "scores", "issues", "recommendations", "created_at"},
    "latency_metrics": {"id", "call_id", "metric_name", "metric_value_ms", "created_at"},
    "agent_availability": {"id", "agent_id", "name", "phone_number", "licensed_states", "status", "priority", "max_concurrent_calls", "current_call_count", "last_call_at", "browser_join_enabled", "created_at", "updated_at"},
    "training_notes": {"id", "source", "topic", "sales_lesson", "good_example", "bad_example", "call_stage", "created_at"},
    "caller_ids": {"caller_id", "campaign_id", "status", "daily_call_count", "answer_rate", "dnc_rate", "complaint_rate", "stir_shaken_status", "last_used_at", "cooldown_until", "total_calls", "total_answers", "total_dncs", "total_complaints", "created_at", "updated_at"},
    "webhook_events": {"id", "event_type", "event_id", "destination", "payload", "status", "attempt_count", "next_attempt_at", "last_error", "response_status_code", "response_body_preview", "sent_at", "claimed_by", "claimed_at", "created_at", "updated_at"},
    "call_costs": {"id", "call_id", "campaign_id", "component", "provider", "model", "usage_unit", "usage_quantity", "unit_rate", "estimated_cost", "currency", "rate_source", "estimated", "dry_run", "created_at", "updated_at"},
    "outcome_metrics": {"id", "campaign_id", "metric_date", "total_dialed", "answered", "human_answered", "voicemail", "no_answer", "busy", "failed", "open_to_review", "qualified", "transferred", "callback", "dnc", "disqualified", "cost", "created_at", "updated_at"},
    "training_sources": {"id", "source_type", "source_uri", "title", "imported_at", "status", "metadata", "created_at"},
    "training_examples": {"id", "source_id", "call_id", "stage", "user_text", "ideal_response", "bad_response", "labels", "approved_by", "approved_at", "use_for", "created_at"},
    "eval_cases": {"id", "stage", "prospect_utterance", "expected_behavior", "must_include", "must_not_include", "expected_tool", "severity", "created_at"},
    "prompt_versions": {"id", "file_path", "sha", "created_at", "created_by", "change_reason", "qa_thresholds", "canary_status"},
    "human_review_items": {"id", "item_type", "payload", "status", "reviewer", "review_notes", "created_at", "reviewed_at"},
    "deployment_experiments": {"id", "experiment_name", "prompt_version_id", "traffic_percent", "status", "metrics", "started_at", "ended_at", "created_at"},
    "call_outcome_labels": {"id", "call_id", "campaign_id", "outcome", "sold", "issued", "transfer_quality_score", "agent_feedback", "labels", "created_at"},
    "rag_documents": {
        "id", "content", "embedding", "metadata", "source", "source_id", "source_type",
        "topic", "call_stage", "doc_type", "approved", "quality_score", "compliance_priority",
        "version", "created_at", "updated_at"
    },
    "telephony_provider_configs": {
        "id", "provider", "name", "status", "telnyx_connection_id", "telnyx_sip_trunk_name",
        "telnyx_phone_numbers", "livekit_url", "livekit_sip_outbound_trunk_id",
        "livekit_sip_inbound_trunk_id", "livekit_dispatch_rule_id", "room_name_template",
        "metadata", "created_at", "updated_at"
    },
    "outbound_campaigns": {
        "id", "name", "description", "status", "campaign_type", "provider_config_id",
        "prompt_name", "max_concurrent_calls", "daily_call_cap", "calls_started_today",
        "timezone", "calling_window_start", "calling_window_end", "allowed_days",
        "retry_policy", "transfer_phone_number", "caller_id", "compliance_mode",
        "dnc_scrub_required", "require_live_mode", "metadata", "created_at", "updated_at",
        "started_at", "paused_at", "stopped_at"
    },
    "campaign_leads": {
        "id", "campaign_id", "first_name", "last_name", "phone_number", "state",
        "timezone", "status", "priority", "attempt_count", "max_attempts", "next_attempt_at",
        "last_attempt_at", "outcome", "suppression_reason", "metadata", "created_at", "updated_at"
    },
    "call_attempts": {
        "id", "campaign_id", "lead_id", "provider_config_id", "status", "phone_number_redacted",
        "phone_number_hash", "livekit_room_name", "livekit_participant_id", "livekit_sip_call_id",
        "provider_call_id", "started_at", "answered_at", "ended_at", "duration_seconds",
        "outcome", "failure_reason", "transfer_consent", "transfer_attempted", "transfer_successful",
        "post_call_export_path", "metadata", "created_at", "updated_at"
    },
    "live_call_sessions": {
        "id", "campaign_id", "lead_id", "attempt_id", "call_id", "status", "current_stage",
        "latest_transcript", "compliance_warnings", "livekit_room_name", "participant_identity",
        "started_at", "updated_at", "ended_at", "outcome", "metadata"
    },
    "campaign_control_events": {
        "id", "campaign_id", "event_type", "operator", "reason", "previous_status",
        "new_status", "metadata", "created_at"
    },
    "dids": {
        "id", "provider", "phone_number", "status", "source", "verified_for_provider",
        "stir_shaken_attestation", "daily_cap", "hourly_cap", "calls_today", "calls_this_hour",
        "last_used_at", "cooldown_until", "spam_label_status", "complaint_count", "dnc_count",
        "answer_rate", "transfer_rate", "metadata", "created_at", "updated_at"
    }
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
                # Connect directly using DATABASE_ADMIN_URL to avoid running migrations via PgBouncer
                admin_dsn = os.environ.get("DATABASE_ADMIN_URL") or self._dsn
                if admin_dsn:
                    logger.info("Running migrations via direct admin connection to bypass PgBouncer...")
                    conn = await asyncpg.connect(admin_dsn)
                    try:
                        from storage.migrations import run_migrations
                        await run_migrations(conn)
                    finally:
                        await conn.close()
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
                or k == "cooldown_until"
                or k == "retry_after"
            ):
                try:
                    mapped[k] = datetime.fromisoformat(v.replace("Z", "+00:00"))
                except ValueError:
                    pass
            elif isinstance(v, str) and k == "metric_date":
                from datetime import date
                try:
                    mapped[k] = date.fromisoformat(v)
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
        if table in ("leads", "consent_records"):
            payload = row_dict.get("payload")
            if isinstance(payload, dict):
                payload["id"] = row_dict.get("id")
                # Merge flat columns into payload so direct SQL updates are reflected
                for col in TABLE_COLUMNS[table]:
                    if col in row_dict and col != "payload":
                        payload[col] = row_dict[col]
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
