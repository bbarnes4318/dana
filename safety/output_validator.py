"""Output validator for Dana voice agent responses.

Ensures agent responses are appropriate for spoken delivery:
short, conversational, free of formatting, and stage-appropriate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    """Result of validating an agent response for voice delivery.

    Attributes:
        is_valid: True if no hard failures were found.
        issues: List of problems that make the response unsuitable.
        suggestions: List of soft warnings / improvement hints.
    """

    is_valid: bool = True
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


# Markdown / formatting patterns that should never appear in voice output.
_MARKDOWN_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"#{1,6}\s"), "Markdown heading detected"),
    (re.compile(r"\*\*[^*]+\*\*"), "Bold markdown detected"),
    (re.compile(r"__[^_]+__"), "Bold (underscore) markdown detected"),
    (re.compile(r"(?<!\w)\*[^*]+\*(?!\w)"), "Italic markdown detected"),
    (re.compile(r"\[([^\]]+)\]\([^)]+\)"), "Markdown link detected"),
    (re.compile(r"```"), "Code block detected"),
    (re.compile(r"`[^`]+`"), "Inline code detected"),
]

# Bullet / list patterns.
_LIST_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^\s*[-*+]\s", re.MULTILINE), "Bullet list detected"),
    (
        re.compile(r"^\s*\d+[.)]\s", re.MULTILINE),
        "Numbered list detected",
    ),
]

# Chatbot-style phrases that sound unnatural in a phone call.
_CHATBOT_PHRASES: list[str] = [
    "how can i assist you",
    "is there anything else",
    "how may i help you",
    "i'm here to help",
    "as an ai",
    "as a language model",
]

# Maximum word count before we warn about length.
_MAX_WORD_COUNT = 60


class OutputValidator:
    """Validates that an agent response is suitable for voice delivery.

    Usage::

        validator = OutputValidator()
        result = validator.validate("Sure thing! Here's a list...", "opening")
        if not result.is_valid:
            print(result.issues)
    """

    def validate(self, response: str, call_stage: str) -> ValidationResult:
        """Validate *response* for voice delivery at *call_stage*.

        Args:
            response: The agent's proposed spoken response.
            call_stage: Current call stage name (e.g. ``"opening"``).

        Returns:
            A :class:`ValidationResult` with issues and suggestions.
        """
        issues: list[str] = []
        suggestions: list[str] = []

        # --- word-count check (soft warning) ---
        word_count = len(response.split())
        if word_count > _MAX_WORD_COUNT:
            suggestions.append(
                f"Response is {word_count} words; keep under "
                f"{_MAX_WORD_COUNT} for natural voice delivery"
            )

        # --- question count (soft warning) ---
        question_count = response.count("?")
        if question_count > 1:
            suggestions.append(
                f"Response contains {question_count} questions; "
                "ask only one question at a time for clarity"
            )

        # --- markdown / formatting (hard fail) ---
        for pattern, description in _MARKDOWN_PATTERNS:
            if pattern.search(response):
                issues.append(description)

        # --- bullet / numbered lists (hard fail) ---
        for pattern, description in _LIST_PATTERNS:
            if pattern.search(response):
                issues.append(description)

        # --- chatbot phrases (hard fail) ---
        response_lower = response.lower()
        for phrase in _CHATBOT_PHRASES:
            if phrase in response_lower:
                issues.append(
                    f"Chatbot phrase detected: '{phrase}'"
                )

        # --- stage-appropriate checks ---
        self._check_stage_content(response_lower, call_stage, issues, suggestions)

        return ValidationResult(
            is_valid=len(issues) == 0,
            issues=issues,
            suggestions=suggestions,
        )

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _check_stage_content(
        response_lower: str,
        call_stage: str,
        issues: list[str],
        suggestions: list[str],
    ) -> None:
        """Add stage-specific validation checks."""
        if call_stage == "opening" and "goodbye" in response_lower:
            issues.append(
                "Opening stage should not contain farewell language"
            )

        if call_stage == "end" and "?" in response_lower:
            suggestions.append(
                "End stage responses should avoid asking new questions"
            )
