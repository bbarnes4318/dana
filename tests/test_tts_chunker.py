import asyncio
import time
import pytest
from unittest.mock import MagicMock
import numpy as np

from tts_service import FastPhraseChunker, normalize_text, LocallyHostedKokoro, LocalTTSStream

def test_punctuation_flush():
    chunker = FastPhraseChunker()
    
    p1 = chunker.feed("Hello")
    assert len(p1) == 0
    
    p2 = chunker.feed(" world")
    assert len(p2) == 0
    
    p3 = chunker.feed("!")
    assert p3 == ["Hello world!"]
    
    p4 = chunker.feed(" How")
    assert len(p4) == 0
    
    p5 = chunker.feed(" are you?")
    assert p5 == ["How are you?"]


def test_word_boundary_flush():
    chunker = FastPhraseChunker()
    
    # Feed buffer that is long and ends in a space (ends with space at index 33 >= 18)
    p = chunker.feed("This is a very long sentence that ")
    assert p == ["This is a very long sentence that"]
    assert chunker.buffer == ""


@pytest.mark.asyncio
async def test_timeout_flush():
    chunker = FastPhraseChunker()
    
    p1 = chunker.feed("Yes indeed")
    assert len(p1) == 0
    
    # Wait > 150ms to trigger timeout
    await asyncio.sleep(0.2)
    
    p2 = chunker.feed(" ")
    assert p2 == ["Yes indeed"]


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
