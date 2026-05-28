"""Tests for the hybrid STT router, phone audio preprocessor, and adaptive endpoint tuner."""

from __future__ import annotations

import asyncio
import os
import time
import logging
import pytest
from typing import AsyncGenerator, Any
import numpy as np
from scipy.signal import butter, lfilter
from livekit import rtc

from voice_config import VoiceConfig
from stt_service import LocallyHostedSTT, STTConfig
from speech.context_registry import (
    register_call,
    unregister_call,
    update_call_stage,
    update_line_quality,
    get_current_call_id,
    get_current_campaign_id,
    get_current_call_stage,
    get_current_line_quality,
)
from speech.phone_audio_preprocessor import PhoneAudioPreprocessor
from speech.endpoint_tuner import get_endpoint_delays, safe_update_endpointing
from speech.hybrid_stt_router import HybridSTTRouter, get_speech_health_report


@pytest.fixture
def clean_registry() -> AsyncGenerator[None, None]:
    """Ensure contextvars are cleared before and after each test."""
    # Setup
    unregister_call("test-call-1")
    unregister_call("test-call-2")
    yield
    # Teardown
    unregister_call("test-call-1")
    unregister_call("test-call-2")


def test_context_registry_isolation(clean_registry: None) -> None:
    """Verify that multiple concurrent call contexts do not leak or cross-contaminate."""
    # Call 1 registration
    register_call("test-call-1", "campaign-1")
    update_call_stage("test-call-1", "INTEREST_CHECK")
    update_line_quality("test-call-1", 0.85)

    assert get_current_call_id() == "test-call-1"
    assert get_current_campaign_id() == "campaign-1"
    assert get_current_call_stage() == "INTEREST_CHECK"
    assert get_current_line_quality() == 0.85

    # Register and verify Call 2 in a separate task context simulated by unregister/register
    # Note: ContextVars are task-local, so they are isolated natively in asyncio tasks.
    # We test the global registry dictionary isolation here.
    register_call("test-call-2", "campaign-2")
    update_call_stage("test-call-2", "AGE_RANGE")
    update_line_quality("test-call-2", 0.45)

    assert get_current_call_id() == "test-call-2"
    assert get_current_campaign_id() == "campaign-2"
    assert get_current_call_stage() == "AGE_RANGE"
    assert get_current_line_quality() == 0.45

    # Check Call 1 still isolated in the registry map
    from speech.context_registry import _registry
    assert _registry["test-call-1"]["campaign_id"] == "campaign-1"
    assert _registry["test-call-1"]["call_stage"] == "INTEREST_CHECK"
    assert _registry["test-call-1"]["line_quality"] == 0.85

    assert _registry["test-call-2"]["campaign_id"] == "campaign-2"
    assert _registry["test-call-2"]["call_stage"] == "AGE_RANGE"
    assert _registry["test-call-2"]["line_quality"] == 0.45

    # Unregister Call 1 and check Call 2 remains intact
    unregister_call("test-call-1")
    assert "test-call-1" not in _registry
    assert "test-call-2" in _registry


def test_phone_audio_preprocessor_resampling() -> None:
    """Test mono conversion, polyphase resampling, DC offset removal, and soft limiting."""
    # Create 8kHz stereo input audio (DC offset 0.1, peak 0.9)
    # Length: 8000 samples = 1 second
    t = np.linspace(0, 1.0, 8000, endpoint=False)
    sig = 0.8 * np.sin(2 * np.pi * 440 * t) + 0.1
    stereo_sig = np.column_stack((sig, sig)).flatten()  # Interleaved stereo

    preprocessor = PhoneAudioPreprocessor(
        enable_mono_conversion=True,
        enable_resampling=True,
        enable_dc_removal=True,
        enable_noise_gate=False,
        enable_pstn_bandpass=False,
        enable_clipping_prevention=True,
    )

    # Process numpy directly
    processed = preprocessor.preprocess_numpy(stereo_sig, sample_rate=8000, num_channels=2)

    # Output should be resampled to 16kHz mono (approx 16000 samples)
    assert len(processed) >= 15000 and len(processed) <= 17000
    # DC offset should be removed (mean close to 0)
    assert np.abs(np.mean(processed)) < 1e-4
    # Peak amplitude should be normalized/limited
    assert np.max(np.abs(processed)) <= 1.0


