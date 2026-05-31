import os
import sys
import logging
from typing import Optional

# Setup standard logging
logger = logging.getLogger("telephony.agent_worker")

def check_worker_dependencies() -> tuple[bool, Optional[str]]:
    """Check if all required LiveKit agent framework packages are installed."""
    try:
        import livekit
        import livekit.agents
        import livekit.api
        from livekit.agents import WorkerOptions, cli
        
        # Audio / Plugin dependencies
        import livekit.plugins.openai
        import livekit.plugins.silero
        return True, None
    except ImportError as e:
        return False, str(e)


def run_worker():
    """Start the LiveKit agent worker."""
    ok, err = check_worker_dependencies()
    if not ok:
        logger.error("Failed to start LiveKit Agent Worker. Missing dependencies: %s", err)
        print(f"\nCRITICAL: Missing dependencies for LiveKit Agent Worker:\n{err}", file=sys.stderr)
        print("\nPlease run:\n  pip install -r requirements.txt\n", file=sys.stderr)
        sys.exit(1)

    # Validate required environment variables
    required_keys = ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"]
    missing = [k for k in required_keys if not os.environ.get(k)]
    if missing:
        logger.error("Missing required environment variables for LiveKit connection: %s", missing)
        print(f"\nCRITICAL: Missing environment variables:\n{', '.join(missing)}\n", file=sys.stderr)
        sys.exit(1)

    # Import main agent entrypoint and prewarm from main.py
    try:
        # Append parent directory to sys.path to ensure absolute import works
        sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from main import entrypoint, prewarm
        from livekit.agents import WorkerOptions, cli
    except ImportError as e:
        logger.error("Failed to import agent entrypoints from main.py: %s", e)
        print(f"\nCRITICAL: Could not load 'main.py': {e}\n", file=sys.stderr)
        sys.exit(1)

    # Run LiveKit worker CLI
    logger.info("Starting LiveKit Agent Worker...")
    
    opts = WorkerOptions(
        entrypoint_fnc=entrypoint,
        prewarm_fnc=prewarm,
    )
    
    if len(sys.argv) == 1:
        # Default to dev mode if run directly without args
        sys.argv.append("dev")
        
    cli.run_app(opts)

if __name__ == "__main__":
    run_worker()
