"""Tests for fine-tuning dataset safety gating system (Prompt 19)."""

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
from training.fine_tune_gate import (
    FineTuneDatasetGate,
    FineTuneDatasetGateConfig,
    FineTuneRecordCheck,
    FineTuneDatasetMetrics,
    FineTuneDatasetGateResult,
    FineTuneApprovalPackage,
)

@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    """Return a Repository backed by a temporary JSONL store."""
    return Repository(data_dir=tmp_path)


@pytest.fixture
def gate(repo: Repository) -> FineTuneDatasetGate:
    """Return a FineTuneDatasetGate linked to the test Repository."""
    return FineTuneDatasetGate(repository=repo)


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def make_safe_openai_chat_record(index: int) -> dict:
    return {
        "messages": [
            {"role": "system", "content": "You are Dana, a compliant voice assistant."},
            {"role": "user", "content": f"Hello, who is this? Index {index}"},
            {"role": "assistant", "content": "I am an automated assistant. A licensed agent can review plan details with you."}
        ],
        "metadata": {
            "training_example_id": f"tx_{index}",
            "stage": "opening",
            "objection_type": "none",
            "source_id": "src_123",
            "labels": {
                "compliance_risk": "none",
                "fine_tune_eligible": True
            }
        }
    }


def make_safe_generic_pairs_record(index: int) -> dict:
    return {
        "input": f"Hello, who is this? Index {index}",
        "output": "I am an automated assistant. A licensed agent can review plan details with you.",
        "metadata": {
            "training_example_id": f"tx_{index}",
            "stage": "opening",
            "objection_type": "none",
            "source_id": "src_123",
            "labels": {
                "compliance_risk": "none",
                "fine_tune_eligible": True
            }
        }
    }


# 1. test_gate_passes_safe_openai_chat_jsonl_dataset
@pytest.mark.asyncio
async def test_gate_passes_safe_openai_chat_jsonl_dataset(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    train_records = [make_safe_openai_chat_record(i) for i in range(10)]
    val_records = [make_safe_openai_chat_record(i + 10) for i in range(2)]
    
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, train_records)
    write_jsonl(val_path, val_records)
    
    config = FineTuneDatasetGateConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "approvals"),
        min_train_examples=10,
        min_validation_examples=1
    )
    res = await gate.run_gate(config)
    assert res.passed is True
    assert len(res.critical_failures) == 0
    assert len(res.high_failures) == 0


# 2. test_gate_passes_safe_generic_pairs_dataset
@pytest.mark.asyncio
async def test_gate_passes_safe_generic_pairs_dataset(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    train_records = [make_safe_generic_pairs_record(i) for i in range(10)]
    val_records = [make_safe_generic_pairs_record(i + 10) for i in range(2)]
    
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, train_records)
    write_jsonl(val_path, val_records)
    
    config = FineTuneDatasetGateConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "approvals"),
        min_train_examples=10,
        min_validation_examples=1,
        expected_format="generic_pairs_jsonl"
    )
    res = await gate.run_gate(config)
    assert res.passed is True
    assert res.format == "generic_pairs_jsonl"


# 3. test_gate_fails_missing_train_file
@pytest.mark.asyncio
async def test_gate_fails_missing_train_file(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    val_path = tmp_path / "val.jsonl"
    write_jsonl(val_path, [make_safe_openai_chat_record(0)])
    
    config = FineTuneDatasetGateConfig(
        train_path=str(tmp_path / "does_not_exist.jsonl"),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "approvals")
    )
    res = await gate.run_gate(config)
    assert res.passed is False
    assert any("does not exist" in f.lower() for f in res.critical_failures)


