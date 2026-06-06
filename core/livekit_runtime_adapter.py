from __future__ import annotations

import logging
import asyncio
from typing import Callable, Awaitable, Optional, Any, AsyncIterable
from pathlib import Path

from core.agent_runtime import AgentRuntime, RuntimeResult
from core.state_machine import StateMachine
from core.lead_profile import LeadProfile
from core.call_state import CallState
from core.prompt_loader import PromptLoader
from core.objection_classifier import ObjectionClassifier
from core.objection_response_policy import ObjectionResponsePolicy
from rag.context_builder import ContextBuilder
from core.action_policy import ActionPolicy
from tools.tool_registry import ToolRegistry
from safety.compliance_filter import ComplianceFilter
from safety.output_validator import OutputValidator
from safety.call_stop_policy import CallStopPolicy
from safety.pii_redaction import PIIRedactor
from storage.repository import Repository
from livekit.agents import llm

logger = logging.getLogger(__name__)

class LiveKitRuntimeAdapter:
    """
    Adapter that connects LiveKit call sessions/events to the deterministic AgentRuntime.
    Created freshly per call to keep mutable state (LeadProfile, StateMachine, CallStopPolicy) isolated.
    """
    def __init__(
        self,
        call_id: str,
        phone_number: Optional[str] = None,
        project_root: Optional[Path] = None,
        prompt_loader: Optional[PromptLoader] = None,
        objection_classifier: Optional[ObjectionClassifier] = None,
        objection_policy: Optional[ObjectionResponsePolicy] = None,
        context_builder: Optional[ContextBuilder] = None,
        action_policy: Optional[ActionPolicy] = None,
        tool_registry: Optional[ToolRegistry] = None,
        compliance_filter: Optional[ComplianceFilter] = None,
        output_validator: Optional[OutputValidator] = None,
        pii_redactor: Optional[PIIRedactor] = None,
        repository: Optional[Repository] = None,
    ):
        self.call_id = call_id
        
        # 1. Instantiate per-call mutable state objects (Do NOT share across calls!)
        self.lead = LeadProfile(
            call_id=call_id,
            phone_type=phone_number or "unknown",
        )
        self.state_machine = StateMachine(lead_profile=self.lead)
        self.call_stop_policy = CallStopPolicy()  # Per-call so refusal counts don't leak!
        
        # 2. Setup shared/stateless dependencies (defaults if none provided)
        root = project_root or Path.cwd()
        self.prompt_loader = prompt_loader or PromptLoader(project_root=root)
        self.objection_classifier = objection_classifier or ObjectionClassifier()
        self.objection_policy = objection_policy or ObjectionResponsePolicy()
        self.context_builder = context_builder or ContextBuilder()
        self.action_policy = action_policy or ActionPolicy()
        self.tool_registry = tool_registry or ToolRegistry()
        self.compliance_filter = compliance_filter or ComplianceFilter()
        self.output_validator = output_validator or OutputValidator()
        self.pii_redactor = pii_redactor or PIIRedactor()
        self.repository = repository or Repository()
        
        # 3. Instantiate the deterministic AgentRuntime
        self.runtime = AgentRuntime(
            prompt_loader=self.prompt_loader,
            state_machine=self.state_machine,
            objection_classifier=self.objection_classifier,
            objection_policy=self.objection_policy,
            context_builder=self.context_builder,
            action_policy=self.action_policy,
            tool_registry=self.tool_registry,
            compliance_filter=self.compliance_filter,
            output_validator=self.output_validator,
            call_stop_policy=self.call_stop_policy,
            pii_redactor=self.pii_redactor,
            repository=self.repository,
        )

    async def process_user_turn(
        self,
        user_text: str,
        chat_fn: Callable[[str], Awaitable[str]],
        interrupted: bool = False,
    ) -> RuntimeResult:
        """
        Process the user turn through the deterministic AgentRuntime.
        Guaranteed to be called exactly once per user turn.
        """
        logger.info(f"Adapter: processing turn for call {self.call_id} with user_text='{user_text}' interrupted={interrupted}")
        result = await self.runtime.process_turn(user_text, chat_fn, interrupted=interrupted)
        logger.info(f"Adapter result: stage={result.stage}, "
            f"should_end_call={result.should_end_call}, compliance_ok={result.compliance_ok}"
        )
        return result

    async def process_user_turn_stream(
        self,
        user_text: str,
        chat_stream_fn: Callable[[str], AsyncIterable[str]],
        latency_recorder: Optional[Any] = None,
        interrupted: bool = False,
    ) -> AsyncIterable[llm.ChatChunk]:
        """
        Process the user turn through the deterministic AgentRuntime in a streaming fashion.
        Utilizes SafeClauseBuffer to emit the first safe clause immediately, while buffering and validating the rest.
        """
        logger.info(f"Adapter (streaming): processing turn for call {self.call_id} with user_text='{user_text}' interrupted={interrupted}")
        
        from core.streaming_response import SafeClauseBuffer
        import uuid
        chunk_id = f"chunk-{uuid.uuid4()}"
        
        # Initialize last_streaming_result to None
        self.last_streaming_result = None
        
        # 1. Run prepare_turn
        instructions, short_circuit_res = await self.runtime.prepare_turn(user_text, interrupted=interrupted)
        
        if short_circuit_res:
            logger.info(f"Adapter (streaming): immediate short-circuit to stage {short_circuit_res.stage}")
            self.last_streaming_result = short_circuit_res
            
            # Yield short-circuit response chunks immediately
            async for chunk in self.convert_response_to_stream(short_circuit_res.agent_response):
                yield chunk
            return
 
        # 2. Get prompt and run stream
        # Select backchannel or repair prefix
        if interrupted:
            backchannel = self.runtime.repair_policy.select_repair_prefix(
                self.state_machine.call_state.current_stage.value,
                self.state_machine.call_state.turn_count
            )
        else:
            backchannel = self.runtime.backchannel_policy.select_backchannel(
                current_stage=self.state_machine.call_state.current_stage.value,
                user_text=user_text,
                turn_count=self.state_machine.call_state.turn_count,
                objection_handled=getattr(self.runtime, "_streaming_objection_guidance", None) is not None,
            )

        buffer = SafeClauseBuffer()
        full_generated_text = ""
        
        # Establish assistant role chunk first
        yield llm.ChatChunk(
            id=chunk_id,
            delta=llm.ChoiceDelta(
                role="assistant",
                content="",
            )
        )

        # Prepend backchannel if present
        if backchannel:
            full_generated_text += backchannel + " "
            first_clause = buffer.process_chunk(backchannel + " ")
            if first_clause:
                logger.info(f"Adapter (streaming): Emitting prepended backchannel as first clause: '{first_clause}'")
                if latency_recorder:
                    latency_recorder.mark("first_safe_clause_detected")
                    latency_recorder.mark("first_streamed_tts_text")
                yield llm.ChatChunk(
                    id=chunk_id,
                    delta=llm.ChoiceDelta(
                        role="assistant",
                        content=first_clause,
                    )
                )

        try:
            async for token in chat_stream_fn(instructions):
                full_generated_text += token
                
                if not buffer.first_clause_emitted:
                    first_clause = buffer.process_chunk(token)
                    if first_clause:
                        logger.info(f"Adapter (streaming): Emitting first safe clause: '{first_clause}'")
                        if latency_recorder:
                            latency_recorder.mark("first_safe_clause_detected")
                            latency_recorder.mark("first_streamed_tts_text")
                        yield llm.ChatChunk(
                            id=chunk_id,
                            delta=llm.ChoiceDelta(
                                role="assistant",
                                content=first_clause,
                            )
                        )
                else:
                    # Keep buffering remainder
                    buffer.process_chunk(token)
                    
        except asyncio.CancelledError:
            logger.info("Adapter (streaming): generation cancelled by LiveKit (user barge-in)")
            buffer.clear()
            # Still run finalize on what we have so we can clean up/save state
            raise

        # Finalize buffer compliance check
        first_clause_final, remainder = buffer.finalize()
        
        if buffer.is_unsafe or (first_clause_final is None and not buffer.first_clause_emitted):
            # Fallback path
            logger.warning("Adapter (streaming): validation failed on stream. Falling back to full validation recovery.")
            if buffer.first_clause_emitted:
                # We already spoke the first clause, so we CANNOT yield any more text to avoid unsafe output
                logger.warning("Adapter (streaming): First clause already spoken, withholding remainder due to safety violation.")
                # We finalize using what was spoken so far (the first clause)
                result = await self.runtime.finalize_streaming_turn(buffer.first_clause or "", interrupted=interrupted)
            else:
                # We haven't spoken anything yet! Fall back to full-response validation path on full generated text
                result = await self.runtime.finalize_streaming_turn(full_generated_text, interrupted=interrupted)
                # Since we didn't yield anything, yield the safe finalized response in full
                async for chunk in self.convert_response_to_stream(result.agent_response):
                    yield chunk
        else:
            # Safe path
            result = await self.runtime.finalize_streaming_turn(full_generated_text, interrupted=interrupted)
            
            # Yield remainder if we already yielded the first clause, or yield full text if we didn't
            if buffer.first_clause_emitted:
                if remainder:
                    yield llm.ChatChunk(
                        id=chunk_id,
                        delta=llm.ChoiceDelta(
                            role="assistant",
                            content=remainder,
                        )
                    )
            else:
                # Yield full response
                async for chunk in self.convert_response_to_stream(result.agent_response):
                    yield chunk

        self.last_streaming_result = result
        logger.info(
            f"Adapter result (streaming): stage={result.stage}, "
            f"should_end_call={result.should_end_call}, compliance_ok={result.compliance_ok}"
        )

    @staticmethod
    def convert_response_to_stream(
        text: str,
        role: str = "assistant",
        chunk_size: int = 15,
        delay: float = 0.005,
    ) -> AsyncIterable[llm.ChatChunk]:
        """
        Converts the deterministic RuntimeResult.agent_response string into a stream
        of livekit.agents.llm.ChatChunk objects to play nicely with LiveKit's TTS chunking.
        """
        import uuid
        chunk_id = f"chunk-{uuid.uuid4()}"
        
        async def _generator():
            # Emit initial chunk establishing the assistant role
            yield llm.ChatChunk(
                id=chunk_id,
                delta=llm.ChoiceDelta(
                    role=role,
                    content="",
                )
            )
            
            # Yield content character/word segments
            for i in range(0, len(text), chunk_size):
                yield llm.ChatChunk(
                    id=chunk_id,
                    delta=llm.ChoiceDelta(
                        role=role,
                        content=text[i:i+chunk_size],
                    )
                )
                await asyncio.sleep(delay)
                
        return _generator()
