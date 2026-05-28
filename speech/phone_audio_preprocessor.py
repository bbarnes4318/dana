"""Phone Audio Preprocessor.

Processes raw PCM audio frames before STT. Supports mono conversion, resampling,
DC offset removal, configurable light noise gating, optional PSTN band-pass filter,
clipping prevention, and rolling-window line quality estimation.
"""

from __future__ import annotations

import logging
from typing import Optional, Dict, List
import numpy as np
import scipy.signal
from livekit import rtc

from speech.context_registry import get_current_call_id, update_line_quality

logger = logging.getLogger(__name__)


class PhoneAudioPreprocessor:
    """Audio preprocessor for phone-call speech enhancement and line quality auditing."""

    def __init__(
        self,
        enable_mono_conversion: bool = True,
        enable_resampling: bool = True,
        enable_dc_removal: bool = True,
        enable_noise_gate: bool = False,
        noise_gate_threshold: float = 0.002,  # Gentle threshold for soft speakers
        noise_gate_attenuation: float = 0.5,  # Only attenuate by 50% to preserve quiet speech
        enable_pstn_bandpass: bool = False,
        enable_clipping_prevention: bool = True,
        enable_normalization: bool = False,
        rolling_window_frames: int = 50,  # ~1 second of audio at 20ms frames
    ) -> None:
        self.enable_mono_conversion = enable_mono_conversion
        self.enable_resampling = enable_resampling
        self.enable_dc_removal = enable_dc_removal
        self.enable_noise_gate = enable_noise_gate
        self.noise_gate_threshold = noise_gate_threshold
        self.noise_gate_attenuation = noise_gate_attenuation
        self.enable_pstn_bandpass = enable_pstn_bandpass
        self.enable_clipping_prevention = enable_clipping_prevention
        self.enable_normalization = enable_normalization
        self.rolling_window_frames = rolling_window_frames

        # Store rolling history per call_id: {call_id: [stats_dicts]}
        self._history: Dict[str, List[Dict[str, float]]] = {}

    def _butter_bandpass(self, lowcut: float, highcut: float, fs: float, order: int = 4):
        nyq = 0.5 * fs
        low = lowcut / nyq
        high = highcut / nyq
        b, a = scipy.signal.butter(order, [low, high], btype="band")
        return b, a

    def _apply_pstn_bandpass(self, audio: np.ndarray, fs: float) -> np.ndarray:
        if fs <= 6800:
            # Cannot apply 300-3400Hz filter safely if Nyquist frequency is too close or lower
            return audio
        try:
            b, a = self._butter_bandpass(300.0, 3400.0, fs, order=4)
            return scipy.signal.lfilter(b, a, audio)
        except Exception as e:
            logger.error(f"Failed to apply PSTN bandpass filter: {e}")
            return audio

    def _update_line_quality_estimate(self, call_id: str, audio: np.ndarray) -> None:
        """Update line quality score based on a rolling window of audio frames."""
        if not audio.size:
            return

        # 1. Clipping ratio: fraction of samples clipping at > 0.95 amplitude
        clip_count = np.sum(np.abs(audio) >= 0.95)
        clipping_ratio = float(clip_count / audio.size)

        # 2. Frame RMS (energy)
        rms = float(np.sqrt(np.mean(audio ** 2)))

        # Update call rolling history
        if call_id not in self._history:
            self._history[call_id] = []
        
        history = self._history[call_id]
        history.append({
            "clipping_ratio": clipping_ratio,
            "rms": rms,
        })

        if len(history) > self.rolling_window_frames:
            history.pop(0)

        # 3. Calculate metrics over the window
        mean_clipping = np.mean([f["clipping_ratio"] for f in history])
        
        # Estimate noise floor as the minimum RMS seen in the window (assumed silence parts)
        noise_floor = np.min([f["rms"] for f in history])

        # Sustained evidence calculation:
        # High clipping and high background noise degrade quality
        quality_score = 1.0 - (mean_clipping * 4.0) - (noise_floor * 6.0)
        quality_score = max(0.0, min(1.0, quality_score))

        update_line_quality(call_id, quality_score)

    def preprocess_numpy(self, audio: np.ndarray, sample_rate: int, call_id: Optional[str] = None, num_channels: int = 1) -> np.ndarray:
        """Process a float32 numpy audio array."""
        if not audio.size:
            return audio

        # Clone to avoid mutating the input
        audio = audio.copy()

        # 1. Mono conversion
        if self.enable_mono_conversion and num_channels > 1:
            audio = audio.reshape(-1, num_channels).mean(axis=1)
            num_channels = 1

        # 2. DC offset removal
        if self.enable_dc_removal:
            audio = audio - np.mean(audio)

        # 3. Resampling using resample_poly (polyphase resampling)
        target_rate = 16000
        if self.enable_resampling and sample_rate != target_rate:
            try:
                # Use scipy's resample_poly for high quality down/up-sampling
                gcd = np.gcd(sample_rate, target_rate)
                up = target_rate // gcd
                down = sample_rate // gcd
                audio = scipy.signal.resample_poly(audio, up, down)
                sample_rate = target_rate
            except Exception as e:
                logger.error(f"Failed polyphase resampling: {e}. Falling back to linear interpolation.")
                duration = len(audio) / sample_rate
                new_len = int(duration * target_rate)
                indices = np.linspace(0, len(audio) - 1, new_len)
                audio = np.interp(indices, np.arange(len(audio)), audio)
                sample_rate = target_rate

        # 4. PSTN Band-pass (300Hz to 3400Hz)
        if self.enable_pstn_bandpass:
            audio = self._apply_pstn_bandpass(audio, float(sample_rate))

        # 5. Update Line Quality Estimation (if call_id is registered/provided)
        active_call_id = call_id or get_current_call_id()
        if active_call_id:
            self._update_line_quality_estimate(active_call_id, audio)

        # 6. Gentle noise gate: attenuates quiet segments slightly instead of silencing
        if self.enable_noise_gate:
            rms = np.sqrt(np.mean(audio ** 2))
            if rms < self.noise_gate_threshold:
                # Smoothly gate only if the frame is extremely quiet
                # Still preserves quiet speakers by applying a gentle attenuation scaling
                audio = audio * self.noise_gate_attenuation

        # 7. Clipping prevention & Peak Normalization
        if self.enable_normalization:
            peak = np.max(np.abs(audio))
            if peak > 0.05:  # Avoid normalising pure silence
                audio = audio / peak * 0.8  # Target peak of 0.8 (-2 dBFS)

        if self.enable_clipping_prevention:
            peak = np.max(np.abs(audio))
            if peak > 0.95:
                # Apply soft limiter above 0.95
                mask = np.abs(audio) > 0.95
                audio[mask] = np.sign(audio[mask]) * (0.95 + 0.05 * np.tanh((np.abs(audio[mask]) - 0.95) / 0.05))
            # Hard clip safety check
            audio = np.clip(audio, -1.0, 1.0)

        return audio

    def preprocess_frame(self, frame: rtc.AudioFrame, call_id: Optional[str] = None) -> rtc.AudioFrame:
        """Process an incoming LiveKit AudioFrame and return a preprocessed AudioFrame."""
        # Convert frame data to numpy float32 [-1.0, 1.0]
        audio_int16 = np.frombuffer(frame.data, dtype=np.int16)
        audio_float32 = audio_int16.astype(np.float32) / 32768.0

        # Run preprocessing on numpy array (handles mono conversion internally)
        processed_audio = self.preprocess_numpy(
            audio_float32,
            frame.sample_rate,
            call_id=call_id,
            num_channels=frame.num_channels
        )

        num_channels = 1 if (self.enable_mono_conversion and frame.num_channels > 1) else frame.num_channels

        # Convert back to int16 bytes
        processed_int16 = (processed_audio * 32767.0).astype(np.int16)
        new_data = processed_int16.tobytes()

        # Build a new AudioFrame
        # Note: If resampling took place, sample rate is now 16000
        new_sample_rate = 16000 if (self.enable_resampling and frame.sample_rate != 16000) else frame.sample_rate
        new_samples_per_channel = len(processed_int16) // num_channels

        return rtc.AudioFrame(
            data=new_data,
            sample_rate=new_sample_rate,
            num_channels=num_channels,
            samples_per_channel=new_samples_per_channel,
        )


    def cleanup_call(self, call_id: str) -> None:
        """Remove rolling history for a closed call."""
        self._history.pop(call_id, None)
