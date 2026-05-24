#!/usr/bin/env bash
# =============================================================================
# bootstrap_server.sh — Dana Hyperstack VM Bootstrap
#
# Runs ON THE HYPERSTACK SERVER (not on the laptop).
# Installs Docker, NVIDIA Container Toolkit, clones the Dana repo.
# Does NOT start any services.
#
# Usage:
#   sudo bash bootstrap_server.sh
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

info()  { echo -e "\n\033[1;34m[INFO]\033[0m  $*"; }
ok()    { echo -e "\033[1;32m[OK]\033[0m    $*"; }
warn()  { echo -e "\033[1;33m[WARN]\033[0m  $*"; }
fatal() { echo -e "\033[1;31m[FATAL]\033[0m $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

info "Checking environment..."

if [[ $EUID -ne 0 ]]; then
    fatal "This script must be run as root (use sudo)."
fi

if ! grep -qi "ubuntu" /etc/os-release 2>/dev/null; then
    fatal "This script requires Ubuntu. Detected OS: $(cat /etc/os-release 2>/dev/null || echo unknown)"
fi

UBUNTU_VERSION=$(grep VERSION_ID /etc/os-release | cut -d'"' -f2)
ok "Ubuntu ${UBUNTU_VERSION} detected."

# ---------------------------------------------------------------------------
# System packages
# ---------------------------------------------------------------------------

info "Updating apt and installing system packages..."
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
apt-get install -y -qq \
    ca-certificates \
    curl \
    git \
    git-lfs \
    htop \
    nvtop \
    tmux \
    ufw \
    unzip

git lfs install --system
ok "System packages installed."

# ---------------------------------------------------------------------------
# Docker Engine (official apt repo)
# ---------------------------------------------------------------------------

info "Installing Docker Engine..."

if command -v docker &>/dev/null; then
    warn "Docker already installed: $(docker --version). Skipping install."
else
    # Add Docker's official GPG key
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc

    # Add the Docker apt repository
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
      https://download.docker.com/linux/ubuntu \
      $(. /etc/os-release && echo "${VERSION_CODENAME}") stable" \
      > /etc/apt/sources.list.d/docker.list

    apt-get update -qq
    apt-get install -y -qq \
        docker-ce \
        docker-ce-cli \
        containerd.io \
        docker-buildx-plugin \
        docker-compose-plugin

    ok "Docker installed."
fi

# ---------------------------------------------------------------------------
# NVIDIA Container Toolkit
# ---------------------------------------------------------------------------

info "Installing NVIDIA Container Toolkit..."

if dpkg -l | grep -q nvidia-container-toolkit; then
    warn "NVIDIA Container Toolkit already installed. Skipping install."
else
    # Add NVIDIA package repository
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
        | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

    curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        > /etc/apt/sources.list.d/nvidia-container-toolkit.list

    apt-get update -qq
    apt-get install -y -qq nvidia-container-toolkit

    ok "NVIDIA Container Toolkit installed."
fi

# ---------------------------------------------------------------------------
# Configure Docker to use NVIDIA runtime
# ---------------------------------------------------------------------------

info "Configuring Docker with NVIDIA runtime..."
nvidia-ctk runtime configure --runtime=docker
systemctl restart docker
ok "Docker configured with NVIDIA runtime."

# ---------------------------------------------------------------------------
# Verify GPU + Docker
# ---------------------------------------------------------------------------

info "Verifying installation..."

echo "  nvidia-smi:"
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader \
    | sed 's/^/    /'
echo ""
echo "  Docker version:          $(docker --version)"
echo "  Docker Compose version:  $(docker compose version)"
ok "Verification complete."

# ---------------------------------------------------------------------------
# Create /opt/dana and clone repo
# ---------------------------------------------------------------------------

DANA_DIR="/opt/dana"

info "Setting up ${DANA_DIR}..."
mkdir -p "${DANA_DIR}"

if [[ -d "${DANA_DIR}/.git" ]]; then
    info "Repository already exists. Pulling latest..."
    cd "${DANA_DIR}"
    git pull --ff-only
else
    info "Cloning dana repository..."
    git clone https://github.com/bbarnes4318/dana.git "${DANA_DIR}"
    cd "${DANA_DIR}"
fi

ok "Dana repo ready at ${DANA_DIR}"

# ---------------------------------------------------------------------------
# .env setup
# ---------------------------------------------------------------------------

if [[ -f "${DANA_DIR}/.env.production.example" ]] && [[ ! -f "${DANA_DIR}/.env" ]]; then
    cp "${DANA_DIR}/.env.production.example" "${DANA_DIR}/.env"
    ok "Copied .env.production.example → .env (edit before starting services)"
elif [[ -f "${DANA_DIR}/.env" ]]; then
    warn ".env already exists — not overwriting."
else
    warn ".env.production.example not found — create .env manually before starting."
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "============================================================"
echo "  Bootstrap complete!"
echo "============================================================"
echo ""
echo "  Installed:"
echo "    • System packages (curl, git, htop, nvtop, tmux, ufw, ...)"
echo "    • Docker Engine + Docker Compose plugin"
echo "    • NVIDIA Container Toolkit (Docker runtime configured)"
echo ""
echo "  Dana repo: ${DANA_DIR}"
echo "  .env file: ${DANA_DIR}/.env"
echo ""
echo "  Next steps:"
echo "    1. Edit ${DANA_DIR}/.env with your production credentials"
echo "    2. cd ${DANA_DIR}"
echo "    3. docker compose up -d"
echo ""
echo "  NOTE: Services were NOT started automatically."
echo "        Review .env and docker-compose.yml before starting."
echo "============================================================"
