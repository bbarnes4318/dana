import os
import sys
import tempfile
import shutil
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure parent directory is in sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.env_loader import load_environment
from config.runtime_env import get_runtime_env
from telephony.livekit_agent_worker import audit_worker_status, LiveKitAgentWorkerConfig
from telephony.live_telephony_readiness import LiveTelephonyReadinessChecker
from telephony.live_smoke_test import LiveTelephonySmokeTester, LiveSmokeTestConfig
from storage.repository import Repository
from telephony.livekit_adapter import LiveKitOutboundAdapter, LiveKitDialConfig

@pytest.fixture
def clean_env():
    """Backup, clear, and restore os.environ after each test."""
    old_env = os.environ.copy()
    # Keep only system variables that python/pytest needs, but clear app specific ones
    sys_keys = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "SYSTEMDRIVE", "USERNAME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "COMMONPROGRAMFILES", "PROGRAMFILES", "PROGRAMFILES(X86)", "COMPUTERNAME", "LOGONSERVER", "USERDOMAIN"}
    for k in list(os.environ.keys()):
        if k not in sys_keys:
            del os.environ[k]
    yield
    os.environ.clear()
    os.environ.update(old_env)

@pytest.fixture
def temp_repo_dir():
    """Create a temporary repository directory structure."""
    temp_dir = tempfile.mkdtemp()
    requirements_path = Path(temp_dir) / "requirements.txt"
    requirements_path.touch()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)


# 1. test_loads_existing_repo_env_contract
def test_loads_existing_repo_env_contract(clean_env, temp_repo_dir):
    env_file = temp_repo_dir / ".env"
    env_file.write_text(
        "LIVEKIT_URL=wss://livekit.test\n"
        "LIVEKIT_API_KEY=lk_key\n"
        "LIVEKIT_API_SECRET=lk_secret\n"
        "TELNYX_API_KEY=telnyx_key\n"
        "DEEPGRAM_API_KEY=deepgram_key\n"
        "VLLM_BASE_URL=http://localhost:8000/v1\n"
        "SIGNALWIRE_DIDS=+1234567890\n"
        "DANA_CONFIRM_PLACE_CALL=no\n"
        "DANA_CONFIRM_TRANSFER_CALL=no\n"
        "DANA_MODEL_ROUTING_MODE=local\n"
        "DANA_LLM_ROUTING_MODE=local\n"
        "DANA_TTS_ROUTING_MODE=local\n"
        "DANA_ALLOW_CLOUD_LLM_FALLBACK=false\n"
        "DANA_ALLOW_CLOUD_TTS_FALLBACK=false\n"
        "KOKORO_MODEL_PATH=path/to/kokoro\n"
        "KOKORO_VOICES_PATH=path/to/voices\n"
        "DATABASE_URL=sqlite://\n"
        "REDIS_URL=redis://localhost\n",
        encoding="utf-8"
    )
    
    with patch("config.env_loader.find_repo_root", return_value=temp_repo_dir):
        env = get_runtime_env()
        
    assert env["livekit_url"] == "wss://livekit.test"
    assert env["livekit_api_key"] == "lk_key"
    assert env["livekit_api_secret"] == "lk_secret"
    assert env["telnyx_api_key"] == "telnyx_key"
    assert env["vllm_base_url"] == "http://localhost:8000/v1"
    assert env["outbound_caller_id"] == "+1234567890"
    assert env["llm_routing_mode"] == "local"
    assert env["tts_routing_mode"] == "local"
    assert env["allow_cloud_llm_fallback"] is False
    assert env["allow_cloud_tts_fallback"] is False


# 2. test_live_call_enabled_from_dana_confirm_place_call_yes
def test_live_call_enabled_from_dana_confirm_place_call_yes(clean_env):
    with patch("config.runtime_env.load_environment"):
        os.environ["DANA_CONFIRM_PLACE_CALL"] = "yes"
        env = get_runtime_env()
        assert env["live_call_enabled"] is True

        os.environ["DANA_CONFIRM_PLACE_CALL"] = "true"
        env = get_runtime_env()
        assert env["live_call_enabled"] is True

        os.environ["DANA_CONFIRM_PLACE_CALL"] = "no"
        os.environ["TELEPHONY_LIVE_MODE"] = "true"
        env = get_runtime_env()
        assert env["live_call_enabled"] is True


