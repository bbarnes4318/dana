import os
import sys
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path

# Add repo root to path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from storage.repository import Repository
from telephony.live_production_readiness_gate import run_production_readiness_gate, ProductionReadinessResult


@pytest.fixture
def repo(tmp_path):
    return Repository(data_dir=tmp_path)


@pytest.mark.asyncio
async def test_readiness_gate_never_marks_production_scale_ready(repo):
    """Verify ready_for_production_scale is always hard locked to false."""
    with patch("telephony.live_production_readiness_gate.glob.glob", return_value=[]):
        res = await run_production_readiness_gate(repository=repo)
        assert isinstance(res, ProductionReadinessResult)
        assert res.ready_for_production_scale is False


@pytest.mark.asyncio
async def test_readiness_gate_requires_live_smoke_test(repo):
    """Verify that the gate check notices when no live smoke test has run in DB."""
    # When no completed call attempts are present in db
    with patch("telephony.live_production_readiness_gate.glob.glob", return_value=[]):
        res = await run_production_readiness_gate(repository=repo)
        assert not any("At least one real live outbound smoke test passed." in c for c in res.passed_checks)
        assert any("No record of a successful real live outbound smoke test." in c for c in res.failed_checks)


@pytest.mark.asyncio
async def test_readiness_gate_requires_one_lead_test(repo):
    """Verify gate requires a recorded successful 1-lead live test in reports."""
    with patch("telephony.live_production_readiness_gate.glob.glob", return_value=[]):
        res = await run_production_readiness_gate(repository=repo)
        assert any("No record of a successful one-lead controlled live campaign test." in c for c in res.failed_checks)


@pytest.mark.asyncio
async def test_readiness_gate_requires_batch_test(repo):
    """Verify gate requires a recorded successful batch live test in reports."""
    with patch("telephony.live_production_readiness_gate.glob.glob", return_value=[]):
        res = await run_production_readiness_gate(repository=repo)
        assert any("No record of a successful 3-lead controlled live batch validation." in c for c in res.failed_checks)


@pytest.mark.asyncio
async def test_readiness_gate_requires_post_call_exports(repo):
    """Verify gate check flags lack of completed calls with post-call exports."""
    with patch("telephony.live_production_readiness_gate.glob.glob", return_value=[]):
        res = await run_production_readiness_gate(repository=repo)
        assert any("No completed calls with verified post-call export files found." in c for c in res.failed_checks)


@pytest.mark.asyncio
async def test_readiness_gate_requires_dnc_controls(repo):
    """Verify gate queries DialerQueue and CampaignLeadImporter for DNC and calling windows."""
    with patch("telephony.dialer_queue.DialerQueue") as mock_dq, \
         patch("telephony.lead_importer.CampaignLeadImporter") as mock_li, \
         patch("telephony.live_production_readiness_gate.glob.glob", return_value=[]):
        
        # Setup class attributes
        mock_dq.return_value.is_within_calling_window = lambda x: True
        mock_dq.return_value.lead_is_callable = lambda x: True
        mock_li.return_value.is_suppressed = lambda x: False
        
        res = await run_production_readiness_gate(repository=repo)
        assert any("DNC list scrubbing and calling window limit checking are enabled." in c for c in res.passed_checks)


def test_web_ui_has_production_readiness_gate_card():
    """Verify that training console index.html contains production-readiness-gate-card."""
    path = Path(__file__).resolve().parent.parent / "static" / "training_console" / "index.html"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "production-readiness-gate-card" in content


@pytest.mark.asyncio
async def test_full_readiness_gate_flow(repo):
    """Test the readiness gate with fully mocked environment where all criteria pass."""
    # 1. Mock LiveTelephonyReadinessChecker
    mock_ready_res = MagicMock()
    mock_ready_res.ready = True
    
    # 2. Mock reports directory glob to simulate successful runs
    mock_glob = [
        "data/telephony_reports/dry_run_batch.json",
        "data/telephony_reports/one_lead.json",
        "data/telephony_reports/three_lead.json"
    ]
    
    def mock_open_reports(fpath, *args, **kwargs):
        if "dry_run_batch.json" in str(fpath):
            data = {"success": True, "dry_run": True, "requested_leads": 3}
        elif "one_lead.json" in str(fpath):
            data = {"success": True, "dry_run": False, "requested_leads": 1}
        else:
            data = {"success": True, "dry_run": False, "requested_leads": 3}
        
        m = MagicMock()
        m.__enter__.return_value = m
        m.read.return_value = json.dumps(data)
        return m

    # 3. Create mock database attempts with exports and intake staged
    # We will patch query_call_attempts to return matching data
    mock_attempts = [
        {
            "status": "completed",
            "outcome": "answered",
            "post_call_export_path": str(repo._store._data_dir / "export_test.json"),
            "metadata": {
                "intake_run": True,
                "intake_result": "staged"
            }
        }
    ]
    
    # Write mock export file to disk so it passes os.path.exists
    export_file = repo._store._data_dir / "export_test.json"
    export_file.write_text(json.dumps({"test": "data"}))

    # 4. Mock git check to pass
    mock_git_res = MagicMock()
    mock_git_res.stdout = ""

    with patch("telephony.live_production_readiness_gate.LiveTelephonyReadinessChecker.run", new_callable=AsyncMock) as mock_run_checker, \
         patch("telephony.live_production_readiness_gate.check_worker_dependencies") as mock_worker, \
         patch("telephony.live_production_readiness_gate.DIDPoolManager.list_numbers", new_callable=AsyncMock) as mock_list_numbers, \
         patch("telephony.live_production_readiness_gate.DIDPoolManager.select_caller_id", new_callable=AsyncMock) as mock_select_caller_id, \
         patch("telephony.live_production_readiness_gate.glob.glob", return_value=mock_glob), \
         patch("telephony.live_production_readiness_gate.open", mock_open_reports), \
         patch.object(repo, "query_call_attempts", new_callable=AsyncMock, return_value=mock_attempts), \
         patch("telephony.live_production_readiness_gate.os.path.exists", return_value=True), \
         patch("telephony.live_production_readiness_gate.subprocess.run", return_value=mock_git_res):
         
         mock_run_checker.return_value = mock_ready_res
         mock_worker.return_value = {"ready": True}
         
         # Mock DIDs list
         mock_did = MagicMock()
         mock_did.status = "active"
         mock_list_numbers.return_value = [mock_did]
         
         # Mock select_caller_id results
         mock_select_res = MagicMock()
         mock_select_res.success = True
         mock_select_res.phone_number = "+15055202898"
         mock_select_caller_id.return_value = mock_select_res
         
         # Set env vars to satisfy checks
         with patch.dict(os.environ, {
             "LIVEKIT_SIP_OUTBOUND_TRUNK_ID": "tr_12345",
             "DANA_AUTO_APPROVE_TRAINING_EXAMPLES": "false",
             "DANA_ACTIVE_TELEPHONY_PROVIDER": "telnyx",
             "DANA_OUTBOUND_CALLER_ID_SOURCE": "pool:telnyx_api"
         }):
             res = await run_production_readiness_gate(repository=repo)
             
             # Assert everything passes for small canary, but not production scale
             assert res.ready_for_small_canary is True
             assert res.ready_for_production_scale is False
             assert len(res.failed_checks) == 0
