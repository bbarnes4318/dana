import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from metrics.gpu_cost_allocator import allocate_gpu_cost

@pytest.mark.asyncio
async def test_allocate_gpu_cost_calculation():
    repository = MagicMock()
    repository.save_gpu_runtime_allocation = AsyncMock(return_value="alloc_id")
    
    # Mock get_rate to return $0.60 hourly rate
    async def mock_get_rate(*args, **kwargs):
        return Decimal("0.60"), "hour", "mock_source", False
        
    with patch("metrics.gpu_cost_allocator.get_rate", side_effect=mock_get_rate):
        # 10 minutes (600 seconds) on a $0.60/hr GPU should cost:
        # (600 / 3600) * 0.60 = (1/6) * 0.60 = 0.10
        allocated = await allocate_gpu_cost(
            repository=repository,
            call_id="call-123",
            component="llm",
            runtime_seconds=600.0,
            gpu_device_id="gpu-0"
        )
        assert allocated == Decimal("0.10")
        
        repository.save_gpu_runtime_allocation.assert_called_once_with(
            call_id="call-123",
            component="llm",
            gpu_device_id="gpu-0",
            runtime_seconds=600.0,
            hourly_rate=Decimal("0.60"),
            allocated_cost=Decimal("0.10")
        )

@pytest.mark.asyncio
async def test_allocate_gpu_cost_zero_runtime():
    repository = MagicMock()
    allocated = await allocate_gpu_cost(
        repository=repository,
        call_id="call-123",
        component="llm",
        runtime_seconds=0.0
    )
    assert allocated == Decimal("0.0")
    repository.save_gpu_runtime_allocation.assert_not_called()
