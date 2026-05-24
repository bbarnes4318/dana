import asyncio
import json
import logging
import pytest
from latency_metrics import LatencyRecorder

def test_latency_recorder_marks_and_durations():
    recorder = LatencyRecorder("test-call-1")
    
    recorder.mark("event_a")
    time_a = recorder.events["event_a"]
    
    recorder.mark("event_b")
    time_b = recorder.events["event_b"]
    
    assert time_b >= time_a
    
    dur = recorder.duration("event_a", "event_b")
    assert dur is not None
    assert dur >= 0.0


def test_latency_recorder_to_dict():
    recorder = LatencyRecorder("test-call-2")
    recorder.mark("call_start")
    recorder.mark("participant_joined")
    
    summary = recorder.to_dict()
    assert summary["call_id"] == "test-call-2"
    assert "call_to_join" in summary["durations"]
    assert "call_start" in summary["events"]


def test_latency_recorder_warnings(caplog):
    recorder = LatencyRecorder("test-call-3")
    
    # Mock timestamps to cross warning thresholds
    recorder.events["transcript_final"] = 10.0
    recorder.events["first_audio_published"] = 11.0  # 1000ms duration (threshold: 900ms)
    
    recorder.events["llm_request_start"] = 10.0
    recorder.events["llm_first_token"] = 10.3  # 300ms duration (threshold: 250ms)
    
    recorder.events["tts_first_text"] = 10.0
    recorder.events["tts_first_audio"] = 10.25  # 250ms duration (threshold: 200ms)
    
    recorder.events["barge_in_detected"] = 10.0
    recorder.events["barge_in_stopped_audio"] = 10.3  # 300ms duration (threshold: 250ms)
    
    with caplog.at_level(logging.WARNING):
        recorder.log_summary()
        
    warnings = [record.message for record in caplog.records if "LATENCY_WARNING" in record.message]
    assert len(warnings) == 4
    assert any("Turn response latency" in w for w in warnings)
    assert any("LLM first token latency" in w for w in warnings)
    assert any("TTS first audio latency" in w for w in warnings)
    assert any("Barge-in interruption" in w for w in warnings)
