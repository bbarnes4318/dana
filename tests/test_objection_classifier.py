"""Tests for the ObjectionClassifier."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.objection_classifier import ObjectionClassifier, ObjectionMatch


# Resolve the YAML path relative to the project root
_YAML_PATH = Path(__file__).resolve().parent.parent / "kb" / "objections" / "final_expense_objections.yaml"


@pytest.fixture
def classifier() -> ObjectionClassifier:
    """Create an ObjectionClassifier with the default YAML definitions."""
    return ObjectionClassifier(yaml_path=_YAML_PATH, confidence_threshold=0.3)


# ------------------------------------------------------------------
# Classification tests
# ------------------------------------------------------------------


class TestClassifyNotInterested:
    """Tests for the not_interested intent."""

    def test_classify_not_interested_explicit(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("I'm not interested") == "not_interested"

    def test_classify_not_interested_polite(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("No thank you, I don't need anything") == "not_interested"

    def test_classify_not_interested_casual(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("nah, I'm good") == "not_interested"

    def test_classify_not_interested_pass(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("I'll pass on that") == "not_interested"


class TestClassifyDNC:
    """Tests for the remove_me / DNC intent."""

    def test_classify_dnc_remove_me(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("Remove me from your list") == "remove_me"

    def test_classify_dnc_do_not_call(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("Put me on the do not call list") == "remove_me"

    def test_classify_dnc_stop_calling(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("Stop calling me please") == "remove_me"

    def test_classify_dnc_never_again(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("Don't ever call this number again") == "remove_me"


class TestClassifyAlreadyInsured:
    """Tests for the already_have_insurance intent."""

    def test_classify_already_insured_direct(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("I already have insurance") == "already_have_insurance"

    def test_classify_already_insured_covered(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("I'm already covered, thanks") == "already_have_insurance"

    def test_classify_already_insured_have_policy(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("I have a policy already") == "already_have_insurance"


class TestClassifyBusy:
    """Tests for the busy intent."""

    def test_classify_busy_explicit(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("I'm busy right now") == "busy"

    def test_classify_busy_bad_time(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("This is not a good time") == "busy"

    def test_classify_busy_at_work(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("I'm at work, can't talk") == "busy"


class TestClassifyGovernmentConcern:
    """Tests for the is_this_government intent."""

    def test_classify_government_direct(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("Are you with the government?") == "is_this_government"

    def test_classify_government_medicare(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("Is this about my medicare?") == "is_this_government"

    def test_classify_government_social_security(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("Are you from social security office?") == "is_this_government"


class TestClassifyScamConcern:
    """Tests for the scam_concern intent."""

    def test_classify_scam_direct(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("Is this a scam?") == "scam_concern"

    def test_classify_scam_sounds_like(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("This sounds like a scam to me") == "scam_concern"

    def test_classify_scam_legit(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("Is this legit or is this a rip off?") == "scam_concern"


class TestNoObjection:
    """Tests for utterances that should NOT trigger any objection."""

    def test_no_objection_returns_none_greeting(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("Hello, how are you doing today?") is None

    def test_no_objection_returns_none_positive(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("Tell me more about that") is None

    def test_no_objection_returns_none_empty(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("") is None

    def test_no_objection_returns_none_whitespace(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("   ") is None

    def test_no_objection_returns_none_random(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("The weather is nice today") is None


class TestClassifyAngry:
    """Tests for the angry intent."""

    def test_classify_angry_explicit(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("I'm angry about these calls") == "angry"

    def test_classify_angry_harassment(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("Stop harassing me!") == "angry"

    def test_classify_angry_sick_of_calls(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("I'm sick of these calls from you people") == "angry"

    def test_classify_angry_profanity(self, classifier: ObjectionClassifier) -> None:
        assert classifier.classify("What the hell do you want") == "angry"


# ------------------------------------------------------------------
# Details / edge-case tests
# ------------------------------------------------------------------


class TestClassifyWithDetails:
    """Tests for classify_with_details returning full ObjectionMatch."""

    def test_returns_objection_match(self, classifier: ObjectionClassifier) -> None:
        result = classifier.classify_with_details("I'm not interested")
        assert result is not None
        assert isinstance(result, ObjectionMatch)
        assert result.intent == "not_interested"
        assert result.confidence > 0
        assert len(result.matched_keywords) > 0

    def test_returns_none_for_no_match(self, classifier: ObjectionClassifier) -> None:
        result = classifier.classify_with_details("The sky is blue")
        assert result is None


class TestKnownIntents:
    """Tests for the known_intents property."""

    def test_known_intents_populated(self, classifier: ObjectionClassifier) -> None:
        intents = classifier.known_intents
        assert len(intents) > 0
        assert "not_interested" in intents
        assert "remove_me" in intents
        assert "angry" in intents
