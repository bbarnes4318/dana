"""Runtime environment checks for the Dana voice platform.

Reads environment variables to determine whether the application is running
in development, test, or production mode, and whether mock/dummy TTS is allowed.
Also loads environment variables and returns a unified/normalized configuration dictionary.
"""

import os
from typing import Optional
from config.env_loader import load_environment

def parse_bool(val: Optional[str]) -> bool:
    """
    Parse a string value into a boolean.
    Supports case-insensitive 'true', 'yes', '1'.
    """
    if val is None:
        return False
    return val.strip().lower() in ("true", "yes", "1")

def get_runtime_env() -> dict:
    """
    Load environment variables and return a unified/normalized configuration dictionary.
    Exposes normalized keys and resolves aliases/precedence rules.
    Never prints or returns secret values in cleartext logs, but exposes the actual values
    for runtime use in the returned dictionary.
    """
    # Load env variables automatically
    load_environment()

    # 0. Active Telephony Provider
    provider = os.environ.get("DANA_TELEPHONY_PROVIDER")
    if provider:
        provider = provider.strip().lower()
    
    if provider not in ("telnyx", "bulkvs", "signalwire", "twilio", "mock"):
        if os.environ.get("TELNYX_API_KEY"):
            provider = "telnyx"
        elif os.environ.get("BULKVS_API_KEY"):
            provider = "bulkvs"
        else:
            provider = "mock"

    # 1. LiveKit credentials
    livekit_url = os.environ.get("LIVEKIT_URL")
    livekit_api_key = os.environ.get("LIVEKIT_API_KEY")
    livekit_api_secret = os.environ.get("LIVEKIT_API_SECRET")

    # 2. LiveKit SIP Outbound Trunk ID (alias mapping)
    if provider == "bulkvs":
        livekit_sip_outbound_trunk_id = (
            os.environ.get("BULKVS_LIVEKIT_SIP_OUTBOUND_TRUNK_ID") or
            os.environ.get("LIVEKIT_SIP_OUTBOUND_TRUNK_ID") or
            os.environ.get("DANA_LIVEKIT_SIP_OUTBOUND_TRUNK_ID")
        )
    else:
        livekit_sip_outbound_trunk_id = (
            os.environ.get("LIVEKIT_SIP_OUTBOUND_TRUNK_ID") or
            os.environ.get("DANA_LIVEKIT_SIP_OUTBOUND_TRUNK_ID") or
            os.environ.get("TELNYX_LIVEKIT_OUTBOUND_TRUNK_ID")
        )

    # 3. Outbound Caller ID resolution based on active provider
    outbound_caller_id = None
    outbound_caller_id_source = None

    if provider == "telnyx":
        val = os.environ.get("DANA_OUTBOUND_CALLER_ID")
        if val:
            outbound_caller_id = val.strip()
            outbound_caller_id_source = "DANA_OUTBOUND_CALLER_ID"
        else:
            val = os.environ.get("TELNYX_OUTBOUND_CALLER_ID")
            if val:
                outbound_caller_id = val.strip()
                outbound_caller_id_source = "TELNYX_OUTBOUND_CALLER_ID"
            else:
                dids = os.environ.get("TELNYX_DIDS", "")
                if dids:
                    parsed = [d.strip() for d in dids.split(",") if d.strip()]
                    if parsed:
                        outbound_caller_id = parsed[0]
                        outbound_caller_id_source = "TELNYX_DIDS"
                
                if not outbound_caller_id:
                    nums = os.environ.get("TELNYX_PHONE_NUMBERS", "")
                    if nums:
                        parsed = [n.strip() for n in nums.split(",") if n.strip()]
                        if parsed:
                            outbound_caller_id = parsed[0]
                            outbound_caller_id_source = "TELNYX_PHONE_NUMBERS"

    elif provider == "bulkvs":
        if os.environ.get("DANA_ALLOW_DANA_CALLER_ID_FOR_BULKVS", "").strip().lower() == "true":
            val = os.environ.get("DANA_OUTBOUND_CALLER_ID")
            if val:
                outbound_caller_id = val.strip()
                outbound_caller_id_source = "DANA_OUTBOUND_CALLER_ID"
        
        if not outbound_caller_id:
            val = os.environ.get("BULKVS_OUTBOUND_CALLER_ID")
            if val:
                outbound_caller_id = val.strip()
                outbound_caller_id_source = "BULKVS_OUTBOUND_CALLER_ID"
            else:
                dids = os.environ.get("BULKVS_DIDS", "")
                if dids:
                    parsed = [d.strip() for d in dids.split(",") if d.strip()]
                    if parsed:
                        outbound_caller_id = parsed[0]
                        outbound_caller_id_source = "BULKVS_DIDS"
                
                if not outbound_caller_id:
                    nums = os.environ.get("BULKVS_PHONE_NUMBERS", "")
                    if nums:
                        parsed = [n.strip() for n in nums.split(",") if n.strip()]
                        if parsed:
                            outbound_caller_id = parsed[0]
                            outbound_caller_id_source = "BULKVS_PHONE_NUMBERS"

    elif provider == "signalwire":
        val = os.environ.get("DANA_OUTBOUND_CALLER_ID")
        if val:
            outbound_caller_id = val.strip()
            outbound_caller_id_source = "DANA_OUTBOUND_CALLER_ID"
        else:
            val = os.environ.get("SIGNALWIRE_OUTBOUND_CALLER_ID")
            if val:
                outbound_caller_id = val.strip()
                outbound_caller_id_source = "SIGNALWIRE_OUTBOUND_CALLER_ID"
            else:
                dids = os.environ.get("SIGNALWIRE_DIDS", "")
                if dids:
                    parsed = [d.strip() for d in dids.split(",") if d.strip()]
                    if parsed:
                        outbound_caller_id = parsed[0]
                        outbound_caller_id_source = "SIGNALWIRE_DIDS"

    elif provider == "twilio":
        val = os.environ.get("DANA_OUTBOUND_CALLER_ID")
        if val:
            outbound_caller_id = val.strip()
            outbound_caller_id_source = "DANA_OUTBOUND_CALLER_ID"
        else:
            val = os.environ.get("TWILIO_CALLER_ID")
            if val:
                outbound_caller_id = val.strip()
                outbound_caller_id_source = "TWILIO_CALLER_ID"
            else:
                nums = os.environ.get("TWILIO_PHONE_NUMBERS", "")
                if nums:
                    parsed = [n.strip() for n in nums.split(",") if n.strip()]
                    if parsed:
                        outbound_caller_id = parsed[0]
                        outbound_caller_id_source = "TWILIO_PHONE_NUMBERS"

    elif provider == "mock":
        val = os.environ.get("DANA_OUTBOUND_CALLER_ID")
        if val:
            outbound_caller_id = val.strip()
            outbound_caller_id_source = "DANA_OUTBOUND_CALLER_ID"
        else:
            dids = os.environ.get("TELNYX_DIDS", "") or os.environ.get("TELNYX_PHONE_NUMBERS", "") or os.environ.get("SIGNALWIRE_DIDS", "") or os.environ.get("TWILIO_PHONE_NUMBERS", "")
            if dids:
                parsed = [d.strip() for d in dids.split(",") if d.strip()]
                if parsed:
                    outbound_caller_id = parsed[0]
                    outbound_caller_id_source = "FALLBACK_MOCK_DID"

    # 4. Test Call To
    test_call_to = (
        os.environ.get("DANA_TEST_CALL_TO") or
        os.environ.get("TEST_CALL_TO")
    )

    # 5. Live Call Enabled
    live_mode = os.environ.get("TELEPHONY_LIVE_MODE")
    dialer_enabled = os.environ.get("DANA_ENABLE_OUTBOUND_DIALER")
    confirm_place = os.environ.get("DANA_CONFIRM_PLACE_CALL")

    live_call_enabled = (
        parse_bool(live_mode) or
        parse_bool(dialer_enabled) or
        (confirm_place is not None and confirm_place.strip().lower() in ("yes", "true", "1"))
    )

    # 6. Worker Enabled
    worker_enabled_val = os.environ.get("DANA_AGENT_WORKER_ENABLED")
    enable_agent_worker = os.environ.get("DANA_ENABLE_AGENT_WORKER")

    worker_enabled = (
        parse_bool(worker_enabled_val) or
        parse_bool(enable_agent_worker)
    )

    # 7. Telnyx API Key
    telnyx_api_key = os.environ.get("TELNYX_API_KEY")
    bulkvs_api_key = os.environ.get("BULKVS_API_KEY")

    # Local-first Routing Modes & Fallbacks
    stt_routing_mode = os.environ.get("DANA_STT_ROUTING_MODE", "local")
    llm_routing_mode = os.environ.get("DANA_LLM_ROUTING_MODE", "local")
    tts_routing_mode = os.environ.get("DANA_TTS_ROUTING_MODE", "local")

    allow_cloud_llm_fallback = parse_bool(os.environ.get("DANA_ALLOW_CLOUD_LLM_FALLBACK", "false"))
    allow_cloud_tts_fallback = parse_bool(os.environ.get("DANA_ALLOW_CLOUD_TTS_FALLBACK", "false"))
    cloud_stt_on_failure = parse_bool(os.environ.get("DANA_CLOUD_STT_ON_FAILURE", "false"))

    # Local Engine config paths
    vllm_base_url = os.environ.get("VLLM_BASE_URL")
    kokoro_model_path = os.environ.get("KOKORO_MODEL_PATH")
    kokoro_voices_path = os.environ.get("KOKORO_VOICES_PATH")

    return {
        "livekit_url": livekit_url,
        "livekit_api_key": livekit_api_key,
        "livekit_api_secret": livekit_api_secret,
        "livekit_sip_outbound_trunk_id": livekit_sip_outbound_trunk_id,
        "outbound_caller_id": outbound_caller_id,
        "outbound_caller_id_source": outbound_caller_id_source,
        "active_provider": provider,
        "test_call_to": test_call_to,
        "live_call_enabled": live_call_enabled,
        "worker_enabled": worker_enabled,
        "telnyx_api_key": telnyx_api_key,
        "bulkvs_api_key": bulkvs_api_key,
        "stt_routing_mode": stt_routing_mode,
        "llm_routing_mode": llm_routing_mode,
        "tts_routing_mode": tts_routing_mode,
        "allow_cloud_llm_fallback": allow_cloud_llm_fallback,
        "allow_cloud_tts_fallback": allow_cloud_tts_fallback,
        "cloud_stt_on_failure": cloud_stt_on_failure,
        "vllm_base_url": vllm_base_url,
        "kokoro_model_path": kokoro_model_path,
        "kokoro_voices_path": kokoro_voices_path,
    }


def get_runtime_mode() -> str:
    """Returns the current runtime mode (development, test, or production).

    Defaults to 'development'.
    """
    env = os.getenv("DANA_RUNTIME_ENV", "development").strip().lower()
    if env in ("production", "prod"):
        return "production"
    if env in ("test", "testing"):
        return "test"
    return "development"


def is_production() -> bool:
    """Returns True if the application is running in production mode."""
    return get_runtime_mode() == "production"


def is_test() -> bool:
    """Returns True if the application is running in test mode."""
    return get_runtime_mode() == "test"


def allow_mock_tts() -> bool:
    """Returns True if mock TTS is explicitly allowed in the current environment.

    Defaults to False. Truthy values: true, 1, yes.
    """
    val = os.getenv("DANA_ALLOW_MOCK_TTS", "false").strip().lower()
    return val in ("true", "1", "yes")
