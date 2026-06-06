# Outbound Dialer Intelligence Layer

This document outlines the architecture, logic, and compliance rules of Dana's outbound intelligence layer. This layer manages when and how leads are dialed, retried, transferred, and suppressed, optimizing connection rates while maintaining strict TCPA compliance.

---

## Architecture Overview

The outbound intelligence layer comprises the following core components under `dialer/`:

```
dialer/
├── __init__.py                  # Package exports
├── schemas.py                    # Common data structures
├── campaign_scheduler.py        # Lead & campaign eligibility checks
├── timezone_policy.py           # Local calling window and timezone logic
├── retry_policy.py              # Outcome-based retry intervals
├── caller_id_pool.py            # Caller ID selection, rotation, and metrics
├── spam_risk_monitor.py         # Spam likelihood scoring and flagging
├── voicemail_strategy.py        # Machine classification and message drop rules
├── transfer_queue.py            # Live agent routing and fallback callback queue
```

---

## 1. Campaign Scheduling & Timezone Compliance

Strict adherence to local calling times is enforced by combining the `CampaignScheduler` and `TimezonePolicy`.

### Timezone Resolution
Lead timezones are resolved progressively:
1. **Explicit callback timezone** defined on the lead.
2. **State abbreviation mapping** (e.g. `FL` -> `America/New_York`, `CA` -> `America/Los_Angeles`).
3. **Phone area code lookup** (e.g. `312` -> `America/Chicago`).

### Active Hours and TCPA Gates
- **TCPA Hard Boundaries**: Calls are strictly prohibited before **8:00 AM** local time and after **9:00 PM** local time.
- **Campaign Windows**: Campaigns can override the default window using `allowed_calling_hours` (e.g. `(9, 18)` for 9 AM to 6 PM) or explicit string ranges `calling_window_start` / `calling_window_end` (e.g. `"09:30"` to `"18:00"`).
- **Allowed Days**: The campaign scheduler verifies that the lead's local time matches the campaign's `allowed_days` list (e.g., lowercase abbreviations `["mon", "tue", "wed", "thu", "fri"]`).

---

## 2. Pacing & Retry Policy

### Retry Intervals
Retry schedules are computed deterministically based on the last call outcome and configured campaign retry rules:
- **No Answer**: Default cooldown of 2 hours (`cooldown_no_answer`).
- **Busy**: Default cooldown of 30 minutes (`cooldown_busy`).
- **Voicemail**: If `voicemail_retry_allowed` is `True`, retried after 4 hours (`cooldown_voicemail`). If `False`, the lead is marked as complete.
- **Transient Failures**: Carrier/telephony failures trigger a short retry cooldown (5-60 minutes).

### Strict Suppression
The retry policy immediately returns `None` (no retry allowed) for final outcome states:
- `dnc` (Do Not Call)
- `wrong_number`
- `hostile_refusal`
- `disconnected_bad_number`
- `consent_invalid`

Leads marked with these statuses are suppressed globally before a call is ever placed.

---

## 3. Caller ID Pool Rotation & Spam Risk Monitoring

To avoid outbound call blocking and "Spam Likely" labeling, caller IDs are rotated and monitored.

### Rotation and Selection
The `CallerIdPool` selects caller IDs using **Least-Recently-Used (LRU)** rotation while verifying:
- Status is active.
- Daily call count is below `caller_id_daily_limit`.
- Cooldown status is cleared.

### Answer Rate Optimization
The `AnswerRateOptimizer` prioritizes caller IDs that show:
- Higher historical answer rates.
- Lower DNC rates.
- Lower complaint rates.

### Spam Risk Monitor
An internal score ($0.0$ to $1.0$) is computed for each caller ID based on:
1. **Answer Rate Drop**: A relative drop of 50% or more in recent calls compared to the historical average.
2. **Short Call Hangups**: Connected calls ending in less than 10 seconds (indicating the prospect hung up due to a spam label).
3. **Complaints & DNC Requests** associated with the caller ID.

Caller IDs with risk scores above 0.7 are flagged as `high_risk` and automatically put on cooldown.

---

## 4. Voicemail Strategy

Upon detecting an answering machine (via Telnyx AMD or LiveKit VAD classification), the dialer checks campaign preferences:
- **Default**: Hang up immediately without leaving a message.
- **Pre-recorded message drop**: Stream/play a specific audio file URL (`audio_url`).
- **TTS Drop**: Read a specified message text using the text-to-speech engine.

---

## 5. Live Agent Transfer Queue

When a conversational session qualifies a prospect for a transfer:
1. The lead is placed into the `TransferQueue` and prioritized (by priority value and time entered).
2. Human agent availability is checked via the `AgentAvailabilityStore` for licensing in the target state.
3. If an agent is available, the call is bridged. Campaigns can configure a **warm bridge** (where the agent is joined first to receive a brief update) or cold transfer.
4. **Fallback Callback**: If no agent is online, the transfer queue automatically schedules a callback for the lead (default 30 minutes later) and logs the failure disposition.
