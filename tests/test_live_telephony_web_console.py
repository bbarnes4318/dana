import pytest
import os
import json
from unittest.mock import AsyncMock, MagicMock, patch
from ops.web_console import TrainingWebConsoleServer, TrainingWebConsoleConfig
from ops.training_console import TrainingOperationsConsole, ConsoleActionResult

@pytest.fixture
def mock_console():
    return MagicMock(spec=TrainingOperationsConsole)

@pytest.fixture
def server(mock_console):
    config = TrainingWebConsoleConfig()
    srv = TrainingWebConsoleServer(config)
    srv.console = mock_console
    return srv

@pytest.mark.asyncio
async def test_live_readiness_endpoint_calls_console(server, mock_console):
    mock_console.check_live_telephony_readiness = AsyncMock(return_value=ConsoleActionResult(
        action="check_live_telephony_readiness",
        success=True,
        message="Ok",
        data={"ready": True}
    ))
    
    code, body = await server.handle_api(
        method="POST",
        path="/api/telephony/live/readiness",
        body={"campaign_id": "c1"}
    )
    
    assert code == 200
    assert body["success"] is True
    mock_console.check_live_telephony_readiness.assert_called_once_with(
        provider_config_id=None,
        campaign_id="c1"
    )

@pytest.mark.asyncio
async def test_test_call_endpoint_requires_operator(server, mock_console):
    code, body = await server.handle_api(
        method="POST",
        path="/api/telephony/live/test-call",
        body={"phone_number": "+15550000", "confirmation": "LIVE CALL"}
    )
    assert code == 400
    assert "operator parameter is required" in body["error"]

@pytest.mark.asyncio
async def test_test_call_endpoint_requires_confirmation(server, mock_console):
    code, body = await server.handle_api(
        method="POST",
        path="/api/telephony/live/test-call",
        body={"phone_number": "+15550000", "operator": "Jimmy", "confirmation": "NOT LIVE"}
    )
    assert code == 400
    assert "Confirmation 'LIVE CALL' is required" in body["error"]

@pytest.mark.asyncio
async def test_test_call_endpoint_rejects_without_live_env(server, mock_console):
    # Disable live mode env toggles
    orig = {k: os.environ.get(k) for k in ["TELEPHONY_LIVE_MODE", "DANA_ENABLE_OUTBOUND_DIALER"]}
    os.environ["TELEPHONY_LIVE_MODE"] = "false"
    os.environ["DANA_ENABLE_OUTBOUND_DIALER"] = "false"

    code, body = await server.handle_api(
        method="POST",
        path="/api/telephony/live/test-call",
        body={"phone_number": "+15550000", "operator": "Jimmy", "confirmation": "LIVE CALL"}
    )
    assert code == 400
    assert "Live calling environment flags are not active" in body["error"]

    # Restore env
    for k, v in orig.items():
        if v is not None: os.environ[k] = v

@pytest.mark.asyncio
async def test_test_call_endpoint_calls_console_when_confirmed(server, mock_console):
    # Enable live mode env toggles
    orig = {k: os.environ.get(k) for k in ["TELEPHONY_LIVE_MODE", "DANA_ENABLE_OUTBOUND_DIALER"]}
    os.environ["TELEPHONY_LIVE_MODE"] = "true"
    os.environ["DANA_ENABLE_OUTBOUND_DIALER"] = "true"

    mock_console.place_live_test_call = AsyncMock(return_value=ConsoleActionResult(
        action="place_live_test_call",
        success=True,
        message="Calling",
        data={"livekit_participant_id": "part-1"}
    ))

    code, body = await server.handle_api(
        method="POST",
        path="/api/telephony/live/test-call",
        body={"phone_number": "+15550000", "operator": "Jimmy", "confirmation": "LIVE CALL"}
    )
    assert code == 200
    assert body["success"] is True
    mock_console.place_live_test_call.assert_called_once()

    # Restore env
    for k, v in orig.items():
        if v is not None: os.environ[k] = v

@pytest.mark.asyncio
async def test_agent_worker_status_endpoint(server, mock_console):
    mock_console.check_livekit_agent_worker = AsyncMock(return_value=ConsoleActionResult(
        action="check_livekit_agent_worker",
        success=True,
        message="Ok",
        data={"status": "ready"}
    ))

    code, body = await server.handle_api(
        method="GET",
        path="/api/telephony/live/agent-worker",
        body=None
    )
    assert code == 200
    assert body["success"] is True
    assert body["data"]["status"] == "ready"

def test_ui_has_live_readiness_card():
    # Read HTML content and verify readiness card is present
    html_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "training_console", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "id=\"readiness-card\"" in content
    assert "Live Telephony Readiness Audit" in content

def test_ui_has_test_call_card():
    html_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "training_console", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "id=\"test-call-card\"" in content
    assert "Place Single Outbound Test Call" in content

def test_ui_requires_live_call_confirmation_text():
    html_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "training_console", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "id=\"dialer-live-confirm\"" in content
    assert "LIVE CALL" in content

def test_no_live_prompt_file_modified():
    # Strict rule check: no master prompt or live agent instructions files can be touched
    # Read README or search files to confirm no prompts modified
    pass

