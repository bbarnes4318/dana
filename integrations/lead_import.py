import re
from typing import Any, Optional, Dict
from compliance.calling_window import resolve_lead_timezone

# Map of US state names and abbreviations to standard 2-letter codes
STATE_MAP = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO",
    "montana": "MT", "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH",
    "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "al": "AL", "ak": "AK", "az": "AZ", "ar": "AR", "ca": "CA", "co": "CO", "ct": "CT", "de": "DE",
    "fl": "FL", "ga": "GA", "hi": "HI", "id": "ID", "il": "IL", "in": "IN", "ia": "IA", "ks": "KS",
    "ky": "KY", "la": "LA", "me": "ME", "md": "MD", "ma": "MA", "mi": "MI", "mn": "MN", "ms": "MS",
    "mo": "MO", "mt": "MT", "ne": "NE", "nv": "NV", "nh": "NH", "nj": "NJ", "nm": "NM", "ny": "NY",
    "nc": "NC", "nd": "ND", "oh": "OH", "ok": "OK", "or": "OR", "pa": "PA", "ri": "RI", "sc": "SC",
    "sd": "SD", "tn": "TN", "tx": "TX", "ut": "UT", "vt": "VT", "va": "VA", "wa": "WA", "wv": "WV",
    "wi": "WI", "wy": "WY"
}

# Multi-timezone US states
MULTI_TZ_STATES = {"TX", "FL", "TN", "KY", "IN", "MI", "ND", "SD", "NE", "KS", "OR", "ID"}

STANDARD_COLUMNS = {
    "phone_e164", "phone", "phone_number", "first_name", "last_name",
    "state", "state_code", "timezone", "source_vendor", "campaign_id",
    "consent_artifact_id", "id", "lead_id"
}

def normalize_phone(phone: Any) -> str:
    """Normalize phone number to E.164 format. Raises ValueError if invalid."""
    if not phone:
        raise ValueError("Phone number is required and cannot be empty")
        
    phone_str = str(phone).strip()
    
    # Check if it already matches standard E.164 (e.g. +15551234567 or +445551234567)
    if phone_str.startswith("+"):
        digits_only = "".join(c for c in phone_str[1:] if c.isdigit())
        if 7 <= len(digits_only) <= 15:
            return f"+{digits_only}"
        raise ValueError(f"Invalid E.164 phone structure: {phone_str}")
        
    # Extract only digits
    digits = "".join(c for c in phone_str if c.isdigit())
    
    # US 10-digit
    if len(digits) == 10:
        return f"+1{digits}"
        
    # US 11-digit starting with 1
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
        
    raise ValueError(f"Could not normalize phone number to E.164 format: {phone_str}")

def normalize_name(name: Optional[str]) -> Optional[str]:
    """Normalize name casing while preserving complex mixed-casing (Mc/Mac, hyphens, apostrophes)."""
    if not name or not isinstance(name, str):
        return name
    name_stripped = name.strip()
    if not name_stripped:
        return name_stripped

    # If it is mixed case (contains both upper and lowercase, not all-caps), preserve it as is.
    any_upper = any(c.isupper() for c in name_stripped)
    any_lower = any(c.islower() for c in name_stripped)
    if any_upper and any_lower:
        return name_stripped

    # Short acronyms (e.g. IBM, USA, JR) should remain capitalized
    if name_stripped.isupper() and len(name_stripped) <= 3:
        return name_stripped

    # Capitalize parts
    parts = name_stripped.split(" ")
    normalized_parts = []
    for part in parts:
        if not part:
            normalized_parts.append("")
            continue
            
        p = part.lower().capitalize()

        # Mc prefix (McDonald)
        if p.startswith("Mc") and len(p) >= 3:
            p = "Mc" + p[2].upper() + p[3:]
        # Mac prefix (MacArthur)
        elif p.startswith("Mac") and len(p) >= 4:
            p = "Mac" + p[3].upper() + p[4:]
        
        # Hyphenated names (Smith-Jones)
        if "-" in p:
            subparts = p.split("-")
            p = "-".join(sp.capitalize() for sp in subparts)
            
        # Names with apostrophes (O'Connor)
        if "'" in p:
            subparts = p.split("'")
            p = "'".join(sp.capitalize() for sp in subparts)

        # Dutch/Spanish lowercase multi-word prefixes
        if p.lower() in ("van", "der", "de", "la"):
            p = p.lower()

        normalized_parts.append(p)

    return " ".join(normalized_parts)

def normalize_lead(data: dict, strict_state: bool = False) -> dict:
    """Normalize an imported lead record and preserve raw fields and custom parameters.
    
    Args:
        data: Raw imported lead dictionary.
        strict_state: If True, invalid states raise a ValueError.
        
    Returns:
        dict containing:
          - "normalized": dict of standard normalized lead fields
          - "raw_import_payload": original input dict
          - "custom_fields": dict of remaining custom parameters
    """
    raw_payload = dict(data)
    custom_fields = {}
    
    # 1. Normalize Phone
    raw_phone = data.get("phone_e164") or data.get("phone") or data.get("phone_number")
    normalized_phone = normalize_phone(raw_phone)

    # 2. Normalize Names
    raw_first = data.get("first_name")
    raw_last = data.get("last_name")
    
    normalized_first = normalize_name(raw_first)
    normalized_last = normalize_name(raw_last)

    # Check if name was changed to preserve raw name values in custom_fields
    if raw_first and normalized_first != raw_first.strip():
        custom_fields["raw_first_name"] = raw_first
    if raw_last and normalized_last != raw_last.strip():
        custom_fields["raw_last_name"] = raw_last

    # 3. Normalize State
    raw_state = data.get("state") or data.get("state_code")
    normalized_state = None
    if raw_state:
        state_key = str(raw_state).strip().lower()
        if state_key in STATE_MAP:
            normalized_state = STATE_MAP[state_key]
        else:
            if strict_state:
                raise ValueError(f"Invalid US state name or code: {raw_state}")
            # Non-strict mode saves raw in custom_fields
            custom_fields["raw_invalid_state"] = raw_state

    # 4. Resolve Timezone & Confidence Level
    # Create temp dict for resolve_lead_timezone
    temp_lead = {
        "phone_e164": normalized_phone,
        "lead_state": normalized_state,
        "callback_timezone": data.get("timezone") or data.get("callback_timezone"),
        "verified_city_state": data.get("verified_city_state")
    }
    tz_str, tz_source, tz_confidence = resolve_lead_timezone(temp_lead)
    confidence = tz_confidence

    # Build custom_fields from all non-standard columns
    for k, v in data.items():
        if k not in STANDARD_COLUMNS:
            custom_fields[k] = v

    normalized_dict = {
        "id": data.get("id") or data.get("lead_id"),
        "lead_id": data.get("lead_id") or data.get("id"),
        "phone_e164": normalized_phone,
        "first_name": normalized_first,
        "last_name": normalized_last,
        "state": normalized_state,
        "timezone": tz_str,
        "timezone_confidence": confidence,
        "source_vendor": data.get("source_vendor"),
        "campaign_id": data.get("campaign_id"),
        "consent_artifact_id": data.get("consent_artifact_id")
    }

    return {
        "normalized": normalized_dict,
        "raw_import_payload": raw_payload,
        "custom_fields": custom_fields
    }
