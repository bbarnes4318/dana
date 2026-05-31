# Live Outbound Call Runbook

This runbook describes the operational procedures to manage and troubleshoot real outbound telephony calls using Telnyx and LiveKit SIP.

## Preflight Checklist

Before enabling live dialing, verify the following:
1. [ ] Check calling hour windows (outbound calls are permitted only between 09:30 and 18:00 recipient local time).
2. [ ] Ensure DNC scrub lists are populated and campaign lead imports are cleaned.
3. [ ] Confirm that the Telnyx outbound trunk connection is registered in the LiveKit project.
4. [ ] Run the readiness audit script.
5. [ ] Verify that the Agent Worker daemon is running in the background.

---

## Required Environment Variables

```bash
# Enable live calls
TELEPHONY_LIVE_MODE=true
DANA_ENABLE_OUTBOUND_DIALER=true

# LiveKit Connection
LIVEKIT_URL=wss://...
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
LIVEKIT_SIP_OUTBOUND_TRUNK_ID=...
DANA_OUTBOUND_CALLER_ID=+1...

# Worker Configuration
DANA_AGENT_WORKER_ENABLED=true
```

---

## Operational Steps

### 1. Verification of Live Readiness (Audit)
- **CLI**:
  ```bash
  python scripts/check_live_telephony_readiness.py
  ```
- **Web UI**:
  Go to the **Telephony & Campaigns** tab, find the **Live Telephony Readiness Audit** card, and click **Run Audit**.

### 2. Live Smoke Test (Preferred Verification Method)
Before starting campaigns or placing individual test calls, run the full automated smoke test. This tests readiness, worker dependencies, SIP trunking, and call attempts in a single guarded flow.
- **CLI**:
  ```bash
  python scripts/run_live_telephony_smoke_test.py --operator "Jimmy" --to +15555550000 --confirm "LIVE CALL"
  ```
- **Web UI**:
  1. Go to the **Telephony** tab and locate the **Live Smoke Test** card.
  2. Enter **Operator Name**, optionally enter **Phone Number**, type **LIVE CALL** in the confirmation input box.
  3. Click **Run Live Smoke Test**.
- For more details, see the [Live Telephony Smoke Test Runbook](file:///C:/Users/jimbo/.gemini/antigravity/worktrees/ultimate-voice/telephony-campaign-ops-layer/docs/live_telephony_smoke_test.md).

### 3. Startup of the Agent Worker
- **CLI**:
  ```bash
  python scripts/run_livekit_agent_worker.py
  ```

### 4. Placing a single Live Test Call
- **CLI**:
  ```bash
  python scripts/test_live_outbound_call.py --to +15555550000 --operator "Jimmy" --confirm "LIVE CALL"
  ```
- **Web UI**:
  1. Under the **Place Single Outbound Test Call** card, fill in the **Phone Number** and **Operator Name**.
  2. Click **Place Real Test Call**.
  3. A browser confirmation pop-up will require you to type **LIVE CALL**.

### 4. Executing Campaign Outbound Dialer pacing tick
- **CLI**:
  ```bash
  python scripts/run_outbound_dialer_once.py --campaign-id <CAMPAIGN_ID> --no-dry-run --live-mode --confirm "LIVE CALL"
  ```
- **Web UI**:
  1. Select a Campaign from the campaign dropdown/list.
  2. Locate the **Run Dialer Pacing Tick** card.
  3. Check the **Live Dial** checkbox (this automatically unchecks and disables Dry Run mode).
  4. Type **LIVE CALL** in the red warning confirmation input box.
  5. Fill in the **Operator ID**.
  6. Click **Execute Dialer Tick**.

---

## Emergency Failsafes

### Kill Switch Procedure (Emergency Stop)
If live dialer needs to be stopped immediately:
1. **Clear Environment Toggles**:
   Change environment variables:
   ```bash
   TELEPHONY_LIVE_MODE=false
   DANA_ENABLE_OUTBOUND_DIALER=false
   ```
   Restart all daemon services.
2. **Stop Campaigns**:
   Set campaign status to `paused` or `stopped` via CLI:
   ```bash
   python scripts/manage_telephony_campaigns.py --campaign-id <ID> --action pause --operator "Jimmy"
   ```
3. **Terminate Worker**:
   Stop `run_livekit_agent_worker.py` daemon by sending `Ctrl+C` or terminating the process.

### Rollback to Mock/Dry-Run
Simply check **Dry Run Mode** in the UI, or omit `--live-mode` when invoking CLI scripts. Ensure `TELEPHONY_LIVE_MODE` env var is removed or set to `false`.
