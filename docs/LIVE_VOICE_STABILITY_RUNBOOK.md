# Runbook - Live Voice Path Stabilization

This runbook outlines the steps for configuring, verifying, and rolling back settings to stabilize the outbound AI voice agent (Dana) for live customer calls.

## 1. Required Production Environment Variables

To force premium live voice mode and disable risky experimental features, configure these defaults in `/opt/dana/.env` (on RunPod or production instance):

```bash
# --- Premium Live Voice Mode Configuration ---
DANA_VOICE_MODE=premium_live
DANA_TTS_PROVIDER=elevenlabs
DANA_TTS_ROUTING_MODE=cloud
DANA_ALLOW_CLOUD_TTS_FALLBACK=true
DANA_ENABLE_STREAMING_RESPONSE=true
DANA_ENABLE_AUDIO_FILTERS=false
DANA_STT_PROVIDER=deepgram
DANA_STT_ROUTING_MODE=cloud
DANA_LLM_ROUTING_MODE=cloud
DANA_ALLOW_CLOUD_LLM_FALLBACK=true

# --- Audio & Turn-Taking Stabilization ---
DANA_ENABLE_AMD_WORKER=false
DANA_ENABLE_FAST_INTERRUPTION=false
DANA_ENABLE_LIVEKIT_AUDIO_MONKEYPATCH=false
DANA_ENABLE_DIRECT_FFI_TTS_PUSH=false
DANA_ALLOW_AGENT_BARGE_IN=false
DANA_INTERRUPTION_SPEECH_THRESHOLD=0.65
DANA_TTS_OUTPUT_GAIN=1.20

# --- Paced Timing Delays ---
DANA_TURN_MIN_DELAY=0.25
DANA_TURN_MAX_DELAY=0.80
```

---

## 2. Running a Single Live Test Call

To place a single manual test call to confirm that audio, routing, and turn-taking paths are completely stable:

1. Connect to the remote agent server via SSH:
   ```bash
   ssh -p <ssh-port> root@<host-ip>
   ```
2. Navigate to the project directory:
   ```bash
   cd /opt/dana
   ```
3. Use the CLI tool to trigger a single outbound call to your phone:
   ```bash
   python scripts/place_live_test_call.py --phone +1XXXXXXXXXX
   ```

---

## 3. Log Confirmations

Verify the logs (`/var/log/agent_worker.log` or container logs) to confirm that the correct paths are active.

### A. Cloud STT and TTS Activation
Search for routing decisions:
- STT select provider log should indicate cloud/deepgram:
  `[STT_PROVIDER_LOG] selected stt provider: deepgram`
- TTS select provider log should indicate cloud/elevenlabs:
  `[TTS_PROVIDER_LOG] selected tts provider: elevenlabs`

### B. Monkeypatch and Direct FFI Push Status
- Confirm monkeypatch is skipped by default:
  `LiveKit audio monkeypatch is disabled by default.`
- Confirm direct FFI audio push is skipped:
  Look at log trace for:
  `Direct FFI push bypassed (DANA_ENABLE_DIRECT_FFI_TTS_PUSH is false)` (or no FFI push logs).

### C. AMD Worker Status
- Confirm AMD is bypassed:
  `Bypassing AMD parallel worker for track <track-id> (DANA_ENABLE_AMD_WORKER is false)`

---

## 4. Rollback to Safe Local Mode

If ElevenLabs or Deepgram API issues occur, you can roll back to the local voice path (which uses local Kokoro and STT) by modifying these variables:

```bash
DANA_VOICE_MODE=local_cost
DANA_TTS_PROVIDER=local
DANA_TTS_ROUTING_MODE=local
DANA_STT_PROVIDER=local
DANA_STT_ROUTING_MODE=local
DANA_LLM_ROUTING_MODE=local
```

Restart the agent worker process for the changes to take effect:
```bash
docker compose restart voice_agent
```
