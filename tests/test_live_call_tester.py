import pytest
import os
from unittest.mock import AsyncMock, MagicMock, patch
from storage.repository import Repository
from telephony.live_call_tester import LiveCallTester, LiveCallTestConfig, LiveCallTestResult

@pytest.mark.asyncio
async def test_test_call_requires_operator():
    tester = LiveCallTester()
    config = LiveCallTestConfig(
        phone_number="+15550000",
        operator="",  # Empty
        live_mode=True
    )
    res = await tester.place_test_call(config)
    assert res.success is False
    assert res.error == "OPERATOR_REQUIRED"

@pytest.mark.asyncio
async def test_test_call_requires_live_mode():
    tester = LiveCallTester()
    config = LiveCallTestConfig(
        phone_number="+15550000",
        operator="Jimmy",
        live_mode=False  # Must be true
    )
    res = await tester.place_test_call(config)
    assert res.success is False
    assert res.error == "LIVE_MODE_REQUIRED"

@pytest.mark.asyncio
async def test_test_call_requires_readiness():
    tester = LiveCallTester()
    config = LiveCallTestConfig(
        phone_number="+15550000",
        operator="Jimmy",
        live_mode=True
    )
    # Fail readiness audit by ensuring env variables are missing
    orig = {k: os.environ.get(k) for k in ["TELEPHONY_LIVE_MODE", "LIVEKIT_URL"]}
    if "TELEPHONY_LIVE_MODE" in os.environ: del os.environ["TELEPHONY_LIVE_MODE"]
    if "LIVEKIT_URL" in os.environ: del os.environ["LIVEKIT_URL"]

    res = await tester.place_test_call(config)
    assert res.success is False
    assert res.error == "READINESS_AUDIT_FAILED"

    # Restore env
    for k, v in orig.items():
        if v is not None: os.environ[k] = v

@pytest.mark.asyncio
async def test_test_call_creates_attempt():
    repository = Repository()
    # Mock checker to bypass actual readiness checks
    mock_checker = MagicMock()
    mock_checker.run = AsyncMock(return_value=MagicMock(ready=True))

    # Mock adapter
    mock_adapter = MagicMock()
    mock_adapter.build_participant_identity.return_value = "outbound-manual-test"
    mock_adapter.dial = AsyncMock(return_value=MagicMock(success=True, livekit_participant_id="part_1", livekit_sip_call_id="sip_1", provider_call_id="sip_1", sip_call_status="active", answered=True))

    tester = LiveCallTester(repository=repository, adapter=mock_adapter)
    tester.checker = mock_checker

    config = LiveCallTestConfig(
        phone_number="+15550000",
        operator="Jimmy",
        live_mode=True
    )

    res = await tester.place_test_call(config)
    assert res.success is True
    assert res.call_attempt_id is not None

    # Check attempt is saved in DB
    attempt = await repository.get_call_attempt(res.call_attempt_id)
    assert attempt is not None
    assert attempt["status"] == "in_progress"

@pytest.mark.asyncio
async def test_test_call_success_updates_attempt_and_session():
    repository = Repository()
    mock_checker = MagicMock()
    mock_checker.run = AsyncMock(return_value=MagicMock(ready=True))

    mock_adapter = MagicMock()
    mock_adapter.build_participant_identity.return_value = "outbound-manual-test"
    mock_adapter.dial = AsyncMock(return_value=MagicMock(
        success=True,
        livekit_participant_id="part_1",
        livekit_sip_call_id="sip_1",
        provider_call_id="sip_1",
        sip_call_status="active",
        answered=True
    ))

    tester = LiveCallTester(repository=repository, adapter=mock_adapter)
    tester.checker = mock_checker

    config = LiveCallTestConfig(
        phone_number="+15550000",
        operator="Jimmy",
        live_mode=True
    )

    res = await tester.place_test_call(config)
    assert res.success is True
    
    # Check attempt updated
    attempt = await repository.get_call_attempt(res.call_attempt_id)
    assert attempt["status"] == "in_progress"
    assert attempt["livekit_participant_id"] == "part_1"
    
    # Check session created
    sessions = await repository.query_live_call_sessions({"attempt_id": res.call_attempt_id})
    assert len(sessions) == 1
    assert sessions[0]["status"] == "active"

@pytest.mark.asyncio
async def test_test_call_failure_updates_attempt():
    repository = Repository()
    mock_checker = MagicMock()
    mock_checker.run = AsyncMock(return_value=MagicMock(ready=True))

    mock_adapter = MagicMock()
    mock_adapter.build_participant_identity.return_value = "outbound-manual-test"
    mock_adapter.dial = AsyncMock(return_value=MagicMock(
        success=False,
        message="Busy signal",
        error="SIP_486_BUSY",
        sip_call_status="busy",
        data={}
    ))

    tester = LiveCallTester(repository=repository, adapter=mock_adapter)
    tester.checker = mock_checker

    config = LiveCallTestConfig(
        phone_number="+15550000",
        operator="Jimmy",
        live_mode=True
    )

    res = await tester.place_test_call(config)
    assert res.success is False
    
    # Check attempt updated to failed
    attempt = await repository.get_call_attempt(res.call_attempt_id)
    assert attempt["status"] == "failed"
    assert attempt["failure_reason"] == "Busy signal"

@pytest.mark.asyncio
async def test_test_call_does_not_bypass_suppression_for_campaign_lead():
    repository = Repository()
    from compliance.dnc_registry import DatabaseDNCRegistry
    dnc = DatabaseDNCRegistry(repository)
    
    phone = "+15559999"
    campaign_id = "camp-dnc-test"
    await dnc.add(phone, reason="Do Not Call requested", campaign_id=campaign_id)

    tester = LiveCallTester(repository=repository)
    config = LiveCallTestConfig(
        phone_number=phone,
        operator="Jimmy",
        live_mode=True,
        campaign_id=campaign_id
    )

    res = await tester.place_test_call(config)
    assert res.success is False
    assert res.error == "BLOCKED_BY_DNC"
