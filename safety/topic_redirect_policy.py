"""Topic redirect policy for handling divergent conversation topics.

Detects if the prospect has diverted the conversation to politics, weather,
sports, personal questions, jokes, AI/bot questions, or other irrelevant topics,
and provides a safe, compliant, one-sentence redirect response based on the
current call stage.
"""

from __future__ import annotations

import re
from typing import Optional
from core.call_state import CallStage

# Divergent topic classification patterns
_PATTERNS = {
    "politics": re.compile(
        r"\b(politics|political|president|trump|biden|harris|election|elections|vote|votes|voting|senate|congress|republican|republicans|democrat|democrats)\b",
        re.IGNORECASE,
    ),
    "weather": re.compile(
        r"\b(weather|rain|raining|rainy|sun|sunny|sunshine|snow|snowing|snowy|temperature|temp|forecast|wind|windy|storm|stormy|hurricane|tornado|hot|cold|cloudy|degrees)\b",
        re.IGNORECASE,
    ),
    "sports": re.compile(
        r"\b(sports|football|basketball|baseball|soccer|nfl|nba|mlb|game|games|match|matches|score|scores|team|teams|super\s*bowl|playoffs)\b",
        re.IGNORECASE,
    ),
    "personal": re.compile(
        r"\b(are\s+you\s+married|how\s+old\s+are\s+you|where\s+do\s+you\s+live|what\s+is\s+your\s+name|what's\s+your\s+name|do\s+you\s+have\s+kids|are\s+you\s+single|your\s+hobbies|who\s+are\s+you|do\s+you\s+have\s+a\s+husband|do\s+you\s+have\s+a\s+wife|your\s+age|you\s+live\s+in|you\s+born|are\s+you\s+a\s+man|are\s+you\s+a\s+woman)\b",
        re.IGNORECASE,
    ),
    "jokes": re.compile(
        r"\b(joke|jokes|funny|laugh|laughs|laughing)\b",
        re.IGNORECASE,
    ),
    "ai_bot": re.compile(
        r"\b(robot|robots|bot|bots|ai|machine|machines|human|humans|recording|recordings|computer|computers|software|assistant|assistants|real\s+person|are\s+you\s+real|you\s+real)\b",
        re.IGNORECASE,
    ),
    "irrelevant": re.compile(
        r"\b(movie|movies|music|song|songs|food|eat|eating|recipe|recipes|book|books|read|reading|travel|traveling|vacation|vacations|holiday|holidays)\b",
        re.IGNORECASE,
    ),
}

# Stage-specific redirect responses (strictly 1 sentence each, compliant with no forbidden phrases)
_REDIRECT_RESPONSES = {
    CallStage.OPENING: "I understand, but I just want to keep this quick — are you the main decision maker for the household?",
    CallStage.INTEREST_CHECK: "I understand, but let's bring this back to the review — are you open to reviewing some options for final expense?",
    CallStage.AGE_RANGE: "I hear you, but let's bring this back to the review — are you between forty and eighty-five?",
    CallStage.LIVING_SITUATION: "I understand, but I just want to keep this quick — are you still living independently, not in a nursing home?",
    CallStage.DECISION_MAKER: "I hear you, but I just need to keep this quick — are you the main financial decision maker in your household?",
    CallStage.TRANSFER_CONSENT: "I understand, but I just need to keep this quick — are you open to reviewing this with someone licensed?",
    CallStage.TRANSFER_READY: "I hear you, but please hold on just one second while I get someone licensed on the line?",
    CallStage.CALLBACK: "I understand, but would later today or tomorrow work better for us to call you back?",
    CallStage.DNC: "Understood, I will make a note of that and take care.",
    CallStage.DISQUALIFIED: "Understood, since these options usually fit people in a different situation, I won't keep you.",
    CallStage.END: "I understand, thank you for your time and have a great day.",
}

# Default response to use if a stage is not explicitly mapped or CallStage.ANSWERED
_DEFAULT_REDIRECT = "I hear you, but let's bring this back to the review — are you open to reviewing some options for final expense?"


class TopicRedirectPolicy:
    """Policy for checking utterances for divergent topics and returning safe redirect responses."""

    def __init__(self) -> None:
        self.patterns = _PATTERNS
        self.responses = _REDIRECT_RESPONSES
        self.default_redirect = _DEFAULT_REDIRECT

    def detect_divergent_topic(self, utterance: str) -> Optional[str]:
        """Detects if the utterance is a divergent topic.

        Returns the matched category name (e.g. "politics", "weather") if matched,
        otherwise None.
        """
        cleaned = utterance.strip()
        if not cleaned:
            return None

        # Clean trailing question marks/punctuation for regex matching
        cleaned = re.sub(r"[^\w\s]", "", cleaned)

        for category, pattern in self.patterns.items():
            if pattern.search(cleaned):
                return category

        return None

    def get_redirect_response(self, stage: CallStage) -> str:
        """Returns the canonical redirect response for the current stage."""
        return self.responses.get(stage, self.default_redirect)
