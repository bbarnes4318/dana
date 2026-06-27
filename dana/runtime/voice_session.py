from __future__ import annotations
import asyncio
import logging
from typing import Optional, Any
from livekit.agents import JobContext, AutoSubscribe
from dana.config.voice_config import VoiceConfig
from dana.runtime.call_context import CallContext
from dana.runtime.turn_manager import TurnManager

logger = logging.getLogger(__name__)

class VoiceSession:
    """Manages the lifecycle of a single outbound AI call session."""
    
    def __init__(self, ctx: JobContext, shared_components: Any) -> None:
        self.ctx = ctx
        self.shared = shared_components
        self.config = shared_components.config
        self.context: Optional[CallContext] = None
        self.turn_manager: Optional[TurnManager] = None

    async def initialize(self, call_id: str, phone_number: str, campaign_id: str, lead_id: str) -> CallContext:
        """Set up call context and turn manager."""
        self.context = CallContext(
            call_id=call_id,
            phone_number=phone_number,
            campaign_id=campaign_id,
            lead_id=lead_id
        )
        # Create adapter
        from core.livekit_runtime_adapter import LiveKitRuntimeAdapter
        from pathlib import Path
        adapter = LiveKitRuntimeAdapter(
            call_id=call_id,
            phone_number=phone_number,
            project_root=Path(__file__).resolve().parent.parent.parent,
            prompt_loader=self.shared.prompt_loader,
            objection_classifier=self.shared.objection_classifier,
            objection_policy=self.shared.objection_policy,
            context_builder=self.shared.context_builder,
            action_policy=self.shared.action_policy,
            tool_registry=self.shared.tool_registry,
            compliance_filter=self.shared.compliance_filter,
            output_validator=self.shared.output_validator,
            pii_redactor=self.shared.pii_redactor,
            repository=self.shared.repository
        )
        self.turn_manager = TurnManager(self.context, adapter)
        return self.context
