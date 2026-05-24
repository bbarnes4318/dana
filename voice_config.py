"""
Dana Voice Agent — Centralized Configuration

All runtime configuration is read from environment variables with safe defaults.
The env_* helpers treat None, empty strings, and whitespace-only strings as
"not set" and fall back to the provided default — no crashes on bad input.
"""

import os
from dataclasses import dataclass, field
from typing import Optional


# =============================================================================
# Safe Environment Helpers
# =============================================================================

def env_str(key: str, default: str = "") -> str:
    """Read an env var as a string. Returns *default* when the var is unset,
    empty, or whitespace-only."""
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return default
    return val.strip()


def env_int(key: str, default: int = 0) -> int:
    """Read an env var as an int. Returns *default* when the var is unset,
    empty, whitespace-only, or not parseable as an integer."""
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return default
    try:
        return int(val.strip())
    except (ValueError, TypeError):
        return default


def env_float(key: str, default: float = 0.0) -> float:
    """Read an env var as a float. Returns *default* when the var is unset,
    empty, whitespace-only, or not parseable as a float."""
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return default
    try:
        return float(val.strip())
    except (ValueError, TypeError):
        return default


def env_bool(key: str, default: bool = False) -> bool:
    """Read an env var as a boolean.

    Truthy: ``"true"``, ``"1"``, ``"yes"`` (case-insensitive).
    Falsy:  ``"false"``, ``"0"``, ``"no"`` (case-insensitive).
    Everything else (including empty/unset): *default*.
    """
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return default
    normalized = val.strip().lower()
    if normalized in ("true", "1", "yes"):
        return True
    if normalized in ("false", "0", "no"):
        return False
    return default


# =============================================================================
# Configuration Dataclass
# =============================================================================

@dataclass
class VoiceConfig:
    """Centralised, strongly-typed runtime configuration for the Dana voice agent."""

    # ---- vLLM / LLM ----
    vllm_base_url: str = field(default_factory=lambda: env_str("VLLM_BASE_URL", "http://vllm-server:8000/v1"))
    llm_model: str = field(default_factory=lambda: env_str("DANA_LLM_MODEL", "meta-llama/Llama-3.1-8B-Instruct"))
    max_tokens: int = field(default_factory=lambda: env_int("DANA_MAX_TOKENS", 70))
    temperature: float = field(default_factory=lambda: env_float("DANA_TEMPERATURE", 0.45))
    top_p: float = field(default_factory=lambda: env_float("DANA_TOP_P", 0.9))

    # ---- Agent Identity ----
    agent_name: str = field(default_factory=lambda: env_str("DANA_AGENT_NAME", "Alex"))
    company_name: str = field(default_factory=lambda: env_str("DANA_COMPANY_NAME", "American Beneficiary"))
    agent_prompt_path: str = field(default_factory=lambda: env_str("DANA_AGENT_PROMPT_PATH", "prompts/final_expense_alex.md"))

    # ---- Opening Behavior ----
    opening_mode: str = field(default_factory=lambda: env_str("DANA_OPENING_MODE", "wait_for_user"))
    opening_line: str = field(default_factory=lambda: env_str("DANA_OPENING_LINE", ""))

    # ---- STT ----
    stt_provider: str = field(default_factory=lambda: env_str("DANA_STT_PROVIDER", "local"))
    stt_model: str = field(default_factory=lambda: env_str("DANA_STT_MODEL", "large-v3-turbo"))
    stt_compute_type: str = field(default_factory=lambda: env_str("DANA_STT_COMPUTE_TYPE", "float16"))
    vad_threshold: float = field(default_factory=lambda: env_float("DANA_VAD_THRESHOLD", 0.5))
    min_silence_ms: int = field(default_factory=lambda: env_int("DANA_MIN_SILENCE_MS", 180))

    # ---- TTS ----
    tts_voice: str = field(default_factory=lambda: env_str("DANA_TTS_VOICE", "af_bella"))
    tts_speed: float = field(default_factory=lambda: env_float("DANA_TTS_SPEED", 1.03))

    # ---- Turn-Taking ----
    turn_min_delay: float = field(default_factory=lambda: env_float("DANA_TURN_MIN_DELAY", 0.15))
    turn_max_delay: float = field(default_factory=lambda: env_float("DANA_TURN_MAX_DELAY", 0.55))
    preemptive_generation: bool = field(default_factory=lambda: env_bool("DANA_PREEMPTIVE_GENERATION", True))

    # ---- Logging ----
    log_level: str = field(default_factory=lambda: env_str("LOG_LEVEL", "INFO"))
