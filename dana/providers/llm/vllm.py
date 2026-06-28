from __future__ import annotations
import os
import aiohttp
from typing import AsyncIterable, Any
from livekit.plugins import openai as lk_openai
from dana.providers.base import LLMProvider

class VLLMProvider(LLMProvider):
    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self._base_url = base_url or os.getenv("VLLM_BASE_URL", "http://127.0.0.1:8000/v1")
        self._model = model or os.getenv("VLLM_MODEL", "meta-llama/Llama-3.1-8B-Instruct")
        self._client = None

    @property
    def name(self) -> str:
        return "local_vllm"

    @property
    def supports_streaming(self) -> bool:
        return True

    @property
    def supports_tools(self) -> bool:
        return True

    @property
    def estimated_input_cost_per_1m_tokens(self) -> float:
        return 0.20

    @property
    def estimated_output_cost_per_1m_tokens(self) -> float:
        return 0.20

    @property
    def average_first_token_ms(self) -> float:
        return 120.0

    async def health_check(self) -> bool:
        # Check if vllm-server is up by doing a quick get or post to health / v1/models
        url = f"{self._base_url.rstrip('/')}/models"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=1.0) as resp:
                    return resp.status == 200
        except Exception:
            return False

    def create_client(self) -> Any:
        if not self._client:
            self._client = lk_openai.LLM(
                base_url=self._base_url,
                model=self._model,
                api_key=os.getenv("VLLM_API_KEY", "dummy-key")
            )
        return self._client

    async def stream_response(self, chat_ctx: Any, **kwargs) -> AsyncIterable[str]:
        client = self.create_client()
        # Call client.chat returns LLMStream
        stream = client.chat(chat_ctx=chat_ctx, **kwargs)
        async for chunk in stream:
            content = chunk.delta.content if chunk.delta else ""
            if content:
                yield content
