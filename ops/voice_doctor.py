"""Voice Quality and Latency Diagnostic Doctor for Dana.

Checks environment variables, local model files, credentials, and configuration
to identify why the voice quality is degraded or latency is high.
"""

import os
import sys
import json
import argparse
from pathlib import Path

def mask_credential(val: str | None) -> str:
    if not val:
        return "MISSING"
    val_clean = val.strip()
    if val_clean.lower() in ("replace_me", "replace-me", ""):
        return "PLACEHOLDER (INVALID)"
    if len(val_clean) <= 8:
        return "PRESENT (TOO SHORT/INSECURE)"
    return f"{val_clean[:4]}...{val_clean[-4:]}"

def get_voice_doctor_report() -> dict:
    from config.runtime_env import get_runtime_env, is_mock_tts_allowed
    
    # 1. Load env configuration
    env_vars = get_runtime_env()
    voice_mode = env_vars.get("voice_mode", "local_cost")
    tts_provider = env_vars.get("tts_provider", "local")
    tts_routing_mode = env_vars.get("tts_routing_mode", "local")
    enable_streaming = env_vars.get("enable_streaming_response", True)
    enable_audio_filters = env_vars.get("enable_audio_filters", False)
    
    # 2. Local model files exist
    model_path = os.environ.get("KOKORO_MODEL_PATH", "/root/.cache/kokoro/kokoro-v1.0.onnx")
    voices_path = os.environ.get("KOKORO_VOICES_PATH", "/root/.cache/kokoro/voices-v1.0.bin")
    
    if not os.path.exists(model_path) and os.path.exists("models/kokoro-v1.0.onnx"):
        model_path = "models/kokoro-v1.0.onnx"
        voices_path = "models/voices-v1.0.bin"
        
    local_model_exists = os.path.exists(model_path)
    local_voices_exists = os.path.exists(voices_path)
    
    # 3. Cloud credentials check
    el_key = os.environ.get("ELEVENLABS_API_KEY", "")
    has_el_creds = bool(el_key and el_key.lower() not in ("replace_me", "replace-me", ""))
    
    oa_key = os.environ.get("OPENAI_API_KEY", "")
    has_oa_creds = bool(oa_key and oa_key.lower() not in ("replace_me", "replace-me", ""))
    
    # 4. Active TTS provider & voice config
    active_tts_provider = tts_provider
    active_voice_id = "unknown"
    sample_rate = 16000
    expected_first_audio_target = "unknown"
    
    if active_tts_provider == "elevenlabs":
        active_voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "hpp4J3VqNfWAUOO0d1Us")
        expected_first_audio_target = "< 350ms (ElevenLabs low-latency)"
    elif active_tts_provider == "openai":
        active_voice_id = os.environ.get("OPENAI_TTS_VOICE", "alloy")
        expected_first_audio_target = "< 400ms (OpenAI TTS)"
    else:
        active_voice_id = os.environ.get("DANA_TTS_VOICE", "af_bella")
        expected_first_audio_target = "< 600ms (Local Kokoro ONNX GPU)"
        
    # 5. MockKokoro status
    mock_allowed = is_mock_tts_allowed()
    mock_active = mock_allowed and (not local_model_exists or not local_voices_exists)
    
    # 6. premium_live configuration check
    premium_live_configured = True
    premium_live_issues = []
    
    if voice_mode == "premium_live":
        if tts_provider not in ("elevenlabs", "openai"):
            premium_live_configured = False
            premium_live_issues.append(f"tts_provider must be 'elevenlabs' or 'openai', got '{tts_provider}'")
        if tts_provider == "elevenlabs" and not has_el_creds:
            premium_live_configured = False
            premium_live_issues.append("Missing ELEVENLABS_API_KEY")
        if tts_provider == "openai" and not has_oa_creds:
            premium_live_configured = False
            premium_live_issues.append("Missing OPENAI_API_KEY")
        if not enable_streaming:
            premium_live_configured = False
            premium_live_issues.append("enable_streaming_response must be True")
        if enable_audio_filters:
            premium_live_configured = False
            premium_live_issues.append("enable_audio_filters must be False")
        if mock_allowed:
            premium_live_configured = False
            premium_live_issues.append("allow_mock_tts must be False")
            
        llm_routing = env_vars.get("llm_routing_mode", "local")
        if llm_routing == "cloud" and not has_oa_creds:
            premium_live_configured = False
            premium_live_issues.append("DANA_LLM_ROUTING_MODE=cloud requires OPENAI_API_KEY")
    else:
        premium_live_configured = False
        premium_live_issues.append("DANA_VOICE_MODE is not set to 'premium_live'")
        
    return {
        "DANA_VOICE_MODE": voice_mode,
        "DANA_TTS_PROVIDER": tts_provider,
        "DANA_TTS_ROUTING_MODE": tts_routing_mode,
        "DANA_ENABLE_STREAMING_RESPONSE": enable_streaming,
        "DANA_ENABLE_AUDIO_FILTERS": enable_audio_filters,
        "local_kokoro_model_exists": local_model_exists,
        "local_kokoro_voice_exists": local_voices_exists,
        "elevenlabs_credentials_present": has_el_creds,
        "openai_credentials_present": has_oa_creds,
        "active_tts_provider": active_tts_provider,
        "active_voice_id": active_voice_id,
        "sample_rate": sample_rate,
        "expected_first_audio_target": expected_first_audio_target,
        "mock_tts_active": mock_active,
        "is_mock_tts_allowed": mock_allowed,
        "premium_live_correctly_configured": premium_live_configured,
        "premium_live_issues": premium_live_issues,
        "masked_keys": {
            "ELEVENLABS_API_KEY": mask_credential(os.environ.get("ELEVENLABS_API_KEY")),
            "OPENAI_API_KEY": mask_credential(os.environ.get("OPENAI_API_KEY")),
        }
    }

