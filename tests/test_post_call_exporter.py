import json
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

from storage.repository import Repository
from training.post_call_exporter import PostCallExporter, PostCallExportConfig
from core.agent_runtime import AgentRuntime


@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    return Repository(data_dir=tmp_path)


@pytest.fixture
def exporter(repo: Repository) -> PostCallExporter:
    return PostCallExporter(repository=repo)


# 1. test_export_disabled_by_default
@pytest.mark.asyncio
async def test_export_disabled_by_default(exporter: PostCallExporter):
    config = PostCallExportConfig(enabled=False)
    payload = {"call_id": "test_1", "turns": []}
    res = await exporter.export_completed_call(payload, config)
    assert res.exported is False
    assert "disabled" in res.warnings[0]


# 2. test_export_completed_call_writes_payload_when_enabled
@pytest.mark.asyncio
async def test_export_completed_call_writes_payload_when_enabled(exporter: PostCallExporter, tmp_path: Path):
    out_dir = tmp_path / "exports"
    config = PostCallExportConfig(enabled=True, output_dir=str(out_dir))
    payload = {
        "call_id": "test_2",
        "turns": [{"speaker": "agent", "text": "Hello"}, {"speaker": "user", "text": "Hi"}]
    }
    
    res = await exporter.export_completed_call(payload, config)
    assert res.exported is True
    assert res.output_path is not None
    assert Path(res.output_path).exists()
    
    data = json.loads(Path(res.output_path).read_text(encoding="utf-8"))
    assert data["call_id"] == "test_2"
    assert len(data["turns"]) == 2


# 3. test_export_redacts_phone_email_and_sensitive_data
@pytest.mark.asyncio
async def test_export_redacts_phone_email_and_sensitive_data(exporter: PostCallExporter, tmp_path: Path):
    out_dir = tmp_path / "exports"
    config = PostCallExportConfig(enabled=True, output_dir=str(out_dir))
    payload = {
        "call_id": "test_3",
        "prospect_phone": "+15555555555",
        "turns": [
            {"speaker": "prospect", "text": "My email is test@domain.com and SSN is 123-45-6789."},
            {"speaker": "prospect", "text": "My date of birth is 01-02-1980 and Medicare number is 1A23-BC4-DE56."}
        ]
    }
    
    res = await exporter.export_completed_call(payload, config)
    assert res.exported is True
    
    data = json.loads(Path(res.output_path).read_text(encoding="utf-8"))
    assert data["prospect_phone"] == "[REDACTED_PHONE]"
    assert "[REDACTED_EMAIL]" in data["turns"][0]["text"]
    assert "[REDACTED_SSN]" in data["turns"][0]["text"]
    assert "[REDACTED_DOB]" in data["turns"][1]["text"]
    assert "[REDACTED_MEDICARE]" in data["turns"][1]["text"]


# 4. test_export_preserves_call_metadata
@pytest.mark.asyncio
async def test_export_preserves_call_metadata(exporter: PostCallExporter, tmp_path: Path):
    out_dir = tmp_path / "exports"
    config = PostCallExportConfig(enabled=True, output_dir=str(out_dir))
    payload = {
        "call_id": "test_4",
        "campaign": "final_expense",
        "direction": "outbound",
        "outcome": "transfer",
        "transfer_consent": True,
        "metadata": {"lead_source": "landing_page"}
    }
    
    res = await exporter.export_completed_call(payload, config)
    data = json.loads(Path(res.output_path).read_text(encoding="utf-8"))
    assert data["campaign"] == "final_expense"
    assert data["direction"] == "outbound"
    assert data["outcome"] == "transfer"
    assert data["transfer_consent"] is True
    assert data["metadata"]["lead_source"] == "landing_page"


# 5. test_export_normalizes_turns
@pytest.mark.asyncio
async def test_export_normalizes_turns(exporter: PostCallExporter):
    raw_turns = [
        {"speaker": "assistant", "text": "hello"},
        {"speaker": "user", "text": "objection"}
    ]
    norm = exporter.normalize_turns(raw_turns)
    assert len(norm) == 2
    assert norm[0]["speaker"] == "agent"
    assert norm[1]["speaker"] == "prospect"


# 6. test_export_dry_run_does_not_write_file
@pytest.mark.asyncio
async def test_export_dry_run_does_not_write_file(exporter: PostCallExporter, tmp_path: Path):
    out_dir = tmp_path / "exports"
    config = PostCallExportConfig(enabled=True, output_dir=str(out_dir), dry_run=True)
    payload = {"call_id": "test_6", "turns": []}
    
    res = await exporter.export_completed_call(payload, config)
    assert res.exported is True
    assert res.dry_run is True
    assert res.output_path is not None
    assert not Path(res.output_path).exists()


