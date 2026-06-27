from __future__ import annotations
from typing import Any
from speech.custom_vad import ElderlySileroVAD
from dana.providers.base import VADProvider

class SileroVADProvider(VADProvider):
    def __init__(self, threshold: float = 0.5, min_silence_ms: int = 180) -> None:
        self._threshold = threshold
        self._min_silence_ms = min_silence_ms
        self._detector = None

    @property
    def name(self) -> str:
        return "silero"

    @property
    def average_detection_ms(self) -> float:
        return 70.0

    @property
    def false_interrupt_risk(self) -> float:
        return 0.10

    async def health_check(self) -> bool:
        try:
            self.create_detector()
            return True
        except Exception:
            return False

    def create_detector(self) -> Any:
        if not self._detector:
            self._detector = ElderlySileroVAD.load(
                min_silence_duration=self._min_silence_ms / 1000.0,
                activation_threshold=self._threshold
            )
        return self._detector
