from __future__ import annotations
import logging
from typing import Callable, Awaitable, Optional, Any, AsyncIterable
from core.livekit_runtime_adapter import LiveKitRuntimeAdapter
from core.agent_runtime import RuntimeResult
from livekit.agents import llm
from dana.runtime.call_context import CallContext
from dana.providers.base import LLMProvider

logger = logging.getLogger(__name__)

class TurnManager:
    """Orchestrates single turns by interfacing with LiveKitRuntimeAdapter and the LLM provider."""
    
    def __init__(self, context: CallContext, adapter: LiveKitRuntimeAdapter) -> None:
        self.context = context
        self.adapter = adapter

    async def process_user_turn(
        self,
        user_text: str,
        llm_provider: LLMProvider,
        chat_ctx: llm.ChatContext,
        interrupted: bool = False
    ) -> RuntimeResult:
        """Process turn in non-streaming mode."""
        
        async def chat_fn(instructions: str) -> str:
            new_ctx = llm.ChatContext()
            loader = self.adapter.prompt_loader
            static_prompt = loader.build_system_prompt() if loader else ""
            combined_prompt = f"{static_prompt}\n\n{instructions}"
            
            new_ctx.add_message(role="system", content=combined_prompt)
            
            # Copy history
            for msg in chat_ctx.messages:
                if msg.role in ("user", "assistant"):
                    content = msg.content or ""
                    new_ctx.add_message(role=msg.role, content=content)
            
            # Estimate prompt tokens
            prompt_str = combined_prompt + "".join(msg.content or "" for msg in new_ctx.messages)
            from metrics.model_cost_metrics import estimate_llm_tokens
            self.context.prompt_tokens += estimate_llm_tokens(prompt_str)

            response_text = ""
            async for token in llm_provider.stream_response(
                new_ctx,
                temperature=self.adapter.runtime.prompt_loader.config.get("temperature", 0.2) if (self.adapter.prompt_loader and hasattr(self.adapter.prompt_loader, "config") and self.adapter.prompt_loader.config) else 0.2,
                max_tokens=70
            ):
                response_text += token

            self.context.completion_tokens += estimate_llm_tokens(response_text)
            return response_text

        result = await self.adapter.process_user_turn(user_text, chat_fn, interrupted=interrupted)
        self.context.current_turn_response = result.agent_response or ""
        return result

    async def process_user_turn_stream(
        self,
        user_text: str,
        llm_provider: LLMProvider,
        chat_ctx: llm.ChatContext,
        interrupted: bool = False
    ) -> AsyncIterable[llm.ChatChunk]:
        """Process turn in streaming mode."""
        
        async def chat_stream_fn(instructions: str) -> AsyncIterable[str]:
            new_ctx = llm.ChatContext()
            loader = self.adapter.prompt_loader
            static_prompt = loader.build_system_prompt() if loader else ""
            combined_prompt = f"{static_prompt}\n\n{instructions}"
            
            new_ctx.add_message(role="system", content=combined_prompt)
            
            # Copy history
            for msg in chat_ctx.messages:
                if msg.role in ("user", "assistant"):
                    content = msg.content or ""
                    new_ctx.add_message(role=msg.role, content=content)
            
            # Estimate prompt tokens
            prompt_str = combined_prompt + "".join(msg.content or "" for msg in new_ctx.messages)
            from metrics.model_cost_metrics import estimate_llm_tokens
            self.context.prompt_tokens += estimate_llm_tokens(prompt_str)

            async for token in llm_provider.stream_response(
                new_ctx,
                temperature=self.adapter.runtime.prompt_loader.config.get("temperature", 0.2) if (self.adapter.prompt_loader and hasattr(self.adapter.prompt_loader, "config") and self.adapter.prompt_loader.config) else 0.2,
                max_tokens=70
            ):
                yield token

        # Yield from process_user_turn_stream
        async for chunk in self.adapter.process_user_turn_stream(
            user_text,
            chat_stream_fn,
            latency_recorder=self.context.latency_recorder,
            interrupted=interrupted
        ):
            yield chunk

        # Track completion tokens on completion
        result = getattr(self.adapter, "last_streaming_result", None)
        if result:
            self.context.current_turn_response = result.agent_response or ""
            from metrics.model_cost_metrics import estimate_llm_tokens
            self.context.completion_tokens += estimate_llm_tokens(self.context.current_turn_response)
