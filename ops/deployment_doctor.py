"""Dana Platform local deployment doctor.

Audits OS, drivers, Docker, Docker Compose, directories, and environment variables.
"""

from __future__ import annotations

import os
import sys
import argparse
import json
import shutil
import subprocess
import platform
from pathlib import Path
from typing import Any

from config.env_schema import validate_env, mask_value, DEPRECATED_ALIASES

def run_command_safe(cmd: list[str]) -> tuple[bool, str]:
    """Execute a system command and return (success, stdout/error)."""
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            return True, res.stdout.strip()
        return False, res.stderr.strip()
    except Exception as e:
        return False, str(e)


class DeploymentDoctor:
    def __init__(self, env_file_path: Path):
        self.env_file_path = env_file_path
        self.repo_root = Path(__file__).parent.parent.resolve()
        self.results: list[dict[str, Any]] = []
        self.failures = 0
        self.warnings = 0
        self.env_dict: dict[str, str] = {}

    def add_result(self, name: str, status: str, message: str, details: str = ""):
        """Add check result row."""
        self.results.append({
            "name": name,
            "status": status,
            "message": message,
            "details": details
        })
        if status == "FAIL":
            self.failures += 1
        elif status == "WARN":
            self.warnings += 1

    def run_system_checks(self):
        # 1. OS check
        os_info = f"{platform.system()} {platform.release()} ({platform.machine()})"
        self.add_result("OS Info", "PASS", f"System: {os_info}")

        # Check if we should mock system checks (useful for test environments without Docker)
        mock_sys = os.environ.get("DANA_MOCK_SYSTEM_CHECKS") == "true" or self.env_dict.get("DANA_MOCK_SYSTEM_CHECKS") == "true"

        # 2. nvidia-smi check
        has_nv = bool(shutil.which("nvidia-smi")) or mock_sys
        if has_nv:
            if mock_sys:
                self.add_result("NVIDIA GPUs", "PASS", "Found 1 GPU(s): NVIDIA GeForce RTX 4090 (Mocked)")
            else:
                ok, out = run_command_safe(["nvidia-smi", "-L"])
                if ok:
                    gpus = [line.strip() for line in out.splitlines() if line.strip()]
                    self.add_result("NVIDIA GPUs", "PASS", f"Found {len(gpus)} GPU(s): {', '.join(gpus)}")
                else:
                    self.add_result("NVIDIA GPUs", "WARN", "nvidia-smi present but returned error", out)
        else:
            self.add_result("NVIDIA GPUs", "WARN", "nvidia-smi command not found on PATH. GPU capabilities cannot be audited.")

        # 3. Docker check
        docker_path = shutil.which("docker")
        if mock_sys and not docker_path:
            docker_path = "/usr/bin/docker"
            
        if docker_path:
            if mock_sys:
                self.add_result("Docker Engine", "PASS", "Docker available: Docker version 25.0.3, build 4debf41 (Mocked)")
            else:
                ok, out = run_command_safe(["docker", "--version"])
                if ok:
                    self.add_result("Docker Engine", "PASS", f"Docker available: {out}")
                else:
                    self.add_result("Docker Engine", "WARN", "Docker binary found but failed to run", out)
        else:
            self.add_result("Docker Engine", "FAIL", "Docker not found. Docker is required to deploy Dana.")

        # 4. Docker Compose check
        has_compose = False
        compose_msg = ""
        if mock_sys:
            has_compose = True
            compose_msg = "Docker Compose (CLI plugin) available: Docker Compose version v2.24.5 (Mocked)"
        else:
            # Try 'docker compose' first
            ok, out = run_command_safe(["docker", "compose", "version"])
            if ok:
                has_compose = True
                compose_msg = f"Docker Compose (CLI plugin) available: {out}"
            else:
                # Fall back to 'docker-compose'
                ok_legacy, out_legacy = run_command_safe(["docker-compose", "--version"])
                if ok_legacy:
                    has_compose = True
                    compose_msg = f"docker-compose (standalone) available: {out_legacy}"
        
        if has_compose:
            self.add_result("Docker Compose", "PASS", compose_msg)
        else:
            self.add_result("Docker Compose", "FAIL", "Docker Compose not found. Docker Compose is required to manage Dana containers.")

    def run_repo_checks(self):
        # 5. docker-compose.yaml check
        compose_file = self.repo_root / "docker-compose.yaml"
        if compose_file.exists():
            self.add_result("docker-compose.yaml", "PASS", f"Found: {compose_file}")
        else:
            self.add_result("docker-compose.yaml", "FAIL", f"Missing docker-compose.yaml at repository root: {compose_file}")

        # 6. .env.production.example check
        example_env = self.repo_root / ".env.production.example"
        if example_env.exists():
            self.add_result(".env.production.example", "PASS", f"Found: {example_env}")
        else:
            self.add_result(".env.production.example", "WARN", f"Missing .env.production.example template: {example_env}")

        # 6.5. PgBouncer userlist.txt safety check
        userlist_file = self.repo_root / "infra" / "pgbouncer" / "userlist.txt"
        if userlist_file.exists():
            try:
                has_non_comment = False
                non_comment_lines = []
                with open(userlist_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line_stripped = line.strip()
                        if line_stripped and not line_stripped.startswith("#"):
                            has_non_comment = True
                            non_comment_lines.append(line_stripped)
                
                if has_non_comment:
                    self.add_result(
                        "PgBouncer Plaintext Credentials Check", 
                        "FAIL", 
                        f"infra/pgbouncer/userlist.txt contains unencrypted plaintext credentials: {', '.join(non_comment_lines)}"
                    )
                else:
                    self.add_result(
                        "PgBouncer Plaintext Credentials Check", 
                        "PASS", 
                        "infra/pgbouncer/userlist.txt is safe (no committed plaintext credentials)"
                    )
            except Exception as e:
                self.add_result("PgBouncer Plaintext Credentials Check", "FAIL", f"Failed to check PgBouncer credentials: {e}")
        else:
            self.add_result("PgBouncer Plaintext Credentials Check", "PASS", "PgBouncer userlist.txt not found (safe)")

        # 7. Active .env file check
        if self.env_file_path.exists():
            self.add_result("Environment File", "PASS", f"Using active environment file: {self.env_file_path.name}")
            # Load environment dictionary from the file
            try:
                with open(self.env_file_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" in line:
                            parts = line.split("=", 1)
                            key = parts[0].strip()
                            val = parts[1].strip()
                            # Strip optional quotes
                            if len(val) >= 2 and val[0] in ('"', "'") and val[-1] == val[0]:
                                val = val[1:-1]
                            self.env_dict[key] = val
            except Exception as e:
                self.add_result("Environment File Parsing", "FAIL", f"Failed to parse {self.env_file_path.name}: {e}")
        else:
            self.add_result("Environment File", "FAIL", f"Active environment file not found: {self.env_file_path.resolve()}")

    def run_env_variable_checks(self):
        if not self.env_dict:
            self.add_result("Env Variable Check", "FAIL", "No environment variables loaded because the env file is missing or empty.")
            return
            
        validation = validate_env(self.env_dict)
        
        # Add failures as FAIL checks
        for failure in validation["failures"]:
            self.add_result("Env Variable Check", "FAIL", failure)
            
        # Add warnings as WARN checks
        for warning in validation["warnings"]:
            self.add_result("Env Variable Check", "WARN", warning)

        if validation["passed"] and not validation["failures"]:
            self.add_result("Env Validation Result", "PASS", "All environment variable checks passed successfully.")

    def run_directory_checks(self):
        # Verify write access to required directories
        dirs_to_check = ["data", "data/ops_readiness", "data/live_call_smoke_tests", "prompts"]
        for d in dirs_to_check:
            dir_path = self.repo_root / d
            try:
                os.makedirs(dir_path, exist_ok=True)
                test_file = dir_path / ".doctor_write_test"
                test_file.write_text("write_test")
                test_file.unlink()
                self.add_result("Directory Access", "PASS", f"Directory is writable: {d}")
            except Exception as e:
                self.add_result("Directory Access", "FAIL", f"Directory is NOT writable/creatable: {d} - {e}")

    def audit(self) -> dict[str, Any]:
        """Perform all audits and return rollup metrics and checks list."""
        self.run_repo_checks()
        self.run_system_checks()
        self.run_env_variable_checks()
        self.run_directory_checks()

        # Rollup statuses
        critical_infra_ok = True
        # Critical items are Docker, Compose, compose files, writable directories, env file existence, and basic DB urls
        for check in self.results:
            if check["status"] == "FAIL":
                # Check which ones are critical
                if any(x in check["name"] for x in ["Docker", "docker-compose.yaml", "Environment File", "Directory Access"]):
                    critical_infra_ok = False

        # Calculate final states
        # PRODUCTION_ENV_VALID: all required environment variables are set and validated correctly (failures == 0)
        prod_env_valid = True
        for check in self.results:
            if check["status"] == "FAIL" and "Env Variable" in check["name"]:
                prod_env_valid = False

        # READY_TO_START_SERVICES: docker, compose, compose files, write access, and environment file exist
        # and there are no critical parsing/validation failures on infrastructure env vars
        ready_to_start = critical_infra_ok and self.env_file_path.exists()
        # If there are missing required variables, we cannot start services correctly
        for check in self.results:
            if check["status"] == "FAIL" and "Missing required variable" in check["message"]:
                ready_to_start = False
            if check["status"] == "FAIL" and "placeholder" in check["message"].lower():
                ready_to_start = False

        # READY_FOR_LIVE_CALL_TEST: must be false unless all required checks pass
        # This requires 0 failures across ALL checks (system, files, dirs, env variables)
        # So failures must be exactly 0
        ready_for_live_call = (self.failures == 0)

        # Build recommended next command
        next_command = ""
        if not self.env_file_path.exists():
            next_command = f"cp {self.repo_root.name}/.env.production.example {self.env_file_path.name} && nano {self.env_file_path.name}"
        elif self.failures > 0:
            next_command = "Resolve the listed FAIL row items, then re-run: python -m ops.deployment_doctor"
        else:
            next_command = f"docker compose --env-file {self.env_file_path.name} up -d"

        # Generate masked env view for logs
        masked_env = {}
        for k, v in self.env_dict.items():
            masked_env[k] = mask_value(k, v)

        return {
            "PRODUCTION_ENV_VALID": prod_env_valid,
            "READY_TO_START_SERVICES": ready_to_start,
            "READY_FOR_LIVE_CALL_TEST": ready_for_live_call,
            "failures": self.failures,
            "warnings": self.warnings,
            "recommended_next_command": next_command,
            "checks": self.results,
            "masked_environment": masked_env
        }


def main():
    parser = argparse.ArgumentParser(description="Dana Deployment Doctor")
    parser.add_argument("--env-file", default=".env", help="Path to environment file (defaults to .env)")
    parser.add_argument("--json", action="store_true", help="Output only valid JSON results")
    args = parser.parse_args()

    env_path = Path(args.env_file)
    doctor = DeploymentDoctor(env_path)
    report = doctor.audit()

    if args.json:
        print(json.dumps(report, indent=2))
        if report["failures"] > 0:
            sys.exit(1)
        sys.exit(0)

    # Human-readable output console log
    print("=" * 80)
    print(" DANA DEPLOYMENT DOCTOR - PRODUCTION ENVIRONMENT AUDIT")
    print("=" * 80)
    print(f"Active Env File: {env_path.resolve()}\n")

    print(f"{'STATUS':<8} | {'CHECK NAME':<30} | {'MESSAGE'}")
    print("-" * 80)
    for check in report["checks"]:
        print(f"{check['status']:<8} | {check['name']:<30} | {check['message']}")
        if check["details"]:
            print(f"         └─ Details: {check['details']}")
    print("-" * 80)

    print("\n" + "=" * 80)
    print(" FINAL ENVIRONMENT STATUS ROLLUP")
    print("=" * 80)
    print(f" PRODUCTION_ENV_VALID        : {str(report['PRODUCTION_ENV_VALID']).upper()}")
    print(f" READY_TO_START_SERVICES    : {str(report['READY_TO_START_SERVICES']).upper()}")
    print(f" READY_FOR_LIVE_CALL_TEST   : {str(report['READY_FOR_LIVE_CALL_TEST']).upper()}")
    print(f" Failures detected          : {report['failures']}")
    print(f" Warnings detected          : {report['warnings']}")
    print("=" * 80)
    
    print(f"\nRecommended next command:\n  {report['recommended_next_command']}\n")
    
    # Masked Environment Check list for confirmation
    print("Masked environment variables snapshot:")
    for k, v in sorted(report["masked_environment"].items()):
        print(f"  {k}={v}")
    print("=" * 80)

    if report["failures"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
