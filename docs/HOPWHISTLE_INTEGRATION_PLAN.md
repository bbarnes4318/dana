# DANA - Hopwhistle Integration Plan

This document outlines the integration design between the **Dana Sovereign Voice Stack** and the **Hopwhistle Outbound Dialer / Campaign Coordinator**.

## Integration Architecture

Hopwhistle acts as the control plane for campaigns, scheduling lead dials and routing active connections. Dana acts as the media plane, running voice AI models, managing conversation turns, qualifying leads, and executing telephony transfers.

```
+--------------------+            Webhooks (CRM / API)           +-----------------------+
|                    | ----------------------------------------> |                       |
|   Hopwhistle       |                                           |  Dana Voice Stack     |
|   Control Plane    | <---------------------------------------- |  Runtime (Media)      |
|                    |         Pushes Call Outcome Logs          |                       |
+--------------------+                                           +-----------------------+
```

## Key Integration Points

### 1. CRM Webhooks & suppression Lists
* **Suppression Checks**: Hopwhistle checks the global DNC registry and active suppression lists before triggering a dial.
* **Consent Checks**: Dana queries lead eligibility during the interest check stage and only performs transfers if explicit transfer consent is confirmed.

### 2. CRM Event Outbox Draining
* Dana uses a background outbox worker (`integrations.crm_webhooks.start_webhook_outbox_worker`) running on the active repository.
* Events like `lead.open_to_review`, `lead.qualified`, and `call.completed` are saved to the local store and drained to Hopwhistle's webhook receiver asynchronously.
* This isolates media path latency from external API response times.

### 3. Call Log & Cost Export
* Upon call termination, Dana's `PostCallExporter` extracts final call stats, including:
  * Total duration.
  * Number of conversational turns.
  * Total computed provider costs.
  * Transcripts and compliance scores.
* Hopwhistle ingests these stats via API to adjust bid rates and allocate billing costs per campaign.
