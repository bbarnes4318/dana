"""
Handoff Summary Builder
Generates a structured, concise qualification summary for the licensed agent.
This summary is strictly for internal agent use (e.g. whispers, CRM) and MUST NOT be spoken to the prospect.
"""

from __future__ import annotations

from typing import Any


def build_handoff_summary(lead_profile: dict[str, Any]) -> str:
    """Build a short internal handoff summary for the licensed agent.
    
    WARNING: Do NOT read or speak this summary to the prospect.
    
    Args:
        lead_profile: Dictionary representation of the LeadProfile.
        
    Returns:
        A structured string summary.
    """
    lines = [
        "INTERNAL LICENSED-AGENT HANDOFF SUMMARY (DO NOT SPEAK TO PROSPECT):",
        f"- Open to Review: {lead_profile.get('open_to_review')}",
        f"- Age Range Confirmed: {lead_profile.get('age_range_confirmed')}",
        f"- Living Independently: {lead_profile.get('living_independently')}",
        f"- Financial Decision Maker: {lead_profile.get('financial_decision_maker')}",
    ]
    
    # Callback preference
    if lead_profile.get("callback_requested") or lead_profile.get("callback_time_local"):
        time_pref = lead_profile.get("callback_time_local") or "As soon as possible"
        tz_pref = lead_profile.get("callback_timezone") or ""
        tz_suffix = f" ({tz_pref})" if tz_pref else ""
        lines.append(f"- Callback Preference: {time_pref}{tz_suffix}")
    else:
        lines.append("- Callback Preference: None")
        
    # Objection notes
    notes = lead_profile.get("notes", [])
    if notes:
        lines.append("- Call Notes:")
        for note in notes:
            lines.append(f"  * {note}")
            
    return "\n".join(lines)
