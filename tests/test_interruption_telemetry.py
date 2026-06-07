"""Unit tests for interruption telemetry, analytics, config, profiles, and dashboard visibility."""

from __future__ import annotations

import time
import pytest
from datetime import datetime, timezone

from voice_config import VoiceConfig
from latency_metrics import LatencyRecorder
from analytics.latency_rollups import get_latency_metrics
from storage.repository import Repository
from ops.web_console import TrainingWebConsoleServer, TrainingWebConsoleConfig
from core.call_state import CallStage

from speech.interruption_profiles import (
    CONSERVATIVE_DEFAULT,
    OPENING_FAST,
    NORMAL,
    OBJECTION_PATIENT,
    TRANSFER_CONSENT_STRICT,
    DNC_IMMEDIATE,
    WRONG_NUMBER_IMMEDIATE,
    PROFILES,
    get_profile_for_stage
)


@pytest.fixture
def repo(tmp_path):
    """Return a Repository backed by a tmp_path JsonlStore."""
    return Repository(data_dir=tmp_path)


def test_interruption_profiles_exist_and_correct_attributes():
    """Verify that all required interruption profiles exist and have the correct attributes."""
    expected_profiles = [
        "CONSERVATIVE_DEFAULT",
        "OPENING_FAST",
        "NORMAL",
        "OBJECTION_PATIENT",
        "TRANSFER_CONSENT_STRICT",
        "DNC_IMMEDIATE",
        "WRONG_NUMBER_IMMEDIATE"
    ]
    
    for name in expected_profiles:
        assert name in PROFILES
        profile = PROFILES[name]
        assert profile.name == name
        assert isinstance(profile.min_silence_duration, float)
        assert isinstance(profile.min_speech_duration, float)
        assert isinstance(profile.activation_threshold, float)
        assert isinstance(profile.deactivation_threshold, float)
        assert isinstance(profile.interruption_speech_threshold, float)

    # Check specific profiles specified in user requirements
    assert DNC_IMMEDIATE.interruption_speech_threshold == 0.05
    assert WRONG_NUMBER_IMMEDIATE.interruption_speech_threshold == 0.05
    assert TRANSFER_CONSENT_STRICT.interruption_speech_threshold == 0.18
    assert CONSERVATIVE_DEFAULT.interruption_speech_threshold == 0.12


def test_fast_interruption_is_disabled_by_default():
    """Verify config flags default to disabled fast interruption and conservative default profile."""
    config = VoiceConfig()
    
    # Assert defaults match requested configuration settings
    assert config.enable_fast_interruption is False
    assert config.interruption_profile == "CONSERVATIVE_DEFAULT"
    assert config.record_interruption_telemetry is True


def test_default_profile_is_conservative():
    """Verify that when DANA_ENABLE_FAST_INTERRUPTION=false, we keep conservative behavior."""
    class MockConfig:
        enable_fast_interruption = False
        interruption_profile = "CONSERVATIVE_DEFAULT"

    config = MockConfig()
    
    # Even if we pass different call stages, they must resolve to CONSERVATIVE_DEFAULT
    assert get_profile_for_stage(CallStage.OPENING, config) == CONSERVATIVE_DEFAULT
    assert get_profile_for_stage(CallStage.TRANSFER_CONSENT, config) == CONSERVATIVE_DEFAULT
    assert get_profile_for_stage(CallStage.DNC, config) == CONSERVATIVE_DEFAULT
    assert get_profile_for_stage(CallStage.DISQUALIFIED, config) == CONSERVATIVE_DEFAULT
    assert get_profile_for_stage(None, config) == CONSERVATIVE_DEFAULT


def test_profile_stage_mapping_when_fast_interruption_enabled():
    """Verify proper profile selection by call stage when DANA_ENABLE_FAST_INTERRUPTION=true."""
    class MockConfig:
        enable_fast_interruption = True
        interruption_profile = "CONSERVATIVE_DEFAULT"

    config = MockConfig()
    
    # Correct stage mappings
    assert get_profile_for_stage(CallStage.OPENING, config) == OPENING_FAST
    assert get_profile_for_stage(CallStage.ANSWERED, config) == OPENING_FAST
    assert get_profile_for_stage(CallStage.TRANSFER_CONSENT, config) == TRANSFER_CONSENT_STRICT
    assert get_profile_for_stage(CallStage.DNC, config) == DNC_IMMEDIATE
    assert get_profile_for_stage(CallStage.DISQUALIFIED, config) == WRONG_NUMBER_IMMEDIATE
    assert get_profile_for_stage(CallStage.AGE_RANGE, config) == NORMAL
    assert get_profile_for_stage(CallStage.LIVING_SITUATION, config) == NORMAL
    
    # None or unknown stages should return the default profile
    assert get_profile_for_stage(None, config) == CONSERVATIVE_DEFAULT
    assert get_profile_for_stage("UNKNOWN_STAGE", config) == CONSERVATIVE_DEFAULT


