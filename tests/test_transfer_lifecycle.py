import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from telephony.agent_availability import LicensedAgent
from telephony.fe_transfer import fe_transfer, release_call_agent, _agent_store
from telephony.cold_transfer import TelnyxColdTransferProvider, ColdTransferResult
from telephony.warm_bridge import LiveKitWarmBridgeProvider, DanaLeaveStrategy, WarmBridgeResult
from storage.repository import Repository


@pytest.fixture(autouse=True)
def clean_agent_store():
    """Ensure the agent availability store is clean before/after tests."""
    _agent_store._agents.clear()
    yield
    _agent_store._agents.clear()


@pytest.mark.asyncio
async def test_warm_bridge_does_not_require_call_control_id(monkeypatch):
    """Test A: warm_bridge does not require Telnyx call_control_id."""
    monkeypatch.setenv("DANA_TRANSFER_MODE", "warm_bridge")
    monkeypatch.setenv("DANA_CONFIRM_TRANSFER_CALL", "yes")
    monkeypatch.setenv("LIVEKIT_URL", "ws://mock-livekit")
    monkeypatch.setenv("LIVEKIT_API_KEY", "key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret")
    monkeypatch.setenv("LIVEKIT_SIP_OUTBOUND_TRUNK_ID", "trunk_id")

    agent = LicensedAgent(
        agent_id="agent-123",
        name="Sam",
        phone_number="+15550000001",
        licensed_states=["*"],
        status="available"
    )
    _agent_store.add_agent(agent)

    # Mock CreateSIPParticipantRequest and LiveKitAPI in telephony.warm_bridge
    mock_participant = MagicMock()
    mock_participant.participant_id = "test-participant-123"

    mock_lkapi = MagicMock()
    mock_lkapi.aclose = AsyncMock()
    mock_lkapi.sip = MagicMock()
    mock_lkapi.sip.create_sip_participant = AsyncMock(return_value=mock_participant)

    with patch("livekit.api.LiveKitAPI", return_value=mock_lkapi):
        res = await fe_transfer(
            room_name="test-room",
            prospect_identity="John Doe",
            licensed_agent_phone_number="+15550000001",
            call_summary="Summary",
            transfer_reason="Consent",
            lead_profile={"lead_id": "lead-1", "lead_phone_e164": "+15550000002"},
            lead_state=None,
            call_id="call-123",
            call_control_id=None  # Explicitly None
        )

        assert res.success is True
        assert res.transfer_mode == "warm_bridge"
        assert res.provider_call_id == "test-participant-123"


@pytest.mark.asyncio
async def test_cold_transfer_fails_clearly_without_call_control_id():
    """Test B: cold_transfer fails clearly without call_control_id."""
    provider = TelnyxColdTransferProvider()

    # Call with call_control_id=None
    result = await provider.initiate_cold_transfer(
        room_name="test-room",
        phone_number="+15559999999",
        call_control_id=None
    )

    assert result.success is False
    assert result.reason == "telnyx_call_control_not_available"
    assert result.transfer_mode == "failed"


@pytest.mark.asyncio
async def test_warm_bridge_creates_sip_participant_for_licensed_agent(monkeypatch):
    """Test C: warm_bridge creates a LiveKit SIP participant for the licensed agent."""
    monkeypatch.setenv("DANA_TRANSFER_MODE", "warm_bridge")
    monkeypatch.setenv("DANA_CONFIRM_TRANSFER_CALL", "yes")
    monkeypatch.setenv("LIVEKIT_URL", "ws://mock-livekit")
    monkeypatch.setenv("LIVEKIT_API_KEY", "key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret")
    monkeypatch.setenv("LIVEKIT_SIP_OUTBOUND_TRUNK_ID", "trunk-trunk")

    agent = LicensedAgent(
        agent_id="agent-abc",
        name="Alice",
        phone_number="+15550000005",
        licensed_states=["*"],
        status="available"
    )
    _agent_store.add_agent(agent)

    mock_participant = MagicMock()
    mock_participant.participant_id = "participant-abc"

    mock_lkapi = MagicMock()
    mock_lkapi.aclose = AsyncMock()
    mock_lkapi.sip = MagicMock()
    mock_lkapi.sip.create_sip_participant = AsyncMock(return_value=mock_participant)

    with patch("livekit.api.LiveKitAPI", return_value=mock_lkapi), \
         patch("livekit.api.CreateSIPParticipantRequest") as mock_req_cls:
        
        await fe_transfer(
            room_name="test-room",
            prospect_identity="Prospect Name",
            licensed_agent_phone_number=None,
            call_summary="Awesome Prospect",
            transfer_reason="Agreement",
            lead_profile={"lead_id": "lead-abc", "lead_phone_e164": "+15550000002"},
            lead_state=None,
            call_id="call-abc",
            call_control_id=None
        )

        # Ensure create_sip_participant was called with correct trunk and destination number
        mock_req_cls.assert_called_once()
        _, kwargs = mock_req_cls.call_args
        assert kwargs["sip_trunk_id"] == "trunk-trunk"
        assert kwargs["sip_call_to"] == "+15550000005"
        assert kwargs["room_name"] == "test-room"
        assert kwargs["participant_identity"] == "agent-agent-abc"


