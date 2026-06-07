"""Safety and compliance analytics rollup functions."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from storage.repository import Repository
from analytics.platform_metrics import is_within_range


async def get_safety_metrics(
    repository: Optional[Repository] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None
) -> dict:
    """Calculate compliance hard fails, DNC failures, transfer consent violations, and unsafe phrase blocks."""
    repo = repository or Repository()
    
    # Query calls, turns, and QA reports
    calls = await repo.store.query("calls", {})
    turns = await repo.store.query("call_turns", {})
    
    # Filter calls within range
    filtered_calls = [
        c for c in calls
        if is_within_range(c.get("created_at") or c.get("started_at"), from_date, to_date)
    ]
    filtered_call_ids = {c["call_id"] for c in filtered_calls if "call_id" in c}
    
    compliance_hard_fails = 0
    dnc_failures = 0
    transfer_consent_violations = 0
    wrong_number_failures = 0
    unsafe_phrase_blocks = 0
    
    for c in filtered_calls:
        comp_flags = c.get("compliance_flags") or {}
        issues = comp_flags.get("issues") or []
        
        # 1. Compliance Hard Fails
        # If there are any compliance issues or explicit hard fail flag
        if comp_flags.get("hard_fail") is True or comp_flags.get("is_safe") is False or len(issues) > 0:
            compliance_hard_fails += 1
            
        # 2. DNC failure count
        if any("dnc" in str(issue).lower() or "do_not_call" in str(issue).lower() for issue in issues):
            dnc_failures += 1
            
        # 3. Transfer consent violations
        if any("consent" in str(issue).lower() or "transfer_before_consent" in str(issue).lower() for issue in issues):
            transfer_consent_violations += 1
            
        # 4. Wrong-number failures
        outcome = str(c.get("outcome") or "").lower()
        if "wrong" in outcome or outcome == "wrong_number" or any("wrong" in str(issue).lower() or "wrong_number" in str(issue).lower() for issue in issues):
            wrong_number_failures += 1
        else:
            # Fallback to transcript phrases scan
            transcript = c.get("transcript") or []
            for turn in transcript:
                if turn.get("speaker") == "prospect":
                    p_text = str(turn.get("text", "")).lower()
                    if any(phrase in p_text for phrase in ["wrong number", "not me", "not the person", "no such person", "don't know who that is", "wrong person"]):
                        wrong_number_failures += 1
                        break
            
    # 5. Unsafe phrase blocks (scan turns belonging to filtered calls)
    for t in turns:
        call_id = t.get("call_id")
        # Ensure the turn belongs to a call in our filtered list
        if call_id in filtered_call_ids:
            warnings = t.get("compliance_warnings") or []
            unsafe_phrase_blocks += len(warnings)
            
    return {
        "compliance_hard_fails": compliance_hard_fails,
        "dnc_failures": dnc_failures,
        "transfer_consent_violations": transfer_consent_violations,
        "wrong_number_failures": wrong_number_failures,
        "unsafe_phrase_blocks": unsafe_phrase_blocks
    }

