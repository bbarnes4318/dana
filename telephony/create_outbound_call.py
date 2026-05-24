"""
LiveKit SIP Outbound Call Initiator
Places an outbound call through LiveKit SIP using the configured outbound trunk.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Dict, Any

# Ensure standard import capability
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telephony.telnyx_config import TelephonyConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

CALL_RESULT_FILE = "telephony/last_outbound_call.json"


async def main():
    parser = argparse.ArgumentParser(description="Place an outbound LiveKit SIP Call")
    parser.add_argument("--to", required=True, help="Destination phone number (E.164 format)")
    parser.add_argument("--room", help="Optional room name to join (default: dana-call-<uuid>)")
    parser.add_argument("--identity", help="Optional participant identity (default: prospect-<uuid>)")
    parser.add_argument("--metadata", help="Optional JSON string metadata for the participant")
    args = parser.parse_args()

    logger.info("Initializing LiveKit SIP Outbound Call Initiator...")

    # Load configuration
    try:
        config = TelephonyConfig()
        config.validate_api_keys()
    except Exception as e:
        logger.error("Configuration validation failed: %s", e)
        sys.exit(1)

    confirm_place_call = config.dana_confirm_place_call

    # Fallback to defaults or generate values
    call_to = args.to.strip()
    room_name = args.room.strip() if args.room else f"{config.dana_room_prefix}-{uuid.uuid4().hex[:8]}"
    participant_identity = args.identity.strip() if args.identity else f"prospect-{uuid.uuid4().hex[:8]}"
    
    # Parse metadata if provided
    metadata_str = ""
    if args.metadata:
        try:
            # Validate JSON
            parsed_metadata = json.loads(args.metadata)
            metadata_str = json.dumps(parsed_metadata)
        except json.JSONDecodeError as exc:
            logger.error("Invalid JSON metadata: %s", exc)
            sys.exit(1)

    def mask_num(num: str) -> str:
        if not num or num == "replace_me":
            return "unset"
        if len(num) <= 4:
            return "****"
        return f"******{num[-4:]}"

    planned_call = {
        "to_masked": mask_num(call_to),
        "from_masked": mask_num(config.dana_default_caller_id),
        "room_name": room_name,
        "participant_identity": participant_identity,
        "trunk_id_masked": mask_num(config.livekit_sip_outbound_trunk_id),
    }

    # Validate trunk ID exists for the call
    if not config.livekit_sip_outbound_trunk_id:
        logger.error("LIVEKIT_SIP_OUTBOUND_TRUNK_ID is not configured in environment.")
        sys.exit(1)

    if not confirm_place_call:
        logger.info("=========================================================================")
        logger.info("DRY-RUN MODE — No call will be placed in LiveKit.")
        logger.info("To place a real call, set: DANA_CONFIRM_PLACE_CALL=yes")
        logger.info("=========================================================================")
        logger.info("Planned Call Details:")
        logger.info("  To: %s", planned_call["to_masked"])
        logger.info("  From: %s", planned_call["from_masked"])
        logger.info("  Room: %s", planned_call["room_name"])
        logger.info("  Identity: %s", planned_call["participant_identity"])
        logger.info("  Trunk ID: %s", planned_call["trunk_id_masked"])
        
        # Save dry-run record
        dry_run_data = {
            "status": "dry_run",
            "to": planned_call["to_masked"],
            "from": planned_call["from_masked"],
            "room_name": planned_call["room_name"],
            "participant_identity": planned_call["participant_identity"],
            "trunk_id": planned_call["trunk_id_masked"],
            "placed_at": datetime.now(timezone.utc).isoformat()
        }
        with open(CALL_RESULT_FILE, "w", encoding="utf-8") as f:
            json.dump(dry_run_data, f, indent=2)
        logger.info("Dry-run call log saved to %s", CALL_RESULT_FILE)
        return

    logger.info("Connecting to LiveKit API...")
    try:
        from livekit import api
    except ImportError as e:
        logger.error("Failed to import LiveKit SDK. Is livekit-api installed? %s", e)
        sys.exit(1)

    # Instantiate API client
    lkapi = api.LiveKitAPI(
        url=config.livekit_url,
        api_key=config.livekit_api_key,
        api_secret=config.livekit_api_secret
    )

    # Safety: check if method exists on the client
    if not hasattr(lkapi, "sip") or not hasattr(lkapi.sip, "create_sip_participant"):
        logger.error("=========================================================================")
        logger.error("CRITICAL ERR: The installed LiveKit SDK version does not support SIP participant creation.")
        try:
            import importlib.metadata
            version = importlib.metadata.version("livekit-api")
            logger.error("Installed livekit-api package version: %s", version)
        except Exception:
            logger.error("Installed livekit-api package version: unknown")
        
        logger.error("Available API fields: %s", dir(lkapi))
        if hasattr(lkapi, "sip"):
            logger.error("Available SIP methods: %s", dir(lkapi.sip))
        logger.error("=========================================================================")
        await lkapi.aclose()
        sys.exit(1)

    logger.info("Executing LiveKit CreateSIPParticipant API call...")
    try:
        request = api.CreateSIPParticipantRequest(
            sip_trunk_id=config.livekit_sip_outbound_trunk_id,
            sip_call_to=call_to,
            room_name=room_name,
            participant_identity=participant_identity,
            participant_metadata=metadata_str
        )
        participant = await lkapi.sip.create_sip_participant(request)
        
        logger.info("Successfully initiated SIP call. Participant: %s", participant)

        result_data = {
            "status": "placed",
            "sip_participant_id": getattr(participant, "participant_id", "unknown"),
            "to": mask_num(call_to),
            "room_name": room_name,
            "participant_identity": participant_identity,
            "trunk_id": config.livekit_sip_outbound_trunk_id,
            "placed_at": datetime.now(timezone.utc).isoformat()
        }

        with open(CALL_RESULT_FILE, "w", encoding="utf-8") as f:
            json.dump(result_data, f, indent=2)
        logger.info("LiveKit SIP call state saved successfully to %s", CALL_RESULT_FILE)

    except Exception as e:
        logger.error("LiveKit API call failed: %s", e)
        sys.exit(1)
    finally:
        await lkapi.aclose()


if __name__ == "__main__":
    asyncio.run(main())