# 3. test_outbound_caller_id_from_dana_outbound_caller_id
def test_outbound_caller_id_from_dana_outbound_caller_id(clean_env):
    with patch("config.runtime_env.load_environment"):
        os.environ["DANA_OUTBOUND_CALLER_ID"] = "+19999999999"
        env = get_runtime_env()
        assert env["outbound_caller_id"] == "+19999999999"


# 4. test_outbound_caller_id_falls_back_to_first_signalwire_did
def test_outbound_caller_id_falls_back_to_first_signalwire_did(clean_env):
    with patch("config.runtime_env.load_environment"):
        if "DANA_OUTBOUND_CALLER_ID" in os.environ:
            del os.environ["DANA_OUTBOUND_CALLER_ID"]
        if "DANA_PRIMARY_DID" in os.environ:
            del os.environ["DANA_PRIMARY_DID"]
        
        os.environ["SIGNALWIRE_DIDS"] = " +18888888888 , +17777777777 "
        env = get_runtime_env()
        assert env["outbound_caller_id"] == "+18888888888"


# 5. test_telnyx_api_key_not_treated_as_trunk_id
def test_telnyx_api_key_not_treated_as_trunk_id(clean_env):
    with patch("config.runtime_env.load_environment"):
        os.environ["TELNYX_API_KEY"] = "some-telnyx-key"
        if "LIVEKIT_SIP_OUTBOUND_TRUNK_ID" in os.environ:
            del os.environ["LIVEKIT_SIP_OUTBOUND_TRUNK_ID"]
        if "DANA_LIVEKIT_SIP_OUTBOUND_TRUNK_ID" in os.environ:
            del os.environ["DANA_LIVEKIT_SIP_OUTBOUND_TRUNK_ID"]
        if "TELNYX_LIVEKIT_OUTBOUND_TRUNK_ID" in os.environ:
            del os.environ["TELNYX_LIVEKIT_OUTBOUND_TRUNK_ID"]
            
        env = get_runtime_env()
        assert env["livekit_sip_outbound_trunk_id"] is None
        assert env["telnyx_api_key"] == "some-telnyx-key"


# 6. test_missing_livekit_trunk_reports_clear_message
@pytest.mark.asyncio
async def test_missing_livekit_trunk_reports_clear_message(clean_env, tmp_path):
    with patch("config.runtime_env.load_environment"):
        os.environ["TELNYX_API_KEY"] = "some-telnyx-key"
        if "LIVEKIT_SIP_OUTBOUND_TRUNK_ID" in os.environ:
            del os.environ["LIVEKIT_SIP_OUTBOUND_TRUNK_ID"]
        if "DANA_LIVEKIT_SIP_OUTBOUND_TRUNK_ID" in os.environ:
            del os.environ["DANA_LIVEKIT_SIP_OUTBOUND_TRUNK_ID"]
        if "TELNYX_LIVEKIT_OUTBOUND_TRUNK_ID" in os.environ:
            del os.environ["TELNYX_LIVEKIT_OUTBOUND_TRUNK_ID"]
            
        checker = LiveTelephonyReadinessChecker(repository=Repository(data_dir=tmp_path))
        res = await checker.check_provider_config()
        
        assert res["ok"] is False
        expected_msg = (
            "Missing LiveKit outbound SIP trunk ID. TELNYX_API_KEY is not the same thing. "
            "Create/locate the LiveKit outbound trunk and set LIVEKIT_SIP_OUTBOUND_TRUNK_ID."
        )
        assert expected_msg in res["failures"]


# 7. test_local_first_worker_does_not_require_openai_api_key
def test_local_first_worker_does_not_require_openai_api_key(clean_env):
    with patch("config.runtime_env.load_environment"):
        os.environ["DANA_LLM_ROUTING_MODE"] = "local"
        os.environ["DANA_TTS_ROUTING_MODE"] = "local"
        os.environ["DANA_STT_ROUTING_MODE"] = "local"
        os.environ["DANA_ALLOW_CLOUD_LLM_FALLBACK"] = "false"
        os.environ["DANA_ALLOW_CLOUD_TTS_FALLBACK"] = "false"
        os.environ["VLLM_BASE_URL"] = "http://localhost:8000/v1"
        os.environ["KOKORO_MODEL_PATH"] = "path/to/model"
        os.environ["KOKORO_VOICES_PATH"] = "path/to/voices"
        os.environ["LIVEKIT_URL"] = "wss://livekit"
        os.environ["LIVEKIT_API_KEY"] = "key"
        os.environ["LIVEKIT_API_SECRET"] = "secret"
        
        if "OPENAI_API_KEY" in os.environ:
            del os.environ["OPENAI_API_KEY"]
            
        status = audit_worker_status()
        assert "OPENAI_API_KEY" not in status.missing_provider_config


