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
        self.total_barge_ins = 0
        self.false_interruption_count = 0
        self.event_history = []

    def mark(self, event_name: str):
        """Record the current high-resolution timestamp for an event."""
        t = time.perf_counter()
        self.events[event_name] = t
        self.event_history.append((event_name, t))

        if event_name == "barge_in_detected":
            self.total_barge_ins += 1
        elif event_name == "false_interruption_detected":
            self.false_interruption_count += 1

        logger.debug(f"Call {self.call_id} marked {event_name}")
        if event_name == "first_audio_published":
            turn_lat = self.duration("transcript_final", "first_audio_published")
            if turn_lat is not None:
                try:
                    from ops.worker_capacity import WorkerCapacity
                    WorkerCapacity.record_turn_latency(turn_lat)
                except ImportError:
                    pass

    @property
    def false_interruption_rate(self) -> float:
        if self.total_barge_ins > 0:
            return round(self.false_interruption_count / self.total_barge_ins, 4)
        return 0.0

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
        
        # Interruption Telemetry metrics
        add_dur("total_barge_in_stop_ms", "barge_in_detected", "agent_audio_stopped")
        add_dur("barge_in_detected_to_interrupt_call_ms", "barge_in_detected", "session_interrupt_called")
        add_dur("interrupt_call_to_audio_stopped_ms", "session_interrupt_called", "agent_audio_stopped")
        add_dur("tts_cancel_duration_ms", "tts_cancel_requested", "tts_cancel_completed")
        add_dur("audio_flush_duration_ms", "audio_output_flush_requested", "audio_output_flush_completed")
        
        # Add new streaming specific metrics
        add_dur("first_safe_clause_ms", "llm_request_start", "first_safe_clause_detected")
        add_dur("first_streamed_tts_text_ms", "llm_request_start", "first_streamed_tts_text")

        return {
            "call_id": self.call_id,
            "streaming_mode_enabled": self.streaming_mode_enabled,
            "total_barge_ins": self.total_barge_ins,
            "false_interruption_count": self.false_interruption_count,
            "false_interruption_rate": self.false_interruption_rate,
            "durations": durations,
            "events": {k: round(v, 4) for k, v in self.events.items()}
        }

    async def save_metrics(self, repository, stage: str = "opening") -> None:
        """Persist all recorded durations and rates to database."""
        summary = self.to_dict()
        durations = summary.get("durations", {})
        
        metrics_to_save = dict(durations)
        metrics_to_save["false_interruption_count"] = float(self.false_interruption_count)
        metrics_to_save["false_interruption_rate"] = self.false_interruption_rate

        for name, val in metrics_to_save.items():
            try:
                await repository.save_latency_metric(
                    call_id=self.call_id,
                    metric_name=name,
                    metric_value_ms=val
                )
                await repository.save_latency_metric(
                    call_id=self.call_id,
                    metric_name=f"{name}_stage_{stage}",
                    metric_value_ms=val
                )
            except Exception as e:
                logger.error(f"Failed to save latency metric {name}: {e}")

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
