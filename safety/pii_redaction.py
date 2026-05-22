"""PII redaction utilities for Dana voice agent.

Detects and replaces personally identifiable information in text
using regular-expression patterns, preserving overall text structure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class RedactedResult:
    """Result of PII redaction on a text string.

    Attributes:
        redacted_text: The text with PII tokens replaced.
        redactions: List of dicts describing each redaction, each
            containing ``type``, ``original``, and ``replacement``.
    """

    redacted_text: str = ""
    redactions: list[dict[str, str]] = field(default_factory=list)


# Order matters: more specific patterns first to avoid partial matches.
_PII_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    # SSN  (XXX-XX-XXXX)
    (
        "SSN",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "[SSN]",
    ),
    # Phone — various North-American formats
    (
        "PHONE",
        re.compile(
            r"(?<!\d)"                       # not preceded by digit
            r"(?:\+?1[-.\s]?)?"              # optional country code
            r"(?:\(?\d{3}\)?[-.\s]?)"        # area code
            r"\d{3}[-.\s]?\d{4}"             # subscriber number
            r"(?!\d)",                        # not followed by digit
        ),
        "[PHONE]",
    ),
    # Email
    (
        "EMAIL",
        re.compile(
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
        ),
        "[EMAIL]",
    ),
    # Street address — basic US-style: number + street name + suffix
    (
        "ADDRESS",
        re.compile(
            r"\b\d{1,6}\s+[A-Za-z0-9.]+(?:\s+[A-Za-z0-9.]+){0,4}\s+"
            r"(?:Street|St|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Road|Rd|"
            r"Lane|Ln|Court|Ct|Way|Place|Pl|Circle|Cir)"
            r"\.?\b",
            re.IGNORECASE,
        ),
        "[ADDRESS]",
    ),
    # Bank account / routing numbers — 9+ digit sequences in financial context
    (
        "ACCOUNT",
        re.compile(
            r"(?:account|routing|acct|a/c)[\s#:]*(\d{9,17})",
            re.IGNORECASE,
        ),
        "[ACCOUNT]",
    ),
]


class PIIRedactor:
    """Redacts personally identifiable information from text.

    Supported PII types:

    * Phone numbers (various North-American formats) → ``[PHONE]``
    * Social Security Numbers (XXX-XX-XXXX) → ``[SSN]``
    * Email addresses → ``[EMAIL]``
    * Street addresses (basic pattern) → ``[ADDRESS]``
    * Bank account / routing numbers (9+ digits in financial context) →
      ``[ACCOUNT]``

    Usage::

        redactor = PIIRedactor()
        result = redactor.redact("Call me at 555-123-4567")
        print(result.redacted_text)  # "Call me at [PHONE]"
    """

    def __init__(self) -> None:
        self._patterns = _PII_PATTERNS

    def redact(self, text: str) -> RedactedResult:
        """Redact PII from *text*.

        Args:
            text: Input text potentially containing PII.

        Returns:
            A :class:`RedactedResult` with redacted text and metadata.
        """
        redactions: list[dict[str, str]] = []
        result_text = text

        for pii_type, pattern, replacement in self._patterns:
            def _make_replacer(
                _pii_type: str,
                _replacement: str,
                _redactions: list[dict[str, str]],
            ) -> Callable[[re.Match[str]], str]:
                """Create a replacer function that also records each redaction."""

                def _replacer(match: re.Match[str]) -> str:
                    _redactions.append(
                        {
                            "type": _pii_type,
                            "original": match.group(0),
                            "replacement": _replacement,
                        }
                    )
                    return _replacement

                return _replacer

            replacer = _make_replacer(pii_type, replacement, redactions)
            result_text = pattern.sub(replacer, result_text)

        return RedactedResult(
            redacted_text=result_text,
            redactions=redactions,
        )
