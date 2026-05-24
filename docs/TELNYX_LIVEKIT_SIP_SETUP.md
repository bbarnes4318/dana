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

### 2. Setup Checklist & Orchestrator Workflow

To provision your telephony infrastructure end-to-end, use the unified orchestrator script:
```bash
python telephony/provision_telnyx_livekit.py
```

It operates in three distinct modes, controlled by the `DANA_PROVISION_MODE` environment variable.

### Step 1: Environment Variables Configuration

Set up your environment variables on the host (never commit these to Git):
```env
# Credentials
TELNYX_API_KEY=your_telnyx_api_key_here
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_livekit_api_key_here
LIVEKIT_API_SECRET=your_livekit_api_secret_here

# Optional/Overrides (omit to auto-discover/create)
TELNYX_CONNECTION_ID=...
TELNYX_OUTBOUND_VOICE_PROFILE_ID=...
TELNYX_PHONE_NUMBER_ID=...
TELNYX_OUTBOUND_NUMBER=...
TELNYX_SIP_USERNAME=...
TELNYX_SIP_PASSWORD=...
LIVEKIT_SIP_OUTBOUND_TRUNK_ID=...

# Purchase parameters (required if DANA_CONFIRM_PURCHASE_NUMBER=yes)
TELNYX_PURCHASE_COUNTRY=US
TELNYX_PURCHASE_AREA_CODE=512
```

### Step 2: Run a Dry-Run Plan
Ensure all safety gates are checked and show planned mutations without making any network calls:
```bash
DANA_PROVISION_MODE=plan \
python telephony/provision_telnyx_livekit.py
```

### Step 3: Run Read-Only Inspection
Verify existing resources on your Telnyx and LiveKit accounts:
```bash
DANA_PROVISION_MODE=inspect \
DANA_CONFIRM_TELNYX_READ=yes \
python telephony/provision_telnyx_livekit.py
```

### Step 4: Apply Provisioning (Mutations)
To perform actual resource mutations and create the LiveKit outbound SIP trunk, run:
```bash
DANA_PROVISION_MODE=apply \
DANA_CONFIRM_TELNYX_READ=yes \
DANA_CONFIRM_TELNYX_MUTATION=yes \
DANA_CONFIRM_CREATE_LIVEKIT_TRUNK=yes \
DANA_PROVISION_APPLY_CONFIRM=yes \
python telephony/provision_telnyx_livekit.py
```
*(If purchasing a number is needed, also set `DANA_CONFIRM_PURCHASE_NUMBER=yes` and `TELNYX_PURCHASE_COUNTRY=US`).*

### Step 5: Test Outbound SIP Calling
Once provisioned successfully, place a test call:
```bash
export DANA_CONFIRM_PLACE_CALL=yes
python -m telephony.create_outbound_call --to +15551234567
```

---

## 3. Orchestrator Outputs & Permissions

When apply mode succeeds, the following files are generated:
1. **`telephony/provisioned.env`** (or **`/opt/dana/.env.telephony`** if running on the server):
   Contains the real credentials and IDs needed by the Dana server. This file is automatically set to `chmod 600` permissions.
2. **`telephony/provisioned_resources.json`**:
   Contains non-sensitive metadata (masked outbound numbers, statuses, and IDs). No secrets are written here.

---

## 4. Monitoring and Troubleshooting

### Log Locations
All outputs from provisioning steps are written to:
- `telephony/provisioned_resources.json` (Non-sensitive resource metadata)
- `telephony/provisioned.env` (or `/opt/dana/.env.telephony` on server - real credentials)
- `telephony/last_outbound_call.json` (Most recent test call details)

### Common Failure Points
1. **HTTP 401 Unauthorized**: Double-check your `TELNYX_API_KEY`.
2. **Method Missing on LiveKit SDK**: Ensure `livekit-api` is upgraded (`>=1.1.8` or newer) to support SIP APIs.
3. **No Audio / One-Way Audio**: Ensure that firewall/UFW rules allow UDP ports required by SIP signaling and media, and that the `TELNYX_IP_PORT_IP_ADDRESS` matches the public IP of the host machine.
4. **Telnyx SIP password could not be retrieved**: If reusing an existing connection, Telnyx does not expose the password. You must provide `TELNYX_SIP_USERNAME` and `TELNYX_SIP_PASSWORD` in the environment.

---

## 4. Integration Verification Disclaimer

> [!WARNING]
> Mocked unit tests (e.g. running under pytest) verify code flow, safety gates, and static structure but do **not** prove real LiveKit/Telnyx integration.
> 
> Real SDK shape and import verification must be executed and pass on the Hyperstack production server (where the real dependencies are installed) using the following verification scripts:
> - `python scripts/verify_livekit_sdk_shape.py`
> - `python scripts/verify_livekit_runtime_imports.py`
> 
> If either script exits with code `1` on the Hyperstack server, deployment is blocked.

