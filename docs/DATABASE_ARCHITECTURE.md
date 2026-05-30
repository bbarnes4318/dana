# Database Architecture Guide

This document outlines the database and state persistence design for the voice agent stack, explaining the distinct roles of PostgreSQL, PgBouncer, Redis, and the Write-Behind persistence queue.

---

## 1. Role Division

Our architecture divides state management into three layers to ensure ultra-low voice response latency and high business compliance:

| Layer / Technology | Mode | Lifespan | Primary Purpose | Examples |
|---|---|---|---|---|
| **PostgreSQL** | Durable | Permanent | Transactional Source of Truth | Campaigns, Leads, DNC registry, Callbacks |
| **PgBouncer** | Connection Pooler | Ephemeral | Protects Postgres from connection spikes | Client connection limit management |
| **Redis** | In-Memory | Transient | Hot state cache & Rate Limiting | Active call counts, Campaign pacing, Locks |
| **Write-Behind** | Memory Queue | Transient | Asynchronous batch saves for non-critical writes | Call turns, Tool event logs, Cost logs |

---

## 2. PostgreSQL & Schema Management

PostgreSQL is our durable database. The database contains tables for:
*   `campaigns` and `leads` (the dialing targets)
*   `calls`, `call_turns`, `call_events`, and `tool_events` (audit logs of conversations)
*   `transfers`, `callbacks`, and `dnc_requests` (business compliance outcomes)
*   `latency_metrics`, `call_costs`, and `outcome_metrics` (analytics)

### No Streaming Data in Postgres
To avoid database bloat and performance degradation:
*   **Do NOT store** raw audio frames, TTS audio packets, or token stream chunks.
*   **Do NOT store** real-time VAD events or sub-100ms turn state details.

---

## 3. PgBouncer Connection Pooling

In a production dialing campaign, hundreds of concurrent voice channels may open and close connections rapidly. Directly opening connections to PostgreSQL can quickly exhaust database resource limits and cause CPU spikes.

### Transaction Pooling
We place PgBouncer in front of PostgreSQL using **Transaction Pooling (`pool_mode = transaction`)**.
*   **Application Connections**: The voice agent runtime connects to PgBouncer:
    `DATABASE_URL=postgresql://user:password@pgbouncer:6432/dana`
*   **Direct Administrative / Migration Connections**: Transaction pooling does **not** support temporary tables, prepared statements, or transactional DDL (Data Definition Language) commands. Therefore, migrations and schema runners must connect directly to PostgreSQL, bypassing PgBouncer:
    `DATABASE_ADMIN_URL=postgresql://user:password@postgres:5432/dana`

---

## 4. Ephemeral Redis Hot State

Redis functions as our ultra-fast hot-state layer. 

### Pacing and Concurrency Checks
Before the campaign runner attempts to lock a lead or reserve resources, it checks campaign pacing using Redis:
1.  **Concurrency Limits**: Checks active call counts for the campaign.
2.  **Rate Limits (CPM)**: Checks calls started in the last 60 seconds (using rolling window timestamps).

If Redis pacing blocks the call, the dialer sleeps without touching the lead or the caller ID pool.

### Resilience and Recovery
*   Redis is **not** a durable source of truth.
*   If Redis crashes, the application falls back to a degraded, single-worker in-memory store (`InMemoryHotStateStore`).
*   Active states and pacing counters can be rebuilt dynamically from PostgreSQL database queries once the connection is restored.

---

## 5. Non-Blocking Write-Behind Queue

Conversations require sub-second turn latency. The voice agent must never await database disk writes during a live call turn.

### Async Persistence Queue
Non-critical writes are pushed to the `WriteBehindQueue` and returned immediately:
*   `call_turns` (storing what was said)
*   `tool_events` (storing log results of API calls)
*   `latency_metrics` and `call_costs`
*   `outcome_metrics` (daily rollups)

The queue pools these writes and flushes them to the database in batches every `250ms` (configurable).

### Critical Synchronous Writes
Critical database operations are **never** put in the write-behind queue and must be awaited synchronously to prevent race conditions:
*   Lead locking (`SELECT ... FOR UPDATE SKIP LOCKED`)
*   DNC registry additions (to prevent dialer from calling a lead who requested DNC in a concurrent run)
*   Consent validation checks
*   Callback scheduling
