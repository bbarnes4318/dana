"""Tests for DirectResponsePolicy and transcript filtering logic."""

import os
import sys
import time
import pytest

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dana.runtime.direct_response_controller import (
    DirectResponsePolicy,
    TurnPolicy,
    extract_transcript_text,
    clean_response,
    get_fallback_response,
    compute_similarity,
    VALID_SHORT_INTENTS,
)
from dana.config.voice_config import VoiceConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config():
    """Return a default VoiceConfig for testing."""
    return VoiceConfig()


@pytest.fixture
def policy(config):
    """Return a DirectResponsePolicy with default config."""
    return DirectResponsePolicy(config)


# ---------------------------------------------------------------------------
# extract_transcript_text
# ---------------------------------------------------------------------------

class TestExtractTranscriptText:

    def test_event_transcript_string(self):
        class Ev:
            transcript = "Hello there"
        assert extract_transcript_text(Ev()) == "Hello there"

    def test_event_transcript_object_with_text(self):
        class Inner:
            text = "Hello"
        class Ev:
            transcript = Inner()
        assert extract_transcript_text(Ev()) == "Hello"

    def test_event_text_attr(self):
        class Ev:
            transcript = None
            text = "Yes I am interested"
        assert extract_transcript_text(Ev()) == "Yes I am interested"

    def test_event_alternatives_string(self):
        class Ev:
            transcript = None
            text = None
            alternatives = ["Who is this?"]
        assert extract_transcript_text(Ev()) == "Who is this?"

    def test_event_alternatives_object_text(self):
        class Alt:
            text = "Hello"
            transcript = None
        class Ev:
            transcript = None
            text = None
            alternatives = [Alt()]
        assert extract_transcript_text(Ev()) == "Hello"

    def test_event_alternatives_object_transcript(self):
        class Alt:
            text = None
            transcript = "Go away"
        class Ev:
            transcript = None
            text = None
            alternatives = [Alt()]
        assert extract_transcript_text(Ev()) == "Go away"

    def test_unknown_event_returns_empty(self):
        class Ev:
            pass
        assert extract_transcript_text(Ev()) == ""

    def test_none_event(self):
        """Even if event has no attributes, should not throw."""
        class Ev:
            transcript = None
            text = None
        assert extract_transcript_text(Ev()) == ""


# ---------------------------------------------------------------------------
# DirectResponsePolicy.get_turn_policy
# ---------------------------------------------------------------------------

class TestDirectResponsePolicy:

    def test_dnc_uses_stop_tokens(self, policy, config):
        result = policy.get_turn_policy(None, "Do not call me again")
        assert result.max_tokens == config.direct_response_max_tokens_stop
        assert result.should_end_after_response is True

    def test_stop_uses_stop_tokens(self, policy, config):
        result = policy.get_turn_policy(None, "stop calling me")
        assert result.max_tokens == config.direct_response_max_tokens_stop
        assert result.should_end_after_response is True

    def test_remove_me_uses_stop_tokens(self, policy, config):
        result = policy.get_turn_policy(None, "Please remove me from your list")
        assert result.max_tokens == config.direct_response_max_tokens_stop
        assert result.should_end_after_response is True

    def test_wrong_number_uses_stop_tokens(self, policy, config):
        result = policy.get_turn_policy(None, "You have the wrong number")
        assert result.max_tokens == config.direct_response_max_tokens_stop
        assert result.should_end_after_response is True

    def test_confusion_uses_objection_tokens(self, policy, config):
        result = policy.get_turn_policy(None, "Who is this?")
        assert result.max_tokens == config.direct_response_max_tokens_objection
        assert result.should_end_after_response is False

    def test_what_is_this_uses_objection_tokens(self, policy, config):
        result = policy.get_turn_policy(None, "What is this about?")
        assert result.max_tokens == config.direct_response_max_tokens_objection
        assert result.should_end_after_response is False

    def test_why_are_you_calling_uses_objection_tokens(self, policy, config):
        result = policy.get_turn_policy(None, "Why are you calling me?")
        assert result.max_tokens == config.direct_response_max_tokens_objection
        assert result.should_end_after_response is False

    def test_objection_not_interested(self, policy, config):
        result = policy.get_turn_policy(None, "I'm not interested in anything")
        assert result.max_tokens == config.direct_response_max_tokens_objection
        assert result.should_end_after_response is False

    def test_objection_too_expensive(self, policy, config):
        result = policy.get_turn_policy(None, "That's too expensive for me")
        assert result.max_tokens == config.direct_response_max_tokens_objection

    def test_normal_progression(self, policy, config):
        result = policy.get_turn_policy(None, "Yes, I am interested in learning more")
        assert result.max_tokens == config.direct_response_max_tokens_default
        assert result.should_end_after_response is False

    def test_hard_max_clamp(self):
        """Policy max_tokens should never exceed hard_max_tokens."""
        config = VoiceConfig()
        config.direct_response_max_tokens_objection = 200
        config.direct_response_hard_max_tokens = 100
        policy = DirectResponsePolicy(config)
        result = policy.get_turn_policy(None, "Who is this?")
        assert result.max_tokens == 100


