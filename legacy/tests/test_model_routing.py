"""Tests for the model/provider routing layer, failover wrappers, and health circuit breakers."""

from __future__ import annotations
import asyncio
import os
import time
import logging
import pytest
from decimal import Decimal
from unittest.mock import MagicMock, AsyncMock

from voice_config import VoiceConfig
from routing.model_router import (
    ModelRouter,
    increment_local_llm_tasks,
    decrement_local_llm_tasks,
    get_active_local_llm_tasks,
    increment_local_tts_tasks,
    decrement_local_tts_tasks,
    get_active_local_tts_tasks,
)
from routing.provider_health import (
    record_failure,
    get_error_count,
    is_on_cooldown,
    check_provider_health,
    cleanup_call,
)
from routing.routed_llm import RoutedLLM
from routing.routed_tts import RoutedTTS
from livekit.agents import llm, tts
from livekit import rtc
from speech.context_registry import register_call, unregister_call, get_current_call_id


@pytest.fixture
def clean_health() -> None:
    """Clean up provider health tracking before and after each test."""
    cleanup_call("test-call-1")
    cleanup_call("test-call-2")
    # Reset concurrency tasks
    while get_active_local_llm_tasks() > 0:
        decrement_local_llm_tasks()
    while get_active_local_tts_tasks() > 0:
        decrement_local_tts_tasks()
    yield
    cleanup_call("test-call-1")
    cleanup_call("test-call-2")


def test_model_router_defaults(clean_health) -> None:
    """Verify that by default, routing selects local STT/LLM/TTS."""
    config = VoiceConfig()
    router = ModelRouter(config)

    assert router.select_provider("stt", "test-call-1") == "local"
    assert router.select_provider("llm", "test-call-1") == "local"
    assert router.select_provider("tts", "test-call-1") == "local"


def test_model_router_fallback_disabled(clean_health, monkeypatch) -> None:
    """Verify that if cloud fallback is disabled, hybrid mode never routes to cloud."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "mock-key")
    monkeypatch.setenv("OPENAI_API_KEY", "mock-key")

    config = VoiceConfig(
        stt_routing_mode="hybrid",
        llm_routing_mode="hybrid",
        tts_routing_mode="hybrid",
        cloud_stt_on_failure=False,
        allow_cloud_llm_fallback=False,
        allow_cloud_tts_fallback=False,
    )
    router = ModelRouter(config)

    # Trigger failure/load which would normally cause fallback
    record_failure("test-call-1", "llm", "local")
    record_failure("test-call-1", "llm", "local")
    record_failure("test-call-1", "llm", "local")
    record_failure("test-call-1", "llm", "local")

    assert router.select_provider("llm", "test-call-1") == "local"
    assert router.select_provider("tts", "test-call-1") == "local"


def test_model_router_overload_fallback(clean_health, monkeypatch) -> None:
    """Verify that overloaded local routes to fallback only if enabled."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "mock-key")
    monkeypatch.setenv("OPENAI_API_KEY", "mock-key")

    # 1. Fallback enabled
    config_enabled = VoiceConfig(
        llm_routing_mode="hybrid",
        allow_cloud_llm_fallback=True,
        max_local_concurrent_calls=2
    )
    router_enabled = ModelRouter(config_enabled)

    increment_local_llm_tasks()
    increment_local_llm_tasks()  # Concurrency = 2 (overloaded)

    assert router_enabled.select_provider("llm", "test-call-1") == "openai"

    # 2. Fallback disabled
    config_disabled = VoiceConfig(
        llm_routing_mode="hybrid",
        allow_cloud_llm_fallback=False,
        max_local_concurrent_calls=2
    )
    router_disabled = ModelRouter(config_disabled)
    assert router_disabled.select_provider("llm", "test-call-1") == "local"


