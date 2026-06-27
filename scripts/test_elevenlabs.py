import asyncio
import os
import aiohttp
from dotenv import load_dotenv

async def test():
    load_dotenv()
    api_key = os.getenv("ELEVENLABS_API_KEY")
    voice_id = os.getenv("ACTIVE_TTS_VOICE", "hpp4J3VqNfWAUOO0d1Us")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    
    print(f"Making direct HTTP POST to: {url}")
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json"
    }
    payload = {"text": "Hello, can you hear me?"}
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, headers=headers, json=payload) as response:
                print(f"HTTP Status: {response.status}")
                body = await response.text()
                print(f"Response Body: {body}")
        except Exception as e:
            print(f"Connection/HTTP Error: {e}")

if __name__ == "__main__":
    asyncio.run(test())
