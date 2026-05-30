"""Unit tests for the Dana Safe Prompt Patch Candidate Generator."""

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
from prompts.versioning import PromptVersionManager
from prompts.patch_generator import PromptPatchGenerator, PromptPatchCandidate


@pytest.fixture
def repo(tmp_path):
    """Return a Repository backed by a temporary JsonlStore."""
    return Repository(data_dir=tmp_path)


@pytest.fixture
def version_manager(repo):
    """Return a PromptVersionManager backed by a temporary Repository."""
    return PromptVersionManager(repository=repo)


@pytest.fixture
def generator(repo, version_manager):
    """Return a PromptPatchGenerator using temporary repository and version manager."""
    return PromptPatchGenerator(repository=repo, prompt_version_manager=version_manager)


# Helper to build a basic candidate for validation tests
def build_basic_candidate(proposed_text: str, patch_type: str = "transfer_consent_rule") -> PromptPatchCandidate:
    return PromptPatchCandidate(
        patch_id=str(uuid.uuid4()),
        prompt_name="final_expense_alex",
        patch_type=patch_type,
        title="Test patch",
        problem_summary="Test problem",
        proposed_change_summary="Test change",
        proposed_text=proposed_text,
        rationale="Test rationale",
        source_evidence=[],
        expected_benefit="Test benefit",
        risk_level="low",
        compliance_impact="medium positive",
        recommended_tests=["eval_cases"],
        labels={},
        payload_hash="mock_hash",
        created_at=datetime.now(timezone.utc)
    )


# 1. test_generates_transfer_consent_patch_from_failure
@pytest.mark.asyncio
async def test_generates_transfer_consent_patch_from_failure(generator, repo):
    """Failure type transfer_before_consent generates transfer_consent_rule candidate."""
    await repo.save_human_review_item(
        item_type="failure_example",
        payload={"failure_type": "transfer_before_consent"},
        status="approved"
    )

    bundle = await generator.gather_sources()
    candidates = generator.generate_candidates_from_sources("final_expense_alex", "", bundle)

    assert len(candidates) >= 1
    consent_patches = [c for c in candidates if c.patch_type == "transfer_consent_rule"]
    assert len(consent_patches) == 1
    assert "Dana must ask permission before transferring." in consent_patches[0].proposed_text


# 2. test_generates_dnc_patch_from_failure
@pytest.mark.asyncio
async def test_generates_dnc_patch_from_failure(generator, repo):
    """Failure type continued_talking_after_dnc generates dnc_handling_rule candidate."""
    await repo.save_human_review_item(
        item_type="failure_example",
        payload={"failure_type": "continued_talking_after_dnc"},
        status="approved"
    )

    bundle = await generator.gather_sources()
    candidates = generator.generate_candidates_from_sources("final_expense_alex", "", bundle)

    dnc_patches = [c for c in candidates if c.patch_type == "dnc_handling_rule"]
    assert len(dnc_patches) == 1
    assert "stop calling, do not call" in dnc_patches[0].proposed_text


# 3. test_generates_wrong_number_patch_from_failure
@pytest.mark.asyncio
async def test_generates_wrong_number_patch_from_failure(generator, repo):
    """Failure type continued_talking_after_wrong_number generates wrong_number_handling_rule candidate."""
    await repo.save_human_review_item(
        item_type="failure_example",
        payload={"failure_type": "continued_talking_after_wrong_number"},
        status="approved"
    )

    bundle = await generator.gather_sources()
    candidates = generator.generate_candidates_from_sources("final_expense_alex", "", bundle)

    wn_patches = [c for c in candidates if c.patch_type == "wrong_number_handling_rule"]
    assert len(wn_patches) == 1
    assert "apologize briefly, mark wrong number" in wn_patches[0].proposed_text


# 4. test_generates_price_question_patch_from_failure
@pytest.mark.asyncio
async def test_generates_price_question_patch_from_failure(generator, repo):
    """Failure type agent_price_quote generates price_question_rule candidate."""
    await repo.save_human_review_item(
        item_type="failure_example",
        payload={"failure_type": "agent_price_quote"},
        status="approved"
    )

    bundle = await generator.gather_sources()
    candidates = generator.generate_candidates_from_sources("final_expense_alex", "", bundle)

    price_patches = [c for c in candidates if c.patch_type == "price_question_rule"]
    assert len(price_patches) == 1
    assert "Do not quote an exact price" in price_patches[0].proposed_text


