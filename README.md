# Dana — Sovereign Outbound Voice AI Agent

Dana is an ultra-low-latency, self-hosted outbound voice AI agent orchestrator designed to run entirely on private hardware (L40/L40S GPU VMs). It integrates local Speech-to-Text (Whisper), local Text-to-Speech (Kokoro ONNX), local LLM inference (vLLM), and LiveKit WebRTC for real-time conversational voice calls.

---

## Production Database & Fast Runtime Architecture

To achieve human-like conversation latency, the production environment is optimized for dedicated bare-metal or VM hosts using local NVMe storage and private networking. **No AWS, Google Cloud, or Azure services are required.**

### Key Architectural Guidelines:
*   **PostgreSQL is the Durable Source of Truth**: All campaigns, leads, calls, wrong numbers, DNC requests, and callbacks are persistently recorded in PostgreSQL.
*   **PgBouncer Connection Pooling**: Required for production runtimes. The agent application connects via PgBouncer using Transaction Pooling on port `6432`.
*   **Migrations Bypass PgBouncer**: Database schema migrations must connect directly to PostgreSQL on port `5432` (`DATABASE_ADMIN_URL`), as transaction pooling is incompatible with transactional DDL schema updates.
*   **Redis Hot State**: Active calls, pacing window counters, rate limits, and short-lived locks reside in an ephemeral Redis cache. If Redis restarts, state is safely rebuilt from PostgreSQL.
*   **Non-Blocking Write-Behind Queue**: Conversations never block on database writes. Turn logs, tool events, cost logs, and daily analytics are persisted asynchronously in batches via `WriteBehindQueue`. Critical operations (DNC updates, consent checks, lead locks) remain synchronous.
*   **Docker PostgreSQL Usage**: Running Postgres inside Docker (as configured in the default compose file) is acceptable for development, staging, or single-node production runs *only* when combined with host NVMe volumes and daily encrypted backups.

---

## Quick Start

### 1. Setup Environment
Copy the production environment example:
```bash
cp .env.production.example .env
# Edit .env with your secrets (HF_TOKEN, LIVEKIT_URL, etc.)
```

### 2. Start Services
Launch the Postgres, PgBouncer, Redis, vLLM, and Voice Agent containers:
```bash
docker compose --env-file .env up -d --build postgres pgbouncer redis vllm-server voice-agent
```

### 3. Run Schema Migrations
Apply database updates directly to Postgres:
```bash
DATABASE_ADMIN_URL=postgresql://dana_user:dana_secure_pass@localhost:5432/dana \
  python -m storage.migrations
```

---

## Testing & CI/CD

To prevent performance degradation or environment pollution on local operator and developer workstations, **running tests locally is strictly discouraged**. 

A complete automated testing pipeline is configured in `.github/workflows/ci.yml`. All unit, integration, and platform tests are run in isolated containerized GitHub runners on every pull request and push to the `main` branch.

---

For detailed setup, monitoring, backups, and restores, read the following files in the `docs/` folder:
*   [docs/HYPERSTACK_L40_DEPLOYMENT_RUNBOOK.md](file:///c:/Users/jimbo/OneDrive/Desktop/ultimate-voice/docs/HYPERSTACK_L40_DEPLOYMENT_RUNBOOK.md) - **Hyperstack L40/L40S Deployment Runbook** (Required Reading)
*   [docs/PRODUCTION_INFRA.md](file:///c:/Users/jimbo/OneDrive/Desktop/ultimate-voice/docs/PRODUCTION_INFRA.md) - **Production Infrastructure & Environment Checklist**
*   [docs/DATABASE_ARCHITECTURE.md](file:///c:/Users/jimbo/OneDrive/Desktop/ultimate-voice/docs/DATABASE_ARCHITECTURE.md)
*   [docs/OPERATIONS_RUNBOOK.md](file:///c:/Users/jimbo/OneDrive/Desktop/ultimate-voice/docs/OPERATIONS_RUNBOOK.md)
*   [docs/continuous_training_runbook.md](file:///C:/Users/jimbo/.gemini/antigravity/worktrees/ultimate-voice/implement-canary-rollout-system/docs/continuous_training_runbook.md)
*   [docs/dana_training_safety_gates.md](file:///C:/Users/jimbo/.gemini/antigravity/worktrees/ultimate-voice/implement-canary-rollout-system/docs/dana_training_safety_gates.md)
*   [docs/fine_tuning_operating_procedure.md](file:///C:/Users/jimbo/.gemini/antigravity/worktrees/ultimate-voice/implement-canary-rollout-system/docs/fine_tuning_operating_procedure.md)
*   [docs/prompt_canary_operating_procedure.md](file:///C:/Users/jimbo/.gemini/antigravity/worktrees/ultimate-voice/implement-canary-rollout-system/docs/prompt_canary_operating_procedure.md)
*   [docs/training_operations_console.md](file:///C:/Users/jimbo/.gemini/antigravity/worktrees/ultimate-voice/dana-training-ops-console/docs/training_operations_console.md)
*   [docs/training_web_console_operating_procedure.md](file:///C:/Users/jimbo/.gemini/antigravity/worktrees/ultimate-voice/dana-training-ops-console/docs/training_web_console_operating_procedure.md)

---

## Local Deployment Doctor

Before starting services or placing test calls on a production host, run the **Deployment Doctor** to audit your local system drivers, Docker versions, directory permissions, and environment variables configuration:

```bash
python -m ops.deployment_doctor --env-file .env
```

Or get output formatted strictly as JSON:
```bash
python -m ops.deployment_doctor --env-file .env --json
```

The doctor validates all required production parameters without making any external API or provider calls. A single failure will block the `READY_FOR_LIVE_CALL_TEST` status, preventing unverified production execution.
