#!/usr/bin/env bash
# hyperstack_start_test.sh — Start the voice stack on Hyperstack GPU server
# Runs on the Hyperstack server from /opt/dana
# WARNING: This starts a PAID GPU workload.
set -euo pipefail

DANA_ROOT="/opt/dana"

# ── Colors ──────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo -e "${RED}  ⚠  WARNING — PAID GPU WORKLOAD  ⚠${NC}"
echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo ""
echo "  This will start the vLLM and Voice Agent containers"
echo "  on the Hyperstack GPU server."
echo ""
echo -e "  ${YELLOW}The GPU VM is billed while running.${NC}"
echo -e "  ${YELLOW}Make sure you intend to start this workload.${NC}"
echo ""

# ── Confirmation gate ───────────────────────────────────────────────
if [ "${DANA_CONFIRM_START:-}" != "yes" ]; then
    echo -n "  Type 'yes' to continue: "
    read -r CONFIRM
    if [ "${CONFIRM}" != "yes" ]; then
        echo ""
        echo "  Aborted. No services were started."
        exit 1
    fi
fi
echo ""

# ── Pre-flight: .env must exist ─────────────────────────────────────
if [ ! -f "${DANA_ROOT}/.env" ]; then
    echo -e "${RED}✘ .env not found at ${DANA_ROOT}/.env${NC}"
    echo "  Copy from .env.production.example, fill in real values, then retry."
    exit 1
fi

# ── Start services ──────────────────────────────────────────────────
echo -e "${CYAN}Starting vllm-server and voice-agent ...${NC}"
echo ""

docker compose \
    -f "${DANA_ROOT}/docker-compose.yaml" \
    --env-file "${DANA_ROOT}/.env" \
    up -d --build vllm-server voice-agent

echo ""
echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Services started${NC}"
echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo ""

docker compose -f "${DANA_ROOT}/docker-compose.yaml" ps

echo ""
echo -e "${CYAN}── Monitor & Control ─────────────────────────────${NC}"
echo ""
echo "  View voice-agent logs:"
echo "    ./scripts/hyperstack_logs.sh voice-agent"
echo ""
echo "  View vllm-server logs:"
echo "    ./scripts/hyperstack_logs.sh vllm-server"
echo ""
echo "  Stop all services:"
echo "    ./scripts/hyperstack_stop.sh"
echo ""
