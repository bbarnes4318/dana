import os
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

# Setup sys.path to root directory
sys.path.append(str(Path(__file__).resolve().parent.parent))

from ops.readiness import (
    check_telephony,
    check_storage,
    check_llm,
    run_readiness_checks
)
from ops.web_console import TrainingWebConsoleServer, TrainingWebConsoleConfig
from storage.repository import Repository


@pytest.mark.asyncio
async def test_readiness_fails_when_telnyx_api_key_missing():
    """Verify telephony readiness fails when TELNYX_API_KEY is missing/placeholder."""
    with patch.dict(os.environ, {"TELNYX_API_KEY": "", "TELNYX_CONNECTION_ID": "conn_123"}):
        ok, msg = await check_telephony()
        assert ok is False
        assert "TELNYX_API_KEY" in msg

    with patch.dict(os.environ, {"TELNYX_API_KEY": "replace_me", "TELNYX_CONNECTION_ID": "conn_123"}):
        ok, msg = await check_telephony()
        assert ok is False
        assert "TELNYX_API_KEY" in msg


@pytest.mark.asyncio
async def test_readiness_fails_when_telnyx_connection_id_missing():
    """Verify telephony readiness fails when TELNYX_CONNECTION_ID is missing/placeholder."""
    with patch.dict(os.environ, {"TELNYX_API_KEY": "key_123", "TELNYX_CONNECTION_ID": ""}):
        ok, msg = await check_telephony()
        assert ok is False
        assert "TELNYX_CONNECTION_ID" in msg

    with patch.dict(os.environ, {"TELNYX_API_KEY": "key_123", "TELNYX_CONNECTION_ID": "replace_me"}):
        ok, msg = await check_telephony()
        assert ok is False
        assert "TELNYX_CONNECTION_ID" in msg


@pytest.mark.asyncio
async def test_readiness_fails_in_production_when_database_url_missing():
    """Verify storage readiness fails in production when DATABASE_URL is missing/placeholder."""
    with patch.dict(os.environ, {"DANA_RUNTIME_ENV": "production", "DATABASE_URL": ""}):
        ok, msg = await check_storage()
        assert ok is False
        assert "DATABASE_URL" in msg


@pytest.mark.asyncio
async def test_readiness_fails_when_vllm_health_endpoint_is_unreachable():
    """Verify vLLM readiness fails when health endpoint is unreachable."""
    # Setting VLLM_BASE_URL to an unreachable address so the HTTP call executes and fails
    with patch.dict(os.environ, {"DANA_RUNTIME_ENV": "production", "VLLM_BASE_URL": "http://unreachable-vllm-server:8000/v1"}):
        ok, msg = await check_llm()
        assert ok is False
        assert "unreachable" in msg or "returned unhealthy" in msg


@pytest.mark.asyncio
async def test_dashboard_readiness_endpoint_includes_telephony_status(tmp_path):
    """Verify dashboard readiness API status response contains telephony status and config flags."""
    repo = MagicMock(spec=Repository)
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config, repository=repo)

    readiness_mock_results = (True, {
        "livekit": (True, "LiveKit ok"),
        "telephony": (True, "Telephony ok"),
        "stt": (True, "STT ok"),
        "llm": (True, "LLM ok"),
        "tts": (True, "TTS ok"),
        "vad": (True, "VAD ok"),
        "storage": (True, "Storage ok")
    })

    with patch("ops.healthcheck.run_healthcheck", AsyncMock(return_value=(True, "Healthy"))), \
         patch("ops.readiness.run_readiness_checks", AsyncMock(return_value=readiness_mock_results)), \
         patch("ops.web_console.os.path.exists", return_value=True), \
         patch("builtins.open", mock_open_scorecard()):
        
        status, data = await server.handle_api("GET", "/api/readiness/status", None)
        
        assert status == 200
        assert data["success"] is True
        assert "missing_livekit_config" in data
        assert "missing_telnyx_config" in data
        assert "missing_database_config" in data
        assert "missing_vllm_config" in data
        assert "production_mock_tts_risk" in data
        assert "remediation_text" in data
        assert data["ops_readiness"]["results"]["telephony"]["ok"] is True


@pytest.mark.asyncio
async def test_smoke_test_refuses_live_mode_without_controlled_test_env():
    """Verify live_call_smoke_test refuses live mode when DANA_CONTROLLED_LIVE_TEST is false."""
    from ops.live_call_smoke_test import main_async
    
    test_args = ["--to", "+15551234567", "--from", "+15557654321"]
    with patch("sys.argv", ["prog"] + test_args), \
         patch.dict(os.environ, {"DANA_CONTROLLED_LIVE_TEST": "false", "DANA_RUNTIME_ENV": "production"}):
        exit_code = await main_async()
        assert exit_code == 1


@pytest.mark.asyncio
async def test_smoke_test_refuses_when_readiness_fails():
    """Verify live_call_smoke_test refuses to run when readiness checks fail."""
    from ops.live_call_smoke_test import main_async
    
    test_args = ["--to", "+15551234567", "--from", "+15557654321"]
    
    # Mocking readiness check to fail
    readiness_mock_results = (False, {"telephony": (False, "Telnyx is not configured")})
    
    with patch("sys.argv", ["prog"] + test_args), \
         patch("ops.live_call_smoke_test.run_readiness_checks", AsyncMock(return_value=readiness_mock_results)), \
         patch.dict(os.environ, {"DANA_CONTROLLED_LIVE_TEST": "true"}):
        exit_code = await main_async()
        assert exit_code == 1


@pytest.mark.asyncio
async def test_smoke_test_dry_run_never_places_call():
    """Verify that dry-run mode validates configuration but never dials a real call."""
    from ops.live_call_smoke_test import main_async
    
    test_args = ["--to", "+15551234567", "--from", "+15557654321", "--dry-run"]
    
    readiness_mock_results = (True, {
        "livekit": (True, "LiveKit ok"),
        "telephony": (True, "Telephony ok"),
        "stt": (True, "STT ok"),
        "llm": (True, "LLM ok"),
        "tts": (True, "TTS ok"),
        "vad": (True, "VAD ok"),
        "storage": (True, "Storage ok")
    })
    
    with patch("sys.argv", ["prog"] + test_args), \
         patch("ops.live_call_smoke_test.run_readiness_checks", AsyncMock(return_value=readiness_mock_results)), \
         patch("telephony.lead_importer.CampaignLeadImporter.is_suppressed", AsyncMock(return_value=(False, None))), \
         patch("compliance.dnc_registry.DatabaseDNCRegistry.contains", AsyncMock(return_value=False)), \
         patch("telephony.livekit_adapter.LiveKitOutboundAdapter.dial") as mock_dial, \
         patch.dict(os.environ, {"DANA_CONTROLLED_LIVE_TEST": "true"}):
        
        exit_code = await main_async()
        
        assert exit_code == 0
        # Assert that dial was never called
        mock_dial.assert_not_called()


def mock_open_scorecard():
    import json
    scorecard_json = json.dumps({"passed": True})
    return patch("builtins.open", patch("builtins.open", MagicMock(return_value=MagicMock(__enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=scorecard_json)))))))
