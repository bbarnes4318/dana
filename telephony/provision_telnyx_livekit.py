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
        if status == "provisioned_successfully":
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
        else:
            logger.info("Skipping writing secret env output file because status is '%s'. Only 'provisioned_successfully' allowed.", status)

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

    def assert_real_success(self, details: dict):
        """Validates that all res_details contain genuine provisioned values."""
        errors = []
        
        # Check required fields are non-empty and not replace_me
        checks = {
            "connection_id": details.get("connection_id"),
            "outbound_voice_profile_id": details.get("outbound_voice_profile_id"),
            "phone_number_id": details.get("phone_number_id"),
            "outbound_number": details.get("outbound_number"),
            "sip_username": details.get("sip_username"),
            "sip_password": details.get("sip_password"),
            "livekit_sip_outbound_trunk_id": details.get("livekit_sip_outbound_trunk_id"),
        }
        
        for k, v in checks.items():
            if not v or v == "replace_me" or v == "unknown_order_pending":
                errors.append(f"Missing or invalid final detail: {k} (val: {v})")

        # Check report statuses
        invalid_statuses = ("failed", "missing", "unverified", "planned", "dry_run", "skipped")
        for k, status in self.report.items():
            if k in ("voice_profile", "connection", "phone_number", "livekit_trunk"):
                if status in invalid_statuses:
                    errors.append(f"Report status for '{k}' is invalid: {status}")

        if errors:
            raise ValueError(f"Success validation failed: {'; '.join(errors)}")

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
            print(f"- Will verify and reuse Telnyx credential connection ID: {self.config.telnyx_connection_id}")
        else:
            print("- Will search for existing credential connection named 'dana-sip-connection'")
            if self.config.dana_confirm_telnyx_mutation:
                print("  -> If not found, WILL create new credential connection 'dana-sip-connection'")
            else:
                print("  -> If not found, WILL fail (requires DANA_CONFIRM_TELNYX_MUTATION=yes)")

        if self.config.telnyx_outbound_voice_profile_id:
            print(f"- Will verify and reuse Outbound Voice Profile ID: {self.config.telnyx_outbound_voice_profile_id}")
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
            print(f"- Will verify and reuse LiveKit Outbound Trunk ID: {self.config.livekit_sip_outbound_trunk_id}")
        else:
            print("- Will check if matching trunk exists in LiveKit (prevents duplicates)")
            if self.config.dana_confirm_create_livekit_trunk:
                print("  -> If matching trunk not found, WILL register new LiveKit Outbound SIP Trunk")
            else:
                print("  -> If matching trunk not found, WILL fail (requires DANA_CONFIRM_CREATE_LIVEKIT_TRUNK=yes)")
                
        print("=========================================================================\n")
        self.print_report("planned")
        sys.exit(0)
        return

    async def _run_inspect(self):
        logger.info("Executing inspect mode...")
        # Inspect mode requires read confirmation
        if not self.config.dana_confirm_telnyx_read:
            logger.error("DANA_CONFIRM_TELNYX_READ=yes is required for inspect mode.")
            self.report["operator_action"] = "Set DANA_CONFIRM_TELNYX_READ=yes in environment."
            self.print_report("failed_requires_operator_action")
            sys.exit(1)
            return

        try:
            self.config.validate_for_telnyx(write_required=False)
        except ValueError as e:
            logger.error("Validation failed: %s", e)
            self.report["operator_action"] = str(e)
            self.print_report("failed_requires_operator_action")
            sys.exit(1)
            return

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
        return

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
            else:
                telnyx_mutations.append(f"- Will verify and reuse Outbound Voice Profile: {self.config.telnyx_outbound_voice_profile_id}")
            
            if not self.config.telnyx_connection_id:
                telnyx_mutations.append("- Will search or create Credential Connection named 'dana-sip-connection'")
            else:
                telnyx_mutations.append(f"- Will verify and reuse Credential Connection: {self.config.telnyx_connection_id}")
                
            if not self.config.telnyx_outbound_number:
                if self.config.dana_confirm_purchase_number:
                    telnyx_mutations.append(f"- Will search & purchase a phone number in country: '{self.config.telnyx_purchase_country or 'US'}'")
                else:
                    telnyx_mutations.append("- Will reuse an existing owned number assigned/unassigned")
            else:
                telnyx_mutations.append(f"- Will verify ownership of number: {self.config.telnyx_outbound_number}")

            telnyx_mutations.append("- Will link outbound voice profile and connection, and assign the phone number")
            
            print(f"Telnyx Resource Mutations:\n" + "\n".join(telnyx_mutations))
            
            if self.config.dana_confirm_create_livekit_trunk:
                print("LiveKit Trunk Mutations:\n- Will create/reuse LiveKit outbound SIP trunk")
            else:
                print("LiveKit Trunk Mutations:\n- None (not confirmed)")
            
            print("=========================================================================\n")
            self.report["operator_action"] = "Set DANA_PROVISION_APPLY_CONFIRM=yes to confirm mutations."
            self.print_report("failed_requires_operator_action")
            sys.exit(1)
            return

        # 2. Check read confirmation
        if not self.config.dana_confirm_telnyx_read:
            logger.error("DANA_CONFIRM_TELNYX_READ=yes is required to list/inspect existing resources.")
            self.report["operator_action"] = "Set DANA_CONFIRM_TELNYX_READ=yes in environment."
            self.print_report("failed_requires_operator_action")
            sys.exit(1)
            return

        # 3. Validate credentials
        try:
            self.config.validate_for_telnyx(write_required=False)
        except ValueError as e:
            logger.error("Validation failed: %s", e)
            self.report["operator_action"] = str(e)
            self.print_report("failed_requires_operator_action")
            sys.exit(1)
            return

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
        if res_details["outbound_voice_profile_id"]:
            # Verify existing profile ID
            logger.info("Verifying provided Outbound Voice Profile ID: %s...", res_details["outbound_voice_profile_id"])
            profiles = await self.client.list_outbound_voice_profiles()
            if profiles is None:
                logger.error("API Call to list voice profiles failed.")
                self.print_report("failed_api_error")
                sys.exit(1)
                return
            
            self.report["inspected"] = "yes"
            match = next((p for p in profiles if p.get("id") == res_details["outbound_voice_profile_id"]), None)
            if match:
                self.report["voice_profile"] = "reused"
                logger.info("Successfully verified provided Voice Profile: %s (ID: %s)", match.get("name"), match.get("id"))
            else:
                logger.error("Provided TELNYX_OUTBOUND_VOICE_PROFILE_ID '%s' was not found on the account.", res_details["outbound_voice_profile_id"])
                self.report["voice_profile"] = "failed"
                self.report["operator_action"] = f"Verify TELNYX_OUTBOUND_VOICE_PROFILE_ID value '{res_details['outbound_voice_profile_id']}' exists in Telnyx dashboard."
                self.print_report("failed_requires_operator_action")
                sys.exit(1)
                return
        else:
            logger.info("Listing existing Outbound Voice Profiles...")
            profiles = await self.client.list_outbound_voice_profiles()
            if profiles is None:
                logger.error("API Call to list voice profiles failed.")
                self.print_report("failed_api_error")
                sys.exit(1)
                return
            
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
                        return
                else:
                    logger.error("Outbound Voice Profile 'dana-voice-profile' not found and DANA_CONFIRM_TELNYX_MUTATION is not yes.")
                    self.report["voice_profile"] = "failed"
                    self.report["operator_action"] = "Create outbound voice profile named 'dana-voice-profile' or enable DANA_CONFIRM_TELNYX_MUTATION=yes."
                    self.print_report("failed_requires_operator_action")
                    sys.exit(1)
                    return

        # 5. SIP Connection logic
        sip_password_generated = False
        if res_details["connection_id"]:
            # Verify existing connection ID
            logger.info("Verifying provided SIP Connection ID: %s...", res_details["connection_id"])
            connections = await self.client.list_credential_connections()
            if connections is None:
                logger.error("API Call to list connections failed.")
                self.print_report("failed_api_error")
                sys.exit(1)
                return
            
            self.report["inspected"] = "yes"
            match = next((c for c in connections if c.get("id") == res_details["connection_id"]), None)
            if match:
                self.report["connection"] = "reused"
                res_details["sip_username"] = match.get("username") or match.get("sip_username") or match.get("user_name")
                logger.info("Successfully verified provided SIP Connection name: %s (ID: %s)", match.get("connection_name"), match.get("id"))
            else:
                logger.error("Provided TELNYX_CONNECTION_ID '%s' was not found on the account.", res_details["connection_id"])
                self.report["connection"] = "failed"
                self.report["operator_action"] = f"Verify TELNYX_CONNECTION_ID value '{res_details['connection_id']}' exists in Telnyx dashboard."
                self.print_report("failed_requires_operator_action")
                sys.exit(1)
                return
        else:
            logger.info("Listing existing Credential Connections...")
            connections = await self.client.list_credential_connections()
            if connections is None:
                logger.error("API Call to list connections failed.")
                self.print_report("failed_api_error")
                sys.exit(1)
                return

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
                        return
                else:
                    logger.error("Credential connection 'dana-sip-connection' not found and DANA_CONFIRM_TELNYX_MUTATION is not yes.")
                    self.report["connection"] = "failed"
                    self.report["operator_action"] = "Create SIP connection 'dana-sip-connection' or enable DANA_CONFIRM_TELNYX_MUTATION=yes."
                    self.print_report("failed_requires_operator_action")
                    sys.exit(1)
                    return

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
                return
            else:
                self.report["sip_credentials"] = "env"

        # 7. SIP Connection <-> Outbound Voice Profile Linkage Verification and Enforcement
        # Retrieve the current connection object details from list
        connections = await self.client.list_credential_connections()
        current_conn = next((c for c in connections if c.get("id") == res_details["connection_id"]), None) if connections else None
        
        if current_conn:
            linked_profile_id = current_conn.get("outbound_voice_profile_id")
            if linked_profile_id != res_details["outbound_voice_profile_id"]:
                logger.info("Outbound voice profile linkage mismatch. Connection has '%s', expected '%s'.", linked_profile_id, res_details["outbound_voice_profile_id"])
                
                if self.config.dana_confirm_telnyx_mutation:
                    logger.info("Updating SIP connection outbound voice profile linkage...")
                    update_res = await self.client.update_credential_connection(
                        res_details["connection_id"],
                        {"outbound_voice_profile_id": res_details["outbound_voice_profile_id"]}
                    )
                    if not update_res:
                        logger.error("Failed to update credential connection voice profile linkage via API.")
                        self.print_report("failed_api_error")
                        sys.exit(1)
                        return
                else:
                    logger.error("SIP Connection is not linked to the selected Outbound Voice Profile, and DANA_CONFIRM_TELNYX_MUTATION=yes is not set.")
                    self.report["operator_action"] = f"SIP Connection is not linked to Outbound Voice Profile ID {res_details['outbound_voice_profile_id']}. Link connection ID {res_details['connection_id']} to profile ID {res_details['outbound_voice_profile_id']} in Telnyx dashboard, or enable DANA_CONFIRM_TELNYX_MUTATION=yes."
                    self.print_report("failed_requires_operator_action")
                    sys.exit(1)
                    return

        # 8. Phone Number Logic
        # A: Verify TELNYX_PHONE_NUMBER_ID if provided
        if res_details["phone_number_id"] and res_details["phone_number_id"] != "replace_me":
            logger.info("Verifying provided Phone Number ID: %s...", res_details["phone_number_id"])
            details = await self.client.get_phone_number_details(res_details["phone_number_id"])
            if not details:
                logger.error("Provided TELNYX_PHONE_NUMBER_ID '%s' was not found on the account.", res_details["phone_number_id"])
                self.report["phone_number"] = "failed"
                self.report["operator_action"] = f"Verify TELNYX_PHONE_NUMBER_ID value '{res_details['phone_number_id']}' exists in Telnyx dashboard."
                self.print_report("failed_requires_operator_action")
                sys.exit(1)
                return
            
            num_val = details.get("phone_number")
            if res_details["outbound_number"] and res_details["outbound_number"] != "replace_me":
                if num_val != res_details["outbound_number"]:
                    logger.error("Phone number ID '%s' maps to number '%s', but TELNYX_OUTBOUND_NUMBER is set to '%s'. They must match.", 
                                 res_details["phone_number_id"], num_val, res_details["outbound_number"])
                    self.report["phone_number"] = "failed"
                    self.report["operator_action"] = "Ensure TELNYX_PHONE_NUMBER_ID and TELNYX_OUTBOUND_NUMBER are consistent. They must match."
                    self.print_report("failed_requires_operator_action")
                    sys.exit(1)
                    return

            res_details["outbound_number"] = num_val

            # Check connection assignment
            current_conn_id = details.get("connection_id")
            if current_conn_id != res_details["connection_id"]:
                if self.config.dana_confirm_telnyx_mutation:
                    logger.info("Assigning phone number ID %s to connection ID %s...", res_details["phone_number_id"], res_details["connection_id"])
                    assign_res = await self.client.assign_phone_number_connection(res_details["phone_number_id"], res_details["connection_id"])
                    if not assign_res:
                        logger.error("Failed to assign phone number to SIP connection.")
                        self.report["phone_number"] = "failed"
                        self.print_report("failed_api_error")
                        sys.exit(1)
                        return
                else:
                    logger.error("Phone number ID %s is not assigned to connection ID %s, and mutation is not confirmed.", res_details["phone_number_id"], res_details["connection_id"])
                    self.report["phone_number"] = "failed"
                    self.report["operator_action"] = f"Phone number ID {res_details['phone_number_id']} is not assigned to connection ID {res_details['connection_id']}. Assign it in Telnyx dashboard, or enable DANA_CONFIRM_TELNYX_MUTATION=yes."
                    self.print_report("failed_requires_operator_action")
                    sys.exit(1)
                    return

            logger.info("Successfully verified Phone Number ID. Number is: %s", num_val)
            self.report["phone_number"] = "reused"

        # B: Verify TELNYX_OUTBOUND_NUMBER if provided but ID is not
        elif res_details["outbound_number"] and res_details["outbound_number"] != "replace_me":
            logger.info("Verifying ownership of outbound number: %s...", res_details["outbound_number"])
            numbers = await self.client.list_phone_numbers()
            if numbers is None:
                logger.error("API Call to list phone numbers failed.")
                self.print_report("failed_api_error")
                sys.exit(1)
                return
            
            match_num = next((n for n in numbers if n.get("phone_number") == res_details["outbound_number"]), None)
            if match_num:
                res_details["phone_number_id"] = match_num.get("id")
                logger.info("Found owned number ID: %s", res_details["phone_number_id"])
                
                # Check connection assignment
                if match_num.get("connection_id") != res_details["connection_id"]:
                    if self.config.dana_confirm_telnyx_mutation:
                        logger.info("Assigning phone number to connection ID %s...", res_details["connection_id"])
                        assign_res = await self.client.assign_phone_number_connection(res_details["phone_number_id"], res_details["connection_id"])
                        if not assign_res:
                            logger.error("Failed to assign phone number to SIP connection.")
                            self.report["phone_number"] = "failed"
                            self.print_report("failed_api_error")
                            sys.exit(1)
                            return
                    else:
                        logger.error("Phone number %s is not assigned to connection ID %s, and mutation is not confirmed.", res_details["outbound_number"], res_details["connection_id"])
                        self.report["phone_number"] = "failed"
                        self.report["operator_action"] = f"Assign number {res_details['outbound_number']} to connection ID {res_details['connection_id']} in Telnyx dashboard, or enable DANA_CONFIRM_TELNYX_MUTATION=yes."
                        self.print_report("failed_requires_operator_action")
                        sys.exit(1)
                        return
                
                self.report["phone_number"] = "reused"
            else:
                logger.error("The configured TELNYX_OUTBOUND_NUMBER '%s' is not owned by this Telnyx account.", res_details["outbound_number"])
                self.report["phone_number"] = "failed"
                self.report["operator_action"] = f"Ensure the number '{res_details['outbound_number']}' is ordered/active in your Telnyx portal. Number is not owned by this Telnyx account."
                self.print_report("failed_requires_operator_action")
                sys.exit(1)
                return

        # C: Auto-Resolve Phone Number
        else:
            logger.info("Searching owned phone numbers...")
            numbers = await self.client.list_phone_numbers()
            if numbers is None:
                logger.error("API Call to list phone numbers failed.")
                self.print_report("failed_api_error")
                sys.exit(1)
                return

            self.report["inspected"] = "yes"
            owned_assigned = next((n for n in numbers if n.get("connection_id") == res_details["connection_id"]), None)
            if owned_assigned:
                res_details["outbound_number"] = owned_assigned.get("phone_number")
                res_details["phone_number_id"] = owned_assigned.get("id")
                self.report["phone_number"] = "reused"
                logger.info("Found number assigned to connection: %s (ID: %s)", res_details["outbound_number"], res_details["phone_number_id"])
            else:
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
                            return
                    else:
                        logger.error("Owned unassigned phone number exists but assignment not confirmed.")
                        self.report["phone_number"] = "failed"
                        self.report["operator_action"] = "Enable DANA_CONFIRM_TELNYX_MUTATION=yes to assign owned number."
                        self.print_report("failed_requires_operator_action")
                        sys.exit(1)
                        return
                else:
                    # Purchase Logic
                    purchase_country = self.config.telnyx_purchase_country
                    if (not self.config.dana_confirm_purchase_number or 
                            not self.config.dana_confirm_telnyx_mutation or
                            not purchase_country):
                        logger.error("No phone number found and purchase confirmations are missing (DANA_CONFIRM_PURCHASE_NUMBER, DANA_CONFIRM_TELNYX_MUTATION, and TELNYX_PURCHASE_COUNTRY).")
                        self.report["phone_number"] = "missing"
                        self.report["operator_action"] = "Provide TELNYX_OUTBOUND_NUMBER, assign an owned number, or verify purchase configs (DANA_CONFIRM_PURCHASE_NUMBER=yes, TELNYX_PURCHASE_COUNTRY=US, DANA_CONFIRM_TELNYX_MUTATION=yes)."
                        self.print_report("failed_requires_operator_action")
                        sys.exit(1)
                        return

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
                        return

                    target_num = available[0].get("phone_number")
                    logger.info("Ordering phone number: %s...", target_num)
                    order = await self.client.purchase_phone_number(target_num)
                    if order and order.get("id"):
                        logger.info("Successfully ordered number. Checking if visible in owned numbers list...")
                        await asyncio.sleep(2)  # brief wait for ordering propagation
                        updated_numbers = await self.client.list_phone_numbers()
                        match_new = next((n for n in updated_numbers if n.get("phone_number") == target_num), None) if updated_numbers else None
                        
                        if match_new:
                            res_details["outbound_number"] = match_new.get("phone_number")
                            res_details["phone_number_id"] = match_new.get("id")
                            
                            # Assign number to SIP connection
                            logger.info("Assigning purchased number %s to connection...", res_details["outbound_number"])
                            assign_res = await self.client.assign_phone_number_connection(res_details["phone_number_id"], res_details["connection_id"])
                            if not assign_res:
                                logger.error("Failed to assign purchased phone number to SIP connection.")
                                self.report["phone_number"] = "failed"
                                self.print_report("failed_api_error")
                                sys.exit(1)
                                return
                            self.report["phone_number"] = "purchased"
                        else:
                            # Strict requirement: Fail if purchased number is not yet visible/provisioned.
                            # Never write success with pending order.
                            logger.error("Number order was created but the phone number is not yet provisioned/visible in the owned numbers list.")
                            self.report["phone_number"] = "failed"
                            self.report["operator_action"] = "Number order was created but the phone number is not yet provisioned/visible. Re-run apply after Telnyx finishes provisioning. phone_number_order_pending"
                            self.report["phone_number_status"] = "phone_number_order_pending"
                            
                            # Output resources metadata as pending/failed before exiting
                            self._write_outputs(res_details, "phone_number_order_pending")
                            self.print_report("failed_requires_operator_action")
                            sys.exit(1)
                            return
                    else:
                        logger.error("Failed to purchase phone number.")
                        self.report["phone_number"] = "failed"
                        self.print_report("failed_api_error")
                        sys.exit(1)
                        return

        # 9. Create or Reuse LiveKit Outbound Trunk
        # Validate LiveKit credentials
        try:
            self.config.validate_for_livekit()
        except ValueError as e:
            logger.error("LiveKit credentials validation failed: %s", e)
            self.report["livekit_trunk"] = "failed"
            self.report["operator_action"] = f"Provide LiveKit credentials to register trunk: {e}"
            self.print_report("failed_requires_operator_action")
            sys.exit(1)
            return

        # Attempt SDK import
        try:
            from livekit import api as lk_api
            has_sdk = True
        except ImportError:
            has_sdk = False

        if not has_sdk:
            logger.error("Failed to import LiveKit SDK. livekit-api is required to register/verify outbound trunk.")
            self.report["livekit_trunk"] = "failed"
            self.report["operator_action"] = "Install livekit-api dependency on Hyperstack server."
            self.print_report("failed_requires_operator_action")
            sys.exit(1)
            return

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
            return

        listing_supported = hasattr(lkapi, "sip") and hasattr(lkapi.sip, "list_sip_outbound_trunk")
        
        if res_details["livekit_sip_outbound_trunk_id"] and res_details["livekit_sip_outbound_trunk_id"] != "replace_me":
            # Override provided. Verify it.
            if listing_supported:
                try:
                    logger.info("Verifying provided LiveKit Outbound SIP Trunk ID: %s...", res_details["livekit_sip_outbound_trunk_id"])
                    req = lk_api.ListSIPOutboundTrunkRequest()
                    res = await lkapi.sip.list_sip_outbound_trunk(req)
                    trunks = getattr(res, "results", getattr(res, "trunks", []))
                    match_trunk = next((t for t in trunks if t.sip_trunk_id == res_details["livekit_sip_outbound_trunk_id"]), None)
                    
                    if match_trunk:
                        # Confirm details match
                        if (match_trunk.name != "Dana Telnyx Outbound Trunk" or 
                                match_trunk.address != "sip.telnyx.com" or 
                                res_details["outbound_number"] not in match_trunk.numbers):
                            logger.error("Provided LiveKit trunk ID details mismatch. Expected Name: 'Dana Telnyx Outbound Trunk', Address: 'sip.telnyx.com', Numbers: [%s]. Got: Name: '%s', Address: '%s', Numbers: %s",
                                         res_details["outbound_number"], match_trunk.name, match_trunk.address, match_trunk.numbers)
                            self.report["livekit_trunk"] = "failed"
                            self.report["operator_action"] = "LiveKit Outbound Trunk details do not match expected Telnyx profile settings."
                            await lkapi.aclose()
                            self.print_report("failed_requires_operator_action")
                            sys.exit(1)
                            return
                        else:
                            self.report["livekit_trunk"] = "reused"
                            logger.info("Successfully verified provided LiveKit Trunk ID.")
                    else:
                        logger.error("Provided LIVEKIT_SIP_OUTBOUND_TRUNK_ID '%s' was not found.", res_details["livekit_sip_outbound_trunk_id"])
                        self.report["livekit_trunk"] = "failed"
                        self.report["operator_action"] = f"Verify trunk ID '{res_details['livekit_sip_outbound_trunk_id']}' exists in LiveKit Cloud."
                        await lkapi.aclose()
                        self.print_report("failed_requires_operator_action")
                        sys.exit(1)
                        return
                except Exception as le:
                    logger.error("Failed to verify LiveKit Trunk ID: %s", le)
                    self.report["livekit_trunk"] = "failed"
                    await lkapi.aclose()
                    self.print_report("failed_api_error")
                    sys.exit(1)
                    return
            else:
                # Listing unsupported. Require override bypass gate
                if self.config.dana_confirm_accept_unverified_livekit_trunk:
                    self.report["livekit_trunk"] = "unverified_existing"
                    logger.warning("LiveKit SDK cannot verify existing trunk ID. Bypassing check due to DANA_CONFIRM_ACCEPT_UNVERIFIED_LIVEKIT_TRUNK=yes.")
                else:
                    logger.error("Provided LIVEKIT_SIP_OUTBOUND_TRUNK_ID cannot be verified because the installed SDK does not support listing. Verification bypass DANA_CONFIRM_ACCEPT_UNVERIFIED_LIVEKIT_TRUNK=yes is required.")
                    self.report["livekit_trunk"] = "unverified"
                    self.report["operator_action"] = "Confirm you accept the unverified trunk by setting DANA_CONFIRM_ACCEPT_UNVERIFIED_LIVEKIT_TRUNK=yes."
                    await lkapi.aclose()
                    self.print_report("failed_requires_operator_action")
                    sys.exit(1)
                    return
            await lkapi.aclose()
        else:
            # Check for existing trunk to prevent duplicates
            matching_trunk_id = None
            if listing_supported:
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
                    return

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
                    return
                finally:
                    await lkapi.aclose()

        # 10. SUCCESS GUARD ASSERTS BEFORE OUTPUT WRITING
        try:
            self.assert_real_success(res_details)
        except ValueError as ve:
            logger.error("Final success assertion failed: %s", ve)
            self.report["operator_action"] = f"Final validation failed: {ve}"
            self.print_report("failed_api_error")
            sys.exit(1)
            return

        # Write outputs and report success
        self._write_outputs(res_details, "provisioned_successfully")
        self.print_report("provisioned_successfully")
        sys.exit(0)
        return


if __name__ == "__main__":
    orchestrator = ProvisioningOrchestrator()
    asyncio.run(orchestrator.run())
