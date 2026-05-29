import pytest
from datetime import datetime, timezone
from qa.call_record import CallRecord, CallTurn
from qa.scoring import CallScorer, detect_hard_failures, is_transfer_ready

@pytest.fixture
def scorer():
    return CallScorer()

def test_is_transfer_ready():
    profile = {
        "open_to_review": True,
        "age_range_confirmed": True,
        "living_independently": True,
        "financial_decision_maker": True,
        "transfer_consent_confirmed": True,
        "do_not_call_requested": False,
        "disqualified_reason": None,
    }
    assert is_transfer_ready(profile) is True

    profile["open_to_review"] = False
    assert is_transfer_ready(profile) is False

def test_happy_path_scores_high(scorer):
    # All 5 fields confirmed, correctly greeting, no hard failures, and transferring.
    turns = [
        CallTurn(speaker="agent", text="Hi, this is Dana from American Beneficiary. I'm calling to see if you qualify.", stage="opening"),
        CallTurn(speaker="prospect", text="Yes?", stage="opening"),
        CallTurn(speaker="agent", text="Great, are you open to review options for final expense?", stage="interest_check"),
        CallTurn(speaker="prospect", text="Yes, sure.", stage="interest_check"),
        CallTurn(speaker="agent", text="Awesome. Are you in the age range of forty to eighty-five?", stage="age_range"),
        CallTurn(speaker="prospect", text="Yes, I am 65.", stage="age_range"),
        CallTurn(speaker="agent", text="Perfect. Do you live independently?", stage="living_situation"),
        CallTurn(speaker="prospect", text="Yes.", stage="living_situation"),
        CallTurn(speaker="agent", text="Got it. Are you the financial decision maker?", stage="decision_maker"),
        CallTurn(speaker="prospect", text="Yes.", stage="decision_maker"),
        CallTurn(speaker="agent", text="And do you give consent to transfer to a licensed agent?", stage="transfer_consent"),
        CallTurn(speaker="prospect", text="Yes, go ahead.", stage="transfer_consent"),
        CallTurn(speaker="agent", text="Perfect. Hold on while I connect you.", stage="transfer_ready"),
    ]
    profile = {
        "open_to_review": True,
        "age_range_confirmed": True,
        "living_independently": True,
        "financial_decision_maker": True,
        "transfer_consent_confirmed": True,
        "do_not_call_requested": False,
        "disqualified_reason": None,
    }
    record = CallRecord(
        turns=turns,
        lead_profile=profile,
        outcome="transferred",
        final_stage="transfer_ready"
    )
    scorecard = scorer.score_call(record)
    assert scorecard.overall_score >= 9.0
    assert scorecard.grade == "A"
    assert len(scorecard.issues) == 0

def test_missing_short_flow_fields_reduces_completion(scorer):
    # Only 3 fields confirmed (6 points)
    turns = [
        CallTurn(speaker="agent", text="Hi, this is Dana from American Beneficiary.", stage="opening"),
        CallTurn(speaker="prospect", text="Hello.", stage="opening"),
    ]
    profile = {
        "open_to_review": True,
        "age_range_confirmed": True,
        "living_independently": True,
        "financial_decision_maker": None,
        "transfer_consent_confirmed": None,
        "do_not_call_requested": False,
        "disqualified_reason": None,
    }
    record = CallRecord(
        turns=turns,
        lead_profile=profile,
        outcome="ended",
        final_stage="living_situation"
    )
    scorecard = scorer.score_call(record)
    assert scorecard.scores["short_flow_completion"] == 6.0
    assert scorecard.grade != "A"

def test_transfer_before_all_five_fields_causes_hard_fail(scorer):
    # Transferred but missing decision maker and transfer consent
    turns = [
        CallTurn(speaker="agent", text="Hi, this is Dana from American Beneficiary.", stage="opening"),
        CallTurn(speaker="prospect", text="Hello.", stage="opening"),
    ]
    profile = {
        "open_to_review": True,
        "age_range_confirmed": True,
        "living_independently": True,
        "financial_decision_maker": None,
        "transfer_consent_confirmed": None,
        "do_not_call_requested": False,
        "disqualified_reason": None,
    }
    record = CallRecord(
        turns=turns,
        lead_profile=profile,
        outcome="transferred",
        final_stage="transfer_ready"
    )
    scorecard = scorer.score_call(record)
    assert scorecard.overall_score == 0.0
    assert scorecard.grade == "F"
    assert any("agent transferred before all five" in issue.lower() for issue in scorecard.issues)

def test_quoted_price_causes_f(scorer):
    turns = [
        CallTurn(speaker="agent", text="Hi, this is Dana. Your monthly cost will be fifty dollars.", stage="opening"),
    ]
    record = CallRecord(turns=turns, lead_profile={}, outcome="ended", final_stage="opening")
    scorecard = scorer.score_call(record)
    assert scorecard.overall_score == 0.0
    assert scorecard.grade == "F"
    assert any("quoted a premium/price" in issue.lower() for issue in scorecard.issues)

def test_approval_claim_causes_f(scorer):
    turns = [
        CallTurn(speaker="agent", text="Hi, this is Dana. I can guarantee you are approved today.", stage="opening"),
    ]
    record = CallRecord(turns=turns, lead_profile={}, outcome="ended", final_stage="opening")
    scorecard = scorer.score_call(record)
    assert scorecard.overall_score == 0.0
    assert scorecard.grade == "F"
    assert any("promised approval" in issue.lower() for issue in scorecard.issues)