# 5. test_generates_licensed_question_patch_from_failure
@pytest.mark.asyncio
async def test_generates_licensed_question_patch_from_failure(generator, repo):
    """Failure type agent_claims_licensed generates licensed_question_rule candidate."""
    await repo.save_human_review_item(
        item_type="failure_example",
        payload={"failure_type": "agent_claims_licensed"},
        status="approved"
    )

    bundle = await generator.gather_sources()
    candidates = generator.generate_candidates_from_sources("final_expense_alex", "", bundle)

    lic_patches = [c for c in candidates if c.patch_type == "licensed_question_rule"]
    assert len(lic_patches) == 1
    assert "Dana must not claim she is licensed." in lic_patches[0].proposed_text


# 6. test_generates_identity_question_patch_from_failure
@pytest.mark.asyncio
async def test_generates_identity_question_patch_from_failure(generator, repo):
    """Failure type asks_if_real generates identity_question_rule candidate."""
    await repo.save_human_review_item(
        item_type="failure_example",
        payload={"failure_type": "asks_if_real"},
        status="approved"
    )

    bundle = await generator.gather_sources()
    candidates = generator.generate_candidates_from_sources("final_expense_alex", "", bundle)

    id_patches = [c for c in candidates if c.patch_type == "identity_question_rule"]
    assert len(id_patches) == 1
    assert "must not claim to be human" in id_patches[0].proposed_text


# 7. test_generates_one_question_patch_from_failure
@pytest.mark.asyncio
async def test_generates_one_question_patch_from_failure(generator, repo):
    """Failure type multiple_questions generates one_question_rule candidate."""
    await repo.save_human_review_item(
        item_type="failure_example",
        payload={"failure_type": "multiple_questions"},
        status="approved"
    )

    bundle = await generator.gather_sources()
    candidates = generator.generate_candidates_from_sources("final_expense_alex", "", bundle)

    oq_patches = [c for c in candidates if c.patch_type == "one_question_rule"]
    assert len(oq_patches) == 1
    assert "Ask one question per turn." in oq_patches[0].proposed_text


# 8. test_generates_disqualification_patch_from_failure
@pytest.mark.asyncio
async def test_generates_disqualification_patch_from_failure(generator, repo):
    """Failure type nursing_home_mishandled generates disqualification_rule candidate."""
    await repo.save_human_review_item(
        item_type="failure_example",
        payload={"failure_type": "nursing_home_mishandled"},
        status="approved"
    )

    bundle = await generator.gather_sources()
    candidates = generator.generate_candidates_from_sources("final_expense_alex", "", bundle)

    disq_patches = [c for c in candidates if c.patch_type == "disqualification_rule"]
    assert len(disq_patches) == 1
    assert "nursing home/assisted living" in disq_patches[0].proposed_text


# 9. test_generates_callback_patch_from_failure
@pytest.mark.asyncio
async def test_generates_callback_patch_from_failure(generator, repo):
    """Failure type callback_requested_no_tool generates callback_rule candidate."""
    await repo.save_human_review_item(
        item_type="failure_example",
        payload={"failure_type": "callback_requested_no_tool"},
        status="approved"
    )

    bundle = await generator.gather_sources()
    candidates = generator.generate_candidates_from_sources("final_expense_alex", "", bundle)

    cb_patches = [c for c in candidates if c.patch_type == "callback_rule"]
    assert len(cb_patches) == 1
    assert "asks for a callback or says they are busy" in cb_patches[0].proposed_text


# 10. test_generates_example_response_patch_from_approved_training_example
@pytest.mark.asyncio
async def test_generates_example_response_patch_from_approved_training_example(generator, repo):
    """Approved TrainingExample with use_for containing prompt generates add_example_response candidate."""
    await repo.save_training_example(
        source_id="src_123",
        stage="greeting",
        user_text="Who is this?",
        ideal_response="This is Alex with American Beneficiary.",
        approved_by="Jimmy",
        use_for=["prompt"]
    )

    bundle = await generator.gather_sources()
    candidates = generator.generate_candidates_from_sources("final_expense_alex", "", bundle)

    ex_patches = [c for c in candidates if c.patch_type == "add_example_response"]
    assert len(ex_patches) == 1
    assert "This is Alex with American Beneficiary" in ex_patches[0].proposed_text


