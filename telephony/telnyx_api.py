"""
Telnyx REST API Client (v2)
Strict carrier compliance and safety gates for Telnyx Mission Control API requests.
"""

import logging
import httpx
from typing import Any, Dict, List, Optional
from telephony.telnyx_config import TelephonyConfig

logger = logging.getLogger(__name__)

BASE_URL = "https://api.telnyx.com/v2"


class TelnyxAPIClient:
    """Lightweight v2 Telnyx API client.
    
    Protects sensitive credentials and respects strict safety read/write gates.
    """

    def __init__(self, config: TelephonyConfig) -> None:
        self.config = config

    def _get_headers(self) -> Dict[str, str]:
        """Returns required HTTP headers with Bearer token authentication."""
        if not self.config.telnyx_api_key:
            raise ValueError("TELNYX_API_KEY is not configured in the environment.")
        return {
            "Authorization": f"Bearer {self.config.telnyx_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _sanitize_error(self, exc: Exception) -> str:
        """Helper to scrub any potential credential leaks in exception strings."""
        err_msg = str(exc)
        if self.config.telnyx_api_key:
            err_msg = err_msg.replace(self.config.telnyx_api_key, "TELNYX_API_KEY_REDACTED")
        return err_msg

    def _check_read_gate(self, action_name: str) -> bool:
        """Validates that read-only API calls are allowed under DANA_CONFIRM_TELNYX_READ."""
        if not self.config.dana_confirm_telnyx_read:
            logger.info("DRY-RUN (Read-Only): '%s' skipped (requires DANA_CONFIRM_TELNYX_READ=yes)", action_name)
            return False
        return True

    def _check_mutation_gate(self, action_name: str) -> bool:
        """Validates that resource creation/mutation calls are allowed."""
        if not self.config.dana_confirm_telnyx_mutation:
            logger.info("DRY-RUN (Mutation): '%s' skipped (requires DANA_CONFIRM_TELNYX_MUTATION=yes)", action_name)
            return False
        return True

    def _check_purchase_gate(self, action_name: str) -> bool:
        """Validates that number purchases are allowed."""
        if not self.config.dana_confirm_purchase_number:
            logger.info("DRY-RUN (Purchase): '%s' skipped (requires DANA_CONFIRM_PURCHASE_NUMBER=yes)", action_name)
            return False
        return True

    # =========================================================================
    # Read-Only Operations (Guarded by DANA_CONFIRM_TELNYX_READ)
    # =========================================================================

    async def list_phone_numbers(self) -> Optional[List[Dict[str, Any]]]:
        """List phone numbers owned by the account.
        
        Endpoint: GET /v2/phone_numbers
        """
        action = "List phone numbers"
        if not self._check_read_gate(action):
            # Save dry-run record for tests/verification
            return None

        url = f"{BASE_URL}/phone_numbers"
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(url, headers=self._get_headers())
                if res.status_code != 200:
                    logger.error("Telnyx API Error listing phone numbers (Status %d): %s", res.status_code, res.text)
                    return None
                return res.json().get("data", [])
        except httpx.HTTPError as e:
            logger.error("HTTP Exception listing phone numbers: %s", self._sanitize_error(e))
            return None

    async def get_phone_number_details(self, number_id: str) -> Optional[Dict[str, Any]]:
        """Get phone number details.
        
        Endpoint: GET /v2/phone_numbers/{id}
        """
        action = f"Get phone number details for ID: {number_id}"
        if not self._check_read_gate(action):
            return None

        url = f"{BASE_URL}/phone_numbers/{number_id}"
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(url, headers=self._get_headers())
                if res.status_code != 200:
                    logger.error("Telnyx API Error fetching number details (Status %d): %s", res.status_code, res.text)
                    return None
                return res.json().get("data", {})
        except httpx.HTTPError as e:
            logger.error("HTTP Exception fetching number details: %s", self._sanitize_error(e))
            return None

    async def list_outbound_voice_profiles(self) -> Optional[List[Dict[str, Any]]]:
        """List outbound voice profiles.
        
        Endpoint: GET /v2/outbound_voice_profiles
        """
        action = "List outbound voice profiles"
        if not self._check_read_gate(action):
            return None

        url = f"{BASE_URL}/outbound_voice_profiles"
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(url, headers=self._get_headers())
                if res.status_code != 200:
                    logger.error("Telnyx API Error listing voice profiles (Status %d): %s", res.status_code, res.text)
                    return None
                return res.json().get("data", [])
        except httpx.HTTPError as e:
            logger.error("HTTP Exception listing voice profiles: %s", self._sanitize_error(e))
            return None

    async def list_credential_connections(self) -> Optional[List[Dict[str, Any]]]:
        """List credential connections.
        
        Endpoint: GET /v2/credential_connections
        """
        action = "List credential connections"
        if not self._check_read_gate(action):
            return None

        url = f"{BASE_URL}/credential_connections"
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(url, headers=self._get_headers())
                if res.status_code != 200:
                    logger.error("Telnyx API Error listing credential connections (Status %d): %s", res.status_code, res.text)
                    return None
                return res.json().get("data", [])
        except httpx.HTTPError as e:
            logger.error("HTTP Exception listing credential connections: %s", self._sanitize_error(e))
            return None

    async def search_available_phone_numbers(self, filter_params: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """Search available phone numbers.
        
        Endpoint: GET /v2/available_phone_numbers
        """
        action = f"Search available numbers with filters: {filter_params}"
        if not self._check_read_gate(action):
            return None

        url = f"{BASE_URL}/available_phone_numbers"
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(url, headers=self._get_headers(), params=filter_params)
                if res.status_code != 200:
                    logger.error("Telnyx API Error searching available numbers (Status %d): %s", res.status_code, res.text)
                    return None
                return res.json().get("data", [])
        except httpx.HTTPError as e:
            logger.error("HTTP Exception searching available numbers: %s", self._sanitize_error(e))
            return None

    # =========================================================================
    # Mutation Operations (Guarded by DANA_CONFIRM_TELNYX_MUTATION)
    # =========================================================================

    async def create_credential_connection(self, connection_name: str) -> Optional[Dict[str, Any]]:
        """Create a credential SIP connection.
        
        Endpoint: POST /v2/credential_connections
        """
        action = f"Create credential connection '{connection_name}'"
        if not self._check_mutation_gate(action):
            return {"id": None, "real_resource_created": False, "status": "dry_run", "would_create": True, "connection_name": connection_name}

        url = f"{BASE_URL}/credential_connections"
        payload = {
            "connection_name": connection_name,
            "active": True,
            "anchorsite_override": "any",
        }
        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(url, headers=self._get_headers(), json=payload)
                if res.status_code != 201:
                    logger.error("Telnyx API Error creating connection (Status %d): %s", res.status_code, res.text)
                    return None
                return res.json().get("data", {})
        except httpx.HTTPError as e:
            logger.error("HTTP Exception creating connection: %s", self._sanitize_error(e))
            return None

    async def update_credential_connection(self, connection_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update an existing credential connection (e.g., set outbound voice profile).
        
        Endpoint: PATCH /v2/credential_connections/{id}
        """
        action = f"Update credential connection ID {connection_id} with payload: {payload}"
        if not self._check_mutation_gate(action):
            return {"id": None, "real_resource_created": False, "status": "dry_run", "would_create": True}

        url = f"{BASE_URL}/credential_connections/{connection_id}"
        try:
            async with httpx.AsyncClient() as client:
                res = await client.patch(url, headers=self._get_headers(), json=payload)
                if res.status_code != 200:
                    logger.error("Telnyx API Error updating connection (Status %d): %s", res.status_code, res.text)
                    return None
                return res.json().get("data", {})
        except httpx.HTTPError as e:
            logger.error("HTTP Exception updating connection: %s", self._sanitize_error(e))
            return None

    async def assign_phone_number_connection(self, phone_number_id: str, connection_id: str) -> Optional[Dict[str, Any]]:
        """Assign a phone number to a SIP credential connection.
        
        Endpoint: PATCH /v2/phone_numbers/{phone_number_id}
        """
        action = f"Assign phone number ID {phone_number_id} to connection ID {connection_id}"
        if not self._check_mutation_gate(action):
            return {"id": None, "real_resource_created": False, "status": "dry_run", "would_create": True}

        url = f"{BASE_URL}/phone_numbers/{phone_number_id}"
        payload = {"connection_id": connection_id}
        try:
            async with httpx.AsyncClient() as client:
                res = await client.patch(url, headers=self._get_headers(), json=payload)
                if res.status_code != 200:
                    logger.error("Telnyx API Error assigning number to connection (Status %d): %s", res.status_code, res.text)
                    return None
                return res.json().get("data", {})
        except httpx.HTTPError as e:
            logger.error("HTTP Exception assigning number connection: %s", self._sanitize_error(e))
            return None

    async def create_outbound_voice_profile(self, name: str) -> Optional[Dict[str, Any]]:
        """Create a new outbound voice profile.
        
        Endpoint: POST /v2/outbound_voice_profiles
        """
        action = f"Create outbound voice profile '{name}'"
        if not self._check_mutation_gate(action):
            return {"id": None, "real_resource_created": False, "status": "dry_run", "would_create": True, "name": name}

        url = f"{BASE_URL}/outbound_voice_profiles"
        payload = {
            "name": name,
            "traffic_type": "conversational",
        }
        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(url, headers=self._get_headers(), json=payload)
                if res.status_code != 201:
                    logger.error("Telnyx API Error creating voice profile (Status %d): %s", res.status_code, res.text)
                    return None
                return res.json().get("data", {})
        except httpx.HTTPError as e:
            logger.error("HTTP Exception creating voice profile: %s", self._sanitize_error(e))
            return None

    # =========================================================================
    # Purchase Operations (strictly guarded by DANA_CONFIRM_PURCHASE_NUMBER)
    # =========================================================================

    async def purchase_phone_number(self, phone_number: str) -> Optional[Dict[str, Any]]:
        """Purchase a phone number.
        
        Endpoint: POST /v2/number_orders
        """
        action = f"Purchase phone number '{phone_number}'"
        if not self._check_purchase_gate(action):
            return {
                "id": None,
                "real_resource_created": False,
                "status": "dry_run",
                "would_create": True,
                "phone_numbers": [{"phone_number": phone_number, "status": "dry_run"}]
            }

        url = f"{BASE_URL}/number_orders"
        payload = {
            "phone_numbers": [
                {"phone_number": phone_number}
            ]
        }
        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(url, headers=self._get_headers(), json=payload)
                if res.status_code != 201:
                    logger.error("Telnyx API Error purchasing phone number (Status %d): %s", res.status_code, res.text)
                    return None
                return res.json().get("data", {})
        except httpx.HTTPError as e:
            logger.error("HTTP Exception purchasing phone number: %s", self._sanitize_error(e))
            return None
