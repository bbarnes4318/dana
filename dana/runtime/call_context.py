from __future__ import annotations
import uuid
import time
from typing import Dict, Any, Optional
from latency_metrics import LatencyRecorder

class CallContext:
    """Isolates mutable state, metadata, and cost metrics for a single call session."""

    def __init__(
        self,
        call_id: Optional[str] = None,
        phone_number: Optional[str] = None,
        campaign_id: Optional[str] = None,
        lead_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        self.call_id = call_id or str(uuid.uuid4())
        self.phone_number = phone_number or "unknown"
        self.campaign_id = campaign_id or "unknown"
        self.lead_id = lead_id or "unknown"
        self.metadata = metadata or {}

        # Timing and latency metrics
        self.latency_recorder = LatencyRecorder(self.call_id)
        self.start_time = time.time()
        self.end_time: Optional[float] = None
        self.cost_calculated = False

        # Usage / Cost tracking metrics
        self.stt_seconds = 0.0
        self.tts_characters = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

        # Run states
        self.user_transcript_received = False
        self.final_transcript_count = 0
        self.current_turn_response = ""
        self.interrupted_current_turn = False
        self.interrupted_at = 0.0
        self.agent_speech_started_time: Optional[float] = None
        self.warm_bridge_active = False
        self.should_disconnect = False
