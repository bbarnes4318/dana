import os
import sys
import json
import pytest
from unittest import mock
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path

# Add repo root to path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from storage.repository import Repository
from telephony.did_pool import DIDPoolManager
from telephony.telnyx_inventory import (
    TelnyxInventoryClient,
    TelnyxInventoryConfig,
    TelnyxNumberRecord,
    TelnyxDIDInventorySyncService
)
from telephony.live_telephony_readiness import LiveTelephonyReadinessChecker


@pytest.fixture
def repo(tmp_path):
    return Repository(data_dir=tmp_path)


@pytest.fixture
def clean_env():
    """Ensure clean telephony env variables for tests."""
    env_keys = [
        "TELNYX_API_KEY",
        "DANA_TELEPHONY_PROVIDER",
        "DANA_OUTBOUND_CALLER_ID",
        "TELNYX_OUTBOUND_CALLER_ID",
        "TELNYX_DIDS",
        "TELNYX_PHONE_NUMBERS",
        "BULKVS_DIDS"
    ]
    old = {}
    for k in env_keys:
        if k in os.environ:
            old[k] = os.environ[k]
            del os.environ[k]
    yield
    for k, v in old.items():
        os.environ[k] = v


@pytest.mark.asyncio
async def test_telnyx_client_requires_api_key():
    """Test 1: TelnyxInventoryClient requires API key."""
    client = TelnyxInventoryClient(api_key=None)
    with pytest.raises(ValueError, match="TELNYX_API_KEY is required"):
        await client.list_owned_phone_numbers()


@pytest.mark.asyncio
async def test_telnyx_client_parses_phone_numbers_response(clean_env):
    """Test 2: TelnyxInventoryClient parses phone numbers response successfully."""
    client = TelnyxInventoryClient(api_key="mock-key-123")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [
            {
                "phone_number": "+18651111111",
                "connection_id": "conn-abc-123",
                "friendly_name": "Test Trunk Number",
                "status": "active",
                "tags": ["dana", "outbound"]
            }
        ],
        "links": {}
    }

    # Patch httpx.AsyncClient.get
    with mock.patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        records = await client.list_owned_phone_numbers()

        assert len(records) == 1
        record = records[0]
        assert record.phone_number == "+18651111111"
        assert record.friendly_name == "Test Trunk Number"
        assert record.connection_id == "conn-abc-123"
        assert record.status == "active"
        assert "dana" in record.tags


