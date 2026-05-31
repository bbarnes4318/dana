import pytest
import os
from telephony.livekit_adapter import LiveKitOutboundAdapter, LiveKitDialConfig


def test_live_mode_disabled_by_default():
    adapter = LiveKitOutboundAdapter()
    assert adapter.live_mode_enabled() is False


def test_validate_live_config_missing_env_fails():
    adapter = LiveKitOutboundAdapter()
    config = LiveKitDialConfig(
        live_mode=True,
        room_name="test-room",
        phone_number="+15550000",
        participant_identity="p_1"
    )

    # Empty env vars
    os.environ["TELEPHONY_LIVE_MODE"] = "true"
    os.environ["DANA_ENABLE_OUTBOUND_DIALER"] = "true"
    if "LIVEKIT_URL" in os.environ: del os.environ["LIVEKIT_URL"]
    if "LIVEKIT_API_KEY" in os.environ: del os.environ["LIVEKIT_API_KEY"]
    if "LIVEKIT_API_SECRET" in os.environ: del os.environ["LIVEKIT_API_SECRET"]

    valid, warnings = adapter.validate_live_config(config)
    assert valid is False
    assert len(warnings) > 0


@pytest.mark.asyncio
async def test_mock_dial_returns_no_provider_call():
    adapter = LiveKitOutboundAdapter()
    config = LiveKitDialConfig(
        live_mode=False,
        room_name="test-room",
        phone_number="+15550000",
        participant_identity="p_1"
    )

    res = await adapter.dial(config)
    assert res.success is True
    assert res.dry_run is True
    assert res.live_mode is False
    assert res.room_name == "test-room"
    assert "mock" in res.message


def test_build_room_name():
    adapter = LiveKitOutboundAdapter()
    room = adapter.build_room_name(
        campaign_id="c1",
        lead_id="l1",
        attempt_id="a1",
        template="dana-{campaign_id}-{lead_id}-{attempt_id}"
    )
    assert room == "dana-c1-l1-a1"


def test_build_participant_identity():
    adapter = LiveKitOutboundAdapter()
    identity = adapter.build_participant_identity(lead_id="l1", attempt_id="a1")
    assert identity == "outbound-l1-a1"
