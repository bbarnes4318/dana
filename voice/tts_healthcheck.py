"""TTS Healthcheck for Dana voice platform.

Verifies that the Kokoro ONNX model and voice files exist, and performs a
synthesis smoke test to ensure the audio output is valid and non-silent.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from typing import Optional
import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HealthcheckResult:
    """The result of the TTS healthcheck."""

    is_healthy: bool
    error_message: Optional[str] = None


async def run_tts_healthcheck(tts_instance) -> HealthcheckResult:
    """Runs a healthcheck on the given local TTS instance.

    Verifies model file presence, voice file presence, and performs a test
    synthesis to check for non-silent audio.
    """
    model_path = os.environ.get("KOKORO_MODEL_PATH", "/root/.cache/kokoro/kokoro-v1.0.onnx")
    voices_path = os.environ.get("KOKORO_VOICES_PATH", "/root/.cache/kokoro/voices-v1.0.bin")

    from config.runtime_env import is_production

    if is_production():
        # In production, model files must exist on disk. No lazy download bypass.
        if not os.path.exists(model_path) and not os.path.exists("models/kokoro-v1.0.onnx"):
            return HealthcheckResult(
                is_healthy=False,
                error_message=f"Kokoro model file not found at path: {model_path}",
            )
        if not os.path.exists(voices_path) and not os.path.exists("models/voices-v1.0.bin"):
            return HealthcheckResult(
                is_healthy=False,
                error_message=f"Kokoro voices file not found at path: {voices_path}",
            )
    else:
        # Local fallback logic mirrors LocallyHostedKokoro.initialize
        if not os.path.exists(model_path):
            if os.path.exists("models/kokoro-v1.0.onnx"):
                model_path = "models/kokoro-v1.0.onnx"
                voices_path = "models/voices-v1.0.bin"
            else:
                model_path = tts_instance.config.model_name
                voices_path = "voices.bin"

        # 1. Verify model path exists
        if not os.path.exists(model_path) and not model_path == "kokoro-v1.0":
            return HealthcheckResult(
                is_healthy=False,
                error_message=f"Kokoro model file not found at path: {model_path}",
            )

        # 2. Verify voices path exists
        if not os.path.exists(voices_path) and not voices_path == "voices.bin":
            return HealthcheckResult(
                is_healthy=False,
                error_message=f"Kokoro voices file not found at path: {voices_path}",
            )

    # 3. Perform test synthesis
    try:
        # We synthesize a short phrase "healthcheck"
        audio = await tts_instance._synthesize_audio("healthcheck")
    except Exception as e:
        return HealthcheckResult(
            is_healthy=False,
            error_message=f"Kokoro synthesis failed with exception: {e}",
        )

    # 4. Verify output has non-zero audio
    if audio is None or len(audio) == 0:
        return HealthcheckResult(
            is_healthy=False,
            error_message="Kokoro synthesized audio output is empty (0 samples).",
        )

    # 5. Verify it's not all-silence
    # We check if standard deviation is zero, or max amplitude is exactly zero, or all samples are zero.
    if np.all(audio == 0.0) or np.max(np.abs(audio)) == 0.0 or np.std(audio) == 0.0:
        return HealthcheckResult(
            is_healthy=False,
            error_message="Kokoro synthesized audio output is completely silent (all zeros).",
        )

    logger.info("TTS healthcheck passed successfully (synthesized non-silent test audio).")
    return HealthcheckResult(is_healthy=True)
