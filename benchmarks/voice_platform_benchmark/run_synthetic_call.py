import random
from typing import List, Dict, Any

from benchmarks.voice_platform_benchmark.metrics_schema import Slotargets
from latency_metrics import LatencyRecorder

# Set of standard agent responses for different stages of the qualification script
qualification_flow = {
    "opening": "Hi, this is Alex with American Beneficiary. I was checking if you're still open to looking at final expense options.",
    "age_range": "Okay. First thing, just so I know this applies — are you between forty and eighty-five?",
    "living_situation": "Okay. And you’re living independently, right, not in a nursing home or assisted living?",
    "decision_maker": "Okay. And you handle your own financial decisions, correct?",
    "transfer_consent": "Would it be okay if I connect you with a licensed agent who can review the details?",
    "transfer_ready": "Great, I’ll connect you now."
}

def simulate_latency_recorder(
    provider_config: Dict[str, Any]
) -> Dict[str, float]:
    """
    Simulates LatencyRecorder events and computes durations in ms.
    Adds a small amount of random variance to the baseline provider profile latencies.
    """
    p50_base = provider_config.get("p50_turn_latency_ms", 400.0)
    llm_base = provider_config.get("llm_first_token_ms", 200.0)
    tts_base = provider_config.get("tts_first_audio_ms", 150.0)
    barge_base = provider_config.get("barge_in_stop_audio_ms", 150.0)
    
    # Introduce random variance of +/- 10%
    variance_factor = random.uniform(0.9, 1.1)
    
    turn_lat = p50_base * variance_factor
    llm_lat = llm_base * variance_factor
    tts_lat = tts_base * variance_factor
    barge_lat = barge_base * variance_factor
    
    # Return a format compatible with LatencyRecorder.to_dict()
    return {
        "turn_response_latency": round(turn_lat, 2),
        "llm_first_token_latency": round(llm_lat, 2),
        "tts_synthesis_start_latency": round(tts_lat, 2),
        "barge_in_stop_audio_latency": round(barge_lat, 2)
    }

def run_synthetic_call(
    provider_id: str,
    scenario_id: str,
    provider_config: Dict[str, Any],
    scenario_config: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Simulates a synthetic call turn-by-turn.
    Generates realistic competitor or Dana agent responses, introducing compliance failures
    or latency SLO breaches according to provider characteristics.
    """
    transcript: List[Dict[str, Any]] = []
    
    # Starting prospect utterance
    prospect_utterance = scenario_config.get("starting_utterance", "Hello?")
    transcript.append({
        "speaker": "prospect",
        "text": prospect_utterance,
        "tool": None,
        "stage": "opening",
        "metadata": {}
    })
    
    # Simulated conversation state
    current_stage = "opening"
    stages_order = ["opening", "age_range", "living_situation", "decision_maker", "transfer_consent", "transfer_ready"]
    stage_idx = 0
    
    # Determine behavior/compliance characteristics based on provider profile
    fail_rate = provider_config.get("compliance_hard_fail_rate", 0.0)
    is_failing_compliance = random.random() < fail_rate
    
    # We will simulate up to 8 turns
    for turn_num in range(8):
        # 1. Dana / Agent Turn
        latency_data = simulate_latency_recorder(provider_config)
        agent_text = ""
        tool = None
        
        # Scenario-specific overrides or general flow
        if scenario_id == "dnc":
            agent_text = "Understood. I'll make sure you're not contacted again. Take care."
            stage = "dnc"
            tool = "mark_dnc"
        elif scenario_id == "wrong_number":
            agent_text = "Sorry about that. I'll mark this as the wrong number. Take care."
            stage = "end"
            tool = "mark_wrong_number"
        elif scenario_id == "busy" or scenario_id == "wants_callback":
            if turn_num == 0:
                agent_text = "No problem. What time would work better for a quick callback?"
                stage = "callback"
            else:
                agent_text = "Perfect. I'll have the licensed agent try you then. Take care."
                stage = "end"
                tool = "schedule_callback"
        elif scenario_id == "not_interested":
            agent_text = "I understand. I'll remove you from our outreach list. Have a good day."
            stage = "end"
        elif scenario_id == "refuses_age":
            agent_text = "I understand, but we must verify that you're in the eligible age group before we can proceed. Have a good day."
            stage = "end"
        else:
            # Standard qualification flow
            stage = stages_order[stage_idx]
            agent_text = qualification_flow.get(stage, "Hello?")
            
            # Inject compliance failure if simulated provider is failing compliance
            if is_failing_compliance and stage_idx == 1:
                # Competitor makes a forbidden claim
                failures = [
                    "We can get you pre-approved for $29 per month guaranteed.",
                    "I am the licensed agent here in your state.",
                    "This is the official government benefit program.",
                    "Your approval is completely guaranteed, no matter what."
                ]
                agent_text = random.choice(failures)
                is_failing_compliance = False  # Only fail once
                
            if stage == "transfer_ready":
                tool = "feTransfer"
                
            # Advance stage index for standard flow
            if stage_idx < len(stages_order) - 1:
                stage_idx += 1
                
        # Simulate transfer without consent scenario
        if scenario_id == "wants_agent" and stage_idx < len(stages_order) - 1:
            # Demands agent immediately -> skip straight to transfer
            agent_text = "Okay, connecting you to a licensed agent now."
            stage = "transfer_ready"
            tool = "feTransfer"
            
        # Add agent turn
        transcript.append({
            "speaker": "dana",
            "text": agent_text,
            "tool": tool,
            "stage": stage,
            "metadata": {"durations": latency_data}
        })
        
        # Check termination
        if stage in ("end", "dnc") or tool == "feTransfer" or "Take care" in agent_text or "Have a good day" in agent_text:
            break
            
        # 2. Prospect Turn
        prospect_text = "Yes"
        if scenario_id == "asks_price" and turn_num == 0:
            prospect_text = "How much does it cost?"
        elif scenario_id == "asks_government" and turn_num == 0:
            prospect_text = "Are you with the government?"
        elif scenario_id == "scam_concern" and turn_num == 0:
            prospect_text = "Is this a scam?"
        elif scenario_id == "confused_senior":
            if turn_num == 0:
                prospect_text = "What is this? Can you repeat that?"
            elif stage == "age_range":
                prospect_text = "Oh, okay. Yes, I am 70."
            elif stage == "living_situation":
                prospect_text = "Yes, I live in my own home."
            elif stage == "decision_maker":
                prospect_text = "Yes, I handle my own money."
            elif stage == "transfer_consent":
                prospect_text = "Okay, go ahead."
            else:
                prospect_text = "Yes"
        elif scenario_id == "barge_in" and turn_num == 1:
            prospect_text = "Wait, hold on, stop talking for a second."
        elif stage == "transfer_consent":
            prospect_text = "Sure, connect me."
            
        transcript.append({
            "speaker": "prospect",
            "text": prospect_text,
            "tool": None,
            "stage": stage,
            "metadata": {}
        })
        
    return transcript
