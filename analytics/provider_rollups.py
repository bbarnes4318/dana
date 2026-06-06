"""Provider performance and routing analytics functions."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from storage.repository import Repository
from analytics.platform_metrics import is_within_range


def is_failure_reason(reason: Optional[str]) -> bool:
    """Check if a provider decision reason indicates a failure or fallback."""
    if not reason:
        return False
    r = reason.lower()
    return any(term in r for term in ["fail", "error", "fallback", "overload", "timeout", "poor", "exception", "broken"])


async def get_provider_performance(
    repository: Optional[Repository] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None
) -> dict:
    """Calculate usage, failure rates, average latencies, and average costs per provider."""
    repo = repository or Repository()
    
    # Query decisions and costs
    decisions = await repo.store.query("provider_decisions", {})
    costs = await repo.store.query("call_costs", {})
    
    # Filter within range
    filtered_decisions = [
        d for d in decisions
        if is_within_range(d.get("created_at"), from_date, to_date)
    ]
    filtered_costs = [
        c for c in costs
        if is_within_range(c.get("created_at"), from_date, to_date)
    ]
    
    # Aggregates
    usage_by_component: dict[str, dict[str, int]] = {}
    provider_attempts: dict[str, int] = {}
    provider_failures: dict[str, int] = {}
    provider_latencies: dict[str, list[float]] = {}
    
    for d in filtered_decisions:
        comp = str(d.get("component") or "").lower()
        provider = str(d.get("selected_provider") or "").lower()
        if not provider or provider == "unknown":
            continue
            
        # Usage count by component
        if comp not in usage_by_component:
            usage_by_component[comp] = {}
        usage_by_component[comp][provider] = usage_by_component[comp].get(provider, 0) + 1
        
        # Attempts and failures
        provider_attempts[provider] = provider_attempts.get(provider, 0) + 1
        reason = d.get("decision_reason")
        if is_failure_reason(reason):
            provider_failures[provider] = provider_failures.get(provider, 0) + 1
            
        # Latencies
        lat = d.get("latency_ms")
        if lat is not None:
            provider_latencies[provider] = provider_latencies.get(provider, []) + [float(lat)]
            
    # Calculate failure rates and average latencies
    failure_rates: dict[str, float] = {}
    average_latencies: dict[str, float] = {}
    
    for provider, attempts in provider_attempts.items():
        fails = provider_failures.get(provider, 0)
        failure_rates[provider] = round(fails / attempts, 4) if attempts > 0 else 0.0
        
        lats = provider_latencies.get(provider, [])
        average_latencies[provider] = round(sum(lats) / len(lats), 2) if lats else 0.0
        
    # Group costs by provider
    provider_costs: dict[str, float] = {}
    provider_cost_counts: dict[str, int] = {}
    
    for c in filtered_costs:
        provider = str(c.get("provider") or "").lower()
        if not provider or provider == "unknown":
            continue
        cost_val = float(c.get("estimated_cost") or 0.0)
        provider_costs[provider] = provider_costs.get(provider, 0.0) + cost_val
        provider_cost_counts[provider] = provider_cost_counts.get(provider, 0) + 1
        
    average_costs: dict[str, float] = {}
    for provider, total_cost in provider_costs.items():
        cnt = provider_cost_counts.get(provider, 0)
        average_costs[provider] = round(total_cost / cnt, 4) if cnt > 0 else 0.0
        
    return {
        "usage_by_component": usage_by_component,
        "failure_rates": failure_rates,
        "average_latencies": average_latencies,
        "average_costs": average_costs
    }
