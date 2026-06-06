import json
import logging
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class LatencyRecorder:
    def __init__(self, call_id: str):
        self.call_id = call_id
        self.events: Dict[str, float] = {}
        self.streaming_mode_enabled = False

    def mark(self, event_name: str):
        """Record the current high-resolution timestamp for an event."""
        self.events[event_name] = time.perf_counter()
        logger.debug(f"Call {self.call_id} marked {event_name}")
        if event_name == "first_audio_published":
            turn_lat = self.duration("transcript_final", "first_audio_published")
            if turn_lat is not None:
                try:
                    from ops.worker_capacity import WorkerCapacity
                    WorkerCapacity.record_turn_latency(turn_lat)
                except ImportError:
                    pass

    def duration(self, start_event: str, end_event: str) -> Optional[float]:
        """Calculate the duration in milliseconds between two marked events."""
        start = self.events.get(start_event)
        end = self.events.get(end_event)
        if start is None or end is None:
            return None
        return (end - start) * 1000.0

    def to_dict(self) -> dict:
        """Return all duration metrics and absolute events as a dict."""
        durations = {}
        
        # Calculate interesting durations
        def add_dur(label, start, end):
            val = self.duration(start, end)
            if val is not None:
                durations[label] = round(val, 2)

        add_dur("call_to_join", "call_start", "participant_joined")
        add_dur("join_to_greeting", "participant_joined", "greeting_started")
        add_dur("speech_duration", "user_speech_start", "user_speech_end")
        add_dur("stt_latency", "user_speech_end", "transcript_final")
        add_dur("llm_first_token_latency", "llm_request_start", "llm_first_token")
        add_dur("llm_duration", "llm_request_start", "llm_done")
        add_dur("tts_synthesis_start_latency", "tts_first_text", "tts_first_audio")
        add_dur("turn_response_latency", "transcript_final", "first_audio_published")
        add_dur("barge_in_stop_audio_latency", "barge_in_detected", "barge_in_stopped_audio")
        
        # Add new streaming specific metrics
        add_dur("first_safe_clause_ms", "llm_request_start", "first_safe_clause_detected")
        add_dur("first_streamed_tts_text_ms", "llm_request_start", "first_streamed_tts_text")

        return {
            "call_id": self.call_id,
            "streaming_mode_enabled": self.streaming_mode_enabled,
            "durations": durations,
            "events": {k: round(v, 4) for k, v in self.events.items()}
        }

    def log_summary(self):
        """Log call summary as one compact JSON line and output any warnings."""
        summary = self.to_dict()
        compact_json = json.dumps(summary)
        print(f"LATENCY_METRICS_SUMMARY: {compact_json}", flush=True)
        
        # Check warnings
        durations = summary["durations"]
        
        turn_lat = durations.get("turn_response_latency")
        if turn_lat is not None and turn_lat > 900.0:
            logger.warning(f"LATENCY_WARNING: Turn response latency (transcript_final -> first_audio_published) exceeded target: {turn_lat}ms > 900ms")
            
        llm_lat = durations.get("llm_first_token_latency")
        if llm_lat is not None and llm_lat > 250.0:
            logger.warning(f"LATENCY_WARNING: LLM first token latency (llm_request_start -> llm_first_token) exceeded target: {llm_lat}ms > 250ms")
            
        tts_lat = durations.get("tts_synthesis_start_latency")
        if tts_lat is not None and tts_lat > 200.0:
            logger.warning(f"LATENCY_WARNING: TTS first audio latency (tts_first_text -> tts_first_audio) exceeded target: {tts_lat}ms > 200ms")
            
        barge_lat = durations.get("barge_in_stop_audio_latency")
        if barge_lat is not None and barge_lat > 250.0:
            logger.warning(f"LATENCY_WARNING: Barge-in interruption stop latency (barge_in_detected -> barge_in_stopped_audio) exceeded target: {barge_lat}ms > 250ms")
