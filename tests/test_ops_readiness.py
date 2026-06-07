"""Tests for ops/readiness.py checks."""

import os
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from ops.readiness import (
    check_livekit,
    check_stt,
    check_llm,
    check_tts,
    check_vad,
    check_storage,
    run_readiness_checks,
)


@pytest.mark.asyncio
async def test_check_livekit_missing_creds():
    with patch.dict(os.environ, {"LIVEKIT_URL": "", "LIVEKIT_API_KEY": "", "LIVEKIT_API_SECRET": ""}):
        ok, msg = await check_livekit()
        assert ok is False
        assert "not configured" in msg


@pytest.mark.asyncio
async def test_check_livekit_present():
    with patch.dict(os.environ, {"LIVEKIT_URL": "wss://test.livekit.cloud", "LIVEKIT_API_KEY": "mykey", "LIVEKIT_API_SECRET": "mysec"}):
        ok, msg = await check_livekit()
        assert ok is True
        assert "configured" in msg


@pytest.mark.asyncio
async def test_check_stt_available():
    ok, msg = await check_stt()
    assert ok is True
    assert "available" in msg


@pytest.mark.asyncio
async def test_check_llm_test_mode():
    with patch.dict(os.environ, {"DANA_RUNTIME_ENV": "test"}):
        ok, msg = await check_llm()
        assert ok is True
        assert "mocked" in msg


@pytest.mark.asyncio
async def test_check_tts_allow_mock_production_fails():
    with patch("ops.readiness.is_production", return_value=True), \
         patch("ops.readiness.allow_mock_tts", return_value=True):
        ok, msg = await check_tts()
        assert ok is False
        assert "Mock TTS is enabled in production" in msg


@pytest.mark.asyncio
async def test_check_storage_test_mode():
    with patch.dict(os.environ, {"DANA_RUNTIME_ENV": "test"}):
        ok, msg = await check_storage()
        assert ok is True
        assert "mocked" in msg


@pytest.mark.asyncio
async def test_run_readiness_checks_aggregate():
    with patch("ops.readiness.check_livekit", return_value=(True, "ok")), \
         patch("ops.readiness.check_telephony", return_value=(True, "ok")), \
         patch("ops.readiness.check_stt", return_value=(True, "ok")), \
         patch("ops.readiness.check_llm", return_value=(True, "ok")), \
         patch("ops.readiness.check_tts", return_value=(True, "ok")), \
         patch("ops.readiness.check_vad", return_value=(True, "ok")), \
         patch("ops.readiness.check_storage", return_value=(True, "ok")):
        
        success, results = await run_readiness_checks()
        assert success is True
        assert len(results) == 7
        assert all(val[0] for val in results.values())
