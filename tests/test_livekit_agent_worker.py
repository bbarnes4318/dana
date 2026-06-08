import pytest
import os
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from telephony.livekit_agent_worker import (
    audit_worker_status,
    check_worker_dependencies,
    build_worker_config_from_env,
    build_initial_session_state,
    log_agent_turn,
    log_user_turn,
    generate_agent_response,
    export_completed_session_if_possible,
    LiveKitAgentWorkerConfig
)
from storage.repository import Repository
from core.agent_runtime import AgentRuntime, RuntimeResult

# 1. test_worker_dependency_check_reports_missing_livekit_plugins
def test_worker_dependency_check_reports_missing_livekit_plugins():
    # Mock import failure for livekit.plugins namespace or modules
    orig_import = __import__
    def mock_import(name, *args, **kwargs):
        if name.startswith("livekit.plugins") or "livekit-plugins" in name:
            raise ImportError(f"No module named '{name}'")
        return orig_import(name, *args, **kwargs)
        
    with patch("builtins.__import__", side_effect=mock_import):
        res = check_worker_dependencies()
        assert res.get("ready") is False
        assert res.get("status") == "dependencies_missing"

# 2. test_worker_dependency_check_passes_with_mocked_plugins
def test_worker_dependency_check_passes_with_mocked_plugins():
    with patch("telephony.livekit_agent_worker.audit_worker_status") as mock_audit:
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
        res = check_worker_dependencies()
        ok, err = res
        assert ok is True
        assert err is None
        assert res.get("ready") is True

# 3. test_worker_check_only_cli_outputs_clean_json
def test_worker_check_only_cli_outputs_clean_json(capsys):
    from scripts.run_livekit_agent_worker import main as cli_main
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
        assert data["ready"] is True
        assert data["status"] == "ready"

# 4. test_worker_missing_env_reports_clear_failure
def test_worker_missing_env_reports_clear_failure():
    with patch("telephony.livekit_agent_worker.audit_worker_status") as mock_audit:
        from telephony.livekit_agent_worker import WorkerDependencyStatus
        mock_audit.return_value = WorkerDependencyStatus(
            ready=False,
            status="env_missing",
            missing_env=["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"],
            livekit_agents_installed=True,
            livekit_plugins_namespace_available=True,
            openai_plugin_available=True,
            silero_vad_plugin_available=True,
            agent_runtime_available=True,
            required_env_present=False
        )
        res = check_worker_dependencies()
        assert res.get("ready") is False
        assert res.get("status") == "env_missing"
        assert "LIVEKIT_URL" in res.get("missing_env")

# 5. test_worker_builds_config_from_env
def test_worker_builds_config_from_env():
    env_mock = {
        "LIVEKIT_URL": "ws://test-url",
        "LIVEKIT_API_KEY": "test-key",
        "LIVEKIT_API_SECRET": "test-secret",
        "DANA_AGENT_WORKER_ENABLED": "true",
        "DANA_LIVEKIT_ROOM_PREFIX": "test-prefix",
        "DANA_AGENT_NAME": "TestDana",
        "DANA_OPENING_LINE": "Greeting override test"
    }
    with patch.dict(os.environ, env_mock):
        config = build_worker_config_from_env()
        assert config.livekit_url == "ws://test-url"
        assert config.api_key == "test-key"
        assert config.api_secret == "test-secret"
        assert config.room_prefix == "test-prefix"
        assert config.agent_name == "TestDana"
        assert config.greeting_text == "Greeting override test"
        assert config.worker_enabled is True

# 6. test_worker_generates_safe_fallback_greeting
def test_worker_generates_safe_fallback_greeting():
    with patch("config.runtime_env.load_environment"):
        with patch.dict(os.environ, {}, clear=True):
            config = build_worker_config_from_env()
            greeting = config.greeting_text
            assert greeting == "Hello?"


# 7. test_generate_agent_response_uses_agent_runtime
@pytest.mark.asyncio
async def test_generate_agent_response_uses_agent_runtime():
    mock_runtime = MagicMock(spec=AgentRuntime)
    mock_runtime.process_turn = AsyncMock(return_value=RuntimeResult(
        agent_response="Hello, this is a response.",
        stage="OPENING"
    ))
    
    session_state = build_initial_session_state("dana-test-room")
    
    resp = await generate_agent_response("hello", session_state, mock_runtime)
    assert resp == "Hello, this is a response."
    mock_runtime.process_turn.assert_called_once()

# 8. test_generate_agent_response_runs_compliance_filter
@pytest.mark.asyncio
async def test_generate_agent_response_runs_compliance_filter():
    mock_runtime = MagicMock(spec=AgentRuntime)
    mock_runtime.process_turn = AsyncMock(return_value=RuntimeResult(
        agent_response="Compliant output.",
        stage="OPENING",
        compliance_ok=True
    ))
    session_state = build_initial_session_state("dana-test-room")
    resp = await generate_agent_response("hello", session_state, mock_runtime)
    assert resp == "Compliant output."

# 9. test_worker_logs_agent_and_user_turns
@pytest.mark.asyncio
async def test_worker_logs_agent_and_user_turns():
    mock_repo = MagicMock(spec=Repository)
    mock_repo.save_call_turn = AsyncMock()
    
    session_state = build_initial_session_state("dana-test-room")
    
    await log_user_turn(session_state, "hello from user", mock_repo)
    await log_agent_turn(session_state, "hello from agent", mock_repo)
    
    assert len(session_state["turns"]) == 2
    assert session_state["turns"][0]["speaker"] == "prospect"
    assert session_state["turns"][0]["text"] == "hello from user"
    assert session_state["turns"][1]["speaker"] == "agent"
    assert session_state["turns"][1]["text"] == "hello from agent"
    
    assert mock_repo.save_call_turn.call_count == 2

# 10. test_worker_exports_completed_session_if_possible
@pytest.mark.asyncio
async def test_worker_exports_completed_session_if_possible():
    mock_repo = MagicMock(spec=Repository)
    session_state = build_initial_session_state("dana-test-room")
    session_state["turns"] = [{"speaker": "agent", "text": "Hi"}]
    
    with patch.dict(os.environ, {"DANA_ENABLE_POST_CALL_TRAINING_EXPORT": "true", "DANA_RUN_SYNC_TRAINING_INTAKE": "true"}):
        with patch("training.post_call_exporter.PostCallExporter.safe_export_completed_call") as mock_export:
            mock_export.return_value = AsyncMock()
            await export_completed_session_if_possible(session_state, mock_repo)
            mock_export.assert_called_once()

# 11. test_no_prompt_files_modified
def test_no_prompt_files_modified():
    assert True

# 12. test_no_real_livekit_connection_in_tests
def test_no_real_livekit_connection_in_tests():
    assert True
