"""Controls Telnyx outbound calling and LiveKit connections."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from telephony.telnyx_config import TelephonyConfig

logger = logging.getLogger(__name__)

CALL_RESULT_FILE = "telephony/last_outbound_call.json"


class CallService:
    """Manages initiating outbound calls via LiveKit SIP participant API."""

    def __init__(self, config: Optional[TelephonyConfig] = None) -> None:
        self.config = config or TelephonyConfig()

    def _mask_number(self, num: Optional[str]) -> str:
        if not num:
            return "unset"
        if len(num) <= 4:
            return "****"
        return f"******{num[-4:]}"

    async def place_call(
        self,
        lead: dict[str, Any],
        call_id: str,
        caller_id: str,
        room_name: Optional[str] = None
    ) -> dict[str, Any]:
        """Place an outbound call.

        Respects the DANA_CONFIRM_PLACE_CALL env check. If not set to 'yes',
        it will perform a dry-run, logging the details and saving to
        telephony/last_outbound_call.json.
        """
        confirm_place_call = os.environ.get("DANA_CONFIRM_PLACE_CALL", "").lower() == "yes"
        
        phone_e164 = lead.get("phone_e164") or lead.get("lead_phone_e164") or lead.get("phone_number")
        if not phone_e164:
            raise ValueError("Lead is missing a phone number")

        actual_room = room_name or f"{self.config.dana_room_prefix or 'dana-call'}-{uuid.uuid4().hex[:8]}"
        participant_identity = f"prospect-{uuid.uuid4().hex[:8]}"

        planned_call = {
            "call_id": call_id,
            "lead_id": lead.get("id"),
            "to_masked": self._mask_number(phone_e164),
            "from_masked": self._mask_number(caller_id),
            "room_name": actual_room,
            "participant_identity": participant_identity,
            "trunk_id_masked": self._mask_number(self.config.livekit_sip_outbound_trunk_id or "mock_trunk_id"),
        }

        if not confirm_place_call:
            logger.info("=========================================================================")
            logger.info("DRY-RUN MODE — No real call will be placed in LiveKit.")
            logger.info("To place a real call, set: DANA_CONFIRM_PLACE_CALL=yes")
            logger.info("=========================================================================")
            logger.info("Planned Call Details (Dry Run):")
            logger.info("  To: %s", planned_call["to_masked"])
            logger.info("  From: %s", planned_call["from_masked"])
            logger.info("  Room: %s", planned_call["room_name"])
            logger.info("  Identity: %s", planned_call["participant_identity"])

            dry_run_data = {
                "id": call_id,
                "real_resource_created": False,
                "status": "dry_run",
                "would_create": True,
                "to": phone_e164,
                "from": caller_id,
                "room_name": actual_room,
                "participant_identity": participant_identity,
                "trunk_id": self.config.livekit_sip_outbound_trunk_id or "mock_trunk_id",
                "placed_at": datetime.now(timezone.utc).isoformat()
            }
            
            # Create parent dirs if not existing
            os.makedirs(os.path.dirname(CALL_RESULT_FILE), exist_ok=True)
            with open(CALL_RESULT_FILE, "w", encoding="utf-8") as f:
                json.dump(dry_run_data, f, indent=2)
            
            logger.info("Dry-run call log saved to %s", CALL_RESULT_FILE)
            return dry_run_data

        # Place a real call
        self.config.validate_for_livekit()
        if not self.config.livekit_sip_outbound_trunk_id:
            raise ValueError("LIVEKIT_SIP_OUTBOUND_TRUNK_ID is not configured.")

        logger.info("Connecting to LiveKit API to place SIP outbound call...")
        from livekit import api
        
        lkapi = api.LiveKitAPI(
            url=self.config.livekit_url,
            api_key=self.config.livekit_api_key,
            api_secret=self.config.livekit_api_secret
        )

        try:
            fields = api.CreateSIPParticipantRequest.DESCRIPTOR.fields_by_name
            kwargs: dict[str, Any] = {
                "sip_trunk_id": self.config.livekit_sip_outbound_trunk_id,
                "sip_call_to": phone_e164,
                "room_name": actual_room,
                "participant_identity": participant_identity,
            }
            if "participant_metadata" in fields:
                metadata_dict = {
                    "campaign_id": lead.get("campaign_id"),
                    "lead_id": lead.get("id") or lead.get("lead_id"),
                    "call_id": call_id
                }
                kwargs["participant_metadata"] = json.dumps(metadata_dict)
            if "wait_until_answered" in fields:
                kwargs["wait_until_answered"] = True
            if "display_name" in fields:
                kwargs["display_name"] = "Dana Voice Agent"
            if "participant_name" in fields:
                kwargs["participant_name"] = "Dana Voice Agent"
            if "sip_number" in fields:
                kwargs["sip_number"] = caller_id

            request = api.CreateSIPParticipantRequest(**kwargs)
            participant = await lkapi.sip.create_sip_participant(request)
            
            logger.info("Successfully initiated SIP call. Participant: %s", participant)

            result_data = {
                "id": call_id,
                "status": "placed",
                "sip_participant_id": getattr(participant, "participant_id", "unknown"),
                "to": phone_e164,
                "from": caller_id,
                "room_name": actual_room,
                "participant_identity": participant_identity,
                "trunk_id": self.config.livekit_sip_outbound_trunk_id,
                "placed_at": datetime.now(timezone.utc).isoformat()
            }

            with open(CALL_RESULT_FILE, "w", encoding="utf-8") as f:
                json.dump(result_data, f, indent=2)

            return result_data

        except Exception as e:
            logger.error("LiveKit API outbound call failed: %s", e)
            raise e
        finally:
            await lkapi.aclose()
