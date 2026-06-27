import os
import sys
import uuid
import json
import re
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Dict, List, Tuple
from pydantic import BaseModel, Field

from storage.repository import Repository
from telephony.livekit_adapter import LiveKitOutboundAdapter
from telephony.live_telephony_readiness import LiveTelephonyReadinessChecker
from telephony.dialer_queue import DialerQueue, DialerTickConfig, DialerTickResult
from telephony.campaign_service import TelephonyCampaignService
from telephony.lead_importer import CampaignLeadImporter

class LiveBatchTestConfig(BaseModel):
    """Configuration for running a controlled multi-lead live campaign batch test."""
    phone_numbers: List[str]
    operator: str
    confirm: str
    allow_now: bool = False
    max_leads: int = 3
    hard_max_leads: int = 5
    require_turns: bool = True
    require_post_call_export: bool = True
    run_intake_after_export: bool = True
    min_agent_turns: int = 1
    min_prospect_turns: int = 0
    interactive: bool = False
    per_call_timeout_seconds: int = 120
    stop_campaign_after_run: bool = True
    output_dir: str = "data/telephony_reports"
    now: Optional[datetime] = None
    dry_run: bool = True


class LiveBatchCallResult(BaseModel):
    """Result details for a single call within the batch."""
    phone_number_masked: str
    lead_id: Optional[str] = None
    call_attempt_id: Optional[str] = None
    livekit_room_name: Optional[str] = None
    sip_call_id: Optional[str] = None
    participant_id: Optional[str] = None
    selected_did: Optional[str] = None
    caller_id_source: Optional[str] = None
    phone_rang: bool = False
    answered: bool = False
    dana_spoke: bool = False
    turn_count: int = 0
    agent_turn_count: int = 0
    prospect_turn_count: int = 0
    transcript_captured: str = "no"
    call_outcome: Optional[str] = None
    post_call_export_path: Optional[str] = None
    intake_status: Optional[str] = None
    failure_reason: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)


class LiveBatchTestResult(BaseModel):
    """Overall outcome of the multi-lead campaign batch test."""
    success: bool
    dry_run: bool = False
    campaign_id: Optional[str] = None
    requested_leads: int
    attempted_calls: int = 0
    completed_calls: int = 0
    failed_calls: int = 0
    exported_calls: int = 0
    intake_imported_examples: int = 0
    campaign_stopped: bool = False
    calls: List[LiveBatchCallResult] = Field(default_factory=list)
    failures: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    report_json_path: Optional[str] = None
    report_markdown_path: Optional[str] = None


