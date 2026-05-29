import os
import pytest
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from storage.repository import Repository
from storage.schemas import CallCost, OutcomeMetric
from metrics.model_cost_metrics import (
    estimate_llm_tokens,
    calculate_and_save_costs,
    get_stt_rate_and_source,
    get_llm_rates_and_source,
    get_tts_rate_and_source,
    get_telephony_rate_and_source,
)
from metrics.outcome_metrics import save_outcome_for_call


@pytest.fixture
def clean_env():
    """Backup and clean cost env vars before each test."""
    keys = [
        "DANA_COST_STT_RATE_PER_SECOND",
        "DANA_COST_LLM_PROMPT_RATE_PER_TOKEN",
        "DANA_COST_LLM_COMPLETION_RATE_PER_TOKEN",
        "DANA_COST_TTS_RATE_PER_CHARACTER",
        "DANA_COST_TELEPHONY_RATE_PER_MINUTE",
        "DANA_COST_LOCAL_STT_INFRA_PER_MINUTE",
        "DANA_COST_LOCAL_LLM_INFRA_PER_1K_TOKENS",
        "DANA_COST_LOCAL_TTS_INFRA_PER_1K_CHARS",
        "DANA_COST_CURRENCY",
    ]
    backup = {k: os.environ.get(k) for k in keys}
    for k in keys:
        if k in os.environ:
            del os.environ[k]
    yield
    for k, v in backup.items():
        if v is not None:
            os.environ[k] = v
        else:
            if k in os.environ:
                del os.environ[k]


@pytest.mark.asyncio
async def test_token_estimation():
    assert estimate_llm_tokens("") == 0
    # 4 characters = 1 token
    assert estimate_llm_tokens("abcd") == 1
    assert estimate_llm_tokens("abcdefgh") == 2
    assert estimate_llm_tokens("a") == 1


@pytest.mark.asyncio
async def test_cost_calculation_rates(clean_env):
    # Test defaults
    rate_stt, src_stt = get_stt_rate_and_source("deepgram")
    assert rate_stt == Decimal("0.000072")
    assert src_stt == "default_rate"

    rate_stt_local, src_stt_local = get_stt_rate_and_source("local")
    assert rate_stt_local == Decimal("0.0")
    assert src_stt_local == "default_local_rate"

    # Test overrides
    os.environ["DANA_COST_STT_RATE_PER_SECOND"] = "0.005"
    rate_stt_over, src_stt_over = get_stt_rate_and_source("deepgram")
    assert rate_stt_over == Decimal("0.005")
    assert src_stt_over == "env_override"

    os.environ["DANA_COST_LOCAL_STT_INFRA_PER_MINUTE"] = "0.60"
    del os.environ["DANA_COST_STT_RATE_PER_SECOND"]
    rate_stt_infra, src_stt_infra = get_stt_rate_and_source("local")
    # $0.60/min = $0.01/sec
    assert rate_stt_infra == Decimal("0.01")
    assert src_stt_infra == "local_infra_rate"


@pytest.mark.asyncio
async def test_llm_rates(clean_env):
    prompt, completion, source = get_llm_rates_and_source("gpt-4o")
    assert prompt == Decimal("0.000005")
    assert completion == Decimal("0.000015")
    assert source == "default_rate"

    prompt_local, completion_local, source_local = get_llm_rates_and_source("meta-llama/Llama-3-8b")
    assert prompt_local == Decimal("0.0000002")
    assert source_local == "default_local_rate"

    os.environ["DANA_COST_LOCAL_LLM_INFRA_PER_1K_TOKENS"] = "0.10"
    prompt_infra, completion_infra, source_infra = get_llm_rates_and_source("meta-llama/Llama-3-8b")
    # $0.10 / 1000 = 0.0001
    assert prompt_infra == Decimal("0.0001")
    assert source_infra == "local_infra_rate"


@pytest.mark.asyncio
async def test_telephony_and_tts_rates(clean_env):
    tele_rate, tele_src = get_telephony_rate_and_source("telnyx")
    assert tele_rate == Decimal("0.01")
    
    os.environ["DANA_COST_TELEPHONY_RATE_PER_MINUTE"] = "0.02"
    tele_rate_over, tele_src_over = get_telephony_rate_and_source("telnyx")
    assert tele_rate_over == Decimal("0.02")

    tts_rate, tts_src = get_tts_rate_and_source("elevenlabs")
    assert tts_rate == Decimal("0.0003")

    os.environ["DANA_COST_LOCAL_TTS_INFRA_PER_1K_CHARS"] = "0.05"
    tts_rate_infra, tts_src_infra = get_tts_rate_and_source("local")
    # $0.05 / 1000 = 0.00005
    assert tts_rate_infra == Decimal("0.00005")


