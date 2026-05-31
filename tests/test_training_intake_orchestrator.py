import os
import sys
import json
import shutil
import subprocess
from pathlib import Path
from datetime import datetime, timezone
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

@pytest.fixture(autouse=True)
def isolated_paths(tmp_path: Path, monkeypatch):
    """Isolates state and output files to a temp directory to prevent leakage."""
    temp_output_dir = tmp_path / "intake_reports"
    temp_state_path = temp_output_dir / "intake_state.json"
    
    orig_init = TrainingIntakeConfig.__init__
    
    def patched_init(self, *args, **kwargs):
        if "output_dir" not in kwargs:
            kwargs["output_dir"] = str(temp_output_dir)
        if "state_path" not in kwargs:
            kwargs["state_path"] = str(temp_state_path)
        orig_init(self, *args, **kwargs)
        
    monkeypatch.setattr(TrainingIntakeConfig, "__init__", patched_init)


from storage.repository import Repository
from training.ingestion import TrainingIngestionService
from training.labeler import TranscriptLabeler, TranscriptLabelingResult
from training.example_miner import TrainingExampleMiner, MiningResult
from training.intake_orchestrator import (
    TrainingIntakeOrchestrator,
    TrainingIntakeConfig,
    TrainingIntakeItemResult,
    TrainingIntakeRunResult,
)
from core.agent_runtime import AgentRuntime


@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    """Returns a temporary repository instance."""
    return Repository(data_dir=tmp_path)


@pytest.fixture
def ingestion_service(repo: Repository) -> TrainingIngestionService:
    """Returns a temporary ingestion service."""
    return TrainingIngestionService(repo)


@pytest.fixture
def orchestrator(repo: Repository) -> TrainingIntakeOrchestrator:
    """Returns an orchestrator instance."""
    return TrainingIntakeOrchestrator(repository=repo)


# 1. test_ingest_post_call_payload_creates_training_source
@pytest.mark.asyncio
async def test_ingest_post_call_payload_creates_training_source(orchestrator: TrainingIntakeOrchestrator, repo: Repository):
    payload = {
        "call_id": "call_123",
        "started_at": "2026-05-31T09:00:00Z",
        "ended_at": "2026-05-31T09:05:00Z",
        "direction": "outbound",
        "campaign": "final_expense",
        "prospect_phone": "+15555555555",
        "recording_url": "http://recordings/call_123.mp3",
        "outcome": "transfer",
        "transfer_consent": True,
        "turns": [
            {"speaker": "agent", "text": "Hello, my name is Alex with American Beneficiary.", "timestamp": "2026-05-31T09:00:05Z"},
            {"speaker": "prospect", "text": "Hi, how much is this life insurance?", "timestamp": "2026-05-31T09:00:15Z"},
        ]
    }
    
    config = TrainingIntakeConfig(mode="post_call", label_after_ingest=False, mine_after_label=False)
    res = await orchestrator.ingest_post_call_payload(payload, config)
    
    assert res.status == "ingested"
    assert res.training_source_id is not None
    
    # Check that source is saved in database
    src = await repo.get_training_source(res.training_source_id)
    assert src is not None
    assert src["source_type"] == "post_call"
    assert "Post-Call call_123" in src["title"]
    
    meta = src["metadata"]
    assert meta["original_metadata"]["call_id"] == "call_123"
    assert meta["original_metadata"]["outcome"] == "transfer"
    assert meta["original_metadata"]["transfer_consent"] is True
    
    # Redaction checks (phone redacted)
    norm_turns = meta["normalized_turns"]
    assert len(norm_turns) == 2
    assert norm_turns[0]["speaker"] == "agent"
    assert "+15555555555" not in json.dumps(src)


# 2. test_post_call_payload_deduplicates_by_content
@pytest.mark.asyncio
async def test_post_call_payload_deduplicates_by_content(orchestrator: TrainingIntakeOrchestrator):
    payload = {
        "call_id": "call_duplicate",
        "turns": [
            {"speaker": "agent", "text": "Hello this is a test"},
            {"speaker": "prospect", "text": "Yes hello test"}
        ]
    }
    
    config = TrainingIntakeConfig(mode="post_call", label_after_ingest=False, mine_after_label=False)
    
    # Process once
    res1 = await orchestrator.ingest_post_call_payload(payload, config)
    assert res1.status == "ingested"
    
    # Process again
    res2 = await orchestrator.ingest_post_call_payload(payload, config)
    assert res2.status == "duplicate"
    assert res2.training_source_id == res1.training_source_id
    assert res2.duplicate_of == res1.training_source_id


