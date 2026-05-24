"""
Telnyx Provisioning Engine
Inspects and prepares necessary Telnyx SIP trunks, connections, and numbers for Dana.
"""

import asyncio
import json
import logging
import os
import sys
from typing import Dict, Any, Optional

# Ensure standard import capability
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telephony.telnyx_config import TelephonyConfig
from telephony.telnyx_api import TelnyxAPIClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

RESOURCES_FILE = "telephony/telnyx_resources.json"
DRY_RUN_FILE = "telephony/telnyx_dry_run.json"


async def main():
    logger.info("Initializing Telnyx Provisioning...")
    
    # Load configuration
    try:
        config = TelephonyConfig()
        config.validate_api_keys()
    except Exception as e:
        logger.error("Configuration validation failed: %s", e)
        sys.exit(1)

    client = TelnyxAPIClient(config)
    confirm_provision = config.dana_confirm_telnyx_provision

    # Prepare provisioning plan/status
    plan: Dict[str, Any] = {
        "connection_id": config.telnyx_connection_id or None,
        "outbound_voice_profile_id": config.telnyx_outbound_voice_profile_id or None,
        "phone_number_id": config.telnyx_phone_number_id or None,
        "outbound_number": config.telnyx_outbound_number or None,
        "sip_address": config.telnyx_sip_address or "sip.telnyx.com",
        "sip_username_present": bool(config.telnyx_sip_username and config.telnyx_sip_username != "replace_me"),
        "sip_password_present": bool(config.telnyx_sip_password and config.telnyx_sip_password != "replace_me"),
    }

    if not confirm_provision:
        logger.info("=========================================================================")
        logger.info("DRY-RUN MODE — No API calls will be made, and no resources will be changed.")
        logger.info("To run live provisioning, set: DANA_CONFIRM_TELNYX_PROVISION=yes")
        logger.info("=========================================================================")
        
        # Log planned actions
        missing_vars = []
        if not config.telnyx_connection_id:
            missing_vars.append("TELNYX_CONNECTION_ID")
        if not config.telnyx_outbound_voice_profile_id:
            missing_vars.append("TELNYX_OUTBOUND_VOICE_PROFILE_ID")
        if not config.telnyx_phone_number_id:
            missing_vars.append("TELNYX_PHONE_NUMBER_ID")
        if not config.telnyx_outbound_number or config.telnyx_outbound_number == "replace_me":
            missing_vars.append("TELNYX_OUTBOUND_NUMBER")
        if not config.telnyx_sip_username or config.telnyx_sip_username == "replace_me":
            missing_vars.append("TELNYX_SIP_USERNAME")
        if not config.telnyx_sip_password or config.telnyx_sip_password == "replace_me":
            missing_vars.append("TELNYX_SIP_PASSWORD")
            
        logger.info("Missing configuration variables to complete setup: %s", missing_vars)
        logger.info("Planned Dry-Run Action: Retrieve or create SIP Credential Connection and associate phone number.")
        
        # Save dry-run output file
        dry_run_data = {
            "status": "dry_run",
            "missing_variables": missing_vars,
            "planned_resources": plan
        }
        with open(DRY_RUN_FILE, "w", encoding="utf-8") as f:
            json.dump(dry_run_data, f, indent=2)
        logger.info("Dry-run plan saved to %s", DRY_RUN_FILE)
        return

    logger.info("Provisioning Live Resources...")
    
    # 1. Verify or find Outbound Voice Profile
    if not plan["outbound_voice_profile_id"]:
        logger.info("Checking for existing Outbound Voice Profile...")
        profiles = await client.list_outbound_voice_profiles()
        if profiles:
            # Look for a profile named 'dana-voice-profile' or use the first one
            match = next((p for p in profiles if p.get("name") == "dana-voice-profile"), None)
            if match:
                logger.info("Found existing voice profile: %s (ID: %s)", match.get("name"), match.get("id"))
                plan["outbound_voice_profile_id"] = match.get("id")
            else:
                logger.info("No matching 'dana-voice-profile' found. Using first available profile: %s", profiles[0].get("id"))
                plan["outbound_voice_profile_id"] = profiles[0].get("id")
        
        # If still missing and mutation allowed, create it
        if not plan["outbound_voice_profile_id"]:
            if config.dana_confirm_telnyx_mutation:
                logger.info("Creating new Outbound Voice Profile 'dana-voice-profile'...")
                new_profile = await client.create_outbound_voice_profile("dana-voice-profile")
                if new_profile:
                    plan["outbound_voice_profile_id"] = new_profile.get("id")
                    logger.info("Created voice profile: %s", plan["outbound_voice_profile_id"])
            else:
                logger.warning("No voice profile ID provided, and DANA_CONFIRM_TELNYX_MUTATION is no. Cannot create one.")

    # 2. Verify or find SIP Credential Connection
    if not plan["connection_id"]:
        logger.info("Checking for existing Credential Connection...")
        connections = await client.list_credential_connections()
        if connections:
            match = next((c for c in connections if c.get("connection_name") == "dana-sip-connection"), None)
            if match:
                logger.info("Found existing connection: %s (ID: %s)", match.get("connection_name"), match.get("id"))
                plan["connection_id"] = match.get("id")
                
                # Fetch SIP username from connection details
                sip_username = match.get("username") or match.get("sip_username")
                if sip_username:
                    plan["sip_username_present"] = True
                    logger.info("SIP username identified: %s", sip_username)
            else:
                logger.info("No matching 'dana-sip-connection' found. Using first connection: %s", connections[0].get("id"))
                plan["connection_id"] = connections[0].get("id")
                plan["sip_username_present"] = bool(connections[0].get("username") or connections[0].get("sip_username"))

        # If still missing and mutation allowed, create it
        if not plan["connection_id"]:
            if config.dana_confirm_telnyx_mutation:
                logger.info("Creating new Credential Connection 'dana-sip-connection'...")
                new_conn = await client.create_credential_connection("dana-sip-connection")
                if new_conn:
                    plan["connection_id"] = new_conn.get("id")
                    plan["sip_username_present"] = bool(new_conn.get("username") or new_conn.get("sip_username"))
                    logger.info("Created connection: %s", plan["connection_id"])
            else:
                logger.warning("No connection ID provided, and DANA_CONFIRM_TELNYX_MUTATION is no. Cannot create one.")

    # 3. Handle Phone Number Assignment
    if not plan["phone_number_id"] or not plan["outbound_number"] or plan["outbound_number"] == "replace_me":
        logger.info("Checking owned phone numbers...")
        numbers = await client.list_phone_numbers()
        if numbers:
            # Select first available number
            selected = numbers[0]
            plan["phone_number_id"] = selected.get("id")
            plan["outbound_number"] = selected.get("phone_number")
            logger.info("Selected owned phone number: %s (ID: %s)", plan["outbound_number"], plan["phone_number_id"])

            # Link phone number to SIP connection if missing and mutation is enabled
            if plan["connection_id"] and selected.get("connection_id") != plan["connection_id"]:
                if config.dana_confirm_telnyx_mutation:
                    logger.info("Assigning number ID %s to connection ID %s...", plan["phone_number_id"], plan["connection_id"])
                    await client.assign_phone_number_connection(plan["phone_number_id"], plan["connection_id"])
                else:
                    logger.info("OUTSTANDING ACTION: Phone number is not assigned to the SIP connection. Enable DANA_CONFIRM_TELNYX_MUTATION=yes to auto-assign.")
        else:
            logger.warning("No owned phone numbers found on the account.")
            
            # Offer number search and purchase instructions
            if config.dana_confirm_purchase_number:
                logger.info("Searching for US local numbers...")
                available = await client.search_available_phone_numbers({"filter[country_code]": "US", "filter[limit]": 1})
                if available:
                    target_num = available[0].get("phone_number")
                    logger.info("Found available number: %s. Ordering now...", target_num)
                    order = await client.purchase_phone_number(target_num)
                    if order:
                        logger.info("Successfully ordered number: %s", target_num)
                        # Re-inspect to get IDs
                        logger.info("Please rerun this script once the order has finished provisioning on Telnyx.")
                else:
                    logger.error("No available numbers found to order.")
            else:
                logger.info("OUTSTANDING ACTION: Purchase a phone number via the Telnyx dashboard, or set DANA_CONFIRM_PURCHASE_NUMBER=yes to auto-purchase.")

    # 4. Outbound Voice Profile Connection Linkage
    if plan["connection_id"] and plan["outbound_voice_profile_id"]:
        if config.dana_confirm_telnyx_mutation:
            logger.info("Assuring Connection is linked to Outbound Voice Profile...")
            await client.update_credential_connection(
                plan["connection_id"],
                {"outbound_voice_profile_id": plan["outbound_voice_profile_id"]}
            )

    # Save resource mappings to JSON
    try:
        with open(RESOURCES_FILE, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2)
        logger.info("Telnyx resources state saved successfully to %s", RESOURCES_FILE)
    except Exception as e:
        logger.error("Failed to write resources file: %s", e)

    # Print operator instructions
    logger.info("=========================================================================")
    logger.info("PROVISIONING REPORT & NEXT OPERATOR STEPS")
    logger.info("=========================================================================")
    logger.info("Copy the following configuration values into your /opt/dana/.env file:")
    logger.info("  TELNYX_CONNECTION_ID=%s", plan["connection_id"] or "replace_me")
    logger.info("  TELNYX_OUTBOUND_VOICE_PROFILE_ID=%s", plan["outbound_voice_profile_id"] or "replace_me")
    logger.info("  TELNYX_PHONE_NUMBER_ID=%s", plan["phone_number_id"] or "replace_me")
    logger.info("  TELNYX_OUTBOUND_NUMBER=%s", plan["outbound_number"] or "replace_me")
    logger.info("-------------------------------------------------------------------------")
    
    if plan["connection_id"] and plan["outbound_number"]:
        logger.info("✅ LiveKit SIP Outbound Trunk can now be created using these resources.")
    else:
        logger.info("⚠️ Telnyx resources are incomplete. Please verify settings in your Telnyx Mission Control dashboard.")


if __name__ == "__main__":
    asyncio.run(main())
