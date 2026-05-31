"""Tests for the Training Operations Web Console server and routes."""

from __future__ import annotations

import os
import sys
import json
import re
import asyncio
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch

from storage.repository import Repository
from ops.web_console import (
    TrainingWebConsoleServer,
    TrainingWebConsoleConfig,
    MAX_UPLOAD_SIZE,
    parse_multipart,
)
from ops.readiness import ContinuousTrainingReadinessAuditor, ContinuousTrainingReadinessConfig


# 1. test_web_console_config_defaults_localhost
def test_web_console_config_defaults_localhost():
    """Verify that configuration defaults to localhost and allow_remote is False."""
    config = TrainingWebConsoleConfig()
    assert config.host == "127.0.0.1"
    assert config.port == 8787
    assert config.allow_remote is False
    assert config.debug is False


# 2. test_health_endpoint_returns_safety_status
@pytest.mark.asyncio
async def test_health_endpoint_returns_safety_status():
    """Verify health API returns active safety guidelines."""
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    status, data = await server.handle_api("GET", "/api/health", None)
    assert status == 200
    assert data["ok"] is True
    assert data["safety"]["no_auto_approval"] is True
    assert data["safety"]["no_prompt_edits"] is True
    assert data["safety"]["no_provider_uploads"] is True


# 3. test_summary_endpoint_calls_console
@pytest.mark.asyncio
async def test_summary_endpoint_calls_console():
    """Verify summary endpoint calls operations console get_summary."""
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    mock_summary = MagicMock()
    mock_summary.model_dump.return_value = {
        "pending_review_items": 3,
        "recent_training_sources": 1,
    }
    
    with patch.object(server.console, "get_summary", return_value=mock_summary) as mock_get:
        status, data = await server.handle_api("GET", "/api/summary", None)
        assert status == 200
        assert data["pending_review_items"] == 3
        mock_get.assert_called_once()


# 4. test_review_list_endpoint_calls_console
@pytest.mark.asyncio
async def test_review_list_endpoint_calls_console():
    """Verify review item list endpoint parses query params and passes to console."""
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    mock_result = MagicMock()
    mock_result.success = True
    mock_result.model_dump.return_value = {"items": []}
    
    with patch.object(server.console, "list_review_items", return_value=mock_result) as mock_list:
        status, data = await server.handle_api(
            "GET", 
            "/api/review-items?status=pending&type=training_example&limit=25", 
            None
        )
        assert status == 200
        mock_list.assert_called_once_with(status="pending", item_type="training_example", limit=25)


# 5. test_review_approve_requires_reviewer
@pytest.mark.asyncio
async def test_review_approve_requires_reviewer():
    """Verify approval endpoint returns error when reviewer name is missing."""
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    status, data = await server.handle_api(
        "POST", 
        "/api/review-items/123/approve", 
        {"reviewer": "", "notes": "notes"}
    )
    assert status == 400
    assert data["success"] is False
    assert "Reviewer is required" in data["message"]


# 6. test_review_reject_requires_notes
@pytest.mark.asyncio
async def test_review_reject_requires_notes():
    """Verify reject endpoint returns error when reject notes are missing."""
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    status, data = await server.handle_api(
        "POST", 
        "/api/review-items/123/reject", 
        {"reviewer": "Jimmy", "notes": "  "}
    )
    assert status == 400
    assert data["success"] is False
    assert "Notes are required for rejection" in data["message"]


# 7. test_intake_folder_endpoint_calls_console
@pytest.mark.asyncio
async def test_intake_folder_endpoint_calls_console():
    """Verify folder intake endpoint parses body and triggers console intake."""
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    mock_result = MagicMock()
    mock_result.success = True
    mock_result.model_dump.return_value = {"mode": "folder"}
    
    with patch.object(server.console, "run_intake", return_value=mock_result) as mock_intake:
        body = {
            "path": "data/imports/call_transcripts",
            "source_type": "call_transcript",
            "daily_qa": True,
            "dry_run": True,
            "limit": 10
        }
        status, data = await server.handle_api("POST", "/api/intake/folder", body)
        assert status == 200
        mock_intake.assert_called_once_with(
            mode="folder",
            path="data/imports/call_transcripts",
            source_type="call_transcript",
            daily_qa=True,
            dry_run=True,
            limit=10
        )