# 3. test_ingest_plain_text_file
@pytest.mark.asyncio
async def test_ingest_plain_text_file(orchestrator: TrainingIntakeOrchestrator, tmp_path: Path, repo: Repository):
    txt_file = tmp_path / "transcript.txt"
    txt_file.write_text("Agent: Hello\nProspect: Goodbye", encoding="utf-8")
    
    config = TrainingIntakeConfig(mode="folder", label_after_ingest=False, mine_after_label=False)
    res = await orchestrator.ingest_file(txt_file, "call_transcript", config)
    
    assert res.status == "ingested"
    assert res.training_source_id is not None
    
    src = await repo.get_training_source(res.training_source_id)
    assert src is not None
    assert len(src["metadata"]["normalized_turns"]) == 2


# 4. test_ingest_json_transcript_file
@pytest.mark.asyncio
async def test_ingest_json_transcript_file(orchestrator: TrainingIntakeOrchestrator, tmp_path: Path, repo: Repository):
    json_file = tmp_path / "transcript.json"
    turns = [
        {"speaker": "agent", "text": "I am a coordinator."},
        {"speaker": "prospect", "text": "Are you a human?"}
    ]
    json_file.write_text(json.dumps({"turns": turns}), encoding="utf-8")
    
    config = TrainingIntakeConfig(mode="folder", label_after_ingest=False, mine_after_label=False)
    res = await orchestrator.ingest_file(json_file, "call_transcript", config)
    
    assert res.status == "ingested"
    assert res.training_source_id is not None
    
    src = await repo.get_training_source(res.training_source_id)
    assert src is not None
    assert src["metadata"]["normalized_turn_count"] == 2


# 5. test_ingest_youtube_transcript_file
@pytest.mark.asyncio
async def test_ingest_youtube_transcript_file(orchestrator: TrainingIntakeOrchestrator, tmp_path: Path, repo: Repository):
    txt_file = tmp_path / "youtube_training" / "video_001.txt"
    txt_file.parent.mkdir(parents=True, exist_ok=True)
    txt_file.write_text("Objection handling strategy lesson", encoding="utf-8")
    
    config = TrainingIntakeConfig(mode="folder", label_after_ingest=False, mine_after_label=False)
    res = await orchestrator.ingest_file(txt_file, "youtube", config)
    
    assert res.status == "ingested"
    assert res.source_type == "youtube"


# 6. test_ingest_manager_note_file
@pytest.mark.asyncio
async def test_ingest_manager_note_file(orchestrator: TrainingIntakeOrchestrator, tmp_path: Path, repo: Repository):
    txt_file = tmp_path / "note.txt"
    txt_file.write_text("Coaching lesson", encoding="utf-8")
    
    config = TrainingIntakeConfig(mode="folder", label_after_ingest=False, mine_after_label=False)
    res = await orchestrator.ingest_file(txt_file, "manager_note", config)
    
    assert res.status == "ingested"
    assert res.source_type == "manager_note"


# 7. test_ingest_licensed_agent_feedback_file
@pytest.mark.asyncio
async def test_ingest_licensed_agent_feedback_file(orchestrator: TrainingIntakeOrchestrator, tmp_path: Path, repo: Repository):
    txt_file = tmp_path / "feedback.txt"
    txt_file.write_text("Agent script correction feedback", encoding="utf-8")
    
    config = TrainingIntakeConfig(mode="folder", label_after_ingest=False, mine_after_label=False)
    res = await orchestrator.ingest_file(txt_file, "licensed_agent_feedback", config)
    
    assert res.status == "ingested"
    assert res.source_type == "licensed_agent_feedback"


# 8. test_folder_discovery_respects_supported_extensions
def test_folder_discovery_respects_supported_extensions(orchestrator: TrainingIntakeOrchestrator, tmp_path: Path):
    (tmp_path / "f1.txt").write_text("txt content")
    (tmp_path / "f2.json").write_text("{}")
    (tmp_path / "f3.jsonl").write_text("{}")
    (tmp_path / "f4.md").write_text("# md")
    (tmp_path / "f5.csv").write_text("csv,unsupported")
    (tmp_path / "sub").mkdir(exist_ok=True)
    (tmp_path / "sub" / "f6.txt").write_text("sub txt")
    
    files = orchestrator.discover_files(tmp_path)
    # Total supported files should be f1, f2, f3, f4, and sub/f6 (total 5)
    assert len(files) == 5
    assert all(f.suffix.lower() in (".txt", ".json", ".jsonl", ".md") for f in files)


