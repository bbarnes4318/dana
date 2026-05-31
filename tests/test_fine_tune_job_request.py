"""Tests for fine-tuning job request builder & upload gate (Prompt 20)."""

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
from training.fine_tune_job_request import (
    FineTuneJobRequestConfig,
    FineTuneJobRequestValidationResult,
    FineTuneJobRequestPackage,
    FineTuneJobRequestResult,
    FineTuneJobRequestBuilder,
)

@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    """Return a Repository backed by a temporary JSONL store."""
    return Repository(data_dir=tmp_path)


@pytest.fixture
def builder(repo: Repository) -> FineTuneJobRequestBuilder:
    """Return a FineTuneJobRequestBuilder linked to the test Repository."""
    return FineTuneJobRequestBuilder(repository=repo)


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


def compute_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

import hashlib


# 1. test_builds_package_from_approved_dataset_review_item
@pytest.mark.asyncio
async def test_builds_package_from_approved_dataset_review_item(tmp_path: Path, builder: FineTuneJobRequestBuilder, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    payload = {
        "passed": True,
        "human_review_required": True,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "file_hashes": {
            "train": t_hash,
            "validation": v_hash
        },
        "fine_tune_upload_allowed": False,
        "fine_tune_job_started": False,
        "deployment_allowed": False,
        "approved_by": "Braden",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "review_history": [
            {
                "action": "approved",
                "reviewer": "Braden",
                "reviewed_at": datetime.now(timezone.utc).isoformat()
            }
        ]
    }
    
    review_item_id = await repo.save_human_review_item(
        item_type="fine_tune_dataset_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobRequestConfig(
        review_item_id=review_item_id,
        output_dir=str(tmp_path / "requests")
    )
    
    res = await builder.build_request_package(config)
    assert res.passed is True
    assert res.upload_ready is True
    assert res.package_json_path is not None
    assert Path(res.package_json_path).exists()


# 2. test_rejects_pending_dataset_review_item
@pytest.mark.asyncio
async def test_rejects_pending_dataset_review_item(tmp_path: Path, builder: FineTuneJobRequestBuilder, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    payload = {
        "passed": True,
        "human_review_required": True,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "file_hashes": {
            "train": t_hash,
            "validation": v_hash
        }
    }
    
    review_item_id = await repo.save_human_review_item(
        item_type="fine_tune_dataset_approval",
        status="pending",
        payload=payload
    )
    
    config = FineTuneJobRequestConfig(
        review_item_id=review_item_id,
        output_dir=str(tmp_path / "requests")
    )
    
    res = await builder.build_request_package(config)
    assert res.passed is False
    assert res.upload_ready is False
    assert any("human review item validation failed" in f.lower() for f in res.validation_result.critical_failures)


# 3. test_rejects_rejected_dataset_review_item
@pytest.mark.asyncio
async def test_rejects_rejected_dataset_review_item(tmp_path: Path, builder: FineTuneJobRequestBuilder, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    payload = {
        "passed": True,
        "human_review_required": True,
        "train_path": str(train_path),
        "validation_path": str(val_path)
    }
    
    review_item_id = await repo.save_human_review_item(
        item_type="fine_tune_dataset_approval",
        status="rejected",
        payload=payload
    )
    
    config = FineTuneJobRequestConfig(
        review_item_id=review_item_id,
        output_dir=str(tmp_path / "requests")
    )
    
    res = await builder.build_request_package(config)
    assert res.passed is False
    assert res.upload_ready is False


# 4. test_rejects_failed_dataset_gate
@pytest.mark.asyncio
async def test_rejects_failed_dataset_gate(tmp_path: Path, builder: FineTuneJobRequestBuilder, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    payload = {
        "passed": False,  # gate failed!
        "human_review_required": True,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "file_hashes": {
            "train": t_hash,
            "validation": v_hash
        },
        "approved_by": "Braden",
        "approved_at": datetime.now(timezone.utc).isoformat()
    }
    
    review_item_id = await repo.save_human_review_item(
        item_type="fine_tune_dataset_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobRequestConfig(
        review_item_id=review_item_id,
        output_dir=str(tmp_path / "requests")
    )
    
    res = await builder.build_request_package(config)
    assert res.passed is False
    assert res.upload_ready is False
    assert any("dataset gate did not pass" in f.lower() for f in res.validation_result.critical_failures)


# 5. test_requires_human_approval_by_default
@pytest.mark.asyncio
async def test_requires_human_approval_by_default(tmp_path: Path, builder: FineTuneJobRequestBuilder) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    config = FineTuneJobRequestConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        output_dir=str(tmp_path / "requests")
    )
    
    res = await builder.build_request_package(config)
    assert res.passed is False
    assert res.upload_ready is False
    assert any("review item id is required" in f.lower() for f in res.validation_result.critical_failures)


# 6. test_dry_run_direct_files_allowed_but_not_upload_ready
@pytest.mark.asyncio
async def test_dry_run_direct_files_allowed_but_not_upload_ready(tmp_path: Path, builder: FineTuneJobRequestBuilder) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    config = FineTuneJobRequestConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        require_human_approval=False,
        dry_run=True,
        output_dir=str(tmp_path / "requests")
    )
    
    res = await builder.build_request_package(config)
    assert res.passed is True
    assert res.upload_ready is False
    assert any("dry run only" in w.lower() for w in res.validation_result.medium_warnings)


# 7. test_file_hash_match_required
@pytest.mark.asyncio
async def test_file_hash_match_required(tmp_path: Path, builder: FineTuneJobRequestBuilder, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    v_hash = compute_hash(val_path)
    
    payload = {
        "passed": True,
        "human_review_required": True,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "file_hashes": {
            "train": "mismatched_hash_value_here",
            "validation": v_hash
        },
        "approved_by": "Braden",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "review_history": [{"action": "approved", "reviewer": "Braden", "reviewed_at": datetime.now(timezone.utc).isoformat()}]
    }
    
    review_item_id = await repo.save_human_review_item(
        item_type="fine_tune_dataset_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobRequestConfig(
        review_item_id=review_item_id,
        output_dir=str(tmp_path / "requests")
    )
    
    res = await builder.build_request_package(config)
    assert res.passed is False
    assert res.upload_ready is False
    assert any("hash mismatch" in f.lower() for f in res.validation_result.critical_failures)


# 8. test_missing_hash_warns_or_fails_upload_ready
@pytest.mark.asyncio
async def test_missing_hash_warns_or_fails_upload_ready(tmp_path: Path, builder: FineTuneJobRequestBuilder, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    payload = {
        "passed": True,
        "human_review_required": True,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "file_hashes": {},  # missing expected hashes!
        "approved_by": "Braden",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "review_history": [{"action": "approved", "reviewer": "Braden", "reviewed_at": datetime.now(timezone.utc).isoformat()}]
    }
    
    review_item_id = await repo.save_human_review_item(
        item_type="fine_tune_dataset_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobRequestConfig(
        review_item_id=review_item_id,
        output_dir=str(tmp_path / "requests")
    )
    
    res = await builder.build_request_package(config)
    assert res.passed is False  # high failure makes passed False by default
    assert res.upload_ready is False
    assert any("no expected file hashes available" in f.lower() for f in res.validation_result.high_failures)


# 9. test_rejects_prior_upload_or_job_started
@pytest.mark.asyncio
async def test_rejects_prior_upload_or_job_started(tmp_path: Path, builder: FineTuneJobRequestBuilder, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    payload = {
        "passed": True,
        "human_review_required": True,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "file_hashes": {"train": t_hash, "validation": v_hash},
        "api_upload_performed": True,  # prior upload!
        "approved_by": "Braden",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "review_history": [{"action": "approved", "reviewer": "Braden", "reviewed_at": datetime.now(timezone.utc).isoformat()}]
    }
    
    review_item_id = await repo.save_human_review_item(
        item_type="fine_tune_dataset_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobRequestConfig(
        review_item_id=review_item_id,
        output_dir=str(tmp_path / "requests")
    )
    
    res = await builder.build_request_package(config)
    assert res.passed is False
    assert res.upload_ready is False
    assert any("prior upload detected" in f.lower() for f in res.validation_result.critical_failures)


# 10. test_rejects_deployment_allowed_true
@pytest.mark.asyncio
async def test_rejects_deployment_allowed_true(tmp_path: Path, builder: FineTuneJobRequestBuilder, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    payload = {
        "passed": True,
        "human_review_required": True,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "file_hashes": {"train": t_hash, "validation": v_hash},
        "deployment_allowed": True,  # deployment allowed!
        "approved_by": "Braden",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "review_history": [{"action": "approved", "reviewer": "Braden", "reviewed_at": datetime.now(timezone.utc).isoformat()}]
    }
    
    review_item_id = await repo.save_human_review_item(
        item_type="fine_tune_dataset_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobRequestConfig(
        review_item_id=review_item_id,
        output_dir=str(tmp_path / "requests")
    )
    
    res = await builder.build_request_package(config)
    assert res.passed is False
    assert res.upload_ready is False
    assert any("prior model deployment detected" in f.lower() for f in res.validation_result.critical_failures)


# 11. test_openai_provider_request_shape
@pytest.mark.asyncio
async def test_openai_provider_request_shape(tmp_path: Path, builder: FineTuneJobRequestBuilder, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    payload = {
        "passed": True,
        "human_review_required": True,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "file_hashes": {"train": t_hash, "validation": v_hash},
        "approved_by": "Braden",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "review_history": [{"action": "approved", "reviewer": "Braden", "reviewed_at": datetime.now(timezone.utc).isoformat()}]
    }
    
    review_item_id = await repo.save_human_review_item(
        item_type="fine_tune_dataset_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobRequestConfig(
        review_item_id=review_item_id,
        provider="openai",
        recommended_base_model="gpt-4o-mini",
        output_dir=str(tmp_path / "requests")
    )
    
    res = await builder.build_request_package(config)
    assert res.passed is True
    
    # Load written provider request JSON
    with open(res.provider_request_json_path, "r", encoding="utf-8") as f:
        req = json.load(f)
    assert req["provider"] == "openai"
    assert req["recommended_base_model"] == "gpt-4o-mini"
    assert req["purpose"] == "fine-tune"
    assert req["manual_only"] is True
    assert req["api_upload_performed"] is False


# 12. test_azure_provider_request_shape
@pytest.mark.asyncio
async def test_azure_provider_request_shape(tmp_path: Path, builder: FineTuneJobRequestBuilder, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    payload = {
        "passed": True,
        "human_review_required": True,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "file_hashes": {"train": t_hash, "validation": v_hash},
        "approved_by": "Braden",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "review_history": [{"action": "approved", "reviewer": "Braden", "reviewed_at": datetime.now(timezone.utc).isoformat()}]
    }
    
    review_item_id = await repo.save_human_review_item(
        item_type="fine_tune_dataset_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobRequestConfig(
        review_item_id=review_item_id,
        provider="azure_openai",
        output_dir=str(tmp_path / "requests")
    )
    
    res = await builder.build_request_package(config)
    assert res.passed is True
    
    with open(res.provider_request_json_path, "r", encoding="utf-8") as f:
        req = json.load(f)
    assert req["provider"] == "azure_openai"
    assert "deployment_name_suggestion" in req
    assert req["purpose"] == "fine-tune"


# 13. test_generic_provider_request_shape
@pytest.mark.asyncio
async def test_generic_provider_request_shape(tmp_path: Path, builder: FineTuneJobRequestBuilder, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    payload = {
        "passed": True,
        "human_review_required": True,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "file_hashes": {"train": t_hash, "validation": v_hash},
        "approved_by": "Braden",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "review_history": [{"action": "approved", "reviewer": "Braden", "reviewed_at": datetime.now(timezone.utc).isoformat()}]
    }
    
    review_item_id = await repo.save_human_review_item(
        item_type="fine_tune_dataset_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobRequestConfig(
        review_item_id=review_item_id,
        provider="generic",
        output_dir=str(tmp_path / "requests")
    )
    
    res = await builder.build_request_package(config)
    assert res.passed is True
    
    with open(res.provider_request_json_path, "r", encoding="utf-8") as f:
        req = json.load(f)
    assert req["provider"] == "generic"
    assert "model_family" in req
    assert "purpose" not in req


# 14. test_unknown_provider_fails
@pytest.mark.asyncio
async def test_unknown_provider_fails(tmp_path: Path, builder: FineTuneJobRequestBuilder, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    payload = {
        "passed": True,
        "human_review_required": True,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "file_hashes": {"train": t_hash, "validation": v_hash},
        "approved_by": "Braden",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "review_history": [{"action": "approved", "reviewer": "Braden", "reviewed_at": datetime.now(timezone.utc).isoformat()}]
    }
    
    review_item_id = await repo.save_human_review_item(
        item_type="fine_tune_dataset_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobRequestConfig(
        review_item_id=review_item_id,
        provider="invalid_provider_name",
        output_dir=str(tmp_path / "requests")
    )
    
    res = await builder.build_request_package(config)
    assert res.passed is False
    assert any("unknown provider" in f.lower() for f in res.validation_result.critical_failures)


# 15. test_writes_package_markdown_provider_json_checklist
@pytest.mark.asyncio
async def test_writes_package_markdown_provider_json_checklist(tmp_path: Path, builder: FineTuneJobRequestBuilder, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    payload = {
        "passed": True,
        "human_review_required": True,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "file_hashes": {"train": t_hash, "validation": v_hash},
        "approved_by": "Braden",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "review_history": [{"action": "approved", "reviewer": "Braden", "reviewed_at": datetime.now(timezone.utc).isoformat()}]
    }
    
    review_item_id = await repo.save_human_review_item(
        item_type="fine_tune_dataset_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobRequestConfig(
        review_item_id=review_item_id,
        output_dir=str(tmp_path / "requests")
    )
    
    res = await builder.build_request_package(config)
    assert res.passed is True
    
    assert res.package_json_path is not None
    assert res.package_markdown_path is not None
    assert res.provider_request_json_path is not None
    assert res.human_checklist_path is not None
    
    assert Path(res.package_json_path).exists()
    assert Path(res.package_markdown_path).exists()
    assert Path(res.provider_request_json_path).exists()
    assert Path(res.human_checklist_path).exists()
    
    md_content = Path(res.package_markdown_path).read_text(encoding="utf-8")
    assert "# Dana Fine-Tune Job Request Package" in md_content
    assert "Provider:" in md_content
    assert "Dataset Files" in md_content


# 16. test_create_review_item_pending_only
@pytest.mark.asyncio
async def test_create_review_item_pending_only(tmp_path: Path, builder: FineTuneJobRequestBuilder, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    payload = {
        "passed": True,
        "human_review_required": True,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "file_hashes": {"train": t_hash, "validation": v_hash},
        "approved_by": "Braden",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "review_history": [{"action": "approved", "reviewer": "Braden", "reviewed_at": datetime.now(timezone.utc).isoformat()}]
    }
    
    review_item_id = await repo.save_human_review_item(
        item_type="fine_tune_dataset_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobRequestConfig(
        review_item_id=review_item_id,
        create_review_item=True,
        output_dir=str(tmp_path / "requests")
    )
    
    res = await builder.build_request_package(config)
    assert res.passed is True
    assert res.review_item_id is not None
    
    item = await repo.get_human_review_item(res.review_item_id)
    assert item is not None
    assert item["item_type"] == "fine_tune_job_request"
    assert item["status"] == "pending"
    assert item["payload"]["upload_ready"] is True
    assert item["payload"]["fine_tune_job_started"] is False


# 17. test_cli_build_from_approval_package_outputs_json
def test_cli_build_from_approval_package_outputs_json(tmp_path: Path) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    pkg_data = {
        "gate_id": "gate_123",
        "dataset_name": "test_dataset",
        "passed": True,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "file_hashes": {
            "train": t_hash,
            "validation": v_hash
        }
    }
    
    pkg_path = tmp_path / "approval_package.json"
    with open(pkg_path, "w", encoding="utf-8") as f:
        json.dump(pkg_data, f)
        
    cmd = [
        sys.executable,
        "scripts/prepare_fine_tune_job_request.py",
        "--approval-package", str(pkg_path),
        "--no-require-human-approval",
        "--output-dir", str(tmp_path / "requests")
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 0
    data = json.loads(res.stdout.strip())
    assert data["passed"] is True
    assert data["upload_ready"] is False


# 18. test_cli_failing_hash_exits_1
def test_cli_failing_hash_exits_1(tmp_path: Path) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    pkg_data = {
        "gate_id": "gate_123",
        "dataset_name": "test_dataset",
        "passed": True,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "file_hashes": {
            "train": "mismatched_hash_here",
            "validation": "mismatched_hash_here"
        }
    }
    
    pkg_path = tmp_path / "approval_package.json"
    with open(pkg_path, "w", encoding="utf-8") as f:
        json.dump(pkg_data, f)
        
    cmd = [
        sys.executable,
        "scripts/prepare_fine_tune_job_request.py",
        "--approval-package", str(pkg_path),
        "--no-require-human-approval",
        "--output-dir", str(tmp_path / "requests")
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 1
    data = json.loads(res.stdout.strip())
    assert data["passed"] is False


# 19. test_cli_requires_input
def test_cli_requires_input() -> None:
    cmd = [
        sys.executable,
        "scripts/prepare_fine_tune_job_request.py"
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 1
    err = json.loads(res.stderr.strip())
    assert err["status"] == "error"
    assert "must provide" in err["message"].lower()


# 20. test_no_live_prompt_file_modified
@pytest.mark.asyncio
async def test_no_live_prompt_file_modified(tmp_path: Path, builder: FineTuneJobRequestBuilder, repo: Repository) -> None:
    prompt_path = Path("prompts/final_expense_alex.md")
    content_before = ""
    if prompt_path.exists():
        content_before = prompt_path.read_text(encoding="utf-8")
        
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    payload = {
        "passed": True,
        "human_review_required": True,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "file_hashes": {"train": t_hash, "validation": v_hash},
        "approved_by": "Braden",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "review_history": [{"action": "approved", "reviewer": "Braden", "reviewed_at": datetime.now(timezone.utc).isoformat()}]
    }
    
    review_item_id = await repo.save_human_review_item(
        item_type="fine_tune_dataset_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobRequestConfig(
        review_item_id=review_item_id,
        output_dir=str(tmp_path / "requests")
    )
    
    await builder.build_request_package(config)
    
    if prompt_path.exists():
        content_after = prompt_path.read_text(encoding="utf-8")
        assert content_before == content_after


# 21. test_no_external_api_or_fine_tune_call
def test_no_external_api_or_fine_tune_call() -> None:
    with open("training/fine_tune_job_request.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "import openai" not in content
    assert "openai." not in content
    assert "httpx" not in content


# 22. test_package_says_no_upload_no_job_no_deployment
@pytest.mark.asyncio
async def test_package_says_no_upload_no_job_no_deployment(tmp_path: Path, builder: FineTuneJobRequestBuilder, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    payload = {
        "passed": True,
        "human_review_required": True,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "file_hashes": {"train": t_hash, "validation": v_hash},
        "approved_by": "Braden",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "review_history": [{"action": "approved", "reviewer": "Braden", "reviewed_at": datetime.now(timezone.utc).isoformat()}]
    }
    
    review_item_id = await repo.save_human_review_item(
        item_type="fine_tune_dataset_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobRequestConfig(
        review_item_id=review_item_id,
        output_dir=str(tmp_path / "requests")
    )
    
    res = await builder.build_request_package(config)
    assert res.passed is True
    
    with open(res.package_json_path, "r", encoding="utf-8") as f:
        package = json.load(f)
        
    assert package["api_upload_performed"] is False
    assert package["fine_tune_job_started"] is False
    assert package["deployment_allowed"] is False


# 23. test_manual_upload_instructions_provider_specific
@pytest.mark.asyncio
async def test_manual_upload_instructions_provider_specific(tmp_path: Path, builder: FineTuneJobRequestBuilder, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    payload = {
        "passed": True,
        "human_review_required": True,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "file_hashes": {"train": t_hash, "validation": v_hash},
        "approved_by": "Braden",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "review_history": [{"action": "approved", "reviewer": "Braden", "reviewed_at": datetime.now(timezone.utc).isoformat()}]
    }
    
    review_item_id = await repo.save_human_review_item(
        item_type="fine_tune_dataset_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    # 1. Test openai
    config_openai = FineTuneJobRequestConfig(
        review_item_id=review_item_id,
        provider="openai",
        output_dir=str(tmp_path / "requests_openai")
    )
    res_openai = await builder.build_request_package(config_openai)
    with open(res_openai.package_json_path, "r", encoding="utf-8") as f:
        pkg_openai = json.load(f)
    assert any("OpenAI" in inst for inst in pkg_openai["manual_upload_instructions"])
    
    # 2. Test azure_openai
    config_azure = FineTuneJobRequestConfig(
        review_item_id=review_item_id,
        provider="azure_openai",
        output_dir=str(tmp_path / "requests_azure")
    )
    res_azure = await builder.build_request_package(config_azure)
    with open(res_azure.package_json_path, "r", encoding="utf-8") as f:
        pkg_azure = json.load(f)
    assert any("Azure" in inst for inst in pkg_azure["manual_upload_instructions"])


# 24. test_review_history_approved_action_accepts_approval
@pytest.mark.asyncio
async def test_review_history_approved_action_accepts_approval(tmp_path: Path, builder: FineTuneJobRequestBuilder, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    payload = {
        "passed": True,
        "human_review_required": True,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "file_hashes": {"train": t_hash, "validation": v_hash},
        "approved_by": "Braden",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        # review_history has action approved!
        "review_history": [
            {
                "action": "approved",
                "reviewer": "Braden",
                "reviewed_at": datetime.now(timezone.utc).isoformat()
            }
        ]
    }
    
    review_item_id = await repo.save_human_review_item(
        item_type="fine_tune_dataset_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobRequestConfig(
        review_item_id=review_item_id,
        output_dir=str(tmp_path / "requests")
    )
    res = await builder.build_request_package(config)
    assert res.passed is True
    assert res.upload_ready is True


# 25. test_status_approved_without_reviewer_fails
@pytest.mark.asyncio
async def test_status_approved_without_reviewer_fails(tmp_path: Path, builder: FineTuneJobRequestBuilder, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    payload = {
        "passed": True,
        "human_review_required": True,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "file_hashes": {"train": t_hash, "validation": v_hash},
        # approved_by/approved_at is missing, reviewer is missing, review_history has no action approved!
        "review_history": []
    }
    
    review_item_id = await repo.save_human_review_item(
        item_type="fine_tune_dataset_approval",
        status="approved",
        payload=payload
    )
    
    config = FineTuneJobRequestConfig(
        review_item_id=review_item_id,
        output_dir=str(tmp_path / "requests")
    )
    res = await builder.build_request_package(config)
    assert res.passed is False
    assert res.upload_ready is False


# 26. test_no_require_human_approval_still_not_upload_ready
@pytest.mark.asyncio
async def test_no_require_human_approval_still_not_upload_ready(tmp_path: Path, builder: FineTuneJobRequestBuilder) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    config = FineTuneJobRequestConfig(
        train_path=str(train_path),
        validation_path=str(val_path),
        require_human_approval=False,
        output_dir=str(tmp_path / "requests")
    )
    res = await builder.build_request_package(config)
    assert res.passed is True  # passed is True since human review isn't required and no compliance failure
    assert res.upload_ready is False  # but upload_ready is False because there is no explicit human approval item


# 27. test_approval_package_alone_not_upload_ready
@pytest.mark.asyncio
async def test_approval_package_alone_not_upload_ready(tmp_path: Path, builder: FineTuneJobRequestBuilder) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    pkg_data = {
        "gate_id": "gate_123",
        "dataset_name": "test_dataset",
        "passed": True,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "file_hashes": {
            "train": t_hash,
            "validation": v_hash
        }
    }
    pkg_path = tmp_path / "approval_package.json"
    with open(pkg_path, "w", encoding="utf-8") as f:
        json.dump(pkg_data, f)
        
    config = FineTuneJobRequestConfig(
        approval_package_path=str(pkg_path),
        require_human_approval=False,
        output_dir=str(tmp_path / "requests")
    )
    res = await builder.build_request_package(config)
    assert res.passed is True
    assert res.upload_ready is False


# 28. test_no_require_human_approval_with_approval_package_still_not_upload_ready
@pytest.mark.asyncio
async def test_no_require_human_approval_with_approval_package_still_not_upload_ready(tmp_path: Path, builder: FineTuneJobRequestBuilder) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    pkg_data = {
        "gate_id": "gate_123",
        "dataset_name": "test_dataset",
        "passed": True,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "file_hashes": {
            "train": t_hash,
            "validation": v_hash
        }
    }
    pkg_path = tmp_path / "approval_package.json"
    with open(pkg_path, "w", encoding="utf-8") as f:
        json.dump(pkg_data, f)
        
    config = FineTuneJobRequestConfig(
        approval_package_path=str(pkg_path),
        require_human_approval=False,
        output_dir=str(tmp_path / "requests")
    )
    res = await builder.build_request_package(config)
    assert res.upload_ready is False
    assert any(
        "Approved fine_tune_dataset_approval review item is required for upload_ready=true." in w
        for w in res.validation_result.medium_warnings
    )


# 29. test_upload_ready_requires_approved_dataset_review_item
@pytest.mark.asyncio
async def test_upload_ready_requires_approved_dataset_review_item(tmp_path: Path, builder: FineTuneJobRequestBuilder, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(10)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    payload = {
        "passed": True,
        "human_review_required": True,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "file_hashes": {
            "train": t_hash,
            "validation": v_hash
        },
        "approved_by": "Braden",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "review_history": [
            {
                "action": "approved",
                "reviewer": "Braden",
                "reviewed_at": datetime.now(timezone.utc).isoformat()
            }
        ]
    }
    
    review_item_id = await repo.save_human_review_item(
        item_type="fine_tune_dataset_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobRequestConfig(
        review_item_id=review_item_id,
        output_dir=str(tmp_path / "requests")
    )
    
    res = await builder.build_request_package(config)
    assert res.passed is True
    assert res.upload_ready is True
