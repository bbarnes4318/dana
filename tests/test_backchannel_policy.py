from __future__ import annotations

from voice.backchannel_policy import BackchannelPolicy, check_confusion_or_hostility


def test_backchannel_stage_restrictions():
    policy = BackchannelPolicy()
    
    # Restrict on opening stage
    bc = policy.select_backchannel(
        current_stage="opening",
        user_text="Yes hello, how can I help you?",
        turn_count=1,
        objection_handled=False,
    )
    assert bc is None

    # Restrict on dnc stage
    bc = policy.select_backchannel(
        current_stage="dnc",
        user_text="Put me on your do not call list please.",
        turn_count=2,
        objection_handled=False,
    )
    assert bc is None


def test_backchannel_turn_restrictions():
    policy = BackchannelPolicy()
    
    # Normal turn should produce a backchannel
    bc1 = policy.select_backchannel(
        current_stage="interest_check",
        user_text="Yes, I am interested in burial insurance options.",
        turn_count=1,
        objection_handled=False,
    )
    assert bc1 is not None
    assert policy.used_last_turn is True
    
    # Should not produce backchannel two turns in a row
    bc2 = policy.select_backchannel(
        current_stage="interest_check",
        user_text="Yes, I am interested in burial insurance options.",
        turn_count=2,
        objection_handled=False,
    )
    assert bc2 is None
    assert policy.used_last_turn is False

    # Third turn should allow it again
    bc3 = policy.select_backchannel(
        current_stage="interest_check",
        user_text="Yes, I am interested in burial insurance options.",
        turn_count=3,
        objection_handled=False,
    )
    assert bc3 is not None
    assert policy.used_last_turn is True


def test_backchannel_length_and_silence():
    policy = BackchannelPolicy()
    
    # Too short user text (<= 2 words)
    bc = policy.select_backchannel(
        current_stage="interest_check",
        user_text="hello there",
        turn_count=1,
        objection_handled=False,
    )
    assert bc is None
    
    # Silence
    bc = policy.select_backchannel(
        current_stage="interest_check",
        user_text="silence",
        turn_count=1,
        objection_handled=False,
    )
    assert bc is None


def test_backchannel_rotation():
    policy = BackchannelPolicy()
    
    bc1 = policy.select_backchannel(
        current_stage="interest_check",
        user_text="Yes, I am interested in burial insurance options.",
        turn_count=1,
        objection_handled=False,
    )
    
    # Reset used_last_turn to allow consecutive selection for test purposes
    policy.used_last_turn = False
    
    bc2 = policy.select_backchannel(
        current_stage="interest_check",
        user_text="Yes, I am interested in burial insurance options.",
        turn_count=2,
        objection_handled=False,
    )
    # Consecutive selections should not repeat the same backchannel prefix
    assert bc1 != bc2


def test_clean_perfect_usage():
    policy = BackchannelPolicy()
    
    # Perfect stripped on interest_check stage
    cleaned = policy.clean_perfect_usage(
        text="Perfect. Yes, let me get that information.",
        current_stage="interest_check",
        user_text="Sure, tell me more.",
        objection_handled=False,
    )
    assert "Perfect" not in cleaned
    assert cleaned == "Yes, let me get that information."
    
    # Perfect stripped on silence
    cleaned = policy.clean_perfect_usage(
        text="Perfect. Yes, let me get that information.",
        current_stage="transfer_ready",
        user_text="silence",
        objection_handled=False,
    )
    assert "Perfect" not in cleaned

    # Perfect preserved on transfer_ready
    cleaned = policy.clean_perfect_usage(
        text="Perfect. Hold the line for me.",
        current_stage="transfer_ready",
        user_text="Yes, connect me to the agent.",
        objection_handled=False,
    )
    assert "Perfect" in cleaned
    assert cleaned == "Perfect. Hold the line for me."


def test_check_confusion_or_hostility():
    # Confusion detection
    is_confused, is_hostile = check_confusion_or_hostility("Wait, who is this?")
    assert is_confused is True
    assert is_hostile is False
    
    # Hostility detection
    is_confused, is_hostile = check_confusion_or_hostility("Stop calling me this is a scam!")
    assert is_confused is False
    assert is_hostile is True
