import asyncio
import os
import sys
import time
import tests.conftest
from dotenv import load_dotenv


# Ensure root directory is on path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voice_config import VoiceConfig
from main import SharedComponents
from latency_metrics import LatencyRecorder
from tts_service import normalize_text

load_dotenv()

async def main():
    print("=== Rebuilt Voice Stack Latency Smoke Test ===")
    config = VoiceConfig()
    shared = SharedComponents(config)
    
    # Initialize components
    try:
        print("Initializing shared components (STT, TTS, LLM)...")
        await shared.initialize()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Initialization failed: {e}")
        print("Note: If CUDA or vLLM is missing, running this script requires them to be up.")
        return
        
    call_id = "smoke-test-call-123"
    recorder = LatencyRecorder(call_id)
    recorder.mark("call_start")
    
    user_utterance = "Yes, I am interested, please tell me more."
    print(f"\nSimulating user utterance: '{user_utterance}'")
    
    recorder.mark("user_speech_end")
    recorder.mark("transcript_final")
    
    # Start LLM Generation
    print("Requesting LLM response...")
    recorder.mark("llm_request_start")
    
    from livekit.agents import llm
    chat_ctx = llm.ChatContext().append(
        role="system",
        text="You are Dana, respond to the user in exactly one short sentence."
    ).append(
        role="user",
        text=user_utterance
    )
    
    try:
        llm_stream = shared.llm.chat(
            chat_ctx=chat_ctx,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        )
        
        # Start TTS Stream
        recorder.mark("tts_first_text")
        tts_stream = shared.tts.stream()
        
        first_token = True
        
        # Pull from LLM and push to TTS concurrently
        async def push_loop():
            nonlocal first_token
            async for chunk in llm_stream:
                if first_token:
                    first_token = False
                    recorder.mark("llm_first_token")
                # Parse choice delta content
                if chunk.choices and chunk.choices[0].delta.content:
                    text_chunk = chunk.choices[0].delta.content
                    await tts_stream.push_text(text_chunk)
            recorder.mark("llm_done")
            await tts_stream.flush()
            
        push_task = asyncio.create_task(push_loop())
        
        # Read TTS frames
        first_audio = True
        frame_count = 0
        
        async for frame in tts_stream:
            if first_audio:
                first_audio = False
                recorder.mark("tts_first_audio")
                recorder.mark("first_audio_published")
            frame_count += 1
            
        await push_task
        await tts_stream.aclose()
        
        print(f"\nReceived {frame_count} audio frames from TTS.")
        recorder.log_summary()
        
    except Exception as e:
        print(f"Error during streaming execution: {e}")

if __name__ == "__main__":
    asyncio.run(main())