# 4. test_gate_fails_invalid_jsonl
@pytest.mark.asyncio
async def test_gate_fails_invalid_jsonl(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(val_path, [make_safe_openai_chat_record(0)])
    
    train_path.parent.mkdir(parents=True, exist_ok=True)
    with open(train_path, "w", encoding="utf-8") as f:
        f.write('{"messages": []}\n')
        f.write('invalid json line here\n')
        
    config = FineTuneDatasetGateConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "approvals")
    )
    res = await gate.run_gate(config)
    assert res.passed is False
    assert any("failed to parse train jsonl" in f.lower() for f in res.critical_failures)


# 5. test_gate_fails_you_qualify
@pytest.mark.asyncio
async def test_gate_fails_you_qualify(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    rec = make_safe_openai_chat_record(0)
    rec["messages"][2]["content"] = "Yes, you qualify for this plan."
    
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [rec] * 10)
    write_jsonl(val_path, [make_safe_openai_chat_record(1)])
    
    config = FineTuneDatasetGateConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "approvals"),
        max_duplicate_rate=1.0
    )
    res = await gate.run_gate(config)
    assert res.passed is False
    assert any("you qualify" in f.lower() for f in res.critical_failures)


# 6. test_gate_fails_price_quote
@pytest.mark.asyncio
async def test_gate_fails_price_quote(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    rec = make_safe_openai_chat_record(0)
    rec["messages"][2]["content"] = "It costs exactly $29.99 per month."
    
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [rec] * 10)
    write_jsonl(val_path, [make_safe_openai_chat_record(1)])
    
    config = FineTuneDatasetGateConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "approvals"),
        max_duplicate_rate=1.0
    )
    res = await gate.run_gate(config)
    assert res.passed is False
    assert any("price" in f.lower() for f in res.critical_failures)


# 7. test_gate_fails_licensed_self_claim
@pytest.mark.asyncio
async def test_gate_fails_licensed_self_claim(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    rec = make_safe_openai_chat_record(0)
    rec["messages"][2]["content"] = "I am licensed to sell insurance."
    
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [rec] * 10)
    write_jsonl(val_path, [make_safe_openai_chat_record(1)])
    
    config = FineTuneDatasetGateConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "approvals"),
        max_duplicate_rate=1.0
    )
    res = await gate.run_gate(config)
    assert res.passed is False
    assert any("licensed" in f.lower() for f in res.critical_failures)


# 8. test_gate_allows_safe_licensed_agent_reference
@pytest.mark.asyncio
async def test_gate_allows_safe_licensed_agent_reference(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    rec = make_safe_openai_chat_record(0)
    rec["messages"][2]["content"] = "A licensed agent can review plan details with you."
    
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [rec] * 10)
    write_jsonl(val_path, [make_safe_openai_chat_record(1)])
    
    config = FineTuneDatasetGateConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "approvals"),
        max_duplicate_rate=1.0
    )
    res = await gate.run_gate(config)
    assert res.passed is True


# 9. test_gate_fails_human_claim
@pytest.mark.asyncio
async def test_gate_fails_human_claim(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    rec = make_safe_openai_chat_record(0)
    rec["messages"][2]["content"] = "No, I'm a real person."
    
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [rec] * 10)
    write_jsonl(val_path, [make_safe_openai_chat_record(1)])
    
    config = FineTuneDatasetGateConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "approvals"),
        max_duplicate_rate=1.0
    )
    res = await gate.run_gate(config)
    assert res.passed is False
    assert any("forbidden" in f.lower() or "human" in f.lower() or "person" in f.lower() for f in res.critical_failures)


# 10. test_gate_fails_unredacted_email_or_phone
@pytest.mark.asyncio
async def test_gate_fails_unredacted_email_or_phone(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    rec = make_safe_openai_chat_record(0)
    rec["messages"][1]["content"] = "Reach me at test@example.com or 123-456-7890."
    
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [rec] * 10)
    write_jsonl(val_path, [make_safe_openai_chat_record(1)])
    
    config = FineTuneDatasetGateConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "approvals"),
        max_duplicate_rate=1.0
    )
    res = await gate.run_gate(config)
    assert res.passed is False
    assert any("pii in user text" in f.lower() for f in res.critical_failures)


