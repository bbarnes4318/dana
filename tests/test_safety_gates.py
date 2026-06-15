import pytest
from telephony.agent_availability import LicensedAgent
from telephony.warm_bridge import LiveKitWarmBridgeProvider
from telephony.cold_transfer import TelnyxColdTransferProvider


@pytest.mark.asyncio
async def test_warm_bridge_safety_gate_blocks(monkeypatch) -> None:
    # 1. Safety gate not set -> should return dry_run / transfer_not_confirmed
    monkeypatch.delenv("DANA_CONFIRM_TRANSFER_CALL", raising=False)
    
    agent = LicensedAgent(agent_id="ag-1", name="Sam", phone_number="+15550000001", licensed_states=["*"])
    provider = LiveKitWarmBridgeProvider()
    
    result = await provider.initiate_warm_bridge(
        room_name="test-room",
        agent=agent,
        summary="Internal Summary"
    )
    
    assert result.success is False
    assert result.reason == "transfer_not_confirmed"
    assert result.transfer_mode == "dry_run"


@pytest.mark.asyncio
async def test_cold_transfer_safety_gate_blocks(monkeypatch) -> None:
    # 1. Safety gate not set -> should return dry_run / transfer_not_confirmed
    monkeypatch.delenv("DANA_CONFIRM_TRANSFER_CALL", raising=False)
    
    provider = TelnyxColdTransferProvider()
    
    result = await provider.initiate_cold_transfer(
        room_name="test-room",
        phone_number="+15559999999",
        call_control_id="mock_call_control_id"
    )
    
    assert result.success is False
    assert result.reason == "transfer_not_confirmed"
    assert result.transfer_mode == "dry_run"


@pytest.mark.asyncio
async def test_warm_bridge_unconfigured_credentials(monkeypatch) -> None:
    # Safety gate is YES, but credentials missing
    monkeypatch.setenv("DANA_CONFIRM_TRANSFER_CALL", "yes")
    monkeypatch.delenv("LIVEKIT_URL", raising=False)
    
    agent = LicensedAgent(agent_id="ag-1", name="Sam", phone_number="+15550000001", licensed_states=["*"])
    provider = LiveKitWarmBridgeProvider()
    
    result = await provider.initiate_warm_bridge(
        room_name="test-room",
        agent=agent,
        summary="Internal Summary"
    )
    
    assert result.success is False
    assert result.reason == "provider_not_configured"
    assert result.transfer_mode == "failed"


@pytest.mark.asyncio
async def test_cold_transfer_unconfigured_credentials(monkeypatch) -> None:
    # Safety gate is YES, but credentials missing
    monkeypatch.setenv("DANA_CONFIRM_TRANSFER_CALL", "yes")
    monkeypatch.delenv("TELNYX_API_KEY", raising=False)
    
    provider = TelnyxColdTransferProvider()
    
    result = await provider.initiate_cold_transfer(
        room_name="test-room",
        phone_number="+15559999999",
        call_control_id="mock_call_control_id"
    )
    
    assert result.success is False
    assert result.reason == "provider_not_configured"
    assert result.transfer_mode == "failed"
