# Sovereign Voice Stack - Voice Agent Dockerfile
# Optimized for ultra-low-latency STT/TTS with CUDA support

FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04 AS base

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3-pip \
    ffmpeg \
    libsndfile1 \
    libportaudio2 \
    git \
    curl \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Set Python 3.11 as default
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1 \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

# Create app directory
WORKDIR /app

# Copy requirements and constraints first for better layer caching
COPY requirements.txt constraints.txt ./

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt -c constraints.txt

# ============================================
# Model Download Stage - Pre-download all models
# ============================================
FROM base AS models

# Create model cache directories
RUN mkdir -p /root/.cache/huggingface \
    && mkdir -p /root/.cache/silero-vad \
    && mkdir -p /app/models

# Pre-download Whisper large-v3-turbo
RUN python -c "from faster_whisper import WhisperModel; \
    print('Downloading Whisper large-v3-turbo...'); \
    model = WhisperModel('large-v3-turbo', device='cpu', compute_type='int8'); \
    print('Whisper model downloaded successfully')"

# Pre-download Silero VAD
RUN python -c "import torch; \
    print('Downloading Silero VAD...'); \
    model, utils = torch.hub.load(repo_or_dir='snakers4/silero-vad', model='silero_vad', force_reload=False, trust_repo=True); \
    print('Silero VAD downloaded successfully')"

# Pre-download Kokoro ONNX model
RUN mkdir -p /root/.cache/kokoro \
    && wget -q -O /root/.cache/kokoro/kokoro-v1.0.onnx https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx \
    && wget -q -O /root/.cache/kokoro/voices-v1.0.bin https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin

# ============================================
# Production Stage
# ============================================
FROM base AS production

# Copy cached models from models stage
COPY --from=models /root/.cache /root/.cache
COPY --from=models /app/models /app/models

# Copy application code
COPY . /app/

# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

# Expose any needed ports (if using HTTP interface)
EXPOSE 8080

# Environment variables for runtime
ENV CUDA_VISIBLE_DEVICES=0
ENV LIVEKIT_URL=""
ENV LIVEKIT_API_KEY=""
ENV LIVEKIT_API_SECRET=""
ENV VLLM_BASE_URL="http://vllm-server:8000/v1"
ENV DANA_RUNTIME_ENV="production"
ENV DANA_ALLOW_MOCK_TTS="false"

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -m ops.healthcheck

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "main.py"]