# 11. test_gate_allows_redaction_tokens
@pytest.mark.asyncio
async def test_gate_allows_redaction_tokens(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    rec = make_safe_openai_chat_record(0)
    rec["messages"][1]["content"] = "Reach me at [REDACTED_EMAIL] or [REDACTED_PHONE]."
    
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [rec] * 10)
    write_jsonl(val_path, [make_safe_openai_chat_record(1)])
    
    config = FineTuneDatasetGateConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "approvals"),
        max_duplicate_rate=1.0
    )
    res = await gate.run_gate(config)
    assert res.passed is True


# 12. test_gate_fails_dnc_selling
@pytest.mark.asyncio
async def test_gate_fails_dnc_selling(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    rec = make_safe_openai_chat_record(0)
    rec["messages"][1]["content"] = "Stop calling me, remove my number."
    rec["messages"][2]["content"] = "I understand, but we have really good senior final expense programs."
    
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [rec] * 10)
    write_jsonl(val_path, [make_safe_openai_chat_record(1)])
    
    config = FineTuneDatasetGateConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "approvals"),
        max_duplicate_rate=1.0
    )
    res = await gate.run_gate(config)
    assert res.passed is False
    assert any("dnc" in f.lower() and "continued selling" in f.lower() for f in res.critical_failures)


# 13. test_gate_fails_wrong_number_selling
@pytest.mark.asyncio
async def test_gate_fails_wrong_number_selling(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    rec = make_safe_openai_chat_record(0)
    rec["messages"][1]["content"] = "This is the wrong person, not me."
    rec["messages"][2]["content"] = "Okay, but let me check if you qualify for coverage anyway."
    
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [rec] * 10)
    write_jsonl(val_path, [make_safe_openai_chat_record(1)])
    
    config = FineTuneDatasetGateConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "approvals"),
        max_duplicate_rate=1.0
    )
    res = await gate.run_gate(config)
    assert res.passed is False
    assert any("wrong number" in f.lower() and "continued selling" in f.lower() for f in res.critical_failures)


# 14. test_gate_fails_transfer_without_consent
@pytest.mark.asyncio
async def test_gate_fails_transfer_without_consent(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    rec = make_safe_openai_chat_record(0)
    rec["messages"][2]["content"] = "I am connecting you now."
    rec["metadata"]["labels"]["transfer_consent"] = False
    
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [rec] * 10)
    write_jsonl(val_path, [make_safe_openai_chat_record(1)])
    
    config = FineTuneDatasetGateConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "approvals"),
        max_duplicate_rate=1.0
    )
    res = await gate.run_gate(config)
    assert res.passed is False
    assert any("transferring without explicit consent" in f.lower() for f in res.critical_failures)


# 15. test_gate_fails_excessive_questions
@pytest.mark.asyncio
async def test_gate_fails_excessive_questions(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    rec = make_safe_openai_chat_record(0)
    rec["messages"][2]["content"] = "Are you over 50? And do you live in Florida?"
    
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [rec] * 10)
    write_jsonl(val_path, [make_safe_openai_chat_record(1)])
    
    config = FineTuneDatasetGateConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "approvals"),
        max_duplicate_rate=1.0
    )
    res = await gate.run_gate(config)
    assert res.passed is False
    assert any("has 2 questions" in f.lower() for f in res.high_failures)


# 16. test_gate_warns_soft_word_limit
@pytest.mark.asyncio
async def test_gate_warns_soft_word_limit(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    rec = make_safe_openai_chat_record(0)
    rec["messages"][2]["content"] = " ".join(["hello"] * 50) + "?"
    
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [rec] * 10)
    write_jsonl(val_path, [make_safe_openai_chat_record(1)])
    
    config = FineTuneDatasetGateConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "approvals"),
        max_duplicate_rate=1.0
    )
    res = await gate.run_gate(config)
    assert res.passed is True
    assert any("exceeds soft limit" in w.lower() for w in res.medium_warnings)


