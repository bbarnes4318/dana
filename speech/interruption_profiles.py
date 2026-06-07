from dataclasses import dataclass
from typing import Any, Union
from core.call_state import CallStage

@dataclass
class InterruptionProfile:
    name: str
    min_silence_duration: float
    min_speech_duration: float
    activation_threshold: float
    deactivation_threshold: float
    interruption_speech_threshold: float  # Time in seconds user must speak to trigger barge-in

# Predefined profiles with conservative/patient defaults to avoid accidental disruption
CONSERVATIVE_DEFAULT = InterruptionProfile(
    name="CONSERVATIVE_DEFAULT",
    min_silence_duration=0.30,  # 300ms silence
    min_speech_duration=0.05,   # 50ms speech
    activation_threshold=0.40,
    deactivation_threshold=0.25,
    interruption_speech_threshold=0.12  # 120ms
)

OPENING_FAST = InterruptionProfile(
    name="OPENING_FAST",
    min_silence_duration=0.20,
    min_speech_duration=0.04,
    activation_threshold=0.35,
    deactivation_threshold=0.20,
    interruption_speech_threshold=0.08  # 80ms for responsive greeting catch
)

NORMAL = InterruptionProfile(
    name="NORMAL",
    min_silence_duration=0.25,
    min_speech_duration=0.05,
    activation_threshold=0.40,
    deactivation_threshold=0.25,
    interruption_speech_threshold=0.10  # 100ms standard
)

OBJECTION_PATIENT = InterruptionProfile(
    name="OBJECTION_PATIENT",
    min_silence_duration=0.40,
    min_speech_duration=0.06,
    activation_threshold=0.45,
    deactivation_threshold=0.30,
    interruption_speech_threshold=0.15  # 150ms patient check
)

TRANSFER_CONSENT_STRICT = InterruptionProfile(
    name="TRANSFER_CONSENT_STRICT",
    min_silence_duration=0.50,
    min_speech_duration=0.08,
    activation_threshold=0.50,
    deactivation_threshold=0.35,
    interruption_speech_threshold=0.18  # 180ms strict checks
)

DNC_IMMEDIATE = InterruptionProfile(
    name="DNC_IMMEDIATE",
    min_silence_duration=0.15,
    min_speech_duration=0.03,
    activation_threshold=0.30,
    deactivation_threshold=0.15,
    interruption_speech_threshold=0.05  # 50ms fast cut-off
)

WRONG_NUMBER_IMMEDIATE = InterruptionProfile(
    name="WRONG_NUMBER_IMMEDIATE",
    min_silence_duration=0.15,
    min_speech_duration=0.03,
    activation_threshold=0.30,
    deactivation_threshold=0.15,
    interruption_speech_threshold=0.05  # 50ms fast cut-off
)

PROFILES = {
    "CONSERVATIVE_DEFAULT": CONSERVATIVE_DEFAULT,
    "OPENING_FAST": OPENING_FAST,
    "NORMAL": NORMAL,
    "OBJECTION_PATIENT": OBJECTION_PATIENT,
    "TRANSFER_CONSENT_STRICT": TRANSFER_CONSENT_STRICT,
    "DNC_IMMEDIATE": DNC_IMMEDIATE,
    "WRONG_NUMBER_IMMEDIATE": WRONG_NUMBER_IMMEDIATE
}

def get_profile_for_stage(stage: Union[CallStage, str, None], config: Any) -> InterruptionProfile:
    """Resolve the interruption profile based on stage and config settings.
    
    If fast interruption is disabled, it will always fall back to the config-defined profile (defaulting to CONSERVATIVE_DEFAULT).
    """
    default_profile_name = getattr(config, "interruption_profile", "CONSERVATIVE_DEFAULT")
    default_profile = PROFILES.get(default_profile_name, CONSERVATIVE_DEFAULT)
    
    # Fast interruption disabled check (conservative default safety fallback)
    if not getattr(config, "enable_fast_interruption", False):
        return default_profile
        
    if stage is None:
        return default_profile
        
    # Standardize stage to lower string value
    stage_str = stage.value if isinstance(stage, CallStage) else str(stage).lower()
    
    if stage_str in ("opening", "answered"):
        return OPENING_FAST
    elif stage_str == "transfer_consent":
        return TRANSFER_CONSENT_STRICT
    elif stage_str == "dnc":
        return DNC_IMMEDIATE
    elif stage_str == "disqualified":
        return WRONG_NUMBER_IMMEDIATE
    elif stage_str in ("interest_check", "age_range", "living_situation", "decision_maker"):
        return NORMAL
        
    return default_profile
