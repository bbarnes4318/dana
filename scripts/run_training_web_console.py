#!/usr/bin/env python
"""CLI launcher script to run the Training Operations Web Console Server."""

from __future__ import annotations

import argparse
import sys
from dotenv import load_dotenv
load_dotenv()

from ops.web_console import TrainingWebConsoleServer, TrainingWebConsoleConfig


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Dana Continuous Training Web Console")
    parser.add_argument("--host", default="127.0.0.1", help="Host address to bind the server to")
    parser.add_argument("--port", type=int, default=8787, help="Port to run the server on")
    parser.add_argument("--allow-remote", action="store_true", default=False, help="Allow remote connections (bind to any interface or non-localhost)")
    parser.add_argument("--static-dir", default="static/training_console", help="Directory serving static files")
    parser.add_argument("--data-dir", default=None, help="JSONL repository data folder override")
    parser.add_argument("--debug", action="store_true", default=False, help="Enable verbose debug mode and local CORS headers")

    args = parser.parse_args()

    config = TrainingWebConsoleConfig(
        host=args.host,
        port=args.port,
        static_dir=args.static_dir,
        data_dir=args.data_dir,
        allow_remote=args.allow_remote,
        debug=args.debug,
    )

    try:
        server = TrainingWebConsoleServer(config)
        
        # Output info to stderr so stdout remains reserved for clean JSON
        sys.stderr.write("--------------------------------------------------\n")
        sys.stderr.write(" Dana Training Operations Web Console Server\n")
        sys.stderr.write("--------------------------------------------------\n")
        sys.stderr.write(f" Host: {server.server_address[0]}\n")
        sys.stderr.write(f" Port: {server.server_address[1]}\n")
        sys.stderr.write(f" Local URL: http://{server.server_address[0]}:{server.server_address[1]}\n")
        sys.stderr.write(" Status: Running (Press Ctrl+C to terminate)\n")
        sys.stderr.write("--------------------------------------------------\n")
        sys.stderr.flush()

        server.serve_forever()
        return 0

    except Exception as e:
        sys.stderr.write(f"CRITICAL: Failed to launch web server: {e}\n")
        sys.stderr.flush()
        return 1


if __name__ == "__main__":
    sys.exit(main())
