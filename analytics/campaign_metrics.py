"""Campaign-level metrics and performance analytics functions."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from storage.repository import Repository
from analytics.platform_metrics import is_within_range


async def get_campaign_analytics(
    repository: Optional[Repository] = None,
    campaign_id: Optional[str] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None
) -> dict:
    """Calculate campaign rates, cost per outcome, and caller ID performance."""
    repo = repository or Repository()
    
    # Query calls, costs, and caller ID records
    calls = await repo.store.query("calls", {})
    costs = await repo.store.query("call_costs", {})
    caller_ids = await repo.store.query("dids", {})
    
    cid_map = {cid.get("phone_number"): cid for cid in caller_ids if cid.get("phone_number")}
    
    # Filter calls and costs within range and campaign
    filtered_calls = []
    for c in calls:
        if campaign_id and c.get("campaign_id") != campaign_id:
            continue
        if is_within_range(c.get("created_at") or c.get("started_at"), from_date, to_date):
            filtered_calls.append(c)
            
    filtered_costs = []
    for cost in costs:
        if campaign_id and cost.get("campaign_id") != campaign_id:
            continue
        if is_within_range(cost.get("created_at"), from_date, to_date):
            filtered_costs.append(cost)
            
    total_calls = len(filtered_calls)
    
    # Rates calculations
    answered_calls = sum(1 for c in filtered_calls if c.get("answered_at") is not None)
    transfers = sum(1 for c in filtered_calls if c.get("outcome") == "transferred")
    callbacks = sum(1 for c in filtered_calls if c.get("outcome") == "callback")
    dnc_count = sum(1 for c in filtered_calls if c.get("outcome") == "dnc")
    
    answer_rate = answered_calls / total_calls if total_calls > 0 else 0.0
    transfer_rate = transfers / total_calls if total_calls > 0 else 0.0
    callback_rate = callbacks / total_calls if total_calls > 0 else 0.0
    dnc_rate = dnc_count / total_calls if total_calls > 0 else 0.0
    
    # Cost per outcome type
    call_costs_map = {}
    for cost in filtered_costs:
        cid = cost.get("call_id")
        if cid:
            call_costs_map[cid] = call_costs_map.get(cid, 0.0) + float(cost.get("estimated_cost") or 0.0)
            
    outcome_costs: dict[str, float] = {}
    outcome_counts: dict[str, int] = {}
    for c in filtered_calls:
        cid = c.get("call_id")
        outcome = c.get("outcome") or "unknown"
        cost_val = call_costs_map.get(cid, 0.0)
        
        outcome_costs[outcome] = outcome_costs.get(outcome, 0.0) + cost_val
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
        
    cost_per_outcome = {}
    for outcome, total_cost in outcome_costs.items():
        cnt = outcome_counts[outcome]
        cost_per_outcome[outcome] = round(total_cost / cnt, 4) if cnt > 0 else 0.0
        
    # Caller ID performance rollup
    caller_id_stats: dict[str, dict[str, int]] = {}
    for c in filtered_calls:
        cid = c.get("caller_id")
        if not cid:
            continue
        stats = caller_id_stats.setdefault(cid, {"total": 0, "answered": 0, "dnc": 0})
        stats["total"] += 1
        if c.get("answered_at") is not None:
            stats["answered"] += 1
        if c.get("outcome") == "dnc":
            stats["dnc"] += 1
            
    caller_id_performance = {}
    for cid, stats in caller_id_stats.items():
        total = stats["total"]
        ans = stats["answered"]
        dnc = stats["dnc"]
        
        attestation = "unknown"
        if cid in cid_map:
            attestation = cid_map[cid].get("stir_shaken_attestation") or "unknown"
            
        caller_id_performance[cid] = {
            "total_calls": total,
            "answer_rate": round(ans / total, 4) if total > 0 else 0.0,
            "dnc_rate": round(dnc / total, 4) if total > 0 else 0.0,
            "stir_shaken_status": attestation
        }
        
    return {
        "total_calls": total_calls,
        "answer_rate": round(answer_rate, 4),
        "transfer_rate": round(transfer_rate, 4),
        "callback_rate": round(callback_rate, 4),
        "dnc_rate": round(dnc_rate, 4),
        "cost_per_outcome": cost_per_outcome,
        "caller_id_performance": caller_id_performance
    }
