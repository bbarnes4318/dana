# DANA Outbound Voice Live-Call Runbook

This document describes how to deploy, test, and troubleshoot live outbound calls on the Dana voice platform.

## Pre-Flight Checklist
1. **LiveKit Server Status**: Verify LiveKit room server is reachable.
2. **Postgres Status**: Verify PgBouncer/Postgres database connection.
3. **Environment Keys**: Ensure all necessary credentials are set in `.env`.
   - `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`
   - `LIVEKIT_SIP_OUTBOUND_TRUNK_ID`
   - `LICENSED_AGENT_PHONE_NUMBER` (destination for warm/cold transfers)

---

## Operations Guide

### 1. Start the LiveKit Agent Worker
Run the agent worker daemon to listen for inbound WebRTC and SIP connections from the dialer:
```bash
python3 main.py
```
> [!NOTE]
> The worker binds to `LIVEKIT_AGENT_PORT` (default: `8085`).

### 2. Run the End-to-End Live Smoke Test
Verify health checks, credential validation, LLM response, and STT/TTS routing using the smoke test script:
```bash
python3 scripts/live_smoke_test.py
```

### 3. Place a Test Outbound Call
Run the controlled live call smoke test to place a real outbound call to a destination phone number:
```bash
python3 ops/live_call_smoke_test.py --to +1XXXXXXXXXX --from +1XXXXXXXXXX --expect-second-turn
```
* Use `--dry-run` to validate the stack without placing a real phone call.
* Add `--interactive` to verify barge-in and human-like response behavior manually.

---

## Troubleshooting

### VAD Health Check Failures
If you see `VAD provider 'silero' is unhealthy` on startup:
* Verify that ONNX Runtime dependencies are correctly installed.
* Verify python modules: `python3 -c "import onnxruntime; print(onnxruntime.__version__)"`.

### Connection Failures
If ElevenLabs/Deepgram fails to connect during a call:
* Check network connectivity and API keys.
* If transient WebSocket errors occur, the system will fall back to local mode (in `balanced` mode) or attempt reconnects.
