from __future__ import annotations
import logging
from typing import AsyncIterable, List, Any
from dana.providers.base import LLMProvider, TTSProvider, STTProvider, VADProvider, TelephonyProvider

logger = logging.getLogger(__name__)

class UnimplementedProviderMixin:
    @property
    def is_implemented(self) -> bool:
        return False

    @property
    def status_reason(self) -> str:
        return f"Provider {self.__class__.__name__} is a future stub and not implemented yet."

    async def health_check(self) -> bool:
        logger.warning(self.status_reason)
        return False


# ---- LLM Stubs ----
class AnthropicLLMProvider(UnimplementedProviderMixin, LLMProvider):
    @property
    def name(self) -> str: return "anthropic"
    @property
    def supports_streaming(self) -> bool: return False
    @property
    def supports_tools(self) -> bool: return False
    @property
    def estimated_input_cost_per_1m_tokens(self) -> float: return 3.0
    @property
    def estimated_output_cost_per_1m_tokens(self) -> float: return 15.0
    @property
    def average_first_token_ms(self) -> float: return 9999.0
    def create_client(self) -> Any: raise NotImplementedError(self.status_reason)
    async def stream_response(self, chat_ctx: Any, **kwargs) -> AsyncIterable[str]:
        raise NotImplementedError(self.status_reason)
        yield ""

class GeminiLLMProvider(UnimplementedProviderMixin, LLMProvider):
    @property
    def name(self) -> str: return "google_gemini"
    @property
    def supports_streaming(self) -> bool: return False
    @property
    def supports_tools(self) -> bool: return False
    @property
    def estimated_input_cost_per_1m_tokens(self) -> float: return 0.075
    @property
    def estimated_output_cost_per_1m_tokens(self) -> float: return 0.30
    @property
    def average_first_token_ms(self) -> float: return 9999.0
    def create_client(self) -> Any: raise NotImplementedError(self.status_reason)
    async def stream_response(self, chat_ctx: Any, **kwargs) -> AsyncIterable[str]:
        raise NotImplementedError(self.status_reason)
        yield ""

class DeepSeekLLMProvider(UnimplementedProviderMixin, LLMProvider):
    @property
    def name(self) -> str: return "deepseek"
    @property
    def supports_streaming(self) -> bool: return False
    @property
    def supports_tools(self) -> bool: return False
    @property
    def estimated_input_cost_per_1m_tokens(self) -> float: return 0.14
    @property
    def estimated_output_cost_per_1m_tokens(self) -> float: return 0.28
    @property
    def average_first_token_ms(self) -> float: return 9999.0
    def create_client(self) -> Any: raise NotImplementedError(self.status_reason)
    async def stream_response(self, chat_ctx: Any, **kwargs) -> AsyncIterable[str]:
        raise NotImplementedError(self.status_reason)
        yield ""

class GroqLLMProvider(UnimplementedProviderMixin, LLMProvider):
    @property
    def name(self) -> str: return "groq"
    @property
    def supports_streaming(self) -> bool: return False
    @property
    def supports_tools(self) -> bool: return False
    @property
    def estimated_input_cost_per_1m_tokens(self) -> float: return 0.05
    @property
    def estimated_output_cost_per_1m_tokens(self) -> float: return 0.10
    @property
    def average_first_token_ms(self) -> float: return 9999.0
    def create_client(self) -> Any: raise NotImplementedError(self.status_reason)
    async def stream_response(self, chat_ctx: Any, **kwargs) -> AsyncIterable[str]:
        raise NotImplementedError(self.status_reason)
        yield ""

class TogetherLLMProvider(UnimplementedProviderMixin, LLMProvider):
    @property
    def name(self) -> str: return "together"
    @property
    def supports_streaming(self) -> bool: return False
    @property
    def supports_tools(self) -> bool: return False
    @property
    def estimated_input_cost_per_1m_tokens(self) -> float: return 0.20
    @property
    def estimated_output_cost_per_1m_tokens(self) -> float: return 0.20
    @property
    def average_first_token_ms(self) -> float: return 9999.0
    def create_client(self) -> Any: raise NotImplementedError(self.status_reason)
    async def stream_response(self, chat_ctx: Any, **kwargs) -> AsyncIterable[str]:
        raise NotImplementedError(self.status_reason)
        yield ""