@pytest.mark.asyncio
async def test_sync_imports_telnyx_numbers_to_did_pool(repo, clean_env):
    """Test 3: Sync imports fetched Telnyx numbers into the local database DID pool."""
    service = TelnyxDIDInventorySyncService(repo)

    mock_records = [
        TelnyxNumberRecord(
            phone_number="+18651111111",
            friendly_name="Main DID",
            status="active",
            connection_id="conn-123",
            metadata={"id": "num-1"}
        )
    ]

    with mock.patch.object(TelnyxInventoryClient, "list_owned_phone_numbers", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = mock_records

        config = TelnyxInventoryConfig(api_key="key-abc", dry_run=False)
        result = await service.sync(config)

        assert result.success is True
        assert result.fetched_count == 1
        assert result.imported_count == 1

        # Check DB
        numbers = await service.pool_manager.list_numbers(provider="telnyx")
        assert len(numbers) == 1
        assert numbers[0].phone_number == "+18651111111"
        assert numbers[0].provider == "telnyx"
        assert numbers[0].source == "telnyx_api"


@pytest.mark.asyncio
async def test_sync_marks_numbers_verified_for_telnyx(repo, clean_env):
    """Test 4: Sync marks imported numbers as verified_for_provider=True."""
    service = TelnyxDIDInventorySyncService(repo)

    mock_records = [
        TelnyxNumberRecord(phone_number="+18651111111", metadata={})
    ]

    with mock.patch.object(TelnyxInventoryClient, "list_owned_phone_numbers", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = mock_records

        config = TelnyxInventoryConfig(api_key="key-abc", dry_run=False)
        await service.sync(config)

        numbers = await service.pool_manager.list_numbers(provider="telnyx")
        assert numbers[0].verified_for_provider is True


@pytest.mark.asyncio
async def test_sync_dry_run_does_not_save(repo, clean_env):
    """Test 5: Dry-run does not write to the database."""
    service = TelnyxDIDInventorySyncService(repo)

    mock_records = [
        TelnyxNumberRecord(phone_number="+18651111111", metadata={})
    ]

    with mock.patch.object(TelnyxInventoryClient, "list_owned_phone_numbers", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = mock_records

        config = TelnyxInventoryConfig(api_key="key-abc", dry_run=True)
        result = await service.sync(config)

        assert result.dry_run is True
        assert result.imported_count == 1

        # Database must still be empty since it was dry-run
        db_dids = await repo.list_dids(provider="telnyx")
        assert len(db_dids) == 0


@pytest.mark.asyncio
async def test_sync_skips_invalid_numbers(repo, clean_env):
    """Test 6: Sync skips non-E.164 formats defensively."""
    service = TelnyxDIDInventorySyncService(repo)

    mock_records = [
        TelnyxNumberRecord(phone_number="12345", metadata={}),  # invalid (too short, no +)
        TelnyxNumberRecord(phone_number="+18652222222", metadata={})  # valid
    ]

    with mock.patch.object(TelnyxInventoryClient, "list_owned_phone_numbers", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = mock_records

        config = TelnyxInventoryConfig(api_key="key-abc", dry_run=False, require_e164=True)
        result = await service.sync(config)

        assert result.fetched_count == 2
        assert result.skipped_count == 1
        assert result.imported_count == 1

        numbers = await service.pool_manager.list_numbers(provider="telnyx")
        assert len(numbers) == 1
        assert numbers[0].phone_number == "+18652222222"


@pytest.mark.asyncio
async def test_sync_never_prints_api_key(repo, clean_env):
    """Test 7: Client exceptions scrub any API credentials before raising/reporting."""
    api_key = "secret_api_key_xyz_123"
    client = TelnyxInventoryClient(api_key=api_key)

    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = f"Invalid API credentials for key {api_key}."

    with mock.patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        
        with pytest.raises(Exception) as excinfo:
            await client.list_owned_phone_numbers()
        
        err_str = str(excinfo.value)
        assert api_key not in err_str
        assert "TELNYX_API_KEY_REDACTED" in err_str


@pytest.mark.asyncio
async def test_cli_sync_outputs_clean_json(repo, clean_env):
    """Test 8: CLI script prints a valid JSON payload to stdout and logs to stderr."""
    import subprocess
    import sys

    cli_path = Path(__file__).resolve().parent.parent / "scripts" / "sync_telnyx_dids.py"
    
    # We run in dry-run mode and bypass API call by putting a dummy API key,
    # but since it would call Telnyx API, we mock the service sync response.
    # To run a clean mock via subprocess is tricky, so we patch main logic instead.
    from scripts.sync_telnyx_dids import main as cli_main

    mock_result = MagicMock()
    mock_result.success = True
    mock_result.fetched_count = 5
    mock_result.imported_count = 5
    mock_result.updated_count = 0
    mock_result.skipped_count = 0
    mock_result.failed_count = 0
    mock_result.dry_run = True
    mock_result.errors = []
    mock_result.warnings = []
    mock_result.model_dump.return_value = {"success": True, "dry_run": True, "fetched_count": 5}

    with mock.patch("telephony.telnyx_inventory.TelnyxDIDInventorySyncService.sync", new_callable=AsyncMock) as mock_sync:
        mock_sync.return_value = mock_result

        test_args = ["scripts/sync_telnyx_dids.py", "--dry-run", "--api-key", "dummy-key"]
        with mock.patch.object(sys, "argv", test_args):
            # Capture print stdout
            stdout_lines = []
            def mock_print(msg, *args, **kwargs):
                stdout_lines.append(msg)

            with mock.patch("builtins.print", mock_print), pytest.raises(SystemExit) as exit_info:
                await cli_main()

            assert exit_info.value.code == 0
            assert len(stdout_lines) == 1
            parsed = json.loads(stdout_lines[0])
            assert parsed["success"] is True
            assert parsed["dry_run"] is True


@pytest.mark.asyncio
async def test_web_sync_endpoint_calls_console(repo, clean_env, tmp_path):
    """Test 9: Web console endpoint POST /api/telephony/dids/sync-telnyx routes properly."""
    from ops.web_console import TrainingWebConsoleServer, TrainingWebConsoleConfig
    from ops.training_console import ConsoleActionResult
    
    config = TrainingWebConsoleConfig(data_dir=str(tmp_path))
    server = TrainingWebConsoleServer(config, repository=repo)
    
    mock_action_result = ConsoleActionResult(
        action="sync_telnyx_dids",
        success=True,
        message="Synced successfully",
        data={"fetched_count": 3}
    )
    
    with mock.patch.object(server.console, "sync_telnyx_dids", new_callable=AsyncMock) as mock_sync:
        mock_sync.return_value = mock_action_result
        
        status, response = await server.handle_api(
            "POST",
            "/api/telephony/dids/sync-telnyx",
            body={"dry_run": True, "daily_cap": 80, "hourly_cap": 15}
        )
        
        assert status == 200
        assert response["success"] is True
        assert response["data"]["fetched_count"] == 3
        mock_sync.assert_called_once_with(dry_run=True, daily_cap=80, hourly_cap=15)


@pytest.mark.asyncio
async def test_readiness_accepts_db_telnyx_did_pool(repo, clean_env):
    """Test 10: Readiness succeeds if TELNYX_DIDS is empty but DB DID pool has active Telnyx numbers."""
    checker = LiveTelephonyReadinessChecker(repository=repo)

    # Pre-configure environment
    os.environ["TELEPHONY_LIVE_MODE"] = "true"
    os.environ["DANA_ENABLE_OUTBOUND_DIALER"] = "true"
    os.environ["LIVEKIT_URL"] = "wss://test.livekit"
    os.environ["LIVEKIT_API_KEY"] = "key-test"
    os.environ["LIVEKIT_API_SECRET"] = "secret-test"
    os.environ["LIVEKIT_SIP_OUTBOUND_TRUNK_ID"] = "trunk-test"
    os.environ["DANA_TELEPHONY_PROVIDER"] = "telnyx"
    os.environ["TELNYX_API_KEY"] = "mock-api-key"

    # Add DID to database pool
    pool = DIDPoolManager(repo)
    await pool.add_number(provider="telnyx", phone_number="+18651111111", verified_for_provider=True)

    result = await checker.run()
    assert result.ready is True
    assert result.caller_id_present is True
    assert result.caller_id_source == "pool:manual"


@pytest.mark.asyncio
async def test_readiness_fails_when_no_telnyx_did_pool(repo, clean_env):
    """Test 11: Readiness fails with custom warning if no Telnyx DID pool exists in DB or env."""
    checker = LiveTelephonyReadinessChecker(repository=repo)

    # Pre-configure environment (No TELNYX_DIDS, no DB DIDs)
    os.environ["TELEPHONY_LIVE_MODE"] = "true"
    os.environ["DANA_ENABLE_OUTBOUND_DIALER"] = "true"
    os.environ["LIVEKIT_URL"] = "wss://test.livekit"
    os.environ["LIVEKIT_API_KEY"] = "key-test"
    os.environ["LIVEKIT_API_SECRET"] = "secret-test"
    os.environ["LIVEKIT_SIP_OUTBOUND_TRUNK_ID"] = "trunk-test"
    os.environ["DANA_TELEPHONY_PROVIDER"] = "telnyx"
    os.environ["TELNYX_API_KEY"] = "mock-api-key"

    result = await checker.run()
    assert result.ready is False
    assert result.caller_id_present is False
    assert any("No Telnyx caller ID pool found. Run python scripts/sync_telnyx_dids.py or set TELNYX_DIDS." in f for f in result.failures)


@pytest.mark.asyncio
async def test_bulkvs_numbers_not_imported_into_telnyx_pool(repo, clean_env):
    """Test 12: Sync strictly ignores non-Telnyx providers and does not import BulkVS/SignalWire DIDs."""
    service = TelnyxDIDInventorySyncService(repo)

    # If the API fetches BulkVS numbers or if BulkVS env config exists, they must not end up in the Telnyx database pool sync.
    mock_records = [
        TelnyxNumberRecord(phone_number="+18651111111", metadata={}),  # Telnyx
    ]

    with mock.patch.object(TelnyxInventoryClient, "list_owned_phone_numbers", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = mock_records

        config = TelnyxInventoryConfig(api_key="key-abc", dry_run=False)
        
        # Configure some BulkVS environment variables
        os.environ["BULKVS_DIDS"] = "+18882222222"
        os.environ["SIGNALWIRE_DIDS"] = "+18883333333"

        await service.sync(config)

        # Check DB DIDs
        db_telnyx = await repo.list_dids(provider="telnyx")
        assert len(db_telnyx) == 1
        assert db_telnyx[0]["phone_number"] == "+18651111111"

        db_bulkvs = await repo.list_dids(provider="bulkvs")
        assert len(db_bulkvs) == 0
