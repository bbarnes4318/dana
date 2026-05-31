# Dana Training Web Console: Advanced Workflows Guide

This guide details the advanced operations and workflows available in the browser-based Training Console for Dana's continuous training pipeline. These tools allow operators to execute the complete QA, evaluation, prompt verification, canary staging, fine-tuning prep, and post-call test cycles entirely from the browser interface.

---

## 🚨 Core Safety Boundaries (Strictly Enforced)

All workflows running via the console are bound by offline safety constraints. Make sure you understand the boundaries of each workflow:

| Workflow Area | What the UI/API Does | What the UI/API DOES NOT Do | Safety Warning |
|---|---|---|---|
| **Prompt Improvements** | Generates patch candidates from data reviews; runs evaluation/safety gates on preview; creates a staged `PromptVersion`. | Does not mutate the active live prompt file (`prompts/final_expense_alex.md`). | "Preview only. Does not modify live prompt." |
| **Canary Rollouts** | Checks candidate eligibility; schedules canary plans; starts, pauses, rolls back, completes, or cancels canaries via `CanaryManager`. | Does not auto-promote prompts. Runtime routing requires explicit environment controls (`DANA_ENABLE_PROMPT_CANARY=true`). | "No auto-promotion. Runtime routing still requires existing environment controls." |
| **Fine-Tuning** | Exports training examples; runs gate checks; prepares config requests; manages manual job tracking entries. | Does not upload datasets to OpenAI/Azure; does not trigger training jobs on provider servers; does not deploy models. | "This UI does not upload files or start provider fine-tune jobs." |
| **Post-Call Export** | Pastes/uploads call payloads; runs local post-call export parser checks; tests intake. | Does not enable live runtime hooks (remains disabled by default). | "Export testing does not enable live runtime hook. Runtime hook remains disabled by default." |

---

## 1. QA & Evaluations Workflow

### A. Daily QA Miner
- **Description**: Evaluates and crawls recent production call transcripts to extract coaching, compliance, and objection examples.
- **Workflow**:
  1. Open the **QA & Evals** tab.
  2. Under **Daily QA Miner**, enter a single date (`YYYY-MM-DD`) or a date range (**Date From** and **Date To**).
  3. Click **Run QA Miner**. Mined examples will be written to the local repository, and if dry run is unchecked, they will populate the Human Review queue.
  4. View the generated audit output and review items count.

### B. Eval Case Runner
- **Description**: Runs a regression test suite against a set of approved/stored evaluation cases using a static mock response provider.
- **Workflow**:
  1. Under **Eval Case Runner**, optionally specify a single Case ID, a specific stage (e.g., `greeting`), or an objection keyword (e.g., `price`).
  2. Click **Run Eval Cases**.
  3. View the test execution log, pass/fail counts, and the path to the detailed evaluation run reports.

### C. Transcript Replay Tester
- **Description**: Replays multi-turn audio transcripts against the agent config to assert state machine flow accuracy.
- **Workflow**:
  1. Under **Transcript Replay Tester**, choose the target **Fixtures Directory** or a specific single fixture path.
  2. Select the execution mode:
     - **Static**: Replays and evaluates matches against static recorded turns.
     - **Runtime**: Replays live against the agent prompt engine (requires active environment credentials).
  3. Click **Run Transcript Replays** to run all matched fixtures.

### D. Prospect Simulator Scenarios
- **Description**: Simulates outbound calls against persona profiles (e.g., `busy_bill`) using static Dana response overrides to assert objection handlers.
- **Workflow**:
  1. Under **Prospect Simulator Scenarios**, optionally filter by a specific Persona ID or check **Run All Default Personas**.
  2. Click **Run Simulations**.
  3. Review the aggregate simulation pass rates and average conversational quality scores.

---

## 2. Prompt Improvements Workflow

### A. Generate Prompt Patches
- **Description**: Scans approved coaching training examples in the database and uses local heuristics to propose prompt refinement patches.
- **Workflow**:
  1. Open the **Prompt Improvements** tab.
  2. In **Generate Prompt Patch Candidates**, set a scan limit and click **Generate Patch Candidates**.
  3. New proposed changes are staged as review items of type `prompt_patch`.

### B. Preview and Gating Validation
- **Description**: Previews staged patch refinements, compiles a preview version of the prompt, and runs regression/safety evaluation gates.
- **Workflow**:
  1. Locate the **Preview & Gate approved Patches** card.
  2. Enter a specific **Review Item Patch ID** (or leave empty to bundle all approved patches).
  3. Check **Create Candidate PromptVersion** if you wish to write the result as a staged version record in the database if all gates pass.
  4. Click **Run Preview & Verification Gates**. If successful, a new candidate `PromptVersion` is stored.

