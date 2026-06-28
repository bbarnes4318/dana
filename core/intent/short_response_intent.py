"""Intent classification for short caller utterances in the direct-response path.

Classifies short inputs like 'yeah', 'no', 'who is this' to decide the state
machine transitions and LLM generation instructions.
"""

from __future__ import annotations
import re

# DNC / stop patterns
_DNC_PATTERNS = [
    re.compile(r"\bdo\s*n[o']?t\s+call\b", re.IGNORECASE),
    re.compile(r"\bdon't\s+call\b", re.IGNORECASE),
    re.compile(r"\bstop\s+calling\b", re.IGNORECASE),
    re.compile(r"\bremove\s+(?:me|my\s+number)\b", re.IGNORECASE),
    re.compile(r"\btake\s+me\s+off\b", re.IGNORECASE),
    re.compile(r"\bput\s+me\s+on\s+the\s+(?:do\s+not\s+call|dnc)\b", re.IGNORECASE),
    re.compile(r"\bnever\s+call\b", re.IGNORECASE),
    re.compile(r"\bunsubscribe\b", re.IGNORECASE),
    re.compile(r"^stop$", re.IGNORECASE),
]

# Wrong-number patterns
_WRONG_NUMBER_PATTERNS = [
    re.compile(r"\bwrong\s+number\b", re.IGNORECASE),
    re.compile(r"\bwrong\s+person\b", re.IGNORECASE),
    re.compile(r"\bnot\s+me\b", re.IGNORECASE),
    re.compile(r"\bnot\s+here\b", re.IGNORECASE),
    re.compile(r"\bno\s+this\s+is\s+not\b", re.IGNORECASE),
]

# Confusion patterns
_CONFUSION_PATTERNS = [
    re.compile(r"\bwho\s+is\s+this\b", re.IGNORECASE),
    re.compile(r"\bwho\s+are\s+you\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+is\s+this\b", re.IGNORECASE),
    re.compile(r"\bwhat's\s+this\s+about\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+is\s+this\s+about\b", re.IGNORECASE),
    re.compile(r"\bwhy\s+are\s+you\s+calling\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+do\s+you\s+want\b", re.IGNORECASE),
    re.compile(r"\bwho's\s+calling\b", re.IGNORECASE),
    re.compile(r"\bwho\s+called\s+me\b", re.IGNORECASE),
    re.compile(r"\bwhere\s+are\s+you\s+going\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+are\s+you\s+doing\b", re.IGNORECASE),
    re.compile(r"\bwhere\s+is\s+this\s+going\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+are\s+you\s+calling\s+for\b", re.IGNORECASE),
    re.compile(r"\bwhy\s+call\s+me\b", re.IGNORECASE),
    re.compile(r"\bwho\s+the\s+hell\s+is\s+this\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+did\s+you\s+call\s+for\b", re.IGNORECASE),
]

# Repeat request patterns
_REPEAT_PATTERNS = [
    re.compile(r"\brepeat\b", re.IGNORECASE),
    re.compile(r"\bsay\s+again\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+did\s+you\s+say\b", re.IGNORECASE),
    re.compile(r"\bpardon\b", re.IGNORECASE),
    re.compile(r"\bcome\s+again\b", re.IGNORECASE),
    re.compile(r"\bcan\s+you\s+say\s+that\s+again\b", re.IGNORECASE),
    re.compile(r"\bwhat's\s+that\b", re.IGNORECASE),
    re.compile(r"\bexcuse\s+me\b", re.IGNORECASE),
]

# Refusal patterns
_REFUSAL_PATTERNS = [
    re.compile(r"\bno\b", re.IGNORECASE),
    re.compile(r"\bnope\b", re.IGNORECASE),
    re.compile(r"\bnah\b", re.IGNORECASE),
    re.compile(r"\bnot\s+really\b", re.IGNORECASE),
    re.compile(r"\bnegative\b", re.IGNORECASE),
    re.compile(r"\bno\s+thanks\b", re.IGNORECASE),
    re.compile(r"\bno\s+thank\s+you\b", re.IGNORECASE),
    re.compile(r"\bi\s+don't\s+think\s+so\b", re.IGNORECASE),
    re.compile(r"\bnot\s+interested\b", re.IGNORECASE),
    re.compile(r"\bnot\s+right\s+now\b", re.IGNORECASE),
    re.compile(r"\balready\s+have\b", re.IGNORECASE),
    re.compile(r"\bbusy\b", re.IGNORECASE),
    re.compile(r"\bcan't\s+talk\b", re.IGNORECASE),
    re.compile(r"\bgo\s+away\b", re.IGNORECASE),
    re.compile(r"\bleave\s+me\s+alone\b", re.IGNORECASE),
    re.compile(r"\bexpensive\b", re.IGNORECASE),
    re.compile(r"\bcost\b", re.IGNORECASE),
    re.compile(r"\bprice\b", re.IGNORECASE),
    re.compile(r"\bafford\b", re.IGNORECASE),
    re.compile(r"\bmoney\b", re.IGNORECASE),
    re.compile(r"\bbudget\b", re.IGNORECASE),
    re.compile(r"\bfixed\s+income\b", re.IGNORECASE),
]

