from __future__ import annotations

from datetime import datetime, timezone
from qa.call_record import CallRecord, CallTurn
from qa.scoring import CallScorer


def test_score_bot_likeness_perfect_score():
    scorer = CallScorer()
    
    # A perfectly normal, human-like call
    record = CallRecord(
        call_id="test-call-1",
        turns=[
            CallTurn(speaker="agent", text="Hi, this is Alex calling from American Beneficiary.", stage="opening"),
            CallTurn(speaker="prospect", text="Yes, who is this?", stage="opening"),
            CallTurn(speaker="agent", text="I'm checking if you're still open to reviewing burial insurance options.", stage="interest_check"),
            CallTurn(speaker="prospect", text="Sure, tell me more.", stage="interest_check"),
        ],
        lead_profile={},
        final_stage="interest_check",
        outcome="ended",
    )
    
    score = scorer._score_bot_likeness(record, [])
    assert score == 10.0


def test_score_bot_likeness_phrase_repetition():
    scorer = CallScorer()
    
    # Overusing "perfect" 4 times (which is 2 overuses -> 4.0 points deduction)
    record = CallRecord(
        call_id="test-call-2",
        turns=[
            CallTurn(speaker="agent", text="Perfect. Hello.", stage="opening"),
            CallTurn(speaker="agent", text="Perfect. I understand.", stage="interest_check"),
            CallTurn(speaker="agent", text="Perfect. Got that.", stage="age_range"),
            CallTurn(speaker="agent", text="Perfect. Let me verify.", stage="living_situation"),
        ],
        lead_profile={},
        final_stage="living_situation",
        outcome="ended",
    )
    
    issues = []
    score = scorer._score_bot_likeness(record, issues)
    # 10.0 - 4.0 = 6.0
    assert score == 6.0
    assert any("overused phrase 'perfect'" in issue for issue in issues)


def test_score_bot_likeness_duplicate_sentences():
    scorer = CallScorer()
    
    # Duplicate sentence: "Let me check that." (length >= 3 words -> 3.0 points deduction)
    record = CallRecord(
        call_id="test-call-3",
        turns=[
            CallTurn(speaker="agent", text="Let me check that.", stage="interest_check"),
            CallTurn(speaker="agent", text="Let me check that.", stage="age_range"),
        ],
        lead_profile={},
        final_stage="age_range",
        outcome="ended",
    )
    
    issues = []
    score = scorer._score_bot_likeness(record, issues)
    # 10.0 - 3.0 = 7.0
    assert score == 7.0
    assert any("duplicate sentence spoken" in issue for issue in issues)


def test_score_bot_likeness_chatbot_phrases():
    scorer = CallScorer()
    
    # Uses chatbot phrase "as an AI" (2.0 points deduction)
    record = CallRecord(
        call_id="test-call-4",
        turns=[
            CallTurn(speaker="agent", text="As an AI, I can help you with options.", stage="interest_check"),
        ],
        lead_profile={},
        final_stage="interest_check",
        outcome="ended",
    )
    
    issues = []
    score = scorer._score_bot_likeness(record, issues)
    # 10.0 - 2.0 = 8.0
    assert score == 8.0
    assert any("chatbot phrase detected" in issue for issue in issues)


def test_score_bot_likeness_interrupted_no_repair():
    scorer = CallScorer()
    
    # Turn was interrupted but agent did not use repair language (3.0 points deduction)
    record = CallRecord(
        call_id="test-call-5",
        turns=[
            CallTurn(speaker="agent", text="I'm checking if you're open to options.", stage="interest_check", interrupted=True),
        ],
        lead_profile={},
        final_stage="interest_check",
        outcome="ended",
    )
    
    issues = []
    score = scorer._score_bot_likeness(record, issues)
    # 10.0 - 3.0 = 7.0
    assert score == 7.0
    assert any("missing repair language after interruption" in issue for issue in issues)


def test_score_bot_likeness_interrupted_with_repair():
    scorer = CallScorer()
    
    # Turn was interrupted and agent properly used repair language (no deduction)
    record = CallRecord(
        call_id="test-call-6",
        turns=[
            CallTurn(speaker="agent", text="Sorry, go ahead. I'm checking if you're open.", stage="interest_check", interrupted=True),
        ],
        lead_profile={},
        final_stage="interest_check",
        outcome="ended",
    )
    
    issues = []
    score = scorer._score_bot_likeness(record, issues)
    assert score == 10.0
    assert not issues
