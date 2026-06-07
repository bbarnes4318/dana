"""Tests for the Dana Command Center Dashboard API routes and readiness status verification."""

from __future__ import annotations

import os
import sys
import json
import pytest
from unittest import mock
from unittest.mock import patch, AsyncMock

from storage.repository import Repository
from ops.web_console import TrainingWebConsoleServer, TrainingWebConsoleConfig


@pytest.fixture
def repo(tmp_path):
    """Return a Repository backed by a tmp_path JsonlStore."""
    return Repository(data_dir=tmp_path)


@pytest.mark.asyncio
async def test_dashboard_api_analytics_endpoints(repo: Repository):
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config, repository=repo)

    # Test GET /api/analytics/platform
    status, data = await server.handle_api("GET", "/api/analytics/platform", None)
    assert status == 200
    assert data["success"] is True
    assert "total_calls" in data["data"]
    assert "connected_calls" in data["data"]
    assert "cost_per_connected_minute" in data["data"]

    # Test GET /api/analytics/latency
    status, data = await server.handle_api("GET", "/api/analytics/latency", None)
    assert status == 200
    assert data["success"] is True
    assert "p50_turn_latency" in data["data"]
    assert "p95_llm_first_token" in data["data"]

    # Test GET /api/analytics/cost
    status, data = await server.handle_api("GET", "/api/analytics/cost", None)
    assert status == 200
    assert data["success"] is True
    assert "total_cost" in data["data"]
    assert "component_costs" in data["data"]

    # Test GET /api/analytics/providers
    status, data = await server.handle_api("GET", "/api/analytics/providers", None)
    assert status == 200
    assert data["success"] is True
    assert "usage_by_component" in data["data"]
    assert "failure_rates" in data["data"]

    # Test GET /api/analytics/safety
    status, data = await server.handle_api("GET", "/api/analytics/safety", None)
    assert status == 200
    assert data["success"] is True
    assert "compliance_hard_fails" in data["data"]
    assert "wrong_number_failures" in data["data"]

    # Test GET /api/analytics/voice-quality
    status, data = await server.handle_api("GET", "/api/analytics/voice-quality", None)
    assert status == 200
    assert data["success"] is True
    assert "bot_likeness_score" in data["data"]
    assert "repetition_score" in data["data"]

    # Test GET /api/analytics/campaigns
    status, data = await server.handle_api("GET", "/api/analytics/campaigns", None)
    assert status == 200
    assert data["success"] is True
    assert "answer_rate" in data["data"]
    assert "caller_id_performance" in data["data"]


@pytest.mark.asyncio
async def test_readiness_status_endpoint_production_ready(repo: Repository):
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config, repository=repo)

    # 1. Test when readiness checks fail (missing environment variables/config)
    with patch("ops.healthcheck.run_healthcheck", AsyncMock(return_value=(False, "Unhealthy"))):
        with patch("ops.readiness.run_readiness_checks", AsyncMock(return_value=(False, {"db": (False, "offline")}))):
            status, data = await server.handle_api("GET", "/api/readiness/status", None)
            assert status == 200
            assert data["success"] is True
            assert data["PRODUCTION_READY"] is False
            assert data["LIVE_TELEPHONY_READY"] is False
            assert "DB: offline" in data["missing_environment_variables"]

    # 2. Test when readiness check passes but healthcheck fails
    with patch("ops.healthcheck.run_healthcheck", AsyncMock(return_value=(False, "Unhealthy"))):
        with patch("ops.readiness.run_readiness_checks", AsyncMock(return_value=(True, {}))):
            status, data = await server.handle_api("GET", "/api/readiness/status", None)
            assert status == 200
            assert data["success"] is True
            assert data["PRODUCTION_READY"] is False
            assert data["LIVE_TELEPHONY_READY"] is False
            assert data["ops_healthcheck"]["ok"] is False

    # 3. Test when all checks pass - PRODUCTION_READY must be True
    with patch("ops.healthcheck.run_healthcheck", AsyncMock(return_value=(True, "Healthy"))):
        with patch("ops.readiness.run_readiness_checks", AsyncMock(return_value=(True, {}))):
            
            # Setup path.exists mock: True only for scorecard
            def mock_exists(path):
                if "platform_scorecard.json" in str(path):
                    return True
                return False
            
            scorecard_json = json.dumps({"passed": True})
            
            with patch("ops.web_console.os.path.exists", side_effect=mock_exists):
                with patch("builtins.open", mock.mock_open(read_data=scorecard_json)):
                    with patch.object(repo, "list_recent_deployment_experiments", AsyncMock(return_value=[{"status": "completed"}])):
                        with patch.object(repo, "list_recent_eval_cases", AsyncMock(return_value=[{"case_id": "case_1"}])):
                            status, data = await server.handle_api("GET", "/api/readiness/status", None)
                            assert status == 200
                            assert data["success"] is True
                            assert data["PRODUCTION_READY"] is True
                            assert data["LIVE_TELEPHONY_READY"] is True
                            assert data["BENCHMARK_READY"] is True
                            assert data["EVAL_READY"] is True
                            assert data["LOCAL_CANARY_READY"] is True
