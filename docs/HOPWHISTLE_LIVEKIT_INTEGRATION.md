# Interface Contract: Hopwhistle LiveKit Integration

This document outlines the architecture, room naming conventions, and webhook/WebRTC event schemas required to integrate the **Hopwhistle Browser Monitoring Dashboard** with the **Dana Sovereign Voice Agent** running on LiveKit Cloud.

---

## 1. Architectural Overview

Hopwhistle enables browser-based supervisors and licensed insurance agents to monitor calls in real-time and join conversations. 

```
               +----------------------+
               |     LiveKit Cloud    |
               +----------+-----------+
                          |
             +------------+------------+
             |                         |
+------------v------------+ +----------v------------+
|    Dana Voice Agent     | |  Hopwhistle Browser   |
|   (Hyperstack VM)       | |  (Agent Dashboard)     |
+-------------------------+ +-------------------------+
```

1. **Passive Monitoring**: Licensed agents can listen to the audio stream of ongoing calls directly from their browser dashboard.
2. **Warm Transfer / Active Join**: A licensed agent can click "Join Call" in the Hopwhistle UI, entering the WebRTC room. When the voice agent detects their presence, it automatically introduces the agent and silences/disconnects itself.

---

## 2. Room Naming & Participant Identity Conventions

To ensure correct state tracking and routing, the following conventions are strictly enforced:

### Room Names
LiveKit Rooms are named using the unique `call_id` of the outbound or inbound call:
* Format: `<call_id>` (e.g., `d68606df-6dc4-4318-9621-91267dab41ca`)

### Participant Identities
Each room participant must identify themselves using a prefixed format:
1. **The Voice Agent**: `agent_<agent_name>` (e.g., `agent_alex`)
2. **The Prospect (Telephone Caller)**: `prospect_<phone_number>` (e.g., `prospect_+15551234567`)
3. **The Licensed Agent (Hopwhistle WebRTC client)**: `human_agent_<agent_id>` (e.g., `human_agent_usr_90a1b2`)
4. **The Supervisor (Silent Listener)**: `supervisor_<user_id>` (e.g., `supervisor_usr_123456`)

---

## 3. Real-Time Event Webhooks

When major state changes occur, Dana dispatches webhook notifications to the Hopwhistle server:

### Endpoint
`POST /api/webhooks/call-events`

### Events

#### A. Call Started (`call.started`)
Fired when a new LiveKit room is established and the prospect connects.
```json
{
  "event": "call.started",
  "call_id": "d68606df-6dc4-4318-9621-91267dab41ca",
  "room_name": "d68606df-6dc4-4318-9621-91267dab41ca",
  "prospect_phone": "+15551234567",
  "timestamp": "2026-05-24T15:00:00Z"
}
```

#### B. Prospect Qualified (`call.qualified`)
Fired when the prospect completes all qualification criteria (Interest, Age, Independent Living, Decision Maker).
```json
{
  "event": "call.qualified",
  "call_id": "d68606df-6dc4-4318-9621-91267dab41ca",
  "qualification": {
    "interest_confirmed": true,
    "age_range_confirmed": true,
    "living_independently": true,
    "financial_decision_maker": true
  },
  "timestamp": "2026-05-24T15:04:30Z"
}
```

#### C. Transfer Requested (`call.transfer_requested`)
Fired when the `feTransfer` tool is invoked.
```json
{
  "event": "call.transfer_requested",
  "call_id": "d68606df-6dc4-4318-9621-91267dab41ca",
  "prospect_name": "John Doe",
  "summary": "Qualified lead: age 65, lives independently, financial decision maker.",
  "reason": "Lead qualified and ready for licensed agent transfer",
  "timestamp": "2026-05-24T15:04:35Z"
}
```

#### D. Call Ended (`call.ended`)
Fired when the room is destroyed and all participants leave.
```json
{
  "event": "call.ended",
  "call_id": "d68606df-6dc4-4318-9621-91267dab41ca",
  "duration_seconds": 215,
  "summary": "Completed qualification attempt.",
  "timestamp": "2026-05-24T15:05:15Z"
}
```

---

## 4. LiveKit WebRTC Token Generation

Hopwhistle client apps must request a connection token from the Hopwhistle server API. The server generates this token using the LiveKit Server SDK.

### Required Permissions by Role

1. **Licensed Agent (WebRTC Call Join)**:
   * Room: `<call_id>`
   * Permissions: `join: true`, `publish_audio: true`, `publish_video: false`, `subscribe: true`
2. **Supervisor (Silent Monitoring)**:
   * Room: `<call_id>`
   * Permissions: `join: true`, `publish_audio: false`, `publish_video: false`, `subscribe: true`

---

## 5. Voice Agent Response to Human Join

To support seamless transitions, the Voice Agent (`DanaAgent` / `main.py`) monitors participant events inside the room:

```python
@session.on("participant_connected")
def on_participant_connected(participant):
    identity = participant.identity
    if identity.startswith("human_agent_"):
        logger.info(f"Licensed agent joined the room: {identity}")
        # 1. Immediately play handoff message to prospect
        asyncio.create_task(session.say(
            "I'm connecting you to our benefits coordinator now. They will take it from here."
        ))
        # 2. Schedule voice agent exit after message finishes playing
        asyncio.create_task(exit_room_after_delay(delay_seconds=3))
```
This native event listening ensures handoffs feel natural and reliable.
