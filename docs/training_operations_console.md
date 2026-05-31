# Dana Continuous Training Operations Console

The Training Operations Console provides a safe operator service layer and CLI utility for administering Dana’s outbound Final Expense continuous training lifecycle offline.

For the browser-based dashboard interface, refer to the [Web Console Operating Procedure](file:///C:/Users/jimbo/.gemini/antigravity/worktrees/ultimate-voice/dana-training-ops-console/docs/training_web_console_operating_procedure.md).

---

## 🚨 Critical Safety Warnings (Strict Enforcement)

> [!CRITICAL]
> **OPERATIONAL SAFETY BOUNDARIES:**
> 1. **No Auto-Approval:** The console never automatically approves training data. All mined coaching items remain strictly in `pending` status until reviewed by a authorized operator.
> 2. **No Live Prompt Mutations:** Under no circumstances does this console write or edit `prompts/final_expense_alex.md` or any active runtime prompt files directly.
> 3. **No Provider Uploads:** The system contains zero code to upload data to OpenAI, Azure, or any other external API providers.
> 4. **No Fine-Tune Jobs:** Running fine-tuning runs must be recorded manually via manual tracking records; the console does not call API endpoints to start fine-tune runs.
> 5. **No Live Deployment:** The console has no deployment capability. Canary promotion and routing are governed strictly by the `CanaryManager` and required environment flags.

---

## What the Console Does
- **Aggregates Summary Metrics:** Lists and counts pending items, recent prompt versions, canaries, and manual job records in one view.
- **Administers Review Queues:** Exposes methods to list pending items, show item payloads, and approve, reject, or request changes on items through `HumanReviewService`.
- **Orchestrates Ingestion and Intake:** Runs folder scans, manifest imports, and post-call intakes locally using the `TrainingIntakeOrchestrator`.
- **Triggers YouTube Imports:** Processes YouTube transcript text or manifests into formatted local txt files offline.
- **Executes Bounded Scheduled Runs:** Safely triggers a single intake scan pass with lock protection to prevent overlap.
- **Performs Readiness Audits:** Invokes `ContinuousTrainingReadinessAuditor` to run integrity checks.
- **Lists and Views Operator Reports:** Safely filters and reads report outputs from data subdirectories without exposing path traversal vulnerabilities.

## What the Console Does NOT Do
- Run active LLM prompts or participate in live voice calls.
- Edit production runtime prompts.
- Upload datasets or initiate job execution on cloud provider endpoints.
- Auto-promote canary configurations without human sign-off.

---

## Operational Workflows

### A. Review Queue Workflow
1. Operator requests a list of pending items:
   ```bash
   python scripts/training_console.py review list --status pending --limit 25
   ```
2. Operator views a specific item:
   ```bash
   python scripts/training_console.py review show --item-id <ITEM_ID>
   ```
3. Actions:
   - **Approve:** Generates downstream training examples or eval cases. Requiring a reviewer name.
     ```bash
     python scripts/training_console.py review approve --item-id <ITEM_ID> --reviewer "Jimmy" --notes "High quality turn."
     ```
   - **Reject:** Marks status as rejected. Notes are strictly mandatory.
     ```bash
     python scripts/training_console.py review reject --item-id <ITEM_ID> --reviewer "Jimmy" --notes "Unsafe price quoting."
     ```
   - **Needs Changes:** Moves item back to coaching workflow for remediation. Notes are mandatory.
     ```bash
     python scripts/training_console.py review needs-changes --item-id <ITEM_ID> --reviewer "Jimmy" --notes "Shorten phrase."
     ```

### B. Intake Workflow
Imports new training data into the repository:
- **Folder:**
  ```bash
  python scripts/training_console.py intake folder --path data/imports/call_transcripts --type call_transcript
  ```
- **Manifest:**
  ```bash
  python scripts/training_console.py intake manifest --file data/imports/manifests/batch.json
  ```
- **Daily:**
  ```bash
  python scripts/training_console.py intake daily --daily-qa
  ```

### C. YouTube Import Workflow
Converts offline YouTube transcript raw content into standardized local coaching logs:
```bash
python scripts/training_console.py intake youtube --file data/imports/youtube_training/video.txt --title "Objection Handling Guide"
```

### D. Scheduler Workflow
Triggers a single lock-bounded iteration pass of the intake orchestrator:
```bash
python scripts/training_console.py scheduler once --daily-qa --limit 100
```

### E. Readiness Workflow
Verifies that all continuous training modules, scripts, files, and safety flags conform to system integrity specifications:
```bash
python scripts/training_console.py readiness --strict
```

### F. Reports Workflow
Discovers and reads generated reports securely:
- **List reports:**
  ```bash
  python scripts/training_console.py reports list --type intake --limit 10
  ```
- **Show report contents:**
  ```bash
  python scripts/training_console.py reports show --path data/intake_reports/training_intake_report.md
  ```

---

## Checklists

### Operator Daily Checklist
- [ ] Run system summary check: `python scripts/training_console.py summary` and check for any alerts.
- [ ] Run daily folder intake: `python scripts/training_console.py intake daily --daily-qa`.
- [ ] List pending items: `python scripts/training_console.py review list --status pending`.
- [ ] Review pending turns: Approve or reject pending examples manually.
- [ ] Run readiness checks to verify environment sanity: `python scripts/training_console.py readiness`.

### Manager Weekly Checklist
- [ ] Scan recent reports: `python scripts/training_console.py reports list`.
- [ ] Audit rejected human review items: `python scripts/training_console.py review list --status rejected` and review reasons.
- [ ] Verify that no direct prompt mutations or provider connections were registered.
- [ ] Verify active canary metrics using canary monitoring tools.
