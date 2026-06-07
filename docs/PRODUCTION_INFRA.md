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

---

## Production Environment Checklist

Before deploying, ensure all variables in `.env` are configured correctly. The list below outlines categories, sources, and safety default rules.

### 1. Required Variables by Source

#### A. LiveKit Cloud (Provider: LiveKit)
*   `LIVEKIT_URL`: WebRTC gateway url (e.g. `wss://<project>.livekit.cloud`).
*   `LIVEKIT_API_KEY`: API authentication key.
*   `LIVEKIT_API_SECRET`: API authentication secret (sensitive).
*   `LIVEKIT_SIP_OUTBOUND_TRUNK_ID`: The unique ID of the SIP trunk registered in LiveKit dashboard for outbound calls.

#### B. Telephony Trunk (Provider: Telnyx)
*   `TELNYX_API_KEY`: Telephony management credential (sensitive).
*   `TELNYX_CONNECTION_ID`: Connection ID of the Telnyx outbound SIP profile.
*   `TELNYX_OUTBOUND_NUMBER`: Active phone number to place calls from.

#### C. Model Engine (Provider: HuggingFace & Local vLLM)
*   `HF_TOKEN`: HuggingFace token for gated repository access (sensitive).
*   `VLLM_BASE_URL`: Model API URL (defaults to `http://vllm-server:8000/v1`).
*   `VLLM_MODEL`: ID of model to execute.

#### D. Database & Cache (Generated Locally)
*   `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB`: Persistent database credentials.
*   `DATABASE_URL`: Connection string pointing to PgBouncer connection pooler on port `6432`.
*   `DATABASE_ADMIN_URL`: Direct PostgreSQL connection string on port `5432` for running migrations.
*   `REDIS_URL`: Cache instance connection string (defaults to `redis://redis:6379/0`).

### 2. Critical Safety Flags (Must remain False by default)
*   `DANA_ALLOW_MOCK_TTS=false`: Setting this to `true` will crash production readiness checks. Real Kokoro ONNX synthesis or a valid cloud fallback must be active.
*   `DANA_CONTROLLED_LIVE_TEST=false`: Set to `true` ONLY when running single controlled outbound smoke calls. Bulk campaigns are disabled while this is enabled.

---

## Staging & Deployment Checks

To verify your configuration before starting services:

1.  **Run the Deployment Doctor**:
    Execute the doctor script to check local drivers, Docker, directories, and variables:
    ```bash
    python -m ops.deployment_doctor --env-file .env
    ```
2.  **Understand Production Readiness Gates**:
    To achieve `PRODUCTION_READY` status in the console, the platform requires:
    *   Health checks pass (system metrics and capacity limits OK).
    *   Readiness checks pass (LiveKit, Telnyx, database, Redis, vLLM pre-warmed).
    *   Offline canary runs succeed.
    *   Voice stack benchmark run completes.
    *   Compliance and quality gates (SLOs) are satisfied.

For a comprehensive step-by-step installation guide on dedicated Hyperstack GPU VMs, refer to the [docs/HYPERSTACK_L40_DEPLOYMENT_RUNBOOK.md](file:///c:/Users/jimbo/OneDrive/Desktop/ultimate-voice/docs/HYPERSTACK_L40_DEPLOYMENT_RUNBOOK.md).

