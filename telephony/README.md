# Dana Telephony Integration (Telnyx + LiveKit SIP)

This directory contains the integration codebase and scripts for orchestrating SIP-based outbound calling and licensed-agent transfers for Dana.

## Architecture & Carrier Restrictions

- **Exclusive Carrier:** Telnyx is the exclusive VOIP and SIP trunk provider for this application. No other carriers (e.g., Twilio, Plivo, SignalWire) or middleman services (e.g., Vapi) are permitted.
- **Media Bridge:** LiveKit Cloud acts as the WebRTC media bridge. The outbound call is routed from LiveKit Cloud through a LiveKit outbound SIP trunk, connecting directly to Telnyx's SIP address (`sip.telnyx.com`).
- **AI Stack Location:** All resource-heavy operations (Speech-to-Text via Whisper, Text-to-Speech via Kokoro, and LLM inference via vLLM) run locally on the Hyperstack GPU server.

---

## File Registry & Purposes

1. **`telnyx_config.py`**
   - Type-safe configuration loader for all environmental parameters. Redacts credentials and private API keys from logs and standard representation output.
2. **`telnyx_livekit_config.py`**
   - Alias configuration wrapper to prevent import path mismatch errors.
3. **`telnyx_api.py`**
   - REST API v2 client for Telnyx. Manages owned phone numbers, credential connections, outbound profiles, and available number searches.
4. **`telnyx_provision.py`**
   - Provisioning engine to automatically configure and verify credential connections, SIP users, and outbound numbers on Telnyx. Outputs resources to `telephony/telnyx_resources.json`.
5. **`create_livekit_telnyx_outbound_trunk.py`**
   - Provisioning script to register the Telnyx outbound SIP trunk with LiveKit Cloud. Outputs metadata to `telephony/livekit_trunk_result.json`.
6. **`create_outbound_call.py`**
   - Call initiator script to place outbound calls through LiveKit. Spawns a SIP participant inside a dynamically generated LiveKit room. Outputs details to `telephony/last_outbound_call.json`.
7. **`fe_transfer.py`**
   - Core transfer function mapping logic for licensed-agent handoffs, incorporating callback schedule fallbacks on failure.

---

## Safety Control Gates

All scripts operate under strict safety gates to prevent accidental billing, resource provisioning, or outbound dialing. Real mutations require specific environment variables to be set to `yes`:

| Variable | Default | Purpose |
|---|---|---|
| `DANA_CONFIRM_TELNYX_READ` | `no` | Allow read-only (GET/list) calls to the Telnyx API to inspect account status. |
| `DANA_CONFIRM_TELNYX_PROVISION` | `no` | Allow running the Telnyx resource provisioning logic. |
| `DANA_CONFIRM_TELNYX_MUTATION` | `no` | Allow creating/updating connections or profiles on Telnyx. |
| `DANA_CONFIRM_PURCHASE_NUMBER` | `no` | Allow buying a new phone number on Telnyx. |
| `DANA_CONFIRM_CREATE_LIVEKIT_TRUNK` | `no` | Allow creating an outbound SIP trunk in LiveKit Cloud. |
| `DANA_CONFIRM_PLACE_CALL` | `no` | Allow placing real outbound calls through the LiveKit SIP API. |
| `DANA_CONFIRM_TRANSFER_CALL` | `no` | Allow transferring calls or bridging live calls to licensed agents. |

If these variables are not set to `yes`, all operations fall back to a dry-run / non-mutating logging mode, and **do not make active network mutation requests**.

---

## Telnyx Connection ID vs Telnyx Call Control ID vs LiveKit SIP IDs

- **`TELNYX_CONNECTION_ID`**: A static Telnyx connection/app config ID. Used for routing SIP credentials.
- **`TELNYX_OUTBOUND_NUMBER`**: The caller ID/DID used for placing calls.
- **`call_control_id`**: A unique identifier created per-call ONLY when using the Telnyx Call Control API/webhooks. It is required for carrier-level cold transfers via Telnyx.
- **LiveKit Room / SIP IDs**: Dana currently places outbound calls through LiveKit SIP, which stores identifiers like `room_name`, `participant_identity`, and `sip_participant_id`. Because these calls go through LiveKit, there is no active Telnyx `call_control_id` available to our application.
- **Production Routing**: Use `warm_bridge` (DANA_TRANSFER_MODE=warm_bridge) for production transfer. This routes the licensed agent as a new SIP participant into the existing LiveKit room. Do not use `cold_transfer` (which requires Telnyx `call_control_id`) unless/until the app is explicitly switched to direct Telnyx Call Control.
