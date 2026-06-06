import pytest
from benchmarks.voice_platform_benchmark.score_cost import score_cost

def test_cost_scoring_free_tier():
    res = score_cost(
        duration_seconds=60.0,
        was_transferred=False,
        cost_per_connected_minute=0.0,
        cost_per_qualified_transfer=0.0
    )
    assert res["total_cost"] == 0.0
    assert res["cost_score"] == 100.0

def test_cost_scoring_standard():
    # 2 minutes connection time at $0.05/min, and transfer cost is 0
    res = score_cost(
        duration_seconds=120.0,
        was_transferred=True,
        cost_per_connected_minute=0.05,
        cost_per_qualified_transfer=0.0,
        budget_limit=1.00
    )
    # connection cost = 2 * 0.05 = 0.10
    # total cost = 0.10
    # score = 100 * (1 - 0.1/1) = 90
    assert res["total_cost"] == 0.10
    assert res["cost_score"] == 90.0

def test_cost_scoring_exceeds_budget():
    # Connection cost exceeds budget limits
    res = score_cost(
        duration_seconds=60.0,
        was_transferred=False,
        cost_per_connected_minute=2.0,
        cost_per_qualified_transfer=0.0,
        budget_limit=1.00
    )
    assert res["total_cost"] == 2.0
    assert res["cost_score"] == 0.0
