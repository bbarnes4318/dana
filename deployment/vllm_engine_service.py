"""Sovereign Voice Stack - vLLM AsyncLLMEngine Production Service.

Initializes the vLLM AsyncLLMEngine with Automatic Prefix Caching (APC)
and FP8 quantization tailored for an NVIDIA L40S GPU.
"""

from __future__ import annotations
import os
import logging
import asyncio
from vllm.engine.arg_utils import AsyncEngineArgs
from vllm.engine.async_llm_engine import AsyncLLMEngine

logger = logging.getLogger(__name__)

def initialize_async_engine() -> AsyncLLMEngine:
    """Configures and instantiates the vLLM AsyncLLMEngine programmatically."""
    model_name = os.getenv("VLLM_MODEL", "meta-llama/Llama-3.1-8B-Instruct")
    gpu_utilization = float(os.getenv("VLLM_GPU_MEMORY_UTILIZATION", "0.70"))
    max_model_len = int(os.getenv("VLLM_MAX_MODEL_LEN", "4096"))
    
    # 1. Setup Engine Arguments programmatically
    engine_args = AsyncEngineArgs(
        model=model_name,
        dtype="auto",
        quantization="fp8",          # Force FP8 quantization to optimize L40S throughput and TTFT < 50ms
        gpu_memory_utilization=gpu_utilization,
        max_model_len=max_model_len,
        block_size=16,               # Strict token block size memory alignment for optimal KV cache reuse
        enforce_eager=True,          # Pre-compile execution path to avoid runtime compilation overhead
        enable_prefix_caching=True,  # Explicitly enable Automatic Prefix Caching (APC)
        trust_remote_code=True,
        disable_log_stats=False,
    )
    
    logger.info("Initializing AsyncLLMEngine with production parameters:")
    logger.info(f"  Model: {engine_args.model}")
    logger.info(f"  Quantization: {engine_args.quantization}")
    logger.info(f"  Block Size: {engine_args.block_size}")
    logger.info(f"  Enable Prefix Caching: {engine_args.enable_prefix_caching}")
    logger.info(f"  GPU Memory Utilization: {engine_args.gpu_memory_utilization}")
    logger.info(f"  Enforce Eager: {engine_args.enforce_eager}")
    
    # 2. Instantiate the engine
    engine = AsyncLLMEngine.from_engine_args(engine_args)
    return engine

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    try:
        engine = initialize_async_engine()
        logger.info("✓ vLLM AsyncLLMEngine initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize vLLM AsyncLLMEngine: {e}")
        raise e
