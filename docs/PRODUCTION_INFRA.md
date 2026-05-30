# Production Infrastructure Guide

This document describes the high-performance dedicated server infrastructure design for running the voice agent stack.

---

## Architecture Design

The production stack is designed for **maximum throughput, ultra-low latency, and self-hosted operations**. It is optimized to run on dedicated bare-metal servers or VMs (such as on Hyperstack or self-hosted NVMe infrastructure) and **does not require AWS, Google Cloud, or Azure**.

```
                           ┌───────────────────────────┐
                           │      LiveKit Cloud        │
                           └───────────────────────────┘
                                         ▲
                                 WebRTC  │ (Outbound Media)
                                         ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Dedicated Server Host (Hyperstack / Bare-metal)                        │
│                                                                        │
│  ┌───────────────────────┐            ┌─────────────────────────────┐  │
│  │   voice-agent         │ ◄──HTTP──► │   vLLM-server               │  │
│  │   (STT/TTS/Orch)      │            │   (Llama-3.1-8B-Instruct)   │  │
│  └──────────┬──────┬─────┘            └─────────────────────────────┘  │
│             │      │                                                   │
│             │      └───────────Redis Protocol (port 6379)────────┐     │
│             │                                                   │     │
│             ▼                                                   ▼     │
│       (port 6432)                                            redis    │
│    ┌──────────────┐                                       (Hot State)  │
│    │  pgbouncer   │                                                   │
│    └──────┬───────┘                                                   │
│           │ (Transaction Pool, port 5432)                              │
│           ▼                                                           │
│    ┌──────────────┐                                                   │
│    │  postgres    │ ◄─── Migrations / CLI Admin (direct port 5432) ───┘  │
│    │ (Durable DB) │                                                   │
│    └──────────────┘                                                   │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Core Infrastructure Rules

### 1. Dedicated Hardware & Local NVMe Storage
*   **No Cloud Hyperscalers**: Do not deploy this stack to AWS, GCP, or Azure unless dedicated hosts and guaranteed resource allocations are secured.
*   **Dedicated GPUs**: Whisper STT, Kokoro TTS, and vLLM require dedicated GPU allocation (NVIDIA L40 or L40S preferred) with GPU passthrough configured.
*   **NVMe Local Drives**: All database storage and local model caches should use fast NVMe drives to prevent disk wait states.

### 2. Ephemeral vs. Durable State
*   **PostgreSQL (Durable Source of Truth)**: Used for persistent logs and entities like campaigns, leads, calls, callbacks, wrong numbers, and DNC registry.
*   **Redis (Ephemeral Hot State)**: Used for real-time campaign pacing, concurrency locks, rate limiting, and active call counters. If Redis dies, the state is reconstructed safely from PostgreSQL.
*   **No Streaming Data in Postgres**: Never write audio streams, packet streams, or per-frame VAD ticks to the database. Keep database schema tight and restricted to call metrics and states.

### 3. PgBouncer Connection Pooling
*   **Pool Mode**: PgBouncer must be configured in `transaction` mode.
*   **Port Layout**: 
    *   Runtime connections: `DATABASE_URL=postgresql://user:password@pgbouncer:6432/dana`
    *   Administrative / Schema Migration connections: `DATABASE_ADMIN_URL=postgresql://user:password@postgres:5432/dana`
*   **CRITICAL**: Do **not** run database migrations through PgBouncer. Transaction pooling is incompatible with transactional DDL operations. Migrations must connect directly to PostgreSQL on port `5432`.

### 4. Non-Blocking Write-Behind Queue
*   **Concept**: Conversations must never block on database writes. Every millisecond of database latency will degrade real-time voice latency.
*   **Write-Behind**: Turn logs, tool events, cost logs, and outcome metrics are pushed to an in-memory queue (`WriteBehindQueue`) and flushed in batches asynchronously.
*   **Immediate Writes**: Critical operations (lead-locking, DNC registry additions, consent checks, and final callbacks) are executed synchronously to ensure real-time business compliance.

---

## Staging & Single-Node Docker postgres
Running PostgreSQL in a Docker container (as configured in the default `docker-compose.yaml`) is acceptable for **development, staging, and single-node production runs** ONLY under the following conditions:
1.  **Named Volumes**: Ensure the `pg-data` volume is backed by host NVMe paths.
2.  **Daily Backups**: Implement the daily encrypted backups script (`infra/backup/backup.sh`) and send them offsite.
3.  **Active Monitoring**: Monitor container memory usage and CPU limits to ensure Postgres is not starved of resources.
