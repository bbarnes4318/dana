"""Response builder for compiling dynamic system prompt instructions.

Assembles current call state, collected lead details, stage-specific guidance,
objection handling guidelines, and RAG context into a composite instruction
block for the LLM.
"""

from __future__ import annotations

from typing import Optional

from core.call_state import CallState, StateResult
from core.lead_profile import LeadProfile
from core.objection_response_policy import ObjectionGuidance


class ResponseBuilder:
    """Assembles dynamic runtime instructions for the LLM."""

    def build_instructions(
        self,
        call_state: CallState,
        lead_profile: LeadProfile,
        objection_guidance: Optional[ObjectionGuidance],
        rag_context: Optional[str],
        stage_handler_result: StateResult,
    ) -> str:
        """Create a dynamic instruction block to inject into the LLM system prompt.

        Args:
            call_state: The current mutable call state.
            lead_profile: The accumulated lead details.
            objection_guidance: Guidance from the objection policy engine, if any.
            rag_context: Relevant RAG documents formatted as context, if any.
            stage_handler_result: The result of the current stage handler execution.

        Returns:
            A formatted string containing system instructions for the next LLM turn.
        """
        parts = []

        # Call State and Summary
        parts.append("# CURRENT CONVERSATION STATE")
        parts.append(f"- Current Stage: {call_state.current_stage.value.upper()}")
        if call_state.previous_stage:
            parts.append(f"- Previous Stage: {call_state.previous_stage.value.upper()}")
        parts.append(f"- Conversational Turn Count: {call_state.turn_count}")
        parts.append(f"- Objections Encountered: {call_state.objection_count}")

        # Lead Profile info
        parts.append("\n# LEAD DATA COLLECTED")
        profile_dict = lead_profile.to_summary_dict()
        fields_to_list = [
            ("name", "Name"),
            ("age", "Age"),
            ("state", "State"),
            ("phone_type", "Phone Type"),
            ("can_receive_text", "Can Receive Text"),
            ("budget_confirmed", "Budget Confirmed"),
            ("has_existing_coverage", "Has Existing Coverage"),
            ("beneficiary_or_family_reason", "Beneficiary/Family Reason"),
            ("interest_level", "Interest Level"),
        ]
        for field_name, label in fields_to_list:
            val = profile_dict.get(field_name)
            parts.append(f"- {label}: {val if val is not None else 'Not yet collected'}")

        # Response Guidance from State Handler
        parts.append("\n# STAGE RESPONSE GUIDANCE")
        parts.append(
            stage_handler_result.response_guidance.strip()
            or "Respond naturally based on the conversation flow."
        )

        # Objection Guidance (if any)
        if objection_guidance:
            parts.append("\n# OBJECTION HANDLING INSTRUCTIONS")
            parts.append(f"An objection of type '{objection_guidance.intent}' was detected.")
            parts.append(objection_guidance.guidance_text.strip())
            if objection_guidance.compliance_warning:
                parts.append(f"COMPLIANCE WARNING: {objection_guidance.compliance_warning}")

        # RAG Context (if any)
        if rag_context and rag_context.strip():
            parts.append("\n# KNOWLEDGE CONTEXT (Use this to answer prospect questions)")
            parts.append(rag_context.strip())

        return "\n".join(parts)
