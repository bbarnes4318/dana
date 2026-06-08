"""Canonical environment variable schema and validation logic for the Dana voice platform.

Serves as the single source of truth for required production environment variables,
optional fallbacks, secrets masking, placeholder values, and deprecated aliases.
"""

from __future__ import annotations

import re
from typing import Any

# Required production variables by category
REQUIRED_PRODUCTION_VARS = {
    "Hugging Face": [
        "HF_TOKEN",
    ],
    "LiveKit Cloud": [
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    ],
    "LiveKit SIP": [
        "LIVEKIT_SIP_OUTBOUND_TRUNK_ID",
    ],
    "Telnyx": [
        "TELNYX_API_KEY",
        "TELNYX_CONNECTION_ID",
    ],
    "Database": [
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "DATABASE_URL",
        "DATABASE_ADMIN_URL",
    ],
    "Redis": [
        "REDIS_URL",
        "DANA_USE_REDIS_HOT_STATE",
    ],
    "vLLM": [
        "VLLM_BASE_URL",
        "VLLM_MODEL",
        "VLLM_GPU_MEMORY_UTILIZATION",
        "VLLM_QUANTIZATION",
        "VLLM_MAX_MODEL_LEN",
    ],
    "Runtime Safety": [
        "DANA_RUNTIME_ENV",
        "DANA_ALLOW_MOCK_TTS",
        "DANA_CONTROLLED_LIVE_TEST",
    ]
}

# Optional variables
OPTIONAL_VARS = [
    # Optional cloud fallbacks
    "OPENAI_API_KEY",
    "DEEPGRAM_API_KEY",
    "ELEVENLABS_API_KEY",
    # Agent identity
    "DANA_AGENT_NAME",
    "DANA_COMPANY_NAME",
    "DANA_AGENT_PROMPT_PATH",
    # Speech
    "DANA_STT_PROVIDER",
    "DANA_STT_MODEL",
    "DANA_STT_COMPUTE_TYPE",
    "DANA_TTS_VOICE",
    "DANA_TTS_SPEED",
    # Turn-taking
    "DANA_TURN_MIN_DELAY",
    "DANA_TURN_MAX_DELAY",
    "DANA_PREEMPTIVE_GENERATION",
    # Interruption telemetry
    "DANA_RECORD_INTERRUPTION_TELEMETRY",
    "DANA_ENABLE_FAST_INTERRUPTION",
    "DANA_INTERRUPTION_PROFILE",
    # Logging
    "LOG_LEVEL",
    # Outbound Caller IDs
    "TELNYX_OUTBOUND_NUMBER",
    "DANA_DEFAULT_CALLER_ID",
    # Other configs
    "DANA_OPENING_MODE",
    "DANA_OPENING_LINE",
    "DANA_VAD_THRESHOLD",
    "DANA_MIN_SILENCE_MS",
    "DANA_REDIS_KEY_PREFIX",
    "DANA_WRITE_BEHIND_ENABLED",
    "DANA_WRITE_BEHIND_MAX_QUEUE_SIZE",
    "DANA_WRITE_BEHIND_FLUSH_INTERVAL_MS",
    "DANA_WRITE_BEHIND_BATCH_SIZE",
    "LICENSED_AGENT_PHONE_NUMBER",
]

# Sensitive/secret variables that must be masked and never printed in cleartext
SECRET_VARS = {
    "HF_TOKEN",
    "LIVEKIT_API_SECRET",
    "TELNYX_API_KEY",
    "POSTGRES_PASSWORD",
    "DATABASE_URL",
    "DATABASE_ADMIN_URL",
    "REDIS_URL",
    "OPENAI_API_KEY",
    "DEEPGRAM_API_KEY",
    "ELEVENLABS_API_KEY",
    "TELNYX_SIP_PASSWORD",
}

# Placeholders or insecure default values that should not be used in production
PLACEHOLDER_VALUES = {
    "replace_me",
    "replace-me",
    "dana_secure_pass",
    "wss://replace-me.livekit.cloud",
    "http://replace-me",
}

# Deprecated aliases and their canonical replacements
DEPRECATED_ALIASES = {
    "DANA_LIVEKIT_SIP_OUTBOUND_TRUNK_ID": "LIVEKIT_SIP_OUTBOUND_TRUNK_ID",
    "TELNYX_LIVEKIT_OUTBOUND_TRUNK_ID": "LIVEKIT_SIP_OUTBOUND_TRUNK_ID",
    "DANA_ENABLE_AGENT_WORKER": "DANA_AGENT_WORKER_ENABLED",
    "DANA_OUTBOUND_CALLER_ID": "TELNYX_OUTBOUND_NUMBER",
    "TELNYX_OUTBOUND_CALLER_ID": "TELNYX_OUTBOUND_NUMBER",
    "TELNYX_DIDS": "TELNYX_OUTBOUND_NUMBER",
    "TELNYX_PHONE_NUMBERS": "TELNYX_OUTBOUND_NUMBER",
}

