#!/usr/bin/env python3
"""
CLI entry point to launch the Dana LiveKit agent worker.
"""

import os
import sys

# Ensure parent directory is in sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telephony.livekit_agent_worker import run_worker

if __name__ == "__main__":
    run_worker()
