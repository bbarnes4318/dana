"""Voice profile model definition."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceProfile:
    """Represents a standardized voice configuration profile."""

    provider: str
    voice_name: str
    sample_rate: int
    expected_first_audio_target: int  # expected first audio latency target in milliseconds
    quality_tier: str  # e.g., 'low', 'medium', 'high', 'premium'
    estimated_cost_tier: str  # e.g., 'lowest', 'low', 'medium', 'high', 'premium'
    allowed_in_production: bool
