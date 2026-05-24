# Operator Checklist: Telnyx + LiveKit SIP Telephony Setup

This guide provides operators with step-by-step instructions for provisioning, configuring, and verifying the Telnyx carrier and LiveKit SIP/telephony infrastructure for the Dana Voice Agent.

---

## 1. Safety Gates & Environment Variables

To prevent accidental charges, unauthorized resource creation, or unintended outbound calls, all telephony scripts implement strict **Safety Gates**. By default, running any script performs a zero-network-request dry-run.

### The Safety Gate Matrix

| Flag | Required For | Effect When `yes` | Default |
|:---|:---|:---|:---|
| `DANA_CONFIRM_TELNYX_READ` | Any Telnyx GET or list API calls | Allows fetching lists of numbers, profiles, and connections. | `no` |
| `DANA_CONFIRM_TELNYX_MUTATION` | Creating/updating Telnyx connections or buying numbers | Allows creating SIP connections, outbound profiles, etc. | `no` |
| `DANA_CONFIRM_CREATE_LIVEKIT_TRUNK` | Creating LiveKit SIP Outbound Trunks | Allows registering Telnyx credentials into LiveKit Cloud. | `no` |
| `DANA_CONFIRM_PLACE_CALL` | Placing outbound test calls via LiveKit SIP | Triggers a live outbound call to the destination number. | `no` |
| `DANA_CONFIRM_TRANSFER_CALL` | Executing live prospect transfers to agents | Bridges a caller in the LiveKit room to the licensed agent. | `no` |

---

## 2. Setup Checklist

Follow these steps in sequence to provision the carrier and bridge.

### Step 1: Environment Variables Configuration

Ensure the following variables are set in `/opt/dana/.env` on the production server (never commit these to Git):

```env
# Telnyx API Keys (v2)
TELNYX_API_KEY=your_telnyx_api_key_here

# Telnyx Connection Details
TELNYX_SIP_CONNECTION_NAME=dana-sip-connection
TELNYX_OUTBOUND_PROFILE_NAME=dana-outbound-profile
TELNYX_IP_PORT_IP_ADDRESS=127.0.0.1  # Replace with the Hyperstack VM's public IP address

# Phone Numbers (E.164 format, e.g., +15551234567)
TELNYX_PHONE_NUMBER=replace_me
LICENSED_AGENT_PHONE_NUMBER=replace_me

# LiveKit SIP Configuration
LIVEKIT_URL=wss://your-livekit-project.livekit.cloud
LIVEKIT_API_KEY=your_livekit_api_key_here
LIVEKIT_API_SECRET=your_livekit_api_secret_here

# Telephony Safety Gates (change to "yes" when performing specific tasks)
DANA_CONFIRM_TELNYX_READ=no
DANA_CONFIRM_TELNYX_MUTATION=no
DANA_CONFIRM_CREATE_LIVEKIT_TRUNK=no
DANA_CONFIRM_PLACE_CALL=no
DANA_CONFIRM_TRANSFER_CALL=no
```

### Step 2: Validate Config and Dry-Run Verification

Run the provisioning script in dry-run mode to ensure that your API keys and configuration values are loaded correctly:

```bash
python -m telephony.telnyx_provision
```

*This will read environment variables and validate settings without making network requests. Results are written to `telephony/telnyx_dry_run.json`.*

### Step 3: Run Read-Only Checks

Verify that the configured Telnyx phone number actually exists and is active under your Telnyx account:

```bash
export DANA_CONFIRM_TELNYX_READ=yes
python -m telephony.telnyx_provision
```

*This checks the Telnyx API to see if the configuration matches existing credentials.*

### Step 4: Provision Telnyx Connections

Create the SIP connection and outbound profile on Telnyx. This will bind your outbound number to the SIP connection:

```bash
export DANA_CONFIRM_TELNYX_READ=yes
export DANA_CONFIRM_TELNYX_MUTATION=yes
python -m telephony.telnyx_provision
```

*Upon success, connection detail configurations and SIP credentials are saved to `telephony/telnyx_resources.json`.*

### Step 5: Register LiveKit SIP Outbound Trunk

Bridge Telnyx to LiveKit by creating the LiveKit SIP Outbound Trunk. This gives LiveKit the credentials to place outbound calls through Telnyx:

```bash
export DANA_CONFIRM_CREATE_LIVEKIT_TRUNK=yes
python -m telephony.create_livekit_telnyx_outbound_trunk
```

*This registers the trunk on LiveKit Cloud and saves details to `telephony/livekit_trunk_result.json`.*

### Step 6: Test Outbound SIP Calling

Place a test call to a destination phone number (e.g., your personal mobile number) to verify media and signaling:

```bash
export DANA_CONFIRM_PLACE_CALL=yes
python -m telephony.create_outbound_call --to +15551234567
```

*This connects to LiveKit Cloud, initiates a SIP participant, and places an outbound call through the Telnyx trunk.*

---

## 3. Monitoring and Troubleshooting

### Log Locations
All outputs from provisioning steps are written to:
- `telephony/telnyx_resources.json` (SIP connection details and credentials)
- `telephony/livekit_trunk_result.json` (LiveKit SIP Outbound Trunk ID)
- `telephony/last_outbound_call.json` (Most recent test call details)

### Common Failure Points
1. **HTTP 401 Unauthorized**: Double-check your `TELNYX_API_KEY`.
2. **Method Missing on LiveKit SDK**: Ensure `livekit-api` is upgraded (`>=1.1.8` or newer) to support SIP APIs.
3. **No Audio / One-Way Audio**: Ensure that firewall/UFW rules allow UDP ports required by SIP signaling and media, and that the `TELNYX_IP_PORT_IP_ADDRESS` matches the public IP of the host machine.
