import pytest
import os
from unittest.mock import AsyncMock, MagicMock, patch
from storage.repository import Repository
from telephony.live_telephony_readiness import LiveTelephonyReadinessChecker
from telephony.livekit_adapter import LiveKitOutboundAdapter

@pytest.mark.asyncio
async def test_readiness_fails_when_env_missing(tmp_path):
    # Save env
    orig = {k: os.environ.get(k) for k in ["TELEPHONY_LIVE_MODE", "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"]}
    
    # Delete them
    for k in orig.keys():
        if k in os.environ: del os.environ[k]
        
    checker = LiveTelephonyReadinessChecker(repository=Repository(data_dir=tmp_path))
    res = await checker.run()
    assert res.ready is False
    assert any("TELEPHONY_LIVE_MODE" in f or "LIVEKIT_URL" in f for f in res.failures)

    # Restore env
    for k, v in orig.items():
        if v is not None: os.environ[k] = v

@pytest.mark.asyncio
async def test_readiness_passes_with_env_and_sdk_mock(tmp_path):
    # Force mock env vars
    orig = {k: os.environ.get(k) for k in ["TELEPHONY_LIVE_MODE", "DANA_ENABLE_OUTBOUND_DIALER", "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "LIVEKIT_SIP_OUTBOUND_TRUNK_ID", "DANA_OUTBOUND_CALLER_ID", "DANA_AGENT_WORKER_ENABLED"]}
    
    os.environ["TELEPHONY_LIVE_MODE"] = "true"
    os.environ["DANA_ENABLE_OUTBOUND_DIALER"] = "true"
    os.environ["LIVEKIT_URL"] = "wss://test.livekit.cloud"
    os.environ["LIVEKIT_API_KEY"] = "key"
    os.environ["LIVEKIT_API_SECRET"] = "secret"
    os.environ["LIVEKIT_SIP_OUTBOUND_TRUNK_ID"] = "trunk-123"
    os.environ["DANA_OUTBOUND_CALLER_ID"] = "+15550000"
    os.environ["DANA_AGENT_WORKER_ENABLED"] = "true"

    checker = LiveTelephonyReadinessChecker(repository=Repository(data_dir=tmp_path))
    
    # Mock SDK import check to succeed
    with patch.object(checker, "check_livekit_sdk", return_value=(True, None)):
        res = await checker.run()
        assert res.ready is True
        assert len(res.failures) == 0

    # Restore env
    for k, v in orig.items():
        if v is not None:
            os.environ[k] = v
        elif k in os.environ:
            del os.environ[k]

@pytest.mark.asyncio
async def test_readiness_checks_provider_config(tmp_path):
    repository = Repository(data_dir=tmp_path)
    checker = LiveTelephonyReadinessChecker(repository=repository)
    
    # Provider config not found scenario
    res = await checker.check_provider_config("missing-provider-id")
    assert res["ok"] is False
    assert any("not found" in f for f in res["failures"])
    
    # Save a valid provider config
    provider_config_id = "prov-ready-test"
    # Set fallback caller ID in env so that it resolves
    os.environ["DANA_OUTBOUND_CALLER_ID"] = "+15551212"
    await repository.save_telephony_provider_config(
        id=provider_config_id,
        name="Test Provider",
        livekit_sip_outbound_trunk_id="trunk-456",
    )
    
    res = await checker.check_provider_config(provider_config_id)
    assert res["ok"] is True
    assert res["outbound_trunk_id_present"] is True
    assert res["caller_id_present"] is True

@pytest.mark.asyncio
async def test_readiness_checks_campaign(tmp_path):
    repository = Repository(data_dir=tmp_path)
    checker = LiveTelephonyReadinessChecker(repository=repository)
    
    # Campaign not found
    res = await checker.check_campaign("missing-camp")
    assert res["ok"] is False
    assert any("not found" in f for f in res["failures"])
    
    # Save campaign in draft status (should fail running constraint)
    campaign_id = "camp-ready-test"
    await repository.save_outbound_campaign(
        id=campaign_id,
        name="Draft Campaign",
        status="draft"
    )
    res = await checker.check_campaign(campaign_id)
    assert res["ok"] is False
    assert any("must be running" in f for f in res["failures"])
    
    # Save campaign in running status but no leads (should fail no active leads)
    await repository.save_outbound_campaign(
        id=campaign_id,
        name="Running Campaign",
        status="running"
    )
    res = await checker.check_campaign(campaign_id)
    assert res["ok"] is False
    assert any("no active/callable leads" in f for f in res["failures"])

    # Add active lead (should now pass checks)
    await repository.save_campaign_lead(
        campaign_id=campaign_id,
        phone_number="+15550001",
        status="new"
    )
    res = await checker.check_campaign(campaign_id)
    assert res["ok"] is True

@pytest.mark.asyncio
async def test_readiness_does_not_place_call(tmp_path):
    # Readiness should run completely without placing any dials
    adapter = LiveKitOutboundAdapter()
    mock_dial = AsyncMock()
    adapter.dial = mock_dial
    
    checker = LiveTelephonyReadinessChecker(repository=Repository(data_dir=tmp_path), adapter=adapter)
    await checker.run()
    
    mock_dial.assert_not_called()

