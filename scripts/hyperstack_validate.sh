#!/usr/bin/env bash
# hyperstack_validate.sh — Validate Hyperstack GPU server readiness
# Runs on the Hyperstack server from /opt/dana
# Does NOT start any services.
set -euo pipefail

DANA_ROOT="/opt/dana"

# ── Colors ──────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

pass() { echo -e "  ${GREEN}✔${NC} $1"; }
fail() { echo -e "  ${RED}✘ $1${NC}"; echo -e "\n${RED}VALIDATION FAILED${NC}"; exit 1; }
warn() { echo -e "  ${YELLOW}⚠ $1${NC}"; }

echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  Hyperstack GPU Server — Validation${NC}"
echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo ""
echo "  This script runs on the Hyperstack server."
echo "  It does NOT start any services."
echo ""

CHECKS_PASSED=0
CHECKS_WARNED=0

# ── 1. nvidia-smi ───────────────────────────────────────────────────
echo -e "${CYAN}[1/7] Checking nvidia-smi ...${NC}"
if ! command -v nvidia-smi &>/dev/null; then
    fail "nvidia-smi not found. NVIDIA drivers are not installed or not on PATH."
fi
if ! nvidia-smi &>/dev/null; then
    fail "nvidia-smi is installed but returned an error. GPU may not be available."
fi
GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)
pass "nvidia-smi OK — GPU: ${GPU_NAME}"
CHECKS_PASSED=$((CHECKS_PASSED + 1))

# ── 2. Docker ───────────────────────────────────────────────────────
echo -e "${CYAN}[2/7] Checking Docker ...${NC}"
if ! command -v docker &>/dev/null; then
    fail "Docker is not installed. Install Docker Engine first: https://docs.docker.com/engine/install/"
fi
if ! docker info &>/dev/null; then
    fail "Docker is installed but the daemon is not running or current user lacks permissions."
fi
DOCKER_VER=$(docker --version)
pass "Docker OK — ${DOCKER_VER}"
CHECKS_PASSED=$((CHECKS_PASSED + 1))

# ── 3. Docker Compose ──────────────────────────────────────────────
echo -e "${CYAN}[3/7] Checking Docker Compose ...${NC}"
if ! docker compose version &>/dev/null; then
    fail "docker compose plugin not found. Install it: https://docs.docker.com/compose/install/"
fi
COMPOSE_VER=$(docker compose version --short)
pass "Docker Compose OK — v${COMPOSE_VER}"
CHECKS_PASSED=$((CHECKS_PASSED + 1))

# ── 4. docker-compose.yaml ─────────────────────────────────────────
echo -e "${CYAN}[4/7] Checking docker-compose.yaml ...${NC}"
if [ ! -f "${DANA_ROOT}/docker-compose.yaml" ]; then
    fail "docker-compose.yaml not found at ${DANA_ROOT}/docker-compose.yaml"
fi
pass "docker-compose.yaml exists"
CHECKS_PASSED=$((CHECKS_PASSED + 1))

# ── 5. .env.production.example ──────────────────────────────────────
echo -e "${CYAN}[5/7] Checking .env.production.example ...${NC}"
# Accept either name that may exist in the repo
if [ -f "${DANA_ROOT}/.env.production.example" ]; then
    pass ".env.production.example exists"
elif [ -f "${DANA_ROOT}/.env.example" ]; then
    pass ".env.example exists (used as production example)"
else
    fail ".env.production.example not found at ${DANA_ROOT}/.env.production.example"
fi
CHECKS_PASSED=$((CHECKS_PASSED + 1))

# ── 6. .env ─────────────────────────────────────────────────────────
echo -e "${CYAN}[6/7] Checking .env ...${NC}"
if [ ! -f "${DANA_ROOT}/.env" ]; then
    warn ".env does not exist. Copy from .env.production.example and fill in real values before starting."
    CHECKS_WARNED=$((CHECKS_WARNED + 1))
else
    pass ".env exists"
    CHECKS_PASSED=$((CHECKS_PASSED + 1))
fi

# ── 7. Compose config validation ───────────────────────────────────
echo -e "${CYAN}[7/7] Validating Compose configuration ...${NC}"
if ! docker compose -f "${DANA_ROOT}/docker-compose.yaml" config --quiet 2>/dev/null; then
    fail "docker compose config failed — check docker-compose.yaml for syntax errors."
fi
pass "docker compose config is valid"
CHECKS_PASSED=$((CHECKS_PASSED + 1))

# ── Extra: vLLM public port warning ────────────────────────────────
echo ""
if grep -qE '^\s*-\s*"0\.0\.0\.0:8000:8000"' "${DANA_ROOT}/docker-compose.yaml" 2>/dev/null \
   || grep -qE '^\s*-\s*"8000:8000"' "${DANA_ROOT}/docker-compose.yaml" 2>/dev/null; then
    warn "vLLM port 8000 appears publicly mapped (0.0.0.0:8000)."
    warn "This exposes the LLM API to the internet. Consider binding to 127.0.0.1:8000:8000"
    warn "or removing the ports mapping entirely (other containers reach vLLM via the Docker network)."
    CHECKS_WARNED=$((CHECKS_WARNED + 1))
fi

# ── Summary ─────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  Validation Summary${NC}"
echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo -e "  ${GREEN}Passed : ${CHECKS_PASSED}${NC}"
if [ "${CHECKS_WARNED}" -gt 0 ]; then
    echo -e "  ${YELLOW}Warnings: ${CHECKS_WARNED}${NC}"
fi
echo ""
echo "  This script did NOT start any services."
echo "  To start the stack:  ./scripts/hyperstack_start_test.sh"
echo ""
