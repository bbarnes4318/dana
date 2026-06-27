import pytest
import os
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from metrics.rate_card import get_rate, get_llm_rates, is_production

@pytest.mark.asyncio
async def test_get_rate_from_db():
    # Setup mock repository with mock rate cards
    repository = MagicMock()
    mock_card = {
        "id": "1",
        "provider": "telnyx",
        "component": "telephony",
        "unit_rate": 0.02,
        "usage_unit": "minute"
    }
    repository.query_cost_rate_cards = AsyncMock(return_value=[mock_card])
    
    rate, unit, src, est = await get_rate(repository, "telnyx", "telephony")
    assert rate == Decimal("0.02")
    assert unit == "minute"
    assert src == "database_rate_card"
    assert not est

@pytest.mark.asyncio
async def test_get_rate_from_env_fallback():
    repository = MagicMock()
    repository.query_cost_rate_cards = AsyncMock(return_value=[])
    
    # Test telephony env override
    with patch.dict(os.environ, {"DANA_COST_TELEPHONY_RATE_PER_MINUTE": "0.015"}):
        rate, unit, src, est = await get_rate(repository, "telnyx", "telephony")
        assert rate == Decimal("0.015")
        assert unit == "minute"
        assert src == "env_override"
        assert not est

@pytest.mark.asyncio
async def test_get_rate_defaults():
    repository = MagicMock()
    repository.query_cost_rate_cards = AsyncMock(return_value=[])
    
    with patch.dict(os.environ, {}, clear=True):
        # Default telephony rate
        rate, unit, src, est = await get_rate(repository, "telnyx", "telephony")
        assert rate == Decimal("0.01")
        assert unit == "minute"
        
        # Default GPU rate
        rate, unit, src, est = await get_rate(repository, "local", "gpu")
        assert rate == Decimal("0.50")
        assert unit == "hour"

@pytest.mark.asyncio
async def test_get_llm_rates():
    repository = MagicMock()
    repository.query_cost_rate_cards = AsyncMock(return_value=[])
    
    # Default cloud model rate (gpt-4o-mini)
    prompt, completion, src, est = await get_llm_rates(repository, "openai", "gpt-4o-mini")
    assert prompt == Decimal("0.00000015")
    assert completion == Decimal("0.00000060")
    assert src in ("default_rate", "provider_costs_yaml")
    assert not est