# 8. test_youtube_import_endpoint_calls_console
@pytest.mark.asyncio
async def test_youtube_import_endpoint_calls_console():
    """Verify YouTube import endpoint calls console youtube importer."""
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    mock_result = MagicMock()
    mock_result.success = True
    mock_result.model_dump.return_value = {"imported_count": 1}
    
    with patch.object(server.console, "import_youtube", return_value=mock_result) as mock_youtube:
        body = {
            "title": "FE Guide",
            "content": "guide text",
            "source_url": "https://youtube.com/123",
            "run_intake": True,
            "dry_run": False
        }
        status, data = await server.handle_api("POST", "/api/youtube/import", body)
        assert status == 200
        mock_youtube.assert_called_once_with(
            title="FE Guide",
            content="guide text",
            source_url="https://youtube.com/123",
            run_intake=True,
            dry_run=False
        )


# 9. test_scheduler_once_endpoint_calls_console
@pytest.mark.asyncio
async def test_scheduler_once_endpoint_calls_console():
    """Verify scheduler single run endpoint invokes bounded scheduler execution."""
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    mock_result = MagicMock()
    mock_result.success = True
    mock_result.model_dump.return_value = {"scheduler_run_id": "sch_123"}
    
    with patch.object(server.console, "run_scheduler_once", return_value=mock_result) as mock_sch:
        body = {"daily_qa": True, "dry_run": True, "limit": 50}
        status, data = await server.handle_api("POST", "/api/scheduler/once", body)
        assert status == 200
        mock_sch.assert_called_once_with(daily_qa=True, dry_run=True, limit=50)


# 10. test_readiness_endpoint_calls_console
@pytest.mark.asyncio
async def test_readiness_endpoint_calls_console():
    """Verify readiness audit API endpoint calls console auditor checks."""
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    mock_result = MagicMock()
    mock_result.success = True
    mock_result.model_dump.return_value = {"passed": True}
    
    with patch.object(server.console, "run_readiness", return_value=mock_result) as mock_audit:
        body = {"strict": True, "fail_on_medium": False}
        status, data = await server.handle_api("POST", "/api/readiness", body)
        assert status == 200
        mock_audit.assert_called_once_with(strict=True, fail_on_medium=False)


# 11. test_reports_endpoint_uses_safe_console_methods
@pytest.mark.asyncio
async def test_reports_endpoint_uses_safe_console_methods():
    """Verify reports queries are securely routed via operations console methods."""
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    mock_list = MagicMock()
    mock_list.success = True
    mock_list.model_dump.return_value = {"reports": []}
    
    mock_read = MagicMock()
    mock_read.success = True
    mock_read.model_dump.return_value = {"content": "report logs"}
    
    with patch.object(server.console, "list_reports", return_value=mock_list) as m_list, \
         patch.object(server.console, "read_report", return_value=mock_read) as m_read:
         
        # Test List Reports
        status_l, data_l = await server.handle_api("GET", "/api/reports?type=qa&limit=20", None)
        assert status_l == 200
        m_list.assert_called_once_with(report_type="qa", limit=20)
        
        # Test Read Report
        status_r, data_r = await server.handle_api("GET", "/api/report?path=data/reports/qa.md", None)
        assert status_r == 200
        m_read.assert_called_once_with("data/reports/qa.md")


# 12. test_upload_rejects_unsupported_extension
def test_upload_rejects_unsupported_extension():
    """Verify that non-conforming file extensions (e.g. .exe) are blocked by upload parser."""
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    with pytest.raises(ValueError, match="Invalid file extension"):
        server.safe_import_path("call_transcript", "payload.exe")


# 13. test_upload_rejects_path_traversal_filename
def test_upload_rejects_path_traversal_filename():
    """Verify upload path traversal sequences are detected and rejected."""
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    # Absolute traversal
    with pytest.raises(ValueError, match="path traversal|Invalid filename"):
        server.safe_import_path("call_transcript", "../../passwd")
        
    # Nested folder path validation
    with pytest.raises(ValueError, match="path traversal|Invalid filename"):
        server.safe_import_path("call_transcript", "sub/folder/data.json")


# 14. test_upload_routes_source_type_to_correct_folder
def test_upload_routes_source_type_to_correct_folder():
    """Verify that source types map directly to their approved imports folders."""
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    path_transcript = server.safe_import_path("call_transcript", "call1.json")
    assert "data/imports/call_transcripts/call1.json" in path_transcript.as_posix()
    
    path_youtube = server.safe_import_path("youtube", "video.txt")
    assert "data/imports/youtube_training/video.txt" in path_youtube.as_posix()


# 15. test_static_files_exist
def test_static_files_exist():
    """Verify that index.html, app.js, and styles.css exist in the repository static directory."""
    static_dir = Path("static/training_console")
    assert (static_dir / "index.html").exists()
    assert (static_dir / "app.js").exists()
    assert (static_dir / "styles.css").exists()


