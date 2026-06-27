from __future__ import annotations
import logging
from typing import Dict, Any, Optional
from dana.providers.base import LLMProvider, TTSProvider, STTProvider, VADProvider, TelephonyProvider
from dana.providers.llm.vllm import VLLMProvider
from dana.providers.llm.openai import OpenAILLMProvider
from dana.providers.tts.kokoro import KokoroTTSProvider
from dana.providers.tts.elevenlabs import ElevenLabsTTSProvider
from dana.providers.stt.whisper import WhisperSTTProvider
from dana.providers.stt.deepgram import DeepgramSTTProvider
from dana.providers.vad.silero import SileroVADProvider
from dana.providers.telephony.livekit_sip import LiveKitSIPTelephonyProvider

from dana.providers.stubs import (
    AnthropicLLMProvider, GeminiLLMProvider, DeepSeekLLMProvider, GroqLLMProvider,
    TogetherLLMProvider, FireworksLLMProvider, OpenRouterLLMProvider,
    CartesiaTTSProvider, DeepgramAuraTTSProvider, OpenAITTSProvider, PlayHTTTSProvider,
    AssemblyAISTTProvider, SpeechmaticsSTTProvider, OpenAIWhisperSTTProvider,
    LiveKitVADProvider, SemanticTurnDetectorVADProvider,
    FreeSwitchSIPTelephonyProvider, HopwhistleTelephonyProvider
)

logger = logging.getLogger(__name__)

class ProviderRegistry:
    """Central registry mapping provider identifiers to provider implementation adapters."""

    def __init__(self) -> None:
        self._llm_providers: Dict[str, LLMProvider] = {}
        self._tts_providers: Dict[str, TTSProvider] = {}
        self._stt_providers: Dict[str, STTProvider] = {}
        self._vad_providers: Dict[str, VADProvider] = {}
        self._telephony_providers: Dict[str, TelephonyProvider] = {}
        self.register_default_providers()

    def register_default_providers(self) -> None:
        # LLMs
        self.register_llm(VLLMProvider())
        self.register_llm(OpenAILLMProvider())
        self.register_llm(AnthropicLLMProvider())
        self.register_llm(GeminiLLMProvider())
        self.register_llm(DeepSeekLLMProvider())
        self.register_llm(GroqLLMProvider())
        self.register_llm(TogetherLLMProvider())
        self.register_llm(FireworksLLMProvider())
        self.register_llm(OpenRouterLLMProvider())

        # TTS
        self.register_tts(KokoroTTSProvider())
        self.register_tts(ElevenLabsTTSProvider())
        self.register_tts(CartesiaTTSProvider())
        self.register_tts(DeepgramAuraTTSProvider())
        self.register_tts(OpenAITTSProvider())
        self.register_tts(PlayHTTTSProvider())

        # STT
        self.register_stt(WhisperSTTProvider())
        self.register_stt(DeepgramSTTProvider())
        self.register_stt(AssemblyAISTTProvider())
        self.register_stt(SpeechmaticsSTTProvider())
        self.register_stt(OpenAIWhisperSTTProvider())

        # VAD
        self.register_vad(SileroVADProvider())
        self.register_vad(LiveKitVADProvider())
        self.register_vad(SemanticTurnDetectorVADProvider())

        # Telephony
        self.register_telephony(LiveKitSIPTelephonyProvider())
        self.register_telephony(FreeSwitchSIPTelephonyProvider())
        self.register_telephony(HopwhistleTelephonyProvider())

    def register_llm(self, provider: LLMProvider) -> None:
        self._llm_providers[provider.name.lower()] = provider

    def register_tts(self, provider: TTSProvider) -> None:
        self._tts_providers[provider.name.lower()] = provider

    def register_stt(self, provider: STTProvider) -> None:
        self._stt_providers[provider.name.lower()] = provider

    def register_vad(self, provider: VADProvider) -> None:
        self._vad_providers[provider.name.lower()] = provider

    def register_telephony(self, provider: TelephonyProvider) -> None:
        self._telephony_providers[provider.name.lower()] = provider

    def get_llm(self, name: str) -> Optional[LLMProvider]:
        n = name.strip().lower()
        if n in ("local", "vllm", "local_vllm"):
            n = "local_vllm"
        return self._llm_providers.get(n)

    def get_tts(self, name: str) -> Optional[TTSProvider]:
        n = name.strip().lower()
        if n in ("local", "local_kokoro", "kokoro"):
            n = "local_kokoro"
        return self._tts_providers.get(n)

    def get_stt(self, name: str) -> Optional[STTProvider]:
        n = name.strip().lower()
        if n in ("local", "local_faster_whisper", "whisper", "faster_whisper"):
            n = "local_faster_whisper"
        return self._stt_providers.get(n)

    def get_vad(self, name: str) -> Optional[VADProvider]:
        return self._vad_providers.get(name.strip().lower())

    def get_telephony(self, name: str) -> Optional[TelephonyProvider]:
        n = name.strip().lower()
        if n in ("telnyx", "livekit", "livekit_sip"):
            n = "livekit_sip"
        return self._telephony_providers.get(n)

    @property
    def llm_providers(self) -> Dict[str, LLMProvider]:
        return self._llm_providers

    @property
    def tts_providers(self) -> Dict[str, TTSProvider]:
        return self._tts_providers

    @property
    def stt_providers(self) -> Dict[str, STTProvider]:
        return self._stt_providers

    @property
    def vad_providers(self) -> Dict[str, VADProvider]:
        return self._vad_providers

    @property
    def telephony_providers(self) -> Dict[str, TelephonyProvider]:
        return self._telephony_providers


# Singleton registry instance
registry = ProviderRegistry()
