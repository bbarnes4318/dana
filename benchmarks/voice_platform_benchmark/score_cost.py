from typing import Dict, Any

def score_cost(
    duration_seconds: float,
    was_transferred: bool,
    cost_per_connected_minute: float,
    cost_per_qualified_transfer: float,
    budget_limit: float = 1.50
) -> Dict[str, Any]:
    """
    Computes total call cost and cost score.
    Returns:
        Dict containing:
            - total_cost: absolute cost in USD
            - cost_score: normalized score out of 100 (higher is better/cheaper)
    """
    connected_minutes = duration_seconds / 60.0
    duration_cost = connected_minutes * cost_per_connected_minute
    transfer_cost = cost_per_qualified_transfer if was_transferred else 0.0
    
    total_cost = duration_cost + transfer_cost
    
    # Cost score is budget-based. A cost of $0.00 is 100. A cost of budget_limit or higher is 0.
    if total_cost >= budget_limit:
        cost_score = 0.0
    else:
        cost_score = 100.0 * (1.0 - (total_cost / budget_limit))
        
    return {
        "total_cost": round(total_cost, 4),
        "cost_score": round(cost_score, 2)
    }
