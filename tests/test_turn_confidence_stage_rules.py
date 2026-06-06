import pytest
from speech.partial_intent_detector import IntentClass
from speech.turn_confidence import calculate_turn_confidence, IMMEDIATE_DELAYS, FAST_YES_NO_DELAYS, PATIENT_DELAYS, BACKCHANNEL_DELAYS, STRICT_TRANSFER_DELAYS

def test_immediate_triggers():
    # DNC, Wrong number, callback or early-emit phrases should map to IMMEDIATE_DELAYS
    res1 = calculate_turn_confidence(IntentClass.DNC_STOP, "INTEREST_CHECK", "remove me")
    assert res1.should_emit_early is True
    assert res1.recommended_min_delay == IMMEDIATE_DELAYS[0]
    assert res1.recommended_max_delay == IMMEDIATE_DELAYS[1]

    res2 = calculate_turn_confidence(IntentClass.WRONG_NUMBER, "AGE_RANGE", "wrong person")
    assert res2.should_emit_early is True
    assert res2.recommended_min_delay == IMMEDIATE_DELAYS[0]

    # Explicit early emit text phrase
    res3 = calculate_turn_confidence(IntentClass.UNKNOWN, "LIVING_SITUATION", "stop calling me")
    assert res3.should_emit_early is True
    assert res3.recommended_min_delay == IMMEDIATE_DELAYS[0]

def test_yes_no_stages_speed():
    # Yes/no stages with affirmative/negative responses should respond fast
    res = calculate_turn_confidence(IntentClass.COMPLETE_ANSWER, "INTEREST_CHECK", "yes")
    assert res.should_emit_early is True
    assert res.recommended_min_delay == FAST_YES_NO_DELAYS[0]
    assert res.recommended_max_delay == FAST_YES_NO_DELAYS[1]

    # But still-thinking/objections in yes/no stages should wait
    res_think = calculate_turn_confidence(IntentClass.STILL_THINKING, "INTEREST_CHECK", "uh let's see")
    assert res_think.should_emit_early is False
    assert res_think.recommended_min_delay == PATIENT_DELAYS[0]

    res_obj = calculate_turn_confidence(IntentClass.OBJECTION, "INTEREST_CHECK", "already have coverage")
    assert res_obj.should_emit_early is False
    assert res_obj.recommended_min_delay == PATIENT_DELAYS[0]

def test_strict_transfer_consent():
    # Strict stage check: transfer consent stage delay mapping
    # Yes/no triggers early emit
    res_yes = calculate_turn_confidence(IntentClass.TRANSFER_CONSENT_YES, "TRANSFER_CONSENT", "sure")
    assert res_yes.should_emit_early is True
    assert res_yes.recommended_min_delay == FAST_YES_NO_DELAYS[0]

    # Ambiguous/unknown waits strictly
    res_amb = calculate_turn_confidence(IntentClass.UNKNOWN, "TRANSFER_CONSENT", "well what do you mean by that")
    assert res_amb.should_emit_early is False
    assert res_amb.recommended_min_delay == STRICT_TRANSFER_DELAYS[0]
    assert res_amb.recommended_max_delay == STRICT_TRANSFER_DELAYS[1]

def test_backchannels_patience():
    # Backchannels should not prematurely cut off
    res = calculate_turn_confidence(IntentClass.BACKCHANNEL_ONLY, "LIVING_SITUATION", "uh huh")
    assert res.should_emit_early is False
    assert res.recommended_min_delay == BACKCHANNEL_DELAYS[0]
    assert res.recommended_max_delay == BACKCHANNEL_DELAYS[1]
