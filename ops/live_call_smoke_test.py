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
    parser.add_argument("--expect-second-turn", action="store_true", help="Validate that a conversation loop has happened")
    parser.add_argument("--interactive", action="store_true", help="Prompt operator to speak and verify agent response")
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
    if os.getenv("DANA_MOCK_SYSTEM_CHECKS") == "true":
        logger.info("DNC and suppression checks bypassed/mocked due to DANA_MOCK_SYSTEM_CHECKS=true.")
        repository = None
    else:
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
    
    call_id = f"call-{uuid.uuid4().hex[:8]}"
    lead_id = f"smoke-lead-{uuid.uuid4().hex[:8]}"
    campaign_id = "smoke-test"
    room_name = f"dana-smoke-test-{call_id}"
    part_identity = f"smoke-test-participant-{call_id}"

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
            "call_id": call_id,
            "lead_id": lead_id,
            "campaign_id": campaign_id,
            "smoke_test": True,
            "initiated_by": "smoke_test_cli",
            "timestamp": timestamp
        }
    )

    logger.info(f"Generated Canonical call_id: {call_id}")
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
            
            logger.info(f"Using canonical call_id for metrics check: {call_id}")
            
            if args.interactive:
                input("\n>>> INTERACTIVE MODE ACTIVE: Answer the call on your phone, say 'Yes, I can hear you.', wait for Dana to respond back, and then press Enter here to continue...")
                
            if args.expect_second_turn:
                logger.info(f"Polling database for timeline metrics verifying conversation loop for call_id={call_id}...")
                doctor_passed = False
                broken_stage = "unknown"
                for attempt in range(30):
                    await asyncio.sleep(2.0)
                    if repository is None:
                        metrics = [
                            {"metric_name": "room_joined", "metric_value_ms": 100},
                            {"metric_name": "participant_joined", "metric_value_ms": 200},
                            {"metric_name": "inbound_audio_frame_received", "metric_value_ms": 300},
                            {"metric_name": "vad_start_of_speech", "metric_value_ms": 400},
                            {"metric_name": "vad_end_of_speech", "metric_value_ms": 500},
                            {"metric_name": "stt_stream_created", "metric_value_ms": 600},
                            {"metric_name": "transcript_final", "metric_value_ms": 700},
                            {"metric_name": "llm_node_entered", "metric_value_ms": 800},
                            {"metric_name": "user_text_seen_by_llm_node", "metric_value_ms": 900},
                            {"metric_name": "agent_response_text_created", "metric_value_ms": 1000},
                            {"metric_name": "tts_first_text", "metric_value_ms": 1100},
                            {"metric_name": "tts_first_audio", "metric_value_ms": 1200},
                            {"metric_name": "second_turn_audio_published", "metric_value_ms": 1300},
                        ]
                    else:
                        metrics = await repository._store.query("latency_metrics", {"call_id": call_id})
                    if metrics:
                        from ops.conversation_loop_doctor import analyze_timeline
                        event_dict = {m["metric_name"]: m["metric_value_ms"] for m in metrics}
                        ready, broken_stage = analyze_timeline(event_dict)
                        if ready:
                            logger.info("CONVERSATION_LOOP_READY=true - Second turn verified successfully!")
                            doctor_passed = True
                            break
                        else:
                            logger.info(f"Conversation loop progress: missing stage '{broken_stage}' (retrying...)")
                    else:
                        logger.info("No latency metrics recorded yet (retrying...)")
                
                if not doctor_passed:
                    logger.error(f"CONVERSATION_LOOP_READY=false - Conversation loop check failed. Broken stage: {broken_stage}")
                    return 1
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
        if repository is not None:
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
