from __future__ import annotations
import pytest
from unittest import mock
from unittest.mock import AsyncMock, patch
from dana.providers.provider_registry import ProviderRegistry
from dana.providers.routing import RoutingEngine
from dana.config.voice_config import VoiceConfig

class DummyHealthyProvider:
    def __init__(self, name: str, cost: float, latency: float) -> None:
        self._name = name
        self._cost = cost
        self._latency = latency

    @property
    def name(self) -> str:
        return self._name

    @property
    def estimated_input_cost_per_1m_tokens(self) -> float: return self._cost / 2.0
    @property
    def estimated_output_cost_per_1m_tokens(self) -> float: return self._cost / 2.0
    @property
    def estimated_cost_per_minute(self) -> float: return self._cost
    
    @property
    def average_first_token_ms(self) -> float: return self._latency
    @property
    def average_first_audio_ms(self) -> float: return self._latency
    @property
    def average_final_transcript_ms(self) -> float: return self._latency

    async def health_check(self) -> bool:
        return True

    def create_client(self) -> str:
        return f"{self._name}-client"

    def synthesize_stream(self) -> str:
        return f"{self._name}-synth"

    def transcribe_stream(self) -> str:
        return f"{self._name}-trans"

    def create_detector(self) -> str:
        return f"{self._name}-det"

class DummyUnhealthyProvider(DummyHealthyProvider):
    async def health_check(self) -> bool:
        return False

@pytest.mark.asyncio
async def test_registry_registration():
    reg = ProviderRegistry()
    dummy = DummyHealthyProvider("test_llm", 0.1, 100.0)
    reg.register_llm(dummy)
    assert reg.get_llm("test_llm") is dummy

@pytest.mark.asyncio
async def test_locked_mode_unhealthy_fails():
    reg = ProviderRegistry()
    # Register an unhealthy LLM provider
    unhealthy = DummyUnhealthyProvider("local_vllm", 0.0, 0.0)
    reg.register_llm(unhealthy)
    
    config = VoiceConfig(provider_mode="locked", llm_provider="local_vllm")
    engine = RoutingEngine(config, reg)
    
    with pytest.raises(RuntimeError) as exc:
        await engine.select_provider_stack()
    assert "is unhealthy or credentials missing" in str(exc.value)

@pytest.mark.asyncio
async def test_cheapest_safe_selection():
    reg = ProviderRegistry()
    
    p1 = DummyHealthyProvider("local_vllm", 0.50, 100.0)
    p2 = DummyHealthyProvider("openai", 0.10, 300.0) # openai is cheaper in this dummy scenario
    
    reg.register_llm(p1)
    reg.register_llm(p2)
    
    config = VoiceConfig(provider_mode="cheapest_safe")
    engine = RoutingEngine(config, reg)
    
    stack = await engine.select_provider_stack()
    assert stack["llm"].name == "openai"

@pytest.mark.asyncio
async def test_fastest_selection():
    reg = ProviderRegistry()
    
    p1 = DummyHealthyProvider("local_vllm", 0.50, 100.0) # local is faster
    p2 = DummyHealthyProvider("openai", 0.10, 300.0)
    
    reg.register_llm(p1)
    reg.register_llm(p2)
    
    config = VoiceConfig(provider_mode="fastest")
    engine = RoutingEngine(config, reg)
    
    stack = await engine.select_provider_stack()
    assert stack["llm"].name == "local_vllm"

@pytest.mark.asyncio
async def test_unavailable_stubs_health_check():
    reg = ProviderRegistry()
    # Stub should return False on health check
    gemini = reg.get_llm("google_gemini")
    assert gemini is not None
    assert await gemini.health_check() is False