# 16. test_static_files_have_no_external_cdn
def test_static_files_have_no_external_cdn():
    """Verify that static HTML assets do not fetch any scripts or links from remote networks."""
    html_content = Path("static/training_console/index.html").read_text(encoding="utf-8")
    js_content = Path("static/training_console/app.js").read_text(encoding="utf-8")
    
    # Assert no script or link tags pull from http/https remote endpoints
    assert not re.search(r'src=["\']http', html_content)
    assert not re.search(r'href=["\']http', html_content)
    # Verify no fetch requests contain hardcoded remote domain references
    assert "http://localhost" not in js_content
    assert "https://" not in js_content


# 17. test_cli_script_exists_and_imports
def test_cli_script_exists_and_imports():
    """Verify launcher script scripts/run_training_web_console.py exists and can be imported."""
    script_path = Path("scripts/run_training_web_console.py")
    assert script_path.exists()
    
    # Assert Python syntax parses cleanly
    with open(script_path, "r", encoding="utf-8") as f:
        code = f.read()
    compile(code, str(script_path), "exec")


# 18. test_no_live_prompt_file_modified
@pytest.mark.asyncio
async def test_no_live_prompt_file_modified():
    """Verify that execution of API calls does not alter the master prompt prompts/final_expense_alex.md."""
    prompt_file = Path("prompts/final_expense_alex.md")
    content_before = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else "dummy"
    
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    # Run safety checks, health scans, read report endpoints
    await server.handle_api("GET", "/api/health", None)
    await server.handle_api("GET", "/api/safety", None)
    
    content_after = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else "dummy"
    assert content_before == content_after


# 19. test_no_external_api_or_provider_calls
def test_no_external_api_or_provider_calls():
    """Verify that ops/web_console.py contains no direct calls to OpenAI, Azure, requests, or httpx for provider calls."""
    web_console_code = Path("ops/web_console.py").read_text(encoding="utf-8")
    assert "import openai" not in web_console_code
    assert "openai." not in web_console_code
    assert "import requests" not in web_console_code
    assert "import httpx" not in web_console_code
    assert "azure" not in web_console_code.lower()


# 20. test_docs_exist_and_include_safety_boundaries
def test_docs_exist_and_include_safety_boundaries():
    """Verify that web console documentation defines continuous training safety limits."""
    doc_path = Path("docs/training_web_console_operating_procedure.md")
    assert doc_path.exists()
    content = doc_path.read_text(encoding="utf-8")
    assert "no auto-approval" in content.lower()
    assert "no live prompt" in content.lower()
    assert "no provider upload" in content.lower()
    assert "no fine-tune" in content.lower()
    assert "no direct deployment" in content.lower()


# 21. test_readiness_includes_web_console_files
def test_readiness_includes_web_console_files():
    """Verify that readiness check results audit training web console files."""
    auditor = ContinuousTrainingReadinessAuditor()
    
    # 1. Pipeline modules checks include ops/web_console.py, app.js, index.html, styles.css
    pipeline_checks = auditor.check_training_pipeline_modules()
    assert any(c.check_id == "ops_web_console" for c in pipeline_checks)
    assert any(c.check_id == "static_index" for c in pipeline_checks)
    assert any(c.check_id == "static_js" for c in pipeline_checks)
    assert any(c.check_id == "static_css" for c in pipeline_checks)
    
    # 2. Document checks include docs/training_web_console_operating_procedure.md
    doc_checks = auditor.check_docs_and_runbooks()
    assert any(c.check_id == "doc_training_web_console_operating_procedure" for c in doc_checks)
    
    # 3. Test checks include tests/test_training_web_console.py
    test_checks = auditor.check_tests_exist()
    assert any(c.check_id == "test_test_training_web_console" for c in test_checks)
    
    # 4. Web Console safety checks
    safety_checks = auditor.check_web_console_safety()
    assert len(safety_checks) > 0
    for check in safety_checks:
        assert check.passed is True


# 22. test_json_error_response_shape
@pytest.mark.asyncio
async def test_json_error_response_shape():
    """Verify json response shape returned on api failures is correctly nested."""
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    
    # Simulate internal handler error
    status, data = await server.handle_api("POST", "/api/review-items/123/approve", {"reviewer": ""})
    assert status == 400
    assert data["success"] is False
    assert "error" in data or "message" in data


# 23. test_api_unknown_route_returns_404_json
@pytest.mark.asyncio
async def test_api_unknown_route_returns_404_json():
    """Verify unknown /api paths return status 404 and structured JSON error."""
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config)
    status, data = await server.handle_api("GET", "/api/unknown-endpoint-route", None)
    assert status == 404
    assert data["success"] is False
    assert "route not found" in data["error"]


# 24. test_upload_size_limit_constant_exists
def test_upload_size_limit_constant_exists():
    """Verify that MAX_UPLOAD_SIZE is defined as 10 MB."""
    assert MAX_UPLOAD_SIZE == 10 * 1024 * 1024
