# Dana Outbound Voice AI Agent - Cleanup & Refactoring Audit

This audit document defines the cleanup map and refactoring plan for transitioning the Dana voice agent codebase into a unified, modular, production-ready runtime.

## 1. Current Entrypoints
* **`main.py`**: The primary entry point for the LiveKit agent, setting up the worker and running `cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))`. Defines `DanaAgent` and `SharedComponents`.
* **`telephony/livekit_agent_worker.py`**: An alternative, duplicated entry point with its own `SimpleAgent` and custom live-check/dependency routing logic. This is redundant and confusing.
* **`telephony/live_smoke_test.py`** & **`telephony/live_call_tester.py`**: Experimental runner scripts containing custom dialing and local runtime invocation loops.

## 2. Current Voice Session Paths
* **Production Path**: `main.py` (LiveKit worker) -> `LiveKitRuntimeAdapter` -> `AgentRuntime` -> `process_turn`/`process_turn_stream`.
* **Duplicated Path**: `telephony/livekit_agent_worker.py` (with customized and outdated audio loop handling, parallel AMD loops).
* **Audio Routing Hacks**:
  - `DANA_ENABLE_LIVEKIT_AUDIO_MONKEYPATCH` patches `livekit.agents.voice.room_io._output._ParticipantAudioOutput._forward_audio` and `_wait_for_playout` to support event-loop bypass for direct FFI playback.
  - `DANA_ENABLE_DIRECT_FFI_TTS_PUSH` bypasses normal LiveKit playout and pushes raw FFI chunks.

## 3. Current Providers (LLM, TTS, STT, VAD, Telephony)
* **LLM**:
  - **Local vLLM**: `OpenAILLM` hitting local vLLM server (`VLLM_BASE_URL`).
  - **OpenAI**: Configured in `ModelRouter` / `RoutedLLM`.
* **TTS**:
  - **Local Kokoro ONNX**: `LocallyHostedTTS` in `tts_service.py`.
  - **ElevenLabs**: Used as a cloud fallback.
  - **OpenAI TTS**: Configured as an alternate fallback.
  - **Mock TTS**: Activated via `allow_mock_tts` / `DANA_ALLOW_MOCK_TTS`, yielding silence/empty bytes.
* **STT**:
  - **Local Faster-Whisper**: `LocallyHostedSTT` in `stt_service.py`.
  - **Deepgram**: Used as cloud STT fallback.
* **VAD / Turn Detection**:
  - **Silero VAD**: Integrated via LiveKit and `ElderlySileroVAD` in `speech/custom_vad.py`.
  - **Semantic Turn Detector**: `speech/semantic_turn_detector.py` evaluates token structures for completion.
* **Telephony / Transport**:
  - **LiveKit SIP**: Handled through room connection and Telnyx SIP Trunk integrations.
* **Cost Routing**:
  - Checked via `ModelRouter` which routes calls depending on GPU load, line quality, and campaign metadata, but lacks a structured routing engine (e.g. `cheapest_safe`, `fastest`).

## 4. Current Env/Config Sources
* `voice_config.py` compiles env variables with safe defaults.
* `.env.production` contains production environment settings.
* Multiple duplicate configuration files exist: `.env`, `.env.example`, `.env.production.example`, `config/agent_config.yaml`, `config/final_expense_config.yaml`.
* Fallbacks and overrides (e.g., `voice_mode = "premium_live"`) in `voice_config.py` forcefully alter routing settings, introducing inconsistency.

## 5. Duplicate, Conflicting, or Dead Code
* **Duplicated workers**: `telephony/livekit_agent_worker.py` duplicates `main.py`'s core LiveKit interaction code.
* **Direct FFI / Monkeypatching**: Direct manipulation of private LiveKit audio buffers is active in both `main.py` and `livekit_agent_worker.py`.
* **Mock TTS fallback**: Under failure, the system falls back to mock silence/dummy audio, hiding errors.
* **AMD Workers**: AMD runs asynchronously alongside the call runtime, occasionally auto-hanging up calls based on duration criteria instead of actual silence/voicemail status.

## 6. Files to Quarantine (Move to `legacy/`)
* `telephony/livekit_agent_worker.py` -> `legacy/livekit_agent_worker.py`
* `stt_service.py` -> `legacy/stt_service.py` (replaced by unified providers)
* `tts_service.py` -> `legacy/tts_service.py` (replaced by unified providers)
* `routing/routed_llm.py` -> `legacy/routed_llm.py`
* `routing/routed_tts.py` -> `legacy/routed_tts.py`
* `speech/hybrid_stt_router.py` -> `legacy/hybrid_stt_router.py`

## 7. Files to Keep / Refactor (Remain in Production)
* `main.py` -> Production entrypoint.
* `core/agent_runtime.py` -> Turn manager / orchestrator.
* `core/livekit_runtime_adapter.py` -> Bridging LiveKit session to runtime.
* `states/*` -> State machine definitions.
* `safety/*` -> Compliance policies and validators.
* `telephony/fe_transfer.py` -> Live call transfers.

## 8. Target Clean Architecture (Dana Registry & Routing)
We will refactor the codebase to implement:
1. `dana/runtime/voice_session.py`: The single LiveKit session runner.
2. `dana/runtime/call_context.py`: Isolates per-call variables, metadata, and cost metrics.
3. `dana/runtime/turn_manager.py`: Core processing of conversation turns.
4. `dana/providers/provider_registry.py`: Unified registry of standard interfaces (`LLMProvider`, `TTSProvider`, `STTProvider`, `VADProvider`, `TelephonyProvider`) with adapters for local/cloud models and stubs for future integrations.
5. `dana/config/voice_config.py` & `dana/config/cost_profiles.py`: Coherent config loaders.
6. A modular routing policy engine that resolves `cheapest_safe`, `fastest`, `highest_quality`, `balanced`, and `locked` modes.