def test_phone_audio_preprocessor_gentle_noise_gate() -> None:
    """Prove that quiet speech is preserved and not aggressively gated/silenced."""
    # Create a quiet speech sample (amplitude 0.005)
    sig_quiet = 0.005 * np.sin(2 * np.pi * 440 * np.linspace(0, 0.1, 800))  # 100ms at 8kHz
    
    # Gate threshold is 0.010, so quiet signal is below threshold
    preprocessor = PhoneAudioPreprocessor(
        enable_mono_conversion=False,
        enable_resampling=False,
        enable_dc_removal=False,
        enable_noise_gate=True,
        noise_gate_threshold=0.010,
        noise_gate_attenuation=0.5,  # Only attenuate by 50%
    )

    processed = preprocessor.preprocess_numpy(sig_quiet, sample_rate=8000)

    # Quiet signal should be attenuated gently but NOT completely silenced (erased)
    assert np.max(np.abs(processed)) > 0.001
    assert np.max(np.abs(processed)) == pytest.approx(0.0025, abs=1e-4)


def test_phone_audio_preprocessor_pstn_bandpass() -> None:
    """Test Butterworth bandpass filter constraints."""
    # Create white noise
    noise = np.random.uniform(-0.5, 0.5, 16000)

    preprocessor = PhoneAudioPreprocessor(
        enable_mono_conversion=False,
        enable_resampling=False,
        enable_dc_removal=False,
        enable_noise_gate=False,
        enable_pstn_bandpass=True,
        enable_clipping_prevention=False,
    )

    # Filter 16kHz noise
    filtered = preprocessor.preprocess_numpy(noise, sample_rate=16000)

    # Verify output length matches input length
    assert len(filtered) == len(noise)
    # Energy in filtered signal should be less due to cut-off bands
    assert np.var(filtered) < np.var(noise)


def test_phone_audio_preprocessor_line_quality_estimation(clean_registry: None) -> None:
    """Test rolling window quality estimation tracking."""
    register_call("test-call-1", "campaign-1")
    
    # Create a clean signal (quality should start high)
    clean_audio = 0.2 * np.sin(2 * np.pi * 440 * np.linspace(0, 0.1, 1600))
    preprocessor = PhoneAudioPreprocessor(enable_mono_conversion=False)

    # Process multiple frames to build rolling window
    silence_audio = np.zeros(1600)
    preprocessor.preprocess_numpy(silence_audio, sample_rate=16000, call_id="test-call-1")
    for _ in range(10):
        preprocessor.preprocess_numpy(clean_audio, sample_rate=16000, call_id="test-call-1")

    # Quality should be close to 1.0 (very low clipping, low noise floor)
    assert get_current_line_quality() > 0.90

    # Create a highly clipping signal (amplitude 1.5)
    clipped_audio = 1.5 * np.sin(2 * np.pi * 440 * np.linspace(0, 0.1, 1600))
    for _ in range(30):
        preprocessor.preprocess_numpy(clipped_audio, sample_rate=16000, call_id="test-call-1")

    # Line quality should drop significantly due to high clipping
    assert get_current_line_quality() < 0.60


def test_hybrid_stt_router_local_integrity() -> None:
    """Verify that local mode never imports or invokes Deepgram classes."""
    config = VoiceConfig(stt_routing_mode="local")
    local_stt = LocallyHostedSTT(STTConfig())
    
    # Temporarily remove API key from env to prove local starts fine
    orig_key = os.environ.pop("DEEPGRAM_API_KEY", None)
    try:
        router = HybridSTTRouter(config, local_stt)
        assert router.select_provider() == "local"
        assert router._deepgram_stt is None
    finally:
        if orig_key:
            os.environ["DEEPGRAM_API_KEY"] = orig_key


