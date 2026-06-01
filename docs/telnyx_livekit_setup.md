# Telnyx + LiveKit Infrastructure Integration Setup

This guide describes how to configure Telnyx carrier connections and route SIP trunks through LiveKit to enable outbound voice dialing.

## Recommended Architecture

```
  Telnyx Number / SIP Connection (Carrier Gateway)
  ──> livekit.sip.outbound_trunk (LiveKit Gateway)
  ──> LiveKit Room (CreateSIPParticipant API)
  ──> Dana Agent (AgentRuntime worker client)
```

1. **Telnyx Role**: Owner of phone numbers, caller IDs, and PSTN SIP trunking routing.
2. **LiveKit Role**: Media room orchestrator and SIP trunk gateway. SIP trunks are provisioned in LiveKit with credentials to route directly to Telnyx carriers.
3. **Dana Role**: Joins the WebRTC room as a virtual participant, streaming real-time audio and listening to the prospect.

## Environment Variables

For live outbound dialing, set these keys in the environment:

```bash
# Telephony Provider Configuration
DANA_TELEPHONY_PROVIDER=telnyx
TELNYX_API_KEY=your_telnyx_api_key
TELNYX_DIDS=+15551234567
TELNYX_OUTBOUND_CALLER_ID=+15551234567

# Telephony Live Mode Flags
TELEPHONY_LIVE_MODE=true
DANA_ENABLE_OUTBOUND_DIALER=true

# LiveKit Credentials
LIVEKIT_URL=wss://your-livekit-project.livekit.cloud
LIVEKIT_API_KEY=devkey-your-api-key
LIVEKIT_API_SECRET=secret-your-api-secret

# Outbound Trunk and Caller Configuration
LIVEKIT_SIP_OUTBOUND_TRUNK_ID=st_your_livekit_sip_trunk_id
DANA_OUTBOUND_CALLER_ID=+15551234567

# Optional Worker and Call settings
DANA_AGENT_WORKER_ENABLED=true
DANA_WAIT_UNTIL_ANSWERED=true
DANA_KRISP_ENABLED=true
```

> [!WARNING]
> If `TELEPHONY_LIVE_MODE` and `DANA_ENABLE_OUTBOUND_DIALER` are not set to `true`, the dialer runs strictly in dry-run/mock mode and will not perform external LiveKit API requests.

---

## Setup Instructions

### 1. Configure Telnyx SIP Connection
1. In the Telnyx Portal, create a new credentials-based or IP-address based **SIP Connection**.
2. Retrieve the SIP username and password (for credential connections).
3. Associate your purchased phone numbers with the newly created SIP connection.
4. Route outbound profile rules so that Calls to the PSTN are allowed.

### 2. Configure LiveKit SIP Outbound Trunk
1. Register an outbound SIP trunk in LiveKit referencing the Telnyx credentials.
   You can register a trunk using `telephony/create_livekit_telnyx_outbound_trunk.py`.
2. Store the resulting `livekit_sip_outbound_trunk_id` in the environment (`LIVEKIT_SIP_OUTBOUND_TRUNK_ID`) or provider config in the DB.

### 3. Run Live Telephony Readiness Diagnostics
Audit your environment and provider settings before initiating any outbound calls:
```bash
python scripts/check_live_telephony_readiness.py
```
This checks for env flags, credentials, package imports, and database campaign validity.

### 4. Start the Agent Worker Daemon
The worker daemon listens to LiveKit room requests and starts the voice model:
```bash
python scripts/run_livekit_agent_worker.py
```
Ensure dependencies from `requirements.txt` are fully installed.

### 5. Execute a Single Test Call
Run a manual test call to confirm SIP ringing and participant connection:
```bash
python scripts/test_live_outbound_call.py --to +15555550000 --operator "Jimmy" --confirm "LIVE CALL"
```
The `--confirm "LIVE CALL"` flag is strictly required to prevent accidental dials.

### 6. Run Campaign Outbound Dialer pacing tick
To dial queued leads under a running campaign:
```bash
python scripts/run_outbound_dialer_once.py --campaign-id <CAMPAIGN_ID> --no-dry-run --live-mode --confirm "LIVE CALL"
```

---

## Verification & Troubleshooting

### A. How to verify the phone rings
- Ensure the destination number is correct (E.164 formatting e.g., `+1` followed by 10 digits).
- Check the LiveKit SIP Trunk settings if you receive Twirp `SIP_DIAL_FAILED` or local carrier busy/rejected tones.

### B. How to verify the LiveKit room has the SIP participant
- Log into your LiveKit Cloud Dashboard.
- Find active rooms prefixing `dana-`.
- Check room details: a participant named `"Test Caller"` or `"Dana Outbound Test Call"` with an active SIP trunk ID should be present.

### C. How to verify the Dana worker joined the room
- Inspect the agent worker logs (`python scripts/run_livekit_agent_worker.py`).
- You should see lines indicating `New connection: room=dana-...` followed by STT/TTS prewarming.

### D. Troubleshooting SIP errors
- **Twirp Error: invalid_argument**: Check if `sip_trunk_id` or room templates are empty.
- **SIP Status 403 Forbidden**: Telnyx credentials or IP authentication mapping in the SIP Connection is misconfigured.
- **SIP Status 404 Not Found**: The destination phone number is invalid or route profile rules are incorrect.
- **SIP Status 486 Busy**: Recipient is busy or has blocked incoming VoIP calls.
