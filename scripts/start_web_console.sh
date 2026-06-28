#!/bin/bash
pkill -f run_training_web_console || true
cd /workspace/dana
rm -f /var/log/training_web_console.log
nohup env PYTHONPATH=/workspace/dana python3 /workspace/dana/scripts/run_training_web_console.py --host 0.0.0.0 --port 8787 --allow-remote > /var/log/training_web_console.log 2>&1 &
