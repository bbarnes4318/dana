# Dana — Hyperstack Operator Runbook

> **All commands in this runbook run on the Hyperstack server over SSH, not on your laptop.**

---

## Normal Workflow

```bash
# 1. SSH into the Hyperstack server
ssh user@YOUR_HYPERSTACK_IP

# 2. Navigate to the project
cd /opt/dana

# 3. Pull latest code
git pull

# 4. Create .env if it does not already exist
#    (Skip this step if .env is already configured)
cp .env.production.example .env
nano .env

# 5. Validate before starting
./scripts/hyperstack_validate.sh

# 6. Start services
./scripts/hyperstack_start_test.sh

# 7. Watch logs (Ctrl+C to stop following)
./scripts/hyperstack_logs.sh voice-agent

# 8. Stop services when done
./scripts/hyperstack_stop.sh
```

---

## Emergency Stop

If something is wrong and you need to shut everything down immediately:

```bash
cd /opt/dana

# Stop all Compose services
docker compose down

# Verify nothing is running
docker ps

# Force-stop any remaining containers
docker stop $(docker ps -q) 2>/dev/null || true
```

After an emergency stop, check logs before restarting:

```bash
docker compose logs --tail=200 voice-agent
docker compose logs --tail=200 vllm-server
```

---

## Quick Diagnostics

### GPU Status

```bash
nvidia-smi
```

Expected output: One NVIDIA L40/L40S with 48 GB, driver loaded, CUDA version shown. If this command fails, the GPU driver is not loaded — do not attempt to start services.

### Container Status

```bash
docker ps
```

Expected output when running:

| Container | Status |
|---|---|
| `sovereign-vllm` | `Up ... (healthy)` |
| `sovereign-voice-agent` | `Up ...` |

If `sovereign-vllm` shows `(health: starting)` for more than 3 minutes, check its logs.

### Port Check

```bash
ss -tulpn
```

Expected:
- Port `22` — sshd (external)
- Port `8000` — vLLM (internal only, bound to Docker network)
- No other unexpected listeners

> [!WARNING]
> Port 8000 should **not** be reachable from the public internet. If `ss -tulpn` shows `0.0.0.0:8000`, verify that UFW is blocking external access.

### Firewall Status

```bash
sudo ufw status verbose
```

Expected: Default deny incoming, allow outgoing, SSH allowed from your IP only.

---

## Credential Hygiene

> [!CAUTION]
> **If any of the following secrets were pasted into chat, logs, GitHub, screenshots, or terminal transcripts, rotate them immediately.**

| Secret | Where to Rotate |
|---|---|
| Hyperstack API key | Hyperstack dashboard |
| SSH private key | Regenerate key pair; update `~/.ssh/authorized_keys` on the server |
| Hugging Face token (`HF_TOKEN`) | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) |
| LiveKit API key / secret | LiveKit Cloud dashboard |
| Telnyx API key / SIP credentials | Telnyx dashboard |

After rotating, update `/opt/dana/.env` on the server with the new values.

---

## Troubleshooting

### GPU Not Detected

**Symptom:** `nvidia-smi` returns `command not found` or `NVIDIA-SMI has failed`.

**Fix:**
1. Verify the VM was provisioned with a GPU (check Hyperstack dashboard).
2. Install or reinstall NVIDIA drivers:
   ```bash
   sudo apt install -y nvidia-driver-535
   sudo reboot
   ```
3. After reboot, run `nvidia-smi` again.
4. If the driver loads but Docker can't see the GPU, reinstall NVIDIA Container Toolkit:
   ```bash
   sudo apt install -y nvidia-container-toolkit
   sudo nvidia-ctk runtime configure --runtime=docker
   sudo systemctl restart docker
   docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi
   ```

---

### vLLM Out of Memory (OOM)

**Symptom:** `sovereign-vllm` crashes with `torch.cuda.OutOfMemoryError` or exits with code 137.

**Fix:**
1. Check current GPU memory usage:
   ```bash
   nvidia-smi
   ```
2. Make sure no other processes are consuming GPU memory. Kill stale Python/CUDA processes:
   ```bash
   sudo fuser -v /dev/nvidia*
   # If stale processes are listed, kill them:
   sudo kill -9 <PID>
   ```
3. Lower GPU memory utilization in `docker-compose.yaml`:
   ```yaml
   --gpu-memory-utilization 0.65
   ```
4. Reduce max model length if needed:
   ```yaml
   --max-model-len 4096
   ```
5. Restart:
   ```bash
   docker compose down
   docker compose --env-file .env up -d --build vllm-server voice-agent
   ```

---

### Voice Agent Cannot Connect to vLLM

**Symptom:** Voice agent logs show connection errors to `http://vllm-server:8000/v1`.

**Fix:**
1. Confirm vLLM is healthy:
   ```bash
   docker compose ps
   docker compose logs --tail=50 vllm-server
   ```