@pytest.mark.asyncio
async def test_dana_is_removed_after_successful_bridge():
    """Test D: Dana is removed after successful bridge."""
    mock_lkapi = MagicMock()
    mock_lkapi.room = MagicMock()
    mock_lkapi.room.remove_participant = AsyncMock()

    # Explicitly mock RoomParticipantIdentity in test
    with patch("livekit.api.RoomParticipantIdentity") as mock_identity_cls:
        strategy = DanaLeaveStrategy()
        success = await strategy.execute_leave(
            room_name="room-123",
            agent_identity="dana_voice_agent",
            lkapi=mock_lkapi
        )

        assert success is True
        mock_identity_cls.assert_called_once_with(room="room-123", identity="dana_voice_agent")
        mock_lkapi.room.remove_participant.assert_called_once()


@pytest.mark.asyncio
async def test_prospect_is_not_disconnected_when_dana_leaves():
    """Test E: prospect is not disconnected when Dana leaves/mutes."""
    mock_lkapi = MagicMock()
    mock_lkapi.room = MagicMock()
    mock_lkapi.room.remove_participant = AsyncMock()

    # The leave strategy only removes Dana participant and must NOT call delete_room
    strategy = DanaLeaveStrategy()
    await strategy.execute_leave("room-123", "dana_voice_agent", lkapi=mock_lkapi)

    # Assert that delete_room is not called on lkapi
    assert not mock_lkapi.room.delete_room.called


@pytest.mark.asyncio
async def test_licensed_agent_reservation_lifecycle(monkeypatch):
    """Test F: licensed agent reservation is released after success/failure/disconnect."""
    monkeypatch.setenv("DANA_TRANSFER_MODE", "warm_bridge")
    monkeypatch.setenv("DANA_CONFIRM_TRANSFER_CALL", "yes")
    monkeypatch.setenv("LIVEKIT_URL", "ws://mock-livekit")
    monkeypatch.setenv("LIVEKIT_API_KEY", "key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret")
    monkeypatch.setenv("LIVEKIT_SIP_OUTBOUND_TRUNK_ID", "trunk_id")

    agent = LicensedAgent(
        agent_id="agent-xyz",
        name="Alice",
        phone_number="+15550000009",
        licensed_states=["*"],
        status="available"
    )
    _agent_store.add_agent(agent)

    mock_participant = MagicMock()
    mock_participant.participant_id = "part-xyz"

    mock_lkapi = MagicMock()
    mock_lkapi.aclose = AsyncMock()
    mock_lkapi.sip = MagicMock()
    mock_lkapi.sip.create_sip_participant = AsyncMock(return_value=mock_participant)

    with patch("livekit.api.LiveKitAPI", return_value=mock_lkapi):
        # 1. Warm bridge success -> agent is reserved (busy)
        res = await fe_transfer(
            room_name="test-room",
            prospect_identity="Prospect Name",
            licensed_agent_phone_number="+15550000009",
            call_summary="Summary",
            transfer_reason="Consent",
            lead_profile={"lead_id": "lead-xyz", "lead_phone_e164": "+15550000002"},
            lead_state=None,
            call_id="call-xyz",
            call_control_id=None
        )
        assert res.success is True
        assert agent.status == "busy"

        # 2. Session disconnect -> calling release_call_agent releases the agent
        await release_call_agent("call-xyz")
        assert agent.status == "available"


@pytest.mark.asyncio
async def test_confirm_transfer_call_must_be_yes_before_real_transfer(monkeypatch):
    """Test G: DANA_CONFIRM_TRANSFER_CALL must be yes before real transfer."""
    monkeypatch.setenv("DANA_CONFIRM_TRANSFER_CALL", "no")

    agent = LicensedAgent(
        agent_id="agent-g",
        name="Gary",
        phone_number="+15551234567",
        licensed_states=["*"],
        status="available"
    )
    _agent_store.add_agent(agent)

    res = await fe_transfer(
        room_name="test-room",
        prospect_identity="Gary Lead",
        licensed_agent_phone_number="+15551234567",
        call_summary="Summary",
        transfer_reason="Consent",
        lead_profile={"lead_id": "lead-g", "lead_phone_e164": "+15550000002"},
        lead_state=None,
        call_id="call-g"
    )

    # Should fall back to dry_run (success=False)
    assert res.success is False
    assert res.reason == "transfer_not_confirmed"
    assert res.transfer_mode == "dry_run"


