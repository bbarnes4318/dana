# Hyperstack Provisioning Infrastructure

## Overview

Dana runs on a Hyperstack GPU VM, **not on the laptop**. This directory contains
scripts to provision the VM, bootstrap its software stack, and connect via SSH.

## Credential Handling

### API Key

| Rule | Detail |
|------|--------|
| Storage | `HYPERSTACK_API_KEY` environment variable only |
| Committed to git? | **Never** |
| Printed to stdout? | **Never** |
| Pasted into agent prompt? | Acceptable *temporarily* for testing — rotate immediately after |

### SSH Keys

- The **private key** is used only on the local machine to SSH into the VM.
- The **public key** is derived automatically from the private key
  (`ssh-keygen -y -f <private_key>`).
- Hyperstack receives the **public key**, never the private key.
- `HYPERSTACK_SSH_PRIVATE_KEY_PATH` points to the local private key file.

### Rotation Policy

If credentials (API key, private key, or any token) were ever pasted into:

- Chat messages or agent prompts
- Terminal logs
- GitHub issues, PRs, or commits
- Screenshots

**Rotate them immediately after testing.** Treat the exposed credential as
compromised.

## Architecture

```
┌─────────────────────┐          ┌──────────────────────────────┐
│  Local Machine      │          │  Hyperstack VM               │
│  (laptop / CI)      │   SSH    │  (GPU cloud)                 │
│                     │ ──────── │                              │
│  provision script   │          │  Docker + NVIDIA runtime     │
│  ssh bootstrap cmd  │          │  /opt/dana (git clone)       │
│  private key        │          │  vLLM, voice agent, etc.     │
└─────────────────────┘          └──────────────────────────────┘
```

- `provision_hyperstack.py` — runs locally, calls the Hyperstack API to create
  a VM and register your SSH public key.
- `bootstrap_server.sh` — runs **on the Hyperstack VM** to install Docker,
  NVIDIA Container Toolkit, and clone the Dana repo.
- `ssh_bootstrap_command.sh` — runs locally, copies `bootstrap_server.sh` to
  the VM and executes it over SSH.

## Environment Variables

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `HYPERSTACK_API_KEY` | yes | — | Never print or commit |
| `HYPERSTACK_SSH_PRIVATE_KEY_PATH` | yes | — | Path to local private key |
| `HYPERSTACK_REGION` | yes | — | Hyperstack region |
| `HYPERSTACK_IMAGE` | no | `Ubuntu 22.04` | OS image name |
| `HYPERSTACK_FLAVOR` | no | — | VM flavor (TODO: discover) |
| `HYPERSTACK_GPU_MODEL` | no | `L40` | GPU model |
| `HYPERSTACK_VM_NAME` | no | `dana-l40-prod` | VM display name |
| `HYPERSTACK_DISK_SIZE_GB` | no | `500` | Root disk size in GB |
| `DANA_CONFIRM_PROVISION` | no | — | Set to `yes` to actually create resources |

## Quick Start

```bash
# 1. Export credentials (never commit these)
export HYPERSTACK_API_KEY="your-api-key-here"
export HYPERSTACK_SSH_PRIVATE_KEY_PATH="$HOME/.ssh/hyperstack_dana"
export HYPERSTACK_REGION="your-region"

# 2. Dry run (no resources created)
python infra/hyperstack/provision_hyperstack.py

# 3. Provision for real
DANA_CONFIRM_PROVISION=yes python infra/hyperstack/provision_hyperstack.py

# 4. Bootstrap the server
bash infra/hyperstack/ssh_bootstrap_command.sh <VM_PUBLIC_IP>
```

## Files

| File | Runs on | Purpose |
|------|---------|---------|
| `provision_hyperstack.py` | Local | Create VM via Hyperstack API |
| `bootstrap_server.sh` | VM | Install Docker, NVIDIA, clone repo |
| `ssh_bootstrap_command.sh` | Local | SCP + SSH to run bootstrap on VM |
| `last_vm.json` | Local | Non-sensitive VM metadata (auto-generated) |
| `README.md` | — | This file |
