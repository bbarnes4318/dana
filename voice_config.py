import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()

def get_env(key: str, default: str) -> str:
    """Helper to load environment variable, falling back to default if unset or empty."""
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return default
    return val

@dataclass
class VoiceConfig:
    opening_line: str = field(
        default_factory=lambda: get_env("DANA_OPENING_LINE", "Hey, this is Dana. Can you hear me okay?")
    )
    llm_model: str = field(
        default_factory=lambda: get_env("DANA_LLM_MODEL", "meta-llama/Llama-3.1-8B-Instruct")
    )
    max_tokens: int = field(
        default_factory=lambda: int(get_env("DANA_MAX_TOKENS", "70"))
    )
    temperature: float = field(
        default_factory=lambda: float(get_env("DANA_TEMPERATURE", "0.45"))
    )
    top_p: float = field(
        default_factory=lambda: float(get_env("DANA_TOP_P", "0.9"))
    )
    stt_model: str = field(
        default_factory=lambda: get_env("DANA_STT_MODEL", "large-v3-turbo")
    )
    stt_compute_type: str = field(
        default_factory=lambda: get_env("DANA_STT_COMPUTE_TYPE", "float16")
    )
    vad_threshold: float = field(
        default_factory=lambda: float(get_env("DANA_VAD_THRESHOLD", "0.5"))
    )
    min_silence_ms: int = field(
        default_factory=lambda: int(get_env("DANA_MIN_SILENCE_MS", "200"))
    )
    tts_voice: str = field(
        default_factory=lambda: get_env("DANA_TTS_VOICE", "af_bella")
    )
    tts_speed: float = field(
        default_factory=lambda: float(get_env("DANA_TTS_SPEED", "1.0"))
    )
    turn_min_delay: float = field(
        default_factory=lambda: float(get_env("DANA_TURN_MIN_DELAY", "0.15"))
    )
    turn_max_delay: float = field(
        default_factory=lambda: float(get_env("DANA_TURN_MAX_DELAY", "0.55"))
    )
    preemptive_generation: bool = field(
        default_factory=lambda: get_env("DANA_PREEMPTIVE_GENERATION", "true").lower() == "true"
    )
    vllm_base_url: str = field(
        default_factory=lambda: get_env("VLLM_BASE_URL", "http://localhost:8000/v1")
    )