def main():
    parser = argparse.ArgumentParser(description="Dana Voice Doctor")
    parser.add_argument("--json", action="store_true", help="Output only valid JSON results")
    args = parser.parse_args()
    
    report = get_voice_doctor_report()
    
    if args.json:
        print(json.dumps(report, indent=2))
        sys.exit(0 if report["premium_live_correctly_configured"] or report["DANA_VOICE_MODE"] != "premium_live" else 1)
        
    print("=" * 80)
    print(" DANA VOICE PIPELINE DIAGNOSTIC REPORT (VOICE DOCTOR)")
    print("=" * 80)
    print(f"  DANA_VOICE_MODE                 : {report['DANA_VOICE_MODE'].upper()}")
    print(f"  DANA_TTS_PROVIDER               : {report['DANA_TTS_PROVIDER']}")
    print(f"  DANA_TTS_ROUTING_MODE           : {report['DANA_TTS_ROUTING_MODE']}")
    print(f"  DANA_ENABLE_STREAMING_RESPONSE  : {str(report['DANA_ENABLE_STREAMING_RESPONSE']).upper()}")
    print(f"  DANA_ENABLE_AUDIO_FILTERS       : {str(report['DANA_ENABLE_AUDIO_FILTERS']).upper()}")
    print("-" * 80)
    print(f"  Local Kokoro Model File Exists  : {str(report['local_kokoro_model_exists']).upper()}")
    print(f"  Local Kokoro Voice File Exists  : {str(report['local_kokoro_voice_exists']).upper()}")
    print(f"  ElevenLabs Credentials Present  : {str(report['elevenlabs_credentials_present']).upper()}")
    print(f"  OpenAI Credentials Present      : {str(report['openai_credentials_present']).upper()}")
    print("-" * 80)
    print(f"  Active TTS Provider             : {report['active_tts_provider'].upper()}")
    print(f"  Active Voice ID/Name            : {report['active_voice_id']}")
    print(f"  Sample Rate                     : {report['sample_rate']} Hz")
    print(f"  Expected First Audio Latency    : {report['expected_first_audio_target']}")
    print(f"  MockKokoro Active               : {str(report['mock_tts_active']).upper()}")
    print("-" * 80)
    
    if report["DANA_VOICE_MODE"] == "premium_live":
        if report["premium_live_correctly_configured"]:
            print("  PREMIUM LIVE CONFIGURATION      : [VALID]")
        else:
            print("  PREMIUM LIVE CONFIGURATION      : [INVALID]")
            print("  Issues:")
            for issue in report["premium_live_issues"]:
                print(f"    - {issue}")
    else:
        print("  PREMIUM LIVE CONFIGURATION      : [BYPASSED] (Not running in premium_live mode)")
        
    print("-" * 80)
    print("Masked Credentials:")
    for k, v in report["masked_keys"].items():
        print(f"  {k:<32}: {v}")
    print("=" * 80)
    
    if report["DANA_VOICE_MODE"] == "premium_live" and not report["premium_live_correctly_configured"]:
        sys.exit(1)
    sys.exit(0)

if __name__ == "__main__":
    main()
