"""Tests verifying canonical responses, prompts, and QA requirements."""

from __future__ import annotations

import os
from pathlib import Path
import pytest

from core.canonical_responses import (
    DNC_CLOSE,
    WRONG_NUMBER_CLOSE,
    NOT_INTERESTED_CLOSE,
    REAL_PERSON_RESPONSE,
    LICENSED_RESPONSE,
    PRICE_RESPONSE,
    TRANSFER_FAILURE_CALLBACK,
)
from core.prompt_loader import PromptLoader, _DEFAULT_PROMPTS
from main import _DEFAULT_INSTRUCTIONS
from qa.scoring import CallScorer
from qa.call_record import CallRecord, CallTurn
from states.transfer_consent import TransferConsentState
from core.call_state import CallStage, CallState
from core.lead_profile import LeadProfile


def test_prompts_and_fallbacks_do_not_contain_dana() -> None:
    # 1. Check real prompt files
    project_root = Path(__file__).resolve().parent.parent
    prompts_dir = project_root / "prompts"
    
    assert prompts_dir.exists()
    for md_file in prompts_dir.glob("*.md"):
        content = md_file.read_text(encoding="utf-8")
        assert "you are dana" not in content.lower(), f"File {md_file.name} contains 'You are Dana'"
        assert "dana" not in content.lower(), f"File {md_file.name} contains 'Dana'"

    # 2. Check prompt loader fallback defaults
    for name, default_text in _DEFAULT_PROMPTS.items():
        assert "you are dana" not in default_text.lower(), f"Default prompt for {name} contains 'You are Dana'"
        assert "dana" not in default_text.lower(), f"Default prompt for {name} contains 'Dana'"

    # 3. Check main.py fallback instructions
    assert "you are dana" not in _DEFAULT_INSTRUCTIONS.lower()
    assert "dana" not in _DEFAULT_INSTRUCTIONS.lower()


def test_prompts_and_fallbacks_do_not_use_age_45_85() -> None:
    # 1. Check real prompt files
    project_root = Path(__file__).resolve().parent.parent
    prompts_dir = project_root / "prompts"
    
    for md_file in prompts_dir.glob("*.md"):
        content = md_file.read_text(encoding="utf-8")
        assert "45-85" not in content
        assert "45 to 85" not in content

    # 2. Check prompt loader fallback defaults
    for name, default_text in _DEFAULT_PROMPTS.items():
        assert "45-85" not in default_text
        assert "45 to 85" not in default_text

    # 3. Check main.py fallback instructions
    assert "45-85" not in _DEFAULT_INSTRUCTIONS
    assert "45 to 85" not in _DEFAULT_INSTRUCTIONS


def test_prompts_and_fallbacks_do_not_ask_for_name_state_health() -> None:
    forbidden_indicators = [
        "ask for their name",
        "ask for state",
        "health status",
        "health verification",
        "general health",
        "state verification",
        "identity verification",
    ]
    
    # Check fallback defaults
    for name, default_text in _DEFAULT_PROMPTS.items():
        lower_text = default_text.lower()
        # Verify it doesn't instruct to ask for name, state, health
        assert "verify state" not in lower_text
        assert "state verification" not in lower_text
        assert "general health" not in lower_text
        assert "identity verification" not in lower_text

    # Check main.py fallback instructions
    lower_main = _DEFAULT_INSTRUCTIONS.lower()
    assert "verify state" not in lower_main
    assert "state verification" not in lower_main
    assert "general health" not in lower_main
    assert "identity verification" not in lower_main


def test_prompts_and_fallbacks_do_not_contain_rates_dropped_or_updated_numbers() -> None:
    forbidden = ["rates dropped", "updated numbers", "new rates", "better numbers", "lowest price", "cheaper"]
    
    # 1. Check real prompt files
    project_root = Path(__file__).resolve().parent.parent
    prompts_dir = project_root / "prompts"
    
    for md_file in prompts_dir.glob("*.md"):
        content = md_file.read_text(encoding="utf-8").lower()
        # Exception: We are allowed to have a rule saying NEVER use/promise savings, cheaper rates, lower prices, etc.
        # So we check if the forbidden term is used in a positive way (e.g. check for "rates dropped", "updated numbers").
        assert "rates dropped" not in content
        assert "updated numbers" not in content
        assert "new rates" not in content

    # 2. Check prompt loader fallback defaults
    for name, default_text in _DEFAULT_PROMPTS.items():
        lower_text = default_text.lower()
        for f in forbidden:
            assert f not in lower_text

    # 3. Check main.py fallback instructions
    lower_main = _DEFAULT_INSTRUCTIONS.lower()
    for f in forbidden:
        assert f not in lower_main


