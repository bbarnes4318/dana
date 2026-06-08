import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from ops.live_call_smoke_test import main_async

@pytest.mark.asyncio
async def test_smoke_test_dry_run(monkeypatch):
    """Verify that dry-run returns success without calling dial."""
    monkeypatch.setenv("DANA_CONTROLLED_LIVE_TEST", "true")
    monkeypatch.setenv("DANA_RUNTIME_ENV", "test")
    
    test_args = [
        "live_call_smoke_test.py",
        "--to", "+18005550199",
        "--from", "+18005550100",
        "--dry-run"
    ]
    with patch("sys.argv", test_args):
        # Mock readiness checks to pass
        with patch("ops.live_call_smoke_test.run_readiness_checks", return_value=(True, {})):
            with patch("ops.live_call_smoke_test.Repository") as mock_repo_cls:
                mock_repo = mock_repo_cls.return_value
                # Mock DNC queries
                mock_repo._store.query = AsyncMock(return_value=[])
                mock_repo.list_recent_call_attempts = AsyncMock(return_value=[])
                mock_repo.query_campaign_leads = AsyncMock(return_value=[])
                mock_repo.close = AsyncMock()
                exit_code = await main_async()
                assert exit_code == 0

@pytest.mark.asyncio
async def test_smoke_test_expect_second_turn_success(monkeypatch):
    """Verify smoke test passes if expectation of second turn is met in database."""
    monkeypatch.setenv("DANA_CONTROLLED_LIVE_TEST", "true")
    monkeypatch.setenv("DANA_RUNTIME_ENV", "test")
    
    test_args = [
        "live_call_smoke_test.py",
        "--to", "+18005550199",
        "--from", "+18005550100",
        "--expect-second-turn"
    ]
    with patch("sys.argv", test_args):
        # Mock readiness checks to pass
        with patch("ops.live_call_smoke_test.run_readiness_checks", return_value=(True, {})):
            with patch("ops.live_call_smoke_test.Repository") as mock_repo_cls:
                mock_repo = mock_repo_cls.return_value
                # Mock DNC / suppression checks
                mock_repo._store.query = AsyncMock(return_value=[])
                mock_repo.list_recent_call_attempts = AsyncMock(return_value=[])
                mock_repo.query_campaign_leads = AsyncMock(return_value=[])
                mock_repo.close = AsyncMock()
                
                # Mock dialer adapter to return success
                mock_dial_result = MagicMock()
                mock_dial_result.success = True
                mock_dial_result.room_name = "smoke-test-room-test"
                mock_dial_result.livekit_participant_id = "test-part"
                mock_dial_result.livekit_sip_call_id = "test-sip"
                mock_dial_result.provider_call_id = "test-provider"
                
                # Setup metrics in database matching all required stages
                required_markers = [
                    "room_joined", "greeting_audio_published", "inbound_audio_frame_received",
                    "stt_stream_created", "stt_final_transcript", "llm_node_entered",
                    "agent_response_text_created", "second_turn_tts_first_audio", "second_turn_audio_published"
                ]
                metrics_data = [
                    {"metric_name": f"event_{marker}", "metric_value_ms": 100.0} for marker in required_markers
                ]
                
                # Direct mock query call for latency_metrics table
                async def mock_query(table, filters):
                    if table == "latency_metrics":
                        return metrics_data
                    return []
                
                mock_repo._store.query = mock_query
                
                with patch("ops.live_call_smoke_test.LiveKitOutboundAdapter") as mock_adapter_cls:
                    mock_adapter_cls.return_value.dial = AsyncMock(return_value=mock_dial_result)
                    
                    exit_code = await main_async()
                    assert exit_code == 0
