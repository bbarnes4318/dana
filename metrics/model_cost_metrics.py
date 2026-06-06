import math
import os
from decimal import Decimal
from typing import Optional, Any
from storage.repository import Repository
from metrics.rate_card import get_rate, get_llm_rates

# Default rates (snapshot snapshot/rate lookup based on names)
DEFAULT_DEEPGRAM_RATE = Decimal("0.000072")      # $0.0043 per minute -> $0.000072/sec
DEFAULT_ELEVENLABS_RATE = Decimal("0.0003")      # $0.0003 per character
DEFAULT_OPENAI_TTS_RATE = Decimal("0.000015")    # $0.000015 per character
DEFAULT_TELNYX_TELEPHONY_RATE = Decimal("0.01")  # $0.01 per minute

# Default LLM rates
DEFAULT_LLM_PROMPT_RATE = Decimal("0.0000002")       # $0.20/1M tokens (local Llama 8B)
DEFAULT_LLM_COMPLETION_RATE = Decimal("0.0000002")   # $0.20/1M tokens (local Llama 8B)


def estimate_llm_tokens(text: str) -> int:
    """Estimate token count for a text as len(text) / 4.0. Returns at least 1 if text is not empty."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4.0))


def get_stt_rate_and_source(provider: str) -> tuple[Decimal, str]:
    """Get STT rate per second and its source (kept for backward compatibility)."""
    env_rate = os.getenv("DANA_COST_STT_RATE_PER_SECOND")
    if env_rate:
        return Decimal(env_rate), "env_override"
        
    provider = provider.lower()
    if provider == "deepgram":
        return DEFAULT_DEEPGRAM_RATE, "default_rate"
    elif provider == "local" or provider == "whisper":
        infra_rate = os.getenv("DANA_COST_LOCAL_STT_INFRA_PER_MINUTE")
        if infra_rate:
            return Decimal(infra_rate) / Decimal("60.0"), "local_infra_rate"
        return Decimal("0.0"), "default_local_rate"
        
    return Decimal("0.0"), "unknown"


def get_llm_rates_and_source(model: str) -> tuple[Decimal, Decimal, str]:
    """Get LLM prompt and completion rates per token and its source (kept for backward compatibility)."""
    env_prompt = os.getenv("DANA_COST_LLM_PROMPT_RATE_PER_TOKEN")
    env_completion = os.getenv("DANA_COST_LLM_COMPLETION_RATE_PER_TOKEN")
    if env_prompt and env_completion:
        return Decimal(env_prompt), Decimal(env_completion), "env_override"
        
    model = model.lower()
    is_local = "llama" in model or "vllm" in model or "local" in model
    if is_local:
        infra_rate = os.getenv("DANA_COST_LOCAL_LLM_INFRA_PER_1K_TOKENS")
        if infra_rate:
            rate = Decimal(infra_rate) / Decimal("1000.0")
            return rate, rate, "local_infra_rate"
        return DEFAULT_LLM_PROMPT_RATE, DEFAULT_LLM_COMPLETION_RATE, "default_local_rate"
        
    if "gpt-4o-mini" in model:
        return Decimal("0.00000015"), Decimal("0.00000060"), "default_rate"
    elif "gpt-4o" in model:
        return Decimal("0.000005"), Decimal("0.000015"), "default_rate"
    elif "claude-3-5-sonnet" in model:
        return Decimal("0.000003"), Decimal("0.000015"), "default_rate"
    elif "claude-3-opus" in model:
        return Decimal("0.000015"), Decimal("0.000075"), "default_rate"
        
    return DEFAULT_LLM_PROMPT_RATE, DEFAULT_LLM_COMPLETION_RATE, "default_rate"


def get_tts_rate_and_source(provider: str) -> tuple[Decimal, str]:
    """Get TTS rate per character and its source (kept for backward compatibility)."""
    env_rate = os.getenv("DANA_COST_TTS_RATE_PER_CHARACTER")
    if env_rate:
        return Decimal(env_rate), "env_override"
        
    provider = provider.lower()
    if provider == "elevenlabs":
        return DEFAULT_ELEVENLABS_RATE, "default_rate"
    elif provider == "openai":
        return DEFAULT_OPENAI_TTS_RATE, "default_rate"
    elif provider in ("local", "kokoro", "bella"):
        infra_rate = os.getenv("DANA_COST_LOCAL_TTS_INFRA_PER_1K_CHARS")
        if infra_rate:
            return Decimal(infra_rate) / Decimal("1000.0"), "local_infra_rate"
        return Decimal("0.0"), "default_local_rate"
        
    return Decimal("0.0"), "unknown"


def get_telephony_rate_and_source(provider: str) -> tuple[Decimal, str]:
    """Get telephony rate per minute and its source (kept for backward compatibility)."""
    env_rate = os.getenv("DANA_COST_TELEPHONY_RATE_PER_MINUTE")
    if env_rate:
        return Decimal(env_rate), "env_override"
    return DEFAULT_TELNYX_TELEPHONY_RATE, "default_rate"


async def calculate_and_save_costs(
    repository: Repository,
    call_id: str,
    campaign_id: str,
    stt_provider: str,
    stt_seconds: float,
    llm_model: str,
    prompt_tokens: int,
    completion_tokens: int,
    tts_provider: str,
    tts_characters: int,
    telephony_provider: str,
    telephony_seconds: float,
    dry_run: bool = False,
    llm_tokens_estimated: bool = True
) -> Decimal:
    """Calculate and save cost records for a call, returning the total cost in Decimal."""
    # Resolve actual dynamic providers/models from ModelRouter
    routed_stt = None
    routed_llm = None
    routed_tts = None
    stt_reason = "normal"
    llm_reason = "normal"
    tts_reason = "normal"
    try:
        from routing.model_router import ModelRouter
        routed_stt, stt_reason = ModelRouter.get_last_decision(call_id, "stt")
        if routed_stt and routed_stt != "unknown":
            stt_provider = routed_stt
            
        routed_llm, llm_reason = ModelRouter.get_last_decision(call_id, "llm")
        if routed_llm == "openai":
            llm_model = "gpt-4o-mini"
            
        routed_tts, tts_reason = ModelRouter.get_last_decision(call_id, "tts")
        if routed_tts and routed_tts != "unknown":
            tts_provider = routed_tts
    except Exception as re:
        pass

    currency = os.getenv("DANA_COST_CURRENCY", "USD")
    total_cost = Decimal("0.0")

    # 1. Telephony component
    tele_rate_per_min, _, tele_source, tele_est = await get_rate(repository, telephony_provider, "telephony")
    tele_qty = Decimal(str(telephony_seconds))
    tele_cost = Decimal("0.0")
    if not dry_run and telephony_seconds > 0:
        tele_cost = (tele_qty / Decimal("60.0")) * tele_rate_per_min
    
    await repository.save_call_cost(
        call_id=call_id,
        campaign_id=campaign_id,
        component="telephony",
        provider=telephony_provider or "unknown",
        model="outbound_call",
        usage_unit="seconds",
        usage_quantity=tele_qty,
        unit_rate=tele_rate_per_min,
        estimated_cost=tele_cost,
        currency=currency,
        rate_source=tele_source,
        estimated=tele_est,
        dry_run=dry_run
    )
    total_cost += tele_cost

    # 2. STT component
    stt_rate_per_sec, _, stt_source, stt_est = await get_rate(repository, stt_provider, "stt")
    if stt_provider == "deepgram" and stt_reason not in ("normal", "local_forced"):
        stt_source = f"{stt_source} (fallback: {stt_reason})"
    stt_qty = Decimal(str(stt_seconds))
    stt_cost = Decimal("0.0")
    if not dry_run and stt_seconds > 0:
        stt_cost = stt_qty * stt_rate_per_sec
        
    await repository.save_call_cost(
        call_id=call_id,
        campaign_id=campaign_id,
        component="stt",
        provider=stt_provider or "unknown",
        model="transcription",
        usage_unit="seconds",
        usage_quantity=stt_qty,
        unit_rate=stt_rate_per_sec,
        estimated_cost=stt_cost,
        currency=currency,
        rate_source=stt_source,
        estimated=stt_est,
        dry_run=dry_run
    )
    total_cost += stt_cost

    # 3. LLM component (split input/output costs)
    llm_provider = "vllm" if "vllm" in llm_model.lower() or "llama" in llm_model.lower() or "local" in llm_model.lower() else "openai"
    llm_prompt_rate, llm_compl_rate, llm_source, llm_est = await get_llm_rates(repository, llm_provider, llm_model)
    if routed_llm == "openai" and llm_reason not in ("normal", "local_forced"):
        llm_source = f"{llm_source} (fallback: {llm_reason})"
    
    prompt_cost = Decimal("0.0")
    if not dry_run and prompt_tokens > 0:
        prompt_cost = Decimal(prompt_tokens) * llm_prompt_rate
        
    await repository.save_call_cost(
        call_id=call_id,
        campaign_id=campaign_id,
        component="llm",
        provider=llm_provider,
        model=llm_model or "unknown",
        usage_unit="prompt_tokens",
        usage_quantity=Decimal(prompt_tokens),
        unit_rate=llm_prompt_rate,
        estimated_cost=prompt_cost,
        currency=currency,
        rate_source=llm_source,
        estimated=llm_est or llm_tokens_estimated,
        dry_run=dry_run
    )
    total_cost += prompt_cost

    completion_cost = Decimal("0.0")
    if not dry_run and completion_tokens > 0:
        completion_cost = Decimal(completion_tokens) * llm_compl_rate
        
    await repository.save_call_cost(
        call_id=call_id,
        campaign_id=campaign_id,
        component="llm",
        provider=llm_provider,
        model=llm_model + "/completion",
        usage_unit="completion_tokens",
        usage_quantity=Decimal(completion_tokens),
        unit_rate=llm_compl_rate,
        estimated_cost=completion_cost,
        currency=currency,
        rate_source=llm_source,
        estimated=llm_est or llm_tokens_estimated,
        dry_run=dry_run
    )
    total_cost += completion_cost

    # 4. TTS component
    tts_rate_per_char, _, tts_source, tts_est = await get_rate(repository, tts_provider, "tts")
    if routed_tts != "local" and tts_reason not in ("normal", "local_forced"):
        tts_source = f"{tts_source} (fallback: {tts_reason})"
    tts_qty = Decimal(str(tts_characters))
    tts_cost = Decimal("0.0")
    if not dry_run and tts_characters > 0:
        tts_cost = tts_qty * tts_rate_per_char
        
    await repository.save_call_cost(
        call_id=call_id,
        campaign_id=campaign_id,
        component="tts",
        provider=tts_provider or "unknown",
        model="synthesis",
        usage_unit="characters",
        usage_quantity=tts_qty,
        unit_rate=tts_rate_per_char,
        estimated_cost=tts_cost,
        currency=currency,
        rate_source=tts_source,
        estimated=tts_est,
        dry_run=dry_run
    )
    total_cost += tts_cost

    return total_cost