# 11. test_patch_validation_blocks_unsafe_price_quote
def test_patch_validation_blocks_unsafe_price_quote(generator):
    """Patch proposed_text containing specific price quote fails validation."""
    unsafe_cand = build_basic_candidate("Say that it costs $29.99 per month.")
    res = generator.validate_patch_candidate(unsafe_cand, "")
    assert res.passed is False
    assert any("price" in cf for cf in res.critical_failures)


# 12. test_patch_validation_blocks_you_qualify
def test_patch_validation_blocks_you_qualify(generator):
    """Patch proposed_text instructing agent to say 'you qualify' fails validation."""
    unsafe_cand = build_basic_candidate("When they ask, say you qualify.")
    res = generator.validate_patch_candidate(unsafe_cand, "")
    assert res.passed is False
    assert any("qualify" in cf for cf in res.critical_failures)


# 13. test_patch_validation_blocks_human_claim
def test_patch_validation_blocks_human_claim(generator):
    """Patch proposed_text instructing agent to claim they are human fails validation."""
    unsafe_cand = build_basic_candidate("Say you are a real person calling from America.")
    res = generator.validate_patch_candidate(unsafe_cand, "")
    assert res.passed is False
    assert any("human" in cf or "real person" in cf for cf in res.critical_failures)


# 14. test_patch_validation_allows_safe_licensed_agent_reference
def test_patch_validation_allows_safe_licensed_agent_reference(generator):
    """Patch proposed_text with safe licensed agent reference passes validation."""
    safe_cand = build_basic_candidate("State: 'A licensed agent can review plan details.'")
    res = generator.validate_patch_candidate(safe_cand, "")
    assert res.passed is True
    assert len(res.critical_failures) == 0


# 15. test_dedupes_existing_pending_prompt_patch
@pytest.mark.asyncio
async def test_dedupes_existing_pending_prompt_patch(generator, repo, tmp_path):
    """Second generation run skips duplicates already pending in review items."""
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("# Role\n", encoding="utf-8")

    await repo.save_human_review_item(
        item_type="failure_example",
        payload={"failure_type": "transfer_before_consent"},
        status="approved"
    )

    # First run: saves candidate
    res1 = await generator.generate_for_prompt("final_expense_alex", prompt_file, output_dir=tmp_path)
    assert res1.candidates_saved == 1
    assert res1.candidates_skipped == 0

    # Second run: skips candidate as duplicate
    res2 = await generator.generate_for_prompt("final_expense_alex", prompt_file, output_dir=tmp_path)
    assert res2.candidates_saved == 0
    assert res2.candidates_skipped == 1
    assert res2.skipped_reasons.get("duplicate prompt_patch") == 1


# 16. test_skips_previously_rejected_prompt_patch
@pytest.mark.asyncio
async def test_skips_previously_rejected_prompt_patch(generator, repo, tmp_path):
    """Skipped candidate should show 'previously rejected' if same payload hash exists in rejected status."""
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("# Role\n", encoding="utf-8")

    await repo.save_human_review_item(
        item_type="failure_example",
        payload={"failure_type": "transfer_before_consent"},
        status="approved"
    )

    # Run once to get candidate payload hash
    bundle = await generator.gather_sources()
    cands = generator.generate_candidates_from_sources("final_expense_alex", "", bundle)
    payload_hash = cands[0].payload_hash

    # Save a rejected item with that hash
    payload = {
        "payload_hash": payload_hash,
        "patch_type": "transfer_consent_rule"
    }
    await repo.save_human_review_item(
        item_type="prompt_patch",
        payload=payload,
        status="rejected"
    )

    res = await generator.generate_for_prompt("final_expense_alex", prompt_file, output_dir=tmp_path)
    assert res.candidates_saved == 0
    assert res.candidates_skipped == 1
    assert res.skipped_reasons.get("previously rejected") == 1


# 17. test_dry_run_does_not_save_review_items
@pytest.mark.asyncio
async def test_dry_run_does_not_save_review_items(generator, repo, tmp_path):
    """Running with save_review_items=False generates report but does not save review items."""
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("# Role\n", encoding="utf-8")

    await repo.save_human_review_item(
        item_type="failure_example",
        payload={"failure_type": "transfer_before_consent"},
        status="approved"
    )

    res = await generator.generate_for_prompt(
        prompt_name="final_expense_alex",
        prompt_path=prompt_file,
        save_review_items=False,
        output_dir=tmp_path
    )

    assert res.candidates_generated == 1
    assert res.candidates_saved == 1  # count generated candidates in results
    assert len(await repo.query_human_review_items({"status": "pending"})) == 0


