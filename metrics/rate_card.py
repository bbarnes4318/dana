import os
import logging
from decimal import Decimal
from typing import Optional, Tuple, Any

logger = logging.getLogger(__name__)

def is_production() -> bool:
    """Check if environment is set to production."""
    env = os.getenv("DANA_ENV") or os.getenv("ENVIRONMENT") or os.getenv("NODE_ENV")
    return str(env).lower() == "production"

async def get_rate(
    repository: Optional[Any],
    provider: str,
    component: str,
    model: Optional[str] = None
) -> Tuple[Decimal, str, str, bool]:
    """Resolve rate for a given provider, component, and model.
    
    Returns:
        tuple: (unit_rate, usage_unit, rate_source, estimated)
    """
    provider_clean = provider.strip().lower() if provider else "unknown"
    component_clean = component.strip().lower() if component else "unknown"
    model_clean = model.strip().lower() if model else ""

    # 1. Try to fetch from database cost_rate_cards
    if repository:
        try:
            cards = await repository.query_cost_rate_cards({
                "provider": provider,
                "component": component,
                "is_active": True
            })
            if cards:
                match = None
                if model:
                    for card in cards:
                        if card.get("model") == model:
                            match = card
                            break
                if not match:
                    for card in cards:
                        if not card.get("model"):
                            match = card
                            break
                    if not match:
                        match = cards[0]
                
                if match:
                    return Decimal(str(match["unit_rate"])), match["usage_unit"], "database_rate_card", False
        except Exception as e:
            logger.error(f"Error querying cost_rate_cards from DB: {e}")

    # 2. Fall back to environment variables or defaults
    estimated = False
    
    # Telephony rate resolving
    if component_clean == "telephony":
        env_rate = os.getenv("DANA_COST_TELEPHONY_RATE_PER_MINUTE")
        if env_rate:
            return Decimal(env_rate), "minute", "env_override", False
        return Decimal("0.01"), "minute", "default_telephony_rate", False

    # LiveKit rate resolving (per minute)
    if component_clean == "livekit":
        env_rate = os.getenv("DANA_COST_LIVEKIT_PER_MINUTE")
        if env_rate:
            return Decimal(env_rate), "minute", "env_override", False
        return Decimal("0.004"), "minute", "default_livekit_rate", False

    # GPU rate resolving (hourly)
    if component_clean == "gpu":
        env_rate = os.getenv("DANA_COST_GPU_HOURLY_USD")
        if env_rate:
            return Decimal(env_rate), "hour", "env_override", False
        return Decimal("0.50"), "hour", "default_gpu_rate", False

    # STT rate resolving (per second)
    if component_clean == "stt":
        env_rate = os.getenv("DANA_COST_STT_RATE_PER_SECOND")
        if env_rate:
            return Decimal(env_rate), "second", "env_override", False
            
        if provider_clean in ("local", "whisper"):
            env_infra = os.getenv("DANA_COST_LOCAL_STT_INFRA_PER_MINUTE")
            if env_infra:
                return Decimal(env_infra) / Decimal("60.0"), "second", "local_infra_rate", False
            
            if is_production():
                logger.warning("Production mode enabled but DANA_COST_LOCAL_STT_INFRA_PER_MINUTE is not set.")
                estimated = True
            return Decimal("0.0"), "second", "default_local_stt_rate", estimated
        
        # Cloud STT (e.g. Deepgram)
        return Decimal("0.000072"), "second", "default_deepgram_rate", False

    # LLM rate resolving (per token)
    if component_clean == "llm":
        is_local = "llama" in provider_clean or "vllm" in provider_clean or "local" in provider_clean or "llama" in model_clean or "vllm" in model_clean or "local" in model_clean
        if is_local:
            env_rate = os.getenv("DANA_COST_LOCAL_LLM_INFRA_PER_1K_TOKENS")
            if env_rate:
                rate = Decimal(env_rate) / Decimal("1000.0")
                return rate, "token", "local_infra_rate", False
            
            if is_production():
                logger.warning("Production mode enabled but DANA_COST_LOCAL_LLM_INFRA_PER_1K_TOKENS is not set.")
                estimated = True
            default_rate = Decimal("0.0000002")
            return default_rate, "token", "default_local_llm_rate", estimated
        
        # Cloud LLM
        if "gpt-4o-mini" in model_clean:
            return Decimal("0.00000015"), "token", "default_rate_gpt4o_mini_prompt", False
        elif "gpt-4o" in model_clean:
            return Decimal("0.000005"), "token", "default_rate_gpt4o_prompt", False
        elif "claude-3-5-sonnet" in model_clean:
            return Decimal("0.000003"), "token", "default_rate_claude_sonnet_prompt", False
        return Decimal("0.0000002"), "token", "default_llm_rate", False

    # TTS rate resolving (per character)
    if component_clean == "tts":
        env_rate = os.getenv("DANA_COST_TTS_RATE_PER_CHARACTER")
        if env_rate:
            return Decimal(env_rate), "character", "env_override", False
            
        if provider_clean in ("local", "kokoro", "bella"):
            env_infra = os.getenv("DANA_COST_LOCAL_TTS_INFRA_PER_1K_CHARS")
            if env_infra:
                rate = Decimal(env_infra) / Decimal("1000.0")
                return rate, "character", "local_infra_rate", False
            
            if is_production():
                logger.warning("Production mode enabled but DANA_COST_LOCAL_TTS_INFRA_PER_1K_CHARS is not set.")
                estimated = True
            return Decimal("0.0"), "character", "default_local_tts_rate", estimated
        
        # Cloud TTS
        if provider_clean == "elevenlabs":
            return Decimal("0.0003"), "character", "default_elevenlabs_rate", False
        elif provider_clean == "openai":
            return Decimal("0.000015"), "character", "default_openai_tts_rate", False
        return Decimal("0.0"), "character", "default_tts_rate", False

    return Decimal("0.0"), "unknown", "unknown_rate", False


