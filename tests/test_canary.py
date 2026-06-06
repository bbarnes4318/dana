"""Tests for ops/canary.py execution."""

import os
from unittest.mock import patch
import pytest
from ops.canary import run_canary, run_canary_dry_run


@pytest.mark.asyncio
async def test_canary_dry_run_success():
    success = await run_canary_dry_run()
    assert success is True


@pytest.mark.asyncio
async def test_canary_fallback_to_dry_run_in_test_env():
    with patch.dict(os.environ, {"DANA_RUNTIME_ENV": "test"}):
        success = await run_canary()
        assert success is True
