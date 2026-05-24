"""
Unified Telnyx & LiveKit Provisioning Orchestrator
Orchestrates the discovery, provisioning, and registration of telephony SIP resources for Dana.
"""

import os
import sys
import json
import logging
import asyncio
from datetime import datetime, timezone

# Ensure standard import capability
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telephony.telnyx_config import TelephonyConfig
from telephony.telnyx_api import TelnyxAPIClient

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("telephony.orchestrator")


def mask_str(val: str, sensitive: bool = True) -> str:
    """Mask credentials or phone numbers for safe display."""
    if not val or val == "replace_me":
        return "unset"
    if sensitive:
        return "********"
    if len(val) <= 4:
        return "****"
    return f"******{val[-4:]}"


class ProvisioningOrchestrator:
    def __init__(self):
        self.config = TelephonyConfig()
        self.client = TelnyxAPIClient(self.config)
        self.mode = self.config.dana_provision_mode.lower()
        if self.mode not in ("plan", "inspect", "apply"):
            logger.warning("Invalid DANA_PROVISION_MODE '%s'. Defaulting to 'plan'.", self.mode)
            self.mode = "plan"

        # Report states for final summary
        self.report = {
            "inspected": "no",
            "voice_profile": "skipped",
            "connection": "skipped",
            "sip_credentials": "env",
            "phone_number": "skipped",
            "livekit_trunk": "skipped",
            "env_written": "no",
            "operator_action": "None"
        }

    def _determine_env_file(self) -> str:
        """Determine path to save secrets env file."""
        if os.path.isdir("/opt/dana"):
            return "/opt/dana/.env.telephony"
        return "telephony/provisioned.env"

    def _write_outputs(self, details: dict, status: str):
        """Write env output file and metadata JSON file."""
        env_file = self._determine_env_file()
        try:
            # Prepare env contents
            env_lines = [
                f"TELNYX_CONNECTION_ID={details.get('connection_id') or ''}",
                f"TELNYX_OUTBOUND_VOICE_PROFILE_ID={details.get('outbound_voice_profile_id') or ''}",
                f"TELNYX_PHONE_NUMBER_ID={details.get('phone_number_id') or ''}",
                f"TELNYX_OUTBOUND_NUMBER={details.get('outbound_number') or ''}",
                f"TELNYX_SIP_ADDRESS={details.get('sip_address') or 'sip.telnyx.com'}",
                f"TELNYX_SIP_USERNAME={details.get('sip_username') or ''}",
                f"TELNYX_SIP_PASSWORD={details.get('sip_password') or ''}",
                f"LIVEKIT_SIP_OUTBOUND_TRUNK_ID={details.get('livekit_sip_outbound_trunk_id') or ''}",
                f"DANA_DEFAULT_CALLER_ID={details.get('outbound_number') or ''}",
                "DANA_ROOM_PREFIX=dana-call"
            ]
            
            with open(env_file, "w", encoding="utf-8") as f:
                f.write("\n".join(env_lines) + "\n")
            
            # chmod 600 immediately
            try:
                os.chmod(env_file, 0o600)
            except Exception as ce:
                logger.warning("Could not set file permissions (chmod 600) on %s: %s", env_file, ce)
                
            self.report["env_written"] = "yes"
            logger.info("Saved secret environment variables to: %s (permissions set to 600)", env_file)
        except Exception as e:
            logger.error("Failed to write env output file: %s", e)

        # Write resources JSON (no secrets allowed)
        json_file = "telephony/provisioned_resources.json"
        try:
            meta = {
                "connection_id": details.get("connection_id"),
                "outbound_voice_profile_id": details.get("outbound_voice_profile_id"),
                "phone_number_id": details.get("phone_number_id"),
                "outbound_number": mask_str(details.get("outbound_number"), sensitive=False),
                "livekit_sip_outbound_trunk_id": details.get("livekit_sip_outbound_trunk_id"),
                "status": status,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)
            logger.info("Saved metadata JSON to: %s", json_file)
        except Exception as e:
            logger.error("Failed to write metadata JSON: %s", e)

    def print_report(self, status: str):
        """Print the final completion report structured as requested."""
        print("\n=========================================================================")
        print(f"TELEPHONY PROVISIONING REPORT (Status: {status})")
        print("=========================================================================")
        print(f"1. Telnyx account inspected: {self.report['inspected']}")
        print(f"2. Outbound voice profile: {self.report['voice_profile']}")
        print(f"3. SIP credential connection: {self.report['connection']}")
        print(f"4. SIP username/password: {self.report['sip_credentials']}")
        print(f"5. Phone number: {self.report['phone_number']}")
        print(f"6. LiveKit trunk: {self.report['livekit_trunk']}")
        print(f"7. provisioned.env written: {self.report['env_written']}")
        print(f"8. Remaining operator action: {self.report['operator_action']}")
        print("=========================================================================\n")

    async def run(self):
        logger.info("Starting provisioner in '%s' mode...", self.mode)

        if self.mode == "plan":
            return await self._run_plan()
        elif self.mode == "inspect":
            return await self._run_inspect()
        elif self.mode == "apply":
            return await self._run_apply()

    async def _run_plan(self):
        print("\n=========================================================================")
        print("PROVISIONING PLAN MODE (Dry-Run — No Network Calls, No Mutations)")
        print("=========================================================================")
        
        # Verify inputs present in config
        print(f"TELNYX_API_KEY: {'configured' if self.config.telnyx_api_key else 'missing'}")
        print(f"LIVEKIT_URL: {'configured' if self.config.livekit_url else 'missing'}")
        print(f"LIVEKIT_API_KEY: {'configured' if self.config.livekit_api_key else 'missing'}")
        print(f"LIVEKIT_API_SECRET: {'configured' if self.config.livekit_api_secret else 'missing'}")
        print("--- Optional inputs / overrides ---")
        print(f"TELNYX_CONNECTION_ID: {self.config.telnyx_connection_id or 'not set'}")
        print(f"TELNYX_OUTBOUND_VOICE_PROFILE_ID: {self.config.telnyx_outbound_voice_profile_id or 'not set'}")
        print(f"TELNYX_PHONE_NUMBER_ID: {self.config.telnyx_phone_number_id or 'not set'}")
        print(f"TELNYX_OUTBOUND_NUMBER: {mask_str(self.config.telnyx_outbound_number, sensitive=False)}")
        print(f"TELNYX_SIP_USERNAME: {mask_str(self.config.telnyx_sip_username, sensitive=False)}")
        print(f"TELNYX_SIP_PASSWORD: {mask_str(self.config.telnyx_sip_password, sensitive=True)}")
        print(f"LIVEKIT_SIP_OUTBOUND_TRUNK_ID: {self.config.livekit_sip_outbound_trunk_id or 'not set'}")
        print("\n--- Planned Actions ---")
        
        if self.config.telnyx_connection_id:
            print(f"- Will reuse existing Telnyx credential connection ID: {self.config.telnyx_connection_id}")
        else:
            print("- Will search for existing credential connection named 'dana-sip-connection'")
            if self.config.dana_confirm_telnyx_mutation:
                print("  -> If not found, WILL create new credential connection 'dana-sip-connection'")
            else:
                print("  -> If not found, WILL fail (requires DANA_CONFIRM_TELNYX_MUTATION=yes)")

        if self.config.telnyx_outbound_voice_profile_id:
            print(f"- Will reuse existing Outbound Voice Profile ID: {self.config.telnyx_outbound_voice_profile_id}")
        else:
            print("- Will search for existing outbound voice profile named 'dana-voice-profile'")
            if self.config.dana_confirm_telnyx_mutation:
                print("  -> If not found, WILL create new profile 'dana-voice-profile'")
            else:
                print("  -> If not found, WILL fail (requires DANA_CONFIRM_TELNYX_MUTATION=yes)")

        if self.config.telnyx_outbound_number:
            print(f"- Will verify and reuse configured phone number: {self.config.telnyx_outbound_number}")
        else:
            print("- Will search for existing owned numbers assigned to the connection/profile")
            if self.config.dana_confirm_purchase_number:
                print(f"- If none found, WILL search and purchase a number in country: '{self.config.telnyx_purchase_country or 'US'}'")
                print(f"  Purchase Area Code: {self.config.telnyx_purchase_area_code or 'any'}")
                print(f"  Purchase Locality: {self.config.telnyx_purchase_locality or 'any'}")
            else:
                print("- If none found, WILL fail (number purchase not confirmed)")

        if self.config.livekit_sip_outbound_trunk_id:
            print(f"- Will reuse existing LiveKit Outbound Trunk ID: {self.config.livekit_sip_outbound_trunk_id}")
        else:
            print("- Will check if matching trunk exists in LiveKit (prevents duplicates)")
            if self.config.dana_confirm_create_livekit_trunk:
                print("  -> If matching trunk not found, WILL register new LiveKit Outbound SIP Trunk")
            else:
                print("  -> If matching trunk not found, WILL fail (requires DANA_CONFIRM_CREATE_LIVEKIT_TRUNK=yes)")
                
        print("=========================================================================\n")
        self.print_report("planned")
        sys.exit(0)

    async def _run_inspect(self):
        logger.info("Executing inspect mode...")
        # Inspect mode requires read confirmation
        if not self.config.dana_confirm_telnyx_read:
            logger.error("DANA_CONFIRM_TELNYX_READ=yes is required for inspect mode.")
            self.report["operator_action"] = "Set DANA_CONFIRM_TELNYX_READ=yes in environment."
            self.print_report("failed_requires_operator_action")
            sys.exit(1)

        try:
            self.config.validate_for_telnyx(write_required=False)
        except ValueError as e:
            logger.error("Validation failed: %s", e)
            self.report["operator_action"] = str(e)
            self.print_report("failed_requires_operator_action")
            sys.exit(1)

        print("\n=========================================================================")
        print("PROVISIONING INSPECT MODE (Read-Only API Inspection)")
        print("=========================================================================")

        # 1. Fetch Outbound Voice Profiles
        logger.info("Listing Outbound Voice Profiles...")
        profiles = await self.client.list_outbound_voice_profiles()
        if profiles is not None:
            self.report["inspected"] = "yes"
            print("\n--- Existing Telnyx Outbound Voice Profiles ---")
            for p in profiles:
                print(f"ID: {p.get('id')} | Name: {p.get('name')} | Traffic: {p.get('traffic_type')}")
        else:
            print("\n--- Failed to inspect Outbound Voice Profiles (API/credentials error) ---")

        # 2. Fetch Credential Connections
        logger.info("Listing Credential Connections...")
        connections = await self.client.list_credential_connections()
        if connections is not None:
            self.report["inspected"] = "yes"
            print("\n--- Existing Telnyx Credential Connections ---")
            for c in connections:
                print(f"ID: {c.get('id')} | Name: {c.get('connection_name')} | Username: {c.get('username') or c.get('sip_username')}")
        else:
            print("\n--- Failed to inspect Credential Connections (API/credentials error) ---")

        # 3. Fetch Phone Numbers
        logger.info("Listing Owned Phone Numbers...")
        numbers = await self.client.list_phone_numbers()
        if numbers is not None:
            self.report["inspected"] = "yes"
            print("\n--- Existing Owned Phone Numbers ---")
            for n in numbers:
                print(f"ID: {n.get('id')} | Number: {n.get('phone_number')} | Connection ID: {n.get('connection_id')}")
        else:
            print("\n--- Failed to inspect Phone Numbers (API/credentials error) ---")

        # 4. Fetch LiveKit SIP Outbound Trunks if credentials and SDK are available
        has_lk = False
        try:
            self.config.validate_for_livekit()
            from livekit import api as lk_api
            has_lk = True
        except Exception:
            pass

        if has_lk:
            logger.info("Connecting to LiveKit to list Outbound SIP Trunks...")
            try:
                from livekit import api as lk_api
                lkapi = lk_api.LiveKitAPI(
                    url=self.config.livekit_url,
                    api_key=self.config.livekit_api_key,
                    api_secret=self.config.livekit_api_secret
                )
                if hasattr(lkapi, "sip") and hasattr(lkapi.sip, "list_sip_outbound_trunk"):
                    req = lk_api.ListSIPOutboundTrunkRequest()
                    res = await lkapi.sip.list_sip_outbound_trunk(req)
                    trunks = getattr(res, "results", getattr(res, "trunks", []))
                    print("\n--- Existing LiveKit Outbound SIP Trunks ---")
                    for t in trunks:
                        print(f"Trunk ID: {t.sip_trunk_id} | Name: {t.name} | Address: {t.address} | Numbers: {t.numbers}")
                else:
                    print("\n--- LiveKit SDK lacks list_sip_outbound_trunk capability ---")
                await lkapi.aclose()
            except Exception as e:
                print(f"\n--- Failed to list LiveKit trunks: {e} ---")
        else:
            print("\n--- LiveKit credentials missing or SDK not installed (skipped LiveKit trunk inspect) ---")

        print("=========================================================================\n")
        self.print_report("inspected")
        sys.exit(0)

    async def _run_apply(self):
        logger.info("Executing apply mode...")

        # 1. Enforce DANA_PROVISION_APPLY_CONFIRM=yes
        if not self.config.dana_provision_apply_confirm:
            print("\n=========================================================================")
            print("APPLY CONFIRMATION REQUIRED — Mutations Blocked")
            print("=========================================================================")
            print("To execute provisioning mutations, you must set:")
            print("DANA_PROVISION_APPLY_CONFIRM=yes")
            print("\nPlanned mutations if apply confirm was enabled:")
            
            # Print planned mutations clearly
            telnyx_mutations = []
            if not self.config.telnyx_outbound_voice_profile_id:
                telnyx_mutations.append("- Will search or create Outbound Voice Profile named 'dana-voice-profile'")
            if not self.config.telnyx_connection_id:
                telnyx_mutations.append("- Will search or create Credential Connection named 'dana-sip-connection'")
            if not self.config.telnyx_outbound_number:
                if self.config.dana_confirm_purchase_number:
                    telnyx_mutations.append(f"- Will search & purchase a phone number in country: '{self.config.telnyx_purchase_country or 'US'}'")
                else:
                    telnyx_mutations.append("- Will reuse an existing owned number assigned/unassigned")
            telnyx_mutations.append("- Will link outbound voice profile and connection, and assign the phone number")
            
            print(f"Telnyx Resource Mutations:\n" + "\n".join(telnyx_mutations))
            
            if self.config.dana_confirm_create_livekit_trunk:
                print("LiveKit Trunk Mutations:\n- Will create LiveKit outbound SIP trunk if not matching")
            else:
                print("LiveKit Trunk Mutations:\n- None (not confirmed)")
            
            print("=========================================================================\n")
            self.report["operator_action"] = "Set DANA_PROVISION_APPLY_CONFIRM=yes to confirm mutations."
            self.print_report("failed_requires_operator_action")
            sys.exit(1)

        # 2. Check read confirmation
        if not self.config.dana_confirm_telnyx_read:
            logger.error("DANA_CONFIRM_TELNYX_READ=yes is required to list existing resources.")
            self.report["operator_action"] = "Set DANA_CONFIRM_TELNYX_READ=yes in environment."
            self.print_report("failed_requires_operator_action")
            sys.exit(1)

        # 3. Validate credentials
        try:
            self.config.validate_for_telnyx(write_required=False)
        except ValueError as e:
            logger.error("Validation failed: %s", e)
            self.report["operator_action"] = str(e)
            self.print_report("failed_requires_operator_action")
            sys.exit(1)

        # Output resource dictionary mapping
        res_details = {
            "connection_id": self.config.telnyx_connection_id,
            "outbound_voice_profile_id": self.config.telnyx_outbound_voice_profile_id,
            "phone_number_id": self.config.telnyx_phone_number_id,
            "outbound_number": self.config.telnyx_outbound_number,
            "sip_address": "sip.telnyx.com",
            "sip_username": self.config.telnyx_sip_username,
            "sip_password": self.config.telnyx_sip_password,
            "livekit_sip_outbound_trunk_id": self.config.livekit_sip_outbound_trunk_id
        }

        # 4. Outbound Voice Profile logic
        if not res_details["outbound_voice_profile_id"]:
            logger.info("Listing existing Outbound Voice Profiles...")
            profiles = await self.client.list_outbound_voice_profiles()
            if profiles is None:
                logger.error("API Call to list voice profiles failed.")
                self.print_report("failed_api_error")
                sys.exit(1)
            
            self.report["inspected"] = "yes"
            match = next((p for p in profiles if p.get("name") == "dana-voice-profile"), None)
            if match:
                res_details["outbound_voice_profile_id"] = match.get("id")
                self.report["voice_profile"] = "reused"
                logger.info("Reusing existing Outbound Voice Profile ID: %s", res_details["outbound_voice_profile_id"])
            else:
                if self.config.dana_confirm_telnyx_mutation:
                    logger.info("Creating voice profile 'dana-voice-profile'...")
                    profile = await self.client.create_outbound_voice_profile("dana-voice-profile")
                    if profile and profile.get("id"):
                        res_details["outbound_voice_profile_id"] = profile.get("id")
                        self.report["voice_profile"] = "created"
                        logger.info("Created voice profile ID: %s", res_details["outbound_voice_profile_id"])
                    else:
                        logger.error("Failed to create Outbound Voice Profile.")
                        self.report["voice_profile"] = "failed"
                        self.print_report("failed_api_error")
                        sys.exit(1)
                else:
                    logger.error("Outbound Voice Profile 'dana-voice-profile' not found and DANA_CONFIRM_TELNYX_MUTATION is not yes.")
                    self.report["voice_profile"] = "failed"
                    self.report["operator_action"] = "Create outbound voice profile named 'dana-voice-profile' or enable DANA_CONFIRM_TELNYX_MUTATION=yes."
                    self.print_report("failed_requires_operator_action")
                    sys.exit(1)
        else:
            self.report["voice_profile"] = "reused"

        # 5. SIP Connection logic
        sip_password_generated = False
        if not res_details["connection_id"]:
            logger.info("Listing existing Credential Connections...")
            connections = await self.client.list_credential_connections()
            if connections is None:
                logger.error("API Call to list connections failed.")
                self.print_report("failed_api_error")
                sys.exit(1)

            self.report["inspected"] = "yes"
            match = next((c for c in connections if c.get("connection_name") == "dana-sip-connection"), None)
            if match:
                res_details["connection_id"] = match.get("id")
                res_details["sip_username"] = match.get("username") or match.get("sip_username") or match.get("user_name")
                self.report["connection"] = "reused"
                logger.info("Reusing existing SIP Connection ID: %s", res_details["connection_id"])
            else:
                if self.config.dana_confirm_telnyx_mutation:
                    logger.info("Creating credential connection 'dana-sip-connection'...")
                    conn = await self.client.create_credential_connection("dana-sip-connection")
                    if conn and conn.get("id"):
                        res_details["connection_id"] = conn.get("id")
                        res_details["sip_username"] = conn.get("username") or conn.get("sip_username") or conn.get("user_name")
                        res_details["sip_password"] = conn.get("password") or conn.get("sip_password")
                        sip_password_generated = True
                        self.report["connection"] = "created"
                        self.report["sip_credentials"] = "generated"
                        logger.info("Created SIP credential connection ID: %s", res_details["connection_id"])
                    else:
                        logger.error("Failed to create credential connection.")
                        self.report["connection"] = "failed"
                        self.print_report("failed_api_error")
                        sys.exit(1)
                else:
                    logger.error("Credential connection 'dana-sip-connection' not found and DANA_CONFIRM_TELNYX_MUTATION is not yes.")
                    self.report["connection"] = "failed"
                    self.report["operator_action"] = "Create SIP connection 'dana-sip-connection' or enable DANA_CONFIRM_TELNYX_MUTATION=yes."
                    self.print_report("failed_requires_operator_action")
                    sys.exit(1)
        else:
            self.report["connection"] = "reused"

        # 6. Retrieve/validate SIP username & password
        if not sip_password_generated:
            # Reusing existing connection. Check environment variables
            if (not res_details["sip_username"] or res_details["sip_username"] == "replace_me" or
                    not res_details["sip_password"] or res_details["sip_password"] == "replace_me"):
                logger.error("Telnyx SIP password could not be retrieved. Create/reset SIP credentials in Telnyx and provide TELNYX_SIP_USERNAME/TELNYX_SIP_PASSWORD.")
                self.report["sip_credentials"] = "missing"
                self.report["operator_action"] = "Telnyx SIP password could not be retrieved. Create/reset SIP credentials in Telnyx and provide TELNYX_SIP_USERNAME/TELNYX_SIP_PASSWORD."
                self.print_report("failed_requires_operator_action")
                sys.exit(1)
            else:
                self.report["sip_credentials"] = "env"

        # 7. Outbound voice profile linkage update
        if self.config.dana_confirm_telnyx_mutation:
            logger.info("Ensuring SIP connection is linked to Outbound Voice Profile...")
            update_res = await self.client.update_credential_connection(
                res_details["connection_id"],
                {"outbound_voice_profile_id": res_details["outbound_voice_profile_id"]}
            )
            if not update_res:
                logger.warning("Could not associate credential connection with Outbound Voice Profile ID via API.")

        # 8. Resolve Phone Number
        if not res_details["outbound_number"] or res_details["outbound_number"] == "replace_me":
            logger.info("Searching owned phone numbers...")
            numbers = await self.client.list_phone_numbers()
            if numbers is None:
                logger.error("API Call to list phone numbers failed.")
                self.print_report("failed_api_error")
                sys.exit(1)

            self.report["inspected"] = "yes"
            # Try to find a number assigned to this connection
            owned_assigned = next((n for n in numbers if n.get("connection_id") == res_details["connection_id"]), None)
            if owned_assigned:
                res_details["outbound_number"] = owned_assigned.get("phone_number")
                res_details["phone_number_id"] = owned_assigned.get("id")
                self.report["phone_number"] = "reused"
                logger.info("Found number assigned to connection: %s (ID: %s)", res_details["outbound_number"], res_details["phone_number_id"])
            else:
                # Find an unassigned owned number
                owned_unassigned = next((n for n in numbers if not n.get("connection_id")), None)
                if owned_unassigned:
                    res_details["outbound_number"] = owned_unassigned.get("phone_number")
                    res_details["phone_number_id"] = owned_unassigned.get("id")
                    
                    if self.config.dana_confirm_telnyx_mutation:
                        logger.info("Assigning unassigned owned number %s (ID: %s) to connection %s...", 
                                    res_details["outbound_number"], res_details["phone_number_id"], res_details["connection_id"])
                        assign_res = await self.client.assign_phone_number_connection(res_details["phone_number_id"], res_details["connection_id"])
                        if assign_res:
                            self.report["phone_number"] = "reused"
                        else:
                            logger.error("Failed to assign owned number to SIP connection.")
                            self.report["phone_number"] = "failed"
                            self.print_report("failed_api_error")
                            sys.exit(1)
                    else:
                        logger.error("Owned unassigned phone number exists but assignment not confirmed.")
                        self.report["phone_number"] = "failed"
                        self.report["operator_action"] = "Enable DANA_CONFIRM_TELNYX_MUTATION=yes to assign owned number."
                        self.print_report("failed_requires_operator_action")
                        sys.exit(1)
                else:
                    # No owned numbers. Attempt to purchase.
                    # Verify ALL required purchase confirmations are present
                    purchase_country = self.config.telnyx_purchase_country
                    if (not self.config.dana_confirm_purchase_number or 
                            not self.config.dana_confirm_telnyx_mutation or
                            not purchase_country):
                        logger.error("No phone number found and purchase confirmations are missing (DANA_CONFIRM_PURCHASE_NUMBER, DANA_CONFIRM_TELNYX_MUTATION, and TELNYX_PURCHASE_COUNTRY).")
                        self.report["phone_number"] = "missing"
                        self.report["operator_action"] = "Provide TELNYX_OUTBOUND_NUMBER, assign an owned number, or verify purchase configs (DANA_CONFIRM_PURCHASE_NUMBER=yes, TELNYX_PURCHASE_COUNTRY=US, DANA_CONFIRM_TELNYX_MUTATION=yes)."
                        self.print_report("failed_requires_operator_action")
                        sys.exit(1)

                    # Search available number
                    logger.info("Searching available numbers in country: %s...", purchase_country)
                    params = {"filter[country_code]": purchase_country, "filter[limit]": 1}
                    if self.config.telnyx_purchase_area_code:
                        params["filter[area_code]"] = self.config.telnyx_purchase_area_code
                    if self.config.telnyx_purchase_locality:
                        params["filter[locality]"] = self.config.telnyx_purchase_locality

                    available = await self.client.search_available_phone_numbers(params)
                    if not available:
                        logger.error("No available phone numbers found to purchase matching criteria.")
                        self.report["phone_number"] = "failed"
                        self.print_report("failed_api_error")
                        sys.exit(1)

                    target_num = available[0].get("phone_number")
                    logger.info("Ordering phone number: %s...", target_num)
                    order = await self.client.purchase_phone_number(target_num)
                    if order and order.get("id"):
                        # In the real Telnyx order api, we may need to pull order status or read owned numbers again.
                        # For provisioning purposes, let's list owned numbers to locate the new ID
                        logger.info("Successfully ordered number. Retrieving ID...")
                        await asyncio.sleep(2)  # brief wait for ordering propagation
                        updated_numbers = await self.client.list_phone_numbers()
                        match_new = next((n for n in updated_numbers if n.get("phone_number") == target_num), None) if updated_numbers else None
                        
                        if match_new:
                            res_details["outbound_number"] = match_new.get("phone_number")
                            res_details["phone_number_id"] = match_new.get("id")
                            
                            # Assign number to SIP connection
                            logger.info("Assigning purchased number %s to connection...", res_details["outbound_number"])
                            await self.client.assign_phone_number_connection(res_details["phone_number_id"], res_details["connection_id"])
                            self.report["phone_number"] = "purchased"
                        else:
                            res_details["outbound_number"] = target_num
                            res_details["phone_number_id"] = "unknown_order_pending"
                            self.report["phone_number"] = "purchased"
                            logger.warning("Number ordered successfully, but ID lookup pending. Re-run after order provisioning completes.")
                    else:
                        logger.error("Failed to purchase phone number.")
                        self.report["phone_number"] = "failed"
                        self.print_report("failed_api_error")
                        sys.exit(1)
        else:
            self.report["phone_number"] = "reused"

        # 9. Create or Reuse LiveKit Outbound Trunk
        if not res_details["livekit_sip_outbound_trunk_id"]:
            # Need to connect to LiveKit
            try:
                self.config.validate_for_livekit()
            except ValueError as e:
                logger.error("LiveKit credentials validation failed: %s", e)
                self.report["livekit_trunk"] = "failed"
                self.report["operator_action"] = f"Provide LiveKit credentials to register trunk: {e}"
                self.print_report("failed_requires_operator_action")
                sys.exit(1)

            # Attempt import and client connection
            try:
                from livekit import api as lk_api
            except ImportError:
                logger.error("Failed to import LiveKit SDK. livekit-api is required to register outbound trunk.")
                self.report["livekit_trunk"] = "failed"
                self.report["operator_action"] = "Install livekit-api dependency on Hyperstack server."
                self.print_report("failed_requires_operator_action")
                sys.exit(1)

            logger.info("Connecting to LiveKit API...")
            try:
                lkapi = lk_api.LiveKitAPI(
                    url=self.config.livekit_url,
                    api_key=self.config.livekit_api_key,
                    api_secret=self.config.livekit_api_secret
                )
            except Exception as e:
                logger.error("Failed to instantiate LiveKit API client: %s", e)
                self.report["livekit_trunk"] = "failed"
                self.print_report("failed_api_error")
                sys.exit(1)

            # Prevent duplicate trunk by listing existing
            matching_trunk_id = None
            if hasattr(lkapi, "sip") and hasattr(lkapi.sip, "list_sip_outbound_trunk"):
                try:
                    logger.info("Checking for duplicate LiveKit Outbound SIP Trunks...")
                    req = lk_api.ListSIPOutboundTrunkRequest()
                    res = await lkapi.sip.list_sip_outbound_trunk(req)
                    trunks = getattr(res, "results", getattr(res, "trunks", []))
                    for t in trunks:
                        if (t.name == "Dana Telnyx Outbound Trunk" and 
                                t.address == "sip.telnyx.com" and 
                                res_details["outbound_number"] in t.numbers):
                            matching_trunk_id = t.sip_trunk_id
                            logger.info("Found matching existing LiveKit Outbound SIP Trunk. Reusing Trunk ID: %s", matching_trunk_id)
                            break
                except Exception as le:
                    logger.warning("Could not list existing LiveKit trunks to check duplicates (will attempt creation if confirmed): %s", le)
            else:
                logger.warning("LiveKit SDK doesn't support listing SIP trunks. Duplicate prevention skipped.")

            if matching_trunk_id:
                res_details["livekit_sip_outbound_trunk_id"] = matching_trunk_id
                self.report["livekit_trunk"] = "reused"
                await lkapi.aclose()
            else:
                # Create a new outbound trunk
                if not self.config.dana_confirm_create_livekit_trunk:
                    logger.error("LiveKit outbound trunk is missing, and DANA_CONFIRM_CREATE_LIVEKIT_TRUNK=yes is not set.")
                    self.report["livekit_trunk"] = "failed"
                    self.report["operator_action"] = "Set DANA_CONFIRM_CREATE_LIVEKIT_TRUNK=yes to register trunk."
                    await lkapi.aclose()
                    self.print_report("failed_requires_operator_action")
                    sys.exit(1)

                logger.info("Creating LiveKit Outbound SIP Trunk...")
                try:
                    trunk_info = lk_api.SIPOutboundTrunkInfo(
                        name="Dana Telnyx Outbound Trunk",
                        address="sip.telnyx.com",
                        auth_username=res_details["sip_username"],
                        auth_password=res_details["sip_password"],
                        numbers=[res_details["outbound_number"]]
                    )
                    request = lk_api.CreateSIPOutboundTrunkRequest(trunk=trunk_info)
                    trunk = await lkapi.sip.create_sip_outbound_trunk(request)
                    res_details["livekit_sip_outbound_trunk_id"] = trunk.sip_trunk_id
                    self.report["livekit_trunk"] = "created"
                    logger.info("Registered LiveKit outbound trunk ID: %s", res_details["livekit_sip_outbound_trunk_id"])
                except Exception as e:
                    logger.error("LiveKit API CreateSIPOutboundTrunk call failed: %s", e)
                    self.report["livekit_trunk"] = "failed"
                    await lkapi.aclose()
                    self.print_report("failed_api_error")
                    sys.exit(1)
                finally:
                    await lkapi.aclose()
        else:
            self.report["livekit_trunk"] = "reused"

        # 10. Write outputs and report success
        self._write_outputs(res_details, "provisioned_successfully")
        self.print_report("provisioned_successfully")
        sys.exit(0)


if __name__ == "__main__":
    orchestrator = ProvisioningOrchestrator()
    asyncio.run(orchestrator.run())