def test_interruption_metrics_are_recorded_and_durations_computed():
    """Verify detailed interruption latency events are recorded and durations are calculated."""
    recorder = LatencyRecorder("test-interruption-call")
    
    # Mocking sequential events
    recorder.events["barge_in_detected"] = 10.0
    recorder.events["session_interrupt_called"] = 10.05
    recorder.events["session_interrupt_completed"] = 10.07
    recorder.events["tts_cancel_requested"] = 10.08
    recorder.events["tts_cancel_completed"] = 10.12
    recorder.events["audio_output_flush_requested"] = 10.13
    recorder.events["audio_output_flush_completed"] = 10.15
    recorder.events["agent_audio_stopped"] = 10.20
    
    recorder.events["false_interruption_detected"] = 10.25
    recorder.events["user_speech_resumed"] = 10.30
    
    # Increment counters directly like marks do internally
    recorder.total_barge_ins = 1
    recorder.false_interruption_count = 1
    
    summary = recorder.to_dict()
    durations = summary["durations"]
    
    # Verify exact calculations in milliseconds
    # total_barge_in_stop_ms: barge_in_detected -> agent_audio_stopped (10.20 - 10.00 = 200ms)
    assert durations["total_barge_in_stop_ms"] == 200.0
    
    # barge_in_detected_to_interrupt_call_ms: barge_in_detected -> session_interrupt_called (10.05 - 10.00 = 50ms)
    assert durations["barge_in_detected_to_interrupt_call_ms"] == 50.0
    
    # interrupt_call_to_audio_stopped_ms: session_interrupt_called -> agent_audio_stopped (10.20 - 10.05 = 150ms)
    assert durations["interrupt_call_to_audio_stopped_ms"] == 150.0
    
    # tts_cancel_duration_ms: tts_cancel_requested -> tts_cancel_completed (10.12 - 10.08 = 40ms)
    assert durations["tts_cancel_duration_ms"] == 40.0
    
    # audio_flush_duration_ms: audio_output_flush_requested -> audio_output_flush_completed (10.15 - 10.13 = 20ms)
    assert durations["audio_flush_duration_ms"] == 20.0
    
    # false_interruption_rate: 1 count / 1 total = 1.0 (100%)
    assert summary["false_interruption_rate"] == 1.0
    assert summary["false_interruption_count"] == 1
    assert summary["total_barge_ins"] == 1


@pytest.mark.asyncio
async def test_dashboard_analytics_include_interruption_metrics(repo: Repository):
    """Verify that the rollups calculate P50/P95 for interruption metrics and expose them via dashboard API."""
    
    # Save mock metrics into the repository (e.g. total_barge_in_stop_ms, tts_cancel_duration_ms, etc.)
    # We will save multiple entries to verify percentile calculations
    for val in [100.0, 200.0, 300.0, 400.0, 500.0]:
        await repo.save_latency_metric("call-1", "total_barge_in_stop_ms", val)
        await repo.save_latency_metric("call-1", "tts_cancel_duration_ms", val / 2.0)
        await repo.save_latency_metric("call-1", "audio_flush_duration_ms", val / 4.0)
        
        # Save stage-specific metrics
        await repo.save_latency_metric("call-1", "total_barge_in_stop_ms_stage_OPENING", val)
        await repo.save_latency_metric("call-1", "total_barge_in_stop_ms_stage_TRANSFER_CONSENT", val * 1.5)

    # Save false interruption stats
    await repo.save_latency_metric("call-1", "false_interruption_count", 2.0)
    await repo.save_latency_metric("call-1", "false_interruption_rate", 0.4)
    
    # Retrieve metrics via the rollup service
    metrics = await get_latency_metrics(repo)
    
    # Assert calculations
    assert metrics["p50_total_barge_in_stop_ms"] == 300.0
    assert metrics["p95_total_barge_in_stop_ms"] == 480.0
    
    assert metrics["p50_tts_cancel_duration_ms"] == 150.0
    assert metrics["p95_tts_cancel_duration_ms"] == 240.0
    
    assert metrics["p50_audio_flush_duration_ms"] == 75.0
    assert metrics["p95_audio_flush_duration_ms"] == 120.0
    
    assert metrics["false_interruption_count"] == 2
    assert metrics["false_interruption_rate"] == 0.4
    
    # Assert stage-wise breakdowns
    assert "OPENING" in metrics["interruption_latency_by_stage"]
    assert "TRANSFER_CONSENT" in metrics["interruption_latency_by_stage"]
    assert metrics["interruption_latency_by_stage"]["OPENING"]["p50"] == 300.0
    assert metrics["interruption_latency_by_stage"]["TRANSFER_CONSENT"]["p50"] == 450.0

    # Verify endpoint GET /api/analytics/latency includes the metrics
    config = TrainingWebConsoleConfig()
    server = TrainingWebConsoleServer(config, repository=repo)
    status, data = await server.handle_api("GET", "/api/analytics/latency", None)
    
    assert status == 200
    assert data["success"] is True
    res_data = data["data"]
    
    assert "p50_total_barge_in_stop_ms" in res_data
    assert "p95_total_barge_in_stop_ms" in res_data
    assert "p50_tts_cancel_duration_ms" in res_data
    assert "p95_tts_cancel_duration_ms" in res_data
    assert "p50_audio_flush_duration_ms" in res_data
    assert "p95_audio_flush_duration_ms" in res_data
    assert "false_interruption_count" in res_data
    assert "false_interruption_rate" in res_data
    assert "interruption_latency_by_stage" in res_data