# 9. test_state_file_prevents_reprocessing_same_file
@pytest.mark.asyncio
async def test_state_file_prevents_reprocessing_same_file(orchestrator: TrainingIntakeOrchestrator, tmp_path: Path):
    input_dir = tmp_path / "inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    txt_file = input_dir / "same_file.txt"
    txt_file.write_text("Intake content hash trigger", encoding="utf-8")
    
    state_file = tmp_path.parent / "intake_state.json"
    
    config = TrainingIntakeConfig(
        mode="folder",
        input_path=str(input_dir),
        state_path=str(state_file),
        label_after_ingest=False,
        mine_after_label=False
    )
    
    # First execution
    res1 = await orchestrator.run(config)
    assert res1.ingested_count == 1
    assert res1.skipped_count == 0
    
    # Second execution
    res2 = await orchestrator.run(config)
    assert res2.ingested_count == 0
    assert res2.skipped_count == 1


# 10. test_manifest_processes_files_and_payloads
@pytest.mark.asyncio
async def test_manifest_processes_files_and_payloads(orchestrator: TrainingIntakeOrchestrator, tmp_path: Path):
    txt_file = tmp_path / "manifest_file.txt"
    txt_file.write_text("Manifest plain text source", encoding="utf-8")
    
    manifest_data = {
        "items": [
            {
                "type": "youtube",
                "file": str(txt_file),
                "title": "Youtube Video Transcript"
            },
            {
                "type": "post_call",
                "payload": {
                    "call_id": "manifest_call_1",
                    "turns": [{"speaker": "agent", "text": "hello"}]
                }
            }
        ]
    }
    
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")
    
    config = TrainingIntakeConfig(
        mode="manifest",
        manifest_path=str(manifest_file),
        file=str(manifest_file),  # to pass configuration file mappings
        label_after_ingest=False,
        mine_after_label=False
    )
    
    res = await orchestrator.run(config)
    assert res.total_items == 2
    assert res.ingested_count == 2
    assert res.failed_count == 0


# 11. test_manifest_invalid_item_records_failure
@pytest.mark.asyncio
async def test_manifest_invalid_item_records_failure(orchestrator: TrainingIntakeOrchestrator, tmp_path: Path):
    manifest_data = {
        "items": [
            {
                "type": "youtube",
                "file": "nonexistent_file_path_123.txt"
            }
        ]
    }
    
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")
    
    config = TrainingIntakeConfig(
        mode="manifest",
        manifest_path=str(manifest_file),
        file=str(manifest_file),
        continue_on_error=True
    )
    
    res = await orchestrator.run(config)
    assert res.total_items == 1
    assert res.failed_count == 1
    assert res.item_results[0].status == "failed"


# 12. test_label_after_ingest_runs_labeler
@pytest.mark.asyncio
async def test_label_after_ingest_runs_labeler(repo: Repository):
    mock_labeler = MagicMock()
    mock_labeler.label_training_source = AsyncMock()
    
    orchestrator = TrainingIntakeOrchestrator(repository=repo, labeler=mock_labeler)
    
    payload = {
        "call_id": "call_label_test",
        "turns": [{"speaker": "agent", "text": "hello"}]
    }
    
    config = TrainingIntakeConfig(mode="post_call", label_after_ingest=True, mine_after_label=False)
    await orchestrator.ingest_post_call_payload(payload, config)
    
    assert mock_labeler.label_training_source.called


# 13. test_no_label_option_skips_labeler
@pytest.mark.asyncio
async def test_no_label_option_skips_labeler(repo: Repository):
    mock_labeler = MagicMock()
    mock_labeler.label_training_source = AsyncMock()
    
    orchestrator = TrainingIntakeOrchestrator(repository=repo, labeler=mock_labeler)
    
    payload = {
        "call_id": "call_no_label",
        "turns": [{"speaker": "agent", "text": "hello"}]
    }
    
    config = TrainingIntakeConfig(mode="post_call", label_after_ingest=False, mine_after_label=False)
    await orchestrator.ingest_post_call_payload(payload, config)
    
    assert not mock_labeler.label_training_source.called


