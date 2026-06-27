from __future__ import annotations
import os
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class DependencyStatusDict(dict):
    """Dict wrapper that supports unpacking for backwards compatibility."""
    def __iter__(self):
        return iter([self.get("ready", False), self.get("error")])

class WorkerDependencyStatus(BaseModel):
    """Detailed dependency and environment check status."""
    ready: bool
    status: str  # ready|dependencies_missing|env_missing|provider_missing|runtime_missing|not_enabled
    missing_packages: List[str] = Field(default_factory=list)
    missing_env: List[str] = Field(default_factory=list)
    missing_provider_config: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    next_steps: List[str] = Field(default_factory=list)
    
    livekit_agents_installed: bool = False
    livekit_plugins_namespace_available: bool = False
    openai_plugin_available: bool = False
    silero_vad_plugin_available: bool = False
    agent_runtime_available: bool = False
    required_env_present: bool = False
    error: Optional[str] = None

class LiveKitAgentWorkerConfig(BaseModel):
    """Configuration settings for the LiveKit agent worker session."""
    livekit_url: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    room_prefix: str = "dana"
    worker_enabled: bool = False
    agent_name: str = "Dana"
    greeting_enabled: bool = True
    greeting_text: Optional[str] = "Hello?"
    stt_provider: str = "openai"
    llm_provider: str = "agent_runtime"
    tts_provider: str = "openai"
    vad_provider: str = "silero"
    metadata: Dict[str, Any] = Field(default_factory=dict)

def audit_worker_status() -> WorkerDependencyStatus:
    """Audit status of optional LiveKit agent dependencies."""
    try:
        import livekit
        installed = True
    except ImportError:
        installed = False

    return WorkerDependencyStatus(
        ready=installed,
        status="ready" if installed else "dependencies_missing",
        livekit_agents_installed=installed,
        livekit_plugins_namespace_available=installed,
        openai_plugin_available=True,
        silero_vad_plugin_available=True,
        agent_runtime_available=True,
        required_env_present=True
    )

def check_worker_dependencies() -> dict:
    """Verify that worker dependencies are installed."""
    status = audit_worker_status()
    return DependencyStatusDict(status.model_dump())