@pytest.mark.asyncio
async def test_calculate_and_save_costs(clean_env, tmp_path):
    repo = Repository(data_dir=tmp_path)
    call_id = "test-call-id"
    campaign_id = "test-camp-id"

    # Set some rates
    os.environ["DANA_COST_STT_RATE_PER_SECOND"] = "0.0001"
    os.environ["DANA_COST_LLM_PROMPT_RATE_PER_TOKEN"] = "0.00001"
    os.environ["DANA_COST_LLM_COMPLETION_RATE_PER_TOKEN"] = "0.00002"
    os.environ["DANA_COST_TTS_RATE_PER_CHARACTER"] = "0.0005"
    os.environ["DANA_COST_TELEPHONY_RATE_PER_MINUTE"] = "0.06" # $0.001 per second

    total = await calculate_and_save_costs(
        repository=repo,
        call_id=call_id,
        campaign_id=campaign_id,
        stt_provider="deepgram",
        stt_seconds=100.0,  # 100 * 0.0001 = 0.01
        llm_model="gpt-4o",
        prompt_tokens=500,  # 500 * 0.00001 = 0.005
        completion_tokens=100, # 100 * 0.00002 = 0.002
        tts_provider="elevenlabs",
        tts_characters=1000, # 1000 * 0.0005 = 0.50
        telephony_provider="telnyx",
        telephony_seconds=600.0, # (600 / 60) * 0.06 = 0.60
        dry_run=False
    )

    # total = 0.01 + 0.005 + 0.002 + 0.50 + 0.60 = 1.117
    assert total == Decimal("1.117")

    # Verify no raw text is saved, and decimal is used
    costs = await repo.list_call_costs(campaign_id)
    assert len(costs) == 5 # telephony, stt, llm (prompt), llm (completion), tts
    for c in costs:
        assert isinstance(c["estimated_cost"], Decimal)
        # Verify forbidden raw text fields are not in the dictionary
        assert "prompt" not in c
        assert "text" not in c
        assert "transcript" not in c

    # Test dry run is zero cost
    total_dry = await calculate_and_save_costs(
        repository=repo,
        call_id="call-dry",
        campaign_id=campaign_id,
        stt_provider="deepgram",
        stt_seconds=100.0,
        llm_model="gpt-4o",
        prompt_tokens=500,
        completion_tokens=100,
        tts_provider="elevenlabs",
        tts_characters=1000,
        telephony_provider="telnyx",
        telephony_seconds=600.0,
        dry_run=True
    )
    assert total_dry == Decimal("0.0")

    dry_costs = await repo.list_call_costs(campaign_id, from_date=datetime.now(timezone.utc) - timedelta(seconds=1))
    # Filter for the dry run call
    dry_call_costs = [c for c in dry_costs if c["call_id"] == "call-dry"]
    assert len(dry_call_costs) == 5
    for c in dry_call_costs:
        assert c["estimated_cost"] == Decimal("0.0")
        assert c["dry_run"] is True


@pytest.mark.asyncio
async def test_save_call_cost_idempotency(tmp_path):
    repo = Repository(data_dir=tmp_path)
    
    # Save once
    id1 = await repo.save_call_cost(
        call_id="call-id-1",
        component="stt",
        provider="deepgram",
        model="transcription",
        usage_unit="seconds",
        usage_quantity=Decimal("10.0"),
        unit_rate=Decimal("0.01"),
        estimated_cost=Decimal("0.10"),
        rate_source="test"
    )
    
    # Save second time with new quantity
    id2 = await repo.save_call_cost(
        call_id="call-id-1",
        component="stt",
        provider="deepgram",
        model="transcription",
        usage_unit="seconds",
        usage_quantity=Decimal("20.0"),
        unit_rate=Decimal("0.01"),
        estimated_cost=Decimal("0.20"),
        rate_source="test"
    )

    assert id1 == id2 # Must be the same ID, proving upsert/idempotency

    # Check store contains exactly one row
    costs = await repo._store.query("call_costs", {"call_id": "call-id-1"})
    assert len(costs) == 1
    assert float(costs[0]["usage_quantity"]) == 20.0
    assert float(costs[0]["estimated_cost"]) == 0.20


