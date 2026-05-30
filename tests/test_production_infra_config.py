"""Verify production infrastructure configurations and requirements."""

from __future__ import annotations

import os
from pathlib import Path
import yaml
import pytest


def test_docker_compose_production_services():
    """Verify docker-compose.yaml contains all required production services and settings."""
    compose_path = Path("docker-compose.yaml")
    assert compose_path.exists(), "docker-compose.yaml does not exist"

    with open(compose_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    services = config.get("services", {})
    
    # 1. Check services exist
    assert "postgres" in services, "postgres service missing from docker-compose.yaml"
    assert "pgbouncer" in services, "pgbouncer service missing from docker-compose.yaml"
    assert "redis" in services, "redis service missing from docker-compose.yaml"
    assert "voice-agent" in services, "voice-agent service missing from docker-compose.yaml"
    assert "vllm-server" in services, "vllm-server service missing from docker-compose.yaml"

    # 2. Check voice-agent environment variables
    agent = services["voice-agent"]
    env_list = agent.get("environment", [])
    
    # Convert list of 'KEY=VAL' or dict to dict
    env = {}
    if isinstance(env_list, list):
        for item in env_list:
            if "=" in item:
                k, v = item.split("=", 1)
                env[k.strip()] = v.strip()
    elif isinstance(env_list, dict):
        env = env_list

    assert "DATABASE_URL" in env, "DATABASE_URL missing from voice-agent environment in docker-compose"
    assert "pgbouncer:6432" in env["DATABASE_URL"], "voice-agent DATABASE_URL must point to pgbouncer"
    
    assert "DATABASE_ADMIN_URL" in env, "DATABASE_ADMIN_URL missing from voice-agent environment in docker-compose"
    assert "postgres:5432" in env["DATABASE_ADMIN_URL"], "voice-agent DATABASE_ADMIN_URL must point to postgres"

    # 3. Check volumes
    volumes = config.get("volumes", {})
    assert "pg-data" in volumes, "pg-data volume missing from docker-compose.yaml"
    assert "redis-data" in volumes, "redis-data volume missing from docker-compose.yaml"


def test_no_cloud_provider_specific_dependencies():
    """Verify that the codebase does not mandate AWS, Google Cloud, or Azure.

    Dedicated servers, private networking, and NVMe local storage should be the targets.
    """
    env_paths = [Path(".env.example"), Path(".env.production.example")]
    
    for p in env_paths:
        if not p.exists():
            continue
        with open(p, "r", encoding="utf-8") as f:
            content = f.read()
            # Ensure S3 endpoint config exists which allows generic endpoints (like MinIO, R2, B2)
            assert "BACKUP_S3_ENDPOINT" in content, f"S3 endpoint config missing from {p}"
            # Ensure AWS is not hardcoded/required
            assert "AWS_ROLE" not in content
            assert "GOOGLE_APPLICATION_CREDENTIALS" not in content
            assert "AZURE_STORAGE_CONNECTION_STRING" not in content
