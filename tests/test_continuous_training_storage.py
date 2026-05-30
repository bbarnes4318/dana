"""Tests for continuous training system storage and Repository integration.

All tests use pytest's ``tmp_path`` fixture and the JSONL backend.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from storage.repository import Repository


@pytest.fixture
def repo(tmp_path):
    """Return a Repository backed by a tmp_path JsonlStore."""
    return Repository(data_dir=tmp_path)


@pytest.mark.asyncio
async def test_save_and_query_training_source(repo: Repository):
    """save_training_source and query_training_sources work as expected."""
    source_id = await repo.save_training_source(
        source_type="video_transcript",
        source_uri="s3://dana-training/call-recordings/2026-05-30.txt",
        title="Objection Handling Best Practices",
        status="imported",
        metadata={"duration_seconds": 1200}
    )
    assert isinstance(source_id, str)

    # Fetch by primary key
    source = await repo.get_training_source(source_id)
    assert source is not None
    assert source["id"] == source_id
    assert source["source_type"] == "video_transcript"
    assert source["title"] == "Objection Handling Best Practices"
    assert source["metadata"]["duration_seconds"] == 1200

    # Query with filters
    sources = await repo.query_training_sources({"source_type": "video_transcript"})
    assert len(sources) == 1
    assert sources[0]["id"] == source_id


@pytest.mark.asyncio
async def test_save_and_query_training_example(repo: Repository):
    """save_training_example and query_training_examples work as expected."""
    example_id = await repo.save_training_example(
        source_id="src-999",
        call_id="call-123",
        stage="interest_check",
        user_text="I'm not interested in this.",
        ideal_response="I understand you're busy, but this will only take a moment.",
        bad_response="You really should consider this policy.",
        labels={"intent": "not_interested", "tonality": "neutral"},
        approved_by="reviewer-1",
        approved_at=datetime.now(timezone.utc),
        use_for=["fine-tuning", "rag-notes"]
    )
    assert isinstance(example_id, str)

    # Fetch by primary key
    example = await repo.get_training_example(example_id)
    assert example is not None
    assert example["id"] == example_id
    assert example["source_id"] == "src-999"
    assert example["stage"] == "interest_check"
    assert example["labels"]["intent"] == "not_interested"
    assert "fine-tuning" in example["use_for"]

    # Query with filters
    examples = await repo.query_training_examples({"stage": "interest_check"})
    assert len(examples) == 1
    assert examples[0]["id"] == example_id


@pytest.mark.asyncio
async def test_save_and_query_eval_case(repo: Repository):
    """save_eval_case and query_eval_cases work as expected."""
    case_id = await repo.save_eval_case(
        stage="transfer_consent",
        prospect_utterance="Sure, put them on the line.",
        expected_behavior="Trigger feTransfer tool",
        must_include=["stay right there", "connect"],
        must_not_include=["price", "quote"],
        expected_tool="feTransfer",
        severity="critical"
    )
    assert isinstance(case_id, str)

    # Fetch by primary key
    case = await repo.get_eval_case(case_id)
    assert case is not None
    assert case["id"] == case_id
    assert case["stage"] == "transfer_consent"
    assert case["expected_tool"] == "feTransfer"
    assert case["severity"] == "critical"
    assert "price" in case["must_not_include"]

    # Query with filters
    cases = await repo.query_eval_cases({"severity": "critical"})
    assert len(cases) == 1
    assert cases[0]["id"] == case_id


@pytest.mark.asyncio
async def test_save_and_query_human_review_item(repo: Repository):
    """save_human_review_item and query_human_review_items work as expected."""
    item_id = await repo.save_human_review_item(
        item_type="objection_mismatch",
        payload={"call_id": "call-123", "utterance": "No thanks"},
        status="pending",
        reviewer=None,
        review_notes=None
    )
    assert isinstance(item_id, str)

    # Fetch by primary key
    item = await repo.get_human_review_item(item_id)
    assert item is not None
    assert item["id"] == item_id
    assert item["item_type"] == "objection_mismatch"
    assert item["status"] == "pending"
    assert item["payload"]["utterance"] == "No thanks"

    # Query with filters
    items = await repo.query_human_review_items({"status": "pending"})
    assert len(items) == 1
    assert items[0]["id"] == item_id


@pytest.mark.asyncio
async def test_other_schemas_validation_and_storage(repo: Repository):
    """Validate other new models PromptVersion, DeploymentExperiment, and CallOutcomeLabel."""
    # Test PromptVersion
    pv_id = await repo.save_prompt_version(
        file_path="prompts/final_expense_alex.md",
        sha="abc123sha",
        created_by="admin-user",
        change_reason="Add new compliance rules",
        qa_thresholds={"compliance": 0.9},
        canary_status="active"
    )
    assert isinstance(pv_id, str)
    pv = await repo.get_prompt_version(pv_id)
    assert pv is not None
    assert pv["sha"] == "abc123sha"

    # Test DeploymentExperiment
    de_id = await repo.save_deployment_experiment(
        experiment_name="Prompt V2 Canary",
        prompt_version_id=pv_id,
        traffic_percent=10.0,
        status="running",
        metrics={"transfers": 12, "errors": 0},
        started_at=datetime.now(timezone.utc)
    )
    assert isinstance(de_id, str)
    de = await repo.get_deployment_experiment(de_id)
    assert de is not None
    assert de["experiment_name"] == "Prompt V2 Canary"

    # Test CallOutcomeLabel
    col_id = await repo.save_call_outcome_label(
        call_id="call-xyz",
        campaign_id="camp-1",
        outcome="transferred",
        sold=True,
        issued=False,
        transfer_quality_score=9.5,
        agent_feedback="Very good lead, sold quickly.",
        labels={"lead_quality": "excellent"}
    )
    assert isinstance(col_id, str)
    col = await repo.get_call_outcome_label(col_id)
    assert col is not None
    assert col["sold"] is True
    assert col["transfer_quality_score"] == 9.5


@pytest.mark.asyncio
async def test_list_recent_and_pending_helpers(repo: Repository):
    """Verify that the list_recent_* and list_pending_* helpers retrieve data correctly."""
    # 1. Training Sources
    s1 = await repo.save_training_source(source_type="doc", source_uri="uri1", title="T1", status="imported")
    s2 = await repo.save_training_source(source_type="doc", source_uri="uri2", title="T2", status="imported")
    sources = await repo.list_recent_training_sources(limit=50)
    assert len(sources) >= 2
    # Verify newest first (s2 should be before s1)
    ids = [s["id"] for s in sources]
    assert ids[0] == s2
    assert ids[1] == s1

    # 2. Training Examples
    ex1 = await repo.save_training_example(source_id="src1", stage="stage1", user_text="u1", ideal_response="i1")
    ex2 = await repo.save_training_example(source_id="src1", stage="stage1", user_text="u2", ideal_response="i2")
    examples = await repo.list_recent_training_examples(limit=50)
    assert len(examples) >= 2
    assert [e["id"] for e in examples][:2] == [ex2, ex1]

    # 3. Eval Cases
    ev1 = await repo.save_eval_case(stage="stage1", prospect_utterance="p1", expected_behavior="eb1", severity="high")
    ev2 = await repo.save_eval_case(stage="stage1", prospect_utterance="p2", expected_behavior="eb2", severity="high")
    eval_cases = await repo.list_recent_eval_cases(limit=50)
    assert len(eval_cases) >= 2
    assert [ev["id"] for ev in eval_cases][:2] == [ev2, ev1]

    # 4. Human Review Items (Pending vs Completed)
    hr1 = await repo.save_human_review_item(item_type="type1", status="pending", payload={"k": "v"})
    hr2 = await repo.save_human_review_item(item_type="type1", status="completed", payload={"k": "v"})
    hr3 = await repo.save_human_review_item(item_type="type1", status="pending", payload={"k": "v"})
    
    pending_items = await repo.list_pending_human_review_items(limit=50)
    assert len(pending_items) == 2
    assert hr2 not in [item["id"] for item in pending_items]
    assert [item["id"] for item in pending_items] == [hr3, hr1]  # Newest first

    # 5. Deployment Experiments
    de1 = await repo.save_deployment_experiment(experiment_name="Exp 1", traffic_percent=50.0, status="completed")
    de2 = await repo.save_deployment_experiment(experiment_name="Exp 2", traffic_percent=50.0, status="running")
    experiments = await repo.list_recent_deployment_experiments(limit=50)
    assert len(experiments) >= 2
    assert [de["id"] for de in experiments][:2] == [de2, de1]

