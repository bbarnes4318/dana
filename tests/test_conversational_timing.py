from __future__ import annotations

from voice.conversational_timing import ConversationalTiming


def test_conversational_timing_delay():
    timing = ConversationalTiming()
    
    # Sensitive stages get 0.8s pre-speech delay
    assert timing.get_pre_speech_delay("age_range") == 0.8
    assert timing.get_pre_speech_delay("living_situation") == 0.8
    assert timing.get_pre_speech_delay("decision_maker") == 0.8
    assert timing.get_pre_speech_delay("transfer_consent") == 0.8

    # Non-sensitive stages get 0.0s delay
    assert timing.get_pre_speech_delay("opening") == 0.0
    assert timing.get_pre_speech_delay("interest_check") == 0.0
    assert timing.get_pre_speech_delay("dnc") == 0.0
    assert timing.get_pre_speech_delay("end") == 0.0
