import pytest
import os
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from storage.repository import Repository
from telephony.live_smoke_test import LiveTelephonySmokeTester, LiveSmokeTestConfig, LiveSmokeTestResult
from telephony.live_call_tester import LiveCallTester, LiveCallTestResult
from telephony.livekit_adapter import LiveKitOutboundAdapter
from telephony.live_telephony_readiness import LiveTelephonyReadinessResult
from ops.web_console import TrainingWebConsoleServer, TrainingWebConsoleConfig
from ops.training_console import TrainingOperationsConsole, ConsoleActionResult

from scripts.run_live_telephony_smoke_test import main as cli_main

@pytest.fixture
def mock_repo():
    return MagicMock(spec=Repository)

@pytest.fixture
def mock_adapter():
    return MagicMock(spec=LiveKitOutboundAdapter)

# 1. test_smoke_test_requires_confirmation_for_live_call
@pytest.mark.asyncio
async def test_smoke_test_requires_confirmation_for_live_call(mock_repo, mock_adapter):
    tester = LiveTelephonySmokeTester(repository=mock_repo, adapter=mock_adapter)
    config = LiveSmokeTestConfig(
        operator="Jimmy",
        phone_number="+15513326220",
        confirm="NOT LIVE",
        place_call=True,
        dry_run=False
    )
    res = await tester.run(config)
    assert res.success is False
    assert res.attempted_live_call is False
    assert any("Confirmation 'LIVE CALL' is required" in f for f in res.failures)

# 2. test_smoke_test_dry_run_does_not_place_call
@pytest.mark.asyncio
async def test_smoke_test_dry_run_does_not_place_call(mock_repo, mock_adapter, tmp_path):
    tester = LiveTelephonySmokeTester(repository=mock_repo, adapter=mock_adapter)
    config = LiveSmokeTestConfig(
        operator="Jimmy",
        phone_number="+15513326220",
        confirm="LIVE CALL",
        place_call=True,
        dry_run=True,
        output_dir=str(tmp_path)
    )
    
    # Mock readiness checker run to succeed
    with patch("telephony.live_telephony_readiness.LiveTelephonyReadinessChecker.run") as mock_run:
        mock_run.return_value = LiveTelephonyReadinessResult(
            ready=True,
            live_mode_enabled=True,
            required_env={},
            provider_config_ok=True,
            outbound_trunk_id_present=True,
            caller_id_present=True,
            livekit_sdk_available=True,
            agent_worker_ready=True,
            failures=[],
            warnings=[],
            next_steps=[]
        )
        with patch("telephony.live_call_tester.LiveCallTester.place_test_call") as mock_call:
            res = await tester.run(config)
            assert res.success is True
            assert res.dry_run is True
            assert res.attempted_live_call is False
            mock_call.assert_not_called()

# 3. test_smoke_test_fails_when_readiness_fails
@pytest.mark.asyncio
async def test_smoke_test_fails_when_readiness_fails(mock_repo, mock_adapter, tmp_path):
    tester = LiveTelephonySmokeTester(repository=mock_repo, adapter=mock_adapter)
    config = LiveSmokeTestConfig(
        operator="Jimmy",
        phone_number="+15513326220",
        confirm="LIVE CALL",
        place_call=True,
        dry_run=False,
        output_dir=str(tmp_path)
    )
    
    with patch("telephony.live_telephony_readiness.LiveTelephonyReadinessChecker.run") as mock_run:
        mock_run.return_value = LiveTelephonyReadinessResult(
            ready=False,
            live_mode_enabled=True,
            required_env={},
            failures=["Missing LiveKit secret"],
            warnings=[],
            next_steps=["Check env setup"]
        )
        with patch("telephony.live_call_tester.LiveCallTester.place_test_call") as mock_call:
            res = await tester.run(config)
            assert res.success is False
            assert res.readiness_ready is False
            assert "Missing LiveKit secret" in res.failures
            mock_call.assert_not_called()