# 8. test_cloud_llm_mode_requires_openai_api_key
def test_cloud_llm_mode_requires_openai_api_key(clean_env):
    with patch("config.runtime_env.load_environment"):
        os.environ["DANA_LLM_ROUTING_MODE"] = "cloud"
        os.environ["LIVEKIT_URL"] = "wss://livekit"
        os.environ["LIVEKIT_API_KEY"] = "key"
        os.environ["LIVEKIT_API_SECRET"] = "secret"
        
        if "OPENAI_API_KEY" in os.environ:
            del os.environ["OPENAI_API_KEY"]
            
        status = audit_worker_status()
        assert "OPENAI_API_KEY" in status.missing_provider_config


# 9. test_cloud_tts_mode_requires_openai_or_provider_key
def test_cloud_tts_mode_requires_openai_or_provider_key(clean_env):
    with patch("config.runtime_env.load_environment"):
        os.environ["DANA_TTS_ROUTING_MODE"] = "cloud"
        os.environ["LIVEKIT_URL"] = "wss://livekit"
        os.environ["LIVEKIT_API_KEY"] = "key"
        os.environ["LIVEKIT_API_SECRET"] = "secret"
        
        if "OPENAI_API_KEY" in os.environ:
            del os.environ["OPENAI_API_KEY"]
            
        status = audit_worker_status()
        assert "OPENAI_API_KEY" in status.missing_provider_config


# 10. test_deepgram_only_required_for_cloud_stt
def test_deepgram_only_required_for_cloud_stt(clean_env):
    with patch("config.runtime_env.load_environment"):
        os.environ["LIVEKIT_URL"] = "wss://livekit"
        os.environ["LIVEKIT_API_KEY"] = "key"
        os.environ["LIVEKIT_API_SECRET"] = "secret"
        
        if "DEEPGRAM_API_KEY" in os.environ:
            del os.environ["DEEPGRAM_API_KEY"]
            
        # Local mode (default): Deepgram not required
        os.environ["DANA_STT_ROUTING_MODE"] = "local"
        os.environ["DANA_CLOUD_STT_ON_FAILURE"] = "false"
        status = audit_worker_status()
        assert "DEEPGRAM_API_KEY" not in status.missing_provider_config

        # Cloud mode: Deepgram is required
        os.environ["DANA_STT_ROUTING_MODE"] = "cloud"
        status = audit_worker_status()
        assert "DEEPGRAM_API_KEY" in status.missing_provider_config


# 11. test_vllm_base_url_required_for_local_llm
def test_vllm_base_url_required_for_local_llm(clean_env):
    with patch("config.runtime_env.load_environment"):
        os.environ["DANA_LLM_ROUTING_MODE"] = "local"
        if "VLLM_BASE_URL" in os.environ:
            del os.environ["VLLM_BASE_URL"]
            
        status = audit_worker_status()
        assert "VLLM_BASE_URL" in status.missing_provider_config


# 12. test_kokoro_paths_reported_for_local_tts
def test_kokoro_paths_reported_for_local_tts(clean_env):
    with patch("config.runtime_env.load_environment"):
        os.environ["DANA_TTS_ROUTING_MODE"] = "local"
        if "KOKORO_MODEL_PATH" in os.environ:
            del os.environ["KOKORO_MODEL_PATH"]
        if "KOKORO_VOICES_PATH" in os.environ:
            del os.environ["KOKORO_VOICES_PATH"]
            
        status = audit_worker_status()
        assert "KOKORO_MODEL_PATH" in status.missing_provider_config
        assert "KOKORO_VOICES_PATH" in status.missing_provider_config


# 13. test_readiness_reports_env_loaded
@pytest.mark.asyncio
async def test_readiness_reports_env_loaded(clean_env, temp_repo_dir, tmp_path):
    env_file = temp_repo_dir / ".env"
    env_file.write_text("LIVEKIT_URL=wss://livekit.test\n", encoding="utf-8")
    
    with patch("config.env_loader.find_repo_root", return_value=temp_repo_dir):
        checker = LiveTelephonyReadinessChecker(repository=Repository(data_dir=tmp_path))
        res = await checker.run()
        assert res.env_loaded is True


