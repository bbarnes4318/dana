import pytest
from speech.partial_intent_detector import classify_partial_intent, IntentClass

def test_dnc_classification():
    assert classify_partial_intent("do not call me anymore") == IntentClass.DNC_STOP
    assert classify_partial_intent("remove my number from your list") == IntentClass.DNC_STOP
    assert classify_partial_intent("dnc") == IntentClass.DNC_STOP
    assert classify_partial_intent("stop calling me") == IntentClass.DNC_STOP

def test_wrong_number_classification():
    assert classify_partial_intent("wrong number") == IntentClass.WRONG_NUMBER
    assert classify_partial_intent("you have the wrong person") == IntentClass.WRONG_NUMBER
    assert classify_partial_intent("no one here by that name") == IntentClass.WRONG_NUMBER

def test_callback_classification():
    assert classify_partial_intent("call me later") == IntentClass.CALLBACK_REQUEST
    assert classify_partial_intent("can you call me back tomorrow") == IntentClass.CALLBACK_REQUEST
    assert classify_partial_intent("i am busy right now") == IntentClass.CALLBACK_REQUEST

def test_objection_and_confusion():
    assert classify_partial_intent("not interested") == IntentClass.OBJECTION
    assert classify_partial_intent("who is this") == IntentClass.OBJECTION
    assert classify_partial_intent("i don't understand what you mean") == IntentClass.CONFUSION
    assert classify_partial_intent("what's going on") == IntentClass.CONFUSION

def test_backchannels_and_still_thinking():
    assert classify_partial_intent("uh huh") == IntentClass.BACKCHANNEL_ONLY
    assert classify_partial_intent("um") == IntentClass.STILL_THINKING
    assert classify_partial_intent("let's see um") == IntentClass.STILL_THINKING
    assert classify_partial_intent("yeah but") == IntentClass.STILL_THINKING
    assert classify_partial_intent("yes and") == IntentClass.STILL_THINKING

def test_yes_no_stages():
    # In TRANSFER_CONSENT stage
    assert classify_partial_intent("sure, go ahead", "TRANSFER_CONSENT") == IntentClass.TRANSFER_CONSENT_YES
    assert classify_partial_intent("no do not transfer", "TRANSFER_CONSENT") == IntentClass.TRANSFER_CONSENT_NO
    
    # In other stages
    assert classify_partial_intent("sure, go ahead", "INTEREST_CHECK") == IntentClass.COMPLETE_ANSWER
    assert classify_partial_intent("no", "AGE_RANGE") == IntentClass.COMPLETE_ANSWER

def test_questions():
    assert classify_partial_intent("how much does it cost?") == IntentClass.PRICE_QUESTION
    assert classify_partial_intent("is this a government program?") == IntentClass.GOVERNMENT_QUESTION
    assert classify_partial_intent("are you a robot?") == IntentClass.BOT_OR_AI_QUESTION
