import asyncio
import time
import tests.conftest
from tts_service import FastPhraseChunker


async def run_test():
    print("=== Testing FastPhraseChunker ===")
    chunker = FastPhraseChunker()
    
    # 1. Punctuation flush test
    print("\n--- Test 1: Punctuation Flush ---")
    tokens = ["Hello", " ", "world", "!", " How", " are", " you?"]
    for token in tokens:
        phrases = chunker.feed(token)
        if phrases:
            print(f"Flushed phrases: {phrases}")
        await asyncio.sleep(0.02)
    flushed = chunker.flush()
    if flushed:
        print(f"Flushed at end: {flushed}")

    # 2. Length-boundary flush test (>= 18 chars)
    print("\n--- Test 2: Length boundary Flush (>= 18 chars) ---")
    tokens = ["I", " can", " help", " you", " with", " that", " today", " if", " you", " want"]
    for token in tokens:
        phrases = chunker.feed(token)
        if phrases:
            print(f"Flushed phrases: {phrases}")
        await asyncio.sleep(0.02)
    flushed = chunker.flush()
    if flushed:
        print(f"Flushed at end: {flushed}")

    # 3. Timeout flush test (150ms pause, length >= 8)
    print("\n--- Test 3: Timeout Flush (150ms pause, length >= 8) ---")
    phrases = chunker.feed("Yes indeed")
    print(f"Fed 'Yes indeed', flushed: {phrases}")
    print("Pausing for 200ms...")
    await asyncio.sleep(0.2)
    phrases = chunker.feed(" ") # Trigger check
    print(f"After pause and feeding space, flushed: {phrases}")
    flushed = chunker.flush()
    if flushed:
        print(f"Flushed at end: {flushed}")

if __name__ == "__main__":
    asyncio.run(run_test())
