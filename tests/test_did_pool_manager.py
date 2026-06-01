import os
import pytest
from unittest import mock
from datetime import datetime, timezone, timedelta

from storage.repository import Repository
from telephony.did_pool import DIDPoolManager
from storage.schemas import CallerIdSelectionConfig, CallerIdNumber

@pytest.fixture(autouse=True)
def clean_telephony_env():
    """Ensure a clean env for all DID pool tests by removing any real telemetry/telephony settings."""
    env_keys = [
        "DANA_TELEPHONY_PROVIDER",
        "DANA_OUTBOUND_CALLER_ID",
        "TELNYX_OUTBOUND_CALLER_ID",
        "TELNYX_DIDS",
        "TELNYX_PHONE_NUMBERS",
        "BULKVS_OUTBOUND_CALLER_ID",
        "BULKVS_DIDS",
        "BULKVS_PHONE_NUMBERS",
        "DANA_ALLOW_DANA_CALLER_ID_FOR_BULKVS",
        "SIGNALWIRE_OUTBOUND_CALLER_ID",
        "SIGNALWIRE_DIDS",
        "TWILIO_CALLER_ID",
        "TWILIO_PHONE_NUMBERS",
    ]
    old_vals = {}
    for key in env_keys:
        if key in os.environ:
            old_vals[key] = os.environ[key]
            del os.environ[key]
    yield
    for key, val in old_vals.items():
        os.environ[key] = val

@pytest.fixture
def repo(tmp_path):
    """Return a Repository backed by a tmp_path JsonlStore."""
    return Repository(data_dir=tmp_path)

@pytest.fixture
def manager(repo):
    """Return a DIDPoolManager backed by the temp Repository."""
    return DIDPoolManager(repo)

@pytest.mark.asyncio
async def test_telnyx_pool_loads_telnyx_dids(manager):
    """Test 1: Loads Telnyx candidates from environment variables."""
    env_vars = {
        "DANA_TELEPHONY_PROVIDER": "telnyx",
        "DANA_OUTBOUND_CALLER_ID": "+18651111111",
        "TELNYX_OUTBOUND_CALLER_ID": "+18652222222",
        "TELNYX_DIDS": "+18653333333, +18654444444",
        "TELNYX_PHONE_NUMBERS": "+18655555555",
        "SIGNALWIRE_DIDS": "+19999999999",
        "BULKVS_DIDS": "+18888888888",
    }
    with mock.patch.dict(os.environ, env_vars):
        numbers = await manager.list_numbers(provider="telnyx")
        phone_numbers = [n.phone_number for n in numbers]
        
        assert "+18651111111" in phone_numbers
        assert "+18652222222" in phone_numbers
        assert "+18653333333" in phone_numbers
        assert "+18654444444" in phone_numbers
        assert "+18655555555" in phone_numbers
        
        # Ignores other providers
        assert "+19999999999" not in phone_numbers
        assert "+18888888888" not in phone_numbers

@pytest.mark.asyncio
async def test_telnyx_pool_ignores_signalwire_dids(manager):
    """Test 2: Telnyx provider ignores SignalWire DIDs."""
    env_vars = {
        "DANA_TELEPHONY_PROVIDER": "telnyx",
        "TELNYX_DIDS": "+18651111111",
        "SIGNALWIRE_DIDS": "+19999999999",
    }
    with mock.patch.dict(os.environ, env_vars):
        numbers = await manager.list_numbers(provider="telnyx")
        phone_numbers = [n.phone_number for n in numbers]
        assert "+18651111111" in phone_numbers
        assert "+19999999999" not in phone_numbers

@pytest.mark.asyncio
async def test_telnyx_pool_ignores_bulkvs_dids_by_default(manager):
    """Test 3: Telnyx provider ignores BulkVS DIDs by default."""
    env_vars = {
        "DANA_TELEPHONY_PROVIDER": "telnyx",
        "TELNYX_DIDS": "+18651111111",
        "BULKVS_DIDS": "+18888888888",
    }
    with mock.patch.dict(os.environ, env_vars):
        numbers = await manager.list_numbers(provider="telnyx")
        phone_numbers = [n.phone_number for n in numbers]
        assert "+18651111111" in phone_numbers
        assert "+18888888888" not in phone_numbers

@pytest.mark.asyncio
async def test_bulkvs_pool_loads_bulkvs_dids(manager):
    """Test 4: BulkVS provider loads BulkVS DIDs."""
    env_vars = {
        "DANA_TELEPHONY_PROVIDER": "bulkvs",
        "BULKVS_DIDS": "+18888888888",
        "BULKVS_PHONE_NUMBERS": "+18887777777",
        "DANA_OUTBOUND_CALLER_ID": "+18651111111",
        "DANA_ALLOW_DANA_CALLER_ID_FOR_BULKVS": "true",
    }
    with mock.patch.dict(os.environ, env_vars):
        numbers = await manager.list_numbers(provider="bulkvs")
        phone_numbers = [n.phone_number for n in numbers]
        assert "+18888888888" in phone_numbers
        assert "+18887777777" in phone_numbers
        assert "+18651111111" in phone_numbers

