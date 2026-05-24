#!/usr/bin/env bash
# hyperstack_stop.sh — Stop all voice stack services on Hyperstack GPU server
# Runs on the Hyperstack server from /opt/dana
set -euo pipefail

DANA_ROOT="/opt/dana"

# ── Colors ──────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  Stopping all services ...${NC}"
echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo ""

docker compose -f "${DANA_ROOT}/docker-compose.yaml" down

echo ""
echo -e "${GREEN}Services stopped.${NC}"
echo ""

echo -e "${CYAN}── Compose status ────────────────────────────────${NC}"
docker compose -f "${DANA_ROOT}/docker-compose.yaml" ps

echo ""
echo -e "${CYAN}── All Docker containers ─────────────────────────${NC}"
docker ps

echo ""
echo -e "${YELLOW}══════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}  REMINDER: The Hyperstack VM is still running${NC}"
echo -e "${YELLOW}  and billing continues until the VM is stopped${NC}"
echo -e "${YELLOW}  or deleted in the Hyperstack dashboard.${NC}"
echo -e "${YELLOW}══════════════════════════════════════════════════${NC}"
echo ""