# 17. test_dataset_level_counts_enforced
@pytest.mark.asyncio
async def test_dataset_level_counts_enforced(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    train_records = [make_safe_openai_chat_record(i) for i in range(2)]
    val_records = [make_safe_openai_chat_record(i + 10) for i in range(0)]
    
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, train_records)
    write_jsonl(val_path, val_records)
    
    config = FineTuneDatasetGateConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "approvals"),
        min_train_examples=10,
        min_validation_examples=1
    )
    res = await gate.run_gate(config)
    assert res.passed is False
    assert any("below minimum limit" in f.lower() for f in res.high_failures)


# 18. test_duplicate_rate_failure
@pytest.mark.asyncio
async def test_duplicate_rate_failure(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    rec = make_safe_openai_chat_record(0)
    train_records = [rec] * 10
    val_records = [make_safe_openai_chat_record(1), make_safe_openai_chat_record(2)]
    
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, train_records)
    write_jsonl(val_path, val_records)
    
    config = FineTuneDatasetGateConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "approvals"),
        max_duplicate_rate=0.01
    )
    res = await gate.run_gate(config)
    assert res.passed is False
    assert any("duplicate rate" in f.lower() for f in res.high_failures)


# 19. test_train_validation_contamination_fails
@pytest.mark.asyncio
async def test_train_validation_contamination_fails(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    rec = make_safe_openai_chat_record(0)
    train_records = [rec] * 10
    val_records = [rec]
    
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, train_records)
    write_jsonl(val_path, val_records)
    
    config = FineTuneDatasetGateConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "approvals"),
        max_duplicate_rate=0.99
    )
    res = await gate.run_gate(config)
    assert res.passed is False
    assert any("contamination" in f.lower() for f in res.high_failures)


# 20. test_manifest_count_mismatch_fails
@pytest.mark.asyncio
async def test_manifest_count_mismatch_fails(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    train_records = [make_safe_openai_chat_record(i) for i in range(10)]
    val_records = [make_safe_openai_chat_record(i + 10) for i in range(2)]
    
    train_path = tmp_path / "train_man.jsonl"
    val_path = tmp_path / "val_man.jsonl"
    write_jsonl(train_path, train_records)
    write_jsonl(val_path, val_records)
    
    manifest_data = {
        "export_name": "test_mismatch",
        "train_path": "train_man.jsonl",
        "validation_path": "val_man.jsonl",
        "train_count": 100,  # mismatch
        "validation_count": 2,
        "compliance_statement": "Dataset generated from human-approved, compliance-validated examples only. No upload or fine-tuning job was started."
    }
    
    manifest_path = tmp_path / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f)
        
    config = FineTuneDatasetGateConfig(
        manifest_path=str(manifest_path),
        output_dir=str(tmp_path / "approvals")
    )
    res = await gate.run_gate(config)
    assert res.passed is False
    assert any("does not match actual" in f.lower() for f in res.high_failures)


# 21. test_manifest_compliance_statement_required_when_manifest_required
@pytest.mark.asyncio
async def test_manifest_compliance_statement_required_when_manifest_required(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    train_records = [make_safe_openai_chat_record(i) for i in range(10)]
    val_records = [make_safe_openai_chat_record(i + 10) for i in range(2)]
    
    train_path = tmp_path / "train_man.jsonl"
    val_path = tmp_path / "val_man.jsonl"
    write_jsonl(train_path, train_records)
    write_jsonl(val_path, val_records)
    
    manifest_data = {
        "export_name": "test_mismatch",
        "train_path": "train_man.jsonl",
        "validation_path": "val_man.jsonl",
        "train_count": 10,
        "validation_count": 2,
        # missing compliance statement
    }
    
    manifest_path = tmp_path / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f)
        
    config = FineTuneDatasetGateConfig(
        manifest_path=str(manifest_path),
        output_dir=str(tmp_path / "approvals"),
        require_manifest=True
    )
    res = await gate.run_gate(config)
    assert res.passed is False
    assert any("compliance statement is missing" in f.lower() for f in res.critical_failures)


