import pytest
import os
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
                mock_repo.close = AsyncMock()
                exit_code = await main_async()
                assert exit_code == 0

@pytest.mark.asyncio
async def test_smoke_test_dry_run_ignores_expect_second_turn(monkeypatch):
    """Verify that dry-run never claims second-turn verification even if flag is set."""
    monkeypatch.setenv("DANA_CONTROLLED_LIVE_TEST", "true")
    monkeypatch.setenv("DANA_RUNTIME_ENV", "test")
    
    test_args = [
        "live_call_smoke_test.py",
        "--to", "+18005550199",
        "--from", "+18005550100",
        "--dry-run",
        "--expect-second-turn"
    ]
    with patch("sys.argv", test_args):
        # Mock readiness checks to pass
        with patch("ops.live_call_smoke_test.run_readiness_checks", return_value=(True, {})):
            with patch("ops.live_call_smoke_test.Repository") as mock_repo_cls:
                mock_repo = mock_repo_cls.return_value
                mock_repo.close = AsyncMock()
                
                with patch("ops.live_call_smoke_test.LiveKitOutboundAdapter") as mock_adapter_cls:
                    exit_code = await main_async()
                    assert exit_code == 0
                    mock_adapter_cls.assert_not_called()

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
        with patch("ops.live_call_smoke_test.run_readiness_checks", return_value=(True, {})):
            with patch("ops.live_call_smoke_test.Repository") as mock_repo_cls:
                mock_repo = mock_repo_cls.return_value
                mock_repo._store.query = AsyncMock(return_value=[])
                mock_repo.list_recent_call_attempts = AsyncMock(return_value=[])
                mock_repo.query_campaign_leads = AsyncMock(return_value=[])
                mock_repo.close = AsyncMock()
                
                mock_dial_result = MagicMock()
                mock_dial_result.success = True
                mock_dial_result.room_name = "smoke-test-room-test"
                mock_dial_result.livekit_participant_id = "test-part"
                mock_dial_result.livekit_sip_call_id = "test-sip"
                mock_dial_result.provider_call_id = "test-provider"
                
                required_markers = [
                    "room_joined", "participant_joined", "inbound_audio_frame_received",
                    "vad_start_of_speech", "vad_end_of_speech", "stt_stream_created",
                    "transcript_final", "llm_node_entered", "user_text_seen_by_llm_node",
                    "agent_response_text_created", "tts_first_text", "tts_first_audio",
                    "second_turn_audio_published"
                ]
                metrics_data = [
                    {"metric_name": marker, "metric_value_ms": 100.0} for marker in required_markers
                ]
                
                async def mock_query(table, filters):
                    if table == "latency_metrics":
                        return metrics_data
                    return []
                mock_repo._store.query = mock_query
                
                with patch("ops.live_call_smoke_test.LiveKitOutboundAdapter") as mock_adapter_cls:
                    mock_adapter_cls.return_value.dial = AsyncMock(return_value=mock_dial_result)
                    
                    exit_code = await main_async()
                    assert exit_code == 0

@pytest.mark.asyncio
async def test_smoke_test_mock_system_checks_still_queries_real_db(monkeypatch):
    """Verify that DANA_MOCK_SYSTEM_CHECKS=true still creates Repository and queries real db metrics."""
    monkeypatch.setenv("DANA_CONTROLLED_LIVE_TEST", "true")
    monkeypatch.setenv("DANA_RUNTIME_ENV", "test")
    monkeypatch.setenv("DANA_MOCK_SYSTEM_CHECKS", "true")
    
    test_args = [
        "live_call_smoke_test.py",
        "--to", "+18005550199",
        "--from", "+18005550100",
        "--expect-second-turn"
    ]
    with patch("sys.argv", test_args):
        with patch("ops.live_call_smoke_test.run_readiness_checks", return_value=(True, {})):
            with patch("ops.live_call_smoke_test.Repository") as mock_repo_cls:
                mock_repo = mock_repo_cls.return_value
                mock_repo._store.query = AsyncMock(return_value=[])
                mock_repo.close = AsyncMock()
                
                mock_dial_result = MagicMock()
                mock_dial_result.success = True
                
                required_markers = [
                    "room_joined", "participant_joined", "inbound_audio_frame_received",
                    "vad_start_of_speech", "vad_end_of_speech", "stt_stream_created",
                    "transcript_final", "llm_node_entered", "user_text_seen_by_llm_node",
                    "agent_response_text_created", "tts_first_text", "tts_first_audio",
                    "second_turn_audio_published"
                ]
                metrics_data = [
                    {"metric_name": marker, "metric_value_ms": 100.0} for marker in required_markers
                ]
                
                async def mock_query(table, filters):
                    if table == "latency_metrics":
                        return metrics_data
                    return []
                mock_repo._store.query = mock_query
                
                with patch("ops.live_call_smoke_test.LiveKitOutboundAdapter") as mock_adapter_cls:
                    mock_adapter_cls.return_value.dial = AsyncMock(return_value=mock_dial_result)
                    
                    exit_code = await main_async()
                    assert exit_code == 0
                    mock_repo_cls.assert_called_once()

@pytest.mark.asyncio
async def test_smoke_test_fails_if_repo_fails_to_initialize(monkeypatch, caplog):
    """Verify smoke test fails with exact error if Repository cannot initialize."""
    monkeypatch.setenv("DANA_CONTROLLED_LIVE_TEST", "true")
    monkeypatch.setenv("DANA_RUNTIME_ENV", "test")
    
    test_args = [
        "live_call_smoke_test.py",
        "--to", "+18005550199",
        "--from", "+18005550100",
        "--expect-second-turn"
    ]
    with patch("sys.argv", test_args):
        with patch("ops.live_call_smoke_test.run_readiness_checks", return_value=(True, {})):
            with patch("ops.live_call_smoke_test.Repository", side_effect=Exception("DB Connection Timeout")):
                exit_code = await main_async()
                assert exit_code == 1
                assert any("Repository is required for live --expect-second-turn verification" in record.message for record in caplog.records)

