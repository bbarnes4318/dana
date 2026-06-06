"""Partial Intent Detector.

Classifies partial user transcripts into semantic intents using deterministic
regex rules and phrase matching to minimize latency.
"""

from __future__ import annotations

import re

class IntentClass:
    COMPLETE_ANSWER = "COMPLETE_ANSWER"
    STILL_THINKING = "STILL_THINKING"
    BACKCHANNEL_ONLY = "BACKCHANNEL_ONLY"
    INTERRUPTION = "INTERRUPTION"
    OBJECTION = "OBJECTION"
    CONFUSION = "CONFUSION"
    WRONG_NUMBER = "WRONG_NUMBER"
    DNC_STOP = "DNC_STOP"
    CALLBACK_REQUEST = "CALLBACK_REQUEST"
    TRANSFER_CONSENT_YES = "TRANSFER_CONSENT_YES"
    TRANSFER_CONSENT_NO = "TRANSFER_CONSENT_NO"
    PRICE_QUESTION = "PRICE_QUESTION"
    GOVERNMENT_QUESTION = "GOVERNMENT_QUESTION"
    BOT_OR_AI_QUESTION = "BOT_OR_AI_QUESTION"
    UNKNOWN = "UNKNOWN"

DNC_PATTERNS = [
    r"\b(do not call|dnc|remove me|stop calling|remove my number|stop calling me|put me on your do not call list)\b",
]

WRONG_NUMBER_PATTERNS = [
    r"\b(wrong number|not the person|not me|no one here by that name|you have the wrong person)\b",
]

CALLBACK_PATTERNS = [
    r"\b(call (me )?later|call (me )?back|call me tomorrow|busy|talk later|not a good time)\b",
]

OBJECTION_PATTERNS = [
    r"\b(not interested|don't want it|no thanks|already have it|have coverage|too expensive|can't afford|no money|who is this|what is this about)\b",
]

CONFUSION_PATTERNS = [
    r"\b(i don't understand|what do you mean|confused|what's going on)\b",
]

TRANSFER_YES_PATTERNS = [
    r"\b(yes|sure|yeah|go ahead|transfer me|connect me|ok|okay|fine|speak to someone|talk to someone)\b",
]

TRANSFER_NO_PATTERNS = [
    r"\b(no|don't transfer|no thanks|i don't want to talk to anyone|not interested in talking)\b",
]

PRICE_PATTERNS = [
    r"\b(price|premium|cost|how much|rate|dollars|payment)\b",
]

GOVERNMENT_PATTERNS = [
    r"\b(government|state|medicare|social security|obama|state program)\b",
]

BOT_OR_AI_PATTERNS = [
    r"\b(are you a robot|are you ai|are you real|are you a computer|are you an automated voice)\b",
]

BACKCHANNEL_PATTERNS = [
    r"^(uh huh|ah|oh|uh|um|huh|hm|hmm|yeah|ok|okay|gotcha)$",
]

STILL_THINKING_PATTERNS = [
    r"\b(uh|um|hmm|let's see|hold on|just a second|wait|give me a moment|thinking|so like|and uh)\b$",
]

INTERRUPTION_PATTERNS = [
    r"\b(wait|hold on|stop|excuse me|hang on)\b",
]


def classify_partial_intent(text: str, stage: str = "") -> str:
    """Classifies a partial transcript string using deterministic rules."""
    text_clean = text.strip().lower()
    if not text_clean:
        return IntentClass.UNKNOWN

    # Remove trailing punctuation for clean backchannel check
    text_no_punc = re.sub(r'[^\w\s]', '', text_clean).strip()

    # DNC / Stop Check
    for pat in DNC_PATTERNS:
        if re.search(pat, text_clean):
            return IntentClass.DNC_STOP

    # Wrong Number
    for pat in WRONG_NUMBER_PATTERNS:
        if re.search(pat, text_clean):
            return IntentClass.WRONG_NUMBER

    # Callback Request
    for pat in CALLBACK_PATTERNS:
        if re.search(pat, text_clean):
            return IntentClass.CALLBACK_REQUEST

    # Still Thinking (run early so continuation/hesitation overrides yes/no/objection matches)
    for pat in STILL_THINKING_PATTERNS:
        if re.search(pat, text_clean):
            return IntentClass.STILL_THINKING

    # Trailing continuation words
    if text_clean.endswith((" but", " and", " because", " so", " or", " if", " when")):
        return IntentClass.STILL_THINKING

    # Bot or AI question
    for pat in BOT_OR_AI_PATTERNS:
        if re.search(pat, text_clean):
            return IntentClass.BOT_OR_AI_QUESTION

    # Price question
    for pat in PRICE_PATTERNS:
        if re.search(pat, text_clean):
            return IntentClass.PRICE_QUESTION

    # Government question
    for pat in GOVERNMENT_PATTERNS:
        if re.search(pat, text_clean):
            return IntentClass.GOVERNMENT_QUESTION

    # Interruption check
    for pat in INTERRUPTION_PATTERNS:
        if re.search(pat, text_clean):
            return IntentClass.INTERRUPTION

    # Objection
    for pat in OBJECTION_PATTERNS:
        if re.search(pat, text_clean):
            return IntentClass.OBJECTION

    # Confusion
    for pat in CONFUSION_PATTERNS:
        if re.search(pat, text_clean):
            return IntentClass.CONFUSION

    # Stage-aware classification for Yes/No
    stage_upper = stage.upper()
    if stage_upper == "TRANSFER_CONSENT":
        for pat in TRANSFER_YES_PATTERNS:
            if re.search(pat, text_clean):
                return IntentClass.TRANSFER_CONSENT_YES
        for pat in TRANSFER_NO_PATTERNS:
            if re.search(pat, text_clean):
                return IntentClass.TRANSFER_CONSENT_NO
    else:
        # General yes/no patterns map to COMPLETE_ANSWER
        for pat in TRANSFER_YES_PATTERNS:
            if re.search(pat, text_clean):
                return IntentClass.COMPLETE_ANSWER
        for pat in TRANSFER_NO_PATTERNS:
            if re.search(pat, text_clean):
                return IntentClass.COMPLETE_ANSWER

    # Backchannel Only
    if re.match(BACKCHANNEL_PATTERNS[0], text_no_punc):
        return IntentClass.BACKCHANNEL_ONLY

    # Ends with terminal punctuation -> COMPLETE_ANSWER
    if text.endswith((".", "!", "?")):
        return IntentClass.COMPLETE_ANSWER

    return IntentClass.UNKNOWN
