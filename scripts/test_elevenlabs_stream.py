import asyncio
import os
from livekit.plugins import elevenlabs
from dotenv import load_dotenv

async def test():
    load_dotenv()
    api_key = os.getenv("ELEVENLABS_API_KEY")
    voice_id = os.getenv("ACTIVE_TTS_VOICE", "hpp4J3VqNfWAUOO0d1Us")
    print(f"Testing ElevenLabs stream with key: {api_key[:10]}... and voice: {voice_id}")
    
    tts = elevenlabs.TTS(api_key=api_key, voice_id=voice_id, model="eleven_turbo_v2_5")
    stream = tts.stream()
    
    stream.push_text("Hello, can you hear me?")
    stream.end_input()
    
    print("Iterating over streaming audio frames...")
    try:
        count = 0
        async for ev in stream:
            print(f"Received frame: {len(ev.frame.data)} bytes")
            count += 1
        print(f"Stream finished. Total frames: {count}")
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(test())