# 18. test_report_files_are_written
@pytest.mark.asyncio
async def test_report_files_are_written(generator, repo, tmp_path):
    """Generation output directory contains valid JSON and Markdown reports with all sections."""
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("# Role\n", encoding="utf-8")

    await repo.save_human_review_item(
        item_type="failure_example",
        payload={"failure_type": "transfer_before_consent"},
        status="approved"
    )

    res = await generator.generate_for_prompt("final_expense_alex", prompt_file, output_dir=tmp_path)

    json_report = Path(res.report_json_path)
    md_report = Path(res.report_markdown_path)

    assert json_report.exists()
    assert md_report.exists()

    md_text = md_report.read_text(encoding="utf-8")
    assert "# Dana Prompt Patch Candidate Report" in md_text
    assert "## Executive Summary" in md_text
    assert "## Patch Candidates" in md_text
    assert "## Candidate Details" in md_text
    assert "## Skipped Candidates" in md_text
    assert "## Required Next Steps" in md_text


# 19. test_cli_generate_prompt_patches_outputs_json
def test_cli_generate_prompt_patches_outputs_json(tmp_path):
    """CLI script outputs clean JSON on stdout, exits 0, and operates in temporary storage."""
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("# Role\n", encoding="utf-8")

    cmd = [
        sys.executable,
        "scripts/generate_prompt_patches.py",
        "--prompt-name", "final_expense_alex",
        "--prompt-file", str(prompt_file),
        "--output-dir", str(tmp_path)
    ]

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    env["DANA_DATA_DIR"] = str(tmp_path)

    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert data["prompt_name"] == "final_expense_alex"
    assert "candidates_generated" in data


# 20. test_cli_dry_run_creates_no_review_items
def test_cli_dry_run_creates_no_review_items(tmp_path):
    """CLI script with --dry-run option does not write review items to database."""
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("# Role\n", encoding="utf-8")

    cmd = [
        sys.executable,
        "scripts/generate_prompt_patches.py",
        "--prompt-name", "final_expense_alex",
        "--prompt-file", str(prompt_file),
        "--output-dir", str(tmp_path),
        "--dry-run"
    ]

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    env["DANA_DATA_DIR"] = str(tmp_path)

    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 0
    
    # Assert database remains empty of prompt patches
    test_repo = Repository(data_dir=tmp_path)
    items = asyncio.run(test_repo.query_human_review_items({}))
    assert len(items) == 0


# 21. test_no_prompt_file_modified
@pytest.mark.asyncio
async def test_no_prompt_file_modified(generator, repo, tmp_path):
    """Prompt file must not be modified by generating patches."""
    prompt_file = tmp_path / "prompt.md"
    original_content = "# Live Prompt Wording\n"
    prompt_file.write_text(original_content, encoding="utf-8")

    await repo.save_human_review_item(
        item_type="failure_example",
        payload={"failure_type": "transfer_before_consent"},
        status="approved"
    )

    await generator.generate_for_prompt("final_expense_alex", prompt_file, output_dir=tmp_path)

    assert prompt_file.read_text(encoding="utf-8") == original_content


# 22. test_generated_review_item_payload_shape
@pytest.mark.asyncio
async def test_generated_review_item_payload_shape(generator, repo, tmp_path):
    """Review item payload holds validation summaries, recommended tests, and proposed text."""
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("# Role\n", encoding="utf-8")

    await repo.save_human_review_item(
        item_type="failure_example",
        payload={"failure_type": "transfer_before_consent"},
        status="approved"
    )

    res = await generator.generate_for_prompt("final_expense_alex", prompt_file, output_dir=tmp_path)
    saved_id = res.review_item_ids[0]

    item = await repo.get_human_review_item(saved_id)
    assert item["item_type"] == "prompt_patch"
    assert item["status"] == "pending"

    payload = item["payload"]
    assert payload["source"] == "prompt_patch_generator"
    assert payload["patch_type"] == "transfer_consent_rule"
    assert payload["proposed_text"] is not None
    assert "validation" in payload
    assert payload["validation"]["passed"] is True
    assert len(payload["validation"]["critical_failures"]) == 0
    assert payload["payload_hash"] is not None
    assert len(payload["recommended_tests"]) > 0
