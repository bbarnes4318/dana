import pytest
import shutil
import tempfile
import os
from pathlib import Path
from decimal import Decimal
from storage.repository import Repository
from metrics.cost_per_outcome import recompute_campaign_rollups, calculate_campaign_metrics

@pytest.fixture
def temp_jsonl_repo():
    # Setup temporary directory for JSONL files
    temp_dir = tempfile.mkdtemp()
    
    # Force JSONL store by clearing DATABASE_URL
    with patch.dict(os.environ, {"DATABASE_URL": ""}):
        repository = Repository(data_dir=temp_dir)
        yield repository
        
    # Teardown temp directory
    shutil.rmtree(temp_dir)

from unittest.mock import patch

@pytest.mark.asyncio
async def test_jsonl_fallback_rollup(temp_jsonl_repo):
    repo = temp_jsonl_repo
    campaign_id = "test-camp-999"
    
    # 1. Create mock call records
    call_1 = {
        "call_id": "call-1",
        "campaign_id": campaign_id,
        "duration_seconds": 60.0,
        "outcome": "connected"
    }
    call_2 = {
        "call_id": "call-2",
        "campaign_id": campaign_id,
        "duration_seconds": 30.0,
        "outcome": "voicemail"
    }
    await repo.save_call(**call_1)
    await repo.save_call(**call_2)
    
    # 2. Create mock call outcome costs
    cost_1 = {
        "call_id": "call-1",
        "campaign_id": campaign_id,
        "outcome": "connected",
        "telephony_cost": Decimal("0.10"),
        "stt_cost": Decimal("0.05"),
        "llm_cost": Decimal("0.08"),
        "tts_cost": Decimal("0.06"),
        "gpu_cost": Decimal("0.00"),
        "total_cost": Decimal("0.29"),
        "is_estimated": False
    }
    cost_2 = {
        "call_id": "call-2",
        "campaign_id": campaign_id,
        "outcome": "voicemail",
        "telephony_cost": Decimal("0.05"),
        "stt_cost": Decimal("0.00"),
        "llm_cost": Decimal("0.02"),
        "tts_cost": Decimal("0.01"),
        "gpu_cost": Decimal("0.00"),
        "total_cost": Decimal("0.08"),
        "is_estimated": False
    }
    await repo.save_call_outcome_cost(**cost_1)
    await repo.save_call_outcome_cost(**cost_2)
    
    # 3. Trigger rollup
    rollups = await recompute_campaign_rollups(repo, campaign_id)
    
    assert "connected" in rollups
    assert "voicemail" in rollups
    
    assert rollups["connected"]["total_calls"] == 1
    assert rollups["connected"]["total_duration_seconds"] == 60.0
    assert rollups["connected"]["total_cost"] == Decimal("0.29")
    
    assert rollups["voicemail"]["total_calls"] == 1
    assert rollups["voicemail"]["total_duration_seconds"] == 30.0
    assert rollups["voicemail"]["total_cost"] == Decimal("0.08")
    
    # 4. Compute KPIs
    metrics = calculate_campaign_metrics(rollups)
    assert metrics["total_campaign_cost"] == Decimal("0.37")
    assert metrics["total_completed_calls"] == 2
    assert metrics["wasted_cost_voicemail_wrong_number"] == Decimal("0.08")
    
    # Check that they were persisted in JSONL
    persisted = await repo.query_campaign_cost_rollups({"campaign_id": campaign_id})
    assert len(persisted) == 2
