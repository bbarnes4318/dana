import os
import sys

# Safety fallback loading
try:
    from config.env_loader import load_environment
    from config.runtime_env import get_runtime_env
    load_environment()
except ImportError:
    from pathlib import Path
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from config.env_loader import load_environment
    from config.runtime_env import get_runtime_env
    load_environment()

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Dict, List, Tuple
from pathlib import Path
from pydantic import BaseModel, Field

from storage.repository import Repository
from telephony.livekit_adapter import LiveKitOutboundAdapter
from telephony.live_telephony_readiness import LiveTelephonyReadinessChecker
from telephony.live_call_tester import LiveCallTester, LiveCallTestConfig

class LiveSmokeTestConfig(BaseModel):
    """Configuration for running the live outbound telephony smoke test."""
    phone_number: Optional[str] = None
    operator: str
    confirm: str
    provider_config_id: Optional[str] = None
    campaign_id: Optional[str] = None
    start_worker_check: bool = True
    place_call: bool = True
    wait_until_answered: bool = True
    krisp_enabled: bool = True
    dry_run: bool = False
    output_dir: str = "data/live_smoke_tests"


class LiveSmokeTestResult(BaseModel):
    """Detailed result containing checklists, masking status, and output report paths."""
    success: bool
    dry_run: bool
    attempted_live_call: bool
    readiness_ready: bool
    readiness: Dict[str, Any] = Field(default_factory=dict)
    worker_status: Dict[str, Any] = Field(default_factory=dict)
    test_call_result: Dict[str, Any] = Field(default_factory=dict)
    phone_number_redacted: Optional[str] = None
    call_attempt_id: Optional[str] = None
    livekit_room_name: Optional[str] = None
    livekit_participant_id: Optional[str] = None
    livekit_sip_call_id: Optional[str] = None
    answered: bool = False
    report_json_path: Optional[str] = None
    report_markdown_path: Optional[str] = None
    failures: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    next_steps: List[str] = Field(default_factory=list)

    # New validation fields
    worker_ready: bool = False
    worker_can_start: bool = False
    expected_agent_join: bool = False
    expected_agent_speech: bool = False
    partial_success: bool = False


