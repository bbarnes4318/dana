import os
import sys
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from storage.repository import Repository

def mask_phone(phone: Optional[str]) -> str:
    """Mask a phone number for safety in public monitoring logs."""
    if not phone:
        return ""
    phone = str(phone)
    if len(phone) > 4:
        if phone.endswith("****"):
            return phone
        return phone[:-4] + "****"
    return "****"

async def get_live_campaign_monitor_snapshot(repository: Repository, campaign_id: Optional[str] = None) -> dict:
    """Query recent database entries and generate a monitoring status snapshot."""
    
    # 1. Active campaigns
    all_campaigns = await repository.query_outbound_campaigns({})
    active_campaigns = []
    for camp in all_campaigns:
        c_id = camp["id"].replace("campaign:", "") if "id" in camp else camp.get("campaign_id")
        if campaign_id and c_id != campaign_id:
            continue
        if camp.get("status") in ("running", "active", "starting", "ready"):
            active_campaigns.append({
                "campaign_id": c_id,
                "name": camp.get("name"),
                "status": camp.get("status"),
                "max_concurrent_calls": camp.get("max_concurrent_calls"),
                "daily_call_cap": camp.get("daily_call_cap"),
            })

    # 2. Active calls, calls by status, and DID usage
    recent_attempts = await repository.list_recent_call_attempts(limit=100)
    active_calls = []
    calls_by_status = {}
    did_usage_map = {}
    recent_turns = []
    recent_exports = []

    for att in recent_attempts:
        att_campaign_id = att.get("campaign_id")
        if campaign_id and att_campaign_id != campaign_id:
            continue

        status = att.get("status", "unknown")
        calls_by_status[status] = calls_by_status.get(status, 0) + 1

        phone_masked = mask_phone(att.get("phone_number_redacted") or att.get("phone_number"))

        if status in ("dialing", "ringing", "answered", "in_progress"):
            active_calls.append({
                "call_attempt_id": att.get("id"),
                "campaign_id": att_campaign_id,
                "status": status,
                "phone_number_masked": phone_masked,
                "selected_did": mask_phone(att.get("metadata", {}).get("selected_caller_id") or att.get("caller_id")),
                "livekit_room_name": att.get("livekit_room_name"),
            })
            
            did = att.get("metadata", {}).get("selected_caller_id") or att.get("caller_id")
            if did:
                did_masked = mask_phone(did)
                did_usage_map[did_masked] = did_usage_map.get(did_masked, 0) + 1

        # Turn count checking
        try:
            turns = await repository.query_call_turns({"call_id": att.get("id")})
            agent_turns = sum(1 for t in turns if t.get("speaker") == "agent")
            prospect_turns = sum(1 for t in turns if t.get("speaker") == "prospect")
            
            recent_turns.append({
                "call_attempt_id": att.get("id"),
                "phone_number_masked": phone_masked,
                "turn_count": len(turns),
                "agent_turn_count": agent_turns,
                "prospect_turn_count": prospect_turns,
            })
        except Exception:
            pass

        # Recent exports
        if att.get("post_call_export_path"):
            recent_exports.append({
                "call_attempt_id": att.get("id"),
                "phone_number_masked": phone_masked,
                "post_call_export_path": att.get("post_call_export_path"),
            })

    # 3. Live call sessions
    all_sessions = await repository.query_live_call_sessions({})
    live_sessions = []
    for s in all_sessions:
        s_camp_id = s.get("campaign_id")
        if campaign_id and s_camp_id != campaign_id:
            continue
        if s.get("status") in ("starting", "ringing", "active", "transferring"):
            live_sessions.append({
                "session_id": s.get("id"),
                "campaign_id": s_camp_id,
                "status": s.get("status"),
                "current_stage": s.get("current_stage"),
                "livekit_room_name": s.get("livekit_room_name"),
            })

    # Format DID usage
    did_usage = [{"caller_id": k, "active_calls": v} for k, v in did_usage_map.items()]

    # 4. Safety blockers
    safety_blockers = []
    warnings = []

    # Check worker status
    try:
        from telephony.livekit_agent_worker import check_worker_dependencies
        worker_status = check_worker_dependencies()
        if not worker_status.get("ready", False):
            safety_blockers.append("LiveKit agent worker is not ready.")
    except Exception as e:
        warnings.append(f"Worker status check error: {e}")

    # Check DID pool
    try:
        from telephony.did_pool import DIDPoolManager
        pool_mgr = DIDPoolManager(repository)
        provider = os.environ.get("DANA_ACTIVE_TELEPHONY_PROVIDER", "telnyx").strip().lower()
        numbers = await pool_mgr.list_numbers(provider=provider)
        if not numbers:
            safety_blockers.append(f"No phone numbers available in DID pool for provider {provider}.")
    except Exception as e:
        warnings.append(f"DID pool query error: {e}")

    return {
        "active_campaigns": active_campaigns,
        "active_calls": active_calls,
        "calls_by_status": calls_by_status,
        "did_usage": did_usage,
        "live_sessions": live_sessions,
        "recent_turns": recent_turns[:10],
        "recent_exports": recent_exports[:10],
        "safety_blockers": safety_blockers,
        "warnings": warnings
    }