# ---------------------------------------------------------------------------
# clean_response
# ---------------------------------------------------------------------------

class TestCleanResponse:

    def test_strips_agent_label(self):
        assert clean_response("Agent: Hello there!") == "Hello there!"
        assert clean_response("Dana: How are you?") == "How are you?"
        assert clean_response("Assistant: Yes") == "Yes"

    def test_strips_bullets(self):
        assert "- " not in clean_response("- Point one\n- Point two")

    def test_collapses_spaces(self):
        assert "  " not in clean_response("Hello   world   test")

    def test_empty_input(self):
        assert clean_response("") == ""
        assert clean_response(None) == ""


# ---------------------------------------------------------------------------
# get_fallback_response
# ---------------------------------------------------------------------------

class TestFallbackResponse:

    def test_dnc_fallback(self):
        result = get_fallback_response(None, "do not call me")
        assert "not contacted" in result.lower() or "not be contacted" in result.lower()

    def test_wrong_number_fallback(self):
        result = get_fallback_response(None, "wrong number")
        assert "not contacted" in result.lower() or "not be contacted" in result.lower()

    def test_confusion_fallback(self):
        result = get_fallback_response(None, "who is this")
        assert "calling" in result.lower()

    def test_normal_fallback(self):
        result = get_fallback_response(None, "yes")
        assert "options" in result.lower()


# ---------------------------------------------------------------------------
# compute_similarity / echo suppression
# ---------------------------------------------------------------------------

class TestEchoSuppression:

    def test_identical_strings(self):
        assert compute_similarity("hello", "hello") == 1.0

    def test_empty_strings(self):
        assert compute_similarity("", "hello") == 0.0
        assert compute_similarity("hello", "") == 0.0

    def test_similar_strings_above_threshold(self):
        # Simulate TTS echo: the agent said X and STT picked up a similar version
        agent_said = "Are you still open to looking at those options?"
        stt_echo = "Are you still open to looking at those options"
        similarity = compute_similarity(agent_said, stt_echo)
        assert similarity >= 0.78

    def test_different_strings_below_threshold(self):
        similarity = compute_similarity(
            "Are you open to looking at those options?",
            "Yes I am interested in learning more"
        )
        assert similarity < 0.78


# ---------------------------------------------------------------------------
# VALID_SHORT_INTENTS
# ---------------------------------------------------------------------------

class TestValidShortIntents:

    def test_yes_is_valid(self):
        assert "yes" in VALID_SHORT_INTENTS

    def test_no_is_valid(self):
        assert "no" in VALID_SHORT_INTENTS

    def test_ok_is_valid(self):
        assert "ok" in VALID_SHORT_INTENTS

    def test_stop_is_valid(self):
        assert "stop" in VALID_SHORT_INTENTS

    def test_wrong_number_is_valid(self):
        assert "wrong number" in VALID_SHORT_INTENTS

    def test_random_short_not_valid(self):
        assert "a" not in VALID_SHORT_INTENTS
        assert "um" not in VALID_SHORT_INTENTS
