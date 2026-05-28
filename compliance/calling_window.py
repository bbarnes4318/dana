"""Timezone resolution and calling window compliance validation."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple, Union
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Mapping of two-letter state codes to standard standard timezones
STATE_TO_TZ: Dict[str, str] = {
    "AL": "America/Chicago",
    "AK": "America/Anchorage",
    "AZ": "America/Phoenix",
    "AR": "America/Chicago",
    "CA": "America/Los_Angeles",
    "CO": "America/Denver",
    "CT": "America/New_York",
    "DE": "America/New_York",
    "DC": "America/New_York",
    "FL": "America/New_York",
    "GA": "America/New_York",
    "HI": "Pacific/Honolulu",
    "ID": "America/Denver",
    "IL": "America/Chicago",
    "IN": "America/Indiana/Indianapolis",
    "IA": "America/Chicago",
    "KS": "America/Chicago",
    "KY": "America/New_York",
    "LA": "America/Chicago",
    "ME": "America/New_York",
    "MD": "America/New_York",
    "MA": "America/New_York",
    "MI": "America/New_York",
    "MN": "America/Chicago",
    "MS": "America/Chicago",
    "MO": "America/Chicago",
    "MT": "America/Denver",
    "NE": "America/Chicago",
    "NV": "America/Los_Angeles",
    "NH": "America/New_York",
    "NJ": "America/New_York",
    "NM": "America/Denver",
    "NY": "America/New_York",
    "NC": "America/New_York",
    "ND": "America/Chicago",
    "OH": "America/New_York",
    "OK": "America/Chicago",
    "OR": "America/Los_Angeles",
    "PA": "America/New_York",
    "RI": "America/New_York",
    "SC": "America/New_York",
    "SD": "America/Chicago",
    "TN": "America/Chicago",
    "TX": "America/Chicago",
    "UT": "America/Denver",
    "VT": "America/New_York",
    "VA": "America/New_York",
    "WA": "America/Los_Angeles",
    "WV": "America/New_York",
    "WI": "America/Chicago",
    "WY": "America/Denver"
}

# Major US phone area codes to standard timezones
AREA_CODE_TO_TZ: Dict[str, str] = {
    # Eastern
    "201": "America/New_York", "203": "America/New_York", "207": "America/New_York",
    "212": "America/New_York", "215": "America/New_York", "216": "America/New_York",
    "229": "America/New_York", "234": "America/New_York", "239": "America/New_York",
    "240": "America/New_York", "248": "America/New_York", "252": "America/New_York",
    "267": "America/New_York", "272": "America/New_York", "276": "America/New_York",
    "301": "America/New_York", "302": "America/New_York", "304": "America/New_York",
    "305": "America/New_York", "313": "America/New_York", "315": "America/New_York",
    "321": "America/New_York", "330": "America/New_York", "336": "America/New_York",
    "339": "America/New_York", "347": "America/New_York", "351": "America/New_York",
    "352": "America/New_York", "386": "America/New_York", "401": "America/New_York",
    "407": "America/New_York", "410": "America/New_York", "412": "America/New_York",
    "413": "America/New_York", "419": "America/New_York", "434": "America/New_York",
    "440": "America/New_York", "443": "America/New_York", "470": "America/New_York",
    "478": "America/New_York", "484": "America/New_York", "508": "America/New_York",
    "513": "America/New_York", "518": "America/New_York", "540": "America/New_York",
    "561": "America/New_York", "570": "America/New_York", "571": "America/New_York",
    "585": "America/New_York", "586": "America/New_York", "607": "America/New_York",
    "609": "America/New_York", "617": "America/New_York", "631": "America/New_York",
    "646": "America/New_York", "678": "America/New_York", "703": "America/New_York",
    "704": "America/New_York", "716": "America/New_York", "717": "America/New_York",
    "718": "America/New_York", "724": "America/New_York", "727": "America/New_York",
    "732": "America/New_York", "734": "America/New_York", "740": "America/New_York",
    "754": "America/New_York", "757": "America/New_York", "772": "America/New_York",
    "774": "America/New_York", "781": "America/New_York", "786": "America/New_York",
    "802": "America/New_York", "803": "America/New_York", "804": "America/New_York",
    "810": "America/New_York", "813": "America/New_York", "814": "America/New_York",
    "828": "America/New_York", "838": "America/New_York", "843": "America/New_York",
    "845": "America/New_York", "848": "America/New_York", "856": "America/New_York",
    "860": "America/New_York", "862": "America/New_York", "864": "America/New_York",
    "908": "America/New_York", "910": "America/New_York", "914": "America/New_York",
    "917": "America/New_York", "919": "America/New_York", "937": "America/New_York",
    "941": "America/New_York", "954": "America/New_York", "973": "America/New_York",
    "978": "America/New_York", "980": "America/New_York", "989": "America/New_York",

    # Central
    "205": "America/Chicago", "210": "America/Chicago", "217": "America/Chicago",
    "218": "America/Chicago", "219": "America/Chicago", "224": "America/Chicago",
    "225": "America/Chicago", "228": "America/Chicago", "231": "America/Chicago",
    "251": "America/Chicago", "256": "America/Chicago", "260": "America/Chicago",
    "262": "America/Chicago", "269": "America/Chicago", "281": "America/Chicago",
    "309": "America/Chicago", "312": "America/Chicago", "314": "America/Chicago",
    "316": "America/Chicago", "318": "America/Chicago", "319": "America/Chicago",
    "320": "America/Chicago", "325": "America/Chicago", "331": "America/Chicago",
    "334": "America/Chicago", "337": "America/Chicago", "346": "America/Chicago",
    "361": "America/Chicago", "402": "America/Chicago", "405": "America/Chicago",
    "409": "America/Chicago", "414": "America/Chicago", "417": "America/Chicago",
    "469": "America/Chicago", "479": "America/Chicago", "501": "America/Chicago",
    "507": "America/Chicago", "512": "America/Chicago", "515": "America/Chicago",
    "563": "America/Chicago", "573": "America/Chicago", "580": "America/Chicago",
    "601": "America/Chicago", "605": "America/Chicago", "608": "America/Chicago",
    "612": "America/Chicago", "615": "America/Chicago", "618": "America/Chicago",
    "620": "America/Chicago", "630": "America/Chicago", "636": "America/Chicago",
    "641": "America/Chicago", "651": "America/Chicago", "660": "America/Chicago",
    "662": "America/Chicago", "682": "America/Chicago", "701": "America/Chicago",
    "708": "America/Chicago", "712": "America/Chicago", "713": "America/Chicago",
    "715": "America/Chicago", "731": "America/Chicago", "763": "America/Chicago",
    "769": "America/Chicago", "773": "America/Chicago", "779": "America/Chicago",
    "785": "America/Chicago", "806": "America/Chicago", "812": "America/Chicago",
    "815": "America/Chicago", "816": "America/Chicago", "817": "America/Chicago",
    "830": "America/Chicago", "832": "America/Chicago", "847": "America/Chicago",
    "850": "America/Chicago", "865": "America/Chicago", "901": "America/Chicago",
    "903": "America/Chicago", "913": "America/Chicago", "915": "America/Chicago",
    "918": "America/Chicago", "920": "America/Chicago", "931": "America/Chicago",
    "936": "America/Chicago", "940": "America/Chicago", "952": "America/Chicago",
    "956": "America/Chicago", "972": "America/Chicago", "979": "America/Chicago",
    "985": "America/Chicago",

    # Mountain
    "208": "America/Denver", "303": "America/Denver", "307": "America/Denver",
    "385": "America/Denver", "406": "America/Denver", "435": "America/Denver",
    "480": "America/Denver", "505": "America/Denver", "520": "America/Denver",
    "575": "America/Denver", "602": "America/Denver", "623": "America/Denver",
    "719": "America/Denver", "720": "America/Denver", "801": "America/Denver",
    "928": "America/Denver", "970": "America/Denver",

    # Pacific
    "206": "America/Los_Angeles", "209": "America/Los_Angeles", "213": "America/Los_Angeles",
    "253": "America/Los_Angeles", "310": "America/Los_Angeles", "323": "America/Los_Angeles",
    "341": "America/Los_Angeles", "360": "America/Los_Angeles", "408": "America/Los_Angeles",
    "415": "America/Los_Angeles", "424": "America/Los_Angeles", "425": "America/Los_Angeles",
    "442": "America/Los_Angeles", "458": "America/Los_Angeles", "503": "America/Los_Angeles",
    "509": "America/Los_Angeles", "510": "America/Los_Angeles", "530": "America/Los_Angeles",
    "541": "America/Los_Angeles", "559": "America/Los_Angeles", "562": "America/Los_Angeles",
    "619": "America/Los_Angeles", "626": "America/Los_Angeles", "628": "America/Los_Angeles",
    "650": "America/Los_Angeles", "661": "America/Los_Angeles", "669": "America/Los_Angeles",
    "702": "America/Los_Angeles", "707": "America/Los_Angeles", "714": "America/Los_Angeles",
    "725": "America/Los_Angeles", "747": "America/Los_Angeles", "760": "America/Los_Angeles",
    "805": "America/Los_Angeles", "818": "America/Los_Angeles", "831": "America/Los_Angeles",
    "858": "America/Los_Angeles", "909": "America/Los_Angeles", "916": "America/Los_Angeles",
    "925": "America/Los_Angeles", "949": "America/Los_Angeles", "951": "America/Los_Angeles",
    "971": "America/Los_Angeles",

    # Alaska
    "907": "America/Anchorage",

    # Hawaii
    "808": "Pacific/Honolulu"
}


def resolve_lead_timezone(lead: Union[dict, Any]) -> Tuple[Optional[str], str, str]:
    """Determine the timezone of the lead.
    
    1. Checks callback_timezone field.
    2. Maps lead_state or state abbreviation.
    3. Infers from lead_phone_e164 area code.
    
    Returns:
        (timezone_str, timezone_source, confidence)
    """
    # Multi-timezone US states
    MULTI_TZ_STATES = {"TX", "FL", "TN", "KY", "IN", "MI", "ND", "SD", "NE", "KS", "OR", "ID"}

    # Helper to get attributes/keys safely
    def get_val(key: str) -> Optional[Any]:
        if isinstance(lead, dict):
            return lead.get(key)
        return getattr(lead, key, None)

    # 1. Callback timezone -> High confidence
    cb_tz = get_val("callback_timezone")
    if cb_tz:
        return str(cb_tz), "explicit_timezone", "high"

    # 2. State abbreviation lookup
    state = get_val("lead_state") or get_val("state")
    if state and isinstance(state, str):
        state_key = state.strip().upper()
        if state_key in STATE_TO_TZ:
            if get_val("verified_city_state"):
                confidence = "high"
            elif state_key in MULTI_TZ_STATES:
                confidence = "medium"
            else:
                confidence = "medium/high"
            return STATE_TO_TZ[state_key], "lead_state", confidence

    # 3. Area code lookup from phone -> Low confidence
    phone = get_val("lead_phone_e164") or get_val("phone_e164") or get_val("phone_number")
    if phone and isinstance(phone, str):
        # Extract digits
        clean = "".join(c for c in phone if c.isdigit())
        # Handles +1 area codes or 10-digit raw
        if clean.startswith("1") and len(clean) >= 4:
            area_code = clean[1:4]
        elif len(clean) == 10:
            area_code = clean[0:3]
        else:
            area_code = None

        if area_code and area_code in AREA_CODE_TO_TZ:
            return AREA_CODE_TO_TZ[area_code], "area_code", "low"

    return None, "unknown", "unknown/low"


def is_calling_window_allowed(
    timezone_str: str,
    allowed_hours: Tuple[int, int],
    current_utc_time: Optional[datetime] = None
) -> bool:
    """Validate if target local time matches standard allowed hours.
    
    allowed_hours: tuple of (start_hour, end_hour) in 24h format (e.g. (8, 20) for 8 AM to 8 PM).
    """
    if current_utc_time is None:
        current_utc_time = datetime.now(timezone.utc)
    elif current_utc_time.tzinfo is None:
        # Assume UTC if naive
        current_utc_time = current_utc_time.replace(tzinfo=timezone.utc)

    try:
        tz = ZoneInfo(timezone_str)
        local_time = current_utc_time.astimezone(tz)
        local_hour = local_time.hour
        start_hour, end_hour = allowed_hours
        return start_hour <= local_hour < end_hour
    except Exception as e:
        logger.error("Error checking calling window for timezone '%s': %s", timezone_str, e)
        return False
