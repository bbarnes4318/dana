"""Platform-level metrics and overview rollup functions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from storage.repository import Repository, parse_dt


def is_within_range(dt_val, from_date: Optional[datetime] = None, to_date: Optional[datetime] = None) -> bool:
    """Helper to check if a datetime is within the specified range."""
    if not dt_val:
        return True
    dt = parse_dt(dt_val)
    if not dt:
        return True
    
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
        
    if from_date:
        from_dt = from_date
        if from_dt.tzinfo is None:
            from_dt = from_dt.replace(tzinfo=timezone.utc)
        if dt < from_dt:
            return False
            
    if to_date:
        to_dt = to_date
        if to_dt.tzinfo is None:
            to_dt = to_dt.replace(tzinfo=timezone.utc)
        if dt > to_dt:
            return False
            
    return True


async def get_platform_overview(
    repository: Optional[Repository] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None
) -> dict:
    """Calculate overall platform execution metrics and cost performance."""
    repo = repository or Repository()
    
    calls = await repo.store.query("calls", {})
    filtered_calls = [c for c in calls if is_within_range(c.get("created_at") or c.get("started_at"), from_date, to_date)]
    
    total_calls = len(filtered_calls)
    connected_calls = sum(1 for c in filtered_calls if c.get("answered_at") is not None)
    
    transfers = sum(1 for c in filtered_calls if c.get("outcome") == "transferred")
    callbacks = sum(1 for c in filtered_calls if c.get("outcome") == "callback")
    dnc_count = sum(1 for c in filtered_calls if c.get("outcome") == "dnc")
    
    # Wrong numbers count
    wrong_number_count = 0
    for c in filtered_calls:
        outcome = str(c.get("outcome") or "").lower()
        if "wrong" in outcome or outcome == "wrong_number":
            wrong_number_count += 1
            continue
        transcript = c.get("transcript") or []
        for turn in transcript:
            if turn.get("speaker") == "prospect":
                p_text = str(turn.get("text", "")).lower()
                if any(phrase in p_text for phrase in ["wrong number", "not me", "not the person", "no such person", "don't know who that is", "wrong person"]):
                    wrong_number_count += 1
                    break
                    
    connected_durations = [c.get("duration_seconds") or 0.0 for c in filtered_calls if c.get("answered_at") is not None]
    avg_duration = sum(connected_durations) / len(connected_durations) if connected_durations else 0.0
    
    # Cost rollups
    costs = await repo.store.query("call_costs", {})
    filtered_costs = [c for c in costs if is_within_range(c.get("created_at"), from_date, to_date)]
    
    call_id_costs = {}
    for cost in filtered_costs:
        cid = cost.get("call_id")
        est_cost = float(cost.get("estimated_cost") or 0.0)
        call_id_costs[cid] = call_id_costs.get(cid, 0.0) + est_cost
        
    total_cost = sum(call_id_costs.values())
    total_connected_minutes = sum(connected_durations) / 60.0
    
    cost_per_connected_minute = total_cost / total_connected_minutes if total_connected_minutes > 0.0 else 0.0
    cost_per_transfer = total_cost / transfers if transfers > 0 else 0.0
    
    # Qualified transfers
    qualified_transfers = 0
    for c in filtered_calls:
        if c.get("outcome") == "transferred":
            qual = c.get("qualification") or {}
            # Allow fallback checks in lead_profile
            lead_prof = c.get("lead_profile") or {}
            is_qualified = (
                (qual.get("open_to_review") is True or lead_prof.get("open_to_review") is True)
                and (qual.get("age_range_confirmed") is True or lead_prof.get("age_range_confirmed") is True)
                and (qual.get("living_independently") is True or lead_prof.get("living_independently") is True)
                and (qual.get("financial_decision_maker") is True or lead_prof.get("financial_decision_maker") is True)
                and (qual.get("transfer_consent_confirmed") is True or lead_prof.get("transfer_consent_confirmed") is True)
            )
            if is_qualified:
                qualified_transfers += 1
                
    cost_per_qualified_transfer = total_cost / qualified_transfers if qualified_transfers > 0 else 0.0
    
    return {
        "total_calls": total_calls,
        "connected_calls": connected_calls,
        "transfers": transfers,
        "callbacks": callbacks,
        "dnc_count": dnc_count,
        "wrong_number_count": wrong_number_count,
        "average_call_duration": round(avg_duration, 2),
        "cost_per_connected_minute": round(cost_per_connected_minute, 4),
        "cost_per_transfer": round(cost_per_transfer, 4),
        "cost_per_qualified_transfer": round(cost_per_qualified_transfer, 4)
    }


if __name__ == "__main__":
    import asyncio
    import json
    async def main():
        repo = Repository()
        res = await get_platform_overview(repo)
        print(json.dumps(res, indent=2))
    asyncio.run(main())
