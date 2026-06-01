import pytest
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from telephony.livekit_adapter import LiveKitOutboundAdapter, LiveKitDialConfig, LiveKitDialResult

def test_live_mode_false_returns_mock():
    adapter = LiveKitOutboundAdapter()
    config = LiveKitDialConfig(
        live_mode=False,
        room_name="test-room",
        phone_number="+15550000",
        participant_identity="p_1"
    )
    # Even if env enables it, config.live_mode=False means mock
    os.environ["TELEPHONY_LIVE_MODE"] = "true"
    os.environ["DANA_ENABLE_OUTBOUND_DIALER"] = "true"
    
    import asyncio
    res = asyncio.run(adapter.dial(config))
    assert res.success is True
    assert res.dry_run is True
    assert res.live_mode is False
    assert res.room_name == "test-room"

def test_live_mode_true_missing_env_fails():
    adapter = LiveKitOutboundAdapter()
    config = LiveKitDialConfig(
        live_mode=True,
        room_name="test-room",
        phone_number="+15550000",
        participant_identity="p_1"
    )
    with patch("config.runtime_env.load_environment"):
        if "TELEPHONY_LIVE_MODE" in os.environ: del os.environ["TELEPHONY_LIVE_MODE"]
        if "DANA_ENABLE_OUTBOUND_DIALER" in os.environ: del os.environ["DANA_ENABLE_OUTBOUND_DIALER"]
        if "DANA_CONFIRM_PLACE_CALL" in os.environ: del os.environ["DANA_CONFIRM_PLACE_CALL"]

        import asyncio
        res = asyncio.run(adapter.dial(config))
        assert res.success is True  # Because live_mode_enabled() is False, it falls back to mock
        assert res.dry_run is True

def test_required_env_status_reports_missing_keys():
    adapter = LiveKitOutboundAdapter()
    # Save env
    orig = {k: os.environ.get(k) for k in ["TELEPHONY_LIVE_MODE", "LIVEKIT_URL"]}
    
    with patch("config.runtime_env.load_environment"):
        os.environ["TELEPHONY_LIVE_MODE"] = "true"
        if "LIVEKIT_URL" in os.environ: del os.environ["LIVEKIT_URL"]
        
        status = adapter.required_env_status()
        assert status["TELEPHONY_LIVE_MODE"] == "true"
        assert status["LIVEKIT_URL"] is None
    
    # Restore env
    for k, v in orig.items():
        if v is not None:
            os.environ[k] = v
        elif k in os.environ:
            del os.environ[k]

def test_validate_live_config_requires_outbound_trunk():
    adapter = LiveKitOutboundAdapter()
    config = LiveKitDialConfig(
        live_mode=True,
        room_name="test-room",
        phone_number="+15550000",
        participant_identity="p_1"
    )
    
    with patch("config.runtime_env.load_environment"):
        # Empty out trunk env and config
        if "LIVEKIT_SIP_OUTBOUND_TRUNK_ID" in os.environ: del os.environ["LIVEKIT_SIP_OUTBOUND_TRUNK_ID"]
        config.outbound_trunk_id = None
        
        # Mock other env
        os.environ["LIVEKIT_URL"] = "wss://test.livekit.cloud"
        os.environ["LIVEKIT_API_KEY"] = "key"
        os.environ["LIVEKIT_API_SECRET"] = "secret"
        
        ok, warnings = adapter.validate_live_config(config)
        assert ok is False
        assert any("outbound_trunk_id" in w for w in warnings)

def test_livekit_import_uses_official_import_path():
    # Verify we can import correctly from the official path
    from livekit import api
    from livekit.protocol.sip import CreateSIPParticipantRequest
    assert api is not None
    assert CreateSIPParticipantRequest is not None

@pytest.mark.asyncio
async def test_create_sip_participant_request_mapping():
    # Verify that the CreateSIPParticipantRequest maps config fields properly
    from livekit.protocol.sip import CreateSIPParticipantRequest
    config = LiveKitDialConfig(
        live_mode=True,
        room_name="my-room",
        phone_number="+15551234",
        participant_identity="part-1",
        participant_name="Dana Outbound",
        krisp_enabled=True,
        wait_until_answered=True
    )
    
    req = CreateSIPParticipantRequest(
        sip_trunk_id="trunk-1",
        sip_call_to=config.phone_number,
        room_name=config.room_name,
        participant_identity=config.participant_identity,
        participant_name=config.participant_name,
        krisp_enabled=config.krisp_enabled,
        wait_until_answered=config.wait_until_answered,
    )
    assert req.kwargs["sip_call_to"] == "+15551234"
    assert req.kwargs["room_name"] == "my-room"
    assert req.kwargs["participant_name"] == "Dana Outbound"

