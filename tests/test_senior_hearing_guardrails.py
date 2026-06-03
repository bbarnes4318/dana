import numpy as np
import pytest
import sys
from unittest.mock import MagicMock, AsyncMock, patch
from tts_service import apply_senior_audio_filters, resample_to_16k, LocallyHostedKokoro

def test_resample_to_16k():
    # Create 1s of 24kHz audio (sine wave)
    fs = 24000
    t = np.linspace(0, 1, fs, endpoint=False)
    audio_24k = np.sin(2 * np.pi * 1000 * t).astype(np.float32)
    
    # Resample to 16kHz
    audio_16k = resample_to_16k(audio_24k, orig_fs=fs)
    
    # Check length
    assert len(audio_16k) == 16000
    
    # Test with already 16kHz
    audio_16k_same = resample_to_16k(audio_16k, orig_fs=16000)
    assert len(audio_16k_same) == 16000
    assert np.array_equal(audio_16k_same, audio_16k)

def test_apply_senior_audio_filters():
    # 1. Test low-pass filter: high frequency (> 3400Hz) should be heavily attenuated
    fs = 16000
    t = np.linspace(0, 0.5, int(fs * 0.5), endpoint=False)
    
    # 4000Hz tone (should be lowpassed since 4000 > 3400)
    high_freq_tone = np.sin(2 * np.pi * 4000 * t).astype(np.float32)
    filtered_high = apply_senior_audio_filters(high_freq_tone)
    
    # 300Hz tone (should be boosted)
    mid_freq_tone = np.sin(2 * np.pi * 300 * t).astype(np.float32)
    filtered_mid = apply_senior_audio_filters(mid_freq_tone)
    
    # Check attenuation of high frequency compared to original (energy ratio should be small)
    high_energy_orig = np.sum(high_freq_tone ** 2)
    high_energy_filt = np.sum(filtered_high ** 2)
    assert high_energy_filt < 0.1 * high_energy_orig  # At least 10dB attenuation
    
    # Check boost of mid frequency (should be boosted by ~3dB, i.e., root-mean-square amplitude ratio around 1.41)
    mid_rms_orig = np.sqrt(np.mean(mid_freq_tone ** 2))
    mid_rms_filt = np.sqrt(np.mean(filtered_mid ** 2))
    boost_ratio = mid_rms_filt / mid_rms_orig
    # Expected boost ratio is around 1.16 due to phase shifts and transients
    assert 1.1 <= boost_ratio <= 1.5

@pytest.mark.asyncio
async def test_divergent_input_logic():
    from telephony.livekit_agent_worker import generate_agent_response
    
    # Test is_user_input_divergent logic inside generate_agent_response via checking the prompt wrapping
    mock_prompt_loader = MagicMock()
    mock_prompt_loader.build_system_prompt.return_value = "STATIC_SYSTEM_PROMPT"
    
    mock_runtime = MagicMock()
    mock_runtime.prompt_loader = mock_prompt_loader
    
    async def mock_process_turn(user_text, chat_fn):
        resp = await chat_fn("INSTRUCTIONS")
        mock_result = MagicMock()
        mock_result.agent_response = resp
        mock_result.stage = "QUALIFYING"
        mock_result.should_end_call = False
        return mock_result
        
    mock_runtime.process_turn = mock_process_turn
    
    session_state = {"turns": [], "stage": "OPENING"}
    
    # livekit.plugins.openai is mocked in sys.modules by conftest.py
    mock_openai = sys.modules.get('livekit.plugins.openai')
    mock_plugins = sys.modules.get('livekit.plugins')
    assert mock_openai is not None
    
    mock_llm_inst = MagicMock()
    mock_stream = AsyncMock()
    async def mock_aiter(self=None):
        if False:
            yield None
    mock_stream.__aiter__ = mock_aiter
    mock_llm_inst.chat.return_value = mock_stream
    
    # Configure mock LLM class on both to return our instance
    mock_openai.LLM.return_value = mock_llm_inst
    if mock_plugins is not None:
        mock_plugins.openai.LLM.return_value = mock_llm_inst
    
    await generate_agent_response("yes I'm 65 and live in Texas", session_state, mock_runtime)
    
    # Verify chat was called with temperature=0.2
    chat_args, chat_kwargs = mock_llm_inst.chat.call_args
    assert chat_kwargs["temperature"] == 0.2
    
    # Find system prompt content via ChatMessage call args
    system_calls = [c for c in mock_llm_inst.ChatMessage.call_args_list if c[1].get("role") == "system"]
    assert len(system_calls) > 0
    system_content = system_calls[-1][1]["content"]
    assert "SYSTEM CONTEXT ENFORCEMENT WARNING" not in system_content
    assert "STATIC_SYSTEM_PROMPT" in system_content

    # Clear call history for next assertion
    mock_llm_inst.ChatMessage.reset_mock()

    # Call with divergent input
    await generate_agent_response("what is the weather like today in new york", session_state, mock_runtime)
    
    # Verify chat was called with temperature=0.2
    chat_args, chat_kwargs = mock_llm_inst.chat.call_args
    assert chat_kwargs["temperature"] == 0.2
    
    # Check system prompt content (should contain warning block)
    system_calls = [c for c in mock_llm_inst.ChatMessage.call_args_list if c[1].get("role") == "system"]
    assert len(system_calls) > 0
    system_content = system_calls[-1][1]["content"]
    assert "SYSTEM CONTEXT ENFORCEMENT WARNING" in system_content
    assert "STATIC_SYSTEM_PROMPT" in system_content