@pytest.mark.asyncio
async def test_transfer_mode_routing(monkeypatch):
    """Test H: DANA_TRANSFER_MODE=warm_bridge routes through LiveKit warm bridge."""
    monkeypatch.setenv("DANA_TRANSFER_MODE", "warm_bridge")
    monkeypatch.setenv("DANA_CONFIRM_TRANSFER_CALL", "yes")
    monkeypatch.setenv("LIVEKIT_URL", "ws://mock-livekit")
    monkeypatch.setenv("LIVEKIT_API_KEY", "key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret")
    monkeypatch.setenv("LIVEKIT_SIP_OUTBOUND_TRUNK_ID", "trunk_id")

    agent = LicensedAgent(
        agent_id="agent-h",
        name="Harry",
        phone_number="+15551234568",
        licensed_states=["*"],
        status="available"
    )
    _agent_store.add_agent(agent)

    mock_participant = MagicMock()
    mock_participant.participant_id = "part-h"

    mock_lkapi = MagicMock()
    mock_lkapi.aclose = AsyncMock()
    mock_lkapi.sip = MagicMock()
    mock_lkapi.sip.create_sip_participant = AsyncMock(return_value=mock_participant)

    with patch("livekit.api.LiveKitAPI", return_value=mock_lkapi):
        res = await fe_transfer(
            room_name="test-room",
            prospect_identity="Gary Lead",
            licensed_agent_phone_number="+15551234568",
            call_summary="Summary",
            transfer_reason="Consent",
            lead_profile={"lead_id": "lead-h", "lead_phone_e164": "+15550000002"},
            lead_state=None,
            call_id="call-h"
        )

        assert res.success is True
        assert res.transfer_mode == "warm_bridge"


@pytest.mark.asyncio
async def test_dana_leave_strategy_fails_in_production(monkeypatch):
    """Test that in production/live mode, execute_leave returns False if remove_participant fails
    or if lkapi is missing, and warm_bridge fails with dana_leave_failed."""
    monkeypatch.setenv("DANA_TRANSFER_MODE", "warm_bridge")
    monkeypatch.setenv("DANA_CONFIRM_TRANSFER_CALL", "yes")
    monkeypatch.setenv("LIVEKIT_URL", "ws://mock-livekit")
    monkeypatch.setenv("LIVEKIT_API_KEY", "key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret")
    monkeypatch.setenv("LIVEKIT_SIP_OUTBOUND_TRUNK_ID", "trunk_id")
    # Set to production mode
    monkeypatch.setenv("DANA_RUNTIME_ENV", "production")

    strategy = DanaLeaveStrategy()
    
    # 1. execute_leave should return False if lkapi is missing in production
    assert await strategy.execute_leave("room-123", "dana_voice_agent", lkapi=None) is False

    # 2. execute_leave should return False if remove_participant raises an exception in production
    mock_lkapi_fail = MagicMock()
    mock_lkapi_fail.room = MagicMock()
    mock_lkapi_fail.room.remove_participant = MagicMock(side_effect=Exception("API offline"))
    assert await strategy.execute_leave("room-123", "dana_voice_agent", lkapi=mock_lkapi_fail) is False

    # 3. propagate to initiate_warm_bridge returning success=False
    agent = LicensedAgent(
        agent_id="agent-prod-fail",
        name="Prod Agent",
        phone_number="+15551234569",
        licensed_states=["*"],
        status="available"
    )
    _agent_store.add_agent(agent)

    mock_participant = MagicMock()
    mock_participant.participant_id = "part-prod-fail"

    mock_lkapi = MagicMock()
    mock_lkapi.aclose = AsyncMock()
    mock_lkapi.sip = MagicMock()
    mock_lkapi.sip.create_sip_participant = AsyncMock(return_value=mock_participant)
    # Make remove_participant fail
    mock_lkapi.room = MagicMock()
    mock_lkapi.room.remove_participant = MagicMock(side_effect=Exception("API offline"))

    with patch("livekit.api.LiveKitAPI", return_value=mock_lkapi):
        res = await fe_transfer(
            room_name="test-room",
            prospect_identity="Gary Lead",
            licensed_agent_phone_number="+15551234569",
            call_summary="Summary",
            transfer_reason="Consent",
            lead_profile={"lead_id": "lead-prod-fail", "lead_phone_e164": "+15550000002"},
            lead_state=None,
            call_id="call-prod-fail"
        )

        assert res.success is False
        assert res.reason == "dana_leave_failed"
        assert res.transfer_mode == "failed"
