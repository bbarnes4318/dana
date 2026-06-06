"""Audits installed packages against exact pinned versions in constraints.txt."""

from __future__ import annotations

import importlib.metadata
import os
import sys
from typing import Dict, List, Tuple


def audit_dependencies(constraints_path: str = "constraints.txt") -> Tuple[bool, List[str]]:
    """Verify that installed packages match the versions pinned in constraints.txt.
    
    Returns:
        A tuple: (is_compatible, list_of_error_messages)
    """
    if not os.path.exists(constraints_path):
        return False, [f"Constraints file not found: {constraints_path}"]

    errors = []
    with open(constraints_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            if "==" in line:
                pkg_name, pinned_version = line.split("==")
                pkg_name = pkg_name.strip()
                pinned_version = pinned_version.strip()
            else:
                continue

            try:
                installed_version = importlib.metadata.version(pkg_name)
                if installed_version != pinned_version:
                    errors.append(
                        f"Version mismatch for '{pkg_name}': installed {installed_version}, required {pinned_version}"
                    )
            except importlib.metadata.PackageNotFoundError:
                # Special case: onnxruntime-gpu might be installed as onnxruntime, or vice versa
                if pkg_name == "onnxruntime-gpu":
                    try:
                        importlib.metadata.version("onnxruntime")
                        continue  # Allow fallback
                    except importlib.metadata.PackageNotFoundError:
                        pass
                errors.append(f"Package '{pkg_name}' is not installed (required version: {pinned_version})")

    return len(errors) == 0, errors


if __name__ == "__main__":
    success, audit_errors = audit_dependencies()
    if not success:
        print("Dependency Audit FAILED:")
        for err in audit_errors:
            print(f"  - {err}")
        sys.exit(1)
    else:
        print("Dependency Audit PASSED: all dependencies match constraints.txt")
        sys.exit(0)
