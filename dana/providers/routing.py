from __future__ import annotations
import logging
from typing import Dict, Any, Optional
from dana.providers.provider_registry import registry, ProviderRegistry
from dana.config.voice_config import VoiceConfig
from dana.config.cost_profiles import CostProfiles

logger = logging.getLogger(__name__)

class RoutingEngine:
    def __init__(self, config: VoiceConfig, reg: Optional[ProviderRegistry] = None) -> None:
        self.config = config
        self.registry = reg or registry
        self.cost_profiles = CostProfiles()

    async def select_provider_stack(self) -> Dict[str, Any]:
        """
        Evaluate and select active provider stack based on DANA_PROVIDER_MODE.
        Returns:
            dict with keys: llm, tts, stt, vad, telephony
        """
        mode = self.config.provider_mode.strip().lower()
        logger.info(f"RoutingEngine resolving provider stack under mode: '{mode}'")

        # 1. Resolve VAD and Telephony (typically locked/configured directly)
        vad = self.registry.get_vad(self.config.vad_provider)
        if not vad:
            raise RuntimeError(f"Configured VAD provider '{self.config.vad_provider}' not found in registry")
        
        telephony = self.registry.get_telephony(self.config.telephony_provider)
        if not telephony:
            raise RuntimeError(f"Configured Telephony provider '{self.config.telephony_provider}' not found in registry")

        # Run health check on VAD and Telephony
        vad_healthy = await vad.health_check()
        telephony_healthy = await telephony.health_check()

        # 2. Resolve LLM, TTS, STT depending on mode
        if mode == "locked":
            llm = self.registry.get_llm(self.config.llm_provider)
            if not llm:
                raise RuntimeError(f"Configured LLM provider '{self.config.llm_provider}' not found in registry")
            
            tts = self.registry.get_tts(self.config.tts_provider)
            if not tts:
                raise RuntimeError(f"Configured TTS provider '{self.config.tts_provider}' not found in registry")

            stt = self.registry.get_stt(self.config.stt_provider)
            if not stt:
                raise RuntimeError(f"Configured STT provider '{self.config.stt_provider}' not found in registry")

            # In locked mode, we verify health and fail if unhealthy
            if not await llm.health_check():
                raise RuntimeError(f"Locked provider LLM '{llm.name}' is unhealthy or credentials missing.")
            if not await tts.health_check():
                raise RuntimeError(f"Locked provider TTS '{tts.name}' is unhealthy or credentials missing.")
            if not await stt.health_check():
                raise RuntimeError(f"Locked provider STT '{stt.name}' is unhealthy or credentials missing.")

        elif mode == "cheapest_safe":
            # Select lowest cost healthy provider
            llm = await self._select_cheapest_llm()
            tts = await self._select_cheapest_tts()
            stt = await self._select_cheapest_stt()

        elif mode == "fastest":
            # Select lowest latency healthy provider
            llm = await self._select_fastest_llm()
            tts = await self._select_fastest_tts()
            stt = await self._select_fastest_stt()

        elif mode == "highest_quality":
            # Select highest quality (usually cloud) healthy provider
            llm = await self._select_highest_quality_llm()
            tts = await self._select_highest_quality_tts()
            stt = await self._select_highest_quality_stt()

        else:  # balanced mode (default)
            # Balanced: cost-effective but avoid bad voice/STT
            llm = await self._select_balanced_llm()
            tts = await self._select_balanced_tts()
            stt = await self._select_balanced_stt()

        # Enforce cloud/local routing constraints from config if explicitly set to "cloud"
        if self.config.stt_routing_mode == "cloud" and "local" in stt.name.lower():
            raise RuntimeError(f"Cloud STT routing explicitly required, but resolved to local provider '{stt.name}'. STT mode premium_live/cloud requires DEEPGRAM_API_KEY.")
            
        if self.config.tts_routing_mode == "cloud" and "local" in tts.name.lower():
            raise RuntimeError(f"Cloud TTS routing explicitly required, but resolved to local provider '{tts.name}'. TTS mode premium_live/cloud requires ELEVENLABS_API_KEY.")

        # Compute estimated cost per connected minute
        est_min_cost = self._estimate_connected_minute_cost(
            llm.name,
            self.config.llm_model,
            tts.name,
            stt.name,
            telephony.name
        )

        return {
            "mode": mode,
            "llm": llm,
            "tts": tts,
            "stt": stt,
            "vad": vad,
            "telephony": telephony,
            "health": {
                "llm": True,
                "tts": True,
                "stt": True,
                "vad": vad_healthy,
                "telephony": telephony_healthy
            },
            "estimated_cost_per_minute": est_min_cost
        }

    # ---- Cheapest Selectors ----
    async def _select_cheapest_llm(self) -> Any:
        best_provider = None
        min_cost = float('inf')
        for provider in self.registry.llm_providers.values():
            if await provider.health_check():
                cost = getattr(provider, "estimated_input_cost_per_1m_tokens", 0.0) + getattr(provider, "estimated_output_cost_per_1m_tokens", 0.0)
                if cost < min_cost:
                    min_cost = cost
                    best_provider = provider
        if best_provider:
            return best_provider
        raise RuntimeError("No healthy LLM provider found for cheapest_safe mode")

    async def _select_cheapest_tts(self) -> Any:
        best_provider = None
        min_cost = float('inf')
        for provider in self.registry.tts_providers.values():
            if await provider.health_check():
                cost = getattr(provider, "estimated_cost_per_minute", 0.0)
                if cost < min_cost:
                    min_cost = cost
                    best_provider = provider
        if best_provider:
            return best_provider
        raise RuntimeError("No healthy TTS provider found for cheapest_safe mode")

    async def _select_cheapest_stt(self) -> Any:
        best_provider = None
        min_cost = float('inf')
        for provider in self.registry.stt_providers.values():
            if await provider.health_check():
                cost = getattr(provider, "estimated_cost_per_minute", 0.0)
                if cost < min_cost:
                    min_cost = cost
                    best_provider = provider
        if best_provider:
            return best_provider
        raise RuntimeError("No healthy STT provider found for cheapest_safe mode")

    # ---- Fastest Selectors ----
    async def _select_fastest_llm(self) -> Any:
        best_provider = None
        min_latency = float('inf')
        for provider in self.registry.llm_providers.values():
            if await provider.health_check():
                latency = getattr(provider, "average_first_token_ms", 0.0)
                if latency < min_latency:
                    min_latency = latency
                    best_provider = provider
        if best_provider:
            return best_provider
        raise RuntimeError("No healthy LLM provider found for fastest mode")

    async def _select_fastest_tts(self) -> Any:
        best_provider = None
        min_latency = float('inf')
        for provider in self.registry.tts_providers.values():
            if await provider.health_check():
                latency = getattr(provider, "average_first_audio_ms", 0.0)
                if latency < min_latency:
                    min_latency = latency
                    best_provider = provider
        if best_provider:
            return best_provider
        raise RuntimeError("No healthy TTS provider found for fastest mode")

    async def _select_fastest_stt(self) -> Any:
        best_provider = None
        min_latency = float('inf')
        for provider in self.registry.stt_providers.values():
            if await provider.health_check():
                latency = getattr(provider, "average_final_transcript_ms", 0.0)
                if latency < min_latency:
                    min_latency = latency
                    best_provider = provider
        if best_provider:
            return best_provider
        raise RuntimeError("No healthy STT provider found for fastest mode")

    # ---- Highest Quality Selectors ----
    async def _select_highest_quality_llm(self) -> Any:
        openai = self.registry.get_llm("openai")
        if openai and await openai.health_check():
            return openai
        vllm = self.registry.get_llm("local_vllm")
        if vllm and await vllm.health_check():
            return vllm
        raise RuntimeError("No healthy LLM provider found for highest_quality mode")

    async def _select_highest_quality_tts(self) -> Any:
        elevenlabs = self.registry.get_tts("elevenlabs")
        if elevenlabs and await elevenlabs.health_check():
            return elevenlabs
        kokoro = self.registry.get_tts("local_kokoro")
        if kokoro and await kokoro.health_check():
            return kokoro
        raise RuntimeError("No healthy TTS provider found for highest_quality mode")

    async def _select_highest_quality_stt(self) -> Any:
        deepgram = self.registry.get_stt("deepgram")
        if deepgram and await deepgram.health_check():
            return deepgram
        whisper = self.registry.get_stt("local_faster_whisper")
        if whisper and await whisper.health_check():
            return whisper
        raise RuntimeError("No healthy STT provider found for highest_quality mode")

    # ---- Balanced Selectors ----
    async def _select_balanced_llm(self) -> Any:
        # Prefer local vllm if healthy (cheaper & lower latency), fall back to openai
        vllm = self.registry.get_llm("local_vllm")
        if vllm and await vllm.health_check():
            return vllm
        openai = self.registry.get_llm("openai")
        if openai and await openai.health_check():
            return openai
        # Fall back to whichever is configured
        fallback_llm = self.registry.get_llm(self.config.llm_provider)
        if fallback_llm:
            return fallback_llm
        raise RuntimeError("No healthy LLM provider found for balanced mode")

    async def _select_balanced_tts(self) -> Any:
        # Prefer elevenlabs for premium voice quality on calls, fall back to local kokoro
        elevenlabs = self.registry.get_tts("elevenlabs")
        if elevenlabs and await elevenlabs.health_check():
            return elevenlabs
        kokoro = self.registry.get_tts("local_kokoro")
        if kokoro and await kokoro.health_check():
            return kokoro
        # Fall back to whichever is configured
        fallback_tts = self.registry.get_tts(self.config.tts_provider)
        if fallback_tts:
            return fallback_tts
        raise RuntimeError("No healthy TTS provider found for balanced mode")

    async def _select_balanced_stt(self) -> Any:
        # Prefer deepgram for low-latency accurate cloud STT, fall back to local whisper
        deepgram = self.registry.get_stt("deepgram")
        if deepgram and await deepgram.health_check():
            return deepgram
        whisper = self.registry.get_stt("local_faster_whisper")
        if whisper and await whisper.health_check():
            return whisper
        fallback_stt = self.registry.get_stt(self.config.stt_provider)
        if fallback_stt:
            return fallback_stt
        raise RuntimeError("No healthy STT provider found for balanced mode")

    def _estimate_connected_minute_cost(
        self,
        llm_name: str,
        llm_model: str,
        tts_name: str,
        stt_name: str,
        telephony_name: str
    ) -> float:
        tel_cost = self.cost_profiles.get_telephony_cost_per_minute(telephony_name)
        stt_cost = self.cost_profiles.get_stt_cost_per_minute(stt_name)
        tts_cost = self.cost_profiles.get_tts_cost_per_minute(tts_name)
        
        # Estimate LLM cost per minute: assume 15 turns per minute, 1000 input tokens, 200 output tokens
        llm_input_cost = self.cost_profiles.get_llm_cost_per_1k_tokens(llm_name, llm_model, is_output=False) * 1.0
        llm_output_cost = self.cost_profiles.get_llm_cost_per_1k_tokens(llm_name, llm_model, is_output=True) * 0.2
        llm_cost_per_minute = (llm_input_cost + llm_output_cost) * 15.0
        
        return tel_cost + stt_cost + tts_cost + llm_cost_per_minute