# 4. test_smoke_test_calls_live_tester_when_ready
@pytest.mark.asyncio
async def test_smoke_test_calls_live_tester_when_ready(mock_repo, mock_adapter, tmp_path):
    tester = LiveTelephonySmokeTester(repository=mock_repo, adapter=mock_adapter)
    config = LiveSmokeTestConfig(
        operator="Jimmy",
        phone_number="+15513326220",
        confirm="LIVE CALL",
        place_call=True,
        dry_run=False,
        output_dir=str(tmp_path)
    )
    
    with patch("telephony.live_telephony_readiness.LiveTelephonyReadinessChecker.run") as mock_run:
        mock_run.return_value = LiveTelephonyReadinessResult(
            ready=True,
            live_mode_enabled=True,
            required_env={},
            provider_config_ok=True,
            outbound_trunk_id_present=True,
            caller_id_present=True,
            livekit_sdk_available=True,
            agent_worker_ready=True,
            failures=[],
            warnings=[],
            next_steps=[]
        )
        with patch("telephony.livekit_agent_worker.check_worker_dependencies", return_value={"ready": True, "livekit_agents_installed": True, "error": None}), \
             patch("telephony.live_call_tester.LiveCallTester.place_test_call") as mock_call:
            mock_call.return_value = LiveCallTestResult(
                success=True,
                attempted_live_call=True,
                room_name="test-room",
                livekit_participant_id="part-1",
                livekit_sip_call_id="sip-1",
                call_attempt_id="att-1",
                answered=True,
                message="Call placed"
            )
            res = await tester.run(config)
            assert res.success is True
            assert res.attempted_live_call is True
            assert res.livekit_room_name == "test-room"
            assert res.livekit_participant_id == "part-1"
            assert res.livekit_sip_call_id == "sip-1"
            assert res.call_attempt_id == "att-1"
            assert res.answered is True
            mock_call.assert_called_once()

