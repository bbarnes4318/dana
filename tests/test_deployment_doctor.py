"""Unit tests for the local Deployment Doctor audit script."""

from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from ops.deployment_doctor import DeploymentDoctor, run_command_safe

@pytest.fixture
def mock_system_helpers():
    """Mock platform system checks to simulate a clean production host environment."""
    with patch("shutil.which") as mock_which, patch("ops.deployment_doctor.run_command_safe") as mock_run:
        # Mock binaries being present
        def side_effect_which(cmd):
            if cmd in ("nvidia-smi", "docker", "docker-compose"):
                return f"/usr/bin/{cmd}"
            return None
        mock_which.side_effect = side_effect_which
        
        # Mock command outputs
        def side_effect_run(cmd):
            if "nvidia-smi" in cmd:
                return True, "GPU 0: NVIDIA L40 (UUID: GPU-12345)\nGPU 1: NVIDIA L40 (UUID: GPU-67890)"
            elif "docker" in cmd and "compose" in cmd:
                return True, "Docker Compose version v2.20.0"
            elif "docker" in cmd and "--version" in cmd:
                return True, "Docker version 24.0.5, build 24.0.5-0ubuntu1~22.04.1"
            return True, "Mock output"
        mock_run.side_effect = side_effect_run
        
        yield mock_which, mock_run


@pytest.fixture
def valid_env_content() -> str:
    """Return env file content corresponding to a valid configuration."""
    return """
HF_TOKEN=hf_token_abc123
LIVEKIT_URL=wss://myproject.livekit.cloud
LIVEKIT_API_KEY=lk_api_key_abc
LIVEKIT_API_SECRET=lk_api_secret_xyz
LIVEKIT_SIP_OUTBOUND_TRUNK_ID=sip_trunk_id_123
TELNYX_API_KEY=telnyx_key_999
TELNYX_CONNECTION_ID=conn_id_xyz
TELNYX_OUTBOUND_NUMBER=+1234567890
POSTGRES_USER=dana_user
POSTGRES_PASSWORD=my_strong_password
POSTGRES_DB=dana
DATABASE_URL=postgresql://dana_user:my_strong_password@pgbouncer:6432/dana
DATABASE_ADMIN_URL=postgresql://dana_user:my_strong_password@postgres:5432/dana
REDIS_URL=redis://redis:6379/0
DANA_USE_REDIS_HOT_STATE=true
VLLM_BASE_URL=http://vllm-server:8000/v1
VLLM_MODEL=meta-llama/Llama-3.1-8B-Instruct
VLLM_GPU_MEMORY_UTILIZATION=0.70
VLLM_QUANTIZATION=fp8
VLLM_MAX_MODEL_LEN=4096
DANA_RUNTIME_ENV=production
DANA_ALLOW_MOCK_TTS=false
DANA_CONTROLLED_LIVE_TEST=false
"""


@pytest.mark.asyncio
async def test_deployment_doctor_passes_when_everything_valid(tmp_path, mock_system_helpers, valid_env_content):
    env_file = tmp_path / ".env"
    env_file.write_text(valid_env_content)

    # Setup repo structure mocks
    with patch("os.makedirs"), patch("pathlib.Path.exists", return_value=True), patch("builtins.open", patch.object(Path, "open", return_value=MagicMock())):
        # To bypass open() on the real system files but read env_file correctly
        original_open = open
        def mock_open_file(file, *args, **kwargs):
            if str(file) == str(env_file):
                return original_open(file, *args, **kwargs)
            return MagicMock()
        
        with patch("builtins.open", side_effect=mock_open_file):
            doctor = DeploymentDoctor(env_file)
            report = doctor.audit()
            
            # Verify status
            assert report["PRODUCTION_ENV_VALID"] is True
            assert report["READY_TO_START_SERVICES"] is True
            assert report["READY_FOR_LIVE_CALL_TEST"] is True
            assert report["failures"] == 0


