#!/usr/bin/env bash
# hyperstack_logs.sh — Tail logs for a voice stack service
# Runs on the Hyperstack server from /opt/dana
# Does NOT start any services.
set -euo pipefail

DANA_ROOT="/opt/dana"

# ── Colors ──────────────────────────────────────────────────────────
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

VALID_SERVICES=("voice-agent" "vllm-server")
SERVICE="${1:-voice-agent}"

# ── Validate service name ───────────────────────────────────────────
is_valid=false
for s in "${VALID_SERVICES[@]}"; do
    if [ "${SERVICE}" = "${s}" ]; then
        is_valid=true
        break
    fi
done

if [ "${is_valid}" = false ]; then
    echo -e "${RED}Error: Unknown service '${SERVICE}'${NC}"
    echo ""
    echo "Usage: $0 [service-name]"
    echo ""
    echo "Supported services:"
    for s in "${VALID_SERVICES[@]}"; do
        echo "  - ${s}"
    done
    echo ""
    echo "Default: voice-agent"
    exit 1
fi

# ── Tail logs ───────────────────────────────────────────────────────
echo -e "${CYAN}Tailing logs for ${SERVICE} (last 100 lines, following) ...${NC}"
echo -e "${CYAN}Press Ctrl+C to stop.${NC}"
echo ""

docker compose -f "${DANA_ROOT}/docker-compose.yaml" logs -f --tail=100 "${SERVICE}"
