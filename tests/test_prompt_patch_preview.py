"""Unit and integration tests for Dana's Safe Prompt Patch Previewer and Eval Gate."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from storage.repository import Repository
from prompts.versioning import PromptVersionManager
from prompts.patch_preview import PromptPatchPreviewer, PromptPatchPreviewResult, PromptPatchGateResult
from prompts.patch_generator import PromptPatchCandidate


@pytest.fixture
def repo(tmp_path):
    """Return a Repository backed by a temporary JsonlStore."""
    return Repository(data_dir=tmp_path)


@pytest.fixture
def version_manager(repo):
    """Return a PromptVersionManager backed by a temporary Repository."""
    return PromptVersionManager(repository=repo)


@pytest.fixture
def previewer(repo, version_manager):
    """Return a PromptPatchPreviewer using temporary repo and version manager."""
    return PromptPatchPreviewer(repository=repo, prompt_version_manager=version_manager)


def build_basic_patch_item(
    item_id: str,
    status: str,
    payload_fields: dict | None = None,
    review_history: list | None = None
) -> dict:
    """Helper to build a mock HumanReviewItem dictionary of type prompt_patch."""
    payload = {
        "patch_type": "transfer_consent_rule",
        "title": "Test patch",
        "proposed_text": "- Make sure to ask permission before transfer.",
        "payload_hash": "mock_payload_hash",
        "source_prompt_path": "prompts/final_expense_alex.md",
        "source_prompt_hash": "mock_prompt_hash",
        "prompt_patch_approved": True,
    }
    if payload_fields is not None:
        payload.update(payload_fields)
    if review_history is not None:
        payload["review_history"] = review_history

    return {
        "id": item_id,
        "item_type": "prompt_patch",
        "status": status,
        "payload": payload,
        "created_at": datetime.now(timezone.utc).isoformat()
    }


# 1. test_loads_only_approved_prompt_patches
@pytest.mark.asyncio
async def test_loads_only_approved_prompt_patches(previewer, repo):
    # Save different status patches
    p1 = build_basic_patch_item("p1", "pending", {"prompt_patch_approved": True})
    p2 = build_basic_patch_item("p2", "rejected", {"prompt_patch_approved": True})
    p3 = build_basic_patch_item("p3", "needs_changes", {"prompt_patch_approved": True})
    p4 = build_basic_patch_item("p4", "approved", {"prompt_patch_approved": True}) # approved
    
    for item in [p1, p2, p3, p4]:
        await repo.save_human_review_item(**item)
        
    loaded = await previewer.load_approved_patch_items()
    assert len(loaded) == 1
    assert loaded[0]["id"] == "p4"


# 2. test_patch_id_requires_approved_item
@pytest.mark.asyncio
async def test_patch_id_requires_approved_item(previewer, repo):
    p1 = build_basic_patch_item("p1", "pending", {"prompt_patch_approved": True})
    await repo.save_human_review_item(**p1)
    
    with pytest.raises(ValueError) as excinfo:
        await previewer.load_approved_patch_items(["p1"])
    assert "is not approved" in str(excinfo.value)


# 3. test_apply_patch_to_target_section
def test_apply_patch_to_target_section(previewer):
    prompt_content = (
        "# Main Heading\n\n"
        "## STRICT COMPLIANCE & GUARDRAILS\n\n"
        "- Existing rule.\n"
    )
    patch_item = build_basic_patch_item("p_target", "approved", {
        "target_section": "STRICT COMPLIANCE & GUARDRAILS",
        "proposed_text": "- Patched rule under section."
    })
    
    patched_text, apps = previewer.apply_patches_to_prompt(prompt_content, [patch_item])
    assert apps[0].applied is True
    assert "<!-- PATCH_START: p_target transfer_consent_rule -->" in patched_text
    assert "- Patched rule under section." in patched_text
    assert "## STRICT COMPLIANCE & GUARDRAILS\n\n<!-- PATCH_START: p_target" in patched_text


# 4. test_apply_patch_with_original_text_replace
def test_apply_patch_with_original_text_replace(previewer):
    prompt_content = (
        "This is original line.\n"
        "Replace me completely.\n"
        "This is trailing line.\n"
    )
    patch_item = build_basic_patch_item("p_replace", "approved", {
        "original_text": "Replace me completely.",
        "insertion_point": "replace",
        "proposed_text": "Completely new replaced text."
    })
    
    patched_text, apps = previewer.apply_patches_to_prompt(prompt_content, [patch_item])
    assert apps[0].applied is True
    assert "Replace me completely." not in patched_text
    assert "Completely new replaced text." in patched_text


# 5. test_apply_patch_appends_when_section_missing
def test_apply_patch_appends_when_section_missing(previewer):
    prompt_content = "# Intro Section\n- Welcome.\n"
    patch_item = build_basic_patch_item("p_missing", "approved", {
        "target_section": "MISSING SECTION",
        "proposed_text": "- Appended rule."
    })
    
    patched_text, apps = previewer.apply_patches_to_prompt(prompt_content, [patch_item])
    assert apps[0].applied is True
    assert "## Patch Candidates: MISSING SECTION" in patched_text
    assert "- Appended rule." in patched_text


# 6. test_does_not_duplicate_existing_patch_text
def test_does_not_duplicate_existing_patch_text(previewer):
    prompt_content = "# Section\n- Make sure to ask permission before transfer.\n"
    patch_item = build_basic_patch_item("p_dup", "approved", {
        "proposed_text": "- Make sure to ask permission before transfer."
    })
    
    patched_text, apps = previewer.apply_patches_to_prompt(prompt_content, [patch_item])
    assert apps[0].applied is False
    assert apps[0].skipped is True
    assert apps[0].skip_reason == "proposed_text already present"


# 7. test_source_prompt_hash_mismatch_warns
@pytest.mark.asyncio
async def test_source_prompt_hash_mismatch_warns(previewer, repo, tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("# Role\n", encoding="utf-8")
    
    # Save approved patch with different source hash
    p = build_basic_patch_item("p_hash", "approved", {
        "source_prompt_hash": "different_hash"
    })
    await repo.save_human_review_item(**p)
    
    res = await previewer.build_preview(
        prompt_name="final_expense_alex",
        prompt_path=prompt_file,
        patch_ids=["p_hash"],
        output_dir=tmp_path,
        run_gates=False
    )
    assert any("hash differs" in w for w in res.warnings)


# 8. test_preview_does_not_modify_live_prompt_file
@pytest.mark.asyncio
async def test_preview_does_not_modify_live_prompt_file(previewer, repo, tmp_path):
    prompt_file = tmp_path / "prompt.md"
    original_content = "# Role\n- Live wording.\n"
    prompt_file.write_text(original_content, encoding="utf-8")
    
    p = build_basic_patch_item("p_live", "approved", {})
    await repo.save_human_review_item(**p)
    
    res = await previewer.build_preview(
        prompt_name="final_expense_alex",
        prompt_path=prompt_file,
        patch_ids=["p_live"],
        output_dir=tmp_path,
        run_gates=False
    )
    
    assert prompt_file.read_text(encoding="utf-8") == original_content


# 9. test_preview_writes_prompt_diff_json_markdown
@pytest.mark.asyncio
async def test_preview_writes_prompt_diff_json_markdown(previewer, repo, tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("# Role\n", encoding="utf-8")
    
    p = build_basic_patch_item("p_write", "approved", {})
    await repo.save_human_review_item(**p)
    
    res = await previewer.build_preview(
        prompt_name="final_expense_alex",
        prompt_path=prompt_file,
        patch_ids=["p_write"],
        output_dir=tmp_path,
        run_gates=False
    )
    
    assert Path(res.patched_prompt_path).exists()
    assert Path(res.diff_path).exists()
    assert Path(res.report_json_path).exists()
    assert Path(res.report_markdown_path).exists()


# 10. test_prompt_validation_failure_blocks_candidate_version
@pytest.mark.asyncio
async def test_prompt_validation_failure_blocks_candidate_version(previewer, repo, tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("# Role\n", encoding="utf-8")
    
    # Proposed text introduces critical safety failure: say "you qualify"
    p = build_basic_patch_item("p_unsafe", "approved", {
        "proposed_text": "Tell them say you qualify right now."
    })
    await repo.save_human_review_item(**p)
    
    res = await previewer.build_preview(
        prompt_name="final_expense_alex",
        prompt_path=prompt_file,
        patch_ids=["p_unsafe"],
        output_dir=tmp_path,
        run_gates=True,
        create_candidate_version=True
    )
    
    assert res.passed is False
    assert res.candidate_prompt_version_id is None


# 11. test_safe_patch_passes_prompt_validation
@pytest.mark.asyncio
async def test_safe_patch_passes_prompt_validation(previewer, repo, tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("# Role\n", encoding="utf-8")
    
    p = build_basic_patch_item("p_safe", "approved", {
        "proposed_text": "- Make sure to ask permission before transfer."
    })
    await repo.save_human_review_item(**p)
    
    res = await previewer.build_preview(
        prompt_name="final_expense_alex",
        prompt_path=prompt_file,
        patch_ids=["p_safe"],
        output_dir=tmp_path,
        run_gates=True,
        create_candidate_version=False
    )
    
    assert res.passed is True


# 12. test_run_gates_includes_replay_and_simulation
@pytest.mark.asyncio
async def test_run_gates_includes_replay_and_simulation(previewer, repo, tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("# Role\n", encoding="utf-8")
    
    p = build_basic_patch_item("p_gates", "approved", {})
    await repo.save_human_review_item(**p)
    
    res = await previewer.build_preview(
        prompt_name="final_expense_alex",
        prompt_path=prompt_file,
        patch_ids=["p_gates"],
        output_dir=tmp_path,
        run_gates=True,
        create_candidate_version=False
    )
    
    assert res.gate_result is not None
    assert "transcript_replay_passed" in res.gate_result
    assert "prospect_simulations_passed" in res.gate_result


# 13. test_create_candidate_version_only_when_gates_pass
@pytest.mark.asyncio
async def test_create_candidate_version_only_when_gates_pass(previewer, repo, tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("# Role\n", encoding="utf-8")
    
    p = build_basic_patch_item("p_vpass", "approved", {})
    await repo.save_human_review_item(**p)
    
    res = await previewer.build_preview(
        prompt_name="final_expense_alex",
        prompt_path=prompt_file,
        patch_ids=["p_vpass"],
        output_dir=tmp_path,
        run_gates=True,
        create_candidate_version=True
    )
    
    assert res.passed is True
    assert res.candidate_prompt_version_id is not None
    
    # Assert PromptVersion created in repository has canary_status == 'candidate'
    db_record = await repo.get_prompt_version(res.candidate_prompt_version_id)
    assert db_record["canary_status"] == "candidate"


# 14. test_no_candidate_version_when_gates_fail
@pytest.mark.asyncio
async def test_no_candidate_version_when_gates_fail(previewer, repo, tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("# Role\n", encoding="utf-8")
    
    p = build_basic_patch_item("p_vfail", "approved", {
        "proposed_text": "Tell them guaranteed acceptance."
    })
    await repo.save_human_review_item(**p)
    
    res = await previewer.build_preview(
        prompt_name="final_expense_alex",
        prompt_path=prompt_file,
        patch_ids=["p_vfail"],
        output_dir=tmp_path,
        run_gates=True,
        create_candidate_version=True
    )
    
    assert res.passed is False
    assert res.candidate_prompt_version_id is None


# 15. test_candidate_version_metadata_contains_patch_ids_and_hashes
@pytest.mark.asyncio
async def test_candidate_version_metadata_contains_patch_ids_and_hashes(previewer, repo, tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("# Role\n", encoding="utf-8")
    
    p = build_basic_patch_item("p_meta", "approved", {})
    await repo.save_human_review_item(**p)
    
    res = await previewer.build_preview(
        prompt_name="final_expense_alex",
        prompt_path=prompt_file,
        patch_ids=["p_meta"],
        output_dir=tmp_path,
        run_gates=True,
        create_candidate_version=True
    )
    
    assert res.candidate_prompt_version_id is not None
    db_record = await repo.get_prompt_version(res.candidate_prompt_version_id)
    meta = db_record["qa_thresholds"]["metadata"]["metadata"]
    
    assert meta["source_prompt_path"] == str(prompt_file)
    assert meta["source_prompt_hash"] == res.source_prompt_hash
    assert meta["patched_prompt_hash"] == res.patched_prompt_hash
    assert "p_meta" in meta["patch_review_item_ids"]
    assert meta["active_runtime"] is False


# 16. test_cli_preview_all_approved_outputs_json
def test_cli_preview_all_approved_outputs_json(tmp_path):
    # Setup approved item in temp db
    db_dir = tmp_path / "db"
    out_dir = tmp_path / "out"
    repo = Repository(data_dir=db_dir)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(repo.save_human_review_item(**build_basic_patch_item("p_cli", "approved", {})))
    finally:
        loop.close()
        
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("# Role\n", encoding="utf-8")
    
    cmd = [
        sys.executable,
        "scripts/preview_prompt_patch.py",
        "--prompt-name", "final_expense_alex",
        "--prompt-file", str(prompt_file),
        "--all-approved",
        "--output-dir", str(out_dir),
        "--data-dir", str(db_dir)
    ]
    
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"Stderr: {proc.stderr}"
    
    res = json.loads(proc.stdout)
    assert res["prompt_name"] == "final_expense_alex"
    assert res["patches_applied"] == 1


# 17. test_cli_requires_patch_selection
def test_cli_requires_patch_selection(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("# Role\n", encoding="utf-8")
    
    cmd = [
        sys.executable,
        "scripts/preview_prompt_patch.py",
        "--prompt-name", "final_expense_alex",
        "--prompt-file", str(prompt_file),
        "--output-dir", str(tmp_path)
    ]
    
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert proc.returncode == 1
    err = json.loads(proc.stderr)
    assert "Must specify either --patch-id or --all-approved" in err["error"]


# 18. test_cli_create_candidate_version
def test_cli_create_candidate_version(tmp_path):
    db_dir = tmp_path / "db"
    out_dir = tmp_path / "out"
    repo = Repository(data_dir=db_dir)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(repo.save_human_review_item(**build_basic_patch_item("p_cliv", "approved", {})))
    finally:
        loop.close()
        
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("# Role\n", encoding="utf-8")
    
    cmd = [
        sys.executable,
        "scripts/preview_prompt_patch.py",
        "--prompt-name", "final_expense_alex",
        "--prompt-file", str(prompt_file),
        "--all-approved",
        "--create-candidate-version",
        "--output-dir", str(out_dir),
        "--data-dir", str(db_dir)
    ]
    
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert proc.returncode == 0
    res = json.loads(proc.stdout)
    assert res["candidate_prompt_version_id"] is not None


# 19. test_skip_gates_prevents_candidate_version
def test_skip_gates_prevents_candidate_version(tmp_path):
    db_dir = tmp_path / "db"
    out_dir = tmp_path / "out"
    repo = Repository(data_dir=db_dir)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(repo.save_human_review_item(**build_basic_patch_item("p_clisg", "approved", {})))
    finally:
        loop.close()
        
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("# Role\n", encoding="utf-8")
    
    cmd = [
        sys.executable,
        "scripts/preview_prompt_patch.py",
        "--prompt-name", "final_expense_alex",
        "--prompt-file", str(prompt_file),
        "--all-approved",
        "--skip-gates",
        "--create-candidate-version",
        "--output-dir", str(out_dir),
        "--data-dir", str(db_dir)
    ]
    
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert proc.returncode == 0
    res = json.loads(proc.stdout)
    assert res["candidate_prompt_version_id"] is None
    assert any("not created" in w for w in res["warnings"])


# 20. test_no_live_prompt_file_modified
@pytest.mark.asyncio
async def test_no_live_prompt_file_modified(previewer, repo, tmp_path):
    live_prompt_file = Path("prompts/final_expense_alex.md")
    
    # Record original content if exists
    orig_content = ""
    if live_prompt_file.exists():
        orig_content = live_prompt_file.read_text(encoding="utf-8")
        
    p = build_basic_patch_item("p_nomod", "approved", {})
    await repo.save_human_review_item(**p)
    
    await previewer.build_preview(
        prompt_name="final_expense_alex",
        prompt_path=live_prompt_file,
        patch_ids=["p_nomod"],
        output_dir=tmp_path,
        run_gates=False
    )
    
    if live_prompt_file.exists():
        assert live_prompt_file.read_text(encoding="utf-8") == orig_content


# 21. test_rejects_output_path_that_overwrites_live_prompt
@pytest.mark.asyncio
async def test_rejects_output_path_that_overwrites_live_prompt(previewer, repo, tmp_path):
    live_prompt_file = Path("prompts/final_expense_alex.md")
    p = build_basic_patch_item("p_overwrite", "approved", {})
    await repo.save_human_review_item(**p)
    
    with pytest.raises(ValueError):
        await previewer.build_preview(
            prompt_name="final_expense_alex",
            prompt_path=live_prompt_file,
            patch_ids=["p_overwrite"],
            output_dir="prompts",
            run_gates=False
        )


# 22. test_patch_payload_with_prompt_patch_approved_true_is_valid
@pytest.mark.asyncio
async def test_patch_payload_with_prompt_patch_approved_true_is_valid(previewer, repo):
    p = build_basic_patch_item("p_fapproved", "approved", {"prompt_patch_approved": True})
    await repo.save_human_review_item(**p)
    loaded = await previewer.load_approved_patch_items(["p_fapproved"])
    assert len(loaded) == 1


# 23. test_approved_status_without_prompt_patch_approved_but_review_history_is_valid
@pytest.mark.asyncio
async def test_approved_status_without_prompt_patch_approved_but_review_history_is_valid(previewer, repo):
    review_history = [
        {"action": "approved", "reviewer": "Jimmy", "new_status": "approved"}
    ]
    p = build_basic_patch_item("p_hist", "approved", {"prompt_patch_approved": False}, review_history=review_history)
    await repo.save_human_review_item(**p)
    loaded = await previewer.load_approved_patch_items(["p_hist"])
    assert len(loaded) == 1


# 24. test_pending_patch_with_prompt_patch_approved_true_is_rejected
@pytest.mark.asyncio
async def test_pending_patch_with_prompt_patch_approved_true_is_rejected(previewer, repo):
    p = build_basic_patch_item("p_pending_true", "pending", {"prompt_patch_approved": True})
    await repo.save_human_review_item(**p)
    
    with pytest.raises(ValueError):
        await previewer.load_approved_patch_items(["p_pending_true"])


# 25. test_build_preview_respects_limit
@pytest.mark.asyncio
async def test_build_preview_respects_limit(previewer, repo, tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("# Role\n", encoding="utf-8")
    
    p1 = build_basic_patch_item("p_limit1", "approved", {"proposed_text": "- Rule 1"})
    p2 = build_basic_patch_item("p_limit2", "approved", {"proposed_text": "- Rule 2"})
    await repo.save_human_review_item(**p1)
    await repo.save_human_review_item(**p2)
    
    res = await previewer.build_preview(
        prompt_name="final_expense_alex",
        prompt_path=prompt_file,
        patch_ids=None,
        output_dir=tmp_path,
        run_gates=False,
        limit=1
    )
    assert res.patches_applied == 1
    assert len(res.patch_review_item_ids) == 1

