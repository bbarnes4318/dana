"""Compliance filter for Dana voice agent responses.

Checks agent output for forbidden phrases that could violate
insurance advertising regulations, make unauthorized claims,
or mislead prospects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ComplianceResult:
    """Result of a compliance check on an agent response.

    Attributes:
        is_safe: True if no compliance violations were found.
        violations: List of human-readable violation descriptions.
        filtered_response: The original response (unmodified). Callers
            should suppress or replace responses that are not safe.
    """

    is_safe: bool = True
    violations: list[str] = field(default_factory=list)
    filtered_response: str = ""


# Each entry is (compiled_regex, violation_description).
_FORBIDDEN_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # --- approval claims ---
    (
        re.compile(r"\byou(?:\s+are|'re)\s+approved\b", re.IGNORECASE),
        "Approval claim: must not tell prospects they are approved",
    ),
    (
        re.compile(r"\bguaranteed\s+approval\b", re.IGNORECASE),
        "Guaranteed approval claim is prohibited",
    ),
    # --- premium quoting ---
    (
        re.compile(
            r"\$\s?\d+(?:\.\d{2})?\s*(?:per|a|/)\s*month",
            re.IGNORECASE,
        ),
        "Specific premium quote: agents must not quote dollar amounts",
    ),
    # --- government program / benefit ---
    (
        re.compile(r"\bgovernment\s+program\b", re.IGNORECASE),
        "Reference to 'government program' is prohibited",
    ),
    (
        re.compile(r"\bgovernment\s+benefit\b", re.IGNORECASE),
        "Reference to 'government benefit' is prohibited",
    ),
    (
        re.compile(r"\bfrom\s+the\s+government\b", re.IGNORECASE),
        "Implying product is from the government is prohibited",
    ),
    # --- federal / state program in insurance context ---
    (
        re.compile(
            r"\bfederal\b.*\b(?:insurance|coverage|plan|program)\b",
            re.IGNORECASE,
        ),
        "Implying federal affiliation with insurance is prohibited",
    ),
    (
        re.compile(r"\bstate\s+program\b", re.IGNORECASE),
        "Reference to 'state program' in insurance context is prohibited",
    ),
    # --- licensing claims ---
    (
        re.compile(
            r"\bI\s+am\s+a\s+licensed\s+agent\b", re.IGNORECASE
        ),
        "AI must not claim to be a licensed agent",
    ),
    (
        re.compile(r"\bI'm\s+licensed\b", re.IGNORECASE),
        "AI must not claim to be licensed",
    ),
    # --- specific policy recommendations ---
    (
        re.compile(
            r"\bI\s+recommend\b.*\b(?:plan|policy|product)\b",
            re.IGNORECASE,
        ),
        "AI must not recommend specific policies",
    ),
    # --- specific coverage amount advice ---
    (
        re.compile(
            r"\byou\s+should\s+get\b.*\$\s?\d+",
            re.IGNORECASE,
        ),
        "AI must not advise specific coverage amounts",
    ),
    # --- urgency / pressure language ---
    (
        re.compile(r"\bact\s+now\b", re.IGNORECASE),
        "Urgency language 'act now' is prohibited",
    ),
    (
        re.compile(r"\blimited\s+time\b", re.IGNORECASE),
        "Urgency language 'limited time' is prohibited",
    ),
    (
        re.compile(r"\bexpires\s+today\b", re.IGNORECASE),
        "Urgency language 'expires today' is prohibited",
    ),
    # --- medical underwriting claims ---
    (
        re.compile(r"\bno\s+health\s+questions\b", re.IGNORECASE),
        "Claiming 'no health questions' is a prohibited underwriting claim",
    ),
]


class ComplianceFilter:
    """Checks agent responses against insurance-compliance forbidden phrases.

    Usage::

        filt = ComplianceFilter()
        result = filt.check("You are approved for coverage!")
        if not result.is_safe:
            print(result.violations)
    """

    def __init__(self) -> None:
        self._patterns = _FORBIDDEN_PATTERNS

    def check(self, response: str) -> ComplianceResult:
        """Check *response* for compliance violations.

        Args:
            response: The agent's proposed spoken response text.

        Returns:
            A :class:`ComplianceResult` indicating safety and any violations.
        """
        violations: list[str] = []

        for pattern, description in self._patterns:
            if pattern.search(response):
                violations.append(description)

        return ComplianceResult(
            is_safe=len(violations) == 0,
            violations=violations,
            filtered_response=response,
        )
