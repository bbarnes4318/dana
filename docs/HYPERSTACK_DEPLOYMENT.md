# Dana — Hyperstack Deployment Guide

> **Key principle:** Dana runs entirely on a Hyperstack GPU server.
> Your laptop is only used for SSH access and control — it runs nothing.

---

## Architecture Overview

| Component | Where It Runs | Details |
|---|---|---|
| **STT** (Speech-to-Text) | Hyperstack GPU server | faster-whisper / Whisper large-v3-turbo |
| **TTS** (Text-to-Speech) | Hyperstack GPU server | Kokoro ONNX |
| **LLM** (Language Model) | Hyperstack GPU server | vLLM — meta-llama/Llama-3.1-8B-Instruct (fp8) |
| **Voice Agent** | Hyperstack GPU server | Orchestrates STT → LLM → TTS pipeline |
| **LiveKit** | LiveKit Cloud (production) | WebRTC media transport — reached outbound by the voice-agent |
| **Your Laptop** | Local machine | SSH client, `git push`, monitoring only |

**vLLM is private.** It listens on `localhost:8000` inside the Docker network and must **never** be exposed to the public internet.

---

## Server Requirements

| Resource | Minimum | Preferred |
|---|---|---|
| **GPU** | NVIDIA L40 48 GB | NVIDIA L40S 48 GB |
| **OS** | Ubuntu 22.04 LTS | Ubuntu 22.04 LTS |
| **vCPU** | 16+ vCPU | 28 pCPU |
| **RAM** | 90 GB+ | 120 GB |
| **Disk** | 250 GB+ SSD | 500 GB SSD |

> [!NOTE]
> vLLM with `--gpu-memory-utilization 0.7` reserves ~33.6 GB of the 48 GB GPU.
> The remaining ~14 GB is shared between Whisper large-v3-turbo (~3 GB) and Kokoro ONNX (~1 GB), with overhead for buffers.

---

## Setup — Step by Step

All commands below run **on the Hyperstack server** over SSH.

### 1. System Packages

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y ca-certificates curl git git-lfs htop nvtop tmux ufw unzip
```

### 2. Docker (Official Apt Repo)

```bash
# Add Docker GPG key
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Add Docker repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker Engine
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Allow current user to run Docker without sudo
sudo usermod -aG docker $USER
newgrp docker
```

### 3. NVIDIA Container Toolkit

```bash
# Add NVIDIA GPG key and repository
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

# Install
sudo apt update
sudo apt install -y nvidia-container-toolkit
```

### 4. Docker Runtime Configuration

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### 5. Verification

```bash
# GPU driver
nvidia-smi

# Docker
docker --version
docker compose version

# GPU passthrough into containers
docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi
```

All four commands must succeed before continuing. If `nvidia-smi` fails, check that NVIDIA drivers are installed on the host. If the `docker run` test fails, verify the NVIDIA Container Toolkit installation.

---

## Repository Setup

```bash
sudo mkdir -p /opt/dana
sudo chown -R $USER:$USER /opt/dana
cd /opt/dana
git clone https://github.com/bbarnes4318/dana.git .
```

---

## Environment Setup

```bash
cp .env.production.example .env
nano .env
```

Fill in every placeholder. Required variables:

| Variable | Description |
|---|---|
| `HF_TOKEN` | Hugging Face token (read access to `meta-llama/Llama-3.1-8B-Instruct`) |
| `LIVEKIT_URL` | Your LiveKit Cloud WebSocket URL (e.g. `wss://your-project.livekit.cloud`) |
| `LIVEKIT_API_KEY` | LiveKit API key |
| `LIVEKIT_API_SECRET` | LiveKit API secret |

> [!CAUTION]
> **Never commit `.env`.** It contains secrets. The `.gitignore` should already exclude it — verify with `git status` before pushing.

---

## Firewall

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from YOUR_STATIC_IP to any port 22 proto tcp
sudo ufw enable
sudo ufw status verbose
```

Replace `YOUR_STATIC_IP` with the public IP address you SSH from.

> [!WARNING]
> **Do NOT open port 8000 publicly.** vLLM must remain internal to the Docker network.
>
> **Do NOT expose random Docker ports.** Only SSH (port 22) should be reachable from the outside.
>
> LiveKit Cloud is reached **outbound** by the voice-agent container — no inbound ports are needed for it.

---

## Validation (Without Starting Services)

Run these checks before launching anything:

```bash
docker compose config
nvidia-smi
ls -la
test -f .env && echo ".env exists"
```

- `docker compose config` — Parses and validates `docker-compose.yaml` and variable substitution. Must exit 0 with no errors.
- `nvidia-smi` — Confirms the GPU driver is loaded.
- `ls -la` — Confirms you are in `/opt/dana` and project files are present.
- `.env exists` — Confirms the environment file was created.

---

## Manual Start (Testing Only)

Start only when you are actively testing:

```bash
docker compose --env-file .env up -d --build vllm-server voice-agent
```

This builds the voice-agent image and pulls the vLLM image. On first run, vLLM will download the Llama model (~16 GB) — this takes several minutes.

### Logs

```bash
# Voice agent logs (STT/TTS pipeline)
docker compose logs -f --tail=100 voice-agent

# vLLM logs (model loading, inference)
docker compose logs -f --tail=100 vllm-server
```

### Stop After Testing

```bash
docker compose down
docker compose ps    # should show nothing
docker ps            # should show nothing
```

---

## Cost Warning

> [!IMPORTANT]
> **Hyperstack GPU billing may continue while the VM is running** even if Docker containers are stopped.
>
> When you are finished testing, **shut down, pause, or delete the VM** from the Hyperstack dashboard to stop billing.
>
> Running containers are not the billing trigger — the VM itself is.

---

## Credential Safety

> [!CAUTION]
> **Never commit `.env`.** Never commit SSH private keys.
>
> **Never paste** private keys, API tokens, or secrets into:
> - GitHub (issues, PRs, code, wiki)
> - Chat messages or transcripts
> - Log files or screenshots
> - Documentation or terminal recordings
>
> **If any credential was accidentally exposed, rotate it immediately:**
> - Hugging Face token → [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
> - LiveKit keys → LiveKit Cloud dashboard
> - SSH keys → regenerate and update `authorized_keys` on the server
> - Hyperstack API key → Hyperstack dashboard

---

## What NOT to Do

| ❌ Don't | ✅ Do Instead |
|---|---|
| Run STT/TTS/LLM on your laptop | SSH into Hyperstack and run everything there |
| Expose port 8000 to the internet | Keep vLLM internal to the Docker network |
| Leave the VM running overnight | Shut down/pause/delete when not testing |
| Commit `.env` or secrets | Use `.env.production.example` as a template; keep `.env` out of git |
| Open ports for LiveKit | LiveKit Cloud is reached outbound — no inbound ports needed |
