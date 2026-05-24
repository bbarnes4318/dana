"""
Wrapper configuration module.
Imports and exports TelephonyConfig, env_str, env_bool, and required from telnyx_config.py
to maintain import interface compatibility.
"""

from telephony.telnyx_config import (
    TelephonyConfig,
    env_str,
    env_bool,
    required
)

__all__ = [
    "TelephonyConfig",
    "env_str",
    "env_bool",
    "required"
]
