from __future__ import annotations
from typing import List, Any, Optional
from legacy.tts_service import LocallyHostedKokoro, TTSConfig
from dana.providers.base import TTSProvider

class KokoroTTSProvider(TTSProvider):
    def __init__(self, voice: str = "af_bella", speed: float = 1.03) -> None:
        self._voice = voice
        self._speed = speed
        self._client: Optional[LocallyHostedKokoro] = None

    @property
    def name(self) -> str:
        return "local_kokoro"

    @property
    def supports_streaming(self) -> bool:
        return True

    @property
    def supports_pcm(self) -> bool:
        return True

    @property
    def supports_ulaw(self) -> bool:
        return False

    @property
    def sample_rates(self) -> List[int]:
        return [16000]

    @property
    def estimated_cost_per_minute(self) -> float:
        return 0.0

    @property
    def average_first_audio_ms(self) -> float:
        return 150.0

    async def health_check(self) -> bool:
        try:
            client = self.synthesize_stream()
            await client.initialize()
            return client._initialized
        except Exception:
            return False

    def synthesize_stream(self) -> Any:
        if not self._client:
            self._client = LocallyHostedKokoro(TTSConfig(
                voice=self._voice,
                speed=self._speed,
                sample_rate=16000
            ))
        return self._client
