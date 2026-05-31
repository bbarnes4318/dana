# LiveKit Agent Worker Setup Guide

This document describes how to configure, run, and troubleshoot the Dana outbound LiveKit agent worker for real-time voice pipeline operations.

---

## 📦 Required Dependencies

The Agent Worker requires the LiveKit Agents Python SDK and plugins for STT, TTS, and audio processing.

Install the exact package pins from `requirements.txt`:
```bash
pip install -r requirements.txt
```

Specifically, the voice stack requires:
- `livekit` and `livekit-agents` (v1.5.12+)
- `livekit-plugins-openai` (v1.5.12+)
- `livekit-plugins-silero` (v1.5.12+)
- `sounddevice` and `soundfile`

---

## ⚙️ Environment Configuration

Set the following environment variables in your `.env` file or shell session:

### 1. LiveKit Credentials
```bash
LIVEKIT_URL=wss://your-livekit-domain.livekit.cloud
LIVEKIT_API_KEY=your-api-key
LIVEKIT_API_SECRET=your-api-secret
```

### 2. Provider Credentials
The voice pipeline defaults to OpenAI for STT/TTS and AgentRuntime LLM.
```bash
OPENAI_API_KEY=your-openai-api-key
```

### 3. Worker Configuration
```bash
DANA_AGENT_WORKER_ENABLED=true
DANA_LIVEKIT_ROOM_PREFIX=dana
DANA_AGENT_NAME=Dana
```

---

## 🛠️ Verification & Startup

### 1. Check Worker Readiness (Dry-Run Check)
Verify that all packages are importable and required environment variables are set without running the worker daemon:
```bash
python scripts/run_livekit_agent_worker.py --check-only
```
- **Exit Code 0**: Everything is ready.
- **Exit Code 1**: Prints detailed JSON containing missing packages, env variables, or provider keys.

### 2. Start the Agent Worker Daemon
Run the worker daemon in the background. It will automatically listen for room dispatch jobs matching the room prefix:
```bash
python scripts/run_livekit_agent_worker.py
```
*Tip: Add `--debug` for verbose debugging logs sent to `stderr`.*

### 3. Test Campaign Dialer / Test Call
In another terminal, place a live test call:
```bash
python scripts/test_live_outbound_call.py --to +15555550100 --operator "Jimmy" --confirm "LIVE CALL"
```
Or run the full automated smoke test:
```bash
python scripts/run_live_telephony_smoke_test.py --operator "Jimmy" --to +15555550100 --confirm "LIVE CALL"
```

---

## 🔍 How to Verify Dana's Interaction

### How to know Dana joined the room:
1. **Console Logs**: The worker logs `Connecting to room: name=dana-test-call-...` and `Participant joined identity=outbound-...`.
2. **Web UI Monitor**: Go to the **Real-time Live Calls Monitor** on the Telephony tab. Active sessions will show status `active` and list the LiveKit room name.

### How to know Dana spoke:
1. **Hearing Audio**: When answering the phone, you should hear Dana speak the opening line:
   *"Hi, this is Dana with American Beneficiary. I’m calling about final expense information you recently requested."*
2. **Turn Database Logs**: Check the `CallAttempt` and `LiveCallSession` database logs or training review tables. You will see turns logged with:
   - `speaker=agent`
   - `text="Hi, this is Dana with..."`

---

## ❌ Troubleshooting Common Blockers

### 1. `No module named 'livekit.plugins'`
- **Symptom**: Worker startup or readiness check fails with this exact import error.
- **Resolution**: Install the specific plugins:
  ```bash
  pip install livekit-plugins-openai livekit-plugins-silero
  ```

### 2. Phone rings, but no AI voice (Dana remains silent)
- **Symptom**: You answer the phone, but there is dead silence.
- **Resolution**:
  - Verify the agent worker daemon is actively running and has not crashed.
  - Verify that `DANA_AGENT_WORKER_ENABLED=true` is set.
  - Check the worker console logs for errors during room connection or participant joins.

### 3. LiveKit room exists, but worker is not assigned (no dispatch)
- **Symptom**: LiveKit room is created, but no logs appear on the worker terminal.
- **Resolution**:
  - The worker daemon only joins rooms starting with the configured prefix (default `dana`). Ensure the trunk or script configures matching room names.
  - Verify the worker daemon is running on the same LiveKit server url (`LIVEKIT_URL`).

### 4. `LLM/STT/TTS provider configuration missing`
- **Symptom**: Check-only script reports `OPENAI_API_KEY` is missing.
- **Resolution**: Ensure `OPENAI_API_KEY` is set and loaded by the shell starting the worker.

### 5. STT works, but TTS is silent
- **Symptom**: You speak, worker logs show transcription, but you hear no audio back.
- **Resolution**:
  - Verify the OpenAI API key is valid and has sufficient quota.
  - Check for TTS synthesis errors in the worker terminal logs.

### 6. TTS works, but AgentRuntime fails
- **Symptom**: You speak, but the worker crashes or logs errors from `AgentRuntime.process_turn`.
- **Resolution**:
  - Ensure the repository database is accessible (Postgres url is configured).
  - Verify database schema compatibility with the `save_call_turn` calls.
