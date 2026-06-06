from __future__ import annotations

from voice.repetition_guard import RepetitionGuard


def test_repetition_guard_duplicate_sentences():
    guard = RepetitionGuard()
    
    res1 = guard.filter_response("Let me look that up for you.")
    assert res1 == "Let me look that up for you."
    
    # Exact same sentence should be skipped
    res2 = guard.filter_response("Let me look that up for you.")
    assert res2 == ""


def test_repetition_guard_acknowledgments_limit():
    guard = RepetitionGuard()
    
    # "okay" is a standard acknowledgment
    res1 = guard.filter_response("Okay.")
    assert res1 == "Okay."
    
    res2 = guard.filter_response("Okay.")
    assert res2 == "Okay."
    
    # Third time should be filtered out
    res3 = guard.filter_response("Okay.")
    assert res3 == ""


def test_repetition_guard_overused_phrase_substitutions():
    guard = RepetitionGuard()
    
    # "absolutely" is an overused phrase
    # First and second time should be allowed
    res1 = guard.filter_response("I absolutely agree with that.")
    assert "absolutely" in res1.lower()
    
    res2 = guard.filter_response("That is absolutely fine.")
    assert "absolutely" in res2.lower()
    
    # Third time should substitute "absolutely" with "yes"
    res3 = guard.filter_response("We can absolutely check that.")
    assert "absolutely" not in res3.lower()
    assert "yes" in res3.lower()


def test_repetition_guard_overused_phrase_skips_entirely():
    guard = RepetitionGuard()
    
    # First time allowed
    res1 = guard.filter_response("Gotcha.")
    assert res1 == "Gotcha."
    
    res2 = guard.filter_response("Gotcha.")
    assert res2 == "Gotcha."
    
    # Third time should be skipped entirely
    res3 = guard.filter_response("Gotcha.")
    assert res3 == ""


def test_repetition_guard_objections():
    guard = RepetitionGuard()
    
    res1 = guard.filter_response("I understand your concern about rates.", is_objection=True)
    assert res1 == "I understand your concern about rates."
    
    # Duplicate objection response should be filtered out
    res2 = guard.filter_response("I understand your concern about rates.", is_objection=True)
    assert res2 == ""
