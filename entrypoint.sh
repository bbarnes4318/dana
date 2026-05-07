#!/bin/bash
# Sovereign Voice Stack - Agent Entrypoint
# Ensures all models are ready before starting the agent

set -e

echo "============================================"
echo "Sovereign Voice Stack - Starting Agent"
echo "============================================"

# Verify CUDA is available
echo "[1/4] Checking CUDA availability..."
python -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}'); print(f'GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"N/A\"}')"

# Verify Whisper model is loaded
echo "[2/4] Verifying Whisper STT model..."
python -c "
from faster_whisper import WhisperModel
import os
print('Loading Whisper large-v3-turbo with float16...')
model = WhisperModel('large-v3-turbo', device='cuda', compute_type='float16')
print('✓ Whisper STT ready')
"

# Verify Kokoro TTS is loaded
echo "[3/4] Verifying Kokoro TTS model..."
python -c "
from kokoro_onnx import Kokoro
print('Loading Kokoro ONNX...')
kokoro = Kokoro('kokoro-v1.0')
print('✓ Kokoro TTS ready')
"

# Verify Silero VAD is loaded
echo "[4/4] Verifying Silero VAD model..."
python -c "
import torch
print('Loading Silero VAD v5...')
model, utils = torch.hub.load(repo_or_dir='snakers4/silero-vad', model='silero_vad', force_reload=False)
print('✓ Silero VAD ready')
"

echo "============================================"
echo "All models verified. Starting Voice Agent..."
echo "============================================"

# Wait for vLLM to be ready
echo "Waiting for vLLM server..."
until curl -s -f http://${VLLM_HOST:-vllm-server}:8000/health > /dev/null 2>&1; do
    echo "  vLLM not ready yet, retrying in 5s..."
    sleep 5
done
echo "✓ vLLM server is ready"

echo ""
echo "Starting LiveKit Agent..."
exec "$@"