class ControlledBatchCampaignTester:
    """Manages sequential dialing of multiple test leads under strict limits and monitoring."""

    def __init__(self, repository: Optional[Repository] = None, adapter: Optional[LiveKitOutboundAdapter] = None) -> None:
        self.repository = repository or Repository()
        self.adapter = adapter or LiveKitOutboundAdapter()
        self.readiness_checker = LiveTelephonyReadinessChecker(repository=self.repository, adapter=self.adapter)
        self.campaign_service = TelephonyCampaignService(repository=self.repository)

    def mask_phone(self, phone: str) -> str:
        """Mask a phone number for safety in reporting."""
        if not phone:
            return ""
        if len(phone) > 4:
            return phone[:-4] + "****"
        return "****"

    def mask_secrets(self, data: dict) -> dict:
        """Recursively scrub secrets from output dictionaries."""
        masked = {}
        for k, v in data.items():
            if isinstance(v, dict):
                masked[k] = self.mask_secrets(v)
            elif isinstance(v, str) and ("SECRET" in k.upper() or "KEY" in k.upper()):
                masked[k] = v[:3] + "..." + v[-3:] if len(v) > 6 else "***"
            else:
                masked[k] = v
        return masked

    def is_valid_e164(self, phone: str) -> bool:
        """Verify standard E.164 or mock formats."""
        phone_stripped = phone.strip()
        if phone_stripped.startswith("+1TEST"):
            return True
        return bool(re.match(r"^\+[1-9]\d{4,14}$", phone_stripped))

    def write_reports(self, config: LiveBatchTestConfig, result: LiveBatchTestResult) -> None:
        """Write reports to output directory."""
        os.makedirs(config.output_dir, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        test_id = str(uuid.uuid4())[:8]

        json_path = os.path.join(config.output_dir, f"batch_test_{timestamp}_{test_id}.json")
        md_path = os.path.join(config.output_dir, f"batch_test_{timestamp}_{test_id}.md")

        result.report_json_path = json_path
        result.report_markdown_path = md_path

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self.mask_secrets(result.model_dump(mode="json")), f, indent=2)

        md_lines = [
            f"# Controlled Live Campaign Batch Test Report",
            f"",
            f"- **Timestamp**: {datetime.now(timezone.utc).isoformat()}",
            f"- **Operator**: {config.operator}",
            f"- **Success**: {'🟢 YES' if result.success else '🔴 NO'}",
            f"- **Dry Run**: {config.dry_run}",
            f"- **Requested Leads**: {result.requested_leads}",
            f"- **Attempted Calls**: {result.attempted_calls}",
            f"- **Completed Calls**: {result.completed_calls}",
            f"- **Failed Calls**: {result.failed_calls}",
            f"- **Exported Calls**: {result.exported_calls}",
            f"- **Intake Imported Examples**: {result.intake_imported_examples}",
            f"- **Campaign Stopped**: {result.campaign_stopped}",
            f"",
            f"## 📞 Call Details",
        ]

        for idx, call in enumerate(result.calls):
            md_lines.extend([
                f"### Call {idx + 1}: {call.phone_number_masked}",
                f"- **Lead ID**: `{call.lead_id or 'N/A'}`",
                f"- **Call Attempt ID**: `{call.call_attempt_id or 'N/A'}`",
                f"- **LiveKit Room**: `{call.livekit_room_name or 'N/A'}`",
                f"- **SIP Call ID**: `{call.sip_call_id or 'N/A'}`",
                f"- **Selected DID**: `{call.selected_did or 'N/A'}`",
                f"- **Phone Rang**: `{call.phone_rang}`",
                f"- **Answered**: `{call.answered}`",
                f"- **Dana Spoke**: `{call.dana_spoke}`",
                f"- **Turns**: {call.turn_count} (Agent: {call.agent_turn_count}, Prospect: {call.prospect_turn_count})",
                f"- **Export Path**: `{call.post_call_export_path or 'N/A'}`",
                f"- **Outcome**: `{call.call_outcome or 'N/A'}`",
            ])
            if call.failure_reason:
                md_lines.append(f"- **Failure Reason**: *{call.failure_reason}*")
            if call.warnings:
                md_lines.append(f"- **Warnings**: {', '.join(call.warnings)}")

        if result.failures:
            md_lines.extend([
                f"",
                f"### ❌ Failures",
                *[f"- {f}" for f in result.failures]
            ])
        if result.warnings:
            md_lines.extend([
                f"",
                f"### ⚠️ Warnings",
                *[f"- {w}" for w in result.warnings]
            ])

        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines) + "\n")

    async def run(self, config: LiveBatchTestConfig) -> LiveBatchTestResult:
        """Run the controlled multi-lead campaign batch test workflow."""
        now = config.now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        result = LiveBatchTestResult(
            success=False,
            dry_run=config.dry_run,
            requested_leads=len(config.phone_numbers)
        )

        # 1. Reject if no numbers
        if not config.phone_numbers:
            result.failures.append("No phone numbers provided.")
            self.write_reports(config, result)
            return result

        # 2. Reject if more than hard max leads (or 5)
        if len(config.phone_numbers) > config.hard_max_leads:
            result.failures.append(f"Batch size {len(config.phone_numbers)} exceeds hard maximum of {config.hard_max_leads}.")
            self.write_reports(config, result)
            return result

        # 3. Reject duplicate numbers
        if len(config.phone_numbers) != len(set(config.phone_numbers)):
            result.failures.append("Duplicate phone numbers supplied.")
            self.write_reports(config, result)
            return result

        # 4. Reject invalid E.164 formats
        for phone in config.phone_numbers:
            if not self.is_valid_e164(phone):
                result.failures.append(f"Invalid E.164 phone number: {phone}")
                self.write_reports(config, result)
                return result

        # 5. Operator check
        if not config.operator or not config.operator.strip():
            result.failures.append("Operator name/ID is required.")
            self.write_reports(config, result)
            return result

        # 6. Confirmation check
        if not config.dry_run and config.confirm != "LIVE CALL":
            result.failures.append("Confirmation 'LIVE CALL' is required to place a live campaign call.")
            self.write_reports(config, result)
            return result

        # 7. Readiness check
        readiness_res = await self.readiness_checker.run()
        if not readiness_res.ready:
            result.failures.append("Readiness checks failed.")
            result.failures.extend(readiness_res.failures)
            result.warnings.extend(readiness_res.warnings)
            self.write_reports(config, result)
            return result

        # 8. Worker check
        try:
            from telephony.livekit_agent_worker import check_worker_dependencies
            worker_status = check_worker_dependencies()
            worker_ready = worker_status.get("ready", False)
        except Exception as e:
            result.failures.append(f"Failed to check worker: {e}")
            worker_ready = False

        if not worker_ready:
            result.failures.append("LiveKit agent worker is not ready or dependencies are missing.")
            self.write_reports(config, result)
            return result

        # 9. Check DID pool availability
        try:
            from telephony.did_pool import DIDPoolManager
            pool_mgr = DIDPoolManager(self.repository)
            env = self.adapter.required_env_status()
            provider = os.environ.get("DANA_ACTIVE_TELEPHONY_PROVIDER", "telnyx").strip().lower()
            numbers = await pool_mgr.list_numbers(provider=provider)
            if not numbers and not config.dry_run:
                result.failures.append(f"No phone numbers available in DID pool for provider {provider}.")
                self.write_reports(config, result)
                return result
        except Exception as e:
            result.failures.append(f"Failed to check DID pool: {e}")
            self.write_reports(config, result)
            return result

        # 10. DNC & Calling window checks for all numbers
        lead_importer = CampaignLeadImporter(repository=self.repository)
        dialer = DialerQueue(repository=self.repository)
        mock_campaign = {
            "timezone": "America/New_York",
            "calling_window_start": "09:30",
            "calling_window_end": "18:00",
            "allowed_days": ["mon", "tue", "wed", "thu", "fri"]
        }

        for phone in config.phone_numbers:
            suppressed, dnc_reason = await lead_importer.is_suppressed(phone)
            if suppressed:
                result.failures.append(f"Phone number {self.mask_phone(phone)} is suppressed: {dnc_reason}")
                self.write_reports(config, result)
                return result

            # Calling window checks
            window_ok, window_err = dialer.is_within_calling_window(mock_campaign, now)
            mock_lead = {
                "phone_number": phone,
                "status": "new",
                "attempt_count": 0,
                "max_attempts": 3,
            }
            lead_callable, lead_callable_err = dialer.lead_is_callable(mock_campaign, mock_lead, now)
            print(f"DEBUG: phone={phone}, window_ok={window_ok}, lead_callable={lead_callable}, allow_now={config.allow_now}, condition={(not window_ok or not lead_callable) and not config.allow_now}")
            if (not window_ok or not lead_callable) and not config.allow_now:
                reason = window_err or lead_callable_err or "Outside calling window."
                result.failures.append(f"Calling window check blocked execution for {self.mask_phone(phone)}: {reason}. Use --allow-now to override.")
                self.write_reports(config, result)
                return result

        # 11. Create or retrieve campaign
        campaign_name = "Dana Live Batch Test Campaign"
        campaign_id = None
        campaigns = await self.repository.query_outbound_campaigns({})
        existing_campaign = None
        for c in campaigns:
            if c.get("name") == campaign_name:
                existing_campaign = c
                break

        calling_start = "00:00" if config.allow_now else "09:30"
        calling_end = "23:59" if config.allow_now else "18:00"
        allowed_days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] if config.allow_now else ["mon", "tue", "wed", "thu", "fri"]

        if existing_campaign:
            campaign_id = existing_campaign["id"].replace("campaign:", "")
            existing_campaign.update({
                "status": "draft",
                "max_concurrent_calls": 1,
                "daily_call_cap": config.max_leads,
                "calling_window_start": calling_start,
                "calling_window_end": calling_end,
                "allowed_days": allowed_days,
                "require_live_mode": True,
                "dnc_scrub_required": True,
            })
            await self.repository.save_outbound_campaign(**existing_campaign)
        else:
            campaign_id = await self.campaign_service.create_campaign(
                name=campaign_name,
                max_concurrent_calls=1,
                daily_call_cap=config.max_leads,
                calling_window_start=calling_start,
                calling_window_end=calling_end,
                allowed_days=allowed_days,
                require_live_mode=True,
                dnc_scrub_required=True,
                operator=config.operator,
            )

        result.campaign_id = campaign_id

        # Loop over numbers sequentially
        for phone in config.phone_numbers:
            call_res = LiveBatchCallResult(phone_number_masked=self.mask_phone(phone))
            result.calls.append(call_res)

            # Check daily call cap logic beforehand
            all_attempts_count = len(await self.repository.query_call_attempts({"campaign_id": campaign_id}))
            if all_attempts_count >= config.max_leads or result.attempted_calls >= config.max_leads:
                # Exceeded attempt count limits
                call_res.failure_reason = "Safety limit: dial attempt cap reached."
                result.failures.append(f"Halted dial loop: attempted calls would exceed max_leads ({config.max_leads}).")
                break

            # Setup lead
            try:
                leads = await self.repository.query_campaign_leads({"campaign_id": campaign_id})
                for l in leads:
                    await self.repository.delete_campaign_lead(l["id"])

                lead_id = str(uuid.uuid4())
                await self.repository.save_campaign_lead(
                    id=lead_id,
                    campaign_id=campaign_id,
                    phone_number=phone,
                    status="new",
                    priority=1,
                    attempt_count=0,
                    max_attempts=3,
                    created_at=now,
                    updated_at=now,
                )
                call_res.lead_id = lead_id
            except Exception as e:
                call_res.failure_reason = f"Lead import failed: {e}"
                result.failures.append(f"Failed to setup lead for number {self.mask_phone(phone)}: {e}")
                break

            # Set campaign status to running
            await self.campaign_service.mark_ready(campaign_id, operator=config.operator)
            await self.campaign_service.start_campaign(campaign_id, operator=config.operator)

            # Dialer tick configuration
            tick_config = DialerTickConfig(
                campaign_id=campaign_id,
                live_mode=not config.dry_run,
                dry_run=config.dry_run,
                max_calls=1,
                operator=config.operator,
                force=config.allow_now,
                now=now,
            )

            # Dialer tick
            tick_res = None
            try:
                tick_res = await dialer.run_tick(tick_config)
                if tick_res.errors:
                    call_res.warnings.extend(tick_res.errors)
                if tick_res.warnings:
                    call_res.warnings.extend(tick_res.warnings)
            except Exception as e:
                call_res.failure_reason = f"Dialer tick failed: {e}"
                result.failures.append(f"Dialer tick failed: {e}")
                break

            # In dry-run mode, simulate successfully placing call
            if config.dry_run:
                # Check if mock or real returned attempt
                if tick_res and tick_res.attempt_ids:
                    attempt_id = tick_res.attempt_ids[0]
                else:
                    attempt_id = str(uuid.uuid4())
                
                call_res.call_attempt_id = attempt_id
                call_res.phone_rang = True
                call_res.answered = True
                call_res.dana_spoke = True
                call_res.turn_count = 2
                call_res.agent_turn_count = 1
                call_res.prospect_turn_count = 1
                call_res.transcript_captured = "yes"
                call_res.call_outcome = "completed"
                call_res.post_call_export_path = f"data/imports/post_call_payloads/{attempt_id}.json"
                call_res.intake_status = "staged"
                
                result.attempted_calls += 1
                result.completed_calls += 1
                result.exported_calls += 1
                result.intake_imported_examples += 1
                
                # Stop campaign
                await self.campaign_service.stop_campaign(campaign_id, operator=config.operator)
                continue

            # Live run tracking
            if tick_res and tick_res.attempt_ids:
                attempt_id = tick_res.attempt_ids[0]
                call_res.call_attempt_id = attempt_id
                result.attempted_calls += 1

                # Save metadata overrides to CallAttempt so worker sees them
                try:
                    attempt_record = await self.repository.get_call_attempt(attempt_id)
                    if attempt_record:
                        attempt_record.setdefault("metadata", {})
                        attempt_record["metadata"]["require_post_call_export"] = config.require_post_call_export
                        attempt_record["metadata"]["run_intake_after_export"] = config.run_intake_after_export
                        attempt_record["metadata"]["min_agent_turns"] = config.min_agent_turns
                        attempt_record["metadata"]["min_prospect_turns"] = config.min_prospect_turns
                        await self.repository.save_call_attempt(**attempt_record)
                except Exception as e:
                    call_res.warnings.append(f"Failed to save testing requirements to CallAttempt: {e}")

                # Polling loop
                max_polls = config.per_call_timeout_seconds
                compliance_failure = False
                worker_disconnected = False

                for _ in range(max_polls):
                    attempt = await self.repository.get_call_attempt(attempt_id)
                    if attempt:
                        call_res.selected_did = attempt.get("metadata", {}).get("selected_caller_id") or attempt.get("caller_id")
                        call_res.caller_id_source = attempt.get("metadata", {}).get("caller_id_source")
                        call_res.livekit_room_name = attempt.get("livekit_room_name")
                        call_res.sip_call_id = attempt.get("livekit_sip_call_id")
                        call_res.participant_id = attempt.get("livekit_participant_id")
                        call_res.post_call_export_path = attempt.get("post_call_export_path")
                        call_res.call_outcome = attempt.get("outcome")

                        status = attempt.get("status")
                        if status in ("ringing", "answered", "in_progress", "completed"):
                            call_res.phone_rang = True
                        if status in ("answered", "in_progress", "completed"):
                            call_res.answered = True
                        if status == "failed":
                            call_res.failure_reason = f"Call failed: {attempt.get('failure_reason')}"
                            break

                    # Check turns
                    try:
                        turns = await self.repository.query_call_turns({"call_id": attempt_id})
                        call_res.turn_count = len(turns)
                        call_res.agent_turn_count = sum(1 for t in turns if t.get("speaker") == "agent")
                        call_res.prospect_turn_count = sum(1 for t in turns if t.get("speaker") == "prospect")
                        call_res.transcript_captured = "yes" if turns else "no"

                        # Check for compliance warning turns
                        for t in turns:
                            warnings = t.get("compliance_warnings") or []
                            if warnings:
                                call_res.warnings.extend(warnings)
                                compliance_failure = True
                    except Exception as e:
                        pass

                    # Check active session
                    try:
                        sessions = await self.repository.query_live_call_sessions({"attempt_id": attempt_id})
                        if sessions:
                            session = sessions[0]
                            if session.get("status") == "active" or session.get("current_stage") is not None:
                                call_res.dana_spoke = True
                            
                            # If session status is failed or worker disconnect recorded
                            if session.get("status") == "failed" and "disconnect" in str(session.get("outcome")).lower():
                                worker_disconnected = True
                    except Exception as e:
                        pass

                    # Check terminal state
                    if attempt and attempt.get("status") in ("completed", "failed", "cancelled"):
                        await asyncio.sleep(2.0)
                        final_attempt = await self.repository.get_call_attempt(attempt_id)
                        if final_attempt:
                            call_res.post_call_export_path = final_attempt.get("post_call_export_path")
                            call_res.call_outcome = final_attempt.get("outcome")
                            meta = final_attempt.get("metadata", {})
                            if meta.get("intake_run"):
                                call_res.intake_status = "staged" if meta.get("intake_result") else "failed"
                        break

                    await asyncio.sleep(1.0)

                # Stop campaign
                await self.campaign_service.stop_campaign(campaign_id, operator=config.operator)

                # Check if call was successful
                attempt_ok = call_res.call_outcome not in ("failed", None)
                turns_ok = True
                if config.require_turns:
                    if call_res.agent_turn_count < config.min_agent_turns or call_res.prospect_turn_count < config.min_prospect_turns:
                        turns_ok = False
                        call_res.failure_reason = "Insufficient turns."

                export_ok = True
                if config.require_post_call_export and not call_res.post_call_export_path:
                    export_ok = False
                    call_res.failure_reason = "Export failed."

                if attempt_ok and turns_ok and export_ok:
                    result.completed_calls += 1
                    if call_res.post_call_export_path:
                        result.exported_calls += 1
                    if call_res.intake_status == "staged":
                        result.intake_imported_examples += 1
                else:
                    result.failed_calls += 1

                # Safety checks to halt the entire batch test campaign
                if compliance_failure:
                    call_res.failure_reason = "Compliance critical failure detected."
                    result.failures.append("Compliance critical failure occurred. Halting batch campaign test.")
                    break

                if worker_disconnected or (call_res.answered and not call_res.dana_spoke):
                    call_res.failure_reason = "Worker disconnect or initialization failure."
                    result.failures.append("LiveKit agent worker disconnected. Halting batch campaign test.")
                    break

                if config.require_post_call_export and not call_res.post_call_export_path:
                    result.failures.append("Post-call export failed. Halting batch campaign test.")
                    break

            else:
                call_res.failure_reason = "No call attempt initiated."
                result.failed_calls += 1
                result.failures.append("No call attempt initiated by dialer tick. Halting batch campaign test.")
                break

        # 12. Cleanup & Campaign close
        if config.stop_campaign_after_run:
            try:
                await self.campaign_service.stop_campaign(campaign_id, operator=config.operator)
                result.campaign_stopped = True
            except Exception as e:
                result.warnings.append(f"Failed to stop campaign: {e}")

        # Check if the overall batch succeeded
        # If there are no failures and completed_calls equals attempted_calls and we successfully dialed at least one call
        result.success = len(result.failures) == 0 and result.completed_calls > 0 and result.completed_calls == result.requested_leads

        self.write_reports(config, result)
        return result
