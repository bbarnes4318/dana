"""Tests for safety.compliance_filter.ComplianceFilter."""

from __future__ import annotations

import pytest

from safety.compliance_filter import ComplianceFilter, ComplianceResult


@pytest.fixture
def filt() -> ComplianceFilter:
    return ComplianceFilter()


# --------------------------------------------------------------------- #
# Clean response
# --------------------------------------------------------------------- #


def test_clean_response_passes(filt: ComplianceFilter) -> None:
    """A compliant response should pass without violations."""
    result = filt.check(
        "I'd love to tell you a bit more about how final expense "
        "coverage works. Would that be okay?"
    )
    assert result.is_safe is True
    assert result.violations == []
    assert result.filtered_response != ""


# --------------------------------------------------------------------- #
# Approval claims
# --------------------------------------------------------------------- #


def test_approval_claim_caught(filt: ComplianceFilter) -> None:
    """Claims of approval should be flagged."""
    result = filt.check("Great news, you are approved for coverage!")
    assert result.is_safe is False
    assert any("approved" in v.lower() for v in result.violations)

    result2 = filt.check("You're approved — let's get started.")
    assert result2.is_safe is False

    result3 = filt.check("We offer guaranteed approval to everyone.")
    assert result3.is_safe is False
    assert any("guaranteed" in v.lower() for v in result3.violations)


# --------------------------------------------------------------------- #
# Government claims
# --------------------------------------------------------------------- #


def test_government_claim_caught(filt: ComplianceFilter) -> None:
    """References to government programs or benefits should be flagged."""
    for phrase in [
        "This is a government program for seniors.",
        "You may qualify for a government benefit.",
        "This coverage is from the government.",
    ]:
        result = filt.check(phrase)
        assert result.is_safe is False, f"Expected violation for: {phrase}"
        assert len(result.violations) > 0


# --------------------------------------------------------------------- #
# Premium quoting
# --------------------------------------------------------------------- #


def test_premium_quote_caught(filt: ComplianceFilter) -> None:
    """Specific dollar-per-month quotes should be flagged."""
    result = filt.check("Your premium would be $29.95 per month.")
    assert result.is_safe is False
    assert any("premium" in v.lower() or "dollar" in v.lower() for v in result.violations)

    result2 = filt.check("Plans start at $15 a month.")
    assert result2.is_safe is False


# --------------------------------------------------------------------- #
# Urgency language
# --------------------------------------------------------------------- #


def test_urgency_language_caught(filt: ComplianceFilter) -> None:
    """Pressure / urgency phrases should be flagged."""
    for phrase in [
        "You need to act now before rates go up!",
        "This is a limited time offer.",
        "This offer expires today.",
    ]:
        result = filt.check(phrase)
        assert result.is_safe is False, f"Expected violation for: {phrase}"


# --------------------------------------------------------------------- #
# Licensed agent claims
# --------------------------------------------------------------------- #


def test_licensed_claim_caught(filt: ComplianceFilter) -> None:
    """Claims of being a licensed agent should be flagged."""
    result = filt.check("I am a licensed agent in your state.")
    assert result.is_safe is False

    result2 = filt.check("Don't worry, I'm licensed to sell insurance.")
    assert result2.is_safe is False


# --------------------------------------------------------------------- #
# Case insensitivity
# --------------------------------------------------------------------- #


def test_case_insensitive(filt: ComplianceFilter) -> None:
    """All patterns should match regardless of case."""
    result = filt.check("YOU ARE APPROVED for this plan!")
    assert result.is_safe is False

    result2 = filt.check("GUARANTEED APPROVAL is available.")
    assert result2.is_safe is False

    result3 = filt.check("ACT NOW to lock in your rate!")
    assert result3.is_safe is False
