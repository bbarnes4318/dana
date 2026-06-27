# DANA Provider Matrix

This document lists all available STT, TTS, LLM, VAD, and Telephony providers supported by the Dana platform, along with their latencies, approximate costs, and required environment variables.

## Provider Options

### 1. Large Language Models (LLM)
| Provider | Model ID | First-Token Latency | Est. Cost / 1M Tokens | Environment Keys |
|---|---|---|---|---|
| **OpenAI** | `gpt-4o-mini` | ~350ms | $0.15 (in) / $0.60 (out) | `OPENAI_API_KEY` |
| **Local vLLM** | `meta-llama/...` | ~150ms | $0.00 | None (requires GPU server) |

### 2. Text-to-Speech (TTS)
| Provider | Voice ID | First-Audio Latency | Est. Cost / Minute | Environment Keys |
|---|---|---|---|---|
| **ElevenLabs** | `hpp4J3VqNfWA...` | ~400ms | $0.27 | `ELEVENLABS_API_KEY` |
| **Local Kokoro** | `local` | ~100ms | $0.00 | None |

### 3. Speech-to-Text (STT)
| Provider | Model ID | Transcript Latency | Est. Cost / Hour | Environment Keys |
|---|---|---|---|---|
| **Deepgram** | `large-v3-turbo`| ~150ms | $0.26 | `DEEPGRAM_API_KEY` |
| **Local Whisper** | `local` | ~200ms | $0.00 | None |

### 4. Voice Activity Detection (VAD)
| Provider | Avg. Detection | False Interrupt Risk | Cost | Details |
|---|---|---|---|---|
| **Silero** | ~70ms | Low (10%) | $0.00 | Tailored for elderly demographic (300ms silence threshold) |

### 5. Telephony
| Provider | Mode | Key Features | Cost | Details |
|---|---|---|---|---|
| **LiveKit SIP** | Outbound Trunk | Native LiveKit SIP stack | Trunk-dependent | Connected call pricing |

---

## Configuration Variables
Set these variables in your `.env` or Docker Compose setup to override defaults:
* `DANA_PROVIDER_MODE`: `locked` | `balanced` | `cheapest_safe`
* `DANA_LLM_PROVIDER`: `openai` | `local_vllm`
* `DANA_TTS_PROVIDER`: `elevenlabs` | `local_kokoro`
* `DANA_STT_PROVIDER`: `deepgram` | `local_faster_whisper`
* `DANA_VAD_PROVIDER`: `silero`
* `DANA_TELEPHONY_PROVIDER`: `livekit_sip`
