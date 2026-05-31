# Live Telephony Smoke Test

This document provides a runbook for using the Outbound Telephony Smoke Test Runner to verify live calling capability using LiveKit SIP and Telnyx.

## Purpose

The smoke test validates the full outbound integration pathway:
```
Telnyx SIP Trunk
  → LiveKit Outbound SIP Trunk
  → LiveKit CreateSIPParticipant
  → Real phone rings
  → LiveKit room created
  → SIP participant appears
  → CallAttempt saved
  → LiveCallSession saved
  → Dana agent worker status reported
```

---

## Required Environment Variables

To place live calls, ensure the following are defined in your environment:

| Variable | Description | Example / Required Value |
| :--- | :--- | :--- |
| `TELEPHONY_LIVE_MODE` | Must be `true` for live calls | `true` |
| `DANA_ENABLE_OUTBOUND_DIALER` | Enable the outbound dialer paths | `true` |
| `LIVEKIT_URL` | LiveKit Cloud WebSocket URL | `wss://ultimate-voice-g4vu57ge.livekit.cloud` |
| `LIVEKIT_API_KEY` | LiveKit credential key | `APIUPMsTGhCcZLr` |
| `LIVEKIT_API_SECRET` | LiveKit credential secret | `y1huiseavK5xHAjGS3MKKiUhoUckEeecRxJd8wCndK4B` |
| `LIVEKIT_SIP_OUTBOUND_TRUNK_ID` | Telnyx SIP outbound trunk in LiveKit | `ST_xNjWipLQGKtY` |
| `DANA_OUTBOUND_CALLER_ID` | Verified phone number to dial from | `+15055202898` |
| `DANA_AGENT_WORKER_ENABLED` | Set `true` to run worker checks | `true` |
| `DANA_TEST_CALL_TO` | Fallback phone number to dial | `+15513326220` |

### Optional Variables
- `DANA_WAIT_UNTIL_ANSWERED=true`: Hold connection until the call is answered.
- `DANA_KRISP_ENABLED=true`: Enable Krisp AI noise reduction.
- `DANA_TRANSFER_PHONE_NUMBER=+12816991120`: Fallback licensed agent transfer destination.

---

## How to Run the Smoke Test

### 1. Dry Run / Configuration Check

A dry run performs all readiness checks, validates environment variables (safely masking secret tokens in output reports), audits LiveKit SDK and dependencies, and verifies worker availability without placing a physical call.

```powershell
python scripts/run_live_telephony_smoke_test.py --operator "Jimmy" --dry-run
```

If successful, this will output a clean JSON structure to `stdout` containing the configuration status and exit with code `0`.

### 2. Live Smoke Test Call

To run a live test call and ring a destination phone number:

```powershell
python scripts/run_live_telephony_smoke_test.py --operator "Jimmy" --to "+1YOURPHONE" --confirm "LIVE CALL"
```

> [!IMPORTANT]
> The exact string `--confirm "LIVE CALL"` is required. If omitted or incorrect, the script terminates immediately with exit code `1` and does not call.

---

## Running from the Web UI

You can also trigger smoke tests directly from the **Training Web Console**:

1. Start the web console:
   ```powershell
   python scripts/run_training_web_console.py
   ```
2. Navigate to `http://localhost:8787` in your browser.
3. Open the **Telephony** tab.
4. Locate the **Live Smoke Test** card.
5. Fill out the fields:
   - **Operator Name**: e.g., `Jimmy`
   - **Phone Number**: Optional destination phone.
   - **Dry Run**: Check this to run readiness checks only.
   - **Place Call**: Uncheck this to only run checks.
   - **Confirm**: Type `LIVE CALL` exactly.
6. Click **Run Smoke Test**.
7. The status updates dynamically, presenting readiness failures, worker warnings, and SIP details.

---

## Report Artifacts

Each test run creates detailed reports under the `data/live_smoke_tests` directory:
- **JSON**: `data/live_smoke_tests/smoke_test_[timestamp]_[test_id].json`
- **Markdown**: `data/live_smoke_tests/smoke_test_[timestamp]_[test_id].md`

Secrets such as `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` are masked in these reports (e.g. `API...ZLr`).

---

## Troubleshooting Common Failures

### 1. Missing Environment Variables
- **Symptom**: Smoke test fails with a readiness error listing unset environment keys.
- **Resolution**: Check your shell session or `.env` file and set the required keys.

### 2. Missing LiveKit SDK/Dependencies
- **Symptom**: Auditor reports missing LiveKit agent worker packages or import errors.
- **Resolution**: Install required packages:
  ```powershell
  pip install livekit-api livekit-agents
  ```

### 3. Outbound SIP Trunk ID / Caller ID Rejected
- **Symptom**: Call attempt terminates immediately or returns `SIP_TRUNK_REJECTED` / `403 Forbidden`.
- **Resolution**: Ensure your `DANA_OUTBOUND_CALLER_ID` matches a verified number in your Telnyx portal and `LIVEKIT_SIP_OUTBOUND_TRUNK_ID` is correct.

### 4. Agent Worker Did Not Join Room
- **Symptom**: The phone rings, you answer, but Dana does not speak.
- **Resolution**: Check if the LiveKit agent worker is running:
  ```powershell
  python scripts/run_livekit_agent_worker.py
  ```
  Ensure `DANA_AGENT_WORKER_ENABLED=true` is set.

---

## Verification Checklist after Ringing

Once the phone rings:
1. Answer the call.
2. Verify Dana greets you (if the worker is running).
3. Check the web console or reports directory for the saved **CallAttempt** and **LiveCallSession** records.