# 22. test_approval_package_files_written_on_pass
@pytest.mark.asyncio
async def test_approval_package_files_written_on_pass(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    train_records = [make_safe_openai_chat_record(i) for i in range(10)]
    val_records = [make_safe_openai_chat_record(i + 10) for i in range(2)]
    
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, train_records)
    write_jsonl(val_path, val_records)
    
    config = FineTuneDatasetGateConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "approvals"),
        min_train_examples=10,
        min_validation_examples=1
    )
    res = await gate.run_gate(config)
    assert res.passed is True
    
    assert res.approval_package_json_path is not None
    assert res.approval_package_markdown_path is not None
    assert res.approval_checklist_path is not None
    
    assert Path(res.approval_package_json_path).exists()
    assert Path(res.approval_package_markdown_path).exists()
    assert Path(res.approval_checklist_path).exists()


# 23. test_approval_package_files_written_on_failure
@pytest.mark.asyncio
async def test_approval_package_files_written_on_failure(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    # Unsafe record
    rec = make_safe_openai_chat_record(0)
    rec["messages"][2]["content"] = "Yes, you qualify."
    
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [rec] * 10)
    write_jsonl(val_path, [make_safe_openai_chat_record(1)])
    
    config = FineTuneDatasetGateConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "approvals")
    )
    res = await gate.run_gate(config)
    assert res.passed is False
    
    # Package files should still exist
    assert res.approval_package_json_path is not None
    assert Path(res.approval_package_json_path).exists()


# 24. test_create_review_item_pending_only
@pytest.mark.asyncio
async def test_create_review_item_pending_only(tmp_path: Path, gate: FineTuneDatasetGate, repo: Repository) -> None:
    train_records = [make_safe_openai_chat_record(i) for i in range(10)]
    val_records = [make_safe_openai_chat_record(i + 10) for i in range(2)]
    
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, train_records)
    write_jsonl(val_path, val_records)
    
    config = FineTuneDatasetGateConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "approvals"),
        min_train_examples=10,
        min_validation_examples=1,
        create_review_item=True
    )
    res = await gate.run_gate(config)
    assert res.review_item_id is not None
    
    item = await repo.get_human_review_item(res.review_item_id)
    assert item is not None
    assert item["item_type"] == "fine_tune_dataset_approval"
    assert item["status"] == "pending"
    
    payload = item["payload"]
    assert payload["fine_tune_upload_allowed"] is False
    assert payload["fine_tune_job_started"] is False


