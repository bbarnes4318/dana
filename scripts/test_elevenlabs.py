import asyncio
import os
import aiohttp
from dotenv import load_dotenv

async def test():
    load_dotenv()
    api_key = os.getenv("ELEVENLABS_API_KEY")
    voice_id = os.getenv("ACTIVE_TTS_VOICE", "hpp4J3VqNfWAUOO0d1Us")
    # Build exact default url
    url = (
        f"wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/multi-stream-input?"
        f"model_id=eleven_turbo_v2_5"
        f"&output_format=mp3_22050_32"
        f"&enable_ssml_parsing=false"
        f"&enable_logging=true"
        f"&inactivity_timeout=20"
    )
    
    print(f"Connecting to WebSocket: {url}")
    headers = {
        "xi-api-key": api_key,
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.ws_connect(url, headers=headers) as ws:
                print("SUCCESS: Connected to ElevenLabs WebSocket!")
        except aiohttp.client_exceptions.WSServerHandshakeError as e:
            print(f"Handshake Error: {e.status} - {e.message}")
        except Exception as e:
            print(f"Connection Error: {e}")

if __name__ == "__main__":
    asyncio.run(test())
