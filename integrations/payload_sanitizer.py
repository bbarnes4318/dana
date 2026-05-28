import os
from typing import Any, Optional

# Case-insensitive partial-match aware forbidden keywords
FORBIDDEN_KEYWORDS = [
    "ssn", "social_security", "bank", "routing", "account",
    "credit_card", "card_number", "cvv", "payment",
    "system_prompt", "instructions", "compliance_internals",
    "raw_transcript"
]

INTERNAL_NOTES_KEYWORDS = [
    "handoff_summary", "objection_notes", "internal_lead_qualification_notes", "compliance_flags"
]

def mask_phone(phone: Optional[str]) -> Optional[str]:
    """Mask phone number to format +1555***4567 or similar to protect PII."""
    if not phone:
        return phone
    phone_str = str(phone).strip()
    if phone_str.startswith("+1") and len(phone_str) >= 10:
        prefix = phone_str[:5]  # e.g. +1555
        suffix = phone_str[-4:]  # e.g. 4567
        middle_len = len(phone_str) - 9
        if middle_len <= 0:
            middle_len = 3
        return f"{prefix}{'*' * middle_len}{suffix}"
    elif len(phone_str) >= 8:
        prefix = phone_str[:3]  # e.g. 555
        suffix = phone_str[-4:]  # e.g. 4567
        middle_len = len(phone_str) - 7
        if middle_len <= 0:
            middle_len = 3
        return f"{prefix}{'*' * middle_len}{suffix}"
    return "****"

def sanitize_payload(payload: Any, is_dashboard: bool = False) -> Any:
    """Recursively inspect and sanitize keys to prevent leaking PII and internals.
    
    Args:
        payload: The payload dictionary, list, or scalar to sanitize.
        is_dashboard: If True, internal handoff metadata is preserved (dashboard use only).
                      If False, internal notes and summaries are stripped for external CRM.
    """
    send_full_phone = os.getenv("DANA_CRM_SEND_FULL_PHONE", "no").lower() == "yes"
    send_transcript = os.getenv("DANA_CRM_SEND_TRANSCRIPT", "no").lower() == "yes"
    send_recording_url = os.getenv("DANA_CRM_SEND_RECORDING_URL", "no").lower() == "yes"

    forbidden_list = list(FORBIDDEN_KEYWORDS)
    if not send_recording_url:
        forbidden_list.append("recording_url")
    if not send_transcript:
        forbidden_list.append("transcript")

    if isinstance(payload, dict):
        sanitized = {}
        for k, v in payload.items():
            k_lower = k.lower()
            
            # 1. Strip forbidden keywords (case-insensitive, partial match)
            if any(kw in k_lower for kw in forbidden_list):
                continue
            
            # 2. Strip internal notes/summaries for external destinations
            if not is_dashboard and any(ik in k_lower for ik in INTERNAL_NOTES_KEYWORDS):
                continue
                
            # 5. Mask phone unless full phone is enabled
            if k_lower in ("phone_e164", "prospect_phone", "phone", "phone_number") and not send_full_phone:
                sanitized[k] = mask_phone(v)
            else:
                sanitized[k] = sanitize_payload(v, is_dashboard=is_dashboard)
        return sanitized
        
    elif isinstance(payload, list):
        return [sanitize_payload(item, is_dashboard=is_dashboard) for item in payload]
        
    else:
        return payload
