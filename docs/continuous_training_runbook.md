# Dana Outbound Sales AI - Continuous Training Runbook

This document details the operational loops and safety guidelines for the outbound Final Expense AI voice agent (Dana) continuous training system.

---

## 1. System Overview

Dana's continuous training system operates on a read-only local foundation, utilizing human-in-the-loop gating for all pipeline transitions. The architecture is designed to prevent auto-approval of training data, live prompt mutations at runtime, and automatic fine-tune deployments.

---

## 2. Operating Loops

### Daily Operating Loop
1. **QA Mining**: Run `python scripts/run_daily_qa_miner.py` to identify compliance flags or high-risk transfers from the previous day's calls.
2. **Review Mining**: Operators review pending items in the `HumanReviewItem` database queue using `python scripts/review_training_items.py`.

### Weekly Operating Loop
1. **Ingest & Mine**: Import new recordings/transcripts with `python scripts/ingest_training_source.py` and extract examples with `python scripts/mine_training_examples.py`.
2. **Rebuild RAG**: Rebuild the vector context database index using `python scripts/rebuild_training_rag.py` if new approved sales materials are imported.

### Monthly Audit Loop
1. **Readiness Audit**: Executing `python scripts/run_continuous_training_readiness.py --strict` to run compliance scans.
2. **Review Audit Logs**: Verify that no unreviewed dataset exports, prompt patches, or model registrations occurred.

---

## 3. Workflow Procedures

### Ingestion
Ingest audio files, text scripts, or coaching transcripts:
```bash
python scripts/ingest_training_source.py --uri "kb/manual_lessons.txt" --title "Coaching Lesson"
```

### Labeling
Label newly ingested logs for TCPA/compliance and transfer consent:
```bash
python scripts/label_training_source.py --source-id SRC_ID --actor "System"
```

### Mining
Mines conversations for compliant examples:
```bash
python scripts/mine_training_examples.py --source-id SRC_ID --stage "opening"
```

### Approving Review Items
New mined examples are staged as `HumanReviewItem` records. Approve or reject manually:
```bash
python scripts/review_training_items.py approve --item-id ID --reviewer "Braden"
```

### Rebuilding RAG Index
To compile approved documents into the vector retrieval database:
```bash
python scripts/rebuild_training_rag.py
```

### Evaluation Cases
Run the local suite of offline evaluation scenarios:
```bash
python scripts/run_eval_cases.py --mode static
```

### Transcript Replay
Replay historical audio transcript turns locally to catch regressions:
```bash
python scripts/replay_transcripts.py --mode static
```

### Prospect Simulation
Simulate conversational paths with default personas without LLM provider APIs:
```bash
python scripts/run_prospect_simulations.py --mode static
```

### Prompt Patch Generation
Draft patches for Dana's master prompt version:
```bash
python scripts/generate_prompt_patches.py --instructions "Update opening objection reply" --actor "Jimmy"
```

### Prompt Patch Preview
Compile and audit draft patches safely inside a preview file:
```bash
python scripts/preview_prompt_patch.py --patch-id PATCH_ID --output-file "data/prompt_previews/patch_preview.md"
```

### Canary Rollouts
1. Create canary plan:
   ```bash
   python scripts/manage_canary_rollout.py create --prompt-version-id VERSION_ID --traffic-percent 10
   ```
2. Start canary (requires `DANA_ENABLE_PROMPT_CANARY=true` flag):
   ```bash
   python scripts/manage_canary_rollout.py start --experiment-id EXP_ID
   ```
3. Monitor performance and rollback alerts:
   ```bash
   python scripts/monitor_canary_rollout.py monitor --experiment-id EXP_ID
   ```

### Fine-Tuning
1. Export dataset:
   ```bash
   python scripts/export_fine_tune_dataset.py --export-name "gpt-4o-mini-ft" --min-examples 10
   ```
2. Gate dataset compliance:
   ```bash
   python scripts/gate_fine_tune_dataset.py --manifest "data/fine_tune_exports/gpt-4o-mini-ft/manifest.json"
   ```
3. Request job approval:
   ```bash
   python scripts/prepare_fine_tune_job_request.py --approval-package "data/fine_tune_approvals/gpt-4o-mini-ft/approval_package.json" --provider openai
   ```
4. Track manual job execution:
   ```bash
   python scripts/track_fine_tune_job.py record-upload --job-start-review-item-id START_ID --provider-file-id file-123
   ```

---

## 4. Operational Red Lines - WHAT MUST NEVER BE DONE MANUALLY

> [!CAUTION]
> **To prevent compliance infractions and system corruption, operators MUST NOT:**
> 1. **Bypass Human Review**: Never modify training examples directly to force fine-tune eligibility without human approval.
> 2. **Modify Master Prompt Directly**: Do not edit `prompts/final_expense_alex.md` in production directly. Always generate prompt versions, test with evals, and use canary rollout controls.
> 3. **Initiate API Fine-Tuning Directly**: The system is read-only for LLM API calls. Never execute `openai.fine_tuning.jobs.create` or run direct uploads using provider scripts inside production.
> 4. **Auto-Promote Prompt Candidates**: Never route live traffic to candidate prompt versions without passing canary monitoring safety checks.
