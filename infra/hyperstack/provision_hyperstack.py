#!/usr/bin/env python3
"""
Provision a Hyperstack GPU VM for Dana.

Reads credentials from environment variables only. Never prints API keys or
private keys. Derives the SSH public key automatically from the private key.

Usage:
    # Dry run (default) — validates config, prints summary, creates nothing
    python provision_hyperstack.py

    # Actually provision
    DANA_CONFIRM_PROVISION=yes python provision_hyperstack.py

    # Override defaults via env or CLI
    HYPERSTACK_REGION=us-east python provision_hyperstack.py --vm-name dana-dev
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import textwrap
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INFRAHUB_BASE_URL = "https://infrahub-api.nexgencloud.com/v1"

SCRIPT_DIR = Path(__file__).resolve().parent
LAST_VM_JSON = SCRIPT_DIR / "last_vm.json"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _env(name: str, default: str | None = None, required: bool = False) -> str | None:
    """Read an environment variable with optional default and required check."""
    value = os.environ.get(name, default)
    if required and not value:
        print(f"ERROR: Required environment variable {name} is not set.", file=sys.stderr)
        sys.exit(1)
    return value


def build_config(args: argparse.Namespace) -> dict:
    """Merge environment variables, defaults, and CLI overrides into config."""
    config = {
        "api_key": _env("HYPERSTACK_API_KEY", required=True),
        "ssh_private_key_path": _env("HYPERSTACK_SSH_PRIVATE_KEY_PATH", required=True),
        "region": args.region or _env("HYPERSTACK_REGION", required=True),
        "image": args.image or _env("HYPERSTACK_IMAGE", default="Ubuntu 22.04"),
        "flavor": args.flavor or _env("HYPERSTACK_FLAVOR"),
        "gpu_model": args.gpu_model or _env("HYPERSTACK_GPU_MODEL", default="L40"),
        "vm_name": args.vm_name or _env("HYPERSTACK_VM_NAME", default="dana-l40-prod"),
        "disk_size_gb": int(
            args.disk_size_gb
            or _env("HYPERSTACK_DISK_SIZE_GB", default="500")
        ),
        "confirm": _env("DANA_CONFIRM_PROVISION", default="no"),
    }
    return config


# ---------------------------------------------------------------------------
# SSH key helpers
# ---------------------------------------------------------------------------


def derive_public_key(private_key_path: str) -> str:
    """Derive the SSH public key from a private key file using ssh-keygen."""
    pk_path = Path(private_key_path).expanduser().resolve()
    if not pk_path.is_file():
        print(f"ERROR: SSH private key not found at {pk_path}", file=sys.stderr)
        sys.exit(1)

    try:
        result = subprocess.run(
            ["ssh-keygen", "-y", "-f", str(pk_path)],
            capture_output=True,
            text=True,
            check=True,
        )
        public_key = result.stdout.strip()
        if not public_key:
            print("ERROR: ssh-keygen produced empty output.", file=sys.stderr)
            sys.exit(1)
        return public_key
    except FileNotFoundError:
        print("ERROR: ssh-keygen not found. Install OpenSSH.", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: ssh-keygen failed: {exc.stderr.strip()}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Hyperstack API helpers
# ---------------------------------------------------------------------------


def _api_request(
    method: str,
    path: str,
    api_key: str,
    body: dict | None = None,
) -> dict:
    """Make an authenticated request to the Hyperstack Infrahub API."""
    url = f"{INFRAHUB_BASE_URL}{path}"
    headers = {
        "api_key": api_key,
        "Content-Type": "application/json",
    }

    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode() if exc.fp else ""
        print(f"API error {exc.code} on {method} {path}: {error_body}", file=sys.stderr)
        raise
    except urllib.error.URLError as exc:
        print(f"Network error on {method} {path}: {exc.reason}", file=sys.stderr)
        raise


def list_flavors(api_key: str) -> list[dict]:
    """GET /core/flavors — list available VM flavors."""
    resp = _api_request("GET", "/core/flavors", api_key)
    return resp.get("flavors", resp.get("data", []))


def list_images(api_key: str) -> list[dict]:
    """GET /core/images — list available OS images."""
    resp = _api_request("GET", "/core/images", api_key)
    return resp.get("images", resp.get("data", []))


def list_keypairs(api_key: str) -> list[dict]:
    """GET /core/keypairs — list registered SSH keypairs."""
    resp = _api_request("GET", "/core/keypairs", api_key)
    return resp.get("keypairs", resp.get("data", []))


def create_keypair(api_key: str, name: str, public_key: str) -> dict:
    """POST /core/keypairs — register an SSH public key."""
    body = {
        "name": name,
        "public_key": public_key,
    }
    return _api_request("POST", "/core/keypairs", api_key, body=body)


def create_vm(api_key: str, payload: dict) -> dict:
    """POST /core/virtual-machines — create a new VM."""
    return _api_request("POST", "/core/virtual-machines", api_key, body=payload)


def get_vm(api_key: str, vm_id: int) -> dict:
    """GET /core/virtual-machines/{id} — get VM details."""
    return _api_request("GET", f"/core/virtual-machines/{vm_id}", api_key)


def list_vms(api_key: str) -> list[dict]:
    """GET /core/virtual-machines — list all VMs."""
    resp = _api_request("GET", "/core/virtual-machines", api_key)
    return resp.get("virtual_machines", resp.get("data", []))


# ---------------------------------------------------------------------------
# Keypair management
# ---------------------------------------------------------------------------


def ensure_keypair(api_key: str, public_key: str, key_name: str = "dana-provision") -> str:
    """Ensure the public key is registered with Hyperstack. Returns keypair name."""
    existing = list_keypairs(api_key)
    for kp in existing:
        if kp.get("name") == key_name:
            print(f"  Keypair '{key_name}' already registered.")
            return key_name

    print(f"  Registering keypair '{key_name}' with Hyperstack...")
    create_keypair(api_key, key_name, public_key)
    print(f"  Keypair '{key_name}' registered.")
    return key_name


# ---------------------------------------------------------------------------
# VM provisioning
# ---------------------------------------------------------------------------


def build_vm_payload(config: dict, keypair_name: str) -> dict:
    """Build the JSON payload for POST /core/virtual-machines."""
    payload = {
        "name": config["vm_name"],
        "image_name": config["image"],
        "key_name": keypair_name,
        # TODO: Adjust field names if the Hyperstack API uses different keys.
        #       Check API docs for exact schema.
    }

    if config.get("flavor"):
        payload["flavor_name"] = config["flavor"]

    if config.get("region"):
        payload["region"] = config["region"]

    if config.get("gpu_model"):
        # TODO: Confirm the correct field name for GPU model selection.
        payload["gpu_model"] = config["gpu_model"]

    if config.get("disk_size_gb"):
        # TODO: Confirm the correct field name for disk size.
        payload["volume_size"] = config["disk_size_gb"]

    return payload


def provision(config: dict) -> None:
    """Main provisioning flow."""
    api_key = config["api_key"]
    is_live = config["confirm"] == "yes"

    print("=" * 60)
    print("  Dana — Hyperstack VM Provisioning")
    print("=" * 60)
    print()

    # --- Derive public key ---
    print("[1/4] Deriving SSH public key...")
    public_key = derive_public_key(config["ssh_private_key_path"])
    # Print only a safe prefix for verification
    pk_preview = public_key[:40] + "..." if len(public_key) > 40 else public_key
    print(f"  Public key (prefix): {pk_preview}")
    print()

    # --- Configuration summary (no secrets) ---
    print("[2/4] Configuration summary:")
    print(f"  Region:        {config['region']}")
    print(f"  Image:         {config['image']}")
    print(f"  Flavor:        {config['flavor'] or '(not set — TODO: discover from API)'}")
    print(f"  GPU model:     {config['gpu_model']}")
    print(f"  VM name:       {config['vm_name']}")
    print(f"  Disk size:     {config['disk_size_gb']} GB")
    print(f"  Key path:      {config['ssh_private_key_path']}")
    print(f"  API key:       ****{config['api_key'][-4:]}")
    print(f"  Mode:          {'LIVE — will create resources' if is_live else 'DRY RUN — no resources created'}")
    print()

    if not is_live:
        print("[DRY RUN] Validating configuration...")
        _validate_config(config)
        print()
        print("[DRY RUN] To provision for real, run with:")
        print("  DANA_CONFIRM_PROVISION=yes python provision_hyperstack.py")
        print()
        return

    # --- Register keypair ---
    print("[3/4] Ensuring SSH keypair is registered...")
    keypair_name = ensure_keypair(api_key, public_key)
    print()

    # --- Create VM ---
    print("[4/4] Creating VM...")
    payload = build_vm_payload(config, keypair_name)
    print(f"  Payload (non-sensitive): { {k: v for k, v in payload.items() if k != 'api_key'} }")

    result = create_vm(api_key, payload)
    vm_data = result.get("virtual_machine", result.get("data", result))

    vm_id = vm_data.get("id", "unknown")
    vm_status = vm_data.get("status", "unknown")
    public_ip = vm_data.get("floating_ip", vm_data.get("public_ip", "pending"))

    print()
    print("  VM created successfully!")
    print(f"  VM ID:      {vm_id}")
    print(f"  Status:     {vm_status}")
    print(f"  Public IP:  {public_ip}")
    print()
    print(f"  SSH command:")
    print(f"    ssh -i {config['ssh_private_key_path']} ubuntu@{public_ip}")
    print()

    # --- Save non-sensitive VM info ---
    vm_info = {
        "vm_id": vm_id,
        "vm_name": config["vm_name"],
        "status": vm_status,
        "public_ip": public_ip,
        "region": config["region"],
        "image": config["image"],
        "flavor": config.get("flavor"),
        "gpu_model": config["gpu_model"],
        "disk_size_gb": config["disk_size_gb"],
        "ssh_key_path": config["ssh_private_key_path"],
        "provisioned_at": datetime.now(timezone.utc).isoformat(),
    }
    LAST_VM_JSON.write_text(json.dumps(vm_info, indent=2) + "\n")
    print(f"  VM info saved to {LAST_VM_JSON}")


def _validate_config(config: dict) -> None:
    """Validate configuration without making API calls (for dry-run)."""
    errors = []

    pk_path = Path(config["ssh_private_key_path"]).expanduser().resolve()
    if not pk_path.is_file():
        errors.append(f"SSH private key not found: {pk_path}")

    if not config["region"]:
        errors.append("HYPERSTACK_REGION is required but not set.")

    if not config.get("flavor"):
        print("  WARNING: No flavor set. You may need to set HYPERSTACK_FLAVOR")
        print("           or use --flavor. Run list-flavors to see options.")
        # TODO: Call list_flavors() to discover and suggest available flavors.

    if config["disk_size_gb"] < 10:
        errors.append(f"Disk size {config['disk_size_gb']} GB seems too small.")

    if errors:
        for err in errors:
            print(f"  ERROR: {err}", file=sys.stderr)
        sys.exit(1)
    else:
        print("  Configuration looks valid.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Provision a Hyperstack GPU VM for Dana.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Environment variables (all overridable via CLI flags):
              HYPERSTACK_API_KEY              API key (required, never printed)
              HYPERSTACK_SSH_PRIVATE_KEY_PATH Path to SSH private key (required)
              HYPERSTACK_REGION               Region (required)
              HYPERSTACK_IMAGE                OS image (default: Ubuntu 22.04)
              HYPERSTACK_FLAVOR               VM flavor (no default)
              HYPERSTACK_GPU_MODEL            GPU model (default: L40)
              HYPERSTACK_VM_NAME              VM name (default: dana-l40-prod)
              HYPERSTACK_DISK_SIZE_GB         Disk size in GB (default: 500)
              DANA_CONFIRM_PROVISION          Set to 'yes' to create resources
        """),
    )
    parser.add_argument("--region", help="Hyperstack region")
    parser.add_argument("--image", help="OS image name")
    parser.add_argument("--flavor", help="VM flavor name")
    parser.add_argument("--gpu-model", help="GPU model")
    parser.add_argument("--vm-name", help="VM display name")
    parser.add_argument("--disk-size-gb", type=int, help="Root disk size in GB")
    parser.add_argument(
        "--list-flavors",
        action="store_true",
        help="List available flavors and exit",
    )
    parser.add_argument(
        "--list-images",
        action="store_true",
        help="List available images and exit",
    )
    parser.add_argument(
        "--list-vms",
        action="store_true",
        help="List existing VMs and exit",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Quick-list commands only need the API key
    if args.list_flavors or args.list_images or args.list_vms:
        api_key = _env("HYPERSTACK_API_KEY", required=True)

        if args.list_flavors:
            flavors = list_flavors(api_key)
            print(json.dumps(flavors, indent=2))

        if args.list_images:
            images = list_images(api_key)
            print(json.dumps(images, indent=2))

        if args.list_vms:
            vms = list_vms(api_key)
            print(json.dumps(vms, indent=2))

        return

    config = build_config(args)
    provision(config)


if __name__ == "__main__":
    main()
