from __future__ import annotations
from typing import List, Any, Optional
from legacy.stt_service import LocallyHostedSTT, STTConfig
from dana.providers.base import STTProvider

class WhisperSTTProvider(STTProvider):
    def __init__(self, model_size: str = "large-v3-turbo", compute_type: str = "float16") -> None:
        self._model_size = model_size
        self._compute_type = compute_type
        self._client: Optional[LocallyHostedSTT] = None

    @property
    def name(self) -> str:
        return "local_faster_whisper"

    @property
    def supports_streaming(self) -> bool:
        return True

    @property
    def languages(self) -> List[str]:
        return ["en", "es", "fr", "de", "it"]

    @property
    def estimated_cost_per_minute(self) -> float:
        return 0.0

    @property
    def average_final_transcript_ms(self) -> float:
        return 400.0

    async def health_check(self) -> bool:
        try:
            client = self.transcribe_stream()
            await client.initialize()
            return client._initialized
        except Exception:
            return False

    def transcribe_stream(self) -> Any:
        if not self._client:
            self._client = LocallyHostedSTT(STTConfig(
                model_size=self._model_size,
                compute_type=self._compute_type
            ))
        return self._client
