"""Unit tests for the Dana Prompt Versioning System."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
import pytest

from storage.repository import Repository
from prompts.versioning import PromptVersionManager, PromptValidationResult


@pytest.fixture
def repo(tmp_path):
    """Return a Repository backed by a temporary JsonlStore."""
    return Repository(data_dir=tmp_path)


@pytest.fixture
def manager(repo):
    """Return a PromptVersionManager using a temporary Repository."""
    return PromptVersionManager(repository=repo)


# 1. test_compute_content_hash_stable
def test_compute_content_hash_stable(manager):
    """Same content with normalized line endings gives same hash, stripping trailing whitespace."""
    content_crlf = "role: agent\r\nname: alex\r\n\r\n"
    content_lf = "role: agent\nname: alex\n\n"
    content_trailing_spaces = "role: agent    \nname: alex  \n\n"

    hash_crlf = manager.compute_content_hash(content_crlf)
    hash_lf = manager.compute_content_hash(content_lf)
    hash_spaces = manager.compute_content_hash(content_trailing_spaces)

    assert hash_crlf == hash_lf
    assert hash_lf == hash_spaces
    assert len(hash_lf) == 64  # SHA-256 length


# 2. test_snapshot_prompt_file_creates_prompt_version
@pytest.mark.asyncio
async def test_snapshot_prompt_file_creates_prompt_version(manager, repo, tmp_path):
    """Verify that snapshotting a prompt file creates a PromptVersion record and leaves original file unchanged."""
    prompt_file = tmp_path / "final_expense_alex.md"
    original_content = "# Hello Agent\nVerify age and state.\n"
    prompt_file.write_text(original_content, encoding="utf-8")

    res = await manager.snapshot_prompt_file(
        prompt_name="final_expense_alex",
        file_path=prompt_file,
        created_by="Jimmy",
        notes="First snapshot",
        status="snapshot"
    )

    assert res.prompt_version_id is not None
    assert res.prompt_name == "final_expense_alex"
    assert res.version is not None
    assert res.content_hash == manager.compute_content_hash(original_content)
    assert res.changed_since_last_snapshot is True

    # Assert record is saved in storage
    record = await repo.get_prompt_version(res.prompt_version_id)
    assert record is not None
    assert record["sha"] == res.content_hash

    # Assert live file is unchanged
    assert prompt_file.read_text(encoding="utf-8") == original_content


# 3. test_snapshot_dedupes_identical_latest_version
@pytest.mark.asyncio
async def test_snapshot_dedupes_identical_latest_version(manager, tmp_path):
    """Snapshotting the same file content twice should reuse the latest version and not duplicate."""
    prompt_file = tmp_path / "final_expense_alex.md"
    content = "# Hello Agent\nConfirm age.\n"
    prompt_file.write_text(content, encoding="utf-8")

    res1 = await manager.snapshot_prompt_file("final_expense_alex", prompt_file, "Jimmy")
    res2 = await manager.snapshot_prompt_file("final_expense_alex", prompt_file, "Sarah")

    assert res1.prompt_version_id == res2.prompt_version_id
    assert res2.changed_since_last_snapshot is False
    assert any("reused" in w for w in res2.warnings)


# 4. test_snapshot_detects_changed_prompt
@pytest.mark.asyncio
async def test_snapshot_detects_changed_prompt(manager, tmp_path):
    """Snapshotting after modifying the prompt file creates a new version with parent relation."""
    prompt_file = tmp_path / "final_expense_alex.md"
    content1 = "# Greeting\nSay hi.\n"
    prompt_file.write_text(content1, encoding="utf-8")

    res1 = await manager.snapshot_prompt_file("final_expense_alex", prompt_file, "Jimmy")

    content2 = "# Greeting\nSay hello.\n"
    prompt_file.write_text(content2, encoding="utf-8")

    res2 = await manager.snapshot_prompt_file("final_expense_alex", prompt_file, "Sarah")

    assert res1.prompt_version_id != res2.prompt_version_id
    assert res2.changed_since_last_snapshot is True
    assert res2.previous_version_id == res1.prompt_version_id


# 5. test_create_prompt_version_draft
@pytest.mark.asyncio
async def test_create_prompt_version_draft(manager, repo):
    """Creating prompt version programmatically creates draft with expected metadata."""
    res = await manager.create_prompt_version(
        prompt_name="custom_prompt",
        content="# Custom logic\n",
        created_by="Sarah",
        status="draft",
        notes="Programmatic draft creation",
        metadata={"custom_key": "custom_val"}
    )

    assert res.status == "draft"
    
    # Retrieve DB record
    db_record = await repo.get_prompt_version(res.prompt_version_id)
    qa = db_record.get("qa_thresholds") or {}
    assert qa.get("metadata", {}).get("created_from") == "prompt_versioning"
    assert qa.get("metadata", {}).get("notes") == "Programmatic draft creation"
    assert qa.get("metadata", {}).get("metadata", {}).get("custom_key") == "custom_val"


# 6. test_detect_prompt_drift_false_when_same
@pytest.mark.asyncio
async def test_detect_prompt_drift_false_when_same(manager, tmp_path):
    """Drift is false if the file content matches the latest snapshot."""
    prompt_file = tmp_path / "final_expense.md"
    content = "# Content\nKeep responses brief.\n"
    prompt_file.write_text(content, encoding="utf-8")

    await manager.snapshot_prompt_file("final_expense", prompt_file, "Jimmy")

    drift_res = await manager.detect_prompt_drift("final_expense", prompt_file)
    assert drift_res["drift"] is False
    assert "matches the latest snapshot" in drift_res["message"]


# 7. test_detect_prompt_drift_true_when_file_changes
@pytest.mark.asyncio
async def test_detect_prompt_drift_true_when_file_changes(manager, tmp_path):
    """Drift is true if the file content has changed since the latest snapshot."""
    prompt_file = tmp_path / "final_expense.md"
    content = "# Content\nKeep responses brief.\n"
    prompt_file.write_text(content, encoding="utf-8")

    await manager.snapshot_prompt_file("final_expense", prompt_file, "Jimmy")

    # Modify file
    prompt_file.write_text("# Content\nKeep responses very brief.\n", encoding="utf-8")

    drift_res = await manager.detect_prompt_drift("final_expense", prompt_file)
    assert drift_res["drift"] is True
    assert "has drift" in drift_res["message"]


# 8. test_diff_prompt_versions_outputs_unified_diff
@pytest.mark.asyncio
async def test_diff_prompt_versions_outputs_unified_diff(manager):
    """Unified diff outputs lines, counts, and flags safety-relevant wording changes."""
    res1 = await manager.create_prompt_version(
        prompt_name="test_prompt",
        content="line 1\nline 2\nNEVER quote exact price or monthly cost\n",
        created_by="Jimmy"
    )

    res2 = await manager.create_prompt_version(
        prompt_name="test_prompt",
        content="line 1\nline 2 modified\nNEVER quote exact price or monthly cost\nAlso, ask for consent before you transfer.\n",
        created_by="Sarah"
    )

    diff_res = await manager.diff_prompt_versions(res1.prompt_version_id, res2.prompt_version_id)
    assert "line 2 modified" in diff_res.unified_diff
    assert diff_res.added_lines > 0
    
    # Check safety keywords flagged
    safety_changes = diff_res.summary["safety_relevant_changes"]
    assert "transfer" in safety_changes or "consent" in safety_changes


# 9. test_validate_prompt_blocks_you_qualify
@pytest.mark.asyncio
async def test_validate_prompt_blocks_you_qualify(manager):
    """Assert critical failure when prompt content instructs saying 'you qualify' without negation."""
    unsafe_content = "# Guidelines\nWhen they answer, say 'you qualify' for our special rate."
    validation = await manager.validate_prompt_content(unsafe_content)
    assert validation.passed is False
    assert any("qualify" in cf for cf in validation.critical_failures)


# 10. test_validate_prompt_blocks_price_quote_instruction
@pytest.mark.asyncio
async def test_validate_prompt_blocks_price_quote_instruction(manager):
    """Assert critical failure when prompt content instructs quoting exact monthly premium."""
    unsafe_content = "# Wording\nTell them the exact price quote is $50 per month."
    validation = await manager.validate_prompt_content(unsafe_content)
    assert validation.passed is False
    assert any("quote" in cf or "price" in cf for cf in validation.critical_failures)


# 11. test_validate_prompt_blocks_licensed_claim_instruction
@pytest.mark.asyncio
async def test_validate_prompt_blocks_licensed_claim_instruction(manager):
    """Assert critical failure when prompt content instructs saying the agent is licensed."""
    unsafe_content = "# Role\nYou are a licensed agent representing the state."
    validation = await manager.validate_prompt_content(unsafe_content)
    assert validation.passed is False
    assert any("licensed" in cf for cf in validation.critical_failures)


# 12. test_validate_prompt_blocks_human_claim_instruction
@pytest.mark.asyncio
async def test_validate_prompt_blocks_human_claim_instruction(manager):
    """Assert critical failure when prompt content instructs claiming the agent is human."""
    unsafe_content = "# Identity\nIf asked, you must pretend you are a real person calling from the office."
    validation = await manager.validate_prompt_content(unsafe_content)
    assert validation.passed is False
    assert any("human" in cf or "real person" in cf for cf in validation.critical_failures)


# 13. test_validate_prompt_warns_missing_required_rules
@pytest.mark.asyncio
async def test_validate_prompt_warns_missing_required_rules(manager):
    """Assert medium warnings when required guidelines (DNC, wrong-number, consent) are missing."""
    empty_prompt = "Just check if they are open."
    validation = await manager.validate_prompt_content(empty_prompt)
    assert len(validation.medium_warnings) > 0
    assert any("consent" in w for w in validation.medium_warnings)
    assert any("DNC" in w or "do not call" in w for w in validation.medium_warnings)


# 14. test_validate_current_final_expense_prompt_passes_or_reports_no_critical_failures
@pytest.mark.asyncio
async def test_validate_current_final_expense_prompt_passes_or_reports_no_critical_failures(manager):
    """Validate prompts/final_expense_alex.md and assert 0 critical failures."""
    prompt_path = Path("prompts/final_expense_alex.md")
    content = prompt_path.read_text(encoding="utf-8")
    validation = await manager.validate_prompt_content(content)
    assert len(validation.critical_failures) == 0


# 15. test_export_prompt_version_safe_path
@pytest.mark.asyncio
async def test_export_prompt_version_safe_path(manager, tmp_path):
    """Verify exporting a version writes the correct content to a safe path."""
    res = await manager.create_prompt_version(
        prompt_name="test_export",
        content="# Exported Content\n",
        created_by="Jimmy"
    )

    out_file = tmp_path / "exports" / "test_export_v1.md"
    exported_path = await manager.export_prompt_version(res.prompt_version_id, out_file)

    assert Path(exported_path).exists()
    assert Path(exported_path).read_text(encoding="utf-8") == "# Exported Content\n"


# 16. test_export_prompt_version_refuses_live_prompt_overwrite
@pytest.mark.asyncio
async def test_export_prompt_version_refuses_live_prompt_overwrite(manager):
    """Verify that exporting to a production prompt file raises ValueError."""
    res = await manager.create_prompt_version(
        prompt_name="test_block",
        content="# Malicious content\n",
        created_by="BadActor"
    )

    with pytest.raises(ValueError, match="Refusing to overwrite"):
        await manager.export_prompt_version(res.prompt_version_id, "prompts/final_expense_alex.md")


# 17. test_generate_prompt_report_writes_json_and_markdown
@pytest.mark.asyncio
async def test_generate_prompt_report_writes_json_and_markdown(manager, tmp_path):
    """Verify report generation writes markdown and JSON files containing required sections."""
    await manager.create_prompt_version(
        prompt_name="final_expense_alex",
        content="# Dana prompt\nLacks consent and wrong number rules.",
        created_by="Jimmy"
    )

    json_path, md_path = await manager.generate_prompt_report("final_expense_alex", tmp_path)

    assert Path(json_path).exists()
    assert Path(md_path).exists()

    # Verify JSON content
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert data["prompt_name"] == "final_expense_alex"
        assert "versions" in data

    # Verify Markdown content
    md_text = Path(md_path).read_text(encoding="utf-8")
    assert "# Dana Prompt Version Report" in md_text
    assert "## Version History" in md_text
    assert "## Latest Validation Summary" in md_text
    assert "## Drift Status" in md_text
    assert "## Safety-Relevant Notes" in md_text
    assert "## Recommended Next Actions" in md_text


# 18. test_cli_snapshot_outputs_json
def test_cli_snapshot_outputs_json(tmp_path):
    """Verify CLI snapshot command writes JSON to stdout, exits 0, and operates in temporary storage."""
    temp_prompt = tmp_path / "temp_prompt.md"
    temp_prompt.write_text("# Temp prompt content\n", encoding="utf-8")

    cmd = [
        sys.executable,
        "scripts/manage_prompt_versions.py",
        "snapshot",
        "--name", "final_expense_alex",
        "--file", str(temp_prompt),
        "--created-by", "CLI_Test",
        "--notes", "CLI Notes"
    ]

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    env["DANA_DATA_DIR"] = str(tmp_path)

    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert "prompt_version_id" in data
    assert data["prompt_name"] == "final_expense_alex"
    assert data["changed_since_last_snapshot"] is True


# 19. test_cli_validate_file_outputs_json
def test_cli_validate_file_outputs_json(tmp_path):
    """Verify CLI validate command outputs validation JSON and exits 0."""
    temp_prompt = tmp_path / "temp_prompt.md"
    temp_prompt.write_text("# Temp prompt content\n", encoding="utf-8")

    cmd = [
        sys.executable,
        "scripts/manage_prompt_versions.py",
        "validate",
        "--file", str(temp_prompt)
    ]

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    env["DANA_DATA_DIR"] = str(tmp_path)

    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert "passed" in data
    assert "critical_failures" in data


# 20. test_cli_diff_outputs_json
def test_cli_diff_outputs_json(tmp_path):
    """Verify CLI diff command outputs valid diff JSON and exits 0."""
    # Write first file snapshot
    prompt_file = tmp_path / "temp_prompt.md"
    prompt_file.write_text("# Version A\n", encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    env["DANA_DATA_DIR"] = str(tmp_path)

    # CLI Snapshot 1
    cmd1 = [
        sys.executable,
        "scripts/manage_prompt_versions.py",
        "snapshot",
        "--name", "final_expense_alex",
        "--file", str(prompt_file),
        "--created-by", "Jimmy"
    ]
    res1 = subprocess.run(cmd1, capture_output=True, text=True, env=env)
    assert res1.returncode == 0
    id1 = json.loads(res1.stdout)["prompt_version_id"]

    # CLI Snapshot 2
    prompt_file.write_text("# Version B\n", encoding="utf-8")
    cmd2 = [
        sys.executable,
        "scripts/manage_prompt_versions.py",
        "snapshot",
        "--name", "final_expense_alex",
        "--file", str(prompt_file),
        "--created-by", "Sarah"
    ]
    res2 = subprocess.run(cmd2, capture_output=True, text=True, env=env)
    assert res2.returncode == 0
    id2 = json.loads(res2.stdout)["prompt_version_id"]

    # CLI Diff
    cmd3 = [
        sys.executable,
        "scripts/manage_prompt_versions.py",
        "diff",
        "--from", id1,
        "--to", id2
    ]
    res3 = subprocess.run(cmd3, capture_output=True, text=True, env=env)
    assert res3.returncode == 0
    diff_data = json.loads(res3.stdout)
    assert "from_version_id" in diff_data
    assert "to_version_id" in diff_data
    assert "unified_diff" in diff_data


# 21. test_no_live_prompt_file_modified
@pytest.mark.asyncio
async def test_no_live_prompt_file_modified(manager, tmp_path):
    """Operations must never modify prompts/final_expense_alex.md content."""
    live_path = Path("prompts/final_expense_alex.md")
    content_before = live_path.read_text(encoding="utf-8")

    # Snapshot, Validate, Report
    await manager.snapshot_prompt_file("final_expense_alex", live_path, "Jimmy")
    await manager.validate_prompt_content(content_before)
    await manager.generate_prompt_report("final_expense_alex", tmp_path)

    content_after = live_path.read_text(encoding="utf-8")
    assert content_before == content_after
