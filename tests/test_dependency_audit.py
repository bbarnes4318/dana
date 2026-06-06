"""Tests for dependency audit tool."""

import os
from unittest.mock import patch
import pytest
from ops.dependency_audit import audit_dependencies


def test_dependency_audit_passed(tmp_path):
    constraints_file = tmp_path / "mock_constraints.txt"
    constraints_file.write_text(
        "# Comments\n"
        "livekit==1.1.8\n"
        "faster-whisper==1.2.1\n",
        encoding="utf-8"
    )
    
    def mock_version(pkg_name):
        if pkg_name == "livekit":
            return "1.1.8"
        if pkg_name == "faster-whisper":
            return "1.2.1"
        raise importlib.metadata.PackageNotFoundError

    with patch("importlib.metadata.version", side_effect=mock_version):
        passed, errors = audit_dependencies(str(constraints_file))
        assert passed is True
        assert len(errors) == 0


def test_dependency_audit_version_mismatch(tmp_path):
    constraints_file = tmp_path / "mock_constraints.txt"
    constraints_file.write_text(
        "livekit==1.1.8\n",
        encoding="utf-8"
    )

    with patch("importlib.metadata.version", return_value="1.2.0"):
        passed, errors = audit_dependencies(str(constraints_file))
        assert passed is False
        assert len(errors) == 1
        assert "Version mismatch for 'livekit'" in errors[0]


def test_dependency_audit_missing_package(tmp_path):
    constraints_file = tmp_path / "mock_constraints.txt"
    constraints_file.write_text(
        "livekit==1.1.8\n",
        encoding="utf-8"
    )

    import importlib.metadata
    with patch("importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError):
        passed, errors = audit_dependencies(str(constraints_file))
        assert passed is False
        assert len(errors) == 1
        assert "Package 'livekit' is not installed" in errors[0]
