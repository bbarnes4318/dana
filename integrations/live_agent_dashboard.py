import os
import unittest.mock
from datetime import datetime, timezone, timedelta
from typing import Optional, Any
from integrations.payload_sanitizer import mask_phone

try:
    from livekit.api import AccessToken, VideoGrants
except ImportError:
    AccessToken = None
    VideoGrants = None

def generate_livekit_token(call_id: str, identity: str, role: str) -> str:
    """Generate a locked-down, single-room LiveKit WebRTC AccessToken with a 10-minute TTL.
    
    Enforces distinct role permissions:
      - agent: publish_audio=True, subscribe=True
      - supervisor: publish_audio=False, subscribe=True
    """
    allow_mock = os.getenv("DANA_ALLOW_MOCK_LIVEKIT_TOKENS", "no").lower() == "yes"
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")

    # Determine if we should generate mock tokens
    is_mock = False
    if AccessToken is None or isinstance(AccessToken, unittest.mock.Mock):
        is_mock = True

    if is_mock:
        if not allow_mock:
            raise ValueError(
                "LiveKit Server SDK is mocked/missing, but DANA_ALLOW_MOCK_LIVEKIT_TOKENS is not 'yes'. "
                "Failing token generation to prevent unauthorized production access."
            )
        return f"mock_token_{role}_{call_id}"

    # Verify credentials if we are in production (real SDK present, not allowing mock)
    if (not api_key or not api_secret) and not allow_mock:
        raise ValueError(
            "Missing LIVEKIT_API_KEY or LIVEKIT_API_SECRET in environment. "
            "Failing token generation in production mode."
        )

    # Scoped grants
    grants = VideoGrants(
        room_join=True,
        room=call_id,
        can_subscribe=True
    )
    if role == "agent":
        grants.can_publish = True
    elif role == "supervisor":
        grants.can_publish = False
    else:
        grants.can_publish = False

    # Short TTL (10 minutes)
    ttl = timedelta(minutes=10)

    token = AccessToken(
        api_key=api_key or "dummy_key",
        api_secret=api_secret or "dummy_secret",
        ttl=ttl
    )
    token.identity = identity
    token.name = identity
    token.video_grants = grants
    
    res = token.to_jwt()
    return res if isinstance(res, str) else str(res)

def generate_dashboard_notification(
    call_id: str,
    lead: dict,
    campaign: dict,
    agent_id: Optional[str] = None
) -> dict:
    """Generate the complete dashboard notification payload for an incoming qualified lead.
    
    Includes masked phone by default, accept/decline action definitions, 
    and a short-lived token.
    """
    agent_id = agent_id or "usr_default"
    identity = f"human_agent_{agent_id}"
    
    # Generate short token for WebRTC join
    token = generate_livekit_token(call_id, identity, "agent")
    
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    
    # 1. Phone number is masked by default, unless config overrides
    send_full_phone = os.getenv("DANA_CRM_SEND_FULL_PHONE", "no").lower() == "yes"
    raw_phone = lead.get("phone_e164") or lead.get("phone") or lead.get("phone_number") or ""
    prospect_phone = raw_phone if send_full_phone else mask_phone(raw_phone)
    
    # 2. Handoff summary (allowed for internal dashboard use)
    from telephony.handoff_summary import build_handoff_summary
    handoff_summary = build_handoff_summary(lead)
    
    # Accept and Decline URLs
    dashboard_base_url = os.getenv("DANA_DASHBOARD_BASE_URL", "http://localhost:8000")
    accept_url = f"{dashboard_base_url}/api/calls/{call_id}/accept"
    decline_url = f"{dashboard_base_url}/api/calls/{call_id}/decline"

    return {
        "event": "incoming_qualified_lead",
        "call_id": call_id,
        "campaign_id": campaign.get("campaign_id"),
        "lead_id": lead.get("lead_id") or lead.get("id"),
        "room_name": call_id,
        "token": token,
        "role": "agent",
        "expires_at": expires_at,
        "handoff_summary": handoff_summary,
        "prospect_phone": prospect_phone,
        "lead_state": lead.get("lead_state") or lead.get("state"),
        "transfer_mode": campaign.get("transfer_mode", "warm_bridge"),
        "accept_action": {
            "url": accept_url,
            "method": "POST"
        },
        "decline_action": {
            "url": decline_url,
            "method": "POST"
        },
        "timeout_seconds": 30,
        "fallback_callback_action": {
            "action": "callback_fallback"
        }
    }
