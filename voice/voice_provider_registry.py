"""Registry of standardized voice profiles for the Dana platform."""

from __future__ import annotations

from typing import Optional
from voice.voice_profile import VoiceProfile

_PROFILES = {
    "local_cost": VoiceProfile(
        provider="kokoro",
        voice_name="af_bella",
        sample_rate=24000,
        expected_first_audio_target=150,
        quality_tier="medium",
        estimated_cost_tier="lowest",
        allowed_in_production=True,
    ),
    "local_fast": VoiceProfile(
        provider="kokoro",
        voice_name="af_bella",
        sample_rate=24000,
        expected_first_audio_target=80,
        quality_tier="medium",
        estimated_cost_tier="lowest",
        allowed_in_production=True,
    ),
    "premium_humanlike": VoiceProfile(
        provider="elevenlabs",
        voice_name="V85zuuN9Jv2CfKdTl7PQ",  # ElevenLabs default premium voice
        sample_rate=24000,
        expected_first_audio_target=250,
        quality_tier="premium",
        estimated_cost_tier="premium",
        allowed_in_production=True,
    ),
    "fallback_safe": VoiceProfile(
        provider="kokoro",
        voice_name="af_bella",
        sample_rate=24000,
        expected_first_audio_target=200,
        quality_tier="medium",
        estimated_cost_tier="lowest",
        allowed_in_production=True,
    ),
}


def get_voice_profile(profile_name: str) -> Optional[VoiceProfile]:
    """Retrieves the voice profile configuration by name."""
    return _PROFILES.get(profile_name)


def list_voice_profiles() -> dict[str, VoiceProfile]:
    """Returns the dictionary of all registered voice profiles."""
    return _PROFILES.copy()