@pytest.mark.asyncio
async def test_smoke_test_fails_if_no_metrics_found(monkeypatch, caplog):
    """Verify smoke test fails if no metrics are found for the call_id."""
    monkeypatch.setenv("DANA_CONTROLLED_LIVE_TEST", "true")
    monkeypatch.setenv("DANA_RUNTIME_ENV", "test")
    
    test_args = [
        "live_call_smoke_test.py",
        "--to", "+18005550199",
        "--from", "+18005550100",
        "--expect-second-turn"
    ]
    with patch("sys.argv", test_args):
        with patch("ops.live_call_smoke_test.run_readiness_checks", return_value=(True, {})):
            with patch("ops.live_call_smoke_test.Repository") as mock_repo_cls:
                mock_repo = mock_repo_cls.return_value
                mock_repo._store.query = AsyncMock(return_value=[])
                mock_repo.list_recent_call_attempts = AsyncMock(return_value=[])
                mock_repo.query_campaign_leads = AsyncMock(return_value=[])
                mock_repo.close = AsyncMock()
                
                mock_dial_result = MagicMock()
                mock_dial_result.success = True
                
                with patch("ops.live_call_smoke_test.LiveKitOutboundAdapter") as mock_adapter_cls:
                    mock_adapter_cls.return_value.dial = AsyncMock(return_value=mock_dial_result)
                    
                    with patch("asyncio.sleep", AsyncMock()):
                        exit_code = await main_async()
                        assert exit_code == 1
                        assert any("No real latency_metrics found for call_id=" in record.message for record in caplog.records)

@pytest.mark.asyncio
async def test_smoke_test_fails_if_metrics_incomplete(monkeypatch, caplog):
    """Verify smoke test fails if metrics exist but are incomplete."""
    monkeypatch.setenv("DANA_CONTROLLED_LIVE_TEST", "true")
    monkeypatch.setenv("DANA_RUNTIME_ENV", "test")
    
    test_args = [
        "live_call_smoke_test.py",
        "--to", "+18005550199",
        "--from", "+18005550100",
        "--expect-second-turn"
    ]
    with patch("sys.argv", test_args):
        with patch("ops.live_call_smoke_test.run_readiness_checks", return_value=(True, {})):
            with patch("ops.live_call_smoke_test.Repository") as mock_repo_cls:
                mock_repo = mock_repo_cls.return_value
                mock_repo.list_recent_call_attempts = AsyncMock(return_value=[])
                mock_repo.query_campaign_leads = AsyncMock(return_value=[])
                mock_repo.close = AsyncMock()
                
                mock_dial_result = MagicMock()
                mock_dial_result.success = True
                
                # Incomplete metrics data (only room_joined)
                metrics_data = [{"metric_name": "room_joined", "metric_value_ms": 100.0}]
                
                async def mock_query(table, filters):
                    if table == "latency_metrics":
                        return metrics_data
                    return []
                mock_repo._store.query = mock_query
                
                with patch("ops.live_call_smoke_test.LiveKitOutboundAdapter") as mock_adapter_cls:
                    mock_adapter_cls.return_value.dial = AsyncMock(return_value=mock_dial_result)
                    
                    with patch("asyncio.sleep", AsyncMock()):
                        exit_code = await main_async()
                        assert exit_code == 1
                        assert any("CONVERSATION_LOOP_READY=false - Broken stage: no participant joined" in record.message for record in caplog.records)

@pytest.mark.asyncio
async def test_smoke_test_canonical_call_id_metadata_propagation(monkeypatch):
    """Verify that canonical call_id is generated and passed via metadata to LiveKit dial."""
    monkeypatch.setenv("DANA_CONTROLLED_LIVE_TEST", "true")
    monkeypatch.setenv("DANA_RUNTIME_ENV", "test")
    
    test_args = [
        "live_call_smoke_test.py",
        "--to", "+18005550199",
        "--from", "+18005550100"
    ]
    with patch("sys.argv", test_args):
        with patch("ops.live_call_smoke_test.run_readiness_checks", return_value=(True, {})):
            with patch("ops.live_call_smoke_test.Repository") as mock_repo_cls:
                mock_repo = mock_repo_cls.return_value
                mock_repo._store.query = AsyncMock(return_value=[])
                mock_repo.list_recent_call_attempts = AsyncMock(return_value=[])
                mock_repo.query_campaign_leads = AsyncMock(return_value=[])
                mock_repo.close = AsyncMock()
                
                mock_dial_result = MagicMock()
                mock_dial_result.success = True
                
                with patch("ops.live_call_smoke_test.LiveKitOutboundAdapter") as mock_adapter_cls:
                    mock_dial = AsyncMock(return_value=mock_dial_result)
                    mock_adapter_cls.return_value.dial = mock_dial
                    
                    exit_code = await main_async()
                    assert exit_code == 0
                    
                    # Verify dial was called and metadata is correct
                    mock_dial.assert_called_once()
                    dial_config = mock_dial.call_args[0][0]
                    assert dial_config.phone_number == "+18005550199"
                    assert dial_config.caller_id == "+18005550100"
                    assert "call_id" in dial_config.metadata
                    assert "lead_id" in dial_config.metadata
                    assert dial_config.metadata["campaign_id"] == "smoke-test"
                    assert dial_config.metadata["smoke_test"] is True
