#!/usr/bin/env bash
# =============================================================================
# ssh_bootstrap_command.sh — SSH into Hyperstack VM and run bootstrap
#
# Runs on the LOCAL MACHINE. Copies bootstrap_server.sh to the VM and
# executes it. Never prints key content.
#
# Usage:
#   bash ssh_bootstrap_command.sh <VM_PUBLIC_IP>
#   bash ssh_bootstrap_command.sh                  # reads IP from last_vm.json
#   bash ssh_bootstrap_command.sh --ssh-only <IP>  # just open an SSH session
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOOTSTRAP_SCRIPT="${SCRIPT_DIR}/bootstrap_server.sh"
LAST_VM_JSON="${SCRIPT_DIR}/last_vm.json"
SSH_USER="ubuntu"

# ---------------------------------------------------------------------------
# Find SSH key
# ---------------------------------------------------------------------------

find_ssh_key() {
    if [[ -n "${HYPERSTACK_SSH_PRIVATE_KEY_PATH:-}" ]] && [[ -f "${HYPERSTACK_SSH_PRIVATE_KEY_PATH}" ]]; then
        echo "${HYPERSTACK_SSH_PRIVATE_KEY_PATH}"
        return
    fi

    if [[ -f /tmp/dana_hyperstack_key ]]; then
        echo "/tmp/dana_hyperstack_key"
        return
    fi

    if [[ -f "${HOME}/.ssh/hyperstack_dana" ]]; then
        echo "${HOME}/.ssh/hyperstack_dana"
        return
    fi

    echo ""
}

# ---------------------------------------------------------------------------
# Read IP from last_vm.json
# ---------------------------------------------------------------------------

read_ip_from_json() {
    if [[ -f "${LAST_VM_JSON}" ]]; then
        # Use python if available, otherwise try grep
        if command -v python3 &>/dev/null; then
            python3 -c "import json; print(json.load(open('${LAST_VM_JSON}'))['public_ip'])" 2>/dev/null || echo ""
        elif command -v python &>/dev/null; then
            python -c "import json; print(json.load(open('${LAST_VM_JSON}'))['public_ip'])" 2>/dev/null || echo ""
        else
            grep -o '"public_ip"[[:space:]]*:[[:space:]]*"[^"]*"' "${LAST_VM_JSON}" \
                | head -1 \
                | sed 's/.*"public_ip"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/' \
                || echo ""
        fi
    else
        echo ""
    fi
}

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------

usage() {
    cat <<EOF
Usage:
  $(basename "$0") <VM_PUBLIC_IP>           Copy bootstrap_server.sh & run it
  $(basename "$0")                          Read IP from last_vm.json
  $(basename "$0") --ssh-only [IP]          Just open an SSH session

SSH key lookup order:
  1. \$HYPERSTACK_SSH_PRIVATE_KEY_PATH (env var)
  2. /tmp/dana_hyperstack_key
  3. ~/.ssh/hyperstack_dana

The VM public IP can be:
  - Passed as the first argument
  - Read automatically from ${LAST_VM_JSON}

Environment variables:
  HYPERSTACK_SSH_PRIVATE_KEY_PATH   Path to SSH private key
  SSH_USER                          Remote user (default: ubuntu)
EOF
    exit 1
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

SSH_ONLY=false

# Parse --ssh-only flag
if [[ "${1:-}" == "--ssh-only" ]]; then
    SSH_ONLY=true
    shift
fi

# Determine VM IP
VM_IP="${1:-}"
if [[ -z "${VM_IP}" ]]; then
    VM_IP="$(read_ip_from_json)"
    if [[ -z "${VM_IP}" || "${VM_IP}" == "pending" ]]; then
        echo "ERROR: No VM IP provided and could not read from ${LAST_VM_JSON}" >&2
        echo "" >&2
        usage
    fi
    echo "Using VM IP from last_vm.json: ${VM_IP}"
fi

# Find SSH key
SSH_KEY="$(find_ssh_key)"
if [[ -z "${SSH_KEY}" ]]; then
    echo "ERROR: No SSH private key found." >&2
    echo "Set HYPERSTACK_SSH_PRIVATE_KEY_PATH or place key at:" >&2
    echo "  /tmp/dana_hyperstack_key" >&2
    echo "  ~/.ssh/hyperstack_dana" >&2
    exit 1
fi

echo "Using SSH key: ${SSH_KEY}"
echo "Connecting to: ${SSH_USER}@${VM_IP}"
echo ""

# Common SSH options — never print key content
SSH_OPTS=(
    -i "${SSH_KEY}"
    -o StrictHostKeyChecking=no
    -o UserKnownHostsFile=/dev/null
    -o LogLevel=ERROR
)

# --- SSH-only mode ---
if [[ "${SSH_ONLY}" == true ]]; then
    echo "Opening SSH session..."
    exec ssh "${SSH_OPTS[@]}" "${SSH_USER}@${VM_IP}"
fi

# --- Bootstrap mode ---
if [[ ! -f "${BOOTSTRAP_SCRIPT}" ]]; then
    echo "ERROR: bootstrap_server.sh not found at ${BOOTSTRAP_SCRIPT}" >&2
    exit 1
fi

echo "Step 1/2: Copying bootstrap_server.sh to VM..."
scp "${SSH_OPTS[@]}" "${BOOTSTRAP_SCRIPT}" "${SSH_USER}@${VM_IP}:/tmp/bootstrap_server.sh"

echo "Step 2/2: Running bootstrap_server.sh on VM..."
ssh "${SSH_OPTS[@]}" "${SSH_USER}@${VM_IP}" "sudo bash /tmp/bootstrap_server.sh"

echo ""
echo "Done! To SSH into the VM:"
echo "  ssh -i ${SSH_KEY} ${SSH_USER}@${VM_IP}"