def test_you_qualify_claim_causes_f(scorer):
    turns = [
        CallTurn(speaker="agent", text="Hi, this is Dana. You qualify for our plans.", stage="opening"),
    ]
    record = CallRecord(turns=turns, lead_profile={}, outcome="ended", final_stage="opening")
    scorecard = scorer.score_call(record)
    assert scorecard.overall_score == 0.0
    assert scorecard.grade == "F"
    assert any("you qualify" in issue.lower() for issue in scorecard.issues)

def test_licensed_claim_causes_f(scorer):
    turns = [
        CallTurn(speaker="agent", text="Hi, this is Dana. I am a licensed agent in your state.", stage="opening"),
    ]
    record = CallRecord(turns=turns, lead_profile={}, outcome="ended", final_stage="opening")
    scorecard = scorer.score_call(record)
    assert scorecard.overall_score == 0.0
    assert scorecard.grade == "F"
    assert any("claimed to be licensed" in issue.lower() for issue in scorecard.issues)

def test_human_claim_causes_f(scorer):
    turns = [
        CallTurn(speaker="agent", text="Yes, I am a real person, not an AI.", stage="opening"),
    ]
    record = CallRecord(turns=turns, lead_profile={}, outcome="ended", final_stage="opening")
    scorecard = scorer.score_call(record)
    assert scorecard.overall_score == 0.0
    assert scorecard.grade == "F"
    assert any("claimed to be human" in issue.lower() for issue in scorecard.issues)

def test_dnc_continuation_causes_f(scorer):
    # Prospect asks to be put on DNC, but agent continues talking in subsequent turns.
    turns = [
        CallTurn(speaker="agent", text="Hi, this is Dana.", stage="opening"),
        CallTurn(speaker="prospect", text="Put me on the do not call list.", stage="opening"),
        CallTurn(speaker="agent", text="Okay, I will do that.", stage="dnc"),
        CallTurn(speaker="agent", text="But wait, are you sure?", stage="dnc"),
    ]
    profile = {"do_not_call_requested": True}
    record = CallRecord(turns=turns, lead_profile=profile, outcome="dnc", final_stage="dnc")
    scorecard = scorer.score_call(record)
    assert scorecard.overall_score == 0.0
    assert scorecard.grade == "F"
    assert any("failed to stop after dnc" in issue.lower() for issue in scorecard.issues)

def test_markdown_causes_f(scorer):
    turns = [
        CallTurn(speaker="agent", text="Hi, this is Dana. **Bold text**.", stage="opening"),
    ]
    record = CallRecord(turns=turns, lead_profile={}, outcome="ended", final_stage="opening")
    scorecard = scorer.score_call(record)
    assert scorecard.overall_score == 0.0
    assert scorecard.grade == "F"
    assert any("markdown/bullets" in issue.lower() for issue in scorecard.issues)

def test_multiple_questions_causes_f(scorer):
    turns = [
        CallTurn(speaker="agent", text="Are you ready? Do you have time?", stage="opening"),
    ]
    record = CallRecord(turns=turns, lead_profile={}, outcome="ended", final_stage="opening")
    scorecard = scorer.score_call(record)
    assert scorecard.overall_score == 0.0
    assert scorecard.grade == "F"
    assert any("more than one question" in issue.lower() for issue in scorecard.issues)

def test_sensitive_info_request_causes_f(scorer):
    turns = [
        CallTurn(speaker="agent", text="Can I get your social security number?", stage="opening"),
    ]
    record = CallRecord(turns=turns, lead_profile={}, outcome="ended", final_stage="opening")
    scorecard = scorer.score_call(record)
    assert scorecard.overall_score == 0.0
    assert scorecard.grade == "F"
    assert any("sensitive info" in issue.lower() for issue in scorecard.issues)

def test_disqualification_confirmation_scoring(scorer):
    # Case 1: call disqualified and agent confirmed
    turns = [
        CallTurn(speaker="agent", text="Just so I make sure I heard you right, did you say you live in a nursing home?", stage="disqualified"),
    ]
    record = CallRecord(turns=turns, lead_profile={}, outcome="disqualified", final_stage="disqualified")
    scorecard = scorer.score_call(record)
    assert scorecard.scores["disqualification_confirmation"] == 10.0

    # Case 2: call disqualified but agent did NOT confirm
    turns_no_confirm = [
        CallTurn(speaker="agent", text="Okay, goodbye then.", stage="disqualified"),
    ]
    record_no_confirm = CallRecord(turns=turns_no_confirm, lead_profile={}, outcome="disqualified", final_stage="disqualified")
    scorecard_no_confirm = scorer.score_call(record_no_confirm)
    assert scorecard_no_confirm.scores["disqualification_confirmation"] == 0.0

def test_dnc_handling_scoring(scorer):
    # Case 1: DNC requested and call stopped
    turns = [
        CallTurn(speaker="agent", text="Hi, this is Dana.", stage="opening"),
        CallTurn(speaker="prospect", text="remove me from your list", stage="opening"),
        CallTurn(speaker="agent", text="I will remove your number.", stage="dnc"),
    ]
    profile = {"do_not_call_requested": True}
    record = CallRecord(turns=turns, lead_profile=profile, outcome="dnc", final_stage="dnc")
    scorecard = scorer.score_call(record)
    assert scorecard.scores["dnc_handling"] == 10.0

    # Case 2: DNC requested but call did not enter terminal stage
    record_fail = CallRecord(turns=turns, lead_profile=profile, outcome="ended", final_stage="opening")
    scorecard_fail = scorer.score_call(record_fail)
    assert scorecard_fail.scores["dnc_handling"] == 0.0
