# DANA Cost-Based Routing Engine

This document details how the Routing Engine selects active providers and optimizes costs without compromising quality or latency.

## Routing Engine Modes

### 1. Locked Mode (`locked`)
Strictly enforces configured provider selections. If any selected provider fails its health check (e.g. invalid credentials, service offline), **startup fails immediately with a `RuntimeError`**.
> [!IMPORTANT]
> This mode is recommended for production campaigns where exact provider consistency is required.

### 2. Balanced Mode (`balanced`)
* Prefers premium cloud APIs (Deepgram STT, ElevenLabs TTS) if they are online and valid credentials exist.
* Automatically falls back to local alternatives (Local Whisper STT, Local Kokoro TTS) if cloud health checks fail or credentials are omitted.

### 3. Cheapest Safe Mode (`cheapest_safe`)
Iterates over all healthy providers and selects the option with the lowest estimated cost per minute.
* Under normal circumstances, this selects local models since they cost $0.00 to run.
* If local GPU servers are overloaded/unhealthy, it falls back to cloud providers.

---

## Cost Tracking & Logging
On session startup, the resolved provider cost per minute is calculated and logged:
`ESTIMATED_COST_PER_CONNECTED_MINUTE: 0.270000`

Post-call, the actual duration and token/character usage are exported to the repository to track campaign expenses.
