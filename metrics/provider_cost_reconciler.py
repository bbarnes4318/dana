import os
import logging
from decimal import Decimal
from typing import Optional, Any, Dict
from metrics.rate_card import get_rate, get_llm_rates
from routing.model_router import ModelRouter

logger = logging.getLogger(__name__)

async def reconcile_call_costs(
    repository: Any,
    call_id: str,
    campaign_id: str,
    duration_seconds: float,
    stt_seconds: float,
    prompt_tokens: int,
    completion_tokens: int,
    tts_characters: int,
    outcome: str
) -> Dict[str, Any]:
    """Reconcile dynamic provider routing decisions and allocate exact component costs.
    
    Saves ProviderDecision logs and the overall CallOutcomeCost breakdown.
    
    Returns:
        dict: Breakdown of resolved costs.
    """
    # 1. Resolve Telephony Cost
    telephony_provider = "telnyx"
    telephony_rate, _, telephony_src, tele_est = await get_rate(repository, telephony_provider, "telephony")
    tele_qty = Decimal(str(max(0.0, duration_seconds)))
    telephony_cost = (tele_qty / Decimal("60.0")) * telephony_rate
    
    # 2. Resolve LiveKit Cost
    livekit_provider = "livekit"
    livekit_rate, _, livekit_src, lk_est = await get_rate(repository, livekit_provider, "livekit")
    livekit_cost = (tele_qty / Decimal("60.0")) * livekit_rate
    
    # Combined Telephony/Infra Cost
    total_telephony_cost = telephony_cost + livekit_cost
    telephony_estimated = tele_est or lk_est

    # 3. Resolve STT Provider & Cost
    stt_provider, stt_reason = ModelRouter.get_last_decision(call_id, "stt")
    # Save ProviderDecision
    await repository.save_provider_decision(
        call_id=call_id,
        component="stt",
        selected_provider=stt_provider,
        decision_reason=stt_reason
    )
    stt_rate, _, stt_src, stt_est = await get_rate(repository, stt_provider, "stt")
    stt_qty = Decimal(str(max(0.0, stt_seconds)))
    stt_cost = stt_qty * stt_rate

    # 4. Resolve LLM Provider & Cost (split prompt & completion)
    llm_provider, llm_reason = ModelRouter.get_last_decision(call_id, "llm")
    await repository.save_provider_decision(
        call_id=call_id,
        component="llm",
        selected_provider=llm_provider,
        decision_reason=llm_reason
    )
    llm_prompt_rate, llm_compl_rate, llm_src, llm_est = await get_llm_rates(repository, llm_provider, model=llm_provider)
    llm_cost = (Decimal(prompt_tokens) * llm_prompt_rate) + (Decimal(completion_tokens) * llm_compl_rate)

    # 5. Resolve TTS Provider & Cost
    tts_provider, tts_reason = ModelRouter.get_last_decision(call_id, "tts")
    await repository.save_provider_decision(
        call_id=call_id,
        component="tts",
        selected_provider=tts_provider,
        decision_reason=tts_reason
    )
    tts_rate, _, tts_src, tts_est = await get_rate(repository, tts_provider, "tts")
    tts_qty = Decimal(str(max(0, tts_characters)))
    tts_cost = tts_qty * tts_rate

    # 6. Resolve GPU Cost (Sum of all GPU allocations for this call)
    gpu_cost = Decimal("0.0")
    try:
        allocations = await repository.query_gpu_runtime_allocations({"call_id": call_id})
        for alloc in allocations:
            gpu_cost += Decimal(str(alloc.get("allocated_cost") or 0.0))
    except Exception as e:
        logger.error(f"Error querying GPU allocations: {e}")

    # Determine if any part of the call cost resolution was estimated
    is_estimated = telephony_estimated or stt_est or llm_est or tts_est

    total_cost = total_telephony_cost + stt_cost + llm_cost + tts_cost + gpu_cost

    # Save CallOutcomeCost
    await repository.save_call_outcome_cost(
        call_id=call_id,
        campaign_id=campaign_id,
        outcome=outcome,
        telephony_cost=total_telephony_cost,
        stt_cost=stt_cost,
        llm_cost=llm_cost,
        tts_cost=tts_cost,
        gpu_cost=gpu_cost,
        total_cost=total_cost,
        is_estimated=is_estimated
    )

    return {
        "call_id": call_id,
        "campaign_id": campaign_id,
        "outcome": outcome,
        "telephony_cost": total_telephony_cost,
        "stt_cost": stt_cost,
        "llm_cost": llm_cost,
        "tts_cost": tts_cost,
        "gpu_cost": gpu_cost,
        "total_cost": total_cost,
        "is_estimated": is_estimated
    }
