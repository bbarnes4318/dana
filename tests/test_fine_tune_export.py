"""Tests for fine-tuning dataset export builder, validation, and CLI (Prompt 18)."""

from __future__ import annotations

import os
import sys
import json
import tempfile
import asyncio
import subprocess
from pathlib import Path
from datetime import datetime, timezone
import pytest

from storage.repository import Repository
from training.fine_tune_export import (
    FineTuneExportBuilder,
    FineTuneExportConfig,
    FineTuneExampleRecord,
    FineTuneValidationResult,
)

@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    """Return a Repository backed by a temporary JSONL store."""
    return Repository(data_dir=tmp_path)

@pytest.fixture
def builder(repo: Repository) -> FineTuneExportBuilder:
    """Return a FineTuneExportBuilder linked to the test Repository."""
    return FineTuneExportBuilder(repository=repo)

async def save_valid_approved_example(repo: Repository, **kwargs) -> str:
    payload = {
        "source_id": "src_123",
        "stage": "objection_handling",
        "user_text": "Is this a real person?",
        "ideal_response": "I am an automated assistant. A licensed agent can review plan details with you.",
        "labels": {
            "fine_tune_eligible": True,
            "compliance_risk": "none",
            "objection_type": "human_check"
        },
        "use_for": ["fine_tune"],
        "approved_by": "Jimmy",
        "approved_at": datetime.now(timezone.utc)
    }
    payload.update(kwargs)
    return await repo.save_training_example(**payload)


# 1. test_loads_only_fine_tune_eligible_training_examples
@pytest.mark.asyncio
async def test_loads_only_fine_tune_eligible_training_examples(repo: Repository, builder: FineTuneExportBuilder) -> None:
    await save_valid_approved_example(repo, id="ex-eligible")
    await save_valid_approved_example(repo, id="ex-not-labels", labels={"fine_tune_eligible": False})
    await save_valid_approved_example(repo, id="ex-not-use-for", use_for=["prompt"])
    
    config = FineTuneExportConfig(
        export_name="test_eligibility",
        min_examples=1,
        dry_run=True
    )
    candidates = await builder.load_candidate_training_examples(config)
    candidate_ids = [c["id"] for c in candidates]
    assert "ex-eligible" in candidate_ids
    assert "ex-not-labels" not in candidate_ids
    assert "ex-not-use-for" not in candidate_ids


# 2. test_rejects_unapproved_or_missing_approval_metadata
@pytest.mark.asyncio
async def test_rejects_unapproved_or_missing_approval_metadata(repo: Repository, builder: FineTuneExportBuilder) -> None:
    await save_valid_approved_example(repo, id="ex-unapproved", approved_by=None, approved_at=None)
    
    config = FineTuneExportConfig(
        export_name="test_approval",
        min_examples=1,
        require_fine_tune_eligible=True,
        dry_run=True
    )
    candidates = await builder.load_candidate_training_examples(config)
    assert not any(c["id"] == "ex-unapproved" for c in candidates)


# 3. test_validation_blocks_you_qualify
def test_validation_blocks_you_qualify(builder: FineTuneExportBuilder) -> None:
    ex = {
        "user_text": "Do I qualify?",
        "ideal_response": "Yes, you qualify for this program."
    }
    config = FineTuneExportConfig(export_name="test", min_examples=1)
    res = builder.validate_training_example(ex, config)
    assert res.passed is False
    assert any("you qualify" in f.lower() for f in res.critical_failures)


# 4. test_validation_blocks_price_quote
def test_validation_blocks_price_quote(builder: FineTuneExportBuilder) -> None:
    ex = {
        "user_text": "How much does it cost?",
        "ideal_response": "It is $29.99 per month."
    }
    config = FineTuneExportConfig(export_name="test", min_examples=1)
    res = builder.validate_training_example(ex, config)
    assert res.passed is False
    assert any("price" in f.lower() for f in res.critical_failures)


# 5. test_validation_blocks_licensed_self_claim
def test_validation_blocks_licensed_self_claim(builder: FineTuneExportBuilder) -> None:
    ex = {
        "user_text": "Are you licensed?",
        "ideal_response": "Yes, I am licensed to sell final expense plans."
    }
    config = FineTuneExportConfig(export_name="test", min_examples=1)
    res = builder.validate_training_example(ex, config)
    assert res.passed is False
    assert any("licensed" in f.lower() for f in res.critical_failures)


