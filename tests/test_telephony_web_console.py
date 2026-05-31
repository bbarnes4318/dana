import re
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ops.web_console import TrainingWebConsoleServer, TrainingWebConsoleConfig
from ops.training_console import ConsoleActionResult


# 1. test_telephony_tab_exists
def test_telephony_tab_exists():
    """Verify that the telephony-tab exists in static index.html."""
    html_content = Path("static/training_console/index.html").read_text(encoding="utf-8")
    assert "telephony-tab" in html_content
    assert "provider-config-form" in html_content
    assert "campaign-create-form-telephony" in html_content


# 2. test_create_provider_endpoint_calls_console
@pytest.mark.asyncio
async def test_create_provider_endpoint_calls_console():
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    mock_result = ConsoleActionResult(
        action="create_telephony_provider_config",
        success=True,
        message="Created",
        data={"provider_config_id": "prov_1"}
    )
    
    with patch.object(server.console, "create_telephony_provider_config", return_value=mock_result) as mock_create:
        body = {"name": "Test Config", "livekit_url": "wss://test.lk"}
        status, data = await server.handle_api("POST", "/api/telephony/providers", body)
        assert status == 200
        assert data["success"] is True
        mock_create.assert_called_once_with(name="Test Config", livekit_url="wss://test.lk")


# 3. test_create_campaign_endpoint_calls_console
@pytest.mark.asyncio
async def test_create_campaign_endpoint_calls_console():
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    mock_result = ConsoleActionResult(
        action="create_telephony_campaign",
        success=True,
        message="Created",
        data={"campaign_id": "camp_1"}
    )
    
    with patch.object(server.console, "create_telephony_campaign", return_value=mock_result) as mock_create:
        body = {"name": "FE Test Campaign", "operator": "Jimmy"}
        status, data = await server.handle_api("POST", "/api/telephony/campaigns", body)
        assert status == 200
        assert data["success"] is True
        mock_create.assert_called_once_with(name="FE Test Campaign", operator="Jimmy")


# 4. test_campaign_start_requires_operator
@pytest.mark.asyncio
async def test_campaign_start_requires_operator():
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    body = {"operator": "", "reason": "test"}
    status, data = await server.handle_api("POST", "/api/telephony/campaigns/camp_1/start", body)
    assert status == 400
    assert "operator parameter is required" in data["error"]


# 5. test_campaign_pause_requires_operator
@pytest.mark.asyncio
async def test_campaign_pause_requires_operator():
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    body = {"operator": "", "reason": "test"}
    status, data = await server.handle_api("POST", "/api/telephony/campaigns/camp_1/pause", body)
    assert status == 400
    assert "operator parameter is required" in data["error"]


# 6. test_campaign_stop_requires_operator
@pytest.mark.asyncio
async def test_campaign_stop_requires_operator():
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    body = {"operator": "", "reason": "test"}
    status, data = await server.handle_api("POST", "/api/telephony/campaigns/camp_1/stop", body)
    assert status == 400
    assert "operator parameter is required" in data["error"]


# 7. test_import_leads_endpoint_calls_console
@pytest.mark.asyncio
async def test_import_leads_endpoint_calls_console():
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    mock_result = ConsoleActionResult(
        action="import_campaign_leads",
        success=True,
        message="Imported",
        data={"lead_ids": ["l1"]}
    )
    
    with patch.object(server.console, "import_campaign_leads", return_value=mock_result) as mock_import:
        body = {"path": "data/leads/test.csv"}
        status, data = await server.handle_api("POST", "/api/telephony/campaigns/camp_1/leads/import", body)
        assert status == 200
        assert data["success"] is True
        mock_import.assert_called_once_with("camp_1", "data/leads/test.csv")


# 8. test_dialer_tick_defaults_dry_run
@pytest.mark.asyncio
async def test_dialer_tick_defaults_dry_run():
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    mock_result = ConsoleActionResult(
        action="run_dialer_once",
        success=True,
        message="Ticked",
        data={}
    )
    
    with patch.object(server.console, "run_dialer_once", return_value=mock_result) as mock_tick:
        body = {"operator": "Jimmy"}
        status, data = await server.handle_api("POST", "/api/telephony/campaigns/camp_1/dialer/tick", body)
        assert status == 200
        # By default dry_run is True
        mock_tick.assert_called_once_with(
            "camp_1", live_mode=False, dry_run=True, max_calls=None, operator="Jimmy", force=False
        )