# 7. test_safe_export_never_raises_when_fail_silently_true
@pytest.mark.asyncio
async def test_safe_export_never_raises_when_fail_silently_true(exporter: PostCallExporter):
    config = PostCallExportConfig(enabled=True, fail_silently=True)
    # Invalid payload structure (None) to cause internal exception
    res = await exporter.safe_export_completed_call(None, config)
    assert res.exported is False
    assert res.error is not None
    assert len(res.warnings) > 0


# 8. test_run_intake_after_export_optional
@pytest.mark.asyncio
async def test_run_intake_after_export_optional(repo: Repository, tmp_path: Path):
    mock_orch = MagicMock()
    mock_orch.run = AsyncMock(return_value={"status": "ingested"})
    
    exporter = PostCallExporter(repository=repo)
    
    out_dir = tmp_path / "exports"
    config = PostCallExportConfig(
        enabled=True,
        output_dir=str(out_dir),
        run_intake_after_export=True,
        intake_sync=True
    )
    
    payload = {"call_id": "test_8", "turns": []}
    
    with patch("training.intake_orchestrator.TrainingIntakeOrchestrator") as mock_class:
        mock_class.return_value = mock_orch
        await exporter.export_completed_call(payload, config)
        assert mock_orch.run.called


# 9. test_payload_from_runtime_state_basic
def test_payload_from_runtime_state_basic(exporter: PostCallExporter):
    from core.call_state import CallState
    from datetime import datetime, timezone
    
    call_state = CallState(
        started_at=datetime.fromisoformat("2026-05-31T10:00:00+00:00"),
        last_transition_at=datetime.fromisoformat("2026-05-31T10:05:00+00:00")
    )
    
    payload = exporter.payload_from_runtime_state(call_state, {"call_id": "runtime_1"})
    assert payload["call_id"] == "runtime_1"
    assert payload["started_at"] == "2026-05-31T10:00:00+00:00"


# 10. test_no_live_prompt_file_modified
@pytest.mark.asyncio
async def test_no_live_prompt_file_modified(exporter: PostCallExporter):
    live_prompt = Path("prompts/final_expense_alex.md")
    content_before = live_prompt.read_text(encoding="utf-8")
    
    config = PostCallExportConfig(enabled=True)
    payload = {"call_id": "test_10", "turns": []}
    await exporter.safe_export_completed_call(payload, config)
    
    content_after = live_prompt.read_text(encoding="utf-8")
    assert content_before == content_after


# 11. test_no_external_api_calls
def test_no_external_api_calls():
    with open("training/post_call_exporter.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "import openai" not in content
    assert "openai." not in content
    assert "import requests" not in content
    assert "import httpx" not in content


# 12. test_runtime_hook_disabled_by_default_if_added
@pytest.mark.asyncio
async def test_runtime_hook_disabled_by_default_if_added(repo: Repository):
    prompt_loader = MagicMock()
    state_machine = MagicMock()
    objection_classifier = MagicMock()
    objection_policy = MagicMock()
    context_builder = MagicMock()
    action_policy = MagicMock()
    tool_registry = MagicMock()
    compliance_filter = MagicMock()
    output_validator = MagicMock()
    call_stop_policy = MagicMock()
    pii_redactor = MagicMock()
    
    runtime = AgentRuntime(
        prompt_loader=prompt_loader,
        state_machine=state_machine,
        objection_classifier=objection_classifier,
        objection_policy=objection_policy,
        context_builder=context_builder,
        action_policy=action_policy,
        tool_registry=tool_registry,
        compliance_filter=compliance_filter,
        output_validator=output_validator,
        call_stop_policy=call_stop_policy,
        pii_redactor=pii_redactor,
        repository=repo
    )
    
    payload = {"call_id": "runtime_hook_test_24", "turns": []}
    
    with patch.dict(os.environ, {"DANA_ENABLE_POST_CALL_TRAINING_EXPORT": "false"}):
        await runtime.record_completed_call_for_training(payload)
        
    # Verify no file written
    exports_dir = Path("data/imports/post_call_payloads")
    if exports_dir.exists():
        files = list(exports_dir.glob("runtime_hook_test_24.json"))
        assert len(files) == 0