# 14. test_mine_after_label_creates_review_items
@pytest.mark.asyncio
async def test_mine_after_label_creates_review_items(repo: Repository):
    mock_labeler = MagicMock()
    mock_labeler.label_training_source = AsyncMock()
    
    mock_miner = MagicMock()
    mock_miner.mine_source = AsyncMock(return_value=MiningResult(
        source_id="mock-src", total_turns=1, candidates_created=3, skipped_candidates=0,
        compliance_review_items=0, eval_case_candidates=0, training_example_candidates=3, failure_candidates=0
    ))
    
    orchestrator = TrainingIntakeOrchestrator(repository=repo, labeler=mock_labeler, miner=mock_miner)
    
    payload = {
        "call_id": "call_mine_test",
        "turns": [{"speaker": "agent", "text": "hello"}]
    }
    
    config = TrainingIntakeConfig(mode="post_call", label_after_ingest=True, mine_after_label=True)
    res = await orchestrator.ingest_post_call_payload(payload, config)
    
    assert res.labeled is True
    assert res.mined is True
    assert res.review_items_created == 3
    assert mock_miner.mine_source.called


# 15. test_no_mine_option_skips_miner
@pytest.mark.asyncio
async def test_no_mine_option_skips_miner(repo: Repository):
    mock_labeler = MagicMock()
    mock_labeler.label_training_source = AsyncMock()
    
    mock_miner = MagicMock()
    mock_miner.mine_source = AsyncMock()
    
    orchestrator = TrainingIntakeOrchestrator(repository=repo, labeler=mock_labeler, miner=mock_miner)
    
    payload = {
        "call_id": "call_no_mine",
        "turns": [{"speaker": "agent", "text": "hello"}]
    }
    
    config = TrainingIntakeConfig(mode="post_call", label_after_ingest=True, mine_after_label=False)
    await orchestrator.ingest_post_call_payload(payload, config)
    
    assert not mock_miner.mine_source.called


# 16. test_daily_mode_scans_standard_folders
@pytest.mark.asyncio
async def test_daily_mode_scans_standard_folders(orchestrator: TrainingIntakeOrchestrator, tmp_path: Path):
    # Set up config folders mock pointing to our temp folders
    f1 = tmp_path / "imports" / "call_transcripts"
    f2 = tmp_path / "imports" / "youtube_training"
    f1.mkdir(parents=True, exist_ok=True)
    f2.mkdir(parents=True, exist_ok=True)
    
    (f1 / "call_1.txt").write_text("Agent: Hello", encoding="utf-8")
    (f2 / "video_1.txt").write_text("Lesson objection", encoding="utf-8")
    
    config = TrainingIntakeConfig(
        mode="daily",
        folders=[str(f1), str(f2)],
        label_after_ingest=False,
        mine_after_label=False
    )
    
    res = await orchestrator.run(config)
    assert res.total_items == 2
    assert res.ingested_count == 2


# 17. test_daily_qa_optional_and_safe_no_logs
@pytest.mark.asyncio
async def test_daily_qa_optional_and_safe_no_logs(repo: Repository):
    orchestrator = TrainingIntakeOrchestrator(repository=repo)
    config = TrainingIntakeConfig(
        mode="daily",
        folders=[],
        run_daily_qa=True,
        since="2026-05-31"
    )
    
    res = await orchestrator.run(config)
    assert res.daily_qa_ran is True
    # Should not crash, but run_daily_qa_summary has 0 calls analyzed
    assert res.daily_qa_summary.get("total_calls_analyzed", 0) == 0


# 18. test_dry_run_does_not_save_training_sources
@pytest.mark.asyncio
async def test_dry_run_does_not_save_training_sources(orchestrator: TrainingIntakeOrchestrator, repo: Repository):
    payload = {
        "call_id": "call_dryrun",
        "turns": [{"speaker": "agent", "text": "hello"}]
    }
    
    config = TrainingIntakeConfig(mode="post_call", dry_run=True)
    res = await orchestrator.ingest_post_call_payload(payload, config)
    
    assert res.status == "dry_run"
    assert res.training_source_id is None
    
    # Assert repository doesn't have it
    sources = await repo.list_recent_training_sources(limit=10)
    assert len(sources) == 0


