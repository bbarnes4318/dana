"""Cost rollup and financial analytics functions."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from storage.repository import Repository
from analytics.platform_metrics import is_within_range


async def get_cost_metrics(
    repository: Optional[Repository] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None
) -> dict:
    """Calculate platform total cost, average cost per call, component costs, and campaign costs."""
    repo = repository or Repository()
    
    # Query all calls and costs
    calls = await repo.store.query("calls", {})
    costs = await repo.store.query("call_costs", {})
    
    # Filter calls and costs within date range
    filtered_calls = [
        c for c in calls
        if is_within_range(c.get("created_at") or c.get("started_at"), from_date, to_date)
    ]
    filtered_costs = [
        c for c in costs
        if is_within_range(c.get("created_at"), from_date, to_date)
    ]
    
    total_calls = len(filtered_calls)
    
    # Compute connected minutes
    connected_durations = [
        c.get("duration_seconds") or 0.0
        for c in filtered_calls
        if c.get("answered_at") is not None
    ]
    total_connected_minutes = sum(connected_durations) / 60.0
    
    # Compute total cost
    total_cost = sum(float(c.get("estimated_cost") or 0.0) for c in filtered_costs)
    
    # Group costs by component (telephony, stt, llm, tts, etc.)
    component_costs = {
        "telephony": 0.0,
        "stt": 0.0,
        "llm": 0.0,
        "tts": 0.0
    }
    
    for c in filtered_costs:
        comp = str(c.get("component") or "").lower()
        cost_val = float(c.get("estimated_cost") or 0.0)
        if comp in component_costs:
            component_costs[comp] += cost_val
        else:
            component_costs[comp] = component_costs.get(comp, 0.0) + cost_val
            
    # Group costs by campaign
    campaign_costs = {}
    for c in filtered_costs:
        campaign_id = c.get("campaign_id") or "unknown"
        cost_val = float(c.get("estimated_cost") or 0.0)
        campaign_costs[campaign_id] = campaign_costs.get(campaign_id, 0.0) + cost_val
        
    avg_cost_per_call = total_cost / total_calls if total_calls > 0 else 0.0
    avg_cost_per_connected_minute = total_cost / total_connected_minutes if total_connected_minutes > 0.0 else 0.0
    
    # Round decimal figures for display
    return {
        "total_cost": round(total_cost, 4),
        "average_cost_per_call": round(avg_cost_per_call, 4),
        "average_cost_per_connected_minute": round(avg_cost_per_connected_minute, 4),
        "component_costs": {k: round(v, 4) for k, v in component_costs.items()},
        "campaign_costs": {k: round(v, 4) for k, v in campaign_costs.items()}
    }