class FireworksLLMProvider(UnimplementedProviderMixin, LLMProvider):
    @property
    def name(self) -> str: return "fireworks"
    @property
    def supports_streaming(self) -> bool: return False
    @property
    def supports_tools(self) -> bool: return False
    @property
    def estimated_input_cost_per_1m_tokens(self) -> float: return 0.20
    @property
    def estimated_output_cost_per_1m_tokens(self) -> float: return 0.20
    @property
    def average_first_token_ms(self) -> float: return 9999.0
    def create_client(self) -> Any: raise NotImplementedError(self.status_reason)
    async def stream_response(self, chat_ctx: Any, **kwargs) -> AsyncIterable[str]:
        raise NotImplementedError(self.status_reason)
        yield ""

class OpenRouterLLMProvider(UnimplementedProviderMixin, LLMProvider):
    @property
    def name(self) -> str: return "openrouter"
    @property
    def supports_streaming(self) -> bool: return False
    @property
    def supports_tools(self) -> bool: return False
    @property
    def estimated_input_cost_per_1m_tokens(self) -> float: return 0.20
    @property
    def estimated_output_cost_per_1m_tokens(self) -> float: return 0.20
    @property
    def average_first_token_ms(self) -> float: return 9999.0
    def create_client(self) -> Any: raise NotImplementedError(self.status_reason)
    async def stream_response(self, chat_ctx: Any, **kwargs) -> AsyncIterable[str]:
        raise NotImplementedError(self.status_reason)
        yield ""


# ---- TTS Stubs ----
class CartesiaTTSProvider(UnimplementedProviderMixin, TTSProvider):
    @property
    def name(self) -> str: return "cartesia"
    @property
    def supports_streaming(self) -> bool: return False
    @property
    def supports_pcm(self) -> bool: return False
    @property
    def supports_ulaw(self) -> bool: return False
    @property
    def sample_rates(self) -> List[int]: return []
    @property
    def estimated_cost_per_minute(self) -> float: return 0.0675
    @property
    def average_first_audio_ms(self) -> float: return 9999.0
    def synthesize_stream(self) -> Any: raise NotImplementedError(self.status_reason)

class DeepgramAuraTTSProvider(UnimplementedProviderMixin, TTSProvider):
    @property
    def name(self) -> str: return "deepgram_aura"
    @property
    def supports_streaming(self) -> bool: return False
    @property
    def supports_pcm(self) -> bool: return False
    @property
    def supports_ulaw(self) -> bool: return False
    @property
    def sample_rates(self) -> List[int]: return []
    @property
    def estimated_cost_per_minute(self) -> float: return 0.0135
    @property
    def average_first_audio_ms(self) -> float: return 9999.0
    def synthesize_stream(self) -> Any: raise NotImplementedError(self.status_reason)

class OpenAITTSProvider(UnimplementedProviderMixin, TTSProvider):
    @property
    def name(self) -> str: return "openai_tts"
    @property
    def supports_streaming(self) -> bool: return False
    @property
    def supports_pcm(self) -> bool: return False
    @property
    def supports_ulaw(self) -> bool: return False
    @property
    def sample_rates(self) -> List[int]: return []
    @property
    def estimated_cost_per_minute(self) -> float: return 0.0135
    @property
    def average_first_audio_ms(self) -> float: return 9999.0
    def synthesize_stream(self) -> Any: raise NotImplementedError(self.status_reason)

class PlayHTTTSProvider(UnimplementedProviderMixin, TTSProvider):
    @property
    def name(self) -> str: return "playht"
    @property
    def supports_streaming(self) -> bool: return False
    @property
    def supports_pcm(self) -> bool: return False
    @property
    def supports_ulaw(self) -> bool: return False
    @property
    def sample_rates(self) -> List[int]: return []
    @property
    def estimated_cost_per_minute(self) -> float: return 0.09
    @property
    def average_first_audio_ms(self) -> float: return 9999.0
    def synthesize_stream(self) -> Any: raise NotImplementedError(self.status_reason)


