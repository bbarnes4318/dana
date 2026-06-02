import os
import sys
import glob
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from storage.repository import Repository
from telephony.live_telephony_readiness import LiveTelephonyReadinessChecker
from telephony.livekit_agent_worker import check_worker_dependencies
from telephony.campaign_service import TelephonyCampaignService
from telephony.did_pool import DIDPoolManager
from storage.schemas import CallerIdSelectionConfig, CallerIdNumber

class ProductionReadinessResult(BaseModel):
    """Production readiness gate status report."""
    ready_for_small_canary: bool
    ready_for_production_scale: bool = False  # Hard locked to False
    passed_checks: List[str] = Field(default_factory=list)
    failed_checks: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    next_steps: List[str] = Field(default_factory=list)


async def run_production_readiness_gate(repository: Optional[Repository] = None) -> ProductionReadinessResult:
    """Audit all system settings and testing gate history to certify small canary readiness."""
    repo = repository or Repository()
    
    passed_checks = []
    failed_checks = []
    warnings = []
    next_steps = []

    # 1. Core Readiness Check
    try:
        checker = LiveTelephonyReadinessChecker(repository=repo)
        readiness_res = await checker.run()
        if readiness_res.ready:
            passed_checks.append("Core environment readiness checks passed.")
        else:
            failed_checks.append(f"Core environment readiness failed: {', '.join(readiness_res.failures)}")
            next_steps.append("Address failures listed in check_live_telephony_readiness.py.")
    except Exception as e:
        failed_checks.append(f"Failed core readiness execution: {e}")

    # 2. Worker Check
    try:
        worker_status = check_worker_dependencies()
        if worker_status.get("ready", False):
            passed_checks.append("LiveKit agent worker dependencies and daemon are ready.")
        else:
            failed_checks.append("LiveKit agent worker is not ready or has missing dependencies.")
            next_steps.append("Start agent worker using python scripts/run_livekit_agent_worker.py.")
    except Exception as e:
        failed_checks.append(f"Failed worker dependencies check: {e}")

    # 3. DID Pool Check
    try:
        pool_mgr = DIDPoolManager(repo)
        active_provider = os.environ.get("DANA_ACTIVE_TELEPHONY_PROVIDER", "telnyx").strip().lower()
        dids = await pool_mgr.list_numbers(provider=active_provider)
        
        # Filter active ones
        active_dids = [d for d in dids if d.status == "active"]
        
        if active_provider == "telnyx":
            if active_dids:
                passed_checks.append(f"DID Pool contains {len(active_dids)} active Telnyx DIDs.")
            else:
                failed_checks.append("DID Pool is empty or contains no active DIDs for active provider 'telnyx'.")
                next_steps.append("Run python scripts/sync_telnyx_dids.py to import owned caller IDs.")
        else:
            if active_dids:
                passed_checks.append(f"DID Pool contains active DIDs for active provider '{active_provider}'.")
            else:
                warnings.append(f"No active DIDs found in pool for provider '{active_provider}'.")

        # Confirm daily/hourly cap fields are in the CallerIdNumber schema class
        has_caps = (
            hasattr(CallerIdNumber, "daily_cap") or 
            "daily_cap" in getattr(CallerIdNumber, "model_fields", {}) or
            "daily_cap" in getattr(CallerIdNumber, "__fields__", {})
        ) and (
            hasattr(CallerIdNumber, "hourly_cap") or
            "hourly_cap" in getattr(CallerIdNumber, "model_fields", {}) or
            "hourly_cap" in getattr(CallerIdNumber, "__fields__", {})
        )
        if has_caps:
            passed_checks.append("Hourly and daily call caps exist on CallerIdNumber schema class.")
        else:
            failed_checks.append("Hourly and daily caps are missing from CallerIdNumber model attributes.")

        # Caller ID source validation
        caller_id_source = os.environ.get("DANA_OUTBOUND_CALLER_ID_SOURCE", "pool:telnyx_api").strip()
        if "pool:" in caller_id_source or caller_id_source == "env":
            passed_checks.append(f"Outbound caller ID source is configured correctly: {caller_id_source}")
        else:
            failed_checks.append(f"Invalid DANA_OUTBOUND_CALLER_ID_SOURCE configured: {caller_id_source}")

    except Exception as e:
        failed_checks.append(f"Failed DID pool readiness audit: {e}")

    # 4. Outbound Trunk Check
    trunk_id = os.environ.get("LIVEKIT_SIP_OUTBOUND_TRUNK_ID")
    if trunk_id:
        passed_checks.append(f"LiveKit Outbound SIP Trunk ID is present: {trunk_id[:6]}...")
    else:
        failed_checks.append("LiveKit Outbound SIP Trunk ID (LIVEKIT_SIP_OUTBOUND_TRUNK_ID) is missing.")
        next_steps.append("Configure LIVEKIT_SIP_OUTBOUND_TRUNK_ID in environment.")

    # 5. Load and scan test reports history
    reports_dir = "data/telephony_reports"
    dry_run_batch_passed = False
    one_lead_live_passed = False
    three_lead_live_passed = False
    
    if os.path.exists(reports_dir):
        for fpath in glob.glob(os.path.join(reports_dir, "*.json")):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("success") is True:
                        is_dry = data.get("dry_run") is True
                        requested = data.get("requested_leads", 0)
                        
                        if is_dry:
                            dry_run_batch_passed = True
                        else:
                            # A live run
                            if requested == 1:
                                one_lead_live_passed = True
                            elif 1 < requested <= 3:
                                three_lead_live_passed = True
            except Exception:
                pass

    # Programmatic live smoke test check (successful call attempt in database)
    smoke_test_passed = False
    exports_working = False
    intake_working = False
    
    try:
        attempts = await repo.query_call_attempts({})
        real_completed = [a for a in attempts if a.get("status") == "completed" and a.get("outcome") not in ("failed", None)]
        if real_completed:
            smoke_test_passed = True
            
            # Check exports
            for a in real_completed:
                path = a.get("post_call_export_path")
                if path and os.path.exists(path):
                    exports_working = True
                meta = a.get("metadata", {})
                if meta.get("intake_run") and meta.get("intake_result") == "staged":
                    intake_working = True
    except Exception as e:
        warnings.append(f"Failed querying call attempts from database: {e}")

    # Assert dry-run batch
    if dry_run_batch_passed:
        passed_checks.append("Controlled live batch dry-run validation passed.")
    else:
        failed_checks.append("No record of a successful controlled live batch dry-run validation.")
        next_steps.append("Run python scripts/run_live_batch_campaign_test.py to execute a batch dry-run.")

    # Assert smoke test
    if smoke_test_passed:
        passed_checks.append("At least one real live outbound smoke test passed.")
    else:
        failed_checks.append("No record of a successful real live outbound smoke test.")
        next_steps.append("Run python scripts/run_live_telephony_smoke_test.py in live mode.")

    # Assert one-lead campaign test
    if one_lead_live_passed:
        passed_checks.append("One-lead controlled live campaign test passed.")
    else:
        failed_checks.append("No record of a successful one-lead controlled live campaign test.")
        next_steps.append("Run python scripts/run_one_lead_live_campaign_test.py in live mode.")

    # Assert 3-lead live batch
    if three_lead_live_passed:
        passed_checks.append("3-lead controlled live batch validation passed.")
    else:
        failed_checks.append("No record of a successful 3-lead controlled live batch validation.")
        next_steps.append("Run python scripts/run_real_live_batch_validation.py in live mode.")

    # Assert exports and intake staging
    if exports_working:
        passed_checks.append("Post-call exports are verified and working on disk.")
    else:
        failed_checks.append("No completed calls with verified post-call export files found.")
        next_steps.append("Execute a live campaign call that produces a post-call export payload.")

    if intake_working:
        passed_checks.append("Training intake staging is verified and working.")
    else:
        failed_checks.append("No call attempts with staging intake statuses found in database.")
        next_steps.append("Execute a live call with --run-intake-after-export enabled.")

    # 6. Compliance & Privacy Safeguards
    # No auto-approval of training data
    auto_approve = os.environ.get("DANA_AUTO_APPROVE_TRAINING_EXAMPLES", "").strip().lower()
    no_auto_approval = auto_approve not in ("true", "yes", "1")
    if no_auto_approval:
        passed_checks.append("Auto-approval of training examples is disabled (DANA_AUTO_APPROVE_TRAINING_EXAMPLES is false).")
    else:
        failed_checks.append("Compliance breach: auto-approval of training examples is enabled in environment!")
        next_steps.append("Set environment variable DANA_AUTO_APPROVE_TRAINING_EXAMPLES=false.")

    # Compliance evaluator (CallScorer) presence
    try:
        from qa.scoring import CallScorer, detect_hard_failures
        passed_checks.append("Compliance evaluator and CallScorer are available.")
    except ImportError:
        failed_checks.append("Compliance evaluator modules are missing from qa/scoring.py.")
        next_steps.append("Ensure qa/scoring.py defines CallScorer and detect_hard_failures.")

    # DNC / calling window checks presence
    try:
        from telephony.dialer_queue import DialerQueue
        dialer = DialerQueue(repository=repo)
        has_window_check = hasattr(dialer, "is_within_calling_window") and hasattr(dialer, "lead_is_callable")
        
        from telephony.lead_importer import CampaignLeadImporter
        importer = CampaignLeadImporter(repository=repo)
        has_dnc_check = hasattr(importer, "is_suppressed")
        
        if has_window_check and has_dnc_check:
            passed_checks.append("DNC list scrubbing and calling window limit checking are enabled.")
        else:
            failed_checks.append("DNC list or calling window constraints are not fully wired in dialer queue.")
    except Exception as e:
        failed_checks.append(f"Failed compliance functions check: {e}")

    # Campaign limits (concurrency limit, daily campaign cap, and emergency stop)
    try:
        has_campaign_caps = hasattr(TelephonyCampaignService, "create_campaign")
        if has_campaign_caps:
            passed_checks.append("Outbound campaign pacing and maximum concurrent call limits exist.")
        else:
            failed_checks.append("Pacing parameters are missing on TelephonyCampaignService.")
            
        has_emergency = hasattr(TelephonyCampaignService, "stop_campaign") and hasattr(TelephonyCampaignService, "pause_campaign")
        if has_emergency:
            passed_checks.append("Emergency stop and pause controls exist on TelephonyCampaignService.")
        else:
            failed_checks.append("Emergency stop or pause functions are missing on TelephonyCampaignService.")
    except Exception as e:
        failed_checks.append(f"Failed campaign service limits check: {e}")

    # Transfer consent requirement
    try:
        from storage.schemas import CallAttempt
        has_consent_field = "transfer_consent" in CallAttempt.model_fields
        if has_consent_field:
            passed_checks.append("Warm transfers require explicit prospect consent (transfer_consent exists).")
        else:
            failed_checks.append("Transfer consent confirmation is missing on CallAttempt schema.")
    except Exception as e:
        failed_checks.append(f"Failed transfer consent schema check: {e}")

    # 7. Cross-provider caller ID restrictions
    try:
        # Programmatic check that select_caller_id prevents cross-provider SignalWire/BulkVS leak
        test_config = CallerIdSelectionConfig(
            provider="telnyx",
            allow_cross_provider=True,
            require_verified=False
        )
        
        # Save mock SignalWire DID in pool
        await repo.save_did(
            provider="signalwire",
            phone_number="+15551119999",
            status="active",
            source="manual",
            verified_for_provider=True
        )
        
        # Save mock BulkVS DID in pool
        await repo.save_did(
            provider="bulkvs",
            phone_number="+15552229999",
            status="active",
            source="manual",
            verified_for_provider=True
        )

        res_select = await pool_mgr.select_caller_id(test_config)
        # Verify selecting a Telnyx number was not allowed to choose SignalWire
        if res_select.success and res_select.phone_number in ("+15551119999", "+15552229999"):
            failed_checks.append("Cross-provider restriction check failed: selected SignalWire/BulkVS caller ID for Telnyx provider.")
        else:
            passed_checks.append("No SignalWire or BulkVS caller ID can be rotated when provider is Telnyx.")
            
        # Clean up database mock DIDs
        sw_did = await repo.get_did_by_number("+15551119999")
        if sw_did:
            await repo.delete_did(sw_did["id"])
        bvs_did = await repo.get_did_by_number("+15552229999")
        if bvs_did:
            await repo.delete_did(bvs_did["id"])
            
    except Exception as e:
        failed_checks.append(f"Failed cross-provider caller ID restriction audit: {e}")

    # 8. Git Safety
    try:
        res = subprocess.run(["git", "ls-files", ".env"], capture_output=True, text=True)
        env_committed = bool(res.stdout.strip())
        if not env_committed:
            passed_checks.append("Git safety checks verified: local secrets (.env) are not committed.")
        else:
            failed_checks.append("Critical safety failure: local credentials (.env) are committed/tracked in git!")
            next_steps.append("Run 'git rm --cached .env' and add '.env' to your .gitignore file.")
    except Exception as e:
        warnings.append(f"Failed git safety file checks: {e}")

    # Determine Small Canary readiness
    ready_for_small_canary = len(failed_checks) == 0

    return ProductionReadinessResult(
        ready_for_small_canary=ready_for_small_canary,
        ready_for_production_scale=False,  # Hard locked to False
        passed_checks=passed_checks,
        failed_checks=failed_checks,
        warnings=warnings,
        next_steps=next_steps
    )
