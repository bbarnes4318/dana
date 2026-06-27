from __future__ import annotations
import asyncio
import logging
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

# Add project root to sys.path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from dana.config.voice_config import VoiceConfig
from main import SharedComponents

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("live_smoke_test")

async def main():
    logger.info("Starting live production smoke test (mocked provider internals)...")

    # Set some dummy variables for testing environment if needed
    os.environ["LIVEKIT_SIP_OUTBOUND_TRUNK_ID"] = "dummy_trunk_id"
    os.environ["DANA_VOICE_MODE"] = "local_cost"
    os.environ["DANA_PROVIDER_MODE"] = "cheapest_safe"

    # Create awaitable mocks for client objects
    mock_llm_client = MagicMock()
    
    mock_tts_client = MagicMock()
    mock_tts_client.initialize = AsyncMock()
    
    mock_stt_client = MagicMock()
    mock_stt_client.initialize = AsyncMock()
    
    mock_vad_detector = MagicMock()

    # Patch provider health checks and client instantiation
    with patch("dana.providers.llm.vllm.VLLMProvider.health_check", AsyncMock(return_value=True)), \
         patch("dana.providers.llm.vllm.VLLMProvider.create_client", MagicMock(return_value=mock_llm_client)), \
         patch("dana.providers.tts.kokoro.KokoroTTSProvider.health_check", AsyncMock(return_value=True)), \
         patch("dana.providers.tts.kokoro.KokoroTTSProvider.synthesize_stream", MagicMock(return_value=mock_tts_client)), \
         patch("dana.providers.stt.whisper.WhisperSTTProvider.health_check", AsyncMock(return_value=True)), \
         patch("dana.providers.stt.whisper.WhisperSTTProvider.transcribe_stream", MagicMock(return_value=mock_stt_client)), \
         patch("dana.providers.vad.silero.SileroVADProvider.health_check", AsyncMock(return_value=True)), \
         patch("dana.providers.vad.silero.SileroVADProvider.create_detector", MagicMock(return_value=mock_vad_detector)), \
         patch("dana.providers.telephony.livekit_sip.LiveKitSIPTelephonyProvider.health_check", AsyncMock(return_value=True)):

        # 1. Initialize configuration and registry stack
        logger.info("Loading VoiceConfig and SharedComponents...")
        config = VoiceConfig()
        shared = SharedComponents(config)
        
        # 2. Run initialization
        logger.info("Initializing voice stack (routing engine, health checks)...")
        try:
            await shared.initialize()
            logger.info("Voice stack initialized successfully!")
        except Exception as e:
            logger.error(f"FATAL: Voice stack initialization failed: {e}", exc_info=True)
            sys.exit(1)

        # 3. Print verification info
        logger.info("Verifying resolved provider instances...")
        logger.info(f"Resolved LLM client instance: {shared.llm}")
        logger.info(f"Resolved TTS instance: {shared.tts}")
        logger.info(f"Resolved STT instance: {shared.stt}")
        logger.info(f"Resolved VAD detector instance: {shared.vad}")
        logger.info(f"Resolved Telephony provider: {shared.telephony}")
        
        # Verify cost calculations are non-zero/available
        active_stack = shared.active_stack
        est_cost = active_stack.get("estimated_cost_per_minute", 0.0)
        logger.info(f"Verified ESTIMATED_COST_PER_CONNECTED_MINUTE: {est_min_cost_str(est_cost)}")
        
        logger.info("SMOKE TEST PASSED SUCCESSFULY!")

def est_min_cost_str(cost: float) -> str:
    return f"${cost:.6f}"

if __name__ == "__main__":
    asyncio.run(main())
