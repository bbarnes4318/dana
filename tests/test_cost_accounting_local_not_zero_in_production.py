import pytest
import os
import logging
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from metrics.rate_card import get_rate

@pytest.mark.asyncio
async def test_production_mode_missing_local_infra_cost_warning():
    repository = MagicMock()
    repository.query_cost_rate_cards = AsyncMock(return_value=[])

    # We patch ENVIRONMENT=production and ensure local STT infra cost env is missing
    with patch.dict(os.environ, {
        "DANA_ENV": "production",
        "ENVIRONMENT": "production",
        "DANA_COST_LOCAL_STT_INFRA_PER_MINUTE": ""
    }, clear=True), patch("logging.Logger.warning") as mock_warning:
        
        rate, unit, src, est = await get_rate(repository, "local", "stt")
        
        # Verify rate is 0.0, but marked as estimated due to missing env in production
        assert rate == Decimal("0.0")
        assert est is True
        assert src == "default_local_stt_rate"
        
        # Verify a warning was logged
        mock_warning.assert_any_call("Production mode enabled but DANA_COST_LOCAL_STT_INFRA_PER_MINUTE is not set.")