def test_cloud_mode_missing_credentials_fails(clean_health, monkeypatch) -> None:
    """Verify that explicitly selecting cloud mode without credentials returns cloud_unavailable."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    
    config = VoiceConfig(llm_routing_mode="cloud")
    router = ModelRouter(config)

    assert router.select_provider("llm", "test-call-1") == "cloud_unavailable"


def test_model_router_decision_logging_privacy(clean_health, caplog) -> None:
    """Assert routing decisions log metadata only and never leak transcripts, prompts, or audio."""
    config = VoiceConfig()
    router = ModelRouter(config)

    with caplog.at_level(logging.INFO):
        router.log_decision(
            component="llm",
            call_id="test-call-1",
            campaign_id="test-camp-1",
            provider="local",
            reason="normal",
            fallback_allowed=False,
            local_load=1
        )

    log_text = caplog.text
    assert "[MODEL ROUTER]" in log_text
    assert "call_id=test-call-1" in log_text
    assert "campaign_id=test-camp-1" in log_text
    assert "provider_selected=local" in log_text
    assert "reason=normal" in log_text
    assert "local_load=1" in log_text
    # Sensitive raw info must be absent
    assert "transcript" not in log_text.lower()
    assert "prompt" not in log_text.lower()
    assert "audio" not in log_text.lower()


@pytest.mark.asyncio
async def test_routed_llm_happy_path(clean_health) -> None:
    """RoutedLLM returns local LLM stream when healthy and local is successful."""
    config = VoiceConfig()
    router = ModelRouter(config)

    # Mock delegate LLMs
    mock_local_llm = MagicMock(spec=llm.LLM)
    mock_cloud_llm = MagicMock(spec=llm.LLM)

    # Set up mock local LLM chat return stream
    mock_stream = AsyncMock()
    mock_stream.__aiter__.return_value = [
        MagicMock(choices=[MagicMock(delta=MagicMock(content="Hello from local"))])
    ]
    mock_local_llm.chat.return_value = mock_stream

    routed_llm = RoutedLLM(mock_local_llm, mock_cloud_llm, router)
    chat_ctx = llm.ChatContext()
    
    register_call("test-call-1", "camp-1")
    try:
        stream = routed_llm.chat(chat_ctx=chat_ctx)
        chunks = []
        async for chunk in stream:
            chunks.append(chunk)
            
        assert len(chunks) == 1
        assert chunks[0].choices[0].delta.content == "Hello from local"
        assert get_active_local_llm_tasks() == 0  # Concurrency counter decremented correctly
    finally:
        unregister_call("test-call-1")


@pytest.mark.asyncio
async def test_routed_llm_failover_to_cloud(clean_health, monkeypatch) -> None:
    """RoutedLLM fails over to cloud LLM stream when local fails and fallback is allowed."""
    monkeypatch.setenv("OPENAI_API_KEY", "mock-key")
    config = VoiceConfig(llm_routing_mode="hybrid", allow_cloud_llm_fallback=True)
    router = ModelRouter(config)

    mock_local_llm = MagicMock(spec=llm.LLM)
    mock_cloud_llm = MagicMock(spec=llm.LLM)

    # Local LLM fails on chat invocation
    mock_local_llm.chat.side_effect = RuntimeError("Local LLM timeout")

    # Cloud LLM succeeds
    mock_cloud_stream = AsyncMock()
    mock_cloud_stream.__aiter__.return_value = [
        MagicMock(choices=[MagicMock(delta=MagicMock(content="Hello from cloud"))])
    ]
    mock_cloud_llm.chat.return_value = mock_cloud_stream

    routed_llm = RoutedLLM(mock_local_llm, mock_cloud_llm, router)
    chat_ctx = llm.ChatContext()

    register_call("test-call-1", "camp-1")
    try:
        stream = routed_llm.chat(chat_ctx=chat_ctx)
        chunks = []
        async for chunk in stream:
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].choices[0].delta.content == "Hello from cloud"
        # Check that local failure was recorded
        assert get_error_count("test-call-1", "llm", "local") == 1
        # Check that cloud LLM did NOT receive tools (Requirement 6)
        args, kwargs = mock_cloud_llm.chat.call_args
        assert "tools" not in kwargs
    finally:
        unregister_call("test-call-1")


@pytest.mark.asyncio
async def test_routed_llm_local_retries(clean_health) -> None:
    """RoutedLLM retries local LLM when cloud fallback is disabled."""
    config = VoiceConfig(llm_routing_mode="hybrid", allow_cloud_llm_fallback=False, llm_local_max_retries=2)
    router = ModelRouter(config)

    mock_local_llm = MagicMock(spec=llm.LLM)
    mock_cloud_llm = MagicMock(spec=llm.LLM)

    # Local LLM fails twice then succeeds
    mock_stream = AsyncMock()
    mock_stream.__aiter__.return_value = [
        MagicMock(choices=[MagicMock(delta=MagicMock(content="Hello after retry"))])
    ]
    
    call_count = 0
    def chat_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise RuntimeError("Local LLM fail")
        return mock_stream

    mock_local_llm.chat.side_effect = chat_side_effect
    routed_llm = RoutedLLM(mock_local_llm, mock_cloud_llm, router)
    chat_ctx = llm.ChatContext()

    register_call("test-call-1", "camp-1")
    try:
        stream = routed_llm.chat(chat_ctx=chat_ctx)
        chunks = []
        async for chunk in stream:
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].choices[0].delta.content == "Hello after retry"
        assert call_count == 3  # Try 1, Retry 1, Retry 2 (Success)
        assert get_error_count("test-call-1", "llm", "local") == 2
    finally:
        unregister_call("test-call-1")


@pytest.mark.asyncio
async def test_routed_tts_failover(clean_health, monkeypatch) -> None:
    """RoutedTTS fails over to cloud TTS stream when local fails and fallback is allowed."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "mock-key")
    config = VoiceConfig(tts_routing_mode="hybrid", allow_cloud_tts_fallback=True)
    router = ModelRouter(config)

    mock_local_tts = MagicMock(spec=tts.TTS)
    mock_local_tts.sample_rate = 24000
    mock_cloud_tts = MagicMock(spec=tts.TTS)

    # Local stream creation fails
    mock_local_tts.stream.side_effect = RuntimeError("Local TTS initialization failed")

    # Cloud stream creation succeeds
    mock_cloud_stream = MagicMock(spec=tts.SynthesizeStream)
    mock_cloud_stream.sample_rate = 24000
    mock_cloud_stream.num_channels = 1
    
    # Mock stream iterator yielding synthesized audio chunks
    async def mock_iter(*args, **kwargs):
        frame = rtc.AudioFrame(data=b"\x00" * 320, sample_rate=24000, num_channels=1, samples_per_channel=160)
        yield frame
    mock_cloud_stream.__aiter__ = mock_iter

    mock_cloud_tts.stream.return_value = mock_cloud_stream

    routed_tts = RoutedTTS(mock_local_tts, mock_cloud_tts, router)

    register_call("test-call-1", "camp-1")
    try:
        stream = routed_tts.stream()
        output_emitter = MagicMock(spec=tts.AudioEmitter)
        
        # Run the stream output generation
        await stream._run(output_emitter)
        
        assert stream.provider == "elevenlabs"
        # Check output_emitter initialization
        output_emitter.initialize.assert_called_once()
        # Check output_emitter received the audio bytes
        output_emitter.push.assert_called_with(b"\x00" * 320)
        assert get_error_count("test-call-1", "tts", "local") == 1
    finally:
        unregister_call("test-call-1")


