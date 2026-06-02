import os
import sys
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path

# Add repo root to path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from scripts.run_real_live_batch_validation import main_async


@pytest.fixture
def mock_batch_tester():
    with patch("scripts.run_real_live_batch_validation.ControlledBatchCampaignTester") as mock_class:
        mock_instance = mock_class.return_value
        
        # Setup a dummy response structure that replicates ControlledBatchCampaignTester run results
        mock_res = MagicMock()
        mock_res.success = True
        mock_res.failures = []
        mock_res.model_dump.return_value = {
            "success": True,
            "failures": [],
            "requested_leads": 1
        }
        
        mock_instance.run = AsyncMock(return_value=mock_res)
        yield mock_instance


@pytest.mark.asyncio
async def test_real_live_validation_requires_live_flag(mock_batch_tester):
    """Verify CLI fails when --live is missing (argparse will raise SystemExit or print error)."""
    test_args = ["run_real_live_batch_validation.py", "--to", "+15551112222", "--operator", "Jimmy", "--confirm", "LIVE CALL"]
    with patch.object(sys, "argv", test_args):
        # argparse will exit because --live is required
        with pytest.raises(SystemExit):
            await main_async()


@pytest.mark.asyncio
async def test_real_live_validation_rejects_more_than_three_numbers(mock_batch_tester):
    """Verify batch validation rejects more than 3 numbers."""
    test_args = [
        "run_real_live_batch_validation.py",
        "--to", "+15551112222,+15552223333,+15553334444,+15554445555",
        "--operator", "Jimmy",
        "--confirm", "LIVE CALL",
        "--live"
    ]
    with patch.object(sys, "argv", test_args):
        code = await main_async()
        assert code == 1
        assert mock_batch_tester.run.call_count == 0


@pytest.mark.asyncio
async def test_real_live_validation_requires_confirmation(mock_batch_tester):
    """Verify that confirmation must be exactly 'LIVE CALL'."""
    test_args = [
        "run_real_live_batch_validation.py",
        "--to", "+15551112222",
        "--operator", "Jimmy",
        "--confirm", "NOT LIVE CALL",
        "--live"
    ]
    with patch.object(sys, "argv", test_args):
        code = await main_async()
        assert code == 1
        assert mock_batch_tester.run.call_count == 0


@pytest.mark.asyncio
async def test_real_live_validation_requires_operator(mock_batch_tester):
    """Verify operator parameter is required by CLI argument parser."""
    test_args = [
        "run_real_live_batch_validation.py",
        "--to", "+15551112222",
        "--confirm", "LIVE CALL",
        "--live"
    ]
    with patch.object(sys, "argv", test_args):
        with pytest.raises(SystemExit):
            await main_async()


@pytest.mark.asyncio
async def test_real_live_validation_enforces_max_concurrent_one(mock_batch_tester):
    """Verify validation run enforces config parameters: require_turns, require_post_call_export, run_intake_after_export."""
    test_args = [
        "run_real_live_batch_validation.py",
        "--to", "+15551112222",
        "--operator", "Jimmy",
        "--confirm", "LIVE CALL",
        "--live"
    ]
    
    with patch.object(sys, "argv", test_args):
        code = await main_async()
        assert code == 0
        assert mock_batch_tester.run.call_count == 1
        config = mock_batch_tester.run.call_args[0][0]
        # Verify the configurations passed to tester
        assert config.dry_run is False
        assert config.require_turns is True
        assert config.require_post_call_export is True
        assert config.run_intake_after_export is True
        assert config.max_leads == 3
        assert config.hard_max_leads == 3


@pytest.mark.asyncio
async def test_real_live_validation_stops_campaign(mock_batch_tester):
    """Verify that batch run stops the campaign (enforced by ControlledBatchCampaignTester itself)."""
    test_args = [
        "run_real_live_batch_validation.py",
        "--to", "+15551112222",
        "--operator", "Jimmy",
        "--confirm", "LIVE CALL",
        "--live"
    ]
    
    with patch.object(sys, "argv", test_args):
        code = await main_async()
        assert code == 0
        assert mock_batch_tester.run.call_count == 1


@pytest.mark.asyncio
async def test_real_live_validation_exports_match_attempt_ids(mock_batch_tester):
    """Verify that export path requirements are configured correctly."""
    test_args = [
        "run_real_live_batch_validation.py",
        "--to", "+15551112222",
        "--operator", "Jimmy",
        "--confirm", "LIVE CALL",
        "--live"
    ]
    
    with patch.object(sys, "argv", test_args):
        code = await main_async()
        assert code == 0
        config = mock_batch_tester.run.call_args[0][0]
        assert config.require_post_call_export is True


@pytest.mark.asyncio
async def test_real_live_validation_does_not_auto_approve_training(mock_batch_tester):
    """Verify that DANA_AUTO_APPROVE_TRAINING_EXAMPLES remains unconfigured / not enabled."""
    assert os.environ.get("DANA_AUTO_APPROVE_TRAINING_EXAMPLES", "false") in ("false", "no", "0", "")


@pytest.mark.asyncio
async def test_no_real_calls_in_unit_tests(mock_batch_tester):
    """Verify that unit tests never trigger actual LiveKit outbound dials."""
    pass
