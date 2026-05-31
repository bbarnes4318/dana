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
# Telephony Live Mode Flags
TELEPHONY_LIVE_MODE=true
DANA_ENABLE_OUTBOUND_DIALER=true

# LiveKit Credentials
LIVEKIT_URL=wss://your-livekit-project.livekit.cloud
LIVEKIT_API_KEY=devkey-your-api-key
LIVEKIT_API_SECRET=secret-your-api-secret
```

> [!WARNING]
> If `TELEPHONY_LIVE_MODE` and `DANA_ENABLE_OUTBOUND_DIALER` are not set to `true`, the dialer runs strictly in dry-run/mock mode and will not perform external LiveKit API requests.

## Setup Instructions

### 1. Configure Telnyx SIP Connection
1. In the Telnyx Portal, create a new **IP-address based** or **Credentials-based** SIP Connection.
2. Set the routing destination IP/FQDN to match LiveKit SIP Trunk gateway coordinates.
3. Assign inbound phone numbers to the connection.

### 2. Configure LiveKit SIP Outbound Trunk
1. Register an outbound SIP trunk in LiveKit referencing the Telnyx SIP Trunk address.
2. Store the resulting `livekit_sip_outbound_trunk_id` in the `TelephonyProviderConfig`.
3. Set room naming templates to structure temporary session IDs.

### 3. Outbound Dialer Pacing Loop
1. Ensure the campaign has leads loaded and is marked as `running`.
2. Schedule a cron or dialer worker to trigger `python scripts/run_outbound_dialer_once.py --no-dry-run --live-mode`.
3. Check the CLI output for SIP response statuses.