# 19. test_cli_folder_outputs_json
def test_cli_folder_outputs_json(tmp_path: Path):
    input_dir = tmp_path / "inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    txt_file = input_dir / "cli_test_file.txt"
    txt_file.write_text("Agent: Hello", encoding="utf-8")
    
    cmd = [
        sys.executable,
        "scripts/run_training_intake.py",
        "folder",
        "--path", str(input_dir),
        "--type", "call_transcript",
        "--output-dir", str(tmp_path / "reports"),
        "--state-path", str(tmp_path / "intake_state.json"),
        "--no-label",
        "--no-mine"
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    env["DANA_DATA_DIR"] = str(tmp_path)
    
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 0
    
    # Verify stdout parseable JSON
    data = json.loads(res.stdout.strip())
    assert data["mode"] == "folder"
    assert data["total_items"] == 1
    assert data["ingested_count"] == 1


# 20. test_cli_manifest_outputs_json
def test_cli_manifest_outputs_json(tmp_path: Path):
    manifest_data = {
        "items": [
            {
                "type": "manager_note",
                "payload": {
                    "call_id": "cli_manifest_1",
                    "turns": [{"speaker": "agent", "text": "hello"}]
                }
            }
        ]
    }
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")
    
    cmd = [
        sys.executable,
        "scripts/run_training_intake.py",
        "manifest",
        "--file", str(manifest_file),
        "--output-dir", str(tmp_path / "reports"),
        "--state-path", str(tmp_path / "intake_state.json"),
        "--no-label",
        "--no-mine"
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    env["DANA_DATA_DIR"] = str(tmp_path)
    
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 0
    
    data = json.loads(res.stdout.strip())
    assert data["mode"] == "manifest"
    assert data["total_items"] == 1


# 21. test_cli_post_call_outputs_json
def test_cli_post_call_outputs_json(tmp_path: Path):
    payload = {
        "call_id": "cli_post_call_1",
        "turns": [{"speaker": "agent", "text": "hello"}]
    }
    payload_file = tmp_path / "payload.json"
    payload_file.write_text(json.dumps(payload), encoding="utf-8")
    
    cmd = [
        sys.executable,
        "scripts/run_training_intake.py",
        "post-call",
        "--file", str(payload_file),
        "--output-dir", str(tmp_path / "reports"),
        "--state-path", str(tmp_path / "intake_state.json"),
        "--no-label",
        "--no-mine"
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    env["DANA_DATA_DIR"] = str(tmp_path)
    
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 0
    
    data = json.loads(res.stdout.strip())
    assert data["mode"] == "post_call"
    assert data["total_items"] == 1


# 22. test_cli_fail_fast_exits_1_on_failure
def test_cli_fail_fast_exits_1_on_failure(tmp_path: Path):
    manifest_data = {
        "items": [
            {
                "type": "youtube",
                "file": "nonexistent_file_path_123.txt"
            }
        ]
    }
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")
    
    cmd = [
        sys.executable,
        "scripts/run_training_intake.py",
        "manifest",
        "--file", str(manifest_file),
        "--output-dir", str(tmp_path / "reports"),
        "--state-path", str(tmp_path / "intake_state.json"),
        "--fail-fast"
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    env["DANA_DATA_DIR"] = str(tmp_path)
    
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 1


# 23. test_no_live_prompt_file_modified
@pytest.mark.asyncio
async def test_no_live_prompt_file_modified(orchestrator: TrainingIntakeOrchestrator):
    live_prompt = Path("prompts/final_expense_alex.md")
    content_before = live_prompt.read_text(encoding="utf-8")
    
    payload = {
        "call_id": "prompt_immutability_check",
        "turns": [{"speaker": "agent", "text": "hello"}]
    }
    
    config = TrainingIntakeConfig(mode="post_call")
    await orchestrator.ingest_post_call_payload(payload, config)
    
    content_after = live_prompt.read_text(encoding="utf-8")
    assert content_before == content_after


# 24. test_no_auto_approval_created
@pytest.mark.asyncio
async def test_no_auto_approval_created(repo: Repository):
    # Test that mining creates PENDING review items only
    # Real pipeline review trigger: Agent: Hello. Prospect: DNC. Agent: Sorry.
    orchestrator = TrainingIntakeOrchestrator(repository=repo)
    
    payload = {
        "call_id": "compliance_trigger_call",
        "turns": [
            {"speaker": "agent", "text": "Hello, this is Alex.", "stage": "opening"},
            {"speaker": "prospect", "text": "Stop calling me, put me on DNC.", "stage": "dnc"},
            {"speaker": "agent", "text": "Sorry about that, goodbye.", "stage": "end"}
        ]
    }
    
    config = TrainingIntakeConfig(mode="post_call", label_after_ingest=True, mine_after_label=True)
    res = await orchestrator.ingest_post_call_payload(payload, config)
    
    assert res.review_items_created > 0
    
    # Load created review items and assert all are pending
    items = await repo.query_human_review_items({})
    assert len(items) > 0
    for item in items:
        assert item["status"] == "pending"
        assert item["reviewer"] is None
        assert item["reviewed_at"] is None


# 25. test_no_external_api_calls
def test_no_external_api_calls():
    # Verify intake orchestrator code does not import provider libraries
    with open("training/intake_orchestrator.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "import openai" not in content
    assert "openai." not in content
    assert "import requests" not in content
    assert "import httpx" not in content
    assert "urllib." not in content


# 26. test_runtime_hook_disabled_by_default_if_added
@pytest.mark.asyncio
async def test_runtime_hook_disabled_by_default_if_added(repo: Repository):
    # Setup runtime mocks
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
    
    payload = {
        "call_id": "runtime_hook_call",
        "turns": [{"speaker": "agent", "text": "hello"}]
    }
    
    # Ensure env var is disabled
    with patch.dict(os.environ, {"DANA_ENABLE_POST_CALL_TRAINING_INTAKE": "false"}):
        await runtime.record_completed_call_for_training(payload)
        
    # Repository should be clean (nothing saved)
    sources = await repo.list_recent_training_sources(limit=10)
    assert len(sources) == 0


# 27. test_report_files_written
@pytest.mark.asyncio
async def test_report_files_written(orchestrator: TrainingIntakeOrchestrator, tmp_path: Path):
    txt_file = tmp_path / "report_file.txt"
    txt_file.write_text("Coaching note info", encoding="utf-8")
    
    report_dir = tmp_path / "reports"
    
    config = TrainingIntakeConfig(
        mode="folder",
        input_path=str(tmp_path),
        source_type="manager_note",
        output_dir=str(report_dir),
        label_after_ingest=False,
        mine_after_label=False
    )
    
    res = await orchestrator.run(config)
    
    assert res.report_json_path is not None
    assert res.report_markdown_path is not None
    assert Path(res.report_json_path).exists()
    assert Path(res.report_markdown_path).exists()


# 28. test_report_contains_safety_notes
@pytest.mark.asyncio
async def test_report_contains_safety_notes(orchestrator: TrainingIntakeOrchestrator, tmp_path: Path):
    report_dir = tmp_path / "reports"
    
    config = TrainingIntakeConfig(
        mode="daily",
        folders=[],
        output_dir=str(report_dir),
        label_after_ingest=False,
        mine_after_label=False
    )
    
    res = await orchestrator.run(config)
    md_content = Path(res.report_markdown_path).read_text(encoding="utf-8")
    
    assert "No auto-approval performed" in md_content
    assert "No prompt edits performed" in md_content
    assert "No fine-tuning started" in md_content
    assert "No provider API calls" in md_content


# 29. test_infer_source_type_from_folder
def test_infer_source_type_from_folder(orchestrator: TrainingIntakeOrchestrator):
    assert orchestrator.infer_source_type_from_path("data/imports/call_transcripts/c1.txt") == "call_transcript"
    assert orchestrator.infer_source_type_from_path("data/imports/youtube_training/y1.txt") == "youtube"
    assert orchestrator.infer_source_type_from_path("data/imports/manager_notes/m1.txt") == "manager_note"
    assert orchestrator.infer_source_type_from_path("data/imports/licensed_agent_feedback/f1.txt") == "licensed_agent_feedback"
    assert orchestrator.infer_source_type_from_path("data/imports/post_call_payloads/p1.txt") == "post_call"
    assert orchestrator.infer_source_type_from_path("data/imports/random_folder/r1.txt") == "unknown"


# 30. test_continue_on_error_processes_remaining_items
@pytest.mark.asyncio
async def test_continue_on_error_processes_remaining_items(orchestrator: TrainingIntakeOrchestrator, tmp_path: Path):
    manifest_data = {
        "items": [
            {
                "type": "youtube",
                "file": "nonexistent_file_path_123.txt"
            },
            {
                "type": "post_call",
                "payload": {
                    "call_id": "good_manifest_call",
                    "turns": [{"speaker": "agent", "text": "hello"}]
                }
            }
        ]
    }
    
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")
    
    config = TrainingIntakeConfig(
        mode="manifest",
        manifest_path=str(manifest_file),
        file=str(manifest_file),
        continue_on_error=True,
        label_after_ingest=False,
        mine_after_label=False
    )
    
    res = await orchestrator.run(config)
    assert res.total_items == 2
    assert res.failed_count == 1
    assert res.ingested_count == 1