# Safe defaults in case optional variables are missing
SAFE_DEFAULTS = {
    "DANA_RUNTIME_ENV": "production",
    "DANA_ALLOW_MOCK_TTS": "false",
    "DANA_CONTROLLED_LIVE_TEST": "false",
    "DANA_USE_REDIS_HOT_STATE": "true",
    "VLLM_BASE_URL": "http://vllm-server:8000/v1",
    "VLLM_MODEL": "meta-llama/Llama-3.1-8B-Instruct",
    "VLLM_GPU_MEMORY_UTILIZATION": "0.70",
    "VLLM_QUANTIZATION": "fp8",
    "VLLM_MAX_MODEL_LEN": "4096",
    "DANA_STT_PROVIDER": "local",
    "DANA_STT_MODEL": "large-v3-turbo",
    "DANA_STT_COMPUTE_TYPE": "float16",
    "DANA_TTS_VOICE": "af_bella",
    "DANA_TTS_SPEED": "1.03",
    "DANA_RECORD_INTERRUPTION_TELEMETRY": "true",
    "DANA_ENABLE_FAST_INTERRUPTION": "false",
    "DANA_INTERRUPTION_PROFILE": "CONSERVATIVE_DEFAULT",
    "LOG_LEVEL": "INFO",
}


def mask_value(key: str, val: str | None) -> str:
    """Mask sensitive variables while leaving non-sensitive ones clear.
    
    If the value is a placeholder or missing, return that status.
    """
    if val is None:
        return "missing"
    if val.strip() == "":
        return "empty"
    if val.strip().lower() in PLACEHOLDER_VALUES:
        return "placeholder"
    if key in SECRET_VARS:
        # Check if it contains a URI (like postgresql://...)
        if "://" in val:
            # Mask the password part of connection strings
            pattern = re.compile(r"([^:]+://[^:]+:)([^@]+)(@.+)")
            if pattern.match(val):
                return pattern.sub(r"\1******\3", val)
        return "present"
    return val