### C. Staged Prompt Versions
- **Description**: Lists all candidate versions compiled through the preview gates.
- **Workflow**:
  1. In the **Staged Prompt Versions** card, click **Load Versions**.
  2. Review the version IDs, creators, change reasons, and canary rollout status of all recorded configurations.

---

## 3. Canary Rollouts Workflow

### A. Check Eligibility
- **Description**: Validates that a candidate version has successfully passed safety checks, evaluations, and mock simulations prior to rollout.
- **Workflow**:
  1. Open the **Canary Rollouts** tab.
  2. Paste the staged **PromptVersion ID** into the **Check Eligibility & Create Canary** form.
  3. Click **Check Eligibility**.

### B. Create and Manage Canaries
- **Description**: Schedules and monitors canary variants, routing a tiny fraction of traffic to evaluate live performance safely.
- **Workflow**:
  1. Fill in the **Staged PromptVersion ID**, **Initial Traffic %** (up to 10%), **Operator** identity, and rollout notes.
  2. Click **Create Plan** to initialize a planned canary experiment.
  3. Use the **Canary State Controller** to transition the experiment state:
     - **Approve**: Move from `planned` to `approved`.
     - **Start**: Run active rollout routing (requires env flags).
     - **Pause**: Temporarily suspend candidate traffic.
     - **Rollback**: Immediately roll back to production defaults (requires Operator & Reason notes).
     - **Complete**: Mark rollout as fully successful.
     - **Cancel**: Cancel the planned rollout (requires Operator & Reason notes).
  4. Click **Run Monitoring Analysis** to evaluate the metric logs of the selected canary.

---

## 4. Fine-Tune Operations Workflow

### A. Export Dataset Package
- **Description**: Compiles approved, eligible examples from human reviews into a standardized dataset format (e.g., OpenAI chat JSONL).
- **Workflow**:
  1. Open the **Fine-Tune Operations** tab.
  2. Under **Export Fine-Tune Dataset**, specify filters (Limit, Stage, Objection) and click **Export Dataset Package**.
  3. The resulting training and validation JSONL files will be exported to `data/fine_tune_exports/`.

### B. Gate Dataset Compliance
- **Description**: Validates exported datasets against formatting, size, tokens, and safety requirements.
- **Workflow**:
  1. In **Gate Dataset Compliance**, paste the exported dataset directory or JSONL file path.
  2. Check **Strict** to fail on any medium quality warnings.
  3. Click **Run Quality Gate Check**. The report will be saved under `data/fine_tune_approvals/`.

### C. Prepare Job Configuration
- **Description**: Generates the metadata and job configuration packages required for provider submission.
- **Workflow**:
  1. In **Prepare Fine-Tune Job Configuration**, enter the paths to the dataset and compliance report.
  2. Choose the target provider (e.g., `openai`).
  3. Click **Prepare Job Request Package**. This drafts the final configuration parameters in `data/fine_tune_job_requests/`.

### D. Track Manual Job Status
- **Description**: Tracks the lifecycles of provider fine-tuning runs manually.
- **Workflow**:
  1. Copy the **Job Request ID** and paste it into the **Track Fine-Tuning Job Status** card.
  2. Select the current state (e.g., `submitted_manually`, `running`, `succeeded`).
  3. Provide the **Operator**, **Provider Job ID** (e.g., `ft-job-1234`), and notes.
  4. Click **Save Tracking Record**.
  5. Click **Load Tracking** in the history log card below to view all tracked provider jobs.

---

## 5. Post-Call Export Test Utility

- **Description**: Simulates post-call ingestion by pasting or uploading call states. This verifies redactions and file exports without risk to active runtime call configurations.
- **Workflow**:
  1. Open the **Post-Call Export Test** tab.
  2. Paste a completed call payload JSON into the text area.
  3. Check the options:
     - **Enabled**: Enforces payload checks.
     - **Run Intake**: Queues the exported file immediately into intake.
     - **Dry Run**: Simulates parser passes without writing files to disk.
  4. Click **Run Exporter Validation** and check the output log for paths and redaction results.

---

## 6. Advanced Reports Filtering

- **Description**: Inspects generated logs across all continuous training pipeline subdirectories.
- **Workflow**:
  1. Navigate to the **Operator Reports** tab.
  2. Select a specific category from the dropdown (e.g. `intake`, `qa`, `eval`, `replay`, `simulation`, `prompt_patch`, `canary`, `fine_tune`, `readiness`, `scheduler`, `web_console`).
  3. Click **List Reports** to find generated JSON, Markdown, and Diff files.
  4. Select a file and click **View** to open its contents in the preview card.

---

## Troubleshooting

- **Subprocess failures**: If evaluations, qa, or simulations fail, verify that there are no active file locks on data directories, and check the command-line console logs.
- **Canary Rollback Required**: If the monitor reports high objection rates or silence durations on a canary variant, immediately input the experiment ID and operator name in the **Canary State Controller** and click **Rollback**.