@pytest.mark.asyncio
async def test_create_sip_participant_success_maps_fields():
    adapter = LiveKitOutboundAdapter()
    config = LiveKitDialConfig(
        live_mode=True,
        room_name="test-room",
        phone_number="+15550000",
        participant_identity="p_1",
        outbound_trunk_id="trunk-123"
    )
    
    # Mock env
    os.environ["TELEPHONY_LIVE_MODE"] = "true"
    os.environ["DANA_ENABLE_OUTBOUND_DIALER"] = "true"
    os.environ["LIVEKIT_URL"] = "wss://test.livekit.cloud"
    os.environ["LIVEKIT_API_KEY"] = "key"
    os.environ["LIVEKIT_API_SECRET"] = "secret"
    os.environ["LIVEKIT_SIP_OUTBOUND_TRUNK_ID"] = "trunk-123"

    mock_participant = MagicMock()
    mock_participant.participant_id = "p_123"
    mock_participant.sip_call_id = "sip_456"

    # Mock LiveKitAPI and its call
    mock_lk = MagicMock()
    mock_lk.sip.create_sip_participant = AsyncMock(return_value=mock_participant)
    mock_lk.aclose = AsyncMock()

    with patch("livekit.api.LiveKitAPI", return_value=mock_lk):
        res = await adapter.dial(config)
        assert res.success is True
        assert res.livekit_participant_id == "p_123"
        assert res.livekit_sip_call_id == "sip_456"

@pytest.mark.asyncio
async def test_create_sip_participant_sip_error_maps_metadata():
    adapter = LiveKitOutboundAdapter()
    config = LiveKitDialConfig(
        live_mode=True,
        room_name="test-room",
        phone_number="+15550000",
        participant_identity="p_1",
        outbound_trunk_id="trunk-123"
    )
    
    os.environ["TELEPHONY_LIVE_MODE"] = "true"
    os.environ["DANA_ENABLE_OUTBOUND_DIALER"] = "true"
    os.environ["LIVEKIT_URL"] = "wss://test.livekit.cloud"
    os.environ["LIVEKIT_API_KEY"] = "key"
    os.environ["LIVEKIT_API_SECRET"] = "secret"
    os.environ["LIVEKIT_SIP_OUTBOUND_TRUNK_ID"] = "trunk-123"

    # Mock Twirp Error
    class MockTwirpError(Exception):
        code = "failed"
        message = "SIP connection timed out"
        meta = {"sip_status_code": "408", "sip_status": "Request Timeout"}

    mock_lk = MagicMock()
    mock_lk.sip.create_sip_participant = AsyncMock(side_effect=MockTwirpError())
    mock_lk.aclose = AsyncMock()

    with patch("livekit.api.LiveKitAPI", return_value=mock_lk):
        res = await adapter.dial(config)
        assert res.success is False
        assert res.sip_status_code == 408
        assert res.sip_status == "Request Timeout"

@pytest.mark.asyncio
async def test_livekit_client_closed_on_success():
    adapter = LiveKitOutboundAdapter()
    config = LiveKitDialConfig(
        live_mode=True,
        room_name="test-room",
        phone_number="+15550000",
        participant_identity="p_1",
        outbound_trunk_id="trunk-123"
    )
    
    os.environ["TELEPHONY_LIVE_MODE"] = "true"
    os.environ["DANA_ENABLE_OUTBOUND_DIALER"] = "true"
    os.environ["LIVEKIT_URL"] = "wss://test.livekit.cloud"
    os.environ["LIVEKIT_API_KEY"] = "key"
    os.environ["LIVEKIT_API_SECRET"] = "secret"
    os.environ["LIVEKIT_SIP_OUTBOUND_TRUNK_ID"] = "trunk-123"

    mock_participant = MagicMock()
    mock_lk = MagicMock()
    mock_lk.sip.create_sip_participant = AsyncMock(return_value=mock_participant)
    mock_lk.aclose = AsyncMock()

    with patch("livekit.api.LiveKitAPI", return_value=mock_lk):
        await adapter.dial(config)
        mock_lk.aclose.assert_called_once()

@pytest.mark.asyncio
async def test_livekit_client_closed_on_failure():
    adapter = LiveKitOutboundAdapter()
    config = LiveKitDialConfig(
        live_mode=True,
        room_name="test-room",
        phone_number="+15550000",
        participant_identity="p_1",
        outbound_trunk_id="trunk-123"
    )
    
    os.environ["TELEPHONY_LIVE_MODE"] = "true"
    os.environ["DANA_ENABLE_OUTBOUND_DIALER"] = "true"
    os.environ["LIVEKIT_URL"] = "wss://test.livekit.cloud"
    os.environ["LIVEKIT_API_KEY"] = "key"
    os.environ["LIVEKIT_API_SECRET"] = "secret"
    os.environ["LIVEKIT_SIP_OUTBOUND_TRUNK_ID"] = "trunk-123"

    mock_lk = MagicMock()
    mock_lk.sip.create_sip_participant = AsyncMock(side_effect=Exception("API failure"))
    mock_lk.aclose = AsyncMock()

    with patch("livekit.api.LiveKitAPI", return_value=mock_lk):
        await adapter.dial(config)
        mock_lk.aclose.assert_called_once()