# Agreement patterns
_AGREEMENT_PATTERNS = [
    re.compile(r"\byes\b", re.IGNORECASE),
    re.compile(r"\byeah\b", re.IGNORECASE),
    re.compile(r"\byep\b", re.IGNORECASE),
    re.compile(r"\byup\b", re.IGNORECASE),
    re.compile(r"\bsure\b", re.IGNORECASE),
    re.compile(r"\babsolutely\b", re.IGNORECASE),
    re.compile(r"\bcorrect\b", re.IGNORECASE),
    re.compile(r"\bright\b", re.IGNORECASE),
    re.compile(r"\baffirmative\b", re.IGNORECASE),
    re.compile(r"\bof\s+course\b", re.IGNORECASE),
    re.compile(r"\bdefinitely\b", re.IGNORECASE),
    re.compile(r"\byou\s+bet\b", re.IGNORECASE),
    re.compile(r"\buh\s*huh\b", re.IGNORECASE),
    re.compile(r"\bokay?\b", re.IGNORECASE),
    re.compile(r"\bmhm\b", re.IGNORECASE),
    re.compile(r"\bmm-hmm\b", re.IGNORECASE),
    re.compile(r"\bagree\b", re.IGNORECASE),
    re.compile(r"\bgo\s+ahead\b", re.IGNORECASE),
]

# Filler patterns
_FILLER_PATTERNS = [
    re.compile(r"^\s*uh+\s*$", re.IGNORECASE),
    re.compile(r"^\s*um+\s*$", re.IGNORECASE),
    re.compile(r"^\s*hello+\s*[?.]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*hi+\s*[?.]?\s*$", re.IGNORECASE),
    re.compile(r"\bis\s+anyone\s+there\b", re.IGNORECASE),
    re.compile(r"\bwho's\s+there\b", re.IGNORECASE),
]

# Off-topic question patterns (generic "what/how/who/why/when/where" questions not matching confusion/repeat/etc.)
_QUESTION_WORDS = [
    re.compile(r"\bwhat\b", re.IGNORECASE),
    re.compile(r"\bhow\b", re.IGNORECASE),
    re.compile(r"\bwho\b", re.IGNORECASE),
    re.compile(r"\bwhy\b", re.IGNORECASE),
    re.compile(r"\bwhen\b", re.IGNORECASE),
    re.compile(r"\bwhere\b", re.IGNORECASE),
]


def classify_intent(text: str) -> str:
    """Classify the intent of a short caller utterance.

    Args:
        text: Raw text transcript.

    Returns:
        Categorized intent string: 'dnc', 'wrong_number', 'confusion',
        'repeat', 'refusal', 'agreement', 'filler', 'off_topic', or 'normal'.
    """
    if not text:
        return "filler"

    cleaned = text.strip()
    cleaned_lower = cleaned.lower()

    # 1. DNC / Stop
    for pat in _DNC_PATTERNS:
        if pat.search(cleaned):
            return "dnc"

    # 2. Wrong Number
    for pat in _WRONG_NUMBER_PATTERNS:
        if pat.search(cleaned):
            return "wrong_number"

    # 3. Confusion
    for pat in _CONFUSION_PATTERNS:
        if pat.search(cleaned):
            return "confusion"

    # 4. Repeat request
    for pat in _REPEAT_PATTERNS:
        if pat.search(cleaned):
            return "repeat"

    # 5. Refusal
    for pat in _REFUSAL_PATTERNS:
        if pat.search(cleaned):
            return "refusal"

    # 6. Agreement
    for pat in _AGREEMENT_PATTERNS:
        if pat.search(cleaned):
            return "agreement"

    # 7. Filler
    for pat in _FILLER_PATTERNS:
        if pat.search(cleaned):
            return "filler"

    # 8. Off-topic questions (starts with question words or contains question mark)
    if "?" in cleaned or any(pat.search(cleaned_lower) for pat in _QUESTION_WORDS):
        return "off_topic"

    return "normal"
