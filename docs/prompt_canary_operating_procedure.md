# Dana Outbound Sales AI - Prompt Canary Operating Procedure

This document outlines the standard operating procedure for designing, previewing, and testing prompt updates using the canary rollout control plane.

---

## 1. Safety Policies

> [!IMPORTANT]
> - **Canary Resolver Default-Off**: The resolver is disabled by default. Live calls will always route to the static control prompt unless the environmental flag `DANA_ENABLE_PROMPT_CANARY=true` is set.
> - **Force Candidate Protection**: Bypassing canary splits using a direct candidate routing override requires `DANA_ALLOW_FORCE_CANDIDATE_PROMPT=true`.
> - **No Direct Master File Edits**: Operators must not manually overwrite `prompts/final_expense_alex.md` in production. Prompt updates must follow the patching, previewing, and versioning workflow.

---

## 2. Operating Steps

```
[Generate Patch] -> [Preview & Validate] -> [Create Version] -> [Create Canary Plan] -> [Approve Rollout] -> [Start Rollout] -> [Monitor & Promote]
```

### Step 1: Generate Prompt Patch
Create a candidate prompt patch based on lessons or coach directives:
```bash
python scripts/generate_prompt_patches.py --instructions "Update the opening state greeting to address licensed agent questions" --actor "Jimmy"
```
*Outputs a pending HumanReviewItem containing the patch lines.*

### Step 2: Preview Patch & Run Validation Gates
Generate a preview of the patched prompt and run offline verification tests (evaluation scenarios, replay transcript turns, simulator persona runs):
```bash
python scripts/preview_prompt_patch.py --patch-id PATCH_REVIEW_ID --output-file "data/prompt_previews/preview.md"
```
*Confirms no syntax errors exist and ensures the preview file is successfully written. Exits with 1 if quality gates are not met.*

### Step 3: Create Candidate PromptVersion
Register the verified candidate version in the database:
```bash
python scripts/manage_prompt_versions.py create --file-path "data/prompt_previews/preview.md" --created-by "Jimmy" --change-reason "Greeting update"
```
*Generates a candidate PromptVersion. Rejects if file content hashes do not match.*

### Step 4: Create Canary Rollout Plan
Create a planned canary deployment experiment with a specific traffic split:
```bash
python scripts/manage_canary_rollout.py create --prompt-version-id VERSION_ID --traffic-percent 10
```
*Generates a DeploymentExperiment record in 'planned' status.*

### Step 5: Approve Canary Rollout
A manager must approve the rollout:
```bash
python scripts/manage_canary_rollout.py approve --experiment-id EXP_ID --actor "Braden"
```

### Step 6: Start Canary Traffic Routing
Activate the canary split routing:
1. Ensure the env flag is active on the server:
   ```bash
   export DANA_ENABLE_PROMPT_CANARY=true
   ```
2. Transition the experiment status to running:
   ```bash
   python scripts/manage_canary_rollout.py start --experiment-id EXP_ID
   ```

### Step 7: Monitor Safety & Auto-Rollback
Verify safety metrics and performance during the rollout:
```bash
python scripts/monitor_canary_rollout.py monitor --experiment-id EXP_ID
```
*The monitor checks compliance violations. If the compliance failure rate exceeds 2% or any critical safety signal triggers, the script automatically rolls back the experiment status to `rolled_back`.*

### Step 8: Promote the Prompt Version
Once the canary completes successfully, check promotion readiness:
```bash
python scripts/monitor_canary_rollout.py readiness --experiment-id EXP_ID
```
*Marks the version as `ready_for_promotion`. To publish the version to production, an operator must run a manual database update command.*