# ---- STT Stubs ----
class AssemblyAISTTProvider(UnimplementedProviderMixin, STTProvider):
    @property
    def name(self) -> str: return "assemblyai"
    @property
    def supports_streaming(self) -> bool: return False
    @property
    def languages(self) -> List[str]: return []
    @property
    def estimated_cost_per_minute(self) -> float: return 0.015
    @property
    def average_final_transcript_ms(self) -> float: return 9999.0
    def transcribe_stream(self) -> Any: raise NotImplementedError(self.status_reason)

class SpeechmaticsSTTProvider(UnimplementedProviderMixin, STTProvider):
    @property
    def name(self) -> str: return "speechmatics"
    @property
    def supports_streaming(self) -> bool: return False
    @property
    def languages(self) -> List[str]: return []
    @property
    def estimated_cost_per_minute(self) -> float: return 0.018
    @property
    def average_final_transcript_ms(self) -> float: return 9999.0
    def transcribe_stream(self) -> Any: raise NotImplementedError(self.status_reason)

class OpenAIWhisperSTTProvider(UnimplementedProviderMixin, STTProvider):
    @property
    def name(self) -> str: return "openai_whisper"
    @property
    def supports_streaming(self) -> bool: return False
    @property
    def languages(self) -> List[str]: return []
    @property
    def estimated_cost_per_minute(self) -> float: return 0.006
    @property
    def average_final_transcript_ms(self) -> float: return 9999.0
    def transcribe_stream(self) -> Any: raise NotImplementedError(self.status_reason)


# ---- VAD Stubs ----
class LiveKitVADProvider(UnimplementedProviderMixin, VADProvider):
    @property
    def name(self) -> str: return "livekit_vad"
    @property
    def average_detection_ms(self) -> float: return 9999.0
    @property
    def false_interrupt_risk(self) -> float: return 1.0
    def create_detector(self) -> Any: raise NotImplementedError(self.status_reason)

class SemanticTurnDetectorVADProvider(UnimplementedProviderMixin, VADProvider):
    @property
    def name(self) -> str: return "semantic_turn_detector"
    @property
    def average_detection_ms(self) -> float: return 9999.0
    @property
    def false_interrupt_risk(self) -> float: return 1.0
    def create_detector(self) -> Any: raise NotImplementedError(self.status_reason)


# ---- Telephony Stubs ----
class FreeSwitchSIPTelephonyProvider(UnimplementedProviderMixin, TelephonyProvider):
    @property
    def name(self) -> str: return "freeswitch_sip"
    @property
    def supports_outbound(self) -> bool: return False
    @property
    def supports_transfer(self) -> bool: return False
    @property
    def supports_recording(self) -> bool: return False
    @property
    def supports_warm_bridge(self) -> bool: return False
    async def originate_call(self, destination: str, **kwargs) -> Any: raise NotImplementedError(self.status_reason)
    async def end_call(self, call_id: str) -> bool: raise NotImplementedError(self.status_reason)
    async def transfer_call(self, call_id: str, destination: str, warm: bool = False) -> bool:
        raise NotImplementedError(self.status_reason)

class HopwhistleTelephonyProvider(UnimplementedProviderMixin, TelephonyProvider):
    @property
    def name(self) -> str: return "hopwhistle_provider"
    @property
    def supports_outbound(self) -> bool: return False
    @property
    def supports_transfer(self) -> bool: return False
    @property
    def supports_recording(self) -> bool: return False
    @property
    def supports_warm_bridge(self) -> bool: return False
    async def originate_call(self, destination: str, **kwargs) -> Any: raise NotImplementedError(self.status_reason)
    async def end_call(self, call_id: str) -> bool: raise NotImplementedError(self.status_reason)
    async def transfer_call(self, call_id: str, destination: str, warm: bool = False) -> bool:
        raise NotImplementedError(self.status_reason)
