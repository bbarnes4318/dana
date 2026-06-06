import pytest
from decimal import Decimal
from metrics.cost_per_outcome import calculate_campaign_metrics

def test_calculate_campaign_metrics_formulas():
    # Setup test rollups
    # Let's say:
    # - 2 connected calls: 120s duration, total cost $0.50
    # - 1 transferred call: 60s duration, total cost $0.30
    # - 1 wrong_number call: 10s duration, total cost $0.05
    # - 1 voicemail call: 20s duration, total cost $0.08
    # Total calls = 5. Total cost = $0.93.
    # Connected duration = 120 + 60 = 180s = 3 minutes.
    # Wasted cost = 0.05 + 0.08 = $0.13.
    rollups = {
        "connected": {
            "total_calls": 2,
            "total_duration_seconds": 120.0,
            "total_cost": Decimal("0.50")
        },
        "transferred": {
            "total_calls": 1,
            "total_duration_seconds": 60.0,
            "total_cost": Decimal("0.30")
        },
        "wrong_number": {
            "total_calls": 1,
            "total_duration_seconds": 10.0,
            "total_cost": Decimal("0.05")
        },
        "voicemail": {
            "total_calls": 1,
            "total_duration_seconds": 20.0,
            "total_cost": Decimal("0.08")
        }
    }
    
    metrics = calculate_campaign_metrics(rollups)
    
    assert metrics["total_completed_calls"] == 5
    assert metrics["total_campaign_cost"] == Decimal("0.93")
    
    # Connected outcomes: connected (2) + transferred (1) + wrong_number (1) = 4 calls.
    # Note: voicemail is NOT in connected_outcomes in our code.
    # Connected duration = 120 + 60 + 10 = 190 seconds = 3.1667 minutes.
    # Cost per connected minute = 0.93 / (190 / 60) = 0.93 / 3.1667 = 0.2937
    expected_connected_duration = 190.0
    expected_minutes = expected_connected_duration / 60.0
    assert metrics["total_connected_duration_seconds"] == expected_connected_duration
    assert metrics["cost_per_connected_minute"] == Decimal("0.93") / Decimal(str(expected_minutes))
    
    # Cost per completed call = 0.93 / 5 = 0.186
    assert metrics["cost_per_completed_call"] == Decimal("0.186")
    
    # Cost per transfer (transferred + qualified_transfer) = 1 call. Cost = 0.93 / 1 = 0.93
    assert metrics["cost_per_transfer"] == Decimal("0.93")
    
    # Wasted cost = voicemail (0.08) + wrong_number (0.05) = 0.13
    assert metrics["wasted_cost_voicemail_wrong_number"] == Decimal("0.13")
