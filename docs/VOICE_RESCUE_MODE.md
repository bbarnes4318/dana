# Emergency Premium Live Voice Rescue Mode

This guide provides instructions and environment configurations to establish a fast, high-quality, professional humanlike voice path for Dana, bypassing the higher latency/lower quality local voice synthesis.

## 1. Emergency Premium Live Rescue Profile

Use this profile for the first live customer tests to guarantee the fastest and highest quality response.

> [!IMPORTANT]
> **RECOMMENDED FOR FIRST LIVE CUSTOMER TESTS**

```env
# Primary Mode Selection
DANA_VOICE_MODE=premium_live
DANA_TTS_PROVIDER=elevenlabs
DANA_TTS_ROUTING_MODE=cloud
DANA_ALLOW_CLOUD_TTS_FALLBACK=true

# Streaming Response Enforcements
DANA_ENABLE_STREAMING_RESPONSE=true
DANA_ENABLE_AUDIO_FILTERS=false

# Cloud LLM Routing Configurations
DANA_LLM_ROUTING_MODE=cloud
DANA_ALLOW_CLOUD_LLM_FALLBACK=true

# Latency and Turn-taking Optimization parameters
DANA_MAX_TOKENS=55
DANA_TEMPERATURE=0.25
DANA_TURN_MIN_DELAY=0.08
DANA_TURN_MAX_DELAY=0.35
DANA_PREEMPTIVE_GENERATION=true
```

## 2. Local-Cost Profile (Alternative)

This profile utilizes local resources to minimize API expenses. Use it for development, offline testing, or internal runs where voice quality and latency are secondary.

> [!WARNING]
> **NOT FOR FIRST LIVE CUSTOMER TESTS**

```env
# Primary Mode Selection
DANA_VOICE_MODE=local_cost
DANA_TTS_PROVIDER=local
DANA_TTS_ROUTING_MODE=local
DANA_ALLOW_CLOUD_TTS_FALLBACK=false

# Streaming & Processing Settings
DANA_ENABLE_STREAMING_RESPONSE=true
DANA_ENABLE_AUDIO_FILTERS=false

# Local LLM Routing Configurations
DANA_LLM_ROUTING_MODE=local
DANA_ALLOW_CLOUD_LLM_FALLBACK=false
```
