from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import Optional

def env_str(key: str, default: str = "") -> str:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return default
    return val.strip()

def env_int(key: str, default: int = 0) -> int:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return default
    try:
        return int(val.strip())
    except (ValueError, TypeError):
        return default

def env_float(key: str, default: float = 0.0) -> float:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return default
    try:
        return float(val.strip())
    except (ValueError, TypeError):
        return default

def env_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return default
    normalized = val.strip().lower()
    if normalized in ("true", "1", "yes"):
        return True
    if normalized in ("false", "0", "no"):
        return False
    return default

@dataclass
class VoiceConfig:
    """Strongly-typed centralized configuration for the Dana voice engine."""

    # ---- Provider Routing Policy ----
    provider_mode: str = field(default_factory=lambda: env_str("DANA_PROVIDER_MODE", "balanced"))

    # ---- LLM Configuration ----
    llm_provider: str = field(default_factory=lambda: env_str("DANA_LLM_PROVIDER", "local_vllm"))
    llm_model: str = field(default_factory=lambda: env_str("DANA_LLM_MODEL", "meta-llama/Llama-3.1-8B-Instruct"))
    llm_fallback_provider: str = field(default_factory=lambda: env_str("DANA_LLM_FALLBACK_PROVIDER", "openai"))
    llm_fallback_model: str = field(default_factory=lambda: env_str("DANA_LLM_FALLBACK_MODEL", "gpt-4o-mini"))
    max_tokens: int = field(default_factory=lambda: env_int("DANA_MAX_TOKENS", 70))
    temperature: float = field(default_factory=lambda: env_float("DANA_TEMPERATURE", 0.2))
    top_p: float = field(default_factory=lambda: env_float("DANA_TOP_P", 0.9))

    # ---- TTS Configuration ----
    tts_provider: str = field(default_factory=lambda: env_str("DANA_TTS_PROVIDER", "local_kokoro"))
    tts_voice: str = field(default_factory=lambda: env_str("DANA_TTS_VOICE", "af_bella"))
    tts_speed: float = field(default_factory=lambda: env_float("DANA_TTS_SPEED", 1.03))
    tts_fallback_provider: str = field(default_factory=lambda: env_str("DANA_TTS_FALLBACK_PROVIDER", "local_kokoro"))

    # ---- STT Configuration ----
    stt_provider: str = field(default_factory=lambda: env_str("DANA_STT_PROVIDER", "local_faster_whisper"))
    stt_model: str = field(default_factory=lambda: env_str("DANA_STT_MODEL", "large-v3-turbo"))
    stt_compute_type: str = field(default_factory=lambda: env_str("DANA_STT_COMPUTE_TYPE", "float16"))
    stt_fallback_provider: str = field(default_factory=lambda: env_str("DANA_STT_FALLBACK_PROVIDER", "local_faster_whisper"))

    # ---- VAD Configuration ----
    vad_provider: str = field(default_factory=lambda: env_str("DANA_VAD_PROVIDER", "silero"))
    vad_threshold: float = field(default_factory=lambda: env_float("DANA_VAD_THRESHOLD", 0.5))
    min_silence_ms: int = field(default_factory=lambda: env_int("DANA_MIN_SILENCE_MS", 180))

    # ---- Telephony Configuration ----
    telephony_provider: str = field(default_factory=lambda: env_str("DANA_TELEPHONY_PROVIDER", "livekit_sip"))

    # ---- Agent General / Identity ----
    agent_name: str = field(default_factory=lambda: env_str("DANA_AGENT_NAME", "Alex"))
    company_name: str = field(default_factory=lambda: env_str("DANA_COMPANY_NAME", "American Beneficiary"))
    agent_prompt_path: str = field(default_factory=lambda: env_str("DANA_AGENT_PROMPT_PATH", "prompts/final_expense.production.md"))
    opening_mode: str = field(default_factory=lambda: env_str("DANA_OPENING_MODE", "immediate"))
    opening_line: str = field(default_factory=lambda: env_str("DANA_OPENING_LINE", "Hello?"))

    # ---- Turn-Taking Delays ----
    turn_min_delay: float = field(default_factory=lambda: env_float("DANA_TURN_MIN_DELAY", 0.25))
    turn_max_delay: float = field(default_factory=lambda: env_float("DANA_TURN_MAX_DELAY", 0.80))
    preemptive_generation: bool = field(default_factory=lambda: env_bool("DANA_PREEMPTIVE_GENERATION", True))
    endpoint_mode: str = field(default_factory=lambda: env_str("DANA_ENDPOINT_MODE", "fixed"))

    # ---- Infrastructure overrides / options ----
    log_level: str = field(default_factory=lambda: env_str("LOG_LEVEL", "INFO"))
    runtime_env: str = field(default_factory=lambda: env_str("DANA_RUNTIME_ENV", "development"))
    allow_mock_tts: bool = field(default_factory=lambda: env_bool("DANA_ALLOW_MOCK_TTS", False))
    vllm_base_url: str = field(default_factory=lambda: env_str("VLLM_BASE_URL", "http://vllm-server:8000/v1"))
    
    # ---- Hacks/Toggles (Defaults must be false in production) ----
    enable_livekit_audio_monkeypatch: bool = field(default_factory=lambda: env_bool("DANA_ENABLE_LIVEKIT_AUDIO_MONKEYPATCH", False))
    enable_direct_ffi_tts_push: bool = field(default_factory=lambda: env_bool("DANA_ENABLE_DIRECT_FFI_TTS_PUSH", False))
    enable_amd_worker: bool = field(default_factory=lambda: env_bool("DANA_ENABLE_AMD_WORKER", False))
    enable_audio_preprocessing: bool = field(default_factory=lambda: env_bool("DANA_ENABLE_AUDIO_PREPROCESSING", False))
    enable_pstn_bandpass: bool = field(default_factory=lambda: env_bool("DANA_ENABLE_PSTN_BANDPASS", False))
    enable_audio_filters: bool = field(default_factory=lambda: env_bool("DANA_ENABLE_AUDIO_FILTERS", False))
    audio_filter_profile: str = field(default_factory=lambda: env_str("DANA_AUDIO_FILTER_PROFILE", "none"))
    enable_fast_interruption: bool = field(default_factory=lambda: env_bool("DANA_ENABLE_FAST_INTERRUPTION", False))
    enable_streaming_response: bool = field(default_factory=lambda: env_bool("DANA_ENABLE_STREAMING_RESPONSE", True))
    
    # ---- Backwards Compatibility Fields ----
    voice_mode: str = field(default_factory=lambda: env_str("DANA_VOICE_MODE", "balanced"))
    record_interruption_telemetry: bool = field(default_factory=lambda: env_bool("DANA_RECORD_INTERRUPTION_TELEMETRY", True))
    allow_agent_barge_in: bool = field(default_factory=lambda: env_bool("DANA_ALLOW_AGENT_BARGE_IN", False))
    allow_cloud_tts_fallback: bool = field(default_factory=lambda: env_bool("DANA_ALLOW_CLOUD_TTS_FALLBACK", True))
    allow_cloud_llm_fallback: bool = field(default_factory=lambda: env_bool("DANA_ALLOW_CLOUD_LLM_FALLBACK", False))
    enable_experimental_audio_monkeypatch: bool = field(default_factory=lambda: env_bool("DANA_ENABLE_EXPERIMENTAL_AUDIO_MONKEYPATCH", False))
    enable_experimental_direct_ffi_audio: bool = field(default_factory=lambda: env_bool("DANA_ENABLE_EXPERIMENTAL_DIRECT_FFI_AUDIO", False))
    stt_routing_mode: str = field(default_factory=lambda: env_str("DANA_STT_ROUTING_MODE", "hybrid"))
    tts_routing_mode: str = field(default_factory=lambda: env_str("DANA_TTS_ROUTING_MODE", "hybrid"))
    llm_routing_mode: str = field(default_factory=lambda: env_str("DANA_LLM_ROUTING_MODE", "local"))
    interruption_profile: str = field(default_factory=lambda: env_str("DANA_INTERRUPTION_PROFILE", "CONSERVATIVE_DEFAULT"))

    # ---- Direct Response Controller ----
    direct_response_enabled: bool = field(default_factory=lambda: env_bool("DANA_DIRECT_RESPONSE_ON_FINAL_TRANSCRIPT", True))
    direct_response_queue_maxsize: int = field(default_factory=lambda: env_int("DANA_DIRECT_RESPONSE_QUEUE_MAXSIZE", 3))
    direct_response_dedupe_window_ms: int = field(default_factory=lambda: env_int("DANA_DIRECT_RESPONSE_DEDUPE_WINDOW_MS", 1200))
    direct_response_min_chars: int = field(default_factory=lambda: env_int("DANA_DIRECT_RESPONSE_MIN_CHARS", 2))
    direct_response_max_tokens_default: int = field(default_factory=lambda: env_int("DANA_DIRECT_RESPONSE_MAX_TOKENS", 70))
    direct_response_max_tokens_objection: int = field(default_factory=lambda: env_int("DANA_DIRECT_RESPONSE_MAX_TOKENS_OBJECTION", 90))
    direct_response_max_tokens_stop: int = field(default_factory=lambda: env_int("DANA_DIRECT_RESPONSE_MAX_TOKENS_STOP", 40))
    direct_response_hard_max_tokens: int = field(default_factory=lambda: env_int("DANA_DIRECT_RESPONSE_HARD_MAX_TOKENS", 100))
    direct_response_echo_similarity_threshold: float = field(default_factory=lambda: env_float("DANA_DIRECT_RESPONSE_ECHO_SIMILARITY_THRESHOLD", 0.78))
    direct_response_max_turn_ms: int = field(default_factory=lambda: env_int("DANA_DIRECT_RESPONSE_MAX_TURN_MS", 3500))

    def __post_init__(self) -> None:
        self.provider_mode = self.provider_mode.strip().lower()
        self.voice_mode = self.voice_mode.strip().lower()

        if self.voice_mode == "premium_live":
            self.tts_routing_mode = env_str("DANA_TTS_ROUTING_MODE", "cloud")
            self.allow_cloud_tts_fallback = env_bool("DANA_ALLOW_CLOUD_TTS_FALLBACK", True)
            self.tts_provider = env_str("DANA_TTS_PROVIDER", "elevenlabs")
            self.enable_streaming_response = env_bool("DANA_ENABLE_STREAMING_RESPONSE", True)
            self.stt_routing_mode = env_str("DANA_STT_ROUTING_MODE", "cloud")
            self.stt_provider = env_str("DANA_STT_PROVIDER", "deepgram")

        # Clamp direct response config values
        self.direct_response_queue_maxsize = max(1, min(10, self.direct_response_queue_maxsize))
        self.direct_response_dedupe_window_ms = max(250, min(5000, self.direct_response_dedupe_window_ms))
        self.direct_response_hard_max_tokens = max(40, min(140, self.direct_response_hard_max_tokens))
        
        # Keep old aliases only as warnings, not behavior-changing logic
        if os.getenv("DANA_VOICE_MODE") is not None:
            import logging
            logging.getLogger(__name__).warning(
                "WARNING: DANA_VOICE_MODE is deprecated and will not change routing behavior. "
                "Use DANA_PROVIDER_MODE instead."
            )
