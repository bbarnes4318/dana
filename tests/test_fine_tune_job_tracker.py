import os
import sys
import json
import uuid
import hashlib
import subprocess
from pathlib import Path
from datetime import datetime, timezone
import pytest

from storage.repository import Repository
from storage.schemas import HumanReviewItem
from training.fine_tune_job_tracker import (
    FineTuneJobTrackerConfig,
    FineTuneJobStartEligibilityResult,
    FineTuneJobTrackingRecord,
    FineTuneJobTrackingResult,
    FineTuneJobTracker,
)

@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    """Return a Repository backed by a temporary JSONL store."""
    return Repository(data_dir=tmp_path)


@pytest.fixture
def tracker(repo: Repository) -> FineTuneJobTracker:
    """Return a FineTuneJobTracker linked to the test Repository."""
    return FineTuneJobTracker(repository=repo)


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


# 1. test_check_start_eligibility_valid_package_and_approved_request
@pytest.mark.asyncio
async def test_check_start_eligibility_valid_package_and_approved_request(tmp_path: Path, tracker: FineTuneJobTracker, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(5)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    pkg_data = {
        "request_id": "req_123",
        "provider": "openai",
        "passed": True,
        "upload_ready": True,
        "manual_only": True,
        "api_upload_performed": False,
        "fine_tune_job_started": False,
        "deployment_allowed": False,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "train_hash": t_hash,
        "validation_hash": v_hash,
        "provider_request": {
            "manual_only": True,
            "api_upload_performed": False,
            "fine_tune_job_started": False,
            "deployment_allowed": False
        }
    }
    
    pkg_path = tmp_path / "job_request_package.json"
    with open(pkg_path, "w", encoding="utf-8") as f:
        json.dump(pkg_data, f)
        
    payload = {
        "passed": True,
        "upload_ready": True,
        "manual_only": True,
        "api_upload_performed": False,
        "fine_tune_job_started": False,
        "deployment_allowed": False,
        "request_id": "req_123",
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "train_hash": t_hash,
        "validation_hash": v_hash,
        "review_history": [
            {
                "action": "approved",
                "reviewer": "Braden",
                "reviewed_at": datetime.now(timezone.utc).isoformat()
            }
        ]
    }
    
    job_req_item_id = await repo.save_human_review_item(
        item_type="fine_tune_job_request",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobTrackerConfig(
        job_request_package_path=str(pkg_path),
        job_request_review_item_id=job_req_item_id
    )
    
    res = await tracker.check_start_eligibility(config)
    assert res.eligible is True
    assert res.upload_ready is True
    assert res.human_request_approved is True
    assert res.files_exist is True
    assert res.hashes_match is True


# 2. test_create_start_approval_request_pending_only
@pytest.mark.asyncio
async def test_create_start_approval_request_pending_only(tmp_path: Path, tracker: FineTuneJobTracker, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(5)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    pkg_data = {
        "request_id": "req_123",
        "provider": "openai",
        "passed": True,
        "upload_ready": True,
        "manual_only": True,
        "api_upload_performed": False,
        "fine_tune_job_started": False,
        "deployment_allowed": False,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "train_hash": t_hash,
        "validation_hash": v_hash,
        "provider_request": {
            "manual_only": True,
            "api_upload_performed": False,
            "fine_tune_job_started": False,
            "deployment_allowed": False
        }
    }
    
    pkg_path = tmp_path / "job_request_package.json"
    with open(pkg_path, "w", encoding="utf-8") as f:
        json.dump(pkg_data, f)
        
    config = FineTuneJobTrackerConfig(
        job_request_package_path=str(pkg_path),
        actor="Jimmy",
        reason="Test creation"
    )
    
    res = await tracker.create_start_approval_request(config)
    assert res.success is True
    assert res.review_item_id is not None
    
    item = await repo.get_human_review_item(res.review_item_id)
    assert item["item_type"] == "fine_tune_job_start_approval"
    assert item["status"] == "pending"
    assert item["payload"]["start_authorized"] is False


# 3. test_start_approval_request_does_not_upload_or_start_job
@pytest.mark.asyncio
async def test_start_approval_request_does_not_upload_or_start_job(tmp_path: Path, tracker: FineTuneJobTracker, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(5)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    pkg_data = {
        "request_id": "req_123",
        "provider": "openai",
        "passed": True,
        "upload_ready": True,
        "manual_only": True,
        "api_upload_performed": False,
        "fine_tune_job_started": False,
        "deployment_allowed": False,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "train_hash": t_hash,
        "validation_hash": v_hash,
        "provider_request": {
            "manual_only": True,
            "api_upload_performed": False,
            "fine_tune_job_started": False,
            "deployment_allowed": False
        }
    }
    
    pkg_path = tmp_path / "job_request_package.json"
    with open(pkg_path, "w", encoding="utf-8") as f:
        json.dump(pkg_data, f)
        
    config = FineTuneJobTrackerConfig(
        job_request_package_path=str(pkg_path),
        actor="Jimmy",
        reason="Test creation"
    )
    
    res = await tracker.create_start_approval_request(config)
    assert res.record.api_upload_performed is False
    assert res.record.fine_tune_job_started is False


# 4. test_rejects_package_upload_ready_false
@pytest.mark.asyncio
async def test_rejects_package_upload_ready_false(tmp_path: Path, tracker: FineTuneJobTracker) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(5)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    pkg_data = {
        "request_id": "req_123",
        "provider": "openai",
        "passed": True,
        "upload_ready": False,  # False!
        "manual_only": True,
        "api_upload_performed": False,
        "fine_tune_job_started": False,
        "deployment_allowed": False,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "train_hash": t_hash,
        "validation_hash": v_hash,
        "provider_request": {
            "manual_only": True,
            "api_upload_performed": False,
            "fine_tune_job_started": False,
            "deployment_allowed": False
        }
    }
    
    pkg_path = tmp_path / "job_request_package.json"
    with open(pkg_path, "w", encoding="utf-8") as f:
        json.dump(pkg_data, f)
        
    config = FineTuneJobTrackerConfig(
        job_request_package_path=str(pkg_path),
        actor="Jimmy",
        reason="Test creation"
    )
    
    res = await tracker.create_start_approval_request(config)
    assert res.success is False
    assert "upload_ready" in res.message


# 5. test_rejects_prior_upload_or_job_flags
@pytest.mark.asyncio
async def test_rejects_prior_upload_or_job_flags(tmp_path: Path, tracker: FineTuneJobTracker) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(5)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    pkg_data = {
        "request_id": "req_123",
        "provider": "openai",
        "passed": True,
        "upload_ready": True,
        "manual_only": True,
        "api_upload_performed": True,  # True!
        "fine_tune_job_started": False,
        "deployment_allowed": False,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "train_hash": t_hash,
        "validation_hash": v_hash,
        "provider_request": {
            "manual_only": True,
            "api_upload_performed": False,
            "fine_tune_job_started": False,
            "deployment_allowed": False
        }
    }
    
    pkg_path = tmp_path / "job_request_package.json"
    with open(pkg_path, "w", encoding="utf-8") as f:
        json.dump(pkg_data, f)
        
    config = FineTuneJobTrackerConfig(
        job_request_package_path=str(pkg_path)
    )
    
    res = await tracker.check_start_eligibility(config)
    assert res.eligible is False
    assert any("prior api upload" in f.lower() for f in res.critical_failures)


# 6. test_rejects_deployment_allowed_true
@pytest.mark.asyncio
async def test_rejects_deployment_allowed_true(tmp_path: Path, tracker: FineTuneJobTracker) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(5)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    pkg_data = {
        "request_id": "req_123",
        "provider": "openai",
        "passed": True,
        "upload_ready": True,
        "manual_only": True,
        "api_upload_performed": False,
        "fine_tune_job_started": False,
        "deployment_allowed": True,  # True!
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "train_hash": t_hash,
        "validation_hash": v_hash,
        "provider_request": {
            "manual_only": True,
            "api_upload_performed": False,
            "fine_tune_job_started": False,
            "deployment_allowed": False
        }
    }
    
    pkg_path = tmp_path / "job_request_package.json"
    with open(pkg_path, "w", encoding="utf-8") as f:
        json.dump(pkg_data, f)
        
    config = FineTuneJobTrackerConfig(
        job_request_package_path=str(pkg_path)
    )
    
    res = await tracker.check_start_eligibility(config)
    assert res.eligible is False
    assert any("prior deployment allowed" in f.lower() for f in res.critical_failures)


# 7. test_record_manual_upload_requires_approved_start_item
@pytest.mark.asyncio
async def test_record_manual_upload_requires_approved_start_item(tmp_path: Path, tracker: FineTuneJobTracker, repo: Repository) -> None:
    payload = {
        "upload_ready": True,
        "manual_only": True,
        "provider": "openai",
        "request_id": "req_123"
    }
    job_start_id = await repo.save_human_review_item(
        item_type="fine_tune_job_start_approval",
        status="pending",  # pending!
        payload=payload
    )
    
    config = FineTuneJobTrackerConfig(
        job_start_review_item_id=job_start_id,
        provider_file_id="file-123",
        provider_validation_file_id="file-456"
    )
    
    res = await tracker.record_manual_upload(config)
    assert res.success is False
    assert "not approved" in res.message


# 8. test_record_manual_upload_requires_provider_file_ids
@pytest.mark.asyncio
async def test_record_manual_upload_requires_provider_file_ids(tmp_path: Path, tracker: FineTuneJobTracker, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(5)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)

    payload = {
        "upload_ready": True,
        "manual_only": True,
        "provider": "openai",
        "request_id": "req_123",
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "train_hash": t_hash,
        "validation_hash": v_hash,
        "start_authorized": True,
        "review_history": [{"action": "approved", "reviewer": "Braden", "reviewed_at": datetime.now(timezone.utc).isoformat()}]
    }
    
    job_start_id = await repo.save_human_review_item(
        item_type="fine_tune_job_start_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobTrackerConfig(
        job_start_review_item_id=job_start_id,
        # missing file ids!
    )
    
    with pytest.raises(ValueError, match="provider_file_id is required"):
        await tracker.record_manual_upload(config)


# 9. test_record_manual_upload_creates_tracking_record
@pytest.mark.asyncio
async def test_record_manual_upload_creates_tracking_record(tmp_path: Path, tracker: FineTuneJobTracker, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(5)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)

    payload = {
        "upload_ready": True,
        "manual_only": True,
        "provider": "openai",
        "request_id": "req_123",
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "train_hash": t_hash,
        "validation_hash": v_hash,
        "start_authorized": True,
        "review_history": [{"action": "approved", "reviewer": "Braden", "reviewed_at": datetime.now(timezone.utc).isoformat()}]
    }
    
    job_start_id = await repo.save_human_review_item(
        item_type="fine_tune_job_start_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobTrackerConfig(
        job_start_review_item_id=job_start_id,
        provider_file_id="file-123",
        provider_validation_file_id="file-456"
    )
    
    res = await tracker.record_manual_upload(config)
    assert res.success is True
    assert res.new_status == "files_uploaded_manual"
    assert res.record.provider_file_id == "file-123"
    assert res.record.provider_validation_file_id == "file-456"
    assert res.record.api_upload_performed is False
    assert res.record.metadata.get("manual_upload_recorded") is True


# 10. test_record_manual_job_start_requires_approved_start_item
@pytest.mark.asyncio
async def test_record_manual_job_start_requires_approved_start_item(tmp_path: Path, tracker: FineTuneJobTracker, repo: Repository) -> None:
    payload = {
        "upload_ready": True,
        "manual_only": True,
        "provider": "openai",
        "request_id": "req_123"
    }
    job_start_id = await repo.save_human_review_item(
        item_type="fine_tune_job_start_approval",
        status="pending",
        payload=payload
    )
    
    config = FineTuneJobTrackerConfig(
        job_start_review_item_id=job_start_id,
        provider_job_id="ftjob-123"
    )
    
    res = await tracker.record_manual_job_start(config)
    assert res.success is False
    assert "not approved" in res.message


# 11. test_record_manual_job_start_requires_provider_job_id
@pytest.mark.asyncio
async def test_record_manual_job_start_requires_provider_job_id(tmp_path: Path, tracker: FineTuneJobTracker, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(5)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)

    payload = {
        "upload_ready": True,
        "manual_only": True,
        "provider": "openai",
        "request_id": "req_123",
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "train_hash": t_hash,
        "validation_hash": v_hash,
        "start_authorized": True,
        "review_history": [{"action": "approved", "reviewer": "Braden", "reviewed_at": datetime.now(timezone.utc).isoformat()}]
    }
    
    job_start_id = await repo.save_human_review_item(
        item_type="fine_tune_job_start_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobTrackerConfig(
        job_start_review_item_id=job_start_id,
        # missing job id!
    )
    
    with pytest.raises(ValueError, match="provider_job_id is required"):
        await tracker.record_manual_job_start(config)


# 12. test_record_manual_job_start_creates_or_updates_tracking_record
@pytest.mark.asyncio
async def test_record_manual_job_start_creates_or_updates_tracking_record(tmp_path: Path, tracker: FineTuneJobTracker, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(5)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)

    payload = {
        "upload_ready": True,
        "manual_only": True,
        "provider": "openai",
        "request_id": "req_123",
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "train_hash": t_hash,
        "validation_hash": v_hash,
        "start_authorized": True,
        "review_history": [{"action": "approved", "reviewer": "Braden", "reviewed_at": datetime.now(timezone.utc).isoformat()}]
    }
    
    job_start_id = await repo.save_human_review_item(
        item_type="fine_tune_job_start_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    # 1. Record manual upload first
    config_upload = FineTuneJobTrackerConfig(
        job_start_review_item_id=job_start_id,
        provider_file_id="file-123",
        provider_validation_file_id="file-456"
    )
    await tracker.record_manual_upload(config_upload)
    
    # 2. Record manual job start
    config_job = FineTuneJobTrackerConfig(
        job_start_review_item_id=job_start_id,
        provider_job_id="ftjob-123"
    )
    res = await tracker.record_manual_job_start(config_job)
    assert res.success is True
    assert res.new_status == "job_started_manual"
    assert res.record.provider_job_id == "ftjob-123"
    assert res.record.fine_tune_job_started is False
    assert res.record.metadata.get("manual_job_start_recorded") is True


# 13. test_manual_status_transition_running_to_succeeded
@pytest.mark.asyncio
async def test_manual_status_transition_running_to_succeeded(tmp_path: Path, tracker: FineTuneJobTracker, repo: Repository) -> None:
    history = []
    record = FineTuneJobTrackingRecord(
        tracking_id="tr_123",
        request_id="req_123",
        provider="openai",
        status="job_started_manual",
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        manual_only=True,
        api_upload_performed=False,
        fine_tune_job_started=False,
        deployment_allowed=False,
        start_authorized=True,
        audit_history=history,
        validation_summary={}
    )
    
    await repo.save_human_review_item(
        item_type="fine_tune_job_tracking",
        status="job_started_manual",
        payload=record.model_dump(mode="json")
    )
    
    # 1. transition to running_manual
    res1 = await tracker.update_manual_status(
        tracking_id="tr_123",
        new_status="running_manual",
        actor="Jimmy",
        reason="Console shows running"
    )
    assert res1.success is True
    assert res1.new_status == "running_manual"
    
    # 2. transition to succeeded_manual
    res2 = await tracker.update_manual_status(
        tracking_id="tr_123",
        new_status="succeeded_manual",
        actor="Jimmy",
        reason="Console shows succeeded",
        provider_model_id="ft:gpt-4o-mini:dana-final-expense-safe"
    )
    assert res2.success is True
    assert res2.new_status == "succeeded_manual"
    assert res2.record.provider_model_id == "ft:gpt-4o-mini:dana-final-expense-safe"
    assert res2.record.deployment_allowed is False


# 14. test_invalid_status_transition_rejected
@pytest.mark.asyncio
async def test_invalid_status_transition_rejected(tmp_path: Path, tracker: FineTuneJobTracker, repo: Repository) -> None:
    history = []
    record = FineTuneJobTrackingRecord(
        tracking_id="tr_123",
        request_id="req_123",
        provider="openai",
        status="requested",  # requested!
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        manual_only=True,
        api_upload_performed=False,
        fine_tune_job_started=False,
        deployment_allowed=False,
        start_authorized=True,
        audit_history=history,
        validation_summary={}
    )
    
    await repo.save_human_review_item(
        item_type="fine_tune_job_tracking",
        status="requested",
        payload=record.model_dump(mode="json")
    )
    
    # requested -> running_manual is disallowed!
    with pytest.raises(ValueError, match="Status transition from 'requested' to 'running_manual' is disallowed"):
        await tracker.update_manual_status(
            tracking_id="tr_123",
            new_status="running_manual",
            actor="Jimmy",
            reason="Skip transitions"
        )

    # terminal -> running_manual fails!
    item = await tracker.get_tracking_record_item("tr_123")
    assert item is not None
    item["status"] = "succeeded_manual"
    item["payload"]["status"] = "succeeded_manual"
    await repo.save_human_review_item(
        id=item["id"],
        item_type="fine_tune_job_tracking",
        status="succeeded_manual",
        payload=item["payload"]
    )
    
    with pytest.raises(ValueError, match="Status transition from 'succeeded_manual' to 'running_manual' is disallowed"):
        await tracker.update_manual_status(
            tracking_id="tr_123",
            new_status="running_manual",
            actor="Jimmy",
            reason="Back to running"
        )


# 15. test_no_deployment_or_active_prompt_created_on_success
@pytest.mark.asyncio
async def test_no_deployment_or_active_prompt_created_on_success(tmp_path: Path, tracker: FineTuneJobTracker, repo: Repository) -> None:
    history = []
    record = FineTuneJobTrackingRecord(
        tracking_id="tr_123",
        request_id="req_123",
        provider="openai",
        status="running_manual",
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        manual_only=True,
        api_upload_performed=False,
        fine_tune_job_started=False,
        deployment_allowed=False,
        start_authorized=True,
        audit_history=history,
        validation_summary={}
    )
    
    await repo.save_human_review_item(
        item_type="fine_tune_job_tracking",
        status="running_manual",
        payload=record.model_dump(mode="json")
    )
    
    res = await tracker.update_manual_status(
        tracking_id="tr_123",
        new_status="succeeded_manual",
        actor="Jimmy",
        reason="Console succeeded",
        provider_model_id="model-123"
    )
    
    assert res.success is True
    assert res.record.deployment_allowed is False
    assert res.record.metadata.get("active_runtime") is not True
    
    # Assert no PromptVersion or Active prompts created in DB
    versions = await repo.query_human_review_items({"item_type": "prompt_version"})
    assert len(versions) == 0


# 16. test_generate_tracking_report_writes_json_and_markdown
@pytest.mark.asyncio
async def test_generate_tracking_report_writes_json_and_markdown(tmp_path: Path, tracker: FineTuneJobTracker, repo: Repository) -> None:
    history = []
    record = FineTuneJobTrackingRecord(
        tracking_id="tr_123",
        request_id="req_123",
        provider="openai",
        status="running_manual",
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        manual_only=True,
        api_upload_performed=False,
        fine_tune_job_started=False,
        deployment_allowed=False,
        start_authorized=True,
        audit_history=history,
        validation_summary={}
    )
    
    await repo.save_human_review_item(
        item_type="fine_tune_job_tracking",
        status="running_manual",
        payload=record.model_dump(mode="json")
    )
    
    json_path, md_path = await tracker.generate_tracking_report("tr_123", output_dir=tmp_path)
    assert Path(json_path).exists()
    assert Path(md_path).exists()
    
    md_content = Path(md_path).read_text(encoding="utf-8")
    assert "# Dana Fine-Tune Job Tracking Report" in md_content
    assert "Executive Summary" in md_content
    assert "Safety Controls" in md_content
    assert "Required Next Steps" in md_content


# 17. test_cli_check_outputs_json
def test_cli_check_outputs_json(tmp_path: Path) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(5)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    pkg_data = {
        "request_id": "req_123",
        "provider": "openai",
        "passed": True,
        "upload_ready": True,
        "manual_only": True,
        "api_upload_performed": False,
        "fine_tune_job_started": False,
        "deployment_allowed": False,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "train_hash": t_hash,
        "validation_hash": v_hash,
        "provider_request": {
            "manual_only": True,
            "api_upload_performed": False,
            "fine_tune_job_started": False,
            "deployment_allowed": False
        }
    }
    
    pkg_path = tmp_path / "job_request_package.json"
    with open(pkg_path, "w", encoding="utf-8") as f:
        json.dump(pkg_data, f)
        
    cmd = [
        sys.executable,
        "scripts/track_fine_tune_job.py",
        "check",
        "--job-request-package", str(pkg_path),
        "--output-dir", str(tmp_path)
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 0
    data = json.loads(res.stdout.strip())
    assert data["upload_ready"] is True
    assert data["provider"] == "openai"


# 18. test_cli_request_start_outputs_json_and_pending_item
def test_cli_request_start_outputs_json_and_pending_item(tmp_path: Path) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(5)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)
    
    pkg_data = {
        "request_id": "req_123",
        "provider": "openai",
        "passed": True,
        "upload_ready": True,
        "manual_only": True,
        "api_upload_performed": False,
        "fine_tune_job_started": False,
        "deployment_allowed": False,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "train_hash": t_hash,
        "validation_hash": v_hash,
        "provider_request": {
            "manual_only": True,
            "api_upload_performed": False,
            "fine_tune_job_started": False,
            "deployment_allowed": False
        }
    }
    
    pkg_path = tmp_path / "job_request_package.json"
    with open(pkg_path, "w", encoding="utf-8") as f:
        json.dump(pkg_data, f)
        
    cmd = [
        sys.executable,
        "scripts/track_fine_tune_job.py",
        "request-start",
        "--job-request-package", str(pkg_path),
        "--actor", "Jimmy",
        "--reason", "CLI request start test",
        "--output-dir", str(tmp_path)
    ]
    # Use temporary repo path via env or repository class settings.
    # To isolate storage to tmp_path, we set the active directory in scripts.
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    env["DANA_DATA_DIR"] = str(tmp_path) # support repo isolation if repo respects it
    
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 0
    data = json.loads(res.stdout.strip())
    assert data["success"] is True
    assert data["new_status"] == "start_approval_pending"


# 19. test_cli_record_upload_fails_without_approval
def test_cli_record_upload_fails_without_approval(tmp_path: Path) -> None:
    cmd = [
        sys.executable,
        "scripts/track_fine_tune_job.py",
        "record-upload",
        "--job-start-review-item-id", "non-existent-id",
        "--provider-file-id", "file-123",
        "--actor", "Jimmy",
        "--reason", "Test record upload",
        "--output-dir", str(tmp_path)
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 1
    err = json.loads(res.stderr.strip())
    assert err["status"] == "error"


# 20. test_cli_record_job_fails_without_approval
def test_cli_record_job_fails_without_approval(tmp_path: Path) -> None:
    cmd = [
        sys.executable,
        "scripts/track_fine_tune_job.py",
        "record-job",
        "--job-start-review-item-id", "non-existent-id",
        "--provider-job-id", "ftjob-123",
        "--actor", "Jimmy",
        "--reason", "Test record job",
        "--output-dir", str(tmp_path)
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 1
    err = json.loads(res.stderr.strip())
    assert err["status"] == "error"


# 21. test_cli_update_status_outputs_json
def test_cli_update_status_outputs_json(tmp_path: Path) -> None:
    # Use config/repo with pre-existing record to check CLI update
    # Here we just check option format parser outputs error for missing tracking-id
    cmd = [
        sys.executable,
        "scripts/track_fine_tune_job.py",
        "update-status",
        "--tracking-id", "non-existent-id",
        "--status", "running_manual",
        "--actor", "Jimmy",
        "--reason", "Updating",
        "--output-dir", str(tmp_path)
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 1
    err = json.loads(res.stderr.strip())
    assert err["status"] == "error"
    assert "tracking record not found" in err["message"].lower()


# 22. test_no_live_prompt_file_modified
@pytest.mark.asyncio
async def test_no_live_prompt_file_modified(tmp_path: Path, tracker: FineTuneJobTracker) -> None:
    prompt_path = Path("prompts/final_expense_alex.md")
    content_before = ""
    if prompt_path.exists():
        content_before = prompt_path.read_text(encoding="utf-8")
        
    config = FineTuneJobTrackerConfig(
        job_request_package_path="non-existent.json",
        notes="Eligibility check should fail, but must not edit prompt files."
    )
    try:
        await tracker.check_start_eligibility(config)
    except Exception:
        pass
        
    if prompt_path.exists():
        content_after = prompt_path.read_text(encoding="utf-8")
        assert content_before == content_after


# 23. test_no_external_api_or_fine_tune_call
def test_no_external_api_or_fine_tune_call() -> None:
    with open("training/fine_tune_job_tracker.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "import openai" not in content
    assert "openai." not in content
    assert "import httpx" not in content
    assert "import requests" not in content
    assert "urllib." not in content


# 24. test_manual_flags_are_truthful
@pytest.mark.asyncio
async def test_manual_flags_are_truthful(tmp_path: Path, tracker: FineTuneJobTracker, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(5)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)

    payload = {
        "upload_ready": True,
        "manual_only": True,
        "provider": "openai",
        "request_id": "req_123",
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "train_hash": t_hash,
        "validation_hash": v_hash,
        "start_authorized": True,
        "review_history": [{"action": "approved", "reviewer": "Braden", "reviewed_at": datetime.now(timezone.utc).isoformat()}]
    }
    
    job_start_id = await repo.save_human_review_item(
        item_type="fine_tune_job_start_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobTrackerConfig(
        job_start_review_item_id=job_start_id,
        provider_file_id="file-123",
        provider_validation_file_id="file-456"
    )
    
    res = await tracker.record_manual_upload(config)
    assert res.record.api_upload_performed is False
    assert res.record.metadata.get("manual_upload_recorded") is True


# 25. test_review_history_approved_action_accepts_start_approval
@pytest.mark.asyncio
async def test_review_history_approved_action_accepts_start_approval(tmp_path: Path, tracker: FineTuneJobTracker, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(5)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)

    payload = {
        "upload_ready": True,
        "manual_only": True,
        "provider": "openai",
        "request_id": "req_123",
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "train_hash": t_hash,
        "validation_hash": v_hash,
        "start_authorized": True,
        "review_history": [
            {
                "action": "approved",
                "reviewer": "Braden",
                "reviewed_at": datetime.now(timezone.utc).isoformat()
            }
        ]
    }
    
    job_start_id = await repo.save_human_review_item(
        item_type="fine_tune_job_start_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobTrackerConfig(
        job_start_review_item_id=job_start_id
    )
    
    res = await tracker.check_start_eligibility(config)
    assert res.human_start_approved is True


# 26. test_status_approved_without_reviewer_fails
@pytest.mark.asyncio
async def test_status_approved_without_reviewer_fails(tmp_path: Path, tracker: FineTuneJobTracker, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(5)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)

    payload = {
        "upload_ready": True,
        "manual_only": True,
        "provider": "openai",
        "request_id": "req_123",
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "train_hash": t_hash,
        "validation_hash": v_hash,
        "start_authorized": True,
        # review_history missing!
        "review_history": []
    }
    
    # status approved but no reviewer/reviewed_at/history
    job_start_id = await repo.save_human_review_item(
        item_type="fine_tune_job_start_approval",
        status="approved",
        payload=payload
    )
    
    config = FineTuneJobTrackerConfig(
        job_start_review_item_id=job_start_id
    )
    
    res = await tracker.check_start_eligibility(config)
    assert res.human_start_approved is False
    assert any("job start review item is not human-approved" in f.lower() for f in res.critical_failures)


# 27. test_list_tracking_records_filters_by_status
@pytest.mark.asyncio
async def test_list_tracking_records_filters_by_status(tmp_path: Path, tracker: FineTuneJobTracker, repo: Repository) -> None:
    history = []
    record1 = FineTuneJobTrackingRecord(
        tracking_id="tr_1",
        request_id="req_1",
        provider="openai",
        status="running_manual",
        created_at="2026-05-31T01:00:00Z",
        updated_at="2026-05-31T01:00:00Z",
        manual_only=True,
        api_upload_performed=False,
        fine_tune_job_started=False,
        deployment_allowed=False,
        start_authorized=True,
        audit_history=history,
        validation_summary={}
    )
    record2 = FineTuneJobTrackingRecord(
        tracking_id="tr_2",
        request_id="req_2",
        provider="openai",
        status="succeeded_manual",
        created_at="2026-05-31T02:00:00Z",
        updated_at="2026-05-31T02:00:00Z",
        manual_only=True,
        api_upload_performed=False,
        fine_tune_job_started=False,
        deployment_allowed=False,
        start_authorized=True,
        audit_history=history,
        validation_summary={}
    )
    
    await repo.save_human_review_item(
        item_type="fine_tune_job_tracking",
        status="running_manual",
        payload=record1.model_dump(mode="json")
    )
    await repo.save_human_review_item(
        item_type="fine_tune_job_tracking",
        status="succeeded_manual",
        payload=record2.model_dump(mode="json")
    )
    
    running = await tracker.list_tracking_records(status="running_manual")
    assert len(running) == 1
    assert running[0]["tracking_id"] == "tr_1"
    
    succeeded = await tracker.list_tracking_records(status="succeeded_manual")
    assert len(succeeded) == 1
    assert succeeded[0]["tracking_id"] == "tr_2"


# 28. test_report_readable_for_nontechnical_manager
@pytest.mark.asyncio
async def test_report_readable_for_nontechnical_manager(tmp_path: Path, tracker: FineTuneJobTracker, repo: Repository) -> None:
    history = []
    record = FineTuneJobTrackingRecord(
        tracking_id="tr_123",
        request_id="req_123",
        provider="openai",
        status="running_manual",
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        manual_only=True,
        api_upload_performed=False,
        fine_tune_job_started=False,
        deployment_allowed=False,
        start_authorized=True,
        audit_history=history,
        validation_summary={}
    )
    
    await repo.save_human_review_item(
        item_type="fine_tune_job_tracking",
        status="running_manual",
        payload=record.model_dump(mode="json")
    )
    
    _, md_path = await tracker.generate_tracking_report("tr_123", output_dir=tmp_path)
    md_content = Path(md_path).read_text(encoding="utf-8")
    assert "Executive Summary" in md_content
    assert "Safety Controls" in md_content
    assert "Required Next Steps" in md_content


# 29. test_approved_start_item_without_start_authorized_fails
@pytest.mark.asyncio
async def test_approved_start_item_without_start_authorized_fails(tmp_path: Path, tracker: FineTuneJobTracker, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(5)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)

    payload = {
        "upload_ready": True,
        "manual_only": True,
        "provider": "openai",
        "request_id": "req_123",
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "train_hash": t_hash,
        "validation_hash": v_hash,
        "start_authorized": False,  # False!
        "api_upload_performed": False,
        "fine_tune_job_started": False,
        "deployment_allowed": False,
        "review_history": [{"action": "approved", "reviewer": "Braden", "reviewed_at": datetime.now(timezone.utc).isoformat()}]
    }
    
    job_start_id = await repo.save_human_review_item(
        item_type="fine_tune_job_start_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobTrackerConfig(
        job_start_review_item_id=job_start_id
    )
    
    res = await tracker.check_start_eligibility(config)
    assert res.human_start_approved is False
    assert any("start_authorized" in f.lower() for f in res.critical_failures)


# 30. test_record_manual_upload_rejects_approved_item_without_start_authorized
@pytest.mark.asyncio
async def test_record_manual_upload_rejects_approved_item_without_start_authorized(tmp_path: Path, tracker: FineTuneJobTracker, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(5)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)

    payload = {
        "upload_ready": True,
        "manual_only": True,
        "provider": "openai",
        "request_id": "req_123",
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "train_hash": t_hash,
        "validation_hash": v_hash,
        "start_authorized": False,  # False!
        "api_upload_performed": False,
        "fine_tune_job_started": False,
        "deployment_allowed": False,
        "review_history": [{"action": "approved", "reviewer": "Braden", "reviewed_at": datetime.now(timezone.utc).isoformat()}]
    }
    
    job_start_id = await repo.save_human_review_item(
        item_type="fine_tune_job_start_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobTrackerConfig(
        job_start_review_item_id=job_start_id,
        provider_file_id="file-123",
        provider_validation_file_id="file-456"
    )
    
    res = await tracker.record_manual_upload(config)
    assert res.success is False
    assert any("start_authorized" in f.lower() for f in res.warnings or [res.message])


# 31. test_record_manual_job_rejects_approved_item_without_start_authorized
@pytest.mark.asyncio
async def test_record_manual_job_rejects_approved_item_without_start_authorized(tmp_path: Path, tracker: FineTuneJobTracker, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(5)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)

    payload = {
        "upload_ready": True,
        "manual_only": True,
        "provider": "openai",
        "request_id": "req_123",
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "train_hash": t_hash,
        "validation_hash": v_hash,
        "start_authorized": False,  # False!
        "api_upload_performed": False,
        "fine_tune_job_started": False,
        "deployment_allowed": False,
        "review_history": [{"action": "approved", "reviewer": "Braden", "reviewed_at": datetime.now(timezone.utc).isoformat()}]
    }
    
    job_start_id = await repo.save_human_review_item(
        item_type="fine_tune_job_start_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobTrackerConfig(
        job_start_review_item_id=job_start_id,
        provider_job_id="ftjob-123"
    )
    
    res = await tracker.record_manual_job_start(config)
    assert res.success is False
    assert any("start_authorized" in f.lower() for f in res.warnings or [res.message])


# 32. test_approved_start_item_with_start_authorized_true_passes
@pytest.mark.asyncio
async def test_approved_start_item_with_start_authorized_true_passes(tmp_path: Path, tracker: FineTuneJobTracker, repo: Repository) -> None:
    train_path = tmp_path / "train.jsonl"
    val_path = tmp_path / "val.jsonl"
    write_jsonl(train_path, [make_safe_openai_chat_record(i) for i in range(5)])
    write_jsonl(val_path, [make_safe_openai_chat_record(i+10) for i in range(2)])
    t_hash = compute_hash(train_path)
    v_hash = compute_hash(val_path)

    payload = {
        "upload_ready": True,
        "manual_only": True,
        "provider": "openai",
        "request_id": "req_123",
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "train_hash": t_hash,
        "validation_hash": v_hash,
        "start_authorized": True,  # True!
        "api_upload_performed": False,
        "fine_tune_job_started": False,
        "deployment_allowed": False,
        "review_history": [{"action": "approved", "reviewer": "Braden", "reviewed_at": datetime.now(timezone.utc).isoformat()}]
    }
    
    job_start_id = await repo.save_human_review_item(
        item_type="fine_tune_job_start_approval",
        status="approved",
        payload=payload,
        reviewer="Braden",
        reviewed_at=datetime.now(timezone.utc)
    )
    
    config = FineTuneJobTrackerConfig(
        job_start_review_item_id=job_start_id
    )
    
    res = await tracker.check_start_eligibility(config)
    assert res.human_start_approved is True
    assert len(res.critical_failures) == 0