def test_real_person_response_passes_qa() -> None:
    scorer = CallScorer()
    turns = [
        CallTurn(speaker="agent", text=REAL_PERSON_RESPONSE, stage="interest_check"),
    ]
    record = CallRecord(turns=turns, lead_profile={}, outcome="ended", final_stage="interest_check")
    scorecard = scorer.score_call(record)
    
    # Verify that the REAL_PERSON_RESPONSE does not trigger human claim / licensed claim F grade
    # Check that there are no hard failures
    assert scorecard.overall_score > 0.0
    assert scorecard.grade != "F"
    assert not any("human" in issue.lower() for issue in scorecard.issues)
    assert not any("licensed" in issue.lower() for issue in scorecard.issues)


@pytest.mark.parametrize("human_claim", [
    "yes",
    "I'm real",
    "I'm a real person",
    "I'm human",
    "I'm not AI",
    "I'm not a bot",
    "Yes, I am a human.",
])
def test_human_claims_fail_qa(human_claim: str) -> None:
    scorer = CallScorer()
    turns = [
        CallTurn(speaker="prospect", text="Are you a real person?", stage="interest_check"),
        CallTurn(speaker="agent", text=human_claim, stage="interest_check"),
    ]
    record = CallRecord(turns=turns, lead_profile={}, outcome="ended", final_stage="interest_check")
    scorecard = scorer.score_call(record)
    assert scorecard.overall_score == 0.0
    assert scorecard.grade == "F"
    assert any("claimed to be human" in issue.lower() or "failed the required response to 'are you real?'" in issue.lower() for issue in scorecard.issues)


@pytest.mark.parametrize("licensed_claim", [
    "I'm licensed",
    "I'm an agent",
    "I am a licensed agent in your state.",
])
def test_licensed_claims_fail_qa(licensed_claim: str) -> None:
    scorer = CallScorer()
    turns = [
        CallTurn(speaker="prospect", text="Are you licensed?", stage="interest_check"),
        CallTurn(speaker="agent", text=licensed_claim, stage="interest_check"),
    ]
    record = CallRecord(turns=turns, lead_profile={}, outcome="ended", final_stage="interest_check")
    scorecard = scorer.score_call(record)
    assert scorecard.overall_score == 0.0
    assert scorecard.grade == "F"
    assert any("claimed to be licensed" in issue.lower() or "failed the required response to 'are you licensed?'" in issue.lower() for issue in scorecard.issues)


def test_dnc_and_wrong_number_use_canonical_close() -> None:
    assert DNC_CLOSE == "Understood. I’ll make a note of that. Take care."
    assert WRONG_NUMBER_CLOSE == "Understood. I’ll make a note of that. Take care."
    assert NOT_INTERESTED_CLOSE == "Understood. I won’t keep you. Take care."


def test_silence_does_not_trigger_transfer() -> None:
    state = TransferConsentState()
    lead = LeadProfile()
    call = CallState(current_stage=CallStage.TRANSFER_CONSENT)
    
    # 1. First silent turn
    result = state.handle("silence", lead, call)
    
    # Should not transition to TRANSFER_READY (stays in same stage)
    assert result.next_stage is None
    assert "asked_silence_followup" in lead.notes
    assert "Are you okay holding" in result.response_guidance

    # 2. Second silent turn
    result2 = state.handle("silence", lead, call)
    
    # Should transition to CALLBACK
    assert result2.next_stage == CallStage.CALLBACK
    assert "asked_silence_followup" not in lead.notes
    assert "lost you" in result2.response_guidance or "callback" in result2.response_guidance


def test_explicit_consent_does_trigger_transfer() -> None:
    state = TransferConsentState()
    lead = LeadProfile()
    call = CallState(current_stage=CallStage.TRANSFER_CONSENT)
    
    # Consenting utterance
    result = state.handle("Yes, go ahead and connect me.", lead, call)
    
    assert result.next_stage == CallStage.TRANSFER_READY
    assert result.extracted_data.get("transfer_consent_confirmed") is True
    assert "Perfect. Stay right there for me." in result.response_guidance


def test_topic_redirect_responses_are_canonical() -> None:
    from safety.topic_redirect_policy import TopicRedirectPolicy
    policy = TopicRedirectPolicy()
    
    # Check that age range redirect matches our safe copy and does not contain "qualify"
    age_resp = policy.get_redirect_response(CallStage.AGE_RANGE)
    assert "forty" in age_resp
    assert "eighty-five" in age_resp
    assert "qualify" not in age_resp.lower()

