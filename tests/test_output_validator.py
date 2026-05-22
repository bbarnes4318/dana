"""Tests for safety.output_validator.OutputValidator."""

from __future__ import annotations

import pytest

from safety.output_validator import OutputValidator, ValidationResult


@pytest.fixture
def validator() -> OutputValidator:
    return OutputValidator()


# --------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------- #


def test_valid_response_passes(validator: OutputValidator) -> None:
    """A short, clean spoken sentence should pass validation."""
    result = validator.validate(
        "Hi there, is this a good time to talk?", "opening"
    )
    assert result.is_valid is True
    assert result.issues == []
    assert result.suggestions == []


# --------------------------------------------------------------------- #
# Length warnings
# --------------------------------------------------------------------- #


def test_too_long_response_warns(validator: OutputValidator) -> None:
    """Responses over 60 words should produce a suggestion."""
    long_response = " ".join(["word"] * 61)
    result = validator.validate(long_response, "opening")
    # Length is a soft warning, not a hard fail.
    assert result.is_valid is True
    assert any("61 words" in s for s in result.suggestions)


# --------------------------------------------------------------------- #
# Question count
# --------------------------------------------------------------------- #


def test_multiple_questions_warns(validator: OutputValidator) -> None:
    """More than one question mark should trigger a suggestion."""
    result = validator.validate(
        "How are you today? Are you interested in coverage?", "opening"
    )
    assert result.is_valid is True
    assert any("2 questions" in s for s in result.suggestions)


# --------------------------------------------------------------------- #
# Markdown / formatting
# --------------------------------------------------------------------- #


def test_markdown_detected(validator: OutputValidator) -> None:
    """Markdown formatting should cause a hard failure."""
    # Bold markdown
    result = validator.validate("This is **important** information.", "opening")
    assert result.is_valid is False
    assert any("Bold markdown" in i for i in result.issues)

    # Heading
    result = validator.validate("## Section Title\nSome content.", "opening")
    assert result.is_valid is False
    assert any("heading" in i.lower() for i in result.issues)

    # Bullet list
    result = validator.validate("Here are the benefits:\n- Low cost\n- Easy", "opening")
    assert result.is_valid is False
    assert any("Bullet" in i for i in result.issues)

    # Numbered list
    result = validator.validate("Steps:\n1. Call us\n2. Sign up", "opening")
    assert result.is_valid is False
    assert any("Numbered" in i for i in result.issues)


# --------------------------------------------------------------------- #
# Chatbot phrases
# --------------------------------------------------------------------- #


def test_chatbot_phrases_detected(validator: OutputValidator) -> None:
    """Common chatbot phrases should be flagged."""
    result = validator.validate(
        "How can I assist you with your insurance needs today?", "opening"
    )
    assert result.is_valid is False
    assert any("how can i assist you" in i.lower() for i in result.issues)

    result2 = validator.validate(
        "Is there anything else I can help with?", "end"
    )
    assert result2.is_valid is False
    assert any("is there anything else" in i.lower() for i in result2.issues)
