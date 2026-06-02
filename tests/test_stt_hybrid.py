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
from speech.hybrid_stt_router import HybridSTTRouter, get_speech_health_report, _local_failures
from livekit.agents import utils
from livekit.agents.stt import SpeechEvent, SpeechEventType, SpeechData


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
    from speech.local_stt_load import (
        increment_active_local_stt_tasks,
        decrement_active_local_stt_tasks,
        get_active_local_stt_tasks,
    )
    
    # Reset first
    while get_active_local_stt_tasks() > 0:
        decrement_active_local_stt_tasks()

    increment_active_local_stt_tasks()  # 1 active task
    assert router.select_provider("test-call-1") == "local"

    # 2. Under overload load (active tasks >= max limit) -> deepgram
    increment_active_local_stt_tasks()  # 2 active tasks
    assert router.select_provider("test-call-1") == "deepgram"
    assert "concurrency_overload" in router_module._last_decision_reason["test-call-1"]

    # Reset tasks
    decrement_active_local_stt_tasks()
    decrement_active_local_stt_tasks()

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


def test_hybrid_stt_no_midstream_provider_swap() -> None:
    """Verify that pushing a frame that fails does not swap provider to Deepgram mid-stream."""
    config = VoiceConfig(stt_routing_mode="hybrid", cloud_stt_on_failure=True)
    local_stt = LocallyHostedSTT(STTConfig())
    router = HybridSTTRouter(config, local_stt)
    
    # Mock Deepgram availability
    router._deepgram_stt = "mock-deepgram-stt"
    
    stream = router.stream()
    # Mock local stream to fail on push_frame
    class FailingStream:
        async def push_frame(self, frame):
            raise RuntimeError("Local push failed")
        async def aclose(self, wait=True):
            pass
            
    stream.active_stream = FailingStream()
    
    # Push frame should fail, but should not hot-swap to deepgram stream or push to deepgram inside the call
    frame = rtc.AudioFrame(data=b"\x00" * 320, sample_rate=16000, num_channels=1, samples_per_channel=160)
    with pytest.raises(RuntimeError, match="Local push failed"):
        asyncio.run(stream.push_frame(frame))
        
    assert stream.provider == "local"  # Did not swap provider mid-stream!
    assert _local_failures.get(stream.call_id, 0) == 1
    assert stream._closed is True


def test_local_task_counter_transcription_jobs() -> None:
    """Verify local task counter behaves correctly during transcription jobs and pushes."""
    from speech.local_stt_load import get_active_local_stt_tasks, increment_active_local_stt_tasks, decrement_active_local_stt_tasks
    
    # Reset count
    while get_active_local_stt_tasks() > 0:
        decrement_active_local_stt_tasks()
        
    config = VoiceConfig(stt_routing_mode="hybrid", local_stt_max_concurrent_tasks=2)
    local_stt = LocallyHostedSTT(STTConfig())
    router = HybridSTTRouter(config, local_stt)
    router._deepgram_stt = "mock-deepgram-stt"
    
    stream = router.stream()
    # Mock push_frame to succeed
    class FakeStream:
        async def push_frame(self, frame):
            pass
            
    stream.active_stream = FakeStream()
    
    # 1. Pushing frames should NOT increase active transcription count
    frame = rtc.AudioFrame(data=b"\x00" * 320, sample_rate=16000, num_channels=1, samples_per_channel=160)
    asyncio.run(stream.push_frame(frame))
    assert get_active_local_stt_tasks() == 0
    
    # 2. Recognize_impl using local STT should wrap task counter
    # Mock local recognize_impl
    class FakeLocalSTT:
        async def _recognize_impl(self, buffer, language=None):
            assert get_active_local_stt_tasks() == 1
            return SpeechEvent(type=SpeechEventType.FINAL_TRANSCRIPT, alternatives=[SpeechData(text="test", language="en")])
            
    router.local_stt = FakeLocalSTT()
    asyncio.run(router._recognize_impl(utils.AudioBuffer([])))
    assert get_active_local_stt_tasks() == 0


def test_stt_cloud_fails_if_not_configured() -> None:
    """Verify that cloud mode fails clearly if Deepgram is not configured."""
    config = VoiceConfig(stt_routing_mode="cloud")
    local_stt = LocallyHostedSTT(STTConfig())
    
    # Remove credentials and plugin
    orig_key = os.environ.pop("DEEPGRAM_API_KEY", None)
    try:
        # Initializing stream should fail since provider is cloud and not configured
        with pytest.raises(RuntimeError, match="provider not configured"):
            router = HybridSTTRouter(config, local_stt)
            router.stream()
    finally:
        if orig_key:
            os.environ["DEEPGRAM_API_KEY"] = orig_key


