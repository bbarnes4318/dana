from __future__ import annotations
import os
import aiohttp
from typing import AsyncIterable, Any
from livekit.plugins import openai as lk_openai
from dana.providers.base import LLMProvider

class OpenAILLMProvider(LLMProvider):
    def __init__(self, model: str = "gpt-4o-mini") -> None:
        self._model = model
        self._client = None

    @property
    def name(self) -> str:
        return "openai"

    @property
    def supports_streaming(self) -> bool:
        return True

    @property
    def supports_tools(self) -> bool:
        return True

    @property
    def estimated_input_cost_per_1m_tokens(self) -> float:
        return 0.15 if "mini" in self._model else 5.0

    @property
    def estimated_output_cost_per_1m_tokens(self) -> float:
        return 0.60 if "mini" in self._model else 15.0

    @property
    def average_first_token_ms(self) -> float:
        return 350.0

    async def health_check(self) -> bool:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return False
        # Do a simple models query to check auth
        url = "https://api.openai.com/v1/models"
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=2.0) as resp:
                    return resp.status == 200
        except Exception:
            return False

    def create_client(self) -> Any:
        if not self._client:
            self._client = lk_openai.LLM(
                model=self._model,
                api_key=os.getenv("OPENAI_API_KEY")
            )
        return self._client

    async def stream_response(self, chat_ctx: Any, **kwargs) -> AsyncIterable[str]:
        client = self.create_client()
        stream = client.chat(chat_ctx=chat_ctx, **kwargs)
        async for chunk in stream:
            content = chunk.delta.content if chunk.delta else ""
            if content:
                yield content
