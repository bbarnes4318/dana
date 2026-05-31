import pytest
import os
import json
from unittest.mock import patch, AsyncMock
from scripts.check_live_telephony_readiness import main as readiness_main
from scripts.test_live_outbound_call import main as cli_test_call_main
from scripts.run_outbound_dialer_once import main_async as dialer_main_async

@pytest.mark.asyncio
async def test_check_readiness_cli_outputs_json(tmp_path, capsys):
    # Isolate data directory
    env_patch = {
        "DANA_DATA_DIR": str(tmp_path),
        "DATABASE_URL": "",
        "TELEPHONY_LIVE_MODE": "true",
        "DANA_ENABLE_OUTBOUND_DIALER": "true",
        "LIVEKIT_URL": "wss://test.livekit.cloud",
        "LIVEKIT_API_KEY": "key",
        "LIVEKIT_API_SECRET": "secret",
        "LIVEKIT_SIP_OUTBOUND_TRUNK_ID": "trunk-123",
        "DANA_OUTBOUND_CALLER_ID": "+15550000",
        "DANA_AGENT_WORKER_ENABLED": "true"
    }
    
    with patch.dict(os.environ, env_patch), patch("sys.argv", ["check_live_telephony_readiness.py"]):
        # Mock check_livekit_sdk to return True
        with patch("telephony.live_telephony_readiness.LiveTelephonyReadinessChecker.check_livekit_sdk", return_value=(True, None)):
            with pytest.raises(SystemExit) as excinfo:
                await readiness_main()
            assert excinfo.value.code == 0
            
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["ready"] is True

@pytest.mark.asyncio
async def test_test_live_call_cli_requires_confirmation(tmp_path, capsys):
    env_patch = {
        "DANA_DATA_DIR": str(tmp_path),
        "DATABASE_URL": "",
        "TELEPHONY_LIVE_MODE": "true",
        "DANA_ENABLE_OUTBOUND_DIALER": "true",
    }
    
    # Confirm not "LIVE CALL"
    with patch.dict(os.environ, env_patch), patch("sys.argv", ["test_live_outbound_call.py", "--to", "+15550000", "--operator", "Jimmy", "--confirm", "NOT LIVE"]):
        with pytest.raises(SystemExit) as excinfo:
            await cli_test_call_main()
        assert excinfo.value.code == 1
        
    captured = capsys.readouterr()
    assert "is required to execute real phone calls" in captured.err

@pytest.mark.asyncio
async def test_test_live_call_cli_does_not_call_livekit_when_env_missing(tmp_path, capsys):
    # Missing live environment flags
    env_patch = {
        "DANA_DATA_DIR": str(tmp_path),
        "DATABASE_URL": "",
        "TELEPHONY_LIVE_MODE": "false",
        "DANA_ENABLE_OUTBOUND_DIALER": "false",
    }
    
    # Even if they confirm, they get blocked due to missing env
    with patch.dict(os.environ, env_patch), patch("sys.argv", ["test_live_outbound_call.py", "--to", "+15550000", "--operator", "Jimmy", "--confirm", "LIVE CALL"]):
        with patch("telephony.live_call_tester.LiveCallTester.place_test_call") as mock_place:
            with pytest.raises(SystemExit) as excinfo:
                await cli_test_call_main()
            assert excinfo.value.code == 1
            mock_place.assert_not_called()
            
    captured = capsys.readouterr()
    assert "Live mode is disabled in this environment" in captured.err

@pytest.mark.asyncio
async def test_run_outbound_dialer_live_mode_requires_confirmation(tmp_path):
    env_patch = {
        "DANA_DATA_DIR": str(tmp_path),
        "DATABASE_URL": "",
        "TELEPHONY_LIVE_MODE": "true",
        "DANA_ENABLE_OUTBOUND_DIALER": "true",
    }
    
    # Missing confirmation for --live-mode
    with patch.dict(os.environ, env_patch), patch("sys.argv", ["run_outbound_dialer_once.py", "--campaign-id", "camp-123", "--live-mode"]):
        with patch("ops.training_console.TrainingOperationsConsole.run_dialer_once") as mock_dial:
            exit_code = await dialer_main_async()
            assert exit_code == 1
            mock_dial.assert_not_called()


def test_worker_check_only_cli_outputs_json(capsys):
    from scripts.run_livekit_agent_worker import main as cli_main
    from unittest.mock import patch
    import json
    with patch("sys.argv", ["run_livekit_agent_worker.py", "--check-only"]), \
         patch("scripts.run_livekit_agent_worker.audit_worker_status") as mock_audit:
        from telephony.livekit_agent_worker import WorkerDependencyStatus
        mock_audit.return_value = WorkerDependencyStatus(
            ready=True,
            status="ready",
            livekit_agents_installed=True,
            livekit_plugins_namespace_available=True,
            openai_plugin_available=True,
            silero_vad_plugin_available=True,
            agent_runtime_available=True,
            required_env_present=True
        )
        with pytest.raises(SystemExit) as excinfo:
            cli_main()
        assert excinfo.value.code == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        assert isinstance(data, dict)
        assert data["ready"] is True
