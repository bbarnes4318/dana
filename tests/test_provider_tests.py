from __future__ import annotations
import os
import pytest
import asyncio
from pathlib import Path
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

from dana.providers.provider_registry import ProviderRegistry
from dana.providers.routing import RoutingEngine
from dana.config.voice_config import VoiceConfig
from core.livekit_runtime_adapter import LiveKitRuntimeAdapter
from core.call_state import CallStage

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

class MockAsyncIterator:
    def __init__(self, items):
        self.items = items
    def __aiter__(self):
        return self
    async def __anext__(self):
        if not self.items:
            raise StopAsyncIteration
        return self.items.pop(0)

# 1. Balanced mode chooses deepgram/elevenlabs when keys are present
@pytest.mark.asyncio
async def test_balanced_mode_chooses_cloud_when_keys_present():
    reg = ProviderRegistry()
    
    # Mock healthy deepgram and elevenlabs
    deepgram = DummyHealthyProvider("deepgram", 0.05, 150.0)
    elevenlabs = DummyHealthyProvider("elevenlabs", 0.15, 300.0)
    local_whisper = DummyHealthyProvider("local_faster_whisper", 0.0, 200.0)
    local_kokoro = DummyHealthyProvider("local_kokoro", 0.0, 100.0)
    
    reg.register_stt(deepgram)
    reg.register_stt(local_whisper)
    reg.register_tts(elevenlabs)
    reg.register_tts(local_kokoro)
    
    reg.register_telephony(DummyHealthyProvider("livekit_sip", 0.0, 0.0))
    reg.register_vad(DummyHealthyProvider("silero", 0.0, 0.0))
    reg.register_llm(DummyHealthyProvider("local_vllm", 0.0, 0.0))
    
    config = VoiceConfig(provider_mode="balanced")
    engine = RoutingEngine(config, reg)
    stack = await engine.select_provider_stack()
    
    assert stack["stt"].name == "deepgram"
    assert stack["tts"].name == "elevenlabs"

# 2. Balanced mode falls back to local only when keys are missing
@pytest.mark.asyncio
async def test_balanced_mode_falls_back_to_local_when_keys_missing():
    reg = ProviderRegistry()
    
    # Mock unhealthy deepgram/elevenlabs and healthy local ones
    deepgram = DummyUnhealthyProvider("deepgram", 0.05, 150.0)
    elevenlabs = DummyUnhealthyProvider("elevenlabs", 0.15, 300.0)
    local_whisper = DummyHealthyProvider("local_faster_whisper", 0.0, 200.0)
    local_kokoro = DummyHealthyProvider("local_kokoro", 0.0, 100.0)
    
    reg.register_stt(deepgram)
    reg.register_stt(local_whisper)
    reg.register_tts(elevenlabs)
    reg.register_tts(local_kokoro)
    
    reg.register_telephony(DummyHealthyProvider("livekit_sip", 0.0, 0.0))
    reg.register_vad(DummyHealthyProvider("silero", 0.0, 0.0))
    reg.register_llm(DummyHealthyProvider("local_vllm", 0.0, 0.0))
    
    config = VoiceConfig(provider_mode="balanced")
    engine = RoutingEngine(config, reg)
    stack = await engine.select_provider_stack()
    
    assert stack["stt"].name == "local_faster_whisper"
    assert stack["tts"].name == "local_kokoro"

# 3. Locked mode fails if Deepgram/ElevenLabs unavailable
@pytest.mark.asyncio
async def test_locked_mode_fails_if_unavailable():
    reg = ProviderRegistry()
    elevenlabs = DummyUnhealthyProvider("elevenlabs", 0.15, 300.0)
    reg.register_tts(elevenlabs)
    
    config = VoiceConfig(provider_mode="locked", tts_provider="elevenlabs")
    engine = RoutingEngine(config, reg)
    
    with pytest.raises(RuntimeError) as exc:
        await engine.select_provider_stack()
    assert "is unhealthy or credentials missing" in str(exc.value)

# 4. Docker/env config loads expected production defaults
def test_docker_env_config_production_defaults(monkeypatch):
    monkeypatch.delenv("DANA_AGENT_PROMPT_PATH", raising=False)
    config = VoiceConfig(runtime_env="production")
    assert config.agent_prompt_path == "prompts/final_expense.production.md"
    assert config.provider_mode == "balanced"

# 5. TTS stream normal completion does not interrupt
@pytest.mark.asyncio
async def test_tts_stream_normal_completion_does_not_interrupt():
    # Mock DanaAgent
    from dana.runtime.voice_session import DanaAgent
    shared = MagicMock()
    shared.config = VoiceConfig()
    
    # Mock tts stream
    tts_stream_mock = MagicMock()
    
    frame_ev = MagicMock()
    frame_ev.frame = MagicMock()
    tts_stream_mock.__aiter__ = lambda *args: MockAsyncIterator([frame_ev])
    tts_stream_mock.interrupt = AsyncMock()
    
    shared.tts.stream.return_value = tts_stream_mock
    
    agent = DanaAgent(shared, MagicMock())
    
    # Consume tts_node
    async def dummy_text():
        yield "Hello world"
        
    frames = []
    async for frame in agent.tts_node(dummy_text(), None):
        frames.append(frame)
        
    assert len(frames) == 1
    # Verify that interrupt was NOT called on the tts_stream
    tts_stream_mock.interrupt.assert_not_called()

