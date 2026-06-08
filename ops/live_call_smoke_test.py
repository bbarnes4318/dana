import os
import sys
import uuid
import argparse
import asyncio
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

# Setup sys.path to root directory
root_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(root_dir))

from config.runtime_env import get_runtime_env
from storage.repository import Repository
from ops.readiness import run_readiness_checks
from telephony.livekit_adapter import LiveKitOutboundAdapter, LiveKitDialConfig
from telephony.lead_importer import CampaignLeadImporter
from compliance.dnc_registry import DatabaseDNCRegistry

def is_valid_e164(phone: str) -> bool:
    """Validate if phone number matches E.164 pattern (+ followed by 10-15 digits)."""
    return bool(re.match(r"^\+\d{10,15}$", phone))

async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Dana Controlled Live-Call Smoke Test CLI")
    parser.add_argument("--to", required=False, help="Destination phone number in E.164 format (e.g. +1XXXXXXXXXX)")
    parser.add_argument("--from", dest="from_num", required=False, help="Outbound caller ID phone number in E.164 format (e.g. +1XXXXXXXXXX)")
    parser.add_argument("--dry-run", action="store_true", help="Validate configuration and run checklist without placing the call")
    args = parser.parse_args()

    # 1. Setup logging
    data_dir_env = os.getenv("DANA_DATA_DIR")
    if data_dir_env:
        log_dir = Path(data_dir_env) / "live_call_smoke_tests"
    else:
        test_dir = root_dir / "data"
        try:
            test_dir.mkdir(parents=True, exist_ok=True)
            dummy_file = test_dir / ".write_test"
            dummy_file.write_text("test")
            dummy_file.unlink()
            log_dir = root_dir / "data" / "live_call_smoke_tests"
        except Exception:
            log_dir = Path("/tmp/live_call_smoke_tests")
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"smoke_test_{timestamp}.log"

    logger = logging.getLogger("live_call_smoke_test")
    logger.setLevel(logging.INFO)

    # Prevent duplicate handlers if script is re-run
    if logger.handlers:
        logger.handlers.clear()

    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")

    # File Handler
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    logger.info("Initializing Live Call Smoke Test...")
    logger.info(f"Arguments: --to={args.to}, --from={args.from_num}, --dry-run={args.dry_run}")
    logger.info(f"Logging all output to {log_file}")

    # 2. Verify explicit phone numbers
    if not args.to or not args.from_num:
        logger.error("Error: Both --to and --from phone numbers must be explicitly provided.")
        return 1

    to_number = args.to.strip()
    from_number = args.from_num.strip()

    if not is_valid_e164(to_number):
        logger.error(f"Error: Target number {to_number} is not in valid E.164 format (+XXXXXXXXXXX).")
        return 1
    if not is_valid_e164(from_number):
        logger.error(f"Error: Caller ID number {from_number} is not in valid E.164 format (+XXXXXXXXXXX).")
        return 1

    # 3. Check Live Mode Restrictions
    controlled_live = os.getenv("DANA_CONTROLLED_LIVE_TEST", "false").lower() in ("true", "1", "yes")
    if not args.dry_run and not controlled_live:
        logger.error("Error: Live calls are blocked. DANA_CONTROLLED_LIVE_TEST=true must be set in environment for live mode.")
        return 1

    # 4. Run readiness checks
    logger.info("Running system readiness checks...")
    success, readiness_results = await run_readiness_checks()
    if not success:
        logger.error("Readiness check FAILED - System is not ready for live outbound telephony.")
        for name, (ok, msg) in readiness_results.items():
            status = "PASS" if ok else "FAIL"
            logger.error(f"  [{status}] [{name.upper()}]: {msg}")
        return 1
    
    logger.info("All readiness checks PASSED.")

    # 5. Check DNC and suppression lists
    logger.info(f"Checking DNC/suppression list for destination number {to_number}...")
    repository = Repository()
    
    try:
        importer = CampaignLeadImporter(repository)
        suppressed, dnc_reason = await importer.is_suppressed(to_number)
        if suppressed:
            logger.error(f"Error: Destination number {to_number} is suppressed: {dnc_reason}")
            return 1

        dnc_registry = DatabaseDNCRegistry(repository)
        on_dnc = await dnc_registry.contains(to_number, campaign_id="smoke-test")
        if on_dnc:
            logger.error(f"Error: Destination number {to_number} is in the campaign DNC registry.")
            return 1
    except Exception as e:
        logger.error(f"Error while performing DNC/suppression check: {e}")
        return 1

    logger.info("DNC and suppression checks passed. Number is clean.")

    # 6. Execute Call or Dry Run
    env = get_runtime_env()
    outbound_trunk_id = env.get("livekit_sip_outbound_trunk_id")
    if not outbound_trunk_id:
        # Check provider config as fallback
        outbound_trunk_id = os.getenv("LIVEKIT_SIP_OUTBOUND_TRUNK_ID")

    logger.info(f"Configured Outbound SIP Trunk ID: {outbound_trunk_id}")

    if args.dry_run:
        logger.info("========== DRY-RUN VALIDATION COMPLETE ==========")
        logger.info("Validation was successful. Would place an outbound call:")
        logger.info(f"  To: {to_number}")
        logger.info(f"  From: {from_number}")
        logger.info(f"  Trunk: {outbound_trunk_id}")
        logger.info("No real call was placed during dry-run.")
        return 0

    # Live Mode Execution
    logger.info("========== EXECUTING CONTROLLED LIVE CALL ==========")
    adapter = LiveKitOutboundAdapter()
    
    room_name = f"smoke-test-room-{uuid.uuid4().hex[:8]}"
    part_identity = f"smoke-test-participant-{uuid.uuid4().hex[:8]}"

    dial_config = LiveKitDialConfig(
        live_mode=True,
        livekit_url=env.get("livekit_url"),
        api_key=env.get("livekit_api_key"),
        api_secret=env.get("livekit_api_secret"),
        outbound_trunk_id=outbound_trunk_id,
        room_name=room_name,
        phone_number=to_number,
        caller_id=from_number,
        participant_identity=part_identity,
        wait_until_answered=True,
        krisp_enabled=True,
        metadata={
            "smoke_test": True,
            "initiated_by": "smoke_test_cli",
            "timestamp": timestamp
        }
    )

    logger.info(f"Placing outbound call request to LiveKit Room: {room_name}")
    logger.info(f"Participant Identity: {part_identity}")
    
    try:
        result = await adapter.dial(dial_config)
        if result.success:
            logger.info("Live call placed successfully.")
            logger.info(f"  LiveKit Participant ID: {result.livekit_participant_id}")
            logger.info(f"  LiveKit SIP Call ID: {result.livekit_sip_call_id}")
            logger.info(f"  Provider Call ID: {result.provider_call_id}")
            logger.info(f"  SIP Call Status: {result.sip_call_status}")
            logger.info(f"  SIP Status Code: {result.sip_status_code}")
            logger.info(f"  SIP Status: {result.sip_status}")
            return 0
        else:
            logger.error(f"Live call placement failed: {result.message}")
            if result.error:
                logger.error(f"  Error code: {result.error}")
            return 1
    except Exception as e:
        logger.error(f"Failed to dial: unexpected error: {e}")
        return 1
    finally:
        await repository.close()

def main():
    try:
        # Check if event loop already runs (e.g. within certain environments)
        sys.exit(asyncio.run(main_async()))
    except RuntimeError:
        loop = asyncio.get_event_loop()
        sys.exit(loop.run_until_complete(main_async()))

if __name__ == "__main__":
    main()
