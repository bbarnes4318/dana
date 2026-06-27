from __future__ import annotations
import os
import aiohttp
from typing import List, Any, Optional

try:
    from livekit.plugins import elevenlabs as lk_elevenlabs
    import_error = None
except ImportError as e:
    lk_elevenlabs = None
    import_error = str(e)

from dana.providers.base import TTSProvider

class ElevenLabsTTSProvider(TTSProvider):
    def __init__(self, voice_id: Optional[str] = None, model_id: str = "eleven_turbo_v2_5") -> None:
        self._voice_id = voice_id or os.getenv("ELEVENLABS_VOICE_ID", "hpp4J3VqNfWAUOO0d1Us").strip()
        self._model_id = model_id
        self._client = None

    @property
    def name(self) -> str:
        return "elevenlabs"

    @property
    def supports_streaming(self) -> bool:
        return True

    @property
    def supports_pcm(self) -> bool:
        return True

    @property
    def supports_ulaw(self) -> bool:
        return True

    @property
    def sample_rates(self) -> List[int]:
        return [16000, 24000, 44100]

    @property
    def estimated_cost_per_minute(self) -> float:
        return 0.27  # estimated average based on characters

    @property
    def average_first_audio_ms(self) -> float:
        return 400.0

    async def health_check(self) -> bool:
        if lk_elevenlabs is None:
            return False
        api_key = os.getenv("ELEVENLABS_API_KEY")
        if not api_key:
            return False
        # Call ElevenLabs user / voices endpoint to check auth
        url = "https://api.elevenlabs.io/v1/voices"
        headers = {"xi-api-key": api_key}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=2.0) as resp:
                    return resp.status == 200
        except Exception:
            return False

    def synthesize_stream(self) -> Any:
        if lk_elevenlabs is None:
            raise RuntimeError(f"ElevenLabs TTS plugin not installed: {import_error}")
        if not self._client:
            self._client = lk_elevenlabs.TTS(
                voice_id=self._voice_id,
                model=self._model_id,
                api_key=os.getenv("ELEVENLABS_API_KEY")
            )
        return self._client
