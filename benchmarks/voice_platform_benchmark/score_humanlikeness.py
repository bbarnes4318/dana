import re
from typing import List, Dict, Any

BOT_LIKE_PHRASES = [
    r"\bas\s+an\s+ai\b",
    r"\bai\s+language\s+model\b",
    r"\btext-to-speech\b",
    r"\bsynthesized\s+voice\b",
    r"\bautomated\s+voice\b",
    r"\bvirtual\s+assistant\b",
    r"\bcomputer-generated\b",
    r"\brobot\b",
    r"\bbot\b",
    r"\bprogrammed\b",
    r"\bsystem\s+error\b",
    r"\bprocessing\s+your\s+request\b",
]

def detect_repetitions(text_list: List[str]) -> int:
    """Detects simple phrase repetitions or identical consecutive turns."""
    repetitions = 0
    seen = set()
    for text in text_list:
        clean = re.sub(r"[^\w\s]", "", text.lower()).strip()
        if not clean:
            continue
        # Check if we saw the exact same phrase earlier in the call
        if clean in seen:
            repetitions += 1
        seen.add(clean)
    return repetitions

def score_humanlikeness(
    agent_turns: List[str],
) -> Dict[str, Any]:
    """
    Evaluates humanlikeness based on agent utterances.
    Returns:
        Dict containing:
            - bot_like_phrase_count
            - repetition_count
            - avg_words_per_turn
            - humanlikeness_score: 0.0 - 100.0
    """
    if not agent_turns:
        return {
            "bot_like_phrase_count": 0,
            "repetition_count": 0,
            "avg_words_per_turn": 0.0,
            "humanlikeness_score": 100.0
        }
        
    bot_phrase_count = 0
    total_words = 0
    
    for turn in agent_turns:
        turn_lower = turn.lower()
        for pattern in BOT_LIKE_PHRASES:
            if re.search(pattern, turn_lower):
                bot_phrase_count += len(re.findall(pattern, turn_lower))
        total_words += len(turn.split())
        
    avg_words = total_words / len(agent_turns)
    rep_count = detect_repetitions(agent_turns)
    
    # Calculate score
    score = 100.0
    
    # Bot-like phrase penalties
    score -= bot_phrase_count * 20.0
    
    # Repetition penalties
    score -= rep_count * 15.0
    
    # Turn length penalties (too long sounds like a robotic script / reading essay)
    if avg_words > 45.0:
        score -= 15.0
    if avg_words > 60.0:
        score -= 15.0
        
    # Extremely short turns might sound robotic/unresponsive if it's the average
    if avg_words < 3.0:
        score -= 10.0
        
    score = max(0.0, min(100.0, score))
    
    return {
        "bot_like_phrase_count": bot_phrase_count,
        "repetition_count": rep_count,
        "avg_words_per_turn": round(avg_words, 2),
        "humanlikeness_score": round(score, 2)
    }
