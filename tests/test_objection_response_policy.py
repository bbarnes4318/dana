"""Tests for the ObjectionResponsePolicy."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.objection_response_policy import ObjectionGuidance, ObjectionResponsePolicy


# Resolve the YAML path relative to the project root
_YAML_PATH = Path(__file__).resolve().parent.parent / "kb" / "objections" / "final_expense_objections.yaml"


@pytest.fixture
def policy() -> ObjectionResponsePolicy:
    """Create an ObjectionResponsePolicy with the default YAML definitions."""
    return ObjectionResponsePolicy(yaml_path=_YAML_PATH)


# ------------------------------------------------------------------
# DNC / remove_me tests
# ------------------------------------------------------------------


class TestDNCEndsImmediately:
    """DNC/remove_me must end the call immediately with zero rebuttals."""

    def test_dnc_ends_immediately(self, policy: ObjectionResponsePolicy) -> None:
        guidance = policy.get_response_guidance("remove_me", attempt_count=0)
        assert guidance.should_end_call is True
        assert guidance.next_stage == "end_call"
        assert guidance.max_attempts == 0

    def test_dnc_guidance_text_mentions_end(self, policy: ObjectionResponsePolicy) -> None:
        guidance = policy.get_response_guidance("remove_me", attempt_count=0)
        assert "end" in guidance.guidance_text.lower() or "immediately" in guidance.guidance_text.lower()

    def test_dnc_compliance_warning_present(self, policy: ObjectionResponsePolicy) -> None:
        guidance = policy.get_response_guidance("remove_me", attempt_count=0)
        assert guidance.compliance_warning is not None
        assert "DNC" in guidance.compliance_warning or "immediately" in guidance.compliance_warning


# ------------------------------------------------------------------
# Angry tests
# ------------------------------------------------------------------


class TestAngryGetsOneApology:
    """Angry customer gets exactly one apology then call ends."""

    def test_angry_first_attempt_allowed(self, policy: ObjectionResponsePolicy) -> None:
        guidance = policy.get_response_guidance("angry", attempt_count=0)
        assert isinstance(guidance, ObjectionGuidance)
        assert guidance.intent == "angry"
        # First attempt is the apology — not yet at max
        assert guidance.max_attempts == 1

    def test_angry_second_attempt_ends_call(self, policy: ObjectionResponsePolicy) -> None:
        guidance = policy.get_response_guidance("angry", attempt_count=1)
        assert guidance.should_end_call is True
        assert guidance.next_stage == "end_call"

    def test_angry_auto_tracking(self, policy: ObjectionResponsePolicy) -> None:
        """Auto-incremented attempt tracking for angry intent."""
        # First call — attempt 0
        g1 = policy.get_response_guidance("angry")
        assert g1.intent == "angry"
        # Second call — attempt 1 (at max, should end)
        g2 = policy.get_response_guidance("angry")
        assert g2.should_end_call is True


# ------------------------------------------------------------------
# Max attempts enforcement
# ------------------------------------------------------------------


class TestMaxAttemptsEnforced:
    """Max attempts must be enforced across objection types."""

    def test_not_interested_max_one(self, policy: ObjectionResponsePolicy) -> None:
        g1 = policy.get_response_guidance("not_interested", attempt_count=0)
        assert g1.max_attempts == 1
        # At max attempts, should transition to closing
        g2 = policy.get_response_guidance("not_interested", attempt_count=1)
        assert g2.next_stage in ("closing", "end_call")

    def test_busy_max_one(self, policy: ObjectionResponsePolicy) -> None:
        g = policy.get_response_guidance("busy", attempt_count=1)
        assert g.next_stage in ("closing", "end_call")

    def test_scam_concern_max_one(self, policy: ObjectionResponsePolicy) -> None:
        g = policy.get_response_guidance("scam_concern", attempt_count=1)
        assert g.next_stage in ("closing", "end_call")

    def test_auto_increment_enforces_max(self, policy: ObjectionResponsePolicy) -> None:
        """Auto-tracking should enforce max_attempts correctly."""
        policy.reset_attempts("not_interested")
        g1 = policy.get_response_guidance("not_interested")
        # After first call, next should enforce max
        g2 = policy.get_response_guidance("not_interested")
        assert g2.next_stage in ("closing", "end_call")


# ------------------------------------------------------------------
# already_have_insurance positions as review
# ------------------------------------------------------------------


class TestAlreadyInsuredPositionsAsReview:
    """already_have_insurance should be positioned as review, not replacement."""

    def test_already_insured_guidance_text(self, policy: ObjectionResponsePolicy) -> None:
        guidance = policy.get_response_guidance("already_have_insurance", attempt_count=0)
        assert isinstance(guidance, ObjectionGuidance)
        assert guidance.intent == "already_have_insurance"
        # Should go to qualifying, not end
        assert guidance.next_stage == "qualifying"
        assert guidance.should_end_call is False

    def test_already_insured_not_end_call(self, policy: ObjectionResponsePolicy) -> None:
        guidance = policy.get_response_guidance("already_have_insurance", attempt_count=0)
        assert guidance.should_end_call is False

    def test_already_insured_compliance(self, policy: ObjectionResponsePolicy) -> None:
        guidance = policy.get_response_guidance("already_have_insurance", attempt_count=0)
        assert guidance.compliance_warning is not None
        # Should mention not disparaging or supplemental
        warning_lower = guidance.compliance_warning.lower()
        assert "never" in warning_lower or "supplemental" in warning_lower or "disparag" in warning_lower

    def test_already_insured_allowed_responses_contain_review(self, policy: ObjectionResponsePolicy) -> None:
        responses = policy.get_allowed_responses("already_have_insurance")
        combined = " ".join(responses).lower()
        assert "review" in combined or "additional" in combined or "supplement" in combined

    def test_already_insured_forbidden_responses_block_replacement(self, policy: ObjectionResponsePolicy) -> None:
        forbidden = policy.get_forbidden_responses("already_have_insurance")
        combined = " ".join(forbidden).lower()
        assert "cancel" in combined or "replace" in combined or "switch" in combined


# ------------------------------------------------------------------
# Price defers to licensed agent
# ------------------------------------------------------------------


class TestPriceDefersToAgent:
    """Price/how_much questions must defer to licensed agent."""

    def test_price_compliance_no_quotes(self, policy: ObjectionResponsePolicy) -> None:
        guidance = policy.get_response_guidance("how_much", attempt_count=0)
        assert guidance.compliance_warning is not None
        warning_lower = guidance.compliance_warning.lower()
        assert "price" in warning_lower or "licensed" in warning_lower or "quote" in warning_lower

    def test_price_next_stage_qualifying(self, policy: ObjectionResponsePolicy) -> None:
        guidance = policy.get_response_guidance("how_much", attempt_count=0)
        assert guidance.next_stage == "qualifying"

    def test_price_allowed_responses_mention_agent(self, policy: ObjectionResponsePolicy) -> None:
        responses = policy.get_allowed_responses("how_much")
        combined = " ".join(responses).lower()
        assert "licensed agent" in combined or "agent" in combined

    def test_price_forbidden_responses_block_quotes(self, policy: ObjectionResponsePolicy) -> None:
        forbidden = policy.get_forbidden_responses("how_much")
        combined = " ".join(forbidden).lower()
        assert "$" in combined or "exact price" in combined or "costs" in combined.replace("don't worry about the cost", "costs")


# ------------------------------------------------------------------
# Utility / edge-case tests
# ------------------------------------------------------------------


class TestPolicyUtilities:
    """Tests for utility methods on ObjectionResponsePolicy."""

    def test_unknown_intent_raises(self, policy: ObjectionResponsePolicy) -> None:
        with pytest.raises(ValueError, match="Unknown objection intent"):
            policy.get_response_guidance("completely_made_up_intent")

    def test_reset_attempts(self, policy: ObjectionResponsePolicy) -> None:
        policy.get_response_guidance("busy")
        assert policy.get_attempt_count("busy") == 1
        policy.reset_attempts("busy")
        assert policy.get_attempt_count("busy") == 0

    def test_reset_all_attempts(self, policy: ObjectionResponsePolicy) -> None:
        policy.get_response_guidance("busy")
        policy.get_response_guidance("angry")
        policy.reset_attempts()
        assert policy.get_attempt_count("busy") == 0
        assert policy.get_attempt_count("angry") == 0

    def test_known_intents(self, policy: ObjectionResponsePolicy) -> None:
        intents = policy.known_intents
        assert "remove_me" in intents
        assert "angry" in intents
        assert "how_much" in intents
        assert "already_have_insurance" in intents

    def test_guidance_returns_dataclass(self, policy: ObjectionResponsePolicy) -> None:
        guidance = policy.get_response_guidance("busy", attempt_count=0)
        assert isinstance(guidance, ObjectionGuidance)
        assert guidance.intent == "busy"
        assert isinstance(guidance.guidance_text, str)
        assert isinstance(guidance.max_attempts, int)
        assert isinstance(guidance.should_end_call, bool)
        assert isinstance(guidance.next_stage, str)
