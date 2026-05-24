"""Event schemas for the Dana agent runtime.

Defines the structure for various events emitted during a call turn's lifecycle.
These events are useful for logging, storage, and QA pipelines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class RuntimeEvent:
    """Base class for all runtime events."""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str = "base_event"


@dataclass
class UtteranceReceivedEvent(RuntimeEvent):
    """Fired when a user utterance is transcribed and received."""
    call_id: str = ""
    text: str = ""
    current_stage: str = ""
    event_type: str = "utterance_received"


@dataclass
class StateTransitionEvent(RuntimeEvent):
    """Fired when the state machine transitions from one stage to another."""
    call_id: str = ""
    from_stage: str = ""
    to_stage: str = ""
    event_type: str = "state_transition"


@dataclass
class ObjectionDetectedEvent(RuntimeEvent):
    """Fired when a prospect objection is classified."""
    call_id: str = ""
    utterance: str = ""
    intent: str = ""
    confidence: float = 0.0
    event_type: str = "objection_detected"


@dataclass
class ResponseGeneratedEvent(RuntimeEvent):
    """Fired when the agent's spoken response is generated."""
    call_id: str = ""
    text: str = ""
    stage: str = ""
    event_type: str = "response_generated"


@dataclass
class ToolTriggeredEvent(RuntimeEvent):
    """Fired when a tool execution is initiated or completed."""
    call_id: str = ""
    tool_name: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    result_message: str = ""
    error: Optional[str] = None
    event_type: str = "tool_triggered"


@dataclass
class ValidationFailedEvent(RuntimeEvent):
    """Fired when an output fails compliance or validation checks."""
    call_id: str = ""
    response: str = ""
    validator_type: str = ""  # "compliance" | "formatting"
    issues: list[str] = field(default_factory=list)
    event_type: str = "validation_failed"
