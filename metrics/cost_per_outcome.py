import os
import sys
import argparse
import asyncio
from decimal import Decimal
from typing import Dict, Any, Optional
from storage.repository import Repository

async def recompute_campaign_rollups(repository: Any, campaign_id: str) -> Dict[str, Dict[str, Any]]:
    """Query all call outcome costs and durations, save rollup records, and return the rollup dictionary."""
    # 1. Fetch all calls for campaign to map call_id -> duration
    calls = await repository.query_calls({"campaign_id": campaign_id})
    call_durations = {}
    for c in calls:
        cid = c.get("call_id")
        if cid:
            call_durations[cid] = float(c.get("duration_seconds") or 0.0)
            
    # 2. Fetch all call_outcome_costs for the campaign
    outcome_costs = await repository.query_call_outcome_costs({"campaign_id": campaign_id})
    
    # 3. Group by outcome
    by_outcome: Dict[str, list] = {}
    for oc in outcome_costs:
        out = oc.get("outcome") or "unknown"
        if out not in by_outcome:
            by_outcome[out] = []
        by_outcome[out].append(oc)
        
    rollups = {}
    for out, items in by_outcome.items():
        total_calls = len(items)
        total_duration = 0.0
        total_telephony = Decimal("0.0")
        total_stt = Decimal("0.0")
        total_llm = Decimal("0.0")
        total_tts = Decimal("0.0")
        total_gpu = Decimal("0.0")
        total_cost = Decimal("0.0")
        
        for oc in items:
            cid = oc.get("call_id")
            total_duration += call_durations.get(cid, 0.0)
            total_telephony += Decimal(str(oc.get("telephony_cost") or 0.0))
            total_stt += Decimal(str(oc.get("stt_cost") or 0.0))
            total_llm += Decimal(str(oc.get("llm_cost") or 0.0))
            total_tts += Decimal(str(oc.get("tts_cost") or 0.0))
            total_gpu += Decimal(str(oc.get("gpu_cost") or 0.0))
            total_cost += Decimal(str(oc.get("total_cost") or 0.0))
            
        avg_cost = total_cost / Decimal(total_calls) if total_calls > 0 else Decimal("0.0")
        
        # Save CampaignCostRollup
        await repository.save_campaign_cost_rollup(
            campaign_id=campaign_id,
            outcome=out,
            total_calls=total_calls,
            total_duration_seconds=total_duration,
            total_telephony_cost=total_telephony,
            total_stt_cost=total_stt,
            total_llm_cost=total_llm,
            total_tts_cost=total_tts,
            total_gpu_cost=total_gpu,
            total_cost=total_cost,
            average_call_cost=avg_cost
        )
        
        rollups[out] = {
            "total_calls": total_calls,
            "total_duration_seconds": total_duration,
            "total_telephony_cost": total_telephony,
            "total_stt_cost": total_stt,
            "total_llm_cost": total_llm,
            "total_tts_cost": total_tts,
            "total_gpu_cost": total_gpu,
            "total_cost": total_cost,
            "average_call_cost": avg_cost
        }
        
    return rollups


