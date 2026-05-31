# Dana Training Intake Operating Procedure

This document defines the standard operating procedures for Dana's **Automatic Training Intake Orchestrator**. The intake orchestrator is the gateway that bridges live-call outputs and manually curated training notes with the continuous offline training system.

---

## 1. What Training Intake Does

The Training Intake Orchestrator automatically ingests completed calls, transcripts, feedback, and notes from multiple import directories, normalizes them, redacts personally identifiable information (PII), checks for duplication, and maps them to raw `TrainingSource` records in the database.

Once a source is ingested, the system automatically runs the deterministic labeler and the example miner to extract training candidates, compliance review tasks, and evaluation regression cases. These are registered as pending `HumanReviewItem` records for administrator review.

### Call Intake vs. Added-Material Intake

1. **Call Intake (Post-Call payload)**: Processes raw LiveKit/AgentRuntime conversation outputs (turns, timestamps, call results) after a phone call completes. Normalizes speakers, redacts phone numbers/emails, and structures them for continuous mining.
2. **Added-Material Intake (Files/Folders)**: Processes manually drop-loaded material (YouTube training transcripts, manager notes, licensed-agent feedback emails) in bulk. This allows training models on real-world edge cases.

---

## 2. Standard Folder Structure

Intake activities are centered around standard input subdirectories under the `data/imports/` directory:

| Input Directory | Inferred Source Type | Target Material |
| --- | --- | --- |
| `data/imports/call_transcripts/` | `call_transcript` | Text or JSON files of historical call transcriptions. |
| `data/imports/youtube_training/` | `youtube` | Text transcripts of coaching or sales strategy videos. |
| `data/imports/manager_notes/` | `manager_note` | Curated observations, strategies, or scripts from team managers. |
| `data/imports/licensed_agent_feedback/` | `licensed_agent_feedback` | Corrections, edits, or feedback notes from licensed agents. |
| `data/imports/post_call_payloads/` | `post_call` | Completed call JSON payloads dropped from telephony hooks. |
| `data/imports/manifests/` | `manifest` | Batch processing manifest JSON lists. |

### Processed / Failed Directories
When `--move-processed` is enabled, the orchestrator handles file movement to prevent reprocessing:
*   **Processed Folder**: `data/imports/processed/`
*   **Failed Folder**: `data/imports/failed/`

---

## 3. Supported File Types

The orchestrator reads files matching these extensions:
1.  `.txt` - Plain text transcripts or notes. Supports `Speaker: Content` colon parsing.
2.  `.json` - JSON-structured transcripts containing turn arrays or post-call details.
3.  `.jsonl` - JSON Lines format, with each line representing an independent call turn.
4.  `.md` - Markdown notes or scripts.

---

## 4. Ingestion Commands & Procedures

### Ingesting Post-Call Payloads

Telephony or call runtime hooks save payload JSON dumps to `data/imports/post_call_payloads/call_123.json` then invoke the orchestrator CLI:

```bash
python scripts/run_training_intake.py post-call --file data/imports/post_call_payloads/call_123.json
```

### Ingesting In-Bulk Folders

To ingest all historical call transcripts dropped in a folder:

```bash
python scripts/run_training_intake.py folder --path data/imports/call_transcripts --type call_transcript
```

You can target any other folder by changing `--path` and `--type`:
```bash
# Ingest YouTube script dumps
python scripts/run_training_intake.py folder --path data/imports/youtube_training --type youtube

# Ingest Manager notes
python scripts/run_training_intake.py folder --path data/imports/manager_notes --type manager_note

# Ingest Licensed Agent feedback
python scripts/run_training_intake.py folder --path data/imports/licensed_agent_feedback --type licensed_agent_feedback
```

### Ingesting Manifest Queues

To process a batch of items listed in a manifest JSON:

```bash
python scripts/run_training_intake.py manifest --file data/imports/manifests/batch_001.json
```

**Manifest Format example**:
```json
{
  "items": [
    {
      "type": "call_transcript",
      "file": "data/imports/call_transcripts/call_001.json",
      "title": "Call 001"
    },
    {
      "type": "post_call",
      "payload": {
        "call_id": "call_123",
        "turns": [
          {"speaker": "prospect", "text": "how much is it"},
          {"speaker": "agent", "text": "I can connect you to a licensed agent who handles that."}
        ]
      }
    }
  ]
}
```

### Running Daily Automation

To run the full automated sweep of all configured folders, process any pending drops, check DNC/wrong-number compliance triggers, and run the daily QA miner report:

```bash
python scripts/run_training_intake.py daily --daily-qa --move-processed
```

---

## 5. Downstream Processing

After a source is successfully ingested:
1.  **Labeling**: The `TranscriptLabeler` deterministic rules analyze turns for sentiment, call stage (Opening, Interest Check, Age Range, etc.), compliance risks, and example candidates.
2.  **Mining**: The `TrainingExampleMiner` extracts candidates based on the labels. It automatically creates pending `HumanReviewItem` records for:
    *   **training_example**: Approved positive turns suitable for prompts/RAG/fine-tuning.
    *   **failure_example**: Conversational errors or rule violations for coaching and test regression.
    *   **compliance_review**: Critical compliance violations (exact pricing quotes, licensed claims, DNC talking).
    *   **eval_case**: Regression test fixtures containing expected inputs, must_include, and must_not_include lists.

### Where Mined Items Appear

All candidates appear in the database in the `human_review_items` table with `status="pending"`. 

Operators can inspect them via:
```bash
python scripts/review_training_items.py list
```

---

## 6. Safety Constraints (What is NOT Automatic)

> [!WARNING]
> While training ingestion and mining pipeline loops are automated, the safety layer requires strict boundaries.
> 
> *   **NO Auto-Approval**:Mined examples are **never** approved automatically. A human reviewer must explicitly inspect, verify compliance compliance, and mark the item as `"approved"`.
> *   **NO Live Prompt Edits**: The system **never** writes to, patches, or alters `prompts/final_expense_alex.md` automatically. Prompt changes are generated as previews that require manual approval, CI testing, and canary rollouts.
> *   **NO Auto-Fine-Tuning**: Datasets are never automatically exported, uploaded, or fine-tuned. The system only prepares request packages for manual operator execution.
> *   **NO Auto-Deployment**: Model checkpoints are never auto-activated or set live. They must first pass offline evals and a monitored canary test.
