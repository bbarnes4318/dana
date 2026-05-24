"""
LiveKit SIP Outbound Trunk Creator
Creates a LiveKit outbound SIP trunk pointing to Telnyx using the LiveKit Python SDK.
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Dict, Any

# Ensure standard import capability
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telephony.telnyx_config import TelephonyConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

TRUNK_RESULT_FILE = "telephony/livekit_trunk_result.json"


async def main():
    logger.info("Initializing LiveKit SIP Outbound Trunk Provisioner...")

    # Load configuration
    try:
        config = TelephonyConfig()
        # Only validate LiveKit credentials if registering/creating the trunk is confirmed
        if config.dana_confirm_create_livekit_trunk:
            config.validate_for_livekit()
    except Exception as e:
        logger.error("Configuration validation failed: %s", e)
        sys.exit(1)

    confirm_create = config.dana_confirm_create_livekit_trunk

    # Non-sensitive planned information
    def mask_num(num: str) -> str:
        if not num or num == "replace_me":
            return "unset"
        if len(num) <= 4:
            return "****"
        return f"******{num[-4:]}"

    planned_trunk = {
        "name": "Dana Telnyx Outbound Trunk",
        "address": config.telnyx_sip_address,
        "outbound_number_masked": mask_num(config.telnyx_outbound_number),
        "sip_username_present": bool(config.telnyx_sip_username and config.telnyx_sip_username != "replace_me"),
        "sip_password_present": bool(config.telnyx_sip_password and config.telnyx_sip_password != "replace_me"),
    }

    if not confirm_create:
        logger.info("=========================================================================")
        logger.info("DRY-RUN MODE — No trunk will be created in LiveKit.")
        logger.info("To run live creation, set: DANA_CONFIRM_CREATE_LIVEKIT_TRUNK=yes")
        logger.info("=========================================================================")
        logger.info("Planned Outbound Trunk Settings:")
        logger.info("  Name: %s", planned_trunk["name"])
        logger.info("  Address: %s", planned_trunk["address"])
        logger.info("  Outbound Number: %s", planned_trunk["outbound_number_masked"])
        logger.info("  SIP Username Present: %s", planned_trunk["sip_username_present"])
        logger.info("  SIP Password Present: %s", planned_trunk["sip_password_present"])
        
        # Save dry-run record
        dry_run_data = {
            "id": None,
            "real_resource_created": False,
            "status": "dry_run",
            "would_create": True,
            "trunk_name": planned_trunk["name"],
            "sip_address": planned_trunk["address"],
            "outbound_number": planned_trunk["outbound_number_masked"],
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        with open(TRUNK_RESULT_FILE, "w", encoding="utf-8") as f:
            json.dump(dry_run_data, f, indent=2)
        logger.info("Dry-run result saved to %s", TRUNK_RESULT_FILE)
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
    if not hasattr(lkapi, "sip") or not hasattr(lkapi.sip, "create_sip_outbound_trunk"):
        logger.error("=========================================================================")
        logger.error("CRITICAL ERR: The installed LiveKit SDK version does not support SIP administration.")
        # Print debug helper details
        try:
            import pkg_resources
            version = pkg_resources.get_distribution("livekit-api").version
            logger.error("Installed livekit-api package version: %s", version)
        except Exception:
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

    # Validate parameters are present
    if not config.telnyx_sip_username or config.telnyx_sip_username == "replace_me":
        logger.error("SIP username is missing or replace_me. Set TELNYX_SIP_USERNAME first.")
        await lkapi.aclose()
        sys.exit(1)
    if not config.telnyx_sip_password or config.telnyx_sip_password == "replace_me":
        logger.error("SIP password is missing or replace_me. Set TELNYX_SIP_PASSWORD first.")
        await lkapi.aclose()
        sys.exit(1)
    if not config.telnyx_outbound_number or config.telnyx_outbound_number == "replace_me":
        logger.error("Outbound number is missing or replace_me. Set TELNYX_OUTBOUND_NUMBER first.")
        await lkapi.aclose()
        sys.exit(1)

    logger.info("Executing LiveKit CreateSIPOutboundTrunk API call...")
    try:
        # Build outbound trunk info
        trunk_info = api.SIPOutboundTrunkInfo(
            name="Dana Telnyx Outbound Trunk",
            address=config.telnyx_sip_address,
            auth_username=config.telnyx_sip_username,
            auth_password=config.telnyx_sip_password,
            numbers=[config.telnyx_outbound_number]
        )

        request = api.CreateSIPOutboundTrunkRequest(trunk=trunk_info)
        trunk = await lkapi.sip.create_sip_outbound_trunk(request)
        
        trunk_id = trunk.sip_trunk_id
        logger.info("Successfully created SIP outbound trunk. ID: %s", trunk_id)

        result_data = {
            "status": "created",
            "trunk_id": trunk_id,
            "trunk_name": trunk.name,
            "sip_address": trunk.address,
            "outbound_number": mask_num(config.telnyx_outbound_number),
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        with open(TRUNK_RESULT_FILE, "w", encoding="utf-8") as f:
            json.dump(result_data, f, indent=2)
        logger.info("LiveKit SIP trunk state saved successfully to %s", TRUNK_RESULT_FILE)

        # Print operators instructions
        print(f"LIVEKIT_SIP_OUTBOUND_TRUNK_ID={trunk_id}", flush=True)

    except Exception as e:
        logger.error("LiveKit API call failed: %s", e)
        sys.exit(1)
    finally:
        await lkapi.aclose()


if __name__ == "__main__":
    asyncio.run(main())