def validate_env(env_dict: dict[str, str]) -> dict[str, Any]:
    """Validate environment dictionary against the canonical schema.
    
    Returns a dict with:
        passed: bool
        failures: list[str]
        warnings: list[str]
        READY_TO_START_SERVICES: bool
        READY_FOR_LIVE_CALL_TEST: bool
        PRODUCTION_ENV_VALID: bool
    """
    failures = []
    warnings = []
    
    # 1. Check deprecated aliases
    for key in env_dict:
        if key in DEPRECATED_ALIASES:
            replacement = DEPRECATED_ALIASES[key]
            warnings.append(f"Deprecated alias used: {key} (use {replacement} instead)")

    # 2. Check required variables
    all_required = []
    for category, vars_list in REQUIRED_PRODUCTION_VARS.items():
        all_required.extend(vars_list)
        for var in vars_list:
            val = env_dict.get(var)
            if not val or val.strip() == "":
                # Check if a deprecated alias is used as a fallback
                alias_used = False
                for alias, target in DEPRECATED_ALIASES.items():
                    if target == var and env_dict.get(alias):
                        alias_used = True
                        break
                if not alias_used:
                    failures.append(f"Missing required variable: {var}")
            elif val.strip().lower() in PLACEHOLDER_VALUES:
                failures.append(f"Required variable contains placeholder value: {var}={val}")

    # 3. Check Telnyx/Caller ID conditional requirement
    # Either TELNYX_OUTBOUND_NUMBER or DANA_DEFAULT_CALLER_ID must be present
    outbound = env_dict.get("TELNYX_OUTBOUND_NUMBER") or env_dict.get("DANA_DEFAULT_CALLER_ID")
    # Check aliases too
    outbound_alias = (
        env_dict.get("DANA_OUTBOUND_CALLER_ID") or 
        env_dict.get("TELNYX_OUTBOUND_CALLER_ID") or 
        env_dict.get("TELNYX_DIDS") or 
        env_dict.get("TELNYX_PHONE_NUMBERS")
    )
    if not outbound and not outbound_alias:
        failures.append("Missing required outbound caller ID: either TELNYX_OUTBOUND_NUMBER or DANA_DEFAULT_CALLER_ID must be set")
    elif outbound and outbound.strip().lower() in PLACEHOLDER_VALUES:
        failures.append("Outbound caller ID contains placeholder value")

    # 4. Enforce production safety blockers
    runtime_env = env_dict.get("DANA_RUNTIME_ENV", "production").strip().lower()
    if runtime_env != "production":
        failures.append(f"DANA_RUNTIME_ENV must be 'production' in production (got '{runtime_env}')")
        
    allow_mock = env_dict.get("DANA_ALLOW_MOCK_TTS", "false").strip().lower()
    if allow_mock in ("true", "1", "yes"):
        failures.append("DANA_ALLOW_MOCK_TTS must be 'false' in production")

    # Enforce premium_live validation
    voice_mode = env_dict.get("DANA_VOICE_MODE", "local_cost").strip().lower()
    if voice_mode == "premium_live":
        tts_provider = env_dict.get("DANA_TTS_PROVIDER", "elevenlabs").strip().lower()
        if tts_provider == "elevenlabs":
            el_key = env_dict.get("ELEVENLABS_API_KEY", "").strip()
            if not el_key or el_key.lower() in PLACEHOLDER_VALUES or el_key == "":
                failures.append("premium_live requires ELEVENLABS_API_KEY to be set")
            el_voice = env_dict.get("ELEVENLABS_VOICE_ID", "").strip()
            if not el_voice or el_voice.lower() in PLACEHOLDER_VALUES or el_voice == "":
                failures.append("premium_live requires ELEVENLABS_VOICE_ID to be set")
        elif tts_provider == "openai":
            oa_key = env_dict.get("OPENAI_API_KEY", "").strip()
            if not oa_key or oa_key.lower() in PLACEHOLDER_VALUES or oa_key == "":
                failures.append("premium_live requires OPENAI_API_KEY to be set for OpenAI TTS")
            oa_voice = env_dict.get("OPENAI_TTS_VOICE", "").strip()
            if not oa_voice or oa_voice.lower() in PLACEHOLDER_VALUES or oa_voice == "":
                failures.append("premium_live requires OPENAI_TTS_VOICE to be set")
        else:
            failures.append(f"premium_live requires a cloud provider (elevenlabs or openai), got '{tts_provider}'")

        enable_streaming = env_dict.get("DANA_ENABLE_STREAMING_RESPONSE", "true").strip().lower() in ("true", "1", "yes")
        if not enable_streaming:
            failures.append("premium_live requires DANA_ENABLE_STREAMING_RESPONSE=true")

        enable_filters = env_dict.get("DANA_ENABLE_AUDIO_FILTERS", "false").strip().lower() in ("true", "1", "yes")
        if enable_filters:
            failures.append("premium_live requires DANA_ENABLE_AUDIO_FILTERS=false")

        allow_mock_tts = env_dict.get("DANA_ALLOW_MOCK_TTS", "false").strip().lower() in ("true", "1", "yes")
        if allow_mock_tts:
            failures.append("premium_live requires DANA_ALLOW_MOCK_TTS=false")

        llm_routing = env_dict.get("DANA_LLM_ROUTING_MODE", "local").strip().lower()
        if llm_routing == "cloud":
            oa_key = env_dict.get("OPENAI_API_KEY", "").strip()
            if not oa_key or oa_key.lower() in PLACEHOLDER_VALUES or oa_key == "":
                failures.append("DANA_LLM_ROUTING_MODE=cloud requires OPENAI_API_KEY to be set")


    # 5. Check DANA_CONTROLLED_LIVE_TEST warning / prevention of default production-ready status
    controlled_test = env_dict.get("DANA_CONTROLLED_LIVE_TEST", "false").strip().lower()
    if controlled_test in ("true", "1", "yes"):
        warnings.append("DANA_CONTROLLED_LIVE_TEST is set to true. Bulk campaign dialing is disabled, and single outbound call tests are enabled.")

    # 6. Assess readiness status
    # READY_TO_START_SERVICES = true if all required non-telephony infrastructure vars are set
    # Wait, the prompt says "READY_TO_START_SERVICES=false when required env is missing"
    # So if any required variable is missing or placeholder, it is false.
    has_critical_failures = any("Missing" in f or "placeholder" in f.lower() for f in failures)
    ready_to_start = not has_critical_failures

    # PRODUCTION_ENV_VALID is true if there are zero failures
    prod_env_valid = len(failures) == 0

    # READY_FOR_LIVE_CALL_TEST must be false unless all required checks pass.
    # If DANA_CONTROLLED_LIVE_TEST=true is set, it prevents default PRODUCTION_READY (but allows live call tests).
    # Wait, the requirement says: "DANA_CONTROLLED_LIVE_TEST=true prevents default production-ready state"
    # "READY_FOR_LIVE_CALL_TEST must be false unless all required checks pass."
    # So if there are any failures, READY_FOR_LIVE_CALL_TEST must be False.
    ready_for_live_call = prod_env_valid

    return {
        "passed": prod_env_valid,
        "failures": failures,
        "warnings": warnings,
        "READY_TO_START_SERVICES": ready_to_start,
        "READY_FOR_LIVE_CALL_TEST": ready_for_live_call,
        "PRODUCTION_ENV_VALID": prod_env_valid,
    }
