"""Direct response validation and compliance guardrails for live phone output.
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional

@dataclass
class ValidationResult:
    is_valid: bool
    reason: Optional[str] = None


# Generic/Forbidden filler patterns (case-insensitive, exact/partial matching)
_FORBIDDEN_FILLERS = [
    re.compile(r"^\s*well,?\s+fair\s+enough\.?\s*$", re.IGNORECASE),
    re.compile(r"^\s*i\s+understand\.?\s*$", re.IGNORECASE),
    re.compile(r"^\s*okay?\.?\s*$", re.IGNORECASE),
    re.compile(r"^\s*sure\.?\s*$", re.IGNORECASE),
    re.compile(r"^\s*gotcha\.?\s*$", re.IGNORECASE),
    re.compile(r"^\s*understood\.?\s*$", re.IGNORECASE),
    re.compile(r"^\s*sorry,?\s+i\s+missed\s+that\s+last\s+part\.?\s*$", re.IGNORECASE),
]

_MARKDOWN_PATTERNS = [
    re.compile(r"[\#\*\_\`\[\]\(\)]"),
    re.compile(r"^[\-\*•]\s+", re.MULTILINE),
]

_LABEL_PATTERNS = [
    re.compile(r"\b(?:Agent|Dana|Assistant|AI|User)\s*:", re.IGNORECASE),
]


class DirectResponseValidator:
    """Validates generated agent responses before they are played over a live call.
    """

    def __init__(self, config: Any) -> None:
        self._config = config

    def validate(self, text: str, stage: str, user_transcript: str) -> ValidationResult:
        """Validate the response text against strict phone-agent quality rules.
        """
        if not text or not text.strip():
            return ValidationResult(False, "Empty response")

        cleaned = text.strip()
        cleaned_lower = cleaned.lower()

        # 1. Reject Markdown/Formatting
        for pat in _MARKDOWN_PATTERNS:
            if pat.search(cleaned):
                return ValidationResult(False, f"Response contains markdown formatting or bullets: '{cleaned}'")

        # 2. Reject Labels
        for pat in _LABEL_PATTERNS:
            if pat.search(cleaned):
                return ValidationResult(False, f"Response contains speaker labels: '{cleaned}'")

        # 3. Reject Forbidden/Generic fillers
        for pat in _FORBIDDEN_FILLERS:
            if pat.match(cleaned):
                # Allow 'Sorry, I missed that' only if user transcript is empty or was flagged as filler/noise
                if "missed" in pat.pattern and not user_transcript.strip():
                    continue
                return ValidationResult(False, f"Response contains forbidden generic filler: '{cleaned}'")

        # 4. Reject multiple questions
        question_count = cleaned.count("?")
        if question_count > 1:
            return ValidationResult(False, f"Response contains multiple questions ({question_count})")

        # 5. Reject greeting repeats after the first stage
        stage_str = stage.lower().replace("_", " ") if stage else "opening"
        if stage_str not in ("opening", "interest check"):
            if "this is alex" in cleaned_lower or "american beneficiary" in cleaned_lower:
                return ValidationResult(False, f"Response repeats greeting/introduction in stage '{stage}'")

        # 6. Length validation (approximate word count * 1.3 for token estimate)
        words = cleaned.split()
        if len(words) > 40:
            return ValidationResult(False, f"Response is too long ({len(words)} words)")

        return ValidationResult(True)

    def get_deterministic_fallback(self, stage: str, user_transcript: str) -> str:
        """Return a natural, stage-appropriate fallback statement.
        """
        stage_lower = stage.lower().replace("_", " ") if stage else "opening"

        from core.intent.short_response_intent import classify_intent
        intent = classify_intent(user_transcript)

        if intent == "dnc" or stage_lower == "dnc":
            return "I understand, I'll make sure this number is not contacted again."
        if intent == "wrong_number" or stage_lower == "wrong number":
            return "I understand, I'll make sure this number is not contacted again."
        if intent in ("refusal", "hostile_refusal") or stage_lower in ("disqualified", "end"):
            return "Understood. I won’t keep you. Take care."
        if stage_lower == "callback":
            return "No problem. Would later today or tomorrow be better?"
        if stage_lower in ("transfer consent", "transfer ready"):
            return "Perfect. Stay right there for me."
        if stage_lower == "opening":
            return "Hey, this is Alex. I'm getting back with you about the final expense burial options. Are you still open to looking at those?"
        if stage_lower == "interest check":
            return "I'm calling about the final expense information you requested; are you still open to looking at it?"
        if stage_lower == "age range":
            return "Okay. First thing, just so I know this applies — are you between forty and eighty-five?"
        if stage_lower == "living situation":
            return "Got it. And do you live independently?"
        if stage_lower == "decision maker":
            return "Understood. And are you the main financial decision maker in your household?"

        return "Are you still open to looking at those options?"
