# Dana Continuous Training Web Console Operating Procedure

The Training Operations Web Console provides a safe, browser-based local user interface to administer Dana's outbound Final Expense continuous training workflows.

---

## 🚨 Critical Safety Boundaries (Strictly Enforced)

> [!CRITICAL]
> **OPERATIONAL CONSTRAINT BOUNDARIES:**
> 1. **No Auto-Approval**: The web interface does not automatically approve training examples or evaluate cases. All imported resources must go through manual, auditable reviewer approval.
> 2. **No Live Prompt Mutations**: The web console never alters the production prompt file `prompts/final_expense_alex.md`.
> 3. **No Provider Uploads**: The console runs offline and contains no integration or logic to upload files to OpenAI, Azure, or external LLM API endpoints.
> 4. **No Fine-Tune Jobs**: Executing fine-tuning runs is forbidden. Manual runs must be cataloged manually via tracking logs only.
> 5. **No Direct Deployment**: The server does not manage canary rollout activation. Promotions require explicit env flag configuration and manual review gates.
> 6. **Local Host Binding**: The server binds to `127.0.0.1:8787` by default. Do not expose this server to external networks.

---

## Startup and Shutdown Workflows

### 1. Launching the Local Server
From the repository root, run the launcher CLI:
```bash
python scripts/run_training_web_console.py
```
To enable debugging or log API payloads, append the `--debug` flag:
```bash
python scripts/run_training_web_console.py --debug
```

### 2. Accessing the Web UI
Open your web browser and navigate to:
[http://127.0.0.1:8787](http://127.0.0.1:8787)

### 3. Stopping the Server
In the command-line interface where the server was launched, send a termination signal:
- Press **Ctrl + C**
- The server will release active ports and shutdown cleanly.

---

## Interface Overview & Workflows

### A. System Dashboard (Overview)
The dashboard displays counts for pending reviews, recent sources, prompts, canaries, and latest readiness audit outcomes. Use the **Refresh** button in the status card to update metrics.

### B. Human Review Queue
1. Navigate to the **Human Review Queue** tab.
2. Filter the queue by **Status** (e.g. `pending`) and **Item Type**.
3. Select an item from the table and click **Inspect**.
4. Review the payload details in the JSON viewer.
5. In the audit form:
   - Provide a **Reviewer Identity** name (strictly required).
   - Provide **Review Notes** (required for rejection/needs-changes).
   - Click **Approve**, **Reject**, or **Needs Changes** to update the review state.

### C. File Upload and Training Intake
1. Navigate to the **Training Intake & Import** tab.
2. Locate the **Upload Local Material** card.
3. Select the target **Import Source Type** (e.g. `call_transcript` or `youtube`).
4. Select a local file (`.txt`, `.json`, `.jsonl`, `.md` only) and click **Upload File**.
5. Once uploaded, run folder intake on the designated directory to queue imports for review:
   - Use the **Run Intake Orchestration** card, select the path, and click **Execute Folder Intake**.

### D. YouTube Transcript pasting
1. Navigate to the **Training Intake & Import** tab.
2. In the **YouTube Transcript Text Import** card:
   - Fill in the video title and optional URL source.
   - Paste raw transcript text.
   - Check the **Trigger Intake Pipeline** option.
   - Click **Import Transcript** to convert the pasted transcript into standard review examples.

### E. Scheduler iteration
1. Go to the **Scheduler iteration** tab.
2. Specify the item processing limit and daily QA evaluation options.
3. Click **Run Scheduler Loop Once** to run a single lock-protected execution cycle of the scheduling engine.

### F. Readiness Auditor
1. Navigate to the **Readiness Auditor** tab.
2. Configure scan options (Strict / Fail on Medium).
3. Click **Run Readiness Audit** to check system validation logs, database schemas, and directory constraints.
4. If failures occur, check the generated **Remediation Items** list at the bottom of the card.

### G. Reports Viewer
1. Navigate to the **Operator Reports** tab.
2. Select a category (e.g., `qa`, `intake`, `readiness`) and click **List Reports**.
3. Select a report file from the list and click **View** to display its full text in the viewer pane.

### H. Advanced Continuous Training Workflows
The console features five additional advanced tabs mapping to continuous training pipeline phases:
1. **QA & Evals**: Runs Daily QA Mining, Eval case runs, Transcript Replays, and Prospect Simulations.
2. **Prompt Improvements**: Views prompt versions, triggers patch candidates generation, and runs preview safety gates.
3. **Canary Rollouts**: Creating rollout plans, verifying candidate eligibility, and managing canary experiment states.
4. **Fine-Tune Operations**: Packaging, gating check runs, configuring job request options, and manually tracking fine-tuning status.
5. **Post-Call Export Test**: Simulates completed call payload file dumps and ingestion parsing safely.

> [!WARNING]
> **Safety Gating Constraints:**
> Even under advanced tabs, the web console operates as a local-only verification command layer. It never mutates live production prompts directly, uploads training packages to provider API endpoints, starts remote provider fine-tune jobs, or executes active model deployments.

---

## 📖 Detailed Advanced Procedures
For in-depth step-by-step guidance on advanced verification loops and state control transitions, refer to:
[docs/training_web_console_advanced_workflows.md](file:///C:/Users/jimbo/.gemini/antigravity/worktrees/ultimate-voice/dana-training-ops-console/docs/training_web_console_advanced_workflows.md)

---

## Troubleshooting & FAQ

- **Address Already in Use**: If port `8787` is occupied, change the port with the `--port` option:
  ```bash
  python scripts/run_training_web_console.py --port 8989
  ```
- **Upload Rejected**: Ensure the uploaded file has a valid extension (`.txt`, `.json`, `.jsonl`, `.md`) and its filename does not contain path traversal indicators (like `..`, `/`, `\`).
- **Review Fails to Submit**: Verify that **Reviewer Identity** is filled in, and that **Review Notes** are provided if performing rejection or needs-changes actions.
