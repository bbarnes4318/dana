# Hyperstack L40/L40S Production Deployment Runbook

This guide details the exact sequence of commands and checks required to deploy the Dana Voice Stack to a dedicated Hyperstack GPU instance (or equivalent dedicated GPU host).

---

## Server Environment Context
* **GPU**: 1x NVIDIA L40 / L40S (48GB VRAM)
* **OS**: Ubuntu 22.04 LTS
* **Driver/CUDA**: R550 CUDA 12.4 with Docker Container Toolkit
* **Inbound Ports**: TCP 22 (SSH) only. Command center dashboard is accessed via secure SSH port forwarding.

---

## Deployment Steps

### 1. SSH into Server
Log into your dedicated GPU server from your local computer:
```bash
ssh root@SERVER_IP
```

### 2. Verify GPU Capabilities
Ensure the NVIDIA drivers and CUDA runtime are functional:
```bash
nvidia-smi
```
*Expected output: Row displaying "NVIDIA L40" or "NVIDIA L40S" and CUDA version 12.x.*

### 3. Verify Docker & Compose Installation
Confirm Docker Engine and the Compose plugin are installed and accessible:
```bash
docker --version
docker compose version || docker compose --version
```

### 4. Verify Docker GPU Container Access
Audit whether Docker can execute containers with GPU reservations:
```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```
*If this fails with "unknown runtime 'nvidia'", install the nvidia-container-toolkit.*

### 5. Install System Utilities
Ensure essential system administration packages are present:
```bash
apt update
apt install -y git curl nano htop nvtop jq unzip ca-certificates gnupg lsb-release ufw
```

### 6. Configure Host Firewall (UFW)
Secure the server by denying all unsolicited inbound traffic except SSH (port 22):
```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable
ufw status
```

### 7. Clone Repository to `/opt/dana`
Clone the official Dana repository and change directories:
```bash
mkdir -p /opt
cd /opt
git clone https://github.com/bbarnes4318/dana.git
cd /opt/dana
```

### 8. Create Environment Configuration
Copy the template and edit it with your real credentials:
```bash
cp .env.production.example .env
nano .env
```
*Fill in the REQUIRED production variables in Section 1 (LiveKit Cloud keys, Telnyx Connection ID, Postgres credentials, HF_TOKEN, etc.).*

### 9. Execute Deployment Doctor
Run the validator script to audit environment variables and host configurations locally (no external API calls):
```bash
python -m ops.deployment_doctor --env-file .env
```
*Ensure `PRODUCTION_ENV_VALID=TRUE` and `READY_TO_START_SERVICES=TRUE` are reported in the final status block.*

### 10. Generate Secure Random Secrets (Optional)
If you need to generate strong database or system passwords, run:
```bash
openssl rand -hex 32
```

### 11. Launch Storage Infrastructure
Start the database (PostgreSQL), PgBouncer connection pooler, and Redis containers:
```bash
docker compose --env-file .env up -d postgres pgbouncer redis
docker compose ps
```
*Verify that postgres, pgbouncer, and redis containers display status 'healthy'.*

### 12. Build Voice Agent Image
Build the local voice agent container from source:
```bash
docker compose --env-file .env build voice-agent
```

### 13. Execute Database Migrations
Apply all schema tables and indexes using the direct admin connection:
```bash
docker compose --env-file .env run --rm --no-deps voice-agent python -m storage.migrations
```

### 14. Start vLLM Model Server
Start the private LLM engine container. This downloads the weights and warms up the GPU memory:
```bash
docker compose --env-file .env up -d vllm-server
docker compose logs -f --tail=100 vllm-server
```
*Note: Weight download and warmup can take 5–10 minutes depending on network bandwidth.*

### 15. Verify vLLM Engine Health
Query the internal health check endpoint of the vLLM engine:
```bash
curl http://localhost:8000/health
```
*Expected response: "ok" (HTTP 200).*

### 16. Start Voice Agent Service
With the backend storage and model engines running, start the orchestration agent:
```bash
docker compose --env-file .env up -d voice-agent
docker compose logs -f --tail=100 voice-agent
```

### 17. Execute Pre-flight Readiness Tests
Verify system integration, health indicators, and canary routing:
```bash
docker compose --env-file .env exec voice-agent python -m ops.healthcheck
docker compose --env-file .env exec voice-agent python -m ops.readiness --benchmark-ready true --eval-ready true --canary-ready true
docker compose --env-file .env exec voice-agent python -m ops.canary
```
*All three commands must return success statuses (exit code 0).*

### 18. Execute Local Voice Stack Benchmark
Run the baseline voice platform benchmark to score pipeline latency:
```bash
docker compose --env-file .env exec voice-agent python -m benchmarks.voice_platform_benchmark.leaderboard
```

### 19. Audit Quality Gate Criteria
Ensure the benchmark outputs satisfy compliance limits (turn response latency < 900ms, LLM first token < 250ms):
```bash
docker compose --env-file .env exec voice-agent python -m qa.platform_quality_gate --benchmark-file data/benchmarks/leaderboard.json --provider dana_local
```

### 20. Open Command Center securely through SSH tunnel only
Because inbound firewall ports are closed, access the dashboard by running the console bound to localhost on the server, and tunnel it from your local machine.

**Step 20a: Run console server inside container (Server-side):**
```bash
docker compose --env-file .env exec voice-agent python - <<'PY'
from ops.web_console import TrainingWebConsoleConfig, TrainingWebConsoleServer

config = TrainingWebConsoleConfig(
    host="127.0.0.1",
    port=8787,
    allow_remote=False,
    debug=False,
)

server = TrainingWebConsoleServer(config)
print("Dana Command Center running on 127.0.0.1:8787")
server.serve_forever()
PY
```

**Step 20b: Create SSH Tunnel (On your LOCAL machine):**
```bash
ssh -L 8787:127.0.0.1:8787 root@SERVER_IP
```

**Step 20c: Load Dashboard (On your LOCAL machine):**
Open your web browser and navigate to:
```
http://127.0.0.1:8787
```

---

## 21. Controlled Live-Call Smoke Test Rules
Live telephony calls are highly restricted. Follow these rules to place a controlled test call:

1. **Verify All Gates Pass First**: Never place a telephony call unless healthcheck, readiness checks, canary runs, and quality gates pass.
2. **Execute Dry-Run First**: Validate SIP connections, suppression lists, and credentials without dialing a phone:
   ```bash
   docker compose --env-file .env exec voice-agent python -m ops.live_call_smoke_test --to +1XXXXXXXXXX --from +1XXXXXXXXXX --dry-run
   ```
3. **Configure Controlled Test Environment Flag**: Add `DANA_CONTROLLED_LIVE_TEST=true` to your `.env` to disable campaign autodialing.
4. **Place a Single Controlled Test Call**: Dial a real phone number:
   ```bash
   docker compose --env-file .env exec voice-agent python -m ops.live_call_smoke_test --to +1XXXXXXXXXX --from +1XXXXXXXXXX
   ```
5. **Restore Safety Default**: Immediately edit your `.env` file to set `DANA_CONTROLLED_LIVE_TEST=false` to prevent any unauthorized calling attempts.
6. **Do NOT Start Dialer Campaigns**: Do not trigger dialer ticks or campaign runs during initial staging.
