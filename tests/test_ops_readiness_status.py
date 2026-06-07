"""Unit tests for the readiness status determination."""

from __future__ import annotations

import pytest
from ops.readiness import get_readiness_status


def test_get_readiness_status_all_pass():
    # If all checks pass, PRODUCTION_READY must be True
    status = get_readiness_status(
        healthcheck_ok=True,
        readiness_ok=True,
        canary_ok=True,
        evals_ok=True,
        quality_gate_ok=True
    )
    assert status["PRODUCTION_READY"] is True
    assert status["LIVE_TELEPHONY_READY"] is True
    assert status["BENCHMARK_READY"] is True
    assert status["EVAL_READY"] is True
    assert status["LOCAL_CANARY_READY"] is True


def test_get_readiness_status_readiness_fails():
    # If ops.readiness check fails, PRODUCTION_READY must be False
    status = get_readiness_status(
        healthcheck_ok=True,
        readiness_ok=False,  # ops.readiness fails
        canary_ok=True,
        evals_ok=True,
        quality_gate_ok=True
    )
    assert status["PRODUCTION_READY"] is False
    assert status["LIVE_TELEPHONY_READY"] is False


def test_get_readiness_status_healthcheck_fails():
    # If ops.healthcheck fails, PRODUCTION_READY must be False
    status = get_readiness_status(
        healthcheck_ok=False,  # ops.healthcheck fails
        readiness_ok=True,
        canary_ok=True,
        evals_ok=True,
        quality_gate_ok=True
    )
    assert status["PRODUCTION_READY"] is False
    assert status["LIVE_TELEPHONY_READY"] is False


def test_get_readiness_status_other_combinations():
    # Test typical benchmark-ready configuration
    status = get_readiness_status(
        healthcheck_ok=False,  # local offline mode (missing credentials)
        readiness_ok=False,    # local offline mode (missing postgres/vllm)
        canary_ok=True,
        evals_ok=True,
        quality_gate_ok=True
    )
    assert status["BENCHMARK_READY"] is True
    assert status["EVAL_READY"] is True
    assert status["LOCAL_CANARY_READY"] is True
    assert status["LIVE_TELEPHONY_READY"] is False
    assert status["PRODUCTION_READY"] is False


def test_readiness_cli_fails_output(capsys):
    from unittest import mock
    import sys
    from ops.readiness import main
    
    # Mock run_readiness_checks to return success=False
    with mock.patch("ops.readiness.run_readiness_checks", return_value=(False, {
        "livekit": (False, "unconfigured"),
        "stt": (True, "available")
    })):
        with mock.patch("sys.argv", ["readiness.py"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1
            
    captured = capsys.readouterr()
    assert "LIVE_TELEPHONY_READY=false" in captured.out
    assert "PRODUCTION_READY=false" in captured.out
    assert "BENCHMARK_READY=unknown" in captured.out
    assert "EVAL_READY=unknown" in captured.out
    assert "LOCAL_CANARY_READY=unknown" in captured.out

