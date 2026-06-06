import os
import logging
from decimal import Decimal
from typing import Optional, Any
from metrics.rate_card import get_rate

logger = logging.getLogger(__name__)

async def allocate_gpu_cost(
    repository: Optional[Any],
    call_id: str,
    component: str,
    runtime_seconds: float,
    gpu_device_id: Optional[str] = None
) -> Decimal:
    """Calculate and persist GPU cost allocation for a call component runtime.
    
    Equation:
        allocated_cost = (runtime_seconds / 3600.0) * DANA_COST_GPU_HOURLY_USD
        
    Returns:
        Decimal: The allocated GPU cost.
    """
    if runtime_seconds <= 0:
        return Decimal("0.0")

    # Resolve GPU hourly rate (defaults to 0.50 if not configured)
    hourly_rate, _, _, _ = await get_rate(repository, provider="local", component="gpu")
    
    # Calculate allocated cost
    allocated_cost = (Decimal(str(runtime_seconds)) / Decimal("3600.0")) * hourly_rate
    
    if repository:
        try:
            await repository.save_gpu_runtime_allocation(
                call_id=call_id,
                component=component,
                gpu_device_id=gpu_device_id,
                runtime_seconds=float(runtime_seconds),
                hourly_rate=hourly_rate,
                allocated_cost=allocated_cost
            )
            logger.info(f"Allocated GPU cost for call {call_id}, component {component}: {allocated_cost} (runtime: {runtime_seconds}s)")
        except Exception as e:
            logger.error(f"Failed to save GPU allocation for call {call_id}: {e}")
            
    return allocated_cost
