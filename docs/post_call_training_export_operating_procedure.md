# Post-Call Training Export Operating Procedure

This document describes how completed voice calls are automatically converted into redacted, intake-compatible training payloads for Dana's continuous training pipeline.

## Overview

The post-call exporter reads completed call logs or runtime objects, sanitizes and redacts any personally identifiable information (PII), and writes the training candidate JSON payload to `data/imports/post_call_payloads/`.

## Runtime Hook Integration

The hook is integrated directly inside `AgentRuntime.record_completed_call_for_training(payload)`.

> [!IMPORTANT]
> **Disabled by default**: The runtime exporter does nothing unless the following environment flag is explicitly set:
> ```bash
> DANA_ENABLE_POST_CALL_TRAINING_EXPORT=true
> ```

To enable synchronous processing (ideal for testing, but should be used with caution in live runtime to avoid network blocking), set:
```bash
DANA_RUN_SYNC_TRAINING_INTAKE=true
```

## Supported Payload Format

The exporter processes payload JSON structures containing:
- `call_id`: Unique identifier of the call.
- `started_at` & `ended_at`: Timestamp strings.
- `prospect_phone`: Phone number of the lead (redacted to `[REDACTED_PHONE]`).
- `turns`: Array of dialog turns with `speaker` ("agent" / "prospect") and `text`.

## CLI Export Command

To run a manual or script-driven export of a completed call JSON file:
```bash
python scripts/export_completed_call.py --file path/to/completed_call.json --output-dir data/imports/post_call_payloads
```

Use the `--run-intake` flag to automatically submit the exported payload to the orchestrator:
```bash
python scripts/export_completed_call.py --file path/to/completed_call.json --run-intake
```

## Security Safeguards
- **100% Local**: No external LLM, OpenAI, or database cloud APIs are triggered.
- **Redaction by default**: Emails, SSNs, credit cards, bank accounts, DOB, and Medicare MBIs are completely redacted from all turn texts.
- **Queueing only**: Mined candidates are created with `pending` status. Auto-approval is forbidden.