# 14. test_scripts_load_dotenv_before_checks
def test_scripts_load_dotenv_before_checks(clean_env):
    with patch("config.env_loader.load_environment") as mock_load, \
         patch("scripts.check_live_telephony_readiness.LiveTelephonyReadinessChecker.run") as mock_run, \
         patch("sys.exit") as mock_exit:
         
        mock_run.return_value = MagicMock()
        mock_run.return_value.ready = True
        mock_run.return_value.model_dump.return_value = {}
        
        import importlib
        import scripts.check_live_telephony_readiness
        importlib.reload(scripts.check_live_telephony_readiness)
        
        with patch("sys.argv", ["check_live_telephony_readiness.py"]):
            import asyncio
            asyncio.run(scripts.check_live_telephony_readiness.main())
            
        assert mock_load.call_count >= 1


# 15. test_no_secret_values_in_readiness_output
@pytest.mark.asyncio
async def test_no_secret_values_in_readiness_output(clean_env, tmp_path):
    with patch("config.runtime_env.load_environment"):
        os.environ["LIVEKIT_API_SECRET"] = "my-secret-key-12345"
        os.environ["LIVEKIT_API_KEY"] = "my-public-key-6789"
        os.environ["TELNYX_API_KEY"] = "my-telnyx-api-key-999"
        
        tester = LiveTelephonySmokeTester(repository=Repository(data_dir=tmp_path))
        env_status = {
            "LIVEKIT_API_SECRET": "my-secret-key-12345",
            "LIVEKIT_API_KEY": "my-public-key-6789",
            "TELNYX_API_KEY": "my-telnyx-api-key-999"
        }
        
        masked = tester.mask_sensitive_env(env_status)
        for k, v in masked.items():
            assert "my-secret-key-12345" not in str(v)
            assert "my-telnyx-api-key-999" not in str(v)


# 16. test_smoke_test_uses_resolved_env_aliases
@pytest.mark.asyncio
async def test_smoke_test_uses_resolved_env_aliases(clean_env, tmp_path):
    with patch("config.runtime_env.load_environment"):
        os.environ["DANA_OUTBOUND_CALLER_ID"] = "+19999999999"
        os.environ["DANA_LIVEKIT_SIP_OUTBOUND_TRUNK_ID"] = "trunk-abc"
        os.environ["LIVEKIT_URL"] = "wss://livekit"
        os.environ["LIVEKIT_API_KEY"] = "key"
        os.environ["LIVEKIT_API_SECRET"] = "secret"
        os.environ["TELEPHONY_LIVE_MODE"] = "true"
        os.environ["DANA_AGENT_WORKER_ENABLED"] = "true"

        tester = LiveTelephonySmokeTester(repository=Repository(data_dir=tmp_path))
        config = LiveSmokeTestConfig(
            phone_number="+15550000",
            operator="Jimmy",
            confirm="LIVE CALL",
            place_call=False  # Only runs readiness
        )
        
        with patch("telephony.live_telephony_readiness.LiveTelephonyReadinessChecker.check_livekit_sdk", return_value=(True, None)):
            res = await tester.run(config)
            assert res.readiness_ready is True
            assert res.readiness.get("outbound_trunk_id_present") is True
            assert res.readiness.get("caller_id_present") is True


# 17. test_no_real_livekit_call_in_tests
@pytest.mark.asyncio
async def test_no_real_livekit_call_in_tests(clean_env):
    with patch("config.runtime_env.load_environment"):
        # Ensure that LiveKitOutboundAdapter.dial does NOT make a real call unless live mode is explicitly enabled
        # and all parameters are passed. Even then, test assertions must not hit the real LiveKit API.
        adapter = LiveKitOutboundAdapter()
        
        # Assert default state has live_mode_enabled returning False
        assert adapter.live_mode_enabled() is False
        
        config = LiveKitDialConfig(
            live_mode=False,
            room_name="test-room",
            phone_number="+15551111",
            participant_identity="test-identity"
        )
        
        res = await adapter.dial(config)
        assert res.dry_run is True
        assert res.success is True