@pytest.mark.asyncio
async def test_unique_constraint_with_defaults(tmp_path):
    repo = Repository(data_dir=tmp_path)

    # Save with missing/None provider/model
    id1 = await repo.save_call_cost(
        call_id="call-id-2",
        component="telephony",
        provider=None,
        model=None,
        estimated_cost=Decimal("0.0")
    )

    id2 = await repo.save_call_cost(
        call_id="call-id-2",
        component="telephony",
        provider=None,
        model=None,
        estimated_cost=Decimal("0.5")
    )

    assert id1 == id2 # Must map to UNIQUE(call_id, component, 'unknown', 'unknown')

    costs = await repo._store.query("call_costs", {"call_id": "call-id-2"})
    assert len(costs) == 1
    assert costs[0]["provider"] == "unknown"
    assert costs[0]["model"] == "unknown"


@pytest.mark.asyncio
async def test_outcome_metrics_daily_rollup(tmp_path):
    repo = Repository(data_dir=tmp_path)
    campaign_id = "camp-rollup"
    metric_date = datetime.now(timezone.utc).date()

    # Create multiple calls in database
    await repo.save_call(call_id="call-1", campaign_id=campaign_id, outcome="transferred", answered_at=datetime.now(timezone.utc), created_at=datetime.now(timezone.utc))
    await repo.save_call(call_id="call-2", campaign_id=campaign_id, outcome="voicemail", created_at=datetime.now(timezone.utc))
    await repo.save_call(call_id="call-3", campaign_id=campaign_id, outcome="failed", created_at=datetime.now(timezone.utc))

    # Add cost records
    await repo.save_call_cost(call_id="call-1", campaign_id=campaign_id, component="stt", estimated_cost=Decimal("0.50"))
    await repo.save_call_cost(call_id="call-2", campaign_id=campaign_id, component="telephony", estimated_cost=Decimal("0.10"))

    # Recompute daily outcome metrics
    await repo.recompute_daily_outcome_metric(campaign_id, metric_date)

    # Verifyrollup
    rollups = await repo._store.query("outcome_metrics", {"campaign_id": campaign_id})
    assert len(rollups) == 1
    r = rollups[0]
    assert r["total_dialed"] == 3
    assert r["transferred"] == 1
    assert r["voicemail"] == 1
    assert r["failed"] == 1
    assert float(r["cost"]) == 0.60

    # Save another call and trigger recompute to verify upsert/idempotency
    await repo.save_call(call_id="call-4", campaign_id=campaign_id, outcome="callback", created_at=datetime.now(timezone.utc))
    await save_outcome_for_call(repo, "call-4", campaign_id, "callback")

    rollups2 = await repo._store.query("outcome_metrics", {"campaign_id": campaign_id})
    assert len(rollups2) == 1 # Ensure no duplicate rows
    assert rollups2[0]["total_dialed"] == 4
    assert rollups2[0]["callback"] == 1


@pytest.mark.asyncio
async def test_get_campaign_metrics_and_summaries(tmp_path):
    repo = Repository(data_dir=tmp_path)
    campaign_id = "camp-summary"
    
    # Place a transfer and a callback call
    await repo.save_call(call_id="call-1", campaign_id=campaign_id, outcome="transferred", answered_at=datetime.now(timezone.utc), created_at=datetime.now(timezone.utc))
    await repo.save_call(call_id="call-2", campaign_id=campaign_id, outcome="callback", answered_at=datetime.now(timezone.utc), created_at=datetime.now(timezone.utc))

    await repo.save_call_cost(call_id="call-1", campaign_id=campaign_id, component="stt", provider="deepgram", estimated_cost=Decimal("0.10"))
    await repo.save_call_cost(call_id="call-1", campaign_id=campaign_id, component="llm", provider="openai", estimated_cost=Decimal("0.20"))
    await repo.save_call_cost(call_id="call-2", campaign_id=campaign_id, component="stt", provider="deepgram", estimated_cost=Decimal("0.05"))

    # get_cost_summary
    summary = await repo.get_cost_summary(campaign_id)
    assert summary["total_estimated_cost"] == 0.35
    assert summary["components"]["stt"] == 0.15
    assert summary["components"]["llm"] == 0.20
    assert summary["providers"]["deepgram"] == 0.15
    assert summary["providers"]["openai"] == 0.20

    # get_campaign_metrics
    metrics = await repo.get_campaign_metrics(campaign_id)
    assert metrics["total_calls"] == 2
    assert metrics["answered_calls"] == 2
    assert metrics["answer_rate"] == 1.0
    assert metrics["transfer_rate"] == 0.5
    assert metrics["callback_rate"] == 0.5
    assert metrics["total_cost"] == 0.35
    assert metrics["cost_per_dial"] == 0.175
    assert metrics["cost_per_transfer"] == 0.35
    assert metrics["cost_per_callback"] == 0.35

    # get_daily_metrics
    daily = await repo.get_daily_metrics(campaign_id, days=3)
    assert len(daily) == 3
    assert daily[0]["total_dialed"] == 2
    assert daily[0]["cost"] == 0.35
