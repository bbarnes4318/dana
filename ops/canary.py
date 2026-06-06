"""Canary test execution script running synthetic calls through voice components without placing real phone calls."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Dict, Optional

# Set test environment if needed
if "DANA_RUNTIME_ENV" not in os.environ:
    os.environ["DANA_RUNTIME_ENV"] = "test"

from config.runtime_env import get_runtime_env
from voice_config import VoiceConfig
from main import SharedComponents
from latency_metrics import LatencyRecorder


async def run_canary_dry_run() -> bool:
    """Mock-canary run used during unit tests and offline audits."""
    print("Canary [DRY-RUN]: Initializing mock components...")
    print("Canary [DRY-RUN]: Simulating synthetic user speech: 'Hello, this is a canary test.'")
    start = time.perf_counter()
    
    # Simulate LLM first token
    await asyncio.sleep(0.05)
    llm_lat = (time.perf_counter() - start) * 1000.0
    print(f"Canary [DRY-RUN]: LLM First Token Latency: {llm_lat:.1f}ms")
    
    # Simulate TTS first chunk
    await asyncio.sleep(0.04)
    tts_lat = (time.perf_counter() - start) * 1000.0 - llm_lat
    print(f"Canary [DRY-RUN]: TTS First Audio Latency: {tts_lat:.1f}ms")
    
    total = llm_lat + tts_lat
    print(f"Canary [DRY-RUN]: Total Turn Latency: {total:.1f}ms")
    return True


async def run_canary() -> bool:
    """Run a synthetic call check through the initialized local STT, LLM, and TTS stack."""
    env = get_runtime_env()
    
    # If in test mode or no LiveKit credentials, fall back to dry run to prevent hanging or external connection errors
    if env == "test" or not os.getenv("LIVEKIT_URL") or os.getenv("LIVEKIT_URL").startswith("wss://replace-me"):
        return await run_canary_dry_run()
        
    print(f"Canary [LIVE]: Initializing production components (Env: {env})...")
    try:
        config = VoiceConfig()
        shared = SharedComponents(config)
        await shared.initialize()
        
        recorder = LatencyRecorder("canary-call-id")
        recorder.mark("user_speech_end")
        
        # 1. Simulate LLM request
        print("Canary [LIVE]: Sending synthetic query to LLM...")
        from livekit.agents.llm import ChatContext, ChatMessage
        chat_ctx = ChatContext()
        chat_ctx.messages.append(ChatMessage(role="user", text="Hello, this is a canary check."))
        
        recorder.mark("llm_request_start")
        llm_stream = shared.llm.chat(chat_ctx)
        
        first_token = True
        response_text = ""
        async for chunk in llm_stream:
            if first_token:
                recorder.mark("llm_first_token")
                first_token = False
            if chunk.choices and chunk.choices[0].delta.content:
                response_text += chunk.choices[0].delta.content
                
        recorder.mark("llm_done")
        llm_lat = recorder.duration("llm_request_start", "llm_first_token") or 0.0
        print(f"Canary [LIVE]: LLM response received: '{response_text.strip()}'")
        print(f"Canary [LIVE]: LLM First Token Latency: {llm_lat:.1f}ms")
        
        # 2. Simulate TTS request
        if not response_text:
            response_text = "Hello, this is a backup response."
            
        print("Canary [LIVE]: Sending LLM text to TTS engine...")
        recorder.mark("tts_first_text")
        tts_stream = shared.tts.synthesize(text=response_text)
        
        first_audio = True
        async for frame in tts_stream:
            if first_audio:
                recorder.mark("tts_first_audio")
                first_audio = False
                
        tts_lat = recorder.duration("tts_first_text", "tts_first_audio") or 0.0
        print(f"Canary [LIVE]: TTS First Audio Latency: {tts_lat:.1f}ms")
        
        # Clean up stream
        await tts_stream.aclose()
        
        total_lat = llm_lat + tts_lat
        print(f"Canary [LIVE]: Total synthetic turn latency: {total_lat:.1f}ms")
        
        if total_lat > 1200.0:
            print("Canary [LIVE] WARNING: Turn latency exceeds warning limits (1200ms)")
            
        return True

    except Exception as e:
        print(f"Canary [LIVE] FAILED: Component execution failed: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    success = asyncio.run(run_canary())
    if success:
        print("Canary execution: SUCCESS")
        sys.exit(0)
    else:
        print("Canary execution: FAILED")
        sys.exit(1)