class LiveTelephonySmokeTester:
    """Orchestrates live outbound telephony smoke tests and logs results securely."""

    def __init__(self, repository: Optional[Repository] = None, adapter: Optional[LiveKitOutboundAdapter] = None) -> None:
        self.repository = repository or Repository()
        self.adapter = adapter or LiveKitOutboundAdapter()

    def mask_sensitive_env(self, env_status: dict) -> dict:
        """Mask sensitive keys to prevent secrets leaking in log reports."""
        masked = {}
        for k, v in env_status.items():
            if not v:
                masked[k] = v
            elif "SECRET" in k or "KEY" in k:
                # Keep first 3 and last 3 chars, mask the rest
                if len(v) <= 6:
                    masked[k] = "***"
                else:
                    masked[k] = v[:3] + "..." + v[-3:]
            else:
                masked[k] = v
        return masked

    def redact_phone(self, phone: str) -> str:
        """Redact the last 4 digits of a phone number."""
        if not phone:
            return ""
        phone = phone.strip()
        if len(phone) > 4:
            return phone[:-4] + "****"
        return "****"

    def write_reports(self, config: LiveSmokeTestConfig, result: LiveSmokeTestResult) -> Tuple[str, str]:
        """Save report files under config.output_dir and update result paths."""
        os.makedirs(config.output_dir, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        test_id = result.call_attempt_id or str(uuid.uuid4())[:8]

        json_path = os.path.join(config.output_dir, f"smoke_test_{timestamp}_{test_id}.json")
        md_path = os.path.join(config.output_dir, f"smoke_test_{timestamp}_{test_id}.md")

        # Set paths in result
        result.report_json_path = json_path
        result.report_markdown_path = md_path

        # Write JSON Report
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result.model_dump(mode="json"), f, indent=2)

        # Write Markdown Report
        md_lines = [
            "# Live Telephony Smoke Test Report",
            "",
            f"- **Timestamp**: {datetime.now(timezone.utc).isoformat()}",
            f"- **Operator**: {config.operator}",
            f"- **Destination Phone**: {result.phone_number_redacted or 'N/A'}",
            f"- **Status**: {'🔴 FAILURE' if not result.success else '🟢 SUCCESS'}",
            f"- **Dry Run**: {result.dry_run}",
            f"- **Attempted Live Call**: {result.attempted_live_call}",
            "",
            "## 📋 Readiness Checklist",
            f"- Live Mode Enabled: {'🟢' if result.readiness.get('live_mode_enabled') else '🔴'}",
            f"- LiveKit SDK Available: {'🟢' if result.readiness.get('livekit_sdk_available') else '🔴'}",
            f"- Provider Config OK: {'🟢' if result.readiness.get('provider_config_ok') else '🔴'}",
            f"- Outbound Trunk ID Present: {'🟢' if result.readiness.get('outbound_trunk_id_present') else '🔴'}",
            f"- Caller ID Present: {'🟢' if result.readiness.get('caller_id_present') else '🔴'}",
            f"- Worker Ready: {'🟢' if result.readiness.get('agent_worker_ready') else '🔴'}",
            "",
            "## 🛠️ Environment Variables Status (Masked)",
        ]

        env_status = result.readiness.get("required_env", {})
        for k, v in env_status.items():
            md_lines.append(f"- **{k}**: `{v or 'Not Set'}`")

        md_lines.extend([
            "",
            "## 🤖 Agent Worker Status",
            f"- Installed: {result.worker_status.get('installed')}",
            f"- Enabled: {result.worker_status.get('enabled')}",
            f"- Worker Status: `{result.worker_status.get('status')}`",
            f"- Errors: `{result.worker_status.get('error') or 'None'}`",
            "",
            "## 📞 Outbound Test Call Result",
            f"- Room Name: `{result.livekit_room_name or 'N/A'}`",
            f"- Participant ID: `{result.livekit_participant_id or 'N/A'}`",
            f"- SIP Call ID: `{result.livekit_sip_call_id or 'N/A'}`",
            f"- Call Attempt ID: `{result.call_attempt_id or 'N/A'}`",
            f"- Answered: {result.answered}",
        ])

        if result.failures:
            md_lines.extend(["", "## ❌ Failures", ""])
            for f in result.failures:
                md_lines.append(f"- {f}")

        if result.warnings:
            md_lines.extend(["", "## ⚠️ Warnings", ""])
            for w in result.warnings:
                md_lines.append(f"- {w}")

        if result.next_steps:
            md_lines.extend(["", "## 🚀 Next Steps", ""])
            for ns in result.next_steps:
                md_lines.append(f"- {ns}")

        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines))

        return json_path, md_path

    async def run(self, config: LiveSmokeTestConfig) -> LiveSmokeTestResult:
        """Execute the readiness checks and optional live outbound call."""
        failures = []
        warnings = []
        next_steps = []

        # 1. Verify operator ID
        if not config.operator or not config.operator.strip():
            failures.append("Operator parameter is required.")
            next_steps.append("Specify operator parameter (e.g. --operator Jimmy).")
            return LiveSmokeTestResult(
                success=False,
                dry_run=config.dry_run,
                attempted_live_call=False,
                readiness_ready=False,
                failures=failures,
                next_steps=next_steps
            )

        # 2. Check confirmation for live call
        if config.place_call and not config.dry_run and config.confirm != "LIVE CALL":
            failures.append("Confirmation 'LIVE CALL' is required to place a live test call.")
            next_steps.append("Specify exact confirmation: --confirm \"LIVE CALL\" to run a live call.")
            return LiveSmokeTestResult(
                success=False,
                dry_run=config.dry_run,
                attempted_live_call=False,
                readiness_ready=False,
                failures=failures,
                next_steps=next_steps
            )

        # 3. Resolve destination phone number
        env = get_runtime_env()
        phone_number = config.phone_number or env["test_call_to"]
        phone_redacted = self.redact_phone(phone_number) if phone_number else None

        if config.place_call and not config.dry_run and not phone_number:
            failures.append("No destination phone number provided.")
            next_steps.append("Define DANA_TEST_CALL_TO environment variable or pass --to phone_number.")
            return LiveSmokeTestResult(
                success=False,
                dry_run=config.dry_run,
                attempted_live_call=False,
                readiness_ready=False,
                failures=failures,
                next_steps=next_steps
            )

        # 4. Check readiness
        readiness_checker = LiveTelephonyReadinessChecker(repository=self.repository, adapter=self.adapter)
        readiness_res = await readiness_checker.run(
            provider_config_id=config.provider_config_id,
            campaign_id=config.campaign_id
        )

        # Mask sensitive variables in stored readiness report
        masked_env = self.mask_sensitive_env(readiness_res.required_env)
        readiness_dict = readiness_res.model_dump(mode="json")
        readiness_dict["required_env"] = masked_env

        readiness_ready = readiness_res.ready
        if not readiness_ready:
            failures.extend(readiness_res.failures)
            warnings.extend(readiness_res.warnings)
            next_steps.extend(readiness_res.next_steps)

        # 5. Check worker status
        worker_ready = False
        worker_status = {}
        worker_can_start = False
        expected_agent_join = False
        expected_agent_speech = False

        if config.start_worker_check:
            try:
                from telephony.livekit_agent_worker import check_worker_dependencies
                status_dict = check_worker_dependencies()
                if isinstance(status_dict, (tuple, list)):
                    worker_ok = status_dict[0]
                    worker_err = status_dict[1]
                    worker_ready = worker_ok
                    worker_can_start = worker_ok
                else:
                    worker_ready = status_dict.get("ready", False)
                    worker_ok = status_dict.get("livekit_agents_installed", False)
                    worker_err = status_dict.get("error")
                    worker_can_start = (
                        status_dict.get("livekit_agents_installed", False) and
                        status_dict.get("agent_runtime_available", True)
                    )
            except Exception as e:
                worker_ok = False
                worker_err = str(e)
                worker_ready = False
                worker_can_start = False

            worker_enabled = env["worker_enabled"]
            worker_status = {
                "installed": worker_ok,
                "error": worker_err,
                "enabled": worker_enabled,
                "status": "ready" if (worker_ready and worker_enabled) else ("disabled" if worker_ok else "dependencies_missing")
            }

            expected_agent_join = worker_ready and worker_enabled
            expected_agent_speech = expected_agent_join

            if not worker_ok:
                warnings.append(f"Worker dependencies are missing: {worker_err}")
                next_steps.append("Install required agent packages: pip install livekit-api livekit-agents")
            elif not worker_enabled:
                warnings.append("DANA_AGENT_WORKER_ENABLED is not set to 'true'. Agent worker will not join rooms.")
                next_steps.append("Set DANA_AGENT_WORKER_ENABLED=true and start worker process.")
            elif not worker_ready:
                warnings.append(f"Worker check failed: {worker_status.get('status')}. Error: {worker_err}")
                next_steps.append("Verify all required environment variables and provider keys are set.")

        # If readiness failed, we stop here and do not dial
        if not readiness_ready:
            result = LiveSmokeTestResult(
                success=False,
                dry_run=config.dry_run,
                attempted_live_call=False,
                readiness_ready=False,
                readiness=readiness_dict,
                worker_status=worker_status,
                phone_number_redacted=phone_redacted,
                failures=failures,
                warnings=warnings,
                next_steps=next_steps,
                worker_ready=worker_ready,
                worker_can_start=worker_can_start,
                expected_agent_join=expected_agent_join,
                expected_agent_speech=expected_agent_speech,
                partial_success=False
            )
            self.write_reports(config, result)
            return result

        # 6. Check dry run or no-place-call
        if config.dry_run or not config.place_call:
            result = LiveSmokeTestResult(
                success=True,
                dry_run=config.dry_run,
                attempted_live_call=False,
                readiness_ready=True,
                readiness=readiness_dict,
                worker_status=worker_status,
                phone_number_redacted=phone_redacted,
                failures=failures,
                warnings=warnings,
                next_steps=next_steps,
                worker_ready=worker_ready,
                worker_can_start=worker_can_start,
                expected_agent_join=expected_agent_join,
                expected_agent_speech=expected_agent_speech,
                partial_success=False
            )
            self.write_reports(config, result)
            return result

        # 7. Execute real outbound call via LiveCallTester
        tester = LiveCallTester(repository=self.repository, adapter=self.adapter)
        tester_config = LiveCallTestConfig(
            phone_number=phone_number,
            campaign_id=config.campaign_id,
            provider_config_id=config.provider_config_id,
            live_mode=True,
            wait_until_answered=config.wait_until_answered,
            krisp_enabled=config.krisp_enabled,
            operator=config.operator,
            export_to_training=True
        )

        test_res = await tester.place_test_call(tester_config)
        
        # Populate result fields
        test_call_dict = test_res.model_dump(mode="json")
        
        success = test_res.success
        partial_success = False

        if success:
            if not worker_ready:
                success = False
                partial_success = True
                failures.append("Phone call path works, but Dana voice worker is not ready.")
                next_steps.append("Install/start worker before expecting Dana to speak.")
        else:
            failures.append(test_res.message)
            if test_res.error:
                failures.append(f"Error Code: {test_res.error}")

        result = LiveSmokeTestResult(
            success=success,
            dry_run=config.dry_run,
            attempted_live_call=test_res.attempted_live_call,
            readiness_ready=True,
            readiness=readiness_dict,
            worker_status=worker_status,
            test_call_result=test_call_dict,
            phone_number_redacted=phone_redacted,
            call_attempt_id=test_res.call_attempt_id,
            livekit_room_name=test_res.room_name,
            livekit_participant_id=test_res.livekit_participant_id,
            livekit_sip_call_id=test_res.livekit_sip_call_id,
            answered=test_res.answered,
            failures=failures,
            warnings=warnings,
            next_steps=next_steps,
            worker_ready=worker_ready,
            worker_can_start=worker_can_start,
            expected_agent_join=expected_agent_join,
            expected_agent_speech=expected_agent_speech,
            partial_success=partial_success
        )
        self.write_reports(config, result)
        return result