async def get_llm_rates(
    repository: Optional[Any],
    provider: str,
    model: str
) -> Tuple[Decimal, Decimal, str, bool]:
    """Resolve LLM prompt and completion rates per token.
    
    Returns:
        tuple: (prompt_rate, completion_rate, rate_source, estimated)
    """
    provider_clean = provider.strip().lower() if provider else "unknown"
    model_clean = model.strip().lower() if model else ""
    
    # Check env overrides first
    env_prompt = os.getenv("DANA_COST_LLM_PROMPT_RATE_PER_TOKEN")
    env_completion = os.getenv("DANA_COST_LLM_COMPLETION_RATE_PER_TOKEN")
    if env_prompt and env_completion:
        return Decimal(env_prompt), Decimal(env_completion), "env_override", False

    # Check database cost_rate_cards
    if repository:
        try:
            prompt_cards = await repository.query_cost_rate_cards({
                "provider": provider,
                "component": "llm_prompt",
                "is_active": True
            })
            completion_cards = await repository.query_cost_rate_cards({
                "provider": provider,
                "component": "llm_completion",
                "is_active": True
            })
            
            prompt_card = None
            for card in prompt_cards:
                if card.get("model") == model:
                    prompt_card = card
                    break
            if not prompt_card and prompt_cards:
                prompt_card = prompt_cards[0]
                
            completion_card = None
            for card in completion_cards:
                if card.get("model") == model:
                    completion_card = card
                    break
            if not completion_card and completion_cards:
                completion_card = completion_cards[0]
                
            if prompt_card and completion_card:
                return (
                    Decimal(str(prompt_card["unit_rate"])),
                    Decimal(str(completion_card["unit_rate"])),
                    "database_rate_card",
                    False
                )
        except Exception as e:
            logger.error(f"Error querying llm cost_rate_cards: {e}")

    # Fallback to local infra or defaults
    is_local = "llama" in provider_clean or "vllm" in provider_clean or "local" in provider_clean or "llama" in model_clean or "vllm" in model_clean or "local" in model_clean
    estimated = False
    
    if is_local:
        infra_rate = os.getenv("DANA_COST_LOCAL_LLM_INFRA_PER_1K_TOKENS")
        if infra_rate:
            rate = Decimal(infra_rate) / Decimal("1000.0")
            return rate, rate, "local_infra_rate", False
        
        if is_production():
            logger.warning("Production mode enabled but DANA_COST_LOCAL_LLM_INFRA_PER_1K_TOKENS is not set.")
            estimated = True
        default_rate = Decimal("0.0000002")
        return default_rate, default_rate, "default_local_llm_rate", estimated

    # Cloud defaults
    if "gpt-4o-mini" in model_clean:
        return Decimal("0.00000015"), Decimal("0.00000060"), "default_rate", False
    elif "gpt-4o" in model_clean:
        return Decimal("0.000005"), Decimal("0.000015"), "default_rate", False
    elif "claude-3-5-sonnet" in model_clean:
        return Decimal("0.000003"), Decimal("0.000015"), "default_rate", False
    elif "claude-3-opus" in model_clean:
        return Decimal("0.000015"), Decimal("0.000075"), "default_rate", False

    default_rate = Decimal("0.0000002")
    return default_rate, default_rate, "default_llm_rate", False
