"""Dana Voice Agent Runtime.

Orchestrates a single conversational turn by running user speech through the
entire pipeline (stop check, state handlers, objection engine, RAG context retrieval,
prompt instruction assembly, response validation, tool execution, and storage logging).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Awaitable

from core.call_state import CallStage, CallState, StateResult
from core.lead_profile import LeadProfile
from core.state_machine import StateMachine
from core.objection_classifier import ObjectionClassifier
from core.objection_response_policy import ObjectionResponsePolicy, ObjectionGuidance
from core.prompt_loader import PromptLoader
from core.response_builder import ResponseBuilder
from rag.context_builder import ContextBuilder
from core.action_policy import ActionPolicy
from tools.tool_registry import ToolRegistry
from safety.compliance_filter import ComplianceFilter
from safety.output_validator import OutputValidator
from safety.call_stop_policy import CallStopPolicy
from safety.pii_redaction import PIIRedactor
from storage.repository import Repository
from core.runtime_events import (
    RuntimeEvent,
    UtteranceReceivedEvent,
    StateTransitionEvent,
    ObjectionDetectedEvent,
    ResponseGeneratedEvent,
    ToolTriggeredEvent,
    ValidationFailedEvent,
)

logger = logging.getLogger(__name__)


@dataclass
class RuntimeResult:
    """Outcome of a single conversational turn."""

    agent_response: str
    stage: str
    extracted_data: dict[str, Any] = field(default_factory=dict)
    tool_results: list[str] = field(default_factory=list)
    compliance_ok: bool = True
    should_end_call: bool = False


class AgentRuntime:
    """Core runtime orchestrator for Dana voice agent turns.

    Wires together state handling, RAG retrieval, objection classification,
    compliance filtering, tool execution, and event/storage logging.
    """

    def __init__(
        self,
        prompt_loader: PromptLoader,
        state_machine: StateMachine,
        objection_classifier: ObjectionClassifier,
        objection_policy: ObjectionResponsePolicy,
        context_builder: ContextBuilder,
        action_policy: ActionPolicy,
        tool_registry: ToolRegistry,
        compliance_filter: ComplianceFilter,
        output_validator: OutputValidator,
        call_stop_policy: CallStopPolicy,
        pii_redactor: PIIRedactor,
        repository: Optional[Repository] = None,
        event_callback: Optional[Callable[[RuntimeEvent], Any]] = None,
    ) -> None:
        self.prompt_loader = prompt_loader
        self.state_machine = state_machine
        self.objection_classifier = objection_classifier
        self.objection_policy = objection_policy
        self.context_builder = context_builder
        self.action_policy = action_policy
        self.tool_registry = tool_registry
        self.compliance_filter = compliance_filter
        self.output_validator = output_validator
        self.call_stop_policy = call_stop_policy
        self.pii_redactor = pii_redactor
        self.repository = repository or Repository()
        self.event_callback = event_callback
        self.response_builder = ResponseBuilder()
        
        self.events: list[RuntimeEvent] = []

        # Lazy-import state handlers to prevent circular dependencies
        from states.age import AgeState
        from states.beneficiary import BeneficiaryState
        from states.budget import BudgetState
        from states.callback import CallbackState
        from states.disqualified import DisqualifiedState
        from states.dnc import DNCState
        from states.interest import InterestState
        from states.objection import ObjectionState
        from states.opening import OpeningState
        from states.permission import PermissionState
        from states.phone_type import PhoneTypeState
        from states.state_location import StateLocationState
        from states.text_capable import TextCapableState
        from states.transfer_ready import TransferReadyState

        self._state_handlers: dict[CallStage, Any] = {
            CallStage.OPENING: OpeningState(),
            CallStage.PERMISSION: PermissionState(),
            CallStage.AGE: AgeState(),
            CallStage.STATE: StateLocationState(),
            CallStage.PHONE_TYPE: PhoneTypeState(),
            CallStage.TEXT_CAPABLE: TextCapableState(),
            CallStage.BUDGET: BudgetState(),
            CallStage.BENEFICIARY: BeneficiaryState(),
            CallStage.INTEREST: InterestState(),
            CallStage.OBJECTION: ObjectionState(),
            CallStage.TRANSFER_READY: TransferReadyState(),
            CallStage.DISQUALIFIED: DisqualifiedState(),
            CallStage.CALLBACK: CallbackState(),
            CallStage.DNC: DNCState(),
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process_turn(
        self,
        user_text: str,
        chat_fn: Optional[Callable[[str], Awaitable[str]]] = None,
    ) -> RuntimeResult:
        """Process one user turn through the complete runtime pipeline.

        Args:
            user_text: The user's spoken utterance.
            chat_fn: Optional function that takes dynamic system instructions and
                returns the LLM's response. If omitted, the response is simulated
                using stage response/objection guidance.

        Returns:
            A :class:`RuntimeResult` with the response and turn outcomes.
        """
        lead = self.state_machine.lead
        call_state = self.state_machine.call_state

        # 1. Update turn count
        call_state.increment_turn()
        current_turn = call_state.turn_count

        # 2. Publish utterance received event & save to storage
        self._publish_event(
            UtteranceReceivedEvent(
                call_id=lead.call_id,
                text=user_text,
                current_stage=call_state.current_stage.value,
            )
        )
        try:
            await self.repository.save_call_turn(
                call_id=lead.call_id,
                turn_number=current_turn * 2 - 1,
                speaker="user",
                text=user_text,
                stage=call_state.current_stage.value,
            )
        except Exception as exc:
            logger.error("Failed to save user turn to repository: %s", exc)

        # 3. Check call stop policy first
        stop_decision = self.call_stop_policy.should_stop(user_text, call_state)
        if stop_decision.should_stop:
            target_stage = CallStage.DNC if stop_decision.stop_type == "dnc" else CallStage.END
            from_stage = call_state.current_stage
            
            # Transition
            self.state_machine.transition(target_stage.value)
            self._publish_event(
                StateTransitionEvent(
                    call_id=lead.call_id,
                    from_stage=from_stage.value,
                    to_stage=target_stage.value,
                )
            )

            # Fire DNC/Callback tool immediately if appropriate
            tool_results = []
            if target_stage == CallStage.DNC:
                lead.do_not_call_requested = True
                tool = self.tool_registry.get_tool("mark_dnc")
                if tool:
                    res = await tool.execute({
                        "phone_number": lead.phone_type or "unknown",
                        "reason": stop_decision.reason,
                        "requested_by": "prospect",
                        "call_id": lead.call_id,
                    })
                    tool_results.append(res.message)
                    await self._log_tool_event(lead.call_id, "mark_dnc", res)

            # Build final message
            response_text = (
                "I understand. We will remove your number from our list immediately. "
                "Have a nice day."
                if target_stage == CallStage.DNC
                else "I understand. Thank you for your time. Goodbye."
            )

            # Log agent turn and lead snapshot
            await self._log_agent_turn(lead.call_id, current_turn * 2, response_text, target_stage.value)
            await self._save_lead_snapshot(lead.call_id, target_stage.value)

            return RuntimeResult(
                agent_response=response_text,
                stage=target_stage.value,
                tool_results=tool_results,
                compliance_ok=True,
                should_end_call=True,
            )

        # 4. Classify objections
        objection_intent = self.objection_classifier.classify(user_text)
        objection_guidance = None
        if objection_intent:
            call_state.increment_objections()
            objection_guidance = self.objection_policy.get_response_guidance(objection_intent)
            self._publish_event(
                ObjectionDetectedEvent(
                    call_id=lead.call_id,
                    utterance=user_text,
                    intent=objection_intent,
                    confidence=0.8,
                )
            )

        # 5. Invoke current state handler
        current_stage = call_state.current_stage
        handler = self._state_handlers.get(current_stage)
        if handler:
            try:
                handler_result = handler.handle(user_text, lead, call_state)
            except Exception as exc:
                logger.error("Error in handler for %s: %s", current_stage.value, exc)
                handler_result = StateResult(
                    response_guidance="Acknowledge politely and ask for details again."
                )
        else:
            handler_result = StateResult(
                response_guidance="Respond naturally based on the conversation."
            )

        # Apply handler outcomes to lead profile
        self.state_machine.apply_result(handler_result)

        # 6. Determine state transition
        next_stage = handler_result.next_stage
        
        # Objection policy stage resolution overrides if it's a DNC/CALLBACK/END transition
        if objection_guidance:
            target_stage = self._resolve_target_stage(objection_guidance.next_stage)
            if objection_guidance.should_end_call or target_stage in (CallStage.DNC, CallStage.CALLBACK, CallStage.END):
                next_stage = target_stage
            elif next_stage is None:
                next_stage = target_stage

        # Apply stage transition
        if next_stage and next_stage != current_stage:
            from_stage = call_state.current_stage
            self.state_machine.transition(next_stage.value)
            self._publish_event(
                StateTransitionEvent(
                    call_id=lead.call_id,
                    from_stage=from_stage.value,
                    to_stage=next_stage.value,
                )
            )
        
        # Ensure we check age boundaries and disqualify underage/overage leads
        if call_state.current_stage == CallStage.AGE and lead.age is not None:
            # Enforce configuration limits
            age_config = self.prompt_loader.get_config("final_expense_config")
            min_age = age_config.get("disqualifier_age_min", 45)
            max_age = age_config.get("disqualifier_age_max", 85)
            if not (min_age <= lead.age <= max_age):
                lead.disqualified_reason = f"Age {lead.age} is outside eligible range ({min_age}-{max_age})"
                from_stage = call_state.current_stage
                self.state_machine.transition(CallStage.DISQUALIFIED.value)
                self._publish_event(
                    StateTransitionEvent(
                        call_id=lead.call_id,
                        from_stage=from_stage.value,
                        to_stage=CallStage.DISQUALIFIED.value,
                    )
                )

        # 7. Query RAG context
        rag_query = user_text
        if objection_guidance:
            rag_query += f" objection: {objection_guidance.intent}"
        
        rag_context = ""
        try:
            rag_context = self.context_builder.build_context(
                query=rag_query,
                call_stage=call_state.current_stage.value,
                lead_profile=lead.to_summary_dict(),
                objection_type=objection_guidance.intent if objection_guidance else None,
            )
        except Exception as exc:
            logger.error("RAG context builder error: %s", exc)

        # 8. Build prompt instructions for LLM
        instructions = self.response_builder.build_instructions(
            call_state=call_state,
            lead_profile=lead,
            objection_guidance=objection_guidance,
            rag_context=rag_context,
            stage_handler_result=handler_result,
        )

        # 9. Generate agent response
        if chat_fn:
            try:
                agent_response = await chat_fn(instructions)
            except Exception as exc:
                logger.error("LLM chat function failed: %s", exc)
                agent_response = self._simulate_response(handler_result, objection_guidance)
        else:
            agent_response = self._simulate_response(handler_result, objection_guidance)

        # 10. Redact PII
        redacted = self.pii_redactor.redact(agent_response)
        agent_response = redacted.redacted_text

        # 11. Run output validation and compliance checks
        compliance_res = self.compliance_filter.check(agent_response)
        output_val_res = self.output_validator.validate(agent_response, call_state.current_stage.value)
        
        compliance_ok = compliance_res.is_safe and output_val_res.is_valid
        if not compliance_ok:
            issues = compliance_res.violations + output_val_res.issues
            self._publish_event(
                ValidationFailedEvent(
                    call_id=lead.call_id,
                    response=agent_response,
                    validator_type="compliance" if compliance_res.violations else "formatting",
                    issues=issues,
                )
            )

            # Recovery: Fallback to a compliant, safe response if there's a compliance violation
            if not compliance_res.is_safe:
                if call_state.current_stage == CallStage.TRANSFER_READY:
                    agent_response = (
                        "I understand. Let me connect you with a licensed benefits coordinat-"
                        "or who can look up all those options and answer pricing details. Hold on."
                    )
                else:
                    agent_response = (
                        "A licensed agent would be the best person to cover pricing and approval. "
                        "I am just helping to see if you qualify. May I ask a few quick questions?"
                    )

        # 12. Determine and fire recommended actions/tools
        tool_results = []
        recommended_actions = self.action_policy.get_recommended_actions(
            call_state, lead.to_summary_dict()
        )
        for action in recommended_actions:
            try:
                tool = self.tool_registry.get_tool(action.tool_name)
            except KeyError as exc:
                logger.error("Recommended action tool '%s' not found: %s", action.tool_name, exc)
                continue
            if tool:
                params = {**action.params}
                if action.tool_name == "save_lead":
                    params["lead_profile"] = lead.to_summary_dict()
                elif action.tool_name == "transfer_to_agent":
                    params["call_id"] = lead.call_id
                    params["lead_summary"] = lead.to_summary_dict()
                    params["transfer_reason"] = action.reason
                elif action.tool_name == "schedule_callback":
                    params["call_id"] = lead.call_id
                    params["lead_name"] = f"{lead.first_name or ''} {lead.last_name or ''}".strip() or "Prospect"
                    params["callback_time"] = (
                        datetime.now(timezone.utc).isoformat()  # standard default
                    )
                    params["phone_number"] = lead.phone_type or "unknown"
                elif action.tool_name == "mark_dnc":
                    params["call_id"] = lead.call_id
                    params["phone_number"] = lead.phone_type or "unknown"
                    params["reason"] = action.reason
                elif action.tool_name == "escalate_to_human":
                    params["call_id"] = lead.call_id
                    params["reason"] = action.reason
                    params["urgency"] = "high"
                    params["lead_summary"] = lead.to_summary_dict()

                try:
                    res = await tool.execute(params)
                    tool_results.append(res.message)
                    await self._log_tool_event(lead.call_id, action.tool_name, res)
                    self._publish_event(
                        ToolTriggeredEvent(
                            call_id=lead.call_id,
                            tool_name=action.tool_name,
                            params=params,
                            success=res.success,
                            result_message=res.message,
                            error=res.error,
                        )
                    )
                except Exception as exc:
                    logger.error("Error executing tool %s: %s", action.tool_name, exc)
                    tool_results.append(str(exc))

        # 13. Publish ResponseGeneratedEvent & Save agent turn and snapshot
        self._publish_event(
            ResponseGeneratedEvent(
                call_id=lead.call_id,
                text=agent_response,
                stage=call_state.current_stage.value,
            )
        )
        await self._log_agent_turn(lead.call_id, current_turn * 2, agent_response, call_state.current_stage.value)
        await self._save_lead_snapshot(lead.call_id, call_state.current_stage.value)

        # Check if the next stage should end the call (DNC, DISQUALIFIED, END)
        should_end = call_state.current_stage in (CallStage.DNC, CallStage.DISQUALIFIED, CallStage.END)

        return RuntimeResult(
            agent_response=agent_response,
            stage=call_state.current_stage.value,
            extracted_data=handler_result.extracted_data,
            tool_results=tool_results,
            compliance_ok=compliance_ok,
            should_end_call=should_end,
        )

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    def _publish_event(self, event: RuntimeEvent) -> None:
        """Publish a runtime event, appending to local history and triggering callback."""
        self.events.append(event)
        if self.event_callback:
            try:
                self.event_callback(event)
            except Exception as exc:
                logger.error("Error in event callback: %s", exc)

    def _resolve_target_stage(self, policy_stage: str) -> CallStage:
        """Map objection policy stage strings to CallStage enum values."""
        if policy_stage in ("qualifying", "continue"):
            return self.state_machine.get_next_stage()
        elif policy_stage in ("callback_scheduled", "callback"):
            return CallStage.CALLBACK
        elif policy_stage in ("end_call", "closing", "end"):
            return CallStage.END
        
        try:
            return CallStage(policy_stage)
        except ValueError:
            return self.state_machine.current_stage

    @staticmethod
    def _simulate_response(
        handler_result: StateResult,
        objection_guidance: Optional[ObjectionGuidance],
    ) -> str:
        """Simulate an agent response from objection or handler guidance for test/mock modes."""
        if objection_guidance:
            # If the objection guidance defines a suggested response, extract it
            for line in objection_guidance.guidance_text.split("\n"):
                if line.startswith("Suggested response:"):
                    return line.replace("Suggested response:", "").strip()
            return objection_guidance.guidance_text.strip()
        return handler_result.response_guidance.strip()

    async def _log_agent_turn(self, call_id: str, turn_number: int, text: str, stage: str) -> None:
        """Log the agent's turn to the repository."""
        try:
            await self.repository.save_call_turn(
                call_id=call_id,
                turn_number=turn_number,
                speaker="agent",
                text=text,
                stage=stage,
            )
        except Exception as exc:
            logger.error("Failed to log agent turn to repository: %s", exc)

    async def _log_tool_event(self, call_id: str, tool_name: str, res: Any) -> None:
        """Log a tool execution event to the repository."""
        try:
            await self.repository.save_tool_event(
                call_id=call_id,
                tool_name=tool_name,
                params={},  # can populate if needed
                result=res.message if hasattr(res, "message") else str(res),
            )
        except Exception as exc:
            logger.error("Failed to log tool event to repository: %s", exc)

    async def _save_lead_snapshot(self, call_id: str, stage: str) -> None:
        """Save a snapshot of the current lead profile to the repository."""
        try:
            await self.repository.save_lead_snapshot(
                call_id=call_id,
                lead_profile=self.state_machine.lead.to_summary_dict(),
                stage=stage,
            )
        except Exception as exc:
            logger.error("Failed to save lead snapshot to repository: %s", exc)