# 6. test_validation_allows_safe_licensed_agent_reference
def test_validation_allows_safe_licensed_agent_reference(builder: FineTuneExportBuilder) -> None:
    ex = {
        "user_text": "Are you licensed?",
        "ideal_response": "I can connect you with a licensed agent if you want."
    }
    config = FineTuneExportConfig(export_name="test", min_examples=1)
    res = builder.validate_training_example(ex, config)
    assert res.passed is True
    assert not res.critical_failures


# 7. test_validation_blocks_human_claim
def test_validation_blocks_human_claim(builder: FineTuneExportBuilder) -> None:
    ex = {
        "user_text": "Are you a robot?",
        "ideal_response": "No, I'm a real person."
    }
    config = FineTuneExportConfig(export_name="test", min_examples=1)
    res = builder.validate_training_example(ex, config)
    assert res.passed is False
    assert any("human" in f.lower() or "person" in f.lower() for f in res.critical_failures)


# 8. test_redacts_email_and_phone
def test_redacts_email_and_phone(builder: FineTuneExportBuilder) -> None:
    text = "My email is jimbo@example.com and phone is 1-800-555-0199."
    redacted, reds = builder.redact_text(text)
    assert "[REDACTED_EMAIL]" in redacted
    assert "[REDACTED_PHONE]" in redacted
    assert reds.get("email") == 1
    assert reds.get("phone") == 1


# 9. test_redacts_sensitive_numbers_but_preserves_age
def test_redacts_sensitive_numbers_but_preserves_age(builder: FineTuneExportBuilder) -> None:
    text = "I am 72 years old. My SSN is 123-45-6789."
    redacted, reds = builder.redact_text(text)
    assert "72" in redacted
    assert "[REDACTED_SSN]" in redacted
    assert reds.get("ssn") == 1


# 10. test_deduplicates_same_user_assistant_pair
def test_deduplicates_same_user_assistant_pair(builder: FineTuneExportBuilder) -> None:
    records = [
        FineTuneExampleRecord(
            training_example_id="1",
            user_text="hi",
            assistant_text="hello",
            stage="opening",
            objection_type="none",
            content_hash="h1",
            redactions={},
            validation={}
        ),
        FineTuneExampleRecord(
            training_example_id="2",
            user_text="hi",
            assistant_text="hello",
            stage="opening",
            objection_type="none",
            content_hash="h1",
            redactions={},
            validation={}
        ),
    ]
    deduped, info = builder.dedupe_examples(records)
    assert len(deduped) == 1
    assert info.get("duplicate") == 1


# 11. test_train_validation_split_deterministic
def test_train_validation_split_deterministic(builder: FineTuneExportBuilder) -> None:
    records = [
        FineTuneExampleRecord(
            training_example_id=str(i),
            user_text=f"hi {i}",
            assistant_text=f"hello {i}",
            content_hash=str(i),
            redactions={},
            validation={}
        )
        for i in range(10)
    ]
    train1, val1 = builder.split_train_validation(records, 0.8, 42)
    train2, val2 = builder.split_train_validation(records, 0.8, 42)
    assert [r.training_example_id for r in train1] == [r.training_example_id for r in train2]
    assert [r.training_example_id for r in val1] == [r.training_example_id for r in val2]


# 12. test_validation_gets_at_least_one_record_when_possible
def test_validation_gets_at_least_one_record_when_possible(builder: FineTuneExportBuilder) -> None:
    records = [
        FineTuneExampleRecord(
            training_example_id="1", user_text="a", assistant_text="b", content_hash="h1", redactions={}, validation={}
        ),
        FineTuneExampleRecord(
            training_example_id="2", user_text="c", assistant_text="d", content_hash="h2", redactions={}, validation={}
        )
    ]
    train, val = builder.split_train_validation(records, 0.9, 42)
    assert len(train) == 1
    assert len(val) == 1


# 13. test_openai_chat_jsonl_format
def test_openai_chat_jsonl_format(builder: FineTuneExportBuilder) -> None:
    record = FineTuneExampleRecord(
        training_example_id="ex_1",
        user_text="How are you?",
        assistant_text="I am doing well.",
        stage="opening",
        objection_type="none",
        source_id="src_1",
        content_hash="h1",
        redactions={},
        validation={}
    )
    config = FineTuneExportConfig(export_name="test", format="openai_chat_jsonl")
    serialized = builder.serialize_record(record, config)
    assert "messages" in serialized
    assert serialized["messages"][0]["role"] == "system"
    assert serialized["messages"][1]["role"] == "user"
    assert serialized["messages"][1]["content"] == "How are you?"
    assert serialized["messages"][2]["role"] == "assistant"
    assert serialized["messages"][2]["content"] == "I am doing well."
    assert serialized["metadata"]["training_example_id"] == "ex_1"