@pytest.mark.asyncio
async def test_routed_tts_unavailability_handling(clean_health) -> None:
    """RoutedTTS fails cleanly and raises a RuntimeError on complete failure without crash or blank audio."""
    config = VoiceConfig(tts_routing_mode="local", tts_local_max_retries=1)
    router = ModelRouter(config)

    mock_local_tts = MagicMock(spec=tts.TTS)
    mock_local_tts.sample_rate = 24000
    mock_cloud_tts = MagicMock(spec=tts.TTS)

    mock_local_tts.stream.side_effect = RuntimeError("Local TTS offline")
    routed_tts = RoutedTTS(mock_local_tts, mock_cloud_tts, router)

    register_call("test-call-1", "camp-1")
    try:
        stream = routed_tts.stream()
        output_emitter = MagicMock(spec=tts.AudioEmitter)

        with pytest.raises(RuntimeError, match="tts_unavailable"):
            await stream._run(output_emitter)

        # Emitter should NOT have received success pushes
        output_emitter.push.assert_not_called()
    finally:
        unregister_call("test-call-1")


def test_interface_compatibility() -> None:
    """RoutedLLM and RoutedTTS subclass LiveKit base classes to ensure compatibility."""
    config = VoiceConfig()
    router = ModelRouter(config)

    mock_local_llm = MagicMock(spec=llm.LLM)
    mock_cloud_llm = MagicMock(spec=llm.LLM)
    routed_llm = RoutedLLM(mock_local_llm, mock_cloud_llm, router)

    mock_local_tts = MagicMock(spec=tts.TTS)
    mock_local_tts.sample_rate = 24000
    mock_cloud_tts = MagicMock(spec=tts.TTS)
    routed_tts = RoutedTTS(mock_local_tts, mock_cloud_tts, router)

    # Check subclassing
    assert isinstance(routed_llm, llm.LLM)
    assert isinstance(routed_tts, tts.TTS)


@pytest.mark.asyncio
async def test_stage_aware_fallbacks() -> None:
    """Verify that stage-aware fallback phrasing functions properly inside AgentRuntime."""
    from core.agent_runtime import AgentRuntime
    
    # Assert exact required fallbacks (Requirement 7)
    assert AgentRuntime._get_stage_fallback("dnc") == "Understood. I’ll make a note of that. Take care."
    assert AgentRuntime._get_stage_fallback("wrong_number") == "Understood. I’ll make a note of that. Take care."
    assert AgentRuntime._get_stage_fallback("callback") == "No problem. Would later today or tomorrow be better?"
    assert AgentRuntime._get_stage_fallback("transfer_ready") == "Perfect. Stay right there for me."
    assert AgentRuntime._get_stage_fallback("general") == "Sorry, I missed that last part. Could you say that again?"
