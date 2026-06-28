from __future__ import annotations
import os
import aiohttp
from typing import List, Any
from livekit.plugins import deepgram as lk_deepgram
from dana.providers.base import STTProvider

class DeepgramSTTProvider(STTProvider):
    def __init__(self, model: str = "nova-2-general") -> None:
        self._model = model
        self._client = None

    @property
    def name(self) -> str:
        return "deepgram"

    @property
    def supports_streaming(self) -> bool:
        return True

    @property
    def languages(self) -> List[str]:
        return ["en", "es", "fr", "de", "it", "pt"]

    @property
    def estimated_cost_per_minute(self) -> float:
        return 0.00432  # 0.000072 per second * 60

    @property
    def average_final_transcript_ms(self) -> float:
        return 150.0

    async def health_check(self) -> bool:
        api_key = os.getenv("DEEPGRAM_API_KEY")
        if not api_key:
            return False
        # POST to /v1/listen to check credentials and credit balance
        url = "https://api.deepgram.com/v1/listen"
        headers = {"Authorization": f"Token {api_key}"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, timeout=2.0) as resp:
                    # 401 (Unauthorized) and 402 (Payment Required) mean unhealthy/inactive.
                    # 400 (Bad Request) or 415 (Unsupported Media Type) indicate valid key/payment but empty payload.
                    return resp.status not in (401, 402)
        except Exception:
            return False

    def transcribe_stream(self) -> Any:
        if not self._client:
            self._client = lk_deepgram.STT(
                model=self._model,
                api_key=os.getenv("DEEPGRAM_API_KEY")
            )
        return self._client
