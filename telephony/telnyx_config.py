"""
Telephony Configuration Loader
Implements safe environment loading for Telnyx and LiveKit SIP telephony settings.
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

# Load env variables (useful during local development)
load_dotenv()


def env_str(name: str, default: str = "") -> str:
    """Load an environment variable as a trimmed string. Falls back to default if unset or empty."""
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    return val.strip()


def env_bool(name: str, default: bool = False) -> bool:
    """Load an environment variable as a boolean.
    
    Truthy: "true", "1", "yes", "y" (case-insensitive).
    Falsy: "false", "0", "no", "n" (case-insensitive).
    """
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    normalized = val.strip().lower()
    if normalized in ("true", "1", "yes", "y"):
        return True
    if normalized in ("false", "0", "no", "n"):
        return False
    return default


def required(name: str) -> str:
    """Retrieve a required environment variable or raise a safe ValueError."""
    val = env_str(name)
    if not val:
        raise ValueError(f"Missing required environment variable: {name}")
    return val


@dataclass
class TelephonyConfig:
    """Type-safe configuration loader for Telnyx + LiveKit SIP telephony layer."""

    # ---- LiveKit Configuration ----
    livekit_url: str = field(default_factory=lambda: env_str("LIVEKIT_URL"))
    livekit_api_key: str = field(default_factory=lambda: env_str("LIVEKIT_API_KEY"))
    livekit_api_secret: str = field(default_factory=lambda: env_str("LIVEKIT_API_SECRET"))

    # ---- Telnyx Configuration ----
    telnyx_api_key: str = field(default_factory=lambda: env_str("TELNYX_API_KEY"))
    telnyx_sip_address: str = field(default_factory=lambda: env_str("TELNYX_SIP_ADDRESS", "sip.telnyx.com"))
    telnyx_connection_id: str = field(default_factory=lambda: env_str("TELNYX_CONNECTION_ID"))
    telnyx_outbound_voice_profile_id: str = field(default_factory=lambda: env_str("TELNYX_OUTBOUND_VOICE_PROFILE_ID"))
    telnyx_phone_number_id: str = field(default_factory=lambda: env_str("TELNYX_PHONE_NUMBER_ID"))
    telnyx_outbound_number: str = field(default_factory=lambda: env_str("TELNYX_OUTBOUND_NUMBER"))
    telnyx_sip_username: str = field(default_factory=lambda: env_str("TELNYX_SIP_USERNAME"))
    telnyx_sip_password: str = field(default_factory=lambda: env_str("TELNYX_SIP_PASSWORD"))

    # ---- SIP & Outbound Call Settings ----
    livekit_sip_outbound_trunk_id: str = field(default_factory=lambda: env_str("LIVEKIT_SIP_OUTBOUND_TRUNK_ID"))
    dana_default_caller_id: str = field(default_factory=lambda: env_str("DANA_DEFAULT_CALLER_ID"))
    dana_room_prefix: str = field(default_factory=lambda: env_str("DANA_ROOM_PREFIX", "dana-call"))
    licensed_agent_phone_number: str = field(default_factory=lambda: env_str("LICENSED_AGENT_PHONE_NUMBER"))

    # ---- Safety Gates ----
    dana_confirm_telnyx_read: bool = field(default_factory=lambda: env_bool("DANA_CONFIRM_TELNYX_READ", False))
    dana_confirm_telnyx_provision: bool = field(default_factory=lambda: env_bool("DANA_CONFIRM_TELNYX_PROVISION", False))
    dana_confirm_telnyx_mutation: bool = field(default_factory=lambda: env_bool("DANA_CONFIRM_TELNYX_MUTATION", False))
    dana_confirm_purchase_number: bool = field(default_factory=lambda: env_bool("DANA_CONFIRM_PURCHASE_NUMBER", False))
    dana_confirm_create_livekit_trunk: bool = field(default_factory=lambda: env_bool("DANA_CONFIRM_CREATE_LIVEKIT_TRUNK", False))
    dana_confirm_accept_unverified_livekit_trunk: bool = field(default_factory=lambda: env_bool("DANA_CONFIRM_ACCEPT_UNVERIFIED_LIVEKIT_TRUNK", False))
    dana_confirm_place_call: bool = field(default_factory=lambda: env_bool("DANA_CONFIRM_PLACE_CALL", False))
    dana_confirm_transfer_call: bool = field(default_factory=lambda: env_bool("DANA_CONFIRM_TRANSFER_CALL", False))
    
    # ---- Provisioning Orchestration Gates ----
    dana_provision_mode: str = field(default_factory=lambda: env_str("DANA_PROVISION_MODE", "plan"))
    dana_provision_apply_confirm: bool = field(default_factory=lambda: env_bool("DANA_PROVISION_APPLY_CONFIRM", False))
    
    # ---- Purchase Filters ----
    telnyx_purchase_country: str = field(default_factory=lambda: env_str("TELNYX_PURCHASE_COUNTRY"))
    telnyx_purchase_area_code: str = field(default_factory=lambda: env_str("TELNYX_PURCHASE_AREA_CODE"))
    telnyx_purchase_locality: str = field(default_factory=lambda: env_str("TELNYX_PURCHASE_LOCALITY"))

    def __post_init__(self):
        # Apply defaults where secondary variables depend on primary ones
        if not self.dana_default_caller_id:
            self.dana_default_caller_id = self.telnyx_outbound_number

    def validate_for_telnyx(self, write_required: bool = False):
        """Validate Telnyx API keys for read-only or mutation/write modes."""
        if not self.telnyx_api_key or self.telnyx_api_key == "replace_me":
            raise ValueError("TELNYX_API_KEY is required and must not be empty.")

    def validate_for_livekit(self):
        """Validate LiveKit credentials."""
        if not self.livekit_url or self.livekit_url == "replace_me":
            raise ValueError("LIVEKIT_URL is required and must not be empty.")
        if not self.livekit_api_key or self.livekit_api_key == "replace_me":
            raise ValueError("LIVEKIT_API_KEY is required and must not be empty.")
        if not self.livekit_api_secret or self.livekit_api_secret == "replace_me":
            raise ValueError("LIVEKIT_API_SECRET is required and must not be empty.")

    def validate_api_keys(self):
        """Perform validation of primary required fields for backward compatibility."""
        self.validate_for_telnyx(write_required=False)
        self.validate_for_livekit()

    def __repr__(self) -> str:
        """Secure string representation filtering out sensitive credential data."""
        def mask(val: str) -> str:
            if not val or val == "replace_me":
                return "unset"
            if len(val) <= 8:
                return "********"
            return f"{val[:4]}...{val[-4:]}"

        return (
            f"TelephonyConfig("
            f"livekit_url={self.livekit_url!r}, "
            f"livekit_api_key={mask(self.livekit_api_key)!r}, "
            f"livekit_api_secret='[REDACTED]', "
            f"telnyx_api_key='[REDACTED]', "
            f"telnyx_sip_address={self.telnyx_sip_address!r}, "
            f"telnyx_connection_id={self.telnyx_connection_id!r}, "
            f"telnyx_outbound_voice_profile_id={self.telnyx_outbound_voice_profile_id!r}, "
            f"telnyx_phone_number_id={self.telnyx_phone_number_id!r}, "
            f"telnyx_outbound_number={mask(self.telnyx_outbound_number)!r}, "
            f"telnyx_sip_username={mask(self.telnyx_sip_username)!r}, "
            f"telnyx_sip_password='[REDACTED]', "
            f"livekit_sip_outbound_trunk_id={self.livekit_sip_outbound_trunk_id!r}, "
            f"dana_default_caller_id={mask(self.dana_default_caller_id)!r}, "
            f"dana_room_prefix={self.dana_room_prefix!r}, "
            f"licensed_agent_phone_number={mask(self.licensed_agent_phone_number)!r}, "
            f"dana_confirm_telnyx_read={self.dana_confirm_telnyx_read}, "
            f"dana_confirm_telnyx_provision={self.dana_confirm_telnyx_provision}, "
            f"dana_confirm_telnyx_mutation={self.dana_confirm_telnyx_mutation}, "
            f"dana_confirm_purchase_number={self.dana_confirm_purchase_number}, "
            f"dana_confirm_create_livekit_trunk={self.dana_confirm_create_livekit_trunk}, "
            f"dana_confirm_accept_unverified_livekit_trunk={self.dana_confirm_accept_unverified_livekit_trunk}, "
            f"dana_confirm_place_call={self.dana_confirm_place_call}, "
            f"dana_confirm_transfer_call={self.dana_confirm_transfer_call}"
            f")"
        )
