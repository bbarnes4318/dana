import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

def find_repo_root(start_path: Optional[Path] = None) -> Path:
    """
    Locate repository root by walking upward from the current file
    until it finds requirements.txt or .git.
    """
    if start_path is None:
        # Start from this file
        start_path = Path(__file__).resolve().parent
    
    current = start_path
    while True:
        if (current / "requirements.txt").exists() or (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            # Fallback to current working directory if root not found
            return Path.cwd()
        current = parent

def parse_env_file_fallback(path: Path) -> Dict[str, str]:
    """
    A small safe fallback parser for KEY=value lines in env files.
    Strips quotes, ignores empty/blank lines and comments.
    """
    parsed = {}
    if not path.exists() or not path.is_file():
        return parsed
    
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            # Ignore empty/blank lines and comments
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                # Strip surrounding quotes
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                parsed[key] = val
    return parsed

def parse_env_file(path: Path) -> Dict[str, str]:
    """
    Parse an env file using python-dotenv if installed, otherwise falling back.
    """
    try:
        import dotenv
        if hasattr(dotenv, "dotenv_values"):
            # dotenv_values reads the file into a dict without touching os.environ
            vals = dotenv.dotenv_values(str(path))
            return {k: str(v) if v is not None else "" for k, v in vals.items()}
    except ImportError:
        pass
    return parse_env_file_fallback(path)

def load_environment() -> dict:
    """
    Locates the repository root, loads environment variables from:
      a. repo-root .env
      b. repo-root .env.local
      c. optional path in DANA_ENV_FILE
    
    Respects DANA_ENV_OVERRIDE=true.
    Strips surrounding quotes and ignores comments/blank lines.
    Never returns secret values.
    """
    repo_root = find_repo_root()
    
    # Files to try loading in precedence order (later overrides earlier)
    env_files_to_try = [
        repo_root / ".env",
        repo_root / ".env.local"
    ]
    
    custom_env_path = os.environ.get("DANA_ENV_FILE")
    if custom_env_path:
        p = Path(custom_env_path)
        if not p.is_absolute():
            p = repo_root / p
        env_files_to_try.append(p)
    
    loaded_files = []
    missing_files = []
    
    merged_vars: Dict[str, str] = {}
    
    for filepath in env_files_to_try:
        resolved_path = filepath.resolve()
        if resolved_path.exists() and resolved_path.is_file():
            parsed = parse_env_file(resolved_path)
            merged_vars.update(parsed)
            # Avoid duplicate paths in list
            path_str = str(resolved_path)
            if path_str not in loaded_files:
                loaded_files.append(path_str)
        else:
            path_str = str(resolved_path)
            if path_str not in missing_files:
                missing_files.append(path_str)
                
    # Determine override configuration
    override_env_val = os.environ.get("DANA_ENV_OVERRIDE")
    override_parsed_val = merged_vars.get("DANA_ENV_OVERRIDE")
    
    override_enabled = (
        (override_env_val is not None and override_env_val.lower() == "true") or
        (override_parsed_val is not None and override_parsed_val.lower() == "true")
    )
    
    keys_loaded = []
    for key, val in merged_vars.items():
        if key not in os.environ or override_enabled:
            os.environ[key] = val
            keys_loaded.append(key)
            
    return {
        "loaded_files": loaded_files,
        "missing_files": missing_files,
        "override_enabled": override_enabled,
        "keys_loaded": sorted(keys_loaded),
        "secret_keys_masked": True
    }
