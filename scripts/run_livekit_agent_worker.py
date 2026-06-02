#!/usr/bin/env python3
"""
CLI entry point to launch the Dana LiveKit agent worker or run readiness checks.
"""

import os
import sys
import json
import argparse
import asyncio

# Ensure parent directory is in sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.env_loader import load_environment
load_environment()

from telephony.livekit_agent_worker import audit_worker_status, start_worker, LiveKitAgentWorkerConfig

def main():
    parser = argparse.ArgumentParser(description="Run LiveKit Agent Worker or verify dependencies.")
    parser.add_argument("--check-only", action="store_true", help="Print dependency JSON and exit without starting worker")
    parser.add_argument("--room-prefix", default="dana", help="Job room prefix to join")
    parser.add_argument("--agent-name", default="Dana", help="Agent speaker name")
    parser.add_argument("--greeting-text", help="Override approved greeting opening sentence")
    parser.add_argument("--debug", action="store_true", help="Enable verbose debug logging")

    args = parser.parse_args()

    if args.debug:
        import logging
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)
    
    # 1. Audit status
    status = audit_worker_status()
    
    # 2. Handle check-only CLI mode
    if args.check_only:
        # Print clean JSON to stdout
        print(json.dumps(status.model_dump(), indent=2))
        sys.exit(0 if status.ready else 1)
        
    # 3. Running standard worker mode
    if not status.ready:
        print(f"CRITICAL: Worker check failed: {status.status}", file=sys.stderr)
        print(f"Errors: {status.error}", file=sys.stderr)
        print("Missing packages:", status.missing_packages, file=sys.stderr)
        print("Missing environment:", status.missing_env, file=sys.stderr)
        print("Next steps:", status.next_steps, file=sys.stderr)
        # Output structured error status to stdout as fallback
        print(json.dumps(status.model_dump(), indent=2))
        sys.exit(1)

    print(f"Starting LiveKit Agent Worker (prefix={args.room_prefix}, agent={args.agent_name})...", file=sys.stderr)
    
    from config.runtime_env import get_runtime_env
    env = get_runtime_env()
    
    # Build config dynamically from parameters and environment
    greeting = args.greeting_text or os.environ.get("DANA_OPENING_LINE") or "Hello?"
    llm_p = env["llm_routing_mode"]
    if llm_p == "local":
        llm_p = "agent_runtime"
    config = LiveKitAgentWorkerConfig(
        livekit_url=env["livekit_url"],
        api_key=env["livekit_api_key"],
        api_secret=env["livekit_api_secret"],
        room_prefix=args.room_prefix,
        worker_enabled=True,
        agent_name=args.agent_name,
        greeting_text=greeting,
        stt_provider=env["stt_routing_mode"],
        llm_provider=llm_p,
        tts_provider=env["tts_routing_mode"],
        vad_provider="silero"
    )

    try:
        start_worker(config)
    except KeyboardInterrupt:
        print("Worker stopped by operator.", file=sys.stderr)
        sys.exit(0)
    except Exception as e:
        print(f"CRITICAL Error starting worker: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
