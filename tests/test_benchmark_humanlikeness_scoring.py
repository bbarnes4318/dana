import pytest
from benchmarks.voice_platform_benchmark.score_humanlikeness import score_humanlikeness, detect_repetitions

def test_detect_repetitions():
    turns = [
        "Hello, how can I help you?",
        "Yes, that's correct.",
        "Hello, how can I help you?",  # Repetition
        "Goodbye"
    ]
    assert detect_repetitions(turns) == 1

def test_humanlikeness_perfect_score():
    turns = [
        "Hi this is Alex, checking if you're open.",
        "Okay, are you between forty and eighty-five?",
        "Great, and do you live independently?"
    ]
    res = score_humanlikeness(turns)
    assert res["bot_like_phrase_count"] == 0
    assert res["repetition_count"] == 0
    assert res["humanlikeness_score"] == 100.0

def test_humanlikeness_bot_phrase_penalties():
    turns = [
        "As an AI language model, I cannot help with that.",
        "This is an automated voice assistant."
    ]
    res = score_humanlikeness(turns)
    # "as an AI" -> 1
    # "assistant" (or "bot") -> "automated voice" contains no bot-like phrase but "assistant" doesn't trigger unless virtual assistant/assistant. Let's see:
    # BOT_LIKE_PHRASES has: "as an ai", "ai language model", "text-to-speech", "synthesized voice", "automated voice" (triggers on "automated voice assistant"), "virtual assistant", "computer-generated", "robot", "bot", "programmed", "system error", "processing your request".
    # So "As an AI language model" matches: "as an ai" and "ai language model". That's 2 counts.
    # "automated voice assistant" matches "automated voice". That's 1 count.
    # Total count = 3. Penalty = 3 * 20 = 60. Score = 100 - 60 = 40.
    assert res["bot_like_phrase_count"] >= 2
    assert res["humanlikeness_score"] < 100.0
