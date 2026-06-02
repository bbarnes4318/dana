import pytest
import asyncio
from unittest.mock import MagicMock

# Detect if the LiveKit Agents SDK is mocked in the test environment
from livekit.agents import vad
is_mocked = isinstance(vad, MagicMock) or type(vad).__name__ == "MagicMock" or not hasattr(vad, "VAD")

if is_mocked:
    pytest.skip("Skipping custom VAD tests because LiveKit Agents VAD is mocked in this environment", allow_module_level=True)

import numpy as np
from livekit import rtc
from speech.custom_vad import ElderlySileroVAD, ElderlySileroVADStream

@pytest.mark.asyncio
async def test_elderly_vad_initialization():
    # Load ElderlySileroVAD
    v = ElderlySileroVAD.load(
        activation_threshold=0.4,
        min_silence_duration=0.3,
        prefix_padding_duration=0.1
    )
    assert v.model == "silero"
    assert v.provider == "ONNX"
    assert v._opts.activation_threshold == 0.4
    assert v._opts.min_silence_duration == 0.3
    assert v._opts.prefix_padding_duration == 0.1


@pytest.mark.asyncio
async def test_elderly_vad_stream_chunking():
    # Load VAD
    v = ElderlySileroVAD.load()
    stream = v.stream()
    
    # Push a 10ms frame of silence (320 bytes at 16kHz mono 16-bit PCM = 160 samples)
    frame_10ms = rtc.AudioFrame(
        data=bytes(320),
        sample_rate=16000,
        num_channels=1,
        samples_per_channel=160
    )
    
    # Push two 10ms frames (total 640 bytes < 960 bytes)
    stream.push_frame(frame_10ms)
    stream.push_frame(frame_10ms)
    await asyncio.sleep(0.01)
    
    # Verification: should still be in the raw input buffer, not chunked into input_buffer yet
    assert stream._raw_input_len == 640
    assert stream._input_len == 0
    
    # Push a third 10ms frame (total 960 bytes = 30ms chunk)
    stream.push_frame(frame_10ms)
    await asyncio.sleep(0.05)
    
    # Verification: chunking loop consumed 960 bytes from raw_input_buffer
    # self._input_len is now 480 (one 30ms chunk of 480 samples)
    assert stream._raw_input_len == 0
    assert stream._input_len == 480
    
    # Push three more 10ms frames (total 6 frames = 60ms = two 30ms chunks)
    stream.push_frame(frame_10ms)
    stream.push_frame(frame_10ms)
    stream.push_frame(frame_10ms)
    await asyncio.sleep(0.05)
    
    # 2 chunks of 480 samples = 960 samples total in input buffer.
    # Inference runs on 512 samples, shifting and leaving 960 - 512 = 448 samples.
    assert stream._input_len == 448
    
    # Cleanup
    await stream.aclose()


@pytest.mark.asyncio
async def test_elderly_vad_update_options():
    v = ElderlySileroVAD.load()
    stream = v.stream()
    
    # Update options dynamically
    stream.update_options(
        activation_threshold=0.45,
        min_silence_duration=0.35,
        max_buffered_speech=30.0
    )
    
    assert stream._opts.activation_threshold == 0.45
    assert stream._opts.min_silence_duration == 0.35
    assert stream._opts.max_buffered_speech == 30.0
    
    # Check that speech buffer is resized
    expected_size = int(30.0 * 16000) + int(stream._opts.prefix_padding_duration * 16000)
    assert len(stream._speech_buffer) == expected_size
    
    await stream.aclose()