@pytest.mark.asyncio
async def test_deployment_doctor_fails_when_env_file_missing(tmp_path, mock_system_helpers):
    env_file = tmp_path / ".env.nonexistent"
    
    # Assert missing env file fails
    doctor = DeploymentDoctor(env_file)
    report = doctor.audit()
    
    assert report["PRODUCTION_ENV_VALID"] is False
    assert report["READY_TO_START_SERVICES"] is False
    assert report["READY_FOR_LIVE_CALL_TEST"] is False
    assert report["failures"] > 0
    assert any("Active environment file not found" in c["message"] for c in report["checks"])


@pytest.mark.asyncio
async def test_deployment_doctor_fails_on_placeholder_env(tmp_path, mock_system_helpers, valid_env_content):
    # Invalidate env by setting a placeholder value
    invalid_content = valid_env_content.replace("hf_token_abc123", "replace_me")
    env_file = tmp_path / ".env"
    env_file.write_text(invalid_content)

    with patch("os.makedirs"), patch("pathlib.Path.exists", return_value=True):
        original_open = open
        def mock_open_file(file, *args, **kwargs):
            if str(file) == str(env_file):
                return original_open(file, *args, **kwargs)
            return MagicMock()
        
        with patch("builtins.open", side_effect=mock_open_file):
            doctor = DeploymentDoctor(env_file)
            report = doctor.audit()
            
            # Placeholder values should trigger validation failures
            assert report["PRODUCTION_ENV_VALID"] is False
            assert report["READY_TO_START_SERVICES"] is False
            assert report["READY_FOR_LIVE_CALL_TEST"] is False
            assert report["failures"] > 0
            assert any("placeholder" in c["message"].lower() for c in report["checks"])


@pytest.mark.asyncio
async def test_deployment_doctor_secrets_masking(tmp_path, mock_system_helpers, valid_env_content):
    env_file = tmp_path / ".env"
    env_file.write_text(valid_env_content)

    with patch("os.makedirs"), patch("pathlib.Path.exists", return_value=True):
        original_open = open
        def mock_open_file(file, *args, **kwargs):
            if str(file) == str(env_file):
                return original_open(file, *args, **kwargs)
            return MagicMock()
        
        with patch("builtins.open", side_effect=mock_open_file):
            doctor = DeploymentDoctor(env_file)
            report = doctor.audit()
            
            # Check secret masking is enforced
            masked_env = report["masked_environment"]
            
            # Secret keys must show "present", NOT the raw secret
            assert masked_env["HF_TOKEN"] == "present"
            assert masked_env["LIVEKIT_API_SECRET"] == "present"
            assert masked_env["TELNYX_API_KEY"] == "present"
            assert masked_env["POSTGRES_PASSWORD"] == "present"
            
            # Check masked DB URL (does not leak the password)
            assert "my_strong_password" not in masked_env["DATABASE_URL"]
            assert "postgresql://dana_user:******@pgbouncer:6432/dana" in masked_env["DATABASE_URL"]
            
            # Verify json output serialization works without error
            json_report = json.dumps(report)
            assert "my_strong_password" not in json_report
            assert "hf_token_abc123" not in json_report
            assert "lk_api_secret_xyz" not in json_report


@pytest.mark.asyncio
async def test_deployment_doctor_fail_closed_controlled_test(tmp_path, mock_system_helpers, valid_env_content):
    # Set controlled test to true
    test_env_content = valid_env_content.replace("DANA_CONTROLLED_LIVE_TEST=false", "DANA_CONTROLLED_LIVE_TEST=true")
    env_file = tmp_path / ".env"
    env_file.write_text(test_env_content)

    with patch("os.makedirs"), patch("pathlib.Path.exists", return_value=True):
        original_open = open
        def mock_open_file(file, *args, **kwargs):
            if str(file) == str(env_file):
                return original_open(file, *args, **kwargs)
            return MagicMock()
        
        with patch("builtins.open", side_effect=mock_open_file):
            doctor = DeploymentDoctor(env_file)
            report = doctor.audit()
            
            # Valid environment for testing, but issue warning check
            assert report["PRODUCTION_ENV_VALID"] is True
            assert report["READY_TO_START_SERVICES"] is True
            assert report["READY_FOR_LIVE_CALL_TEST"] is True
            assert report["warnings"] > 0
            assert any("DANA_CONTROLLED_LIVE_TEST is set to true" in c["message"] for c in report["checks"])