2. If vLLM shows `(health: starting)`, it may still be loading the model. Wait up to 3 minutes after initial pull.
3. If vLLM is healthy, test connectivity from inside the voice-agent container:
   ```bash
   docker exec sovereign-voice-agent curl -s http://vllm-server:8000/health
   ```
   Expected output: `{"status":"ok"}` or similar.
4. If the `curl` test fails, both containers may not be on the same Docker network:
   ```bash
   docker network inspect sovereign-voice-network
   ```
   Both `sovereign-vllm` and `sovereign-voice-agent` must appear in the network's `Containers` list.
5. If the network is wrong, bring everything down and back up:
   ```bash
   docker compose down
   docker compose --env-file .env up -d --build vllm-server voice-agent
   ```

---

### LiveKit Connection Issues

**Symptom:** Voice agent logs show WebSocket connection failures to LiveKit, or `LIVEKIT_URL` errors.

**Fix:**
1. Verify `.env` has the correct LiveKit Cloud URL:
   ```bash
   grep LIVEKIT_URL /opt/dana/.env
   ```
   It should start with `wss://` (not `ws://`, not `http://`).

2. Verify the API key and secret are set (non-empty):
   ```bash
   grep LIVEKIT_API_KEY /opt/dana/.env
   grep LIVEKIT_API_SECRET /opt/dana/.env
   ```

3. Test outbound connectivity from the server:
   ```bash
   curl -I https://your-project.livekit.cloud
   ```
   Replace with your actual LiveKit Cloud hostname. A 2xx or 4xx response means the server can reach LiveKit. A timeout means a network/firewall issue.

4. Confirm UFW allows outbound traffic:
   ```bash
   sudo ufw status verbose
   ```
   Default policy for outgoing should be `allow`.

5. If credentials were recently rotated, restart the voice agent to pick up new values:
   ```bash
   docker compose down
   docker compose --env-file .env up -d --build vllm-server voice-agent
   ```

---

### Model Download Stalls or Fails

**Symptom:** First `docker compose up` hangs during vLLM startup while downloading from Hugging Face.

**Fix:**
1. Confirm `HF_TOKEN` is set and valid:
   ```bash
   grep HF_TOKEN /opt/dana/.env
   ```
2. Test Hugging Face access directly:
   ```bash
   curl -s -o /dev/null -w "%{http_code}" \
     -H "Authorization: Bearer YOUR_HF_TOKEN_HERE" \
     https://huggingface.co/api/models/meta-llama/Llama-3.1-8B-Instruct
   ```
   Expected: `200`. If `401` or `403`, the token is invalid or lacks access to the gated model — accept the license at [huggingface.co/meta-llama/Llama-3.1-8B-Instruct](https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct).
3. If the download was partially completed, remove the cache and retry:
   ```bash
   docker volume rm dana_vllm-cache
   docker compose --env-file .env up -d --build vllm-server voice-agent
   ```

---

### Disk Space Exhausted

**Symptom:** Containers fail to start; `No space left on device` errors in logs.

**Fix:**
1. Check disk usage:
   ```bash
   df -h /
   ```
2. Prune unused Docker resources:
   ```bash
   docker system prune -a --volumes
   ```
   > [!WARNING]
   > This removes **all** unused images, containers, and volumes. Model caches stored in Docker volumes will be deleted and must be re-downloaded on next start.
3. If pruning is not enough, the disk is too small — resize the VM's disk in the Hyperstack dashboard.

---

### Telnyx + LiveKit Telephony Issues

**Symptom:** Outbound call fails or transfer returns `success=False` with `reason="transfer_not_confirmed"` or `reason="licensed_agent_phone_number_not_configured"`.

**Fix:**
1. Check that the safety gates in `/opt/dana/.env` are correctly set:
   ```bash
   grep DANA_CONFIRM_ /opt/dana/.env
   ```
   To run live calls and transfers, ensure `DANA_CONFIRM_PLACE_CALL=yes` and `DANA_CONFIRM_TRANSFER_CALL=yes` are set.
2. Confirm the destination phone numbers are formatted in E.164:
   ```bash
   grep PHONE_NUMBER /opt/dana/.env
   ```
   Values must not be `"replace_me"`. They should be like `+15551234567`.
3. Check the saved resources on the server:
   ```bash
   cat /opt/dana/telephony/provisioned_resources.json
   cat /opt/dana/.env.telephony
   ```
   If these files do not exist or are empty, re-run the setup setup steps in [TELNYX_LIVEKIT_SIP_SETUP.md](file:///opt/dana/docs/TELNYX_LIVEKIT_SIP_SETUP.md).

---

## Cost Reminder

> [!IMPORTANT]
> Hyperstack bills for the VM while it is running, regardless of whether Docker containers are active.
>
> **When you are done testing:**
> 1. `docker compose down`
> 2. Shut down / pause / delete the VM from the Hyperstack dashboard
>
> Do not leave the VM running overnight unless you intend to pay for it.
