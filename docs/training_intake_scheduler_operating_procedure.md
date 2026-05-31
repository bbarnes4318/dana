# Training Intake Scheduler Operating Procedure

This document describes how to configure, execute, and monitor the automated scheduler that triggers Dana's training intake runs.

## Overview

The intake scheduler runs the training intake orchestrator loop safely on a configured schedule (once, daily, or hourly). It aggregates scanned standard folders, processescompleted post-call drops, runs down-stream labeler/miner routines, triggers Daily QA logs analysis, and generates diagnostic reports.

## Process Exclusion Locking

The scheduler implements filesystem-level mutual exclusion locking to prevent concurrent scheduler instances from running. 

- **Lock Path**: `data/intake_reports/intake_scheduler.lock`
- **Behavior**: Writes the current process PID. If the lock file exists and the associated process is running, new instances fail-closed and print:
  ```json
  {
    "error": "Lock conflict",
    "warnings": ["Scheduler lock already exists."]
  }
  ```

## CLI Usage

To run a single automated scan and QA miner run:
```bash
python scripts/run_training_intake_scheduler.py --mode once
```

To run as a daemon loop executing once every hour (up to 24 times for example):
```bash
python scripts/run_training_intake_scheduler.py --mode hourly --max-runs 24 --sleep-seconds 3600
```

To test without saving records or writing reports, append `--dry-run`:
```bash
python scripts/run_training_intake_scheduler.py --dry-run
```

## Recommended Production Automation

### Cron (Linux/macOS)
To run once every night at 1:00 AM:
```bash
0 1 * * * cd /opt/dana && DANA_DATA_DIR=/opt/dana/data python scripts/run_training_intake_scheduler.py --mode once --json-only >> /var/log/dana_intake_scheduler.log 2>&1
```

### Windows Task Scheduler (Windows)
Create a task running daily:
- **Program/Script**: `python`
- **Arguments**: `scripts/run_training_intake_scheduler.py --mode once --json-only`
- **Start in**: `C:\Users\jimbo\.gemini\antigravity\worktrees\ultimate-voice\implement-canary-rollout-system`

## Security Safeguards
- **100% Offline**: No network requests to OpenAI or other providers are made.
- **Pending Review Items only**: Candidates are registered as `pending` under `HumanReviewItem` schemas. No auto-approval is performed.
- **No Prompt Modification**: Production final expense prompts remain immutable.
