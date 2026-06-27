import asyncio
import os
from livekit.plugins import elevenlabs
from dotenv import load_dotenv

async def test():
    load_dotenv()
    api_key = os.getenv("ELEVENLABS_API_KEY")
    voice_id = os.getenv("ACTIVE_TTS_VOICE", "hpp4J3VqNfWAUOO0d1Us")
    print(f"Testing ElevenLabs with key: {api_key[:10]}... and voice: {voice_id}")
    
    tts = elevenlabs.TTS(api_key=api_key, voice_id=voice_id)
    try:
        stream = tts.synthesize("Hello, can you hear me?")
        print("Synthesize stream created successfully. Iterating...")
        async for frame in stream:
            print("SUCCESS: Received audio frame!")
            return
        print("FAILED: Stream finished without returning any frames.")
    except Exception as e:
        print(f"ERROR: ElevenLabs synthesis failed: {e}")

if __name__ == "__main__":
    asyncio.run(test())
