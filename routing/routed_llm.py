"""Routed LLM Wrapper.

Subclasses livekit.agents.llm.LLM and dynamically routes chat calls to the
appropriate provider (local vLLM or cloud fallback) based on ModelRouter decisions.
Supports automatic local retries and fallback failover.
"""

from __future__ import annotations
import logging
import asyncio
from typing import AsyncIterable, Optional, Any

from livekit.agents import llm
from routing.model_router import ModelRouter, TrackLocalLLMTask
from routing.provider_health import record_failure

logger = logging.getLogger(__name__)

class RoutedLLM(llm.LLM):
    """Failover-safe wrapper for LLM services conforming to LiveKit's LLM interface."""

    def __init__(
        self,
        local_llm: llm.LLM,
        cloud_llm: Optional[llm.LLM],
        router: ModelRouter
    ) -> None:
        super().__init__()
        self.local_llm = local_llm
        self.cloud_llm = cloud_llm
        self.router = router

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        fenced_ctx: Optional[llm.ChatContext] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_tokens: Optional[int] = None,
        frequency_penalty: Optional[float] = None,
        presence_penalty: Optional[float] = None,
        **kwargs: Any
    ) -> RoutedLLMChatStream:
        """Route the chat call to the selected LLM provider stream."""
        from speech.context_registry import get_current_call_id, get_current_campaign_id
        call_id = get_current_call_id() or "unknown"
        campaign_id = get_current_campaign_id()

        # Initial provider selection
        provider = self.router.select_provider(
            component="llm",
            call_id=call_id,
            campaign_id=campaign_id
        )

        return RoutedLLMChatStream(
            routed_llm=self,
            chat_ctx=chat_ctx,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            provider=provider,
            call_id=call_id,
            campaign_id=campaign_id,
            kwargs=kwargs
        )


class RoutedLLMChatStream(llm.LLMStream):
    """An LLM stream that handles failover and retries internally."""

    def __init__(
        self,
        *,
        routed_llm: RoutedLLM,
        chat_ctx: llm.ChatContext,
        temperature: Optional[float],
        top_p: Optional[float],
        max_tokens: Optional[int],
        frequency_penalty: Optional[float],
        presence_penalty: Optional[float],
        provider: str,
        call_id: str,
        campaign_id: Optional[str],
        kwargs: Dict[str, Any]
    ) -> None:
        super().__init__()
        self.routed_llm = routed_llm
        self.chat_ctx = chat_ctx
        self.temperature = temperature
        self.top_p = top_p
        self.max_tokens = max_tokens
        self.frequency_penalty = frequency_penalty
        self.presence_penalty = presence_penalty
        self.provider = provider
        self.call_id = call_id
        self.campaign_id = campaign_id
        self.kwargs = kwargs
        self.active_stream = None
        self._concurrency_context = None

    async def _init_stream(self, provider: str) -> llm.LLMStream:
        """Instantiate the provider stream delegate."""
        if provider == "local":
            self._concurrency_context = TrackLocalLLMTask()
            self._concurrency_context.__enter__()
            try:
                return self.routed_llm.local_llm.chat(
                    chat_ctx=self.chat_ctx,
                    temperature=self.temperature,
                    top_p=self.top_p,
                    max_tokens=self.max_tokens,
                    frequency_penalty=self.frequency_penalty,
                    presence_penalty=self.presence_penalty,
                    **self.kwargs
                )
            except Exception as e:
                # Cleanup concurrency tracker on creation failure
                self._concurrency_context.__exit__(None, None, None)
                self._concurrency_context = None
                raise e
        else:
            # Cloud OpenAI fallback
            if not self.routed_llm.cloud_llm:
                raise RuntimeError("Cloud LLM requested but not configured.")
            
            # Clean kwargs - DO NOT pass LiveKit native tools to cloud LLM (Requirement 6)
            cloud_kwargs = {k: v for k, v in self.kwargs.items() if k not in ("tools",)}
            return self.routed_llm.cloud_llm.chat(
                chat_ctx=self.chat_ctx,
                temperature=self.temperature,
                top_p=self.top_p,
                max_tokens=self.max_tokens,
                frequency_penalty=self.frequency_penalty,
                presence_penalty=self.presence_penalty,
                **cloud_kwargs
            )

    async def _run(self, *args, **kwargs) -> AsyncIterable[llm.ChatChunk]:
        """Runs the stream iteration, catching failures to trigger retries/failover."""
        attempts = 0
        current_provider = self.provider

        try:
            while True:
                attempts += 1
                try:
                    # If cloud_unavailable is selected, fail directly
                    if current_provider == "cloud_unavailable":
                        raise RuntimeError("Cloud LLM provider has missing credentials.")

                    self.active_stream = await self._init_stream(current_provider)
                    # Yield all chunks from the active stream
                    async for chunk in self.active_stream:
                        yield chunk
                    # If completed successfully, break loop
                    break

                except Exception as exc:
                    logger.error(
                        f"LLM stream failure on provider '{current_provider}' "
                        f"(attempt {attempts}): {exc}"
                    )

                    # Clean up concurrency tracker if active for this failed attempt
                    if self._concurrency_context:
                        self._concurrency_context.__exit__(None, None, None)
                        self._concurrency_context = None

                    # Record the provider failure
                    record_failure(self.call_id, "llm", current_provider)

                    # Check if we should failover to cloud
                    mode = self.routed_llm.router.config.llm_routing_mode.lower()
                    fallback_allowed = self.routed_llm.router.config.allow_cloud_llm_fallback
                    has_cloud_creds = self.routed_llm.router.has_credentials("openai")

                    if current_provider == "local" and fallback_allowed and has_cloud_creds:
                        logger.info("Failing over from local LLM to cloud fallback (OpenAI).")
                        current_provider = "openai"
                        # Log the failover decision
                        self.routed_llm.router.log_decision(
                            component="llm",
                            call_id=self.call_id,
                            campaign_id=self.campaign_id,
                            provider="openai",
                            reason=f"local_failure: {exc}",
                            fallback_allowed=True
                        )
                        continue

                    # Local retry check if we cannot fallback
                    max_retries = self.routed_llm.router.config.llm_local_max_retries
                    if current_provider == "local" and attempts <= max_retries:
                        logger.info(f"Retrying local LLM (attempt {attempts} of {max_retries})...")
                        await asyncio.sleep(0.5)
                        continue

                    # If all retries/failovers fail, propagate the exception to trigger the AgentRuntime fallback phrasing
                    raise exc
        finally:
            if self._concurrency_context:
                self._concurrency_context.__exit__(None, None, None)
                self._concurrency_context = None

    async def aclose(self) -> None:
        """Clean up the active stream delegates."""
        if self._concurrency_context:
            self._concurrency_context.__exit__(None, None, None)
            self._concurrency_context = None
        if self.active_stream and hasattr(self.active_stream, "aclose"):
            await self.active_stream.aclose()
