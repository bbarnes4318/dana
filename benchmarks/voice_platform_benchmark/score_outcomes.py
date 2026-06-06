from typing import Dict, Any

def score_outcome(
    actual_outcome: str,
    expected_outcome: str
) -> Dict[str, Any]:
    """
    Validates the final call outcome.
    Returns:
        Dict containing:
            - outcome_passed: bool
            - outcome_score: float (100.0 if passed, else lower)
    """
    # Normalize outcomes
    act = actual_outcome.lower().strip()
    exp = expected_outcome.lower().strip()
    
    # Map synonyms
    synonyms = {
        "transfer": "transferred",
        "end": "ended",
        "hangup": "ended",
        "remove_me": "dnc",
        "dnc": "dnc"
    }
    
    act = synonyms.get(act, act)
    exp = synonyms.get(exp, exp)
    
    passed = act == exp
    
    score = 100.0 if passed else 50.0
    
    return {
        "outcome_passed": passed,
        "outcome_score": score
    }