# 9. test_dialer_tick_live_mode_requires_flags
@pytest.mark.asyncio
async def test_dialer_tick_live_mode_requires_flags():
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    # Live mode is disabled by default
    os.environ["TELEPHONY_LIVE_MODE"] = "false"
    os.environ["DANA_ENABLE_OUTBOUND_DIALER"] = "false"
    
    body = {"operator": "Jimmy", "live_mode": True, "dry_run": False}
    status, data = await server.handle_api("POST", "/api/telephony/campaigns/camp_1/dialer/tick", body)
    # The console layer handles checking environment, it should fail
    assert status == 400
    assert "Live mode is not enabled" in data["error"] or data["success"] is False


# 10. test_list_live_calls_endpoint
@pytest.mark.asyncio
async def test_list_live_calls_endpoint():
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    mock_result = ConsoleActionResult(
        action="list_live_telephony_calls",
        success=True,
        message="Live calls listed",
        data={"calls": []}
    )
    
    with patch.object(server.console, "list_live_telephony_calls", return_value=mock_result) as mock_list:
        status, data = await server.handle_api("GET", "/api/telephony/calls/live?campaign_id=camp_1&limit=5", None)
        assert status == 200
        mock_list.assert_called_once_with(campaign_id="camp_1", limit=5)


# 11. test_mark_outcome_endpoint
@pytest.mark.asyncio
async def test_mark_outcome_endpoint():
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    mock_result = ConsoleActionResult(
        action="mark_call_outcome",
        success=True,
        message="Marked",
        data={}
    )
    
    with patch.object(server.console, "mark_call_outcome", return_value=mock_result) as mock_mark:
        body = {"operator": "Jimmy", "outcome": "answered", "metadata": {"test": "val"}}
        status, data = await server.handle_api("POST", "/api/telephony/calls/att_1/outcome", body)
        assert status == 200
        mock_mark.assert_called_once_with("att_1", "answered", "Jimmy", metadata={"test": "val"})


# 12. test_export_attempt_to_training_endpoint
@pytest.mark.asyncio
async def test_export_attempt_to_training_endpoint():
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    mock_result = ConsoleActionResult(
        action="export_call_attempt_to_training",
        success=True,
        message="Exported",
        data={}
    )
    
    with patch.object(server.console, "export_call_attempt_to_training", return_value=mock_result) as mock_export:
        body = {"operator": "Jimmy"}
        status, data = await server.handle_api("POST", "/api/telephony/calls/att_1/export-training", body)
        assert status == 200
        mock_export.assert_called_once_with("att_1", "Jimmy")


# 13. test_no_live_prompt_file_modified
@pytest.mark.asyncio
async def test_no_live_prompt_file_modified():
    """Verify that execution of API calls does not alter the master prompt prompts/final_expense_alex.md."""
    prompt_file = Path("prompts/final_expense_alex.md")
    content_before = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else "dummy"
    
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    # Run safety checks
    await server.handle_api("GET", "/api/telephony/campaigns", None)
    
    content_after = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else "dummy"
    assert content_before == content_after


# 14. test_no_provider_calls_without_live_env
@pytest.mark.asyncio
async def test_no_provider_calls_without_live_env():
    """Verify that dialer ticks do not trigger live calls if env is disabled."""
    # Reset env keys
    os.environ["TELEPHONY_LIVE_MODE"] = "false"
    os.environ["DANA_ENABLE_OUTBOUND_DIALER"] = "false"
    
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    body = {"operator": "Jimmy", "live_mode": True, "dry_run": False}
    status, data = await server.handle_api("POST", "/api/telephony/campaigns/camp_1/dialer/tick", body)
    assert data["success"] is False


# 15. test_static_js_has_telephony_functions
def test_static_js_has_telephony_functions():
    """Verify that app.js contains functions for campaign actions."""
    js_content = Path("static/training_console/app.js").read_text(encoding="utf-8")
    assert "triggerCampaignLifecycleAction" in js_content
    assert "endLiveCallSession" in js_content
    assert "exportAttemptToTraining" in js_content


# 16. test_docs_exist
def test_docs_exist():
    """Verify that required documentation files exist."""
    assert Path("docs/telephony_campaign_operations.md").exists()
    assert Path("docs/telnyx_livekit_setup.md").exists()
    assert Path("docs/outbound_dialer_safety_controls.md").exists()