# 14. test_generic_pairs_jsonl_format
def test_generic_pairs_jsonl_format(builder: FineTuneExportBuilder) -> None:
    record = FineTuneExampleRecord(
        training_example_id="ex_1",
        user_text="How are you?",
        assistant_text="I am doing well.",
        stage="opening",
        objection_type="none",
        source_id="src_1",
        content_hash="h1",
        redactions={},
        validation={}
    )
    config = FineTuneExportConfig(export_name="test", format="generic_pairs_jsonl")
    serialized = builder.serialize_record(record, config)
    assert serialized["input"] == "How are you?"
    assert serialized["output"] == "I am doing well."
    assert serialized["metadata"]["training_example_id"] == "ex_1"


# 15. test_export_writes_train_validation_manifest_report
@pytest.mark.asyncio
async def test_export_writes_train_validation_manifest_report(repo: Repository, builder: FineTuneExportBuilder, tmp_path: Path) -> None:
    for i in range(10):
        await save_valid_approved_example(repo, id=f"ex-{i}", user_text=f"User {i}", ideal_response=f"Assistant {i}")
        
    config = FineTuneExportConfig(
        export_name="dana_test_export",
        min_examples=10,
        output_dir=str(tmp_path)
    )
    res = await builder.build_export(config)
    assert res.train_path is not None
    assert res.validation_path is not None
    assert res.manifest_path is not None
    assert res.report_json_path is not None
    assert res.report_markdown_path is not None
    
    assert Path(res.train_path).exists()
    assert Path(res.validation_path).exists()
    assert Path(res.manifest_path).exists()
    assert Path(res.report_json_path).exists()
    assert Path(res.report_markdown_path).exists()


# 16. test_report_contains_required_sections
@pytest.mark.asyncio
async def test_report_contains_required_sections(repo: Repository, builder: FineTuneExportBuilder, tmp_path: Path) -> None:
    for i in range(10):
        await save_valid_approved_example(repo, id=f"ex-{i}", user_text=f"User {i}", ideal_response=f"Assistant {i}")
        
    config = FineTuneExportConfig(
        export_name="dana_report_test",
        min_examples=10,
        output_dir=str(tmp_path)
    )
    res = await builder.build_export(config)
    
    md_content = Path(res.report_markdown_path).read_text(encoding="utf-8")
    assert "Executive Summary" in md_content
    assert "Safety Summary" in md_content
    assert "Dataset Distribution" in md_content
    assert "Required Next Steps" in md_content


# 17. test_not_enough_examples_does_not_write_dataset
@pytest.mark.asyncio
async def test_not_enough_examples_does_not_write_dataset(repo: Repository, builder: FineTuneExportBuilder, tmp_path: Path) -> None:
    await save_valid_approved_example(repo, id="ex-1")
    
    config = FineTuneExportConfig(
        export_name="too_few",
        min_examples=10,
        output_dir=str(tmp_path)
    )
    res = await builder.build_export(config)
    assert res.train_path is None
    assert res.validation_path is None
    assert any("below min_examples" in w for w in res.warnings)


# 18. test_dry_run_does_not_write_dataset_files
@pytest.mark.asyncio
async def test_dry_run_does_not_write_dataset_files(repo: Repository, builder: FineTuneExportBuilder, tmp_path: Path) -> None:
    for i in range(10):
        await save_valid_approved_example(repo, id=f"ex-{i}")
        
    exports_dir = tmp_path / "exports"
    config = FineTuneExportConfig(
        export_name="dry_run_export",
        min_examples=5,
        output_dir=str(exports_dir),
        dry_run=True
    )
    res = await builder.build_export(config)
    assert res.train_path is None
    assert res.validation_path is None
    assert not exports_dir.exists() or len(list(exports_dir.iterdir())) == 0