# 6. "who is this?" produces a response
@pytest.mark.asyncio
async def test_who_is_this_produces_response(monkeypatch) -> None:
    project_root = Path(__file__).resolve().parent.parent
    adapter = LiveKitRuntimeAdapter(call_id="test-who-is-this", project_root=project_root)
    
    async def mock_chat(inst: str) -> str:
        return "I am Alex with American Beneficiary. We are a coordinator for final expense programs."
        
    res = await adapter.process_user_turn("Who is this?", mock_chat)
    assert res.agent_response is not None
    assert len(res.agent_response.strip()) > 0
    assert "alex" in res.agent_response.lower()

# 7. "what is this about?" produces a response
@pytest.mark.asyncio
async def test_what_is_this_about_produces_response(monkeypatch) -> None:
    project_root = Path(__file__).resolve().parent.parent
    adapter = LiveKitRuntimeAdapter(call_id="test-what-is-about", project_root=project_root)
    
    async def mock_chat(inst: str) -> str:
        return "This is about the state-regulated program that covers final burial expenses."
        
    res = await adapter.process_user_turn("What is this about?", mock_chat)
    assert res.agent_response is not None
    assert len(res.agent_response.strip()) > 0
    assert "burial" in res.agent_response.lower() or "expense" in res.agent_response.lower()

# 8. DNC ends the call
@pytest.mark.asyncio
async def test_dnc_ends_call() -> None:
    project_root = Path(__file__).resolve().parent.parent
    adapter = LiveKitRuntimeAdapter(call_id="test-dnc-ends", project_root=project_root)
    
    async def mock_chat(inst: str) -> str:
        return "Goodbye"
        
    res = await adapter.process_user_turn("Please put me on the do not call list.", mock_chat)
    assert res.should_end_call is True
    assert res.stage == "dnc"

# 9. wrong number ends the call
@pytest.mark.asyncio
async def test_wrong_number_ends_call() -> None:
    project_root = Path(__file__).resolve().parent.parent
    adapter = LiveKitRuntimeAdapter(call_id="test-wrong-ends", project_root=project_root)
    
    async def mock_chat(inst: str) -> str:
        return "Goodbye"
        
    res = await adapter.process_user_turn("This is the wrong number.", mock_chat)
    assert res.should_end_call is True

# 10. no transfer before consent
@pytest.mark.asyncio
async def test_no_transfer_before_consent() -> None:
    project_root = Path(__file__).resolve().parent.parent
    adapter = LiveKitRuntimeAdapter(call_id="test-no-transfer-yet", project_root=project_root)
    adapter.state_machine.call_state.transition_to(CallStage.INTEREST_CHECK)
    
    async def mock_chat(inst: str) -> str:
        return "Are you between forty and eighty-five?"
        
    res = await adapter.process_user_turn("Yes I am open to reviewing the information.", mock_chat)
    # Lead should not be marked transfer ready yet since we haven't done other checks or asked for consent
    assert res.stage != "transfer_ready"
    assert not adapter.state_machine.lead.transfer_consent_confirmed

# 11. transfer after consent
@pytest.mark.asyncio
async def test_transfer_after_consent(monkeypatch) -> None:
    project_root = Path(__file__).resolve().parent.parent
    monkeypatch.setenv("LICENSED_AGENT_PHONE_NUMBER", "+15551234567")
    monkeypatch.setenv("DANA_CONFIRM_TRANSFER_CALL", "yes")

    adapter = LiveKitRuntimeAdapter(call_id="test-transfer-after-consent", project_root=project_root)
    
    # Fully qualify the lead
    adapter.state_machine.lead.open_to_review = True
    adapter.state_machine.lead.age_range_confirmed = True
    adapter.state_machine.lead.living_independently = True
    adapter.state_machine.lead.financial_decision_maker = True
    
    adapter.state_machine.call_state.transition_to(CallStage.TRANSFER_CONSENT)
    
    from telephony.fe_transfer import FeTransferResult
    async def mock_fe_transfer(*args, **kwargs):
        return FeTransferResult(
            success=True,
            reason="success",
            transfer_mode="cold_transfer"
        )
    from tools import fe_transfer as tools_fe_transfer
    monkeypatch.setattr(tools_fe_transfer, "fe_transfer", mock_fe_transfer)

    async def mock_chat(inst: str) -> str:
        return "Stay on the line."
        
    res = await adapter.process_user_turn("Yes, connect me.", mock_chat)
    assert res.stage == "transfer_ready"
    assert adapter.state_machine.lead.transfer_consent_confirmed is True

# 12. telephony provider unhealthy fails production startup
@pytest.mark.asyncio
async def test_telephony_provider_unhealthy_fails_production_startup():
    reg = ProviderRegistry()
    
    # Register unhealthy telephony provider, healthy others
    unhealthy_tel = DummyUnhealthyProvider("livekit_sip", 0.0, 0.0)
    healthy_vad = DummyHealthyProvider("silero", 0.0, 0.0)
    healthy_llm = DummyHealthyProvider("local_vllm", 0.0, 0.0)
    healthy_tts = DummyHealthyProvider("local_kokoro", 0.0, 0.0)
    healthy_stt = DummyHealthyProvider("local_faster_whisper", 0.0, 0.0)
    
    reg.register_telephony(unhealthy_tel)
    reg.register_vad(healthy_vad)
    reg.register_llm(healthy_llm)
    reg.register_tts(healthy_tts)
    reg.register_stt(healthy_stt)
    
    # Locked mode / Production mode should fail
    config = VoiceConfig(runtime_env="production", telephony_provider="livekit_sip", vad_provider="silero")
    engine = RoutingEngine(config, reg)
    
    with pytest.raises(RuntimeError) as exc:
        await engine.select_provider_stack()
    assert "Telephony provider 'livekit_sip' is unhealthy or credentials missing." in str(exc.value)