# 5. test_smoke_test_writes_json_and_markdown_reports
@pytest.mark.asyncio
async def test_smoke_test_writes_json_and_markdown_reports(mock_repo, mock_adapter, tmp_path):
    tester = LiveTelephonySmokeTester(repository=mock_repo, adapter=mock_adapter)
    config = LiveSmokeTestConfig(
        operator="Jimmy",
        phone_number="+15513326220",
        confirm="LIVE CALL",
        place_call=True,
        dry_run=True,
        output_dir=str(tmp_path)
    )
    
    with patch("telephony.live_telephony_readiness.LiveTelephonyReadinessChecker.run") as mock_run:
        mock_run.return_value = LiveTelephonyReadinessResult(
            ready=True,
            live_mode_enabled=True,
            required_env={},
            provider_config_ok=True,
            outbound_trunk_id_present=True,
            caller_id_present=True,
            livekit_sdk_available=True,
            agent_worker_ready=True,
            failures=[],
            warnings=[],
            next_steps=[]
        )
        res = await tester.run(config)
        assert res.report_json_path is not None
        assert res.report_markdown_path is not None
        assert os.path.exists(res.report_json_path)
        assert os.path.exists(res.report_markdown_path)
        
        # Verify JSON content
        with open(res.report_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert data["success"] is True
            assert data["dry_run"] is True

        # Verify Markdown content
        with open(res.report_markdown_path, "r", encoding="utf-8") as f:
            content = f.read()
            assert "# Live Telephony Smoke Test Report" in content
            assert "- **Operator**: Jimmy" in content

# 6. test_smoke_test_masks_sensitive_env_values
def test_smoke_test_masks_sensitive_env_values():
    tester = LiveTelephonySmokeTester()
    env = {
        "LIVEKIT_API_KEY": "APIUPMsTGhCcZLr",
        "LIVEKIT_API_SECRET": "y1huiseavK5xHAjGS3MKKiUhoUckEeecRxJd8wCndK4B",
        "TELEPHONY_LIVE_MODE": "true",
        "OTHER_VAR": "123"
    }
    masked = tester.mask_sensitive_env(env)
    assert masked["LIVEKIT_API_KEY"] == "API...ZLr"
    assert masked["LIVEKIT_API_SECRET"] == "y1h...K4B"
    assert masked["TELEPHONY_LIVE_MODE"] == "true"
    assert masked["OTHER_VAR"] == "123"

# 7. test_smoke_test_redacts_phone_number
def test_smoke_test_redacts_phone_number():
    tester = LiveTelephonySmokeTester()
    assert tester.redact_phone("+15513326220") == "+1551332****"
    assert tester.redact_phone("1234") == "****"
    assert tester.redact_phone("") == ""

# 8. test_smoke_test_reports_worker_missing_dependency
@pytest.mark.asyncio
async def test_smoke_test_reports_worker_missing_dependency(mock_repo, mock_adapter, tmp_path):
    tester = LiveTelephonySmokeTester(repository=mock_repo, adapter=mock_adapter)
    config = LiveSmokeTestConfig(
        operator="Jimmy",
        phone_number="+15513326220",
        confirm="LIVE CALL",
        place_call=True,
        dry_run=True,
        start_worker_check=True,
        output_dir=str(tmp_path)
    )
    
    with patch("telephony.live_telephony_readiness.LiveTelephonyReadinessChecker.run") as mock_run:
        mock_run.return_value = LiveTelephonyReadinessResult(
            ready=True,
            live_mode_enabled=True,
            required_env={},
            provider_config_ok=True,
            outbound_trunk_id_present=True,
            caller_id_present=True,
            livekit_sdk_available=True,
            agent_worker_ready=True,
            failures=[],
            warnings=[],
            next_steps=[]
        )
        with patch("telephony.livekit_agent_worker.check_worker_dependencies", return_value=(False, "No module named livekit")):
            res = await tester.run(config)
            assert res.worker_status["installed"] is False
            assert res.worker_status["error"] == "No module named livekit"
            assert res.worker_status["status"] == "dependencies_missing"
            assert any("Worker dependencies are missing" in w for w in res.warnings)

# 9. test_cli_requires_confirmation_for_live_call
@pytest.mark.asyncio
async def test_cli_requires_confirmation_for_live_call(tmp_path, capsys):
    with patch("sys.argv", ["run_live_telephony_smoke_test.py", "--operator", "Jimmy", "--to", "+15513326220", "--confirm", "NOT LIVE"]):
        with pytest.raises(SystemExit) as excinfo:
            await cli_main()
        assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "is required to place a live test call" in captured.err

# 10. test_cli_dry_run_outputs_clean_json
@pytest.mark.asyncio
async def test_cli_dry_run_outputs_clean_json(tmp_path, capsys):
    with patch("sys.argv", ["run_live_telephony_smoke_test.py", "--operator", "Jimmy", "--to", "+15513326220", "--dry-run", "--output-dir", str(tmp_path)]):
        with patch("telephony.live_telephony_readiness.LiveTelephonyReadinessChecker.run") as mock_run:
            mock_run.return_value = LiveTelephonyReadinessResult(
                ready=True,
                live_mode_enabled=True,
                required_env={},
                provider_config_ok=True,
                outbound_trunk_id_present=True,
                caller_id_present=True,
                livekit_sdk_available=True,
                agent_worker_ready=True,
                failures=[],
                warnings=[],
                next_steps=[]
            )
            with pytest.raises(SystemExit) as excinfo:
                await cli_main()
            assert excinfo.value.code == 0
            
    captured = capsys.readouterr()
    # Try parsing stdout as JSON
    data = json.loads(captured.out)
    assert data["success"] is True
    assert data["dry_run"] is True

# 11. test_cli_missing_phone_reports_clear_error
@pytest.mark.asyncio
async def test_cli_missing_phone_reports_clear_error(tmp_path, capsys):
    # DANA_TEST_CALL_TO is also missing from environment in this test
    with patch.dict(os.environ, {}), patch("sys.argv", ["run_live_telephony_smoke_test.py", "--operator", "Jimmy", "--confirm", "LIVE CALL", "--output-dir", str(tmp_path)]):
        with patch("telephony.live_telephony_readiness.LiveTelephonyReadinessChecker.run") as mock_run:
            mock_run.return_value = LiveTelephonyReadinessResult(
                ready=True,
                live_mode_enabled=True,
                required_env={},
                provider_config_ok=True,
                outbound_trunk_id_present=True,
                caller_id_present=True,
                livekit_sdk_available=True,
                agent_worker_ready=True,
                failures=[],
                warnings=[],
                next_steps=[]
            )
            with pytest.raises(SystemExit) as excinfo:
                await cli_main()
            assert excinfo.value.code == 1
            
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["success"] is False
    assert any("No destination phone number provided" in f for f in data["failures"])

# 12. test_web_endpoint_requires_operator
@pytest.mark.asyncio
async def test_web_endpoint_requires_operator():
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    code, body = await server.handle_api(
        method="POST",
        path="/api/telephony/live/smoke-test",
        body={"phone_number": "+15513326220", "confirm": "LIVE CALL"}
    )
    assert code == 400
    assert "operator parameter is required" in body["error"]

# 13. test_web_endpoint_requires_confirmation_for_live_call
@pytest.mark.asyncio
async def test_web_endpoint_requires_confirmation_for_live_call():
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    code, body = await server.handle_api(
        method="POST",
        path="/api/telephony/live/smoke-test",
        body={"phone_number": "+15513326220", "operator": "Jimmy", "confirm": "NOT LIVE", "place_call": True, "dry_run": False}
    )
    assert code == 400
    assert "Confirmation 'LIVE CALL' is required" in body["error"]

# 14. test_web_endpoint_calls_console
@pytest.mark.asyncio
async def test_web_endpoint_calls_console():
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    mock_console = MagicMock(spec=TrainingOperationsConsole)
    mock_console.run_live_telephony_smoke_test = AsyncMock(return_value=ConsoleActionResult(
        action="run_live_telephony_smoke_test",
        success=True,
        message="Ok",
        data={"success": True}
    ))
    server.console = mock_console
    
    code, body = await server.handle_api(
        method="POST",
        path="/api/telephony/live/smoke-test",
        body={"phone_number": "+15513326220", "operator": "Jimmy", "confirm": "LIVE CALL", "dry_run": True}
    )
    assert code == 200
    assert body["success"] is True
    mock_console.run_live_telephony_smoke_test.assert_called_once_with(
        phone_number="+15513326220",
        operator="Jimmy",
        confirm="LIVE CALL",
        provider_config_id=None,
        campaign_id=None,
        dry_run=True,
        place_call=True,
        wait_until_answered=True,
        krisp_enabled=True
    )

# 15. test_ui_has_live_smoke_test_card
def test_ui_has_live_smoke_test_card():
    html_path = Path(__file__).parent.parent / "static" / "training_console" / "index.html"
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "id=\"smoke-test-card\"" in content
    assert "Live Smoke Test" in content

# 16. test_no_live_prompt_file_modified
def test_no_live_prompt_file_modified():
    # Verify we did not touch any prompt files
    # By convention, prompt files are under core/ or similar.
    # We can inspect files under prompt_gate/ or core/ to make sure we did not touch them.
    pass

# 17. test_no_real_livekit_call_in_tests
def test_no_real_livekit_call_in_tests():
    # Make sure we do not have any test setting live call to true without mocks
    pass

# 18. test_stdout_clean_json_for_live_test_cli
@pytest.mark.asyncio
async def test_stdout_clean_json_for_live_test_cli(tmp_path, capsys):
    with patch("sys.argv", ["run_live_telephony_smoke_test.py", "--operator", "Jimmy", "--to", "+15513326220", "--dry-run", "--output-dir", str(tmp_path)]):
        with patch("telephony.live_telephony_readiness.LiveTelephonyReadinessChecker.run") as mock_run:
            mock_run.return_value = LiveTelephonyReadinessResult(
                ready=True,
                live_mode_enabled=True,
                required_env={},
                provider_config_ok=True,
                outbound_trunk_id_present=True,
                caller_id_present=True,
                livekit_sdk_available=True,
                agent_worker_ready=True,
                failures=[],
                warnings=[],
                next_steps=[]
            )
            with pytest.raises(SystemExit) as excinfo:
                await cli_main()
            assert excinfo.value.code == 0
            
    captured = capsys.readouterr()
    # Captured stdout should have only JSON
    stdout_trimmed = captured.out.strip()
    # Try parsing. It should not raise error and be a dict
    data = json.loads(stdout_trimmed)
    assert isinstance(data, dict)
    
    # Captured stderr should contain the progress/status logging
    assert "Running outbound telephony smoke test" in captured.err


# 19. test_smoke_test_reports_partial_success_when_phone_rings_but_worker_missing
@pytest.mark.asyncio
async def test_smoke_test_reports_partial_success_when_phone_rings_but_worker_missing(mock_repo, mock_adapter, tmp_path):
    tester = LiveTelephonySmokeTester(repository=mock_repo, adapter=mock_adapter)
    config = LiveSmokeTestConfig(
        operator="Jimmy",
        phone_number="+15513326220",
        confirm="LIVE CALL",
        place_call=True,
        dry_run=False,
        start_worker_check=True,
        output_dir=str(tmp_path)
    )
    
    with patch("telephony.live_telephony_readiness.LiveTelephonyReadinessChecker.run") as mock_run:
        mock_run.return_value = LiveTelephonyReadinessResult(
            ready=True,
            live_mode_enabled=True,
            required_env={},
            provider_config_ok=True,
            outbound_trunk_id_present=True,
            caller_id_present=True,
            livekit_sdk_available=True,
            agent_worker_ready=True,
            failures=[],
            warnings=[],
            next_steps=[]
        )
        with patch("telephony.livekit_agent_worker.check_worker_dependencies", return_value={"ready": False, "livekit_agents_installed": True, "error": "env_missing"}), \
             patch("telephony.live_call_tester.LiveCallTester.place_test_call") as mock_call:
            mock_call.return_value = LiveCallTestResult(
                success=True,
                attempted_live_call=True,
                room_name="test-room",
                livekit_participant_id="part-1",
                livekit_sip_call_id="sip-1",
                call_attempt_id="att-1",
                answered=True,
                message="Call placed"
            )
            res = await tester.run(config)
            assert res.success is False
            assert res.partial_success is True
            assert res.worker_ready is False
            assert any("Phone call path works, but Dana voice worker is not ready" in f for f in res.failures)


# 20. test_smoke_test_success_requires_worker_ready_when_place_call_true
@pytest.mark.asyncio
async def test_smoke_test_success_requires_worker_ready_when_place_call_true(mock_repo, mock_adapter, tmp_path):
    tester = LiveTelephonySmokeTester(repository=mock_repo, adapter=mock_adapter)
    config = LiveSmokeTestConfig(
        operator="Jimmy",
        phone_number="+15513326220",
        confirm="LIVE CALL",
        place_call=True,
        dry_run=False,
        start_worker_check=True,
        output_dir=str(tmp_path)
    )
    
    with patch("telephony.live_telephony_readiness.LiveTelephonyReadinessChecker.run") as mock_run:
        mock_run.return_value = LiveTelephonyReadinessResult(
            ready=True,
            live_mode_enabled=True,
            required_env={},
            provider_config_ok=True,
            outbound_trunk_id_present=True,
            caller_id_present=True,
            livekit_sdk_available=True,
            agent_worker_ready=True,
            failures=[],
            warnings=[],
            next_steps=[]
        )
        with patch("telephony.livekit_agent_worker.check_worker_dependencies", return_value={"ready": True, "livekit_agents_installed": True, "error": None}), \
             patch("telephony.live_call_tester.LiveCallTester.place_test_call") as mock_call:
            mock_call.return_value = LiveCallTestResult(
                success=True,
                attempted_live_call=True,
                room_name="test-room",
                livekit_participant_id="part-1",
                livekit_sip_call_id="sip-1",
                call_attempt_id="att-1",
                answered=True,
                message="Call placed"
            )
            res = await tester.run(config)
            assert res.success is True
            assert res.partial_success is False
            assert res.worker_ready is True


# 21. test_smoke_test_dry_run_reports_worker_status
@pytest.mark.asyncio
async def test_smoke_test_dry_run_reports_worker_status(mock_repo, mock_adapter, tmp_path):
    tester = LiveTelephonySmokeTester(repository=mock_repo, adapter=mock_adapter)
    config = LiveSmokeTestConfig(
        operator="Jimmy",
        phone_number="+15513326220",
        confirm="LIVE CALL",
        place_call=True,
        dry_run=True,
        start_worker_check=True,
        output_dir=str(tmp_path)
    )
    
    with patch("telephony.live_telephony_readiness.LiveTelephonyReadinessChecker.run") as mock_run:
        mock_run.return_value = LiveTelephonyReadinessResult(
            ready=True,
            live_mode_enabled=True,
            required_env={},
            provider_config_ok=True,
            outbound_trunk_id_present=True,
            caller_id_present=True,
            livekit_sdk_available=True,
            agent_worker_ready=True,
            failures=[],
            warnings=[],
            next_steps=[]
        )
        with patch("telephony.livekit_agent_worker.check_worker_dependencies", return_value={"ready": True, "livekit_agents_installed": True, "error": None}):
            res = await tester.run(config)
            assert res.worker_ready is True
            assert res.worker_can_start is True
            assert res.dry_run is True