def calculate_campaign_metrics(rollups: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate aggregated cost KPIs across all outcomes."""
    total_campaign_cost = Decimal("0.0")
    total_completed_calls = 0
    total_connected_calls = 0
    total_connected_duration = 0.0
    
    count_transfers = 0
    count_qualified_transfers = 0
    count_callbacks = 0
    wasted_cost = Decimal("0.0")
    
    # Outcomes that count as connected (answered by human/agent)
    connected_outcomes = {"connected", "transferred", "qualified_transfer", "callback", "dnc", "disqualified", "wrong_number"}
    
    for outcome, data in rollups.items():
        outcome_cost = Decimal(str(data["total_cost"]))
        outcome_calls = data["total_calls"]
        outcome_duration = data["total_duration_seconds"]
        
        total_campaign_cost += outcome_cost
        total_completed_calls += outcome_calls
        
        if outcome in connected_outcomes:
            total_connected_calls += outcome_calls
            total_connected_duration += outcome_duration
            
        if outcome in ("transferred", "qualified_transfer"):
            count_transfers += outcome_calls
        if outcome == "qualified_transfer":
            count_qualified_transfers += outcome_calls
        if outcome == "callback":
            count_callbacks += outcome_calls
        if outcome in ("voicemail", "wrong_number"):
            wasted_cost += outcome_cost

    # Calculate metrics
    connected_minutes = total_connected_duration / 60.0
    cost_per_connected_minute = total_campaign_cost / Decimal(str(connected_minutes)) if connected_minutes > 0 else Decimal("0.0")
    cost_per_completed_call = total_campaign_cost / Decimal(total_completed_calls) if total_completed_calls > 0 else Decimal("0.0")
    cost_per_transfer = total_campaign_cost / Decimal(count_transfers) if count_transfers > 0 else Decimal("0.0")
    cost_per_qualified_transfer = total_campaign_cost / Decimal(count_qualified_transfers) if count_qualified_transfers > 0 else Decimal("0.0")
    cost_per_callback = total_campaign_cost / Decimal(count_callbacks) if count_callbacks > 0 else Decimal("0.0")

    return {
        "total_campaign_cost": total_campaign_cost,
        "total_completed_calls": total_completed_calls,
        "total_connected_calls": total_connected_calls,
        "total_connected_duration_seconds": total_connected_duration,
        "cost_per_connected_minute": cost_per_connected_minute,
        "cost_per_completed_call": cost_per_completed_call,
        "cost_per_transfer": cost_per_transfer,
        "cost_per_qualified_transfer": cost_per_qualified_transfer,
        "cost_per_callback": cost_per_callback,
        "wasted_cost_voicemail_wrong_number": wasted_cost,
        "count_transfers": count_transfers,
        "count_qualified_transfers": count_qualified_transfers,
        "count_callbacks": count_callbacks
    }


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Campaign Cost Per Outcome CLI Reporter")
    parser.add_argument("--campaign-id", required=True, help="Campaign ID to roll up")
    args = parser.parse_args()

    repository = Repository()
    try:
        rollups = await recompute_campaign_rollups(repository, args.campaign_id)
        if not rollups:
            print(f"No cost records found for campaign '{args.campaign_id}'")
            return 0
            
        metrics = calculate_campaign_metrics(rollups)
        
        # Print Outcome Breakdowns
        print(f"\n=======================================================")
        print(f"COST OUTCOME BREAKDOWN FOR CAMPAIGN: {args.campaign_id}")
        print(f"=======================================================")
        print(f"{'Outcome':<20} | {'Calls':<6} | {'Duration (s)':<12} | {'Total Cost (USD)':<16}")
        print(f"-------------------------------------------------------")
        for outcome, data in rollups.items():
            print(f"{outcome:<20} | {data['total_calls']:<6} | {data['total_duration_seconds']:<12.1f} | ${data['total_cost']:<15.4f}")
        print(f"-------------------------------------------------------")
        
        # Print Aggregated KPIs
        print(f"\n=======================================================")
        print(f"CAMPAIGN COST PERFORMANCE METRICS")
        print(f"=======================================================")
        print(f"Total Completed Calls:             {metrics['total_completed_calls']}")
        print(f"Total Campaign Cost:               ${metrics['total_campaign_cost']:.4f}")
        print(f"Cost Per Connected Minute:         ${metrics['cost_per_connected_minute']:.4f}")
        print(f"Cost Per Completed Call:           ${metrics['cost_per_completed_call']:.4f}")
        print(f"Cost Per Transfer:                 ${metrics['cost_per_transfer']:.4f}")
        print(f"Cost Per Qualified Transfer:       ${metrics['cost_per_qualified_transfer']:.4f}")
        print(f"Cost Per Callback:                 ${metrics['cost_per_callback']:.4f}")
        print(f"Wasted Cost (Voicemail/Wrong Num): ${metrics['wasted_cost_voicemail_wrong_number']:.4f}")
        print(f"=======================================================\n")
        
        return 0
    except Exception as e:
        print(f"Error compiling cost report: {e}", file=sys.stderr)
        return 1
    finally:
        await repository.close()


def main():
    sys.exit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
