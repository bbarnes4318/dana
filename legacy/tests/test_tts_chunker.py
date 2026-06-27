import asyncio
import time
import pytest
from unittest.mock import MagicMock
import numpy as np

from tts_service import FastPhraseChunker, normalize_text, LocallyHostedKokoro, LocalTTSStream

def test_token_phrase_chunking():
    # Test that it chunks when hitting min_tokens (3)
    chunker = FastPhraseChunker(min_tokens=3, max_tokens=5)
    
    # 1 word, not completed (no trailing space)
    p1 = chunker.feed("Hello")
    assert len(p1) == 0
    
    # 2 words completed (with trailing space)
    p2 = chunker.feed(" world ")
    assert len(p2) == 0
    
    # 3 words completed total (Hello, world, this)
    p3 = chunker.feed("this is")
    # "Hello world this" are completed words (followed by a space/word boundaries)
    # completed words: "Hello", "world", "this". The "is" at the end is not followed by space yet.
    assert p3 == ["Hello world this"]
    assert chunker.buffer == "is"
    
    # Push more to trigger next chunk
    p4 = chunker.feed(" a test of the")
    # Buffer was "is a test of the"
    # Words in buffer: ["is", "a", "test", "of", "the"]
    # Completed count: 4 (since "the" is not followed by a space)
    # With min_tokens=3 and max_tokens=5: chunk_len = min(4, 5) = 4
    # Returns ["is a test of"]
    # Buffer becomes "the"
    assert p4 == ["is a test of"]
    assert chunker.buffer == "the"
    
    # Flush remaining
    p5 = chunker.flush()
    assert p5 == ["the"]
    assert chunker.buffer == ""


def test_pronunciation_normalization():
    assert normalize_text("This is an AI model.") == "This is an A I model."
    assert normalize_text("Price is $29.99 today!") == "Price is 29.99 dollars today!"
    assert normalize_text("Interest is 5%") == "Interest is 5 percent"
    assert normalize_text("Normal text.") == "Normal text."
    assert normalize_text("Double **asterisks** here.") == "Double asterisks here."
    assert normalize_text("Multiple   spaces   here.") == "Multiple spaces here."


@pytest.mark.asyncio
async def test_tts_stream_aclose():
    # Mock Kokoro TTS instance so we don't load the real ONNX model
    from livekit.agents import tts
    tts_instance = LocallyHostedKokoro()
    tts_instance._initialized = True
    tts_instance._model = MagicMock()
    
    conn_options = tts.APIConnectOptions(max_retry=1, retry_interval=1.0, timeout=1.0)
    stream = LocalTTSStream(tts=tts_instance, conn_options=conn_options)
    
    # Push text to queue
    stream.push_text("Hello world!")
    await stream.aclose()
    
    # Verify input channel is closed
    assert stream._input_ch.closed