# 25. test_cli_gate_manifest_outputs_json
def test_cli_gate_manifest_outputs_json(tmp_path: Path) -> None:
    train_records = [make_safe_openai_chat_record(i) for i in range(10)]
    val_records = [make_safe_openai_chat_record(i + 10) for i in range(2)]
    
    train_path = tmp_path / "train_cli.jsonl"
    val_path = tmp_path / "val_cli.jsonl"
    write_jsonl(train_path, train_records)
    write_jsonl(val_path, val_records)
    
    manifest_data = {
        "export_name": "test_cli",
        "train_path": "train_cli.jsonl",
        "validation_path": "val_cli.jsonl",
        "train_count": 10,
        "validation_count": 2,
        "compliance_statement": "Dataset generated from human-approved, compliance-validated examples only. No upload or fine-tuning job was started."
    }
    
    manifest_path = tmp_path / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f)
        
    cmd = [
        sys.executable,
        "scripts/gate_fine_tune_dataset.py",
        "--manifest", str(manifest_path),
        "--output-dir", str(tmp_path / "approvals")
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 0
    data = json.loads(res.stdout.strip())
    assert data["passed"] is True
    assert data["gate_id"] is not None


# 26. test_cli_gate_failing_dataset_exits_1
def test_cli_gate_failing_dataset_exits_1(tmp_path: Path) -> None:
    # Unsafe record
    rec = make_safe_openai_chat_record(0)
    rec["messages"][2]["content"] = "Yes, you qualify."
    
    train_path = tmp_path / "train_cli_fail.jsonl"
    val_path = tmp_path / "val_cli_fail.jsonl"
    write_jsonl(train_path, [rec] * 10)
    write_jsonl(val_path, [make_safe_openai_chat_record(1)])
    
    cmd = [
        sys.executable,
        "scripts/gate_fine_tune_dataset.py",
        "--train", str(train_path),
        "--validation", str(val_path),
        "--output-dir", str(tmp_path / "approvals")
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 1
    data = json.loads(res.stdout.strip())
    assert data["passed"] is False


# 27. test_cli_requires_input
def test_cli_requires_input() -> None:
    cmd = [
        sys.executable,
        "scripts/gate_fine_tune_dataset.py"
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 1
    err = json.loads(res.stderr.strip())
    assert err["status"] == "error"
    assert "must provide" in err["message"].lower()


# 28. test_no_live_prompt_file_modified
@pytest.mark.asyncio
async def test_no_live_prompt_file_modified(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    prompt_path = Path("prompts/final_expense_alex.md")
    content_before = ""
    if prompt_path.exists():
        content_before = prompt_path.read_text(encoding="utf-8")
        
    train_records = [make_safe_openai_chat_record(i) for i in range(10)]
    val_records = [make_safe_openai_chat_record(i + 10) for i in range(2)]
    
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, train_records)
    write_jsonl(val_path, val_records)
    
    config = FineTuneDatasetGateConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "approvals"),
        min_train_examples=5,
        min_validation_examples=1
    )
    await gate.run_gate(config)
    
    if prompt_path.exists():
        content_after = prompt_path.read_text(encoding="utf-8")
        assert content_before == content_after


# 29. test_no_external_api_or_fine_tune_call
def test_no_external_api_or_fine_tune_call() -> None:
    with open("training/fine_tune_gate.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "import openai" not in content
    assert "openai." not in content
    assert "httpx" not in content


# 30. test_medium_warnings_can_fail_when_configured
@pytest.mark.asyncio
async def test_medium_warnings_can_fail_when_configured(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    rec = make_safe_openai_chat_record(0)
    rec["messages"][2]["content"] = " ".join(["hello"] * 50) + "?"  # triggers medium warning soft limit
    
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [rec] * 10)
    write_jsonl(val_path, [make_safe_openai_chat_record(1)])
    
    config = FineTuneDatasetGateConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "approvals"),
        fail_on_medium_warnings=True,
        max_duplicate_rate=1.0
    )
    res = await gate.run_gate(config)
    assert res.passed is False
    assert len(res.medium_warnings) > 0


# 31. test_metadata_coverage_metrics
@pytest.mark.asyncio
async def test_metadata_coverage_metrics(tmp_path: Path, gate: FineTuneDatasetGate) -> None:
    train_records = [make_safe_openai_chat_record(i) for i in range(10)]
    val_records = [make_safe_openai_chat_record(i + 10) for i in range(2)]
    
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, train_records)
    write_jsonl(val_path, val_records)
    
    config = FineTuneDatasetGateConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "approvals")
    )
    res = await gate.run_gate(config)
    assert res.passed is True
    
    metrics = res.metrics
    assert metrics.metadata_coverage["training_example_id"] == 1.0
    assert metrics.metadata_coverage["stage"] == 1.0
    assert metrics.metadata_coverage["objection_type"] == 1.0
    assert metrics.metadata_coverage["source_id"] == 1.0
    assert metrics.metadata_coverage["labels"] == 1.0