# 19. test_cli_export_outputs_json
def test_cli_export_outputs_json() -> None:
    cmd = [
        sys.executable,
        "scripts/export_fine_tune_dataset.py",
        "--export-name", "cli_test",
        "--min-examples", "1",
        "--dry-run"
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 0
    data = json.loads(res.stdout.strip())
    assert data["export_name"] == "cli_test"
    assert data["dry_run"] is True


# 20. test_cli_not_enough_examples_exits_1
def test_cli_not_enough_examples_exits_1() -> None:
    cmd = [
        sys.executable,
        "scripts/export_fine_tune_dataset.py",
        "--export-name", "cli_test_fail",
        "--min-examples", "100"
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 1
    err = json.loads(res.stderr.strip())
    assert err["status"] == "error"
    assert "Not enough" in err["message"]


# 21. test_no_live_prompt_file_modified
@pytest.mark.asyncio
async def test_no_live_prompt_file_modified(repo: Repository, builder: FineTuneExportBuilder, tmp_path: Path) -> None:
    prompt_path = Path("prompts/final_expense_alex.md")
    content_before = ""
    if prompt_path.exists():
        content_before = prompt_path.read_text(encoding="utf-8")
        
    for i in range(10):
        await save_valid_approved_example(repo, id=f"ex-{i}")
        
    config = FineTuneExportConfig(
        export_name="test_live_prompt",
        min_examples=5,
        output_dir=str(tmp_path)
    )
    await builder.build_export(config)
    
    if prompt_path.exists():
        content_after = prompt_path.read_text(encoding="utf-8")
        assert content_before == content_after


# 22. test_no_external_api_or_fine_tune_call
def test_no_external_api_or_fine_tune_call() -> None:
    with open("training/fine_tune_export.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "import openai" not in content
    assert "openai." not in content
    assert "httpx" not in content


# 23. test_stage_and_objection_filters
@pytest.mark.asyncio
async def test_stage_and_objection_filters(repo: Repository, builder: FineTuneExportBuilder) -> None:
    await save_valid_approved_example(repo, id="ex-opening", stage="opening")
    await save_valid_approved_example(repo, id="ex-objection", stage="objection", labels={"fine_tune_eligible": True, "objection_type": "price"})
    
    config = FineTuneExportConfig(
        export_name="stage_filter",
        min_examples=1,
        include_stages=["opening"],
        dry_run=True
    )
    res = await builder.build_export(config)
    assert res.eligible_examples == 1
    
    config_obj = FineTuneExportConfig(
        export_name="objection_filter",
        min_examples=1,
        exclude_objection_types=["price"],
        dry_run=True
    )
    res_obj = await builder.build_export(config_obj)
    assert res_obj.eligible_examples == 1


# 24. test_skips_medium_high_critical_compliance_risk
@pytest.mark.asyncio
async def test_skips_medium_high_critical_compliance_risk(repo: Repository, builder: FineTuneExportBuilder) -> None:
    await save_valid_approved_example(repo, id="ex-critical-risk", labels={"fine_tune_eligible": True, "compliance_risk": "critical"})
    await save_valid_approved_example(repo, id="ex-high-risk", labels={"fine_tune_eligible": True, "compliance_risk": "high"})
    await save_valid_approved_example(repo, id="ex-med-risk", labels={"fine_tune_eligible": True, "compliance_risk": "medium"})
    await save_valid_approved_example(repo, id="ex-low-risk", labels={"fine_tune_eligible": True, "compliance_risk": "low"})
    await save_valid_approved_example(repo, id="ex-no-risk", labels={"fine_tune_eligible": True, "compliance_risk": "none"})
    
    config = FineTuneExportConfig(
        export_name="risk_test",
        min_examples=1,
        dry_run=True
    )
    res = await builder.build_export(config)
    assert res.eligible_examples == 2


# 25. test_dnc_or_wrong_number_selling_example_rejected
@pytest.mark.asyncio
async def test_dnc_or_wrong_number_selling_example_rejected(repo: Repository, builder: FineTuneExportBuilder) -> None:
    await save_valid_approved_example(
        repo,
        id="ex-dnc-compliant",
        user_text="Stop calling my number.",
        ideal_response="I apologize for the inconvenience. I will update our records right away."
    )
    await save_valid_approved_example(
        repo,
        id="ex-dnc-selling",
        user_text="Stop calling my number.",
        ideal_response="I apologize, but we offer really cheap final expense coverage. How old are you?"
    )
    
    config = FineTuneExportConfig(
        export_name="dnc_test",
        min_examples=1,
        dry_run=True
    )
    res = await builder.build_export(config)
    assert res.eligible_examples == 1
