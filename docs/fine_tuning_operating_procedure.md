# Dana Outbound Sales AI - Fine-Tuning Operating Procedure

This document outlines the standard operating procedure (SOP) for exporting, gating, requesting approval, and manual tracking of fine-tuning jobs.

---

## 1. Safety Policies

> [!WARNING]
> - **Fine-tuning is optional and gated**: Fine-tuning should only be performed when specific conversational gaps are identified in daily QA mining reports.
> - **Zero Automation for Uploads**: The system contains no direct integration with LLM APIs to start jobs or upload dataset files. All file uploads and job launches must be executed manually by operators outside the system.
> - **No Direct Deployment**: A successful fine-tuning job does NOT approve the model for deployment. The new model ID must be registered, evaluated, simulated, and run through a canary rollout before live-call runtime use.

---

## 2. Operating Steps

```
[Export Dataset] -> [Gate Compliance] -> [Approve Dataset] -> [Prepare Job Request] -> [Approve Job Start] -> [Manual Upload/Run] -> [Record IDs]
```

### Step 1: Export Mined Data
Compile approved examples into dataset JSONL files:
```bash
python scripts/export_fine_tune_dataset.py --export-name "ft_june_2026" --min-examples 10 --format openai_chat_jsonl
```
*Gathers only human-approved rows containing `fine_tune_eligible = True`.*

### Step 2: Run Dataset Gate
Validate compliance and dataset splitting:
```bash
python scripts/gate_fine_tune_dataset.py --manifest "data/fine_tune_exports/ft_june_2026/manifest.json" --create-review-item
```
*Creates a pending dataset approval HumanReviewItem. Exits with 1 if compliance rules are violated.*

### Step 3: Approve Dataset
A human operator reviews the approval package under `data/fine_tune_approvals/ft_june_2026/` and marks it approved in the database:
```bash
python scripts/review_training_items.py approve --item-id DATASET_APPROVAL_ITEM_ID --reviewer "Braden"
```

### Step 4: Prepare Provider-Safe Job Request
Generate the request package and manual instructions:
```bash
python scripts/prepare_fine_tune_job_request.py --approval-package "data/fine_tune_approvals/ft_june_2026/approval_package.json" --provider openai --recommended-base-model "gpt-4o-mini-2024-07-18"
```
*Verifies database approval status and checks file hashes. Generates `job_request_package.json`.*

### Step 5: Request Job Start Approval
Submit a request to start the fine-tuning job:
```bash
python scripts/track_fine_tune_job.py request-start --job-request-package "data/fine_tune_job_request_ft_june_2026.json" --actor "Jimmy" --reason "Requesting permission to run manual upload and start job."
```
*Creates a pending `fine_tune_job_start_approval` HumanReviewItem.*

### Step 6: Approve Job Start
The manager approves the job start:
```bash
python scripts/review_training_items.py approve --item-id JOB_START_APPROVAL_ID --reviewer "Braden" --notes "Start authorized."
```
*Ensure that the approval payload has `start_authorized = True`.*

### Step 7: Perform Manual Upload & Job Start
1. Open the provider dashboard (e.g., OpenAI Dashboard) or run provider CLI commands.
2. Manually upload `train.jsonl` and `val.jsonl`.
3. Note the generated provider File IDs.
4. Manually start the fine-tuning job using the uploaded files and note the Job ID.

### Step 8: Record Manual References
Once started, record the IDs to maintain the audit trail:
```bash
python scripts/track_fine_tune_job.py record-upload --job-start-review-item-id JOB_START_APPROVAL_ID --provider-file-id "file-xyz" --provider-validation-file-id "file-abc" --actor "Jimmy" --reason "Manual upload complete."

python scripts/track_fine_tune_job.py record-job --job-start-review-item-id JOB_START_APPROVAL_ID --provider-job-id "ftjob-123" --actor "Jimmy" --reason "Fine-tuning job started on dashboard."
```

### Step 9: Monitor & Conclude Tracking
1. Regularly check the provider dashboard.
2. Update the tracking status as it progresses:
   ```bash
   python scripts/track_fine_tune_job.py update-status --tracking-id TRACKING_ID --status running_manual --actor "Jimmy" --reason "Job is currently training."
   ```
3. Once completed, save the final model ID:
   ```bash
   python scripts/track_fine_tune_job.py update-status --tracking-id TRACKING_ID --status succeeded_manual --provider-model-id "ft:gpt-4o-mini:dana-custom-v1" --actor "Jimmy" --reason "Job completed successfully."
   ```