def test_warm_bridge_no_room_disconnect() -> None:
    """Verify that warm bridge success does not call room.disconnect(), but terminal outcomes do."""
    from main import DanaAgent
    from unittest.mock import MagicMock, AsyncMock
    
    agent = DanaAgent(shared=MagicMock(), latency_recorder=MagicMock())
    agent.room = MagicMock()
    agent.room.is_connected.return_value = True
    agent.room.disconnect = AsyncMock()
    agent.session = AsyncMock()
    
    # 1. Successful warm bridge
    agent.warm_bridge_active = False
    agent.should_disconnect = False
    
    # Mock result and events
    from core.runtime_events import ToolTriggeredEvent
    event = ToolTriggeredEvent(
        call_id="test-call-id",
        tool_name="feTransfer",
        success=True,
        result_message="warm bridge success"
    )
            
    class FakeResult:
        def __init__(self):
            self.should_end_call = True
            self.agent_response = "Hold on."
            self.stage = "TRANSFER_READY"
            
    agent.adapter = MagicMock()
    agent.adapter.runtime.events = [event]
    agent.adapter.process_user_turn = AsyncMock()
    agent.adapter.process_user_turn.return_value = FakeResult()
    agent.adapter.convert_response_to_stream = MagicMock()
    async def fake_stream(response):
        yield response
    agent.adapter.convert_response_to_stream.return_value = fake_stream("Hold on.")
    
    # Run the stream collection to trigger the disconnect evaluation block
    async def run_llm_node():
        async for chunk in agent.llm_node(MagicMock(), [], MagicMock()):
            pass
            
    asyncio.run(run_llm_node())
    
    # Warm bridge should set flags but NOT trigger room disconnect immediately
    assert agent.should_disconnect is False
    assert agent.warm_bridge_active is True
    agent.room.disconnect.assert_not_called()
    
    # 2. Terminal DNC outcome
    agent.warm_bridge_active = False
    agent.should_disconnect = False
    
    # Result DNC
    class DNCResult:
        def __init__(self):
            self.should_end_call = True
            self.agent_response = "Goodbye."
            self.stage = "DNC"
            
    agent.adapter.runtime.events = []
    
    # Mock chat_fn or process_user_turn
    agent.adapter.process_user_turn = AsyncMock()
    agent.adapter.process_user_turn.return_value = DNCResult()
    agent.adapter.convert_response_to_stream.return_value = fake_stream("Goodbye.")
    
    asyncio.run(run_llm_node())
    
    # DNC should set should_disconnect = True
    assert agent.should_disconnect is True
    assert agent.warm_bridge_active is False


def test_runtime_finalization_audits() -> None:
    """Verify that overridden callback offer responses are audited and fallbacks are applied on failure."""
    from core.agent_runtime import AgentRuntime
    from unittest.mock import MagicMock
    
    # Create runtime instance
    runtime = AgentRuntime(
        prompt_loader=MagicMock(),
        state_machine=MagicMock(),
        objection_classifier=MagicMock(),
        objection_policy=MagicMock(),
        context_builder=MagicMock(),
        action_policy=MagicMock(),
        tool_registry=MagicMock(),
        compliance_filter=MagicMock(),
        output_validator=MagicMock(),
        call_stop_policy=MagicMock(),
        pii_redactor=MagicMock(),
        repository=MagicMock(),
    )
    
    # Mock compliance filter, output validator, and spoken output auditor
    runtime.compliance_filter = MagicMock()
    runtime.output_validator = MagicMock()
    runtime.spoken_output_auditor = MagicMock()
    
    # 1. Compliant text should pass finalization audit
    runtime.compliance_filter.check.return_value.is_safe = True
    runtime.output_validator.validate.return_value.is_valid = True
    runtime.spoken_output_auditor.audit.return_value = []
    
    text = "Looks like I couldn't get them on the line. Let's schedule a callback instead."
    finalized, compliant = runtime._finalize_spoken_response(text, "callback")
    assert finalized == text
    assert compliant is True
    
    # 2. Non-compliant text (e.g. contains markdown or corporate phrases) should trigger stage fallback
    runtime.compliance_filter.check.return_value.is_safe = False
    finalized, compliant = runtime._finalize_spoken_response(text, "callback")
    assert finalized == "No problem. Would later today or tomorrow be better?"
    assert compliant is False


def test_local_stt_stream_rolling_and_early_emit() -> None:
    """Verify that LocalSTTStream handles rolling buffer writes and detects early emit tokens."""
    from unittest.mock import MagicMock, AsyncMock
    
    # 1. Setup mock LocallyHostedSTT instance
    stt_mock = MagicMock()
    stt_mock.config.sample_rate = 16000
    stt_mock.config.max_speech_duration_s = 30.0
    stt_mock.config.language = "en"
    stt_mock.config.beam_size = 1
    stt_mock.config.vad_filter = True
    stt_mock.config.min_speech_duration_ms = 250
    stt_mock.config.vad_threshold = 0.5
    stt_mock.config.min_silence_ms = 300
    
    stt_mock._ensure_initialized = AsyncMock()
    
    # Mock VAD
    vad_mock = MagicMock()
    vad_mock.is_speech.return_value = True
    stt_mock._vad = vad_mock
    
    # 2. Instantiate stream
    from stt_service import LocalSTTStream
    stream = LocalSTTStream(stt_mock)
    
    # Assert rolling/inference buffer initialized
    assert stream._rolling_buffer is not None
    assert len(stream._rolling_buffer) == 480000
    assert stream._write_cursor == 0
    
    # 3. Simulate push_frame with speech data (30ms frame at 16kHz = 480 samples = 960 bytes)
    frame_data = b"\x00" * 960
    frame = rtc.AudioFrame(data=frame_data, sample_rate=16000, num_channels=1, samples_per_channel=480)
    
    asyncio.run(stream.push_frame(frame))
    
    # Assert write cursor incremented
    assert stream._write_cursor == 480
    assert stream._is_speaking is True
    
    # 4. Test _run_whisper result mapping
    # Mock transcribe call response
    class MockSegment:
        def __init__(self, text, words):
            self.text = text
            self.words = words
            
    class MockWord:
        def __init__(self, word, start, end, probability):
            self.word = word
            self.start = start
            self.end = end
            self.probability = probability
            
    stt_mock._model.transcribe.return_value = (
        [MockSegment("yes", [MockWord("yes", 0.0, 0.3, 0.95)])],
        None
    )
    
    results = stream._run_whisper(stream._inference_buffer)
    assert len(results) == 1
    assert results[0]["text"] == "yes"
    assert len(results[0]["words"]) == 1
    assert results[0]["words"][0]["word"] == "yes"
    assert results[0]["words"][0]["probability"] == 0.95