@pytest.mark.asyncio
async def test_select_caller_id_round_robin(manager):
    """Test 5: Round Robin strategy selects the least recently used number."""
    # Add numbers manually to pool
    await manager.add_number(provider="telnyx", phone_number="+18651111111", daily_cap=10, hourly_cap=5)
    await manager.add_number(provider="telnyx", phone_number="+18652222222", daily_cap=10, hourly_cap=5)
    
    # Record use for one of them
    await manager.record_call_use("+18651111111")
    
    config = CallerIdSelectionConfig(
        provider="telnyx",
        strategy="round_robin",
    )
    # The one not used (or used furthest in past) should be selected (+18652222222)
    res = await manager.select_caller_id(config)
    assert res.success is True
    assert res.phone_number == "+18652222222"

@pytest.mark.asyncio
async def test_select_caller_id_least_used(manager):
    """Test 6: Least Used strategy selects the number with the fewest calls today."""
    await manager.add_number(provider="telnyx", phone_number="+18651111111", daily_cap=10, hourly_cap=5)
    await manager.add_number(provider="telnyx", phone_number="+18652222222", daily_cap=10, hourly_cap=5)
    
    # Make calls today
    await manager.record_call_use("+18651111111")
    await manager.record_call_use("+18651111111")
    await manager.record_call_use("+18652222222")
    
    config = CallerIdSelectionConfig(
        provider="telnyx",
        strategy="least_used",
    )
    res = await manager.select_caller_id(config)
    assert res.success is True
    assert res.phone_number == "+18652222222"

@pytest.mark.asyncio
async def test_select_caller_id_blocks_paused_numbers(manager):
    """Test 7: Paused numbers are never selected."""
    await manager.add_number(provider="telnyx", phone_number="+18651111111")
    await manager.pause_number("+18651111111")
    
    config = CallerIdSelectionConfig(
        provider="telnyx",
        strategy="round_robin",
    )
    res = await manager.select_caller_id(config)
    assert res.success is False
    assert "No eligible numbers" in res.reason

@pytest.mark.asyncio
async def test_select_caller_id_blocks_cooldown_numbers(manager):
    """Test 8: Cooldown numbers are excluded until cooldown expires."""
    now = datetime.now(timezone.utc)
    future = now + timedelta(minutes=10)
    await manager.add_number(
        provider="telnyx",
        phone_number="+18651111111",
        cooldown_until=future.isoformat(),
    )
    
    config = CallerIdSelectionConfig(
        provider="telnyx",
        strategy="round_robin",
    )
    res = await manager.select_caller_id(config)
    assert res.success is False
    
    # After cooldown expires, it becomes eligible
    past = now - timedelta(minutes=1)
    await manager.add_number(
        provider="telnyx",
        phone_number="+18651111111",
        cooldown_until=past.isoformat(),
    )
    res2 = await manager.select_caller_id(config)
    assert res2.success is True
    assert res2.phone_number == "+18651111111"

@pytest.mark.asyncio
async def test_select_caller_id_respects_daily_cap(manager):
    """Test 9: Numbers hitting their daily cap are excluded."""
    await manager.add_number(provider="telnyx", phone_number="+18651111111", daily_cap=1)
    
    # First call uses up the daily cap
    await manager.record_call_use("+18651111111")
    
    config = CallerIdSelectionConfig(
        provider="telnyx",
        strategy="round_robin",
    )
    res = await manager.select_caller_id(config)
    assert res.success is False

@pytest.mark.asyncio
async def test_select_caller_id_respects_hourly_cap(manager):
    """Test 10: Numbers hitting their hourly cap are excluded."""
    await manager.add_number(provider="telnyx", phone_number="+18651111111", hourly_cap=1)
    
    # First call uses up the hourly cap
    await manager.record_call_use("+18651111111")
    
    config = CallerIdSelectionConfig(
        provider="telnyx",
        strategy="round_robin",
    )
    res = await manager.select_caller_id(config)
    assert res.success is False

@pytest.mark.asyncio
async def test_cross_provider_blocked_by_default(manager):
    """Test 11: Cross-provider selection is blocked by default."""
    await manager.add_number(provider="bulkvs", phone_number="+18888888888")
    
    config = CallerIdSelectionConfig(
        provider="telnyx",
        strategy="round_robin",
        allow_cross_provider=False,
    )
    res = await manager.select_caller_id(config)
    assert res.success is False

@pytest.mark.asyncio
async def test_cross_provider_allowed_only_with_warning(manager):
    """Test 12: Cross-provider selection allowed with warning when configured."""
    await manager.add_number(provider="bulkvs", phone_number="+18888888888")
    
    config = CallerIdSelectionConfig(
        provider="telnyx",
        strategy="round_robin",
        allow_cross_provider=True,
    )
    res = await manager.select_caller_id(config)
    assert res.success is True
    assert res.phone_number == "+18888888888"
    assert any("Cross-provider caller ID" in w for w in res.warnings)

@pytest.mark.asyncio
async def test_record_call_use_updates_counts(manager):
    """Test 13: record_call_use correctly updates call counters."""
    await manager.add_number(provider="telnyx", phone_number="+18651111111")
    await manager.record_call_use("+18651111111")
    
    numbers = await manager.list_numbers(provider="telnyx")
    match = [n for n in numbers if n.phone_number == "+18651111111"][0]
    assert match.calls_today == 1
    assert match.calls_this_hour == 1

@pytest.mark.asyncio
async def test_no_eligible_numbers_returns_clear_failure(manager):
    """Test 14: Clear failure response when no eligible numbers exist."""
    config = CallerIdSelectionConfig(
        provider="telnyx",
        strategy="round_robin",
    )
    res = await manager.select_caller_id(config)
    assert res.success is False
    assert "No eligible numbers found in did pool for provider telnyx." in res.reason
