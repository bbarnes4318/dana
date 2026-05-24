import asyncio
import httpx
import time
import os
from dotenv import load_dotenv

load_dotenv()

async def run_test():
    base_url = os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1")
    url = f"{base_url}/chat/completions"
    
    print(f"Connecting to vLLM at: {url}")
    payload = {
        "model": os.getenv("DANA_LLM_MODEL", "meta-llama/Llama-3.1-8B-Instruct"),
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello! Say hi in exactly one word."}
        ],
        "temperature": 0.45,
        "max_tokens": 70,
        "stream": True
    }
    
    start_time = time.perf_counter()
    first_token_time = None
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            async with client.stream("POST", url, json=payload) as response:
                if response.status_code != 200:
                    print(f"Error: status code {response.status_code}")
                    return
                
                async for line in response.iter_lines():
                    if first_token_time is None and line.strip().startswith("data:"):
                        first_token_time = time.perf_counter()
                        latency = (first_token_time - start_time) * 1000.0
                        print(f"First token latency: {latency:.2f}ms")
                    if line.strip():
                        print(line)
        
        end_time = time.perf_counter()
        total_duration = (end_time - start_time) * 1000.0
        print(f"\nTotal streaming time: {total_duration:.2f}ms")
    except Exception as e:
        print(f"Failed to connect to vLLM server: {e}")
        print("Make sure the vLLM server is running and accessible.")

if __name__ == "__main__":
    asyncio.run(run_test())