def test_hybrid_stt_router_overload_routing(clean_registry: None) -> None:
    """Test that STT router falls back to cloud on local overload or line quality issues."""
    config = VoiceConfig(
        stt_routing_mode="hybrid",
        local_stt_max_concurrent_tasks=2,
        allow_cloud_stt_for_poor_line=True,
    )
    local_stt = LocallyHostedSTT(STTConfig())
    router = HybridSTTRouter(config, local_stt)

    # Mock Deepgram availability
    router._deepgram_stt = "mock-deepgram-stt"

    register_call("test-call-1", "campaign-1")

    # 1. Under normal load -> local
    import speech.hybrid_stt_router as router_module
    router_module._active_local_tasks = 1
    assert router.select_provider("test-call-1") == "local"

    # 2. Under overload load (active tasks >= max limit) -> deepgram
    router_module._active_local_tasks = 2
    assert router.select_provider("test-call-1") == "deepgram"
    assert "concurrency_overload" in router_module._last_decision_reason["test-call-1"]

    # Reset tasks
    router_module._active_local_tasks = 0

    # 3. Poor line quality -> deepgram
    update_line_quality("test-call-1", 0.45)
    assert router.select_provider("test-call-1") == "deepgram"
    assert "poor_line_quality" in router_module._last_decision_reason["test-call-1"]


def test_hybrid_stt_router_campaign_routing(clean_registry: None) -> None:
    """Test premium campaign routing config."""
    config = VoiceConfig(
        stt_routing_mode="hybrid",
        premium_stt_campaigns="camp-vip,camp-premium",
    )
    local_stt = LocallyHostedSTT(STTConfig())
    router = HybridSTTRouter(config, local_stt)
    router._deepgram_stt = "mock-deepgram-stt"

    # Call with non-premium campaign -> local
    register_call("test-call-1", "camp-normal")
    assert router.select_provider("test-call-1") == "local"

    # Call with premium campaign -> deepgram
    register_call("test-call-2", "camp-vip")
    assert router.select_provider("test-call-2") == "deepgram"


def test_hybrid_stt_router_decision_logging(caplog: pytest.LogCaptureFixture) -> None:
    """Assert provider-decision logs contain metadata only, omitting transcript/audio content."""
    config = VoiceConfig(stt_routing_mode="local")
    local_stt = LocallyHostedSTT(STTConfig())
    router = HybridSTTRouter(config, local_stt)

    with caplog.at_level(logging.INFO):
        router.log_decision("local", "forced_config", call_id="test-call-1", campaign_id="camp-1")

    log_text = caplog.text
    # Log must contain call metadata
    assert "[STT ROUTER]" in log_text
    assert "call_id=test-call-1" in log_text
    assert "campaign_id=camp-1" in log_text
    assert "provider_selected=local" in log_text
    assert "reason=forced_config" in log_text
    # Log must NOT contain audio/transcripts/text
    assert "transcript" not in log_text.lower()
    assert "audio" not in log_text.lower()


def test_endpoint_tuner_delays() -> None:
    """Verify stage-correct min/max delays and objection overrides."""
    # yes/no qualification stages (e.g. INTEREST_CHECK, AGE_RANGE)
    min_d, max_d = get_endpoint_delays("interest_check")
    assert min_d == 0.2
    assert max_d == 0.5

    min_d, max_d = get_endpoint_delays("AGE_RANGE")
    assert min_d == 0.2
    assert max_d == 0.5

    # opening stage
    min_d, max_d = get_endpoint_delays("opening")
    assert min_d == 0.6
    assert max_d == 1.5

    # objection/confusion overrides
    min_d, max_d = get_endpoint_delays("interest_check", is_objection_or_confusion=True)
    assert min_d == 0.8
    assert max_d == 2.0


def test_safe_update_endpointing(caplog: pytest.LogCaptureFixture) -> None:
    """Verify that update endpointing does not crash if AgentSession is missing or unsupported."""
    class UnsupportedSession:
        pass

    session = UnsupportedSession()
    with caplog.at_level(logging.WARNING):
        safe_update_endpointing(session, 0.2, 0.5)

    assert "endpoint_update_not_supported" in caplog.text


def test_health_report(clean_registry: None) -> None:
    """Verify that the health report correctly returns config states."""
    register_call("test-call-1", "campaign-1")
    report = get_speech_health_report("test-call-1")

    assert "stt_routing_mode" in report
    assert "selected_provider" in report
    assert "active_local_stt_tasks" in report
    assert "preprocessing_enabled" in report
    assert "endpoint_mode" in report
