import pytest
from unittest.mock import MagicMock, AsyncMock
from ops.conversation_loop_doctor import analyze_timeline, run_text_injection

def test_analyze_timeline_success():
    """Verify that a complete timeline passes the analyzer."""
    events = {
        "room_joined": 100,
        "participant_joined": 150,
        "inbound_audio_frame_received": 200,
        "vad_start_of_speech": 250,
        "vad_end_of_speech": 300,
        "stt_stream_created": 350,
        "transcript_final": 400,
        "llm_node_entered": 450,
        "user_text_seen_by_llm_node": 500,
        "agent_response_text_created": 550,
        "tts_first_text": 600,
        "tts_first_audio": 650,
        "second_turn_audio_published": 700
    }
    # Test with event_ prefix
    prefixed_events = {f"event_{k}": v for k, v in events.items()}
    passed, broken_stage = analyze_timeline(prefixed_events)
    assert passed is True
    assert broken_stage == "none"

    # Test without prefix
    passed, broken_stage = analyze_timeline(events)
    assert passed is True
    assert broken_stage == "none"

def test_analyze_timeline_no_inbound_audio():
    """Verify that missing inbound_audio is correctly reported."""
    events = {
        "room_joined": 100,
        "participant_joined": 150,
        # Missing inbound_audio_frame_received
        "vad_start_of_speech": 250,
        "vad_end_of_speech": 300,
        "stt_stream_created": 350,
        "transcript_final": 400,
        "llm_node_entered": 450,
        "user_text_seen_by_llm_node": 500,
        "agent_response_text_created": 550,
        "tts_first_text": 600,
        "tts_first_audio": 650,
        "second_turn_audio_published": 700
    }
    passed, broken_stage = analyze_timeline(events)
    assert passed is False
    assert broken_stage == "no inbound audio"

def test_analyze_timeline_no_transcript():
    """Verify that missing transcript_final is correctly reported."""
    events = {
        "room_joined": 100,
        "participant_joined": 150,
        "inbound_audio_frame_received": 200,
        "vad_start_of_speech": 250,
        "vad_end_of_speech": 300,
        "stt_stream_created": 350,
        # Missing transcript_final
        "llm_node_entered": 450,
        "user_text_seen_by_llm_node": 500,
        "agent_response_text_created": 550,
        "tts_first_text": 600,
        "tts_first_audio": 650,
        "second_turn_audio_published": 700
    }
    passed, broken_stage = analyze_timeline(events)
    assert passed is False
    assert broken_stage == "no transcript"

def test_analyze_timeline_no_llm_node():
    """Verify that missing llm_node_entered is correctly reported."""
    events = {
        "room_joined": 100,
        "participant_joined": 150,
        "inbound_audio_frame_received": 200,
        "vad_start_of_speech": 250,
        "vad_end_of_speech": 300,
        "stt_stream_created": 350,
        "transcript_final": 400,
        # Missing llm_node_entered
        "user_text_seen_by_llm_node": 500,
        "agent_response_text_created": 550,
        "tts_first_text": 600,
        "tts_first_audio": 650,
        "second_turn_audio_published": 700
    }
    passed, broken_stage = analyze_timeline(events)
    assert passed is False
    assert broken_stage == "no llm_node"

def test_analyze_timeline_no_tts_audio():
    """Verify that missing tts_first_audio is correctly reported."""
    events = {
        "room_joined": 100,
        "participant_joined": 150,
        "inbound_audio_frame_received": 200,
        "vad_start_of_speech": 250,
        "vad_end_of_speech": 300,
        "stt_stream_created": 350,
        "transcript_final": 400,
        "llm_node_entered": 450,
        "user_text_seen_by_llm_node": 500,
        "agent_response_text_created": 550,
        "tts_first_text": 600,
        # Missing tts_first_audio
        "second_turn_audio_published": 700
    }
    passed, broken_stage = analyze_timeline(events)
    assert passed is False
    assert broken_stage == "no TTS audio"

@pytest.mark.asyncio
async def test_run_text_injection(monkeypatch):
    """Verify that run_text_injection runs without exceptions when services are mocked."""
    monkeypatch.setenv("DANA_ALLOW_MOCK_TTS", "true")
    monkeypatch.setenv("DANA_RUNTIME_ENV", "test")
    
    # Run the injection tool path
    await run_text_injection("hello")
