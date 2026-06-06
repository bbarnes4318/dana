from voice.voice_provider_registry import get_voice_profile, list_voice_profiles
from voice.voice_profile import VoiceProfile

def test_profiles_defined():
    profiles = list_voice_profiles()
    assert "local_cost" in profiles
    assert "local_fast" in profiles
    assert "premium_humanlike" in profiles
    assert "fallback_safe" in profiles

def test_profile_fields():
    for name, profile in list_voice_profiles().items():
        assert isinstance(profile, VoiceProfile)
        assert profile.provider in ("kokoro", "elevenlabs")
        assert isinstance(profile.voice_name, str)
        assert isinstance(profile.sample_rate, int)
        assert isinstance(profile.expected_first_audio_target, int)
        assert isinstance(profile.quality_tier, str)
        assert isinstance(profile.estimated_cost_tier, str)
        assert isinstance(profile.allowed_in_production, bool)

def test_get_voice_profile():
    profile = get_voice_profile("local_cost")
    assert profile is not None
    assert profile.provider == "kokoro"
    
    none_profile = get_voice_profile("nonexistent_profile")
    assert none_profile is None
