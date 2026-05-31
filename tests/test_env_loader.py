import os
import sys
import tempfile
import shutil
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock

# Ensure parent directory is in sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.env_loader import load_environment, find_repo_root

@pytest.fixture
def clean_env():
    """Backup and restore os.environ after each test."""
    old_env = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(old_env)

@pytest.fixture
def temp_repo_dir():
    """Create a temporary repository directory structure."""
    temp_dir = tempfile.mkdtemp()
    # Create requirements.txt to identify it as repo root
    requirements_path = Path(temp_dir) / "requirements.txt"
    requirements_path.touch()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)

def test_loads_repo_root_env_file(clean_env, temp_repo_dir):
    # Write key to .env
    env_file = temp_repo_dir / ".env"
    env_file.write_text("TEST_KEY_ROOT=root_val\n", encoding="utf-8")
    
    with patch("config.env_loader.find_repo_root", return_value=temp_repo_dir):
        res = load_environment()
        
    assert os.environ.get("TEST_KEY_ROOT") == "root_val"
    assert "TEST_KEY_ROOT" in res["keys_loaded"]
    assert str(env_file.resolve()) in res["loaded_files"]

def test_loads_env_local(clean_env, temp_repo_dir):
    # Write to both .env and .env.local
    env_file = temp_repo_dir / ".env"
    env_file.write_text("TEST_KEY_LOCAL=root_val\nOTHER_KEY=1\n", encoding="utf-8")
    
    env_local_file = temp_repo_dir / ".env.local"
    env_local_file.write_text("TEST_KEY_LOCAL=local_val\n", encoding="utf-8")
    
    with patch("config.env_loader.find_repo_root", return_value=temp_repo_dir):
        res = load_environment()
        
    assert os.environ.get("TEST_KEY_LOCAL") == "local_val"
    assert os.environ.get("OTHER_KEY") == "1"
    assert "TEST_KEY_LOCAL" in res["keys_loaded"]
    assert str(env_local_file.resolve()) in res["loaded_files"]

def test_dana_env_file_override_path(clean_env, temp_repo_dir):
    # Write custom env file
    custom_file = temp_repo_dir / "custom.env"
    custom_file.write_text("TEST_KEY_CUSTOM=custom_val\n", encoding="utf-8")
    
    os.environ["DANA_ENV_FILE"] = str(custom_file)
    
    with patch("config.env_loader.find_repo_root", return_value=temp_repo_dir):
        res = load_environment()
        
    assert os.environ.get("TEST_KEY_CUSTOM") == "custom_val"
    assert "TEST_KEY_CUSTOM" in res["keys_loaded"]
    assert str(custom_file.resolve()) in res["loaded_files"]

def test_does_not_override_existing_env_by_default(clean_env, temp_repo_dir):
    os.environ["TEST_EXISTING_KEY"] = "original_val"
    
    env_file = temp_repo_dir / ".env"
    env_file.write_text("TEST_EXISTING_KEY=new_val\n", encoding="utf-8")
    
    with patch("config.env_loader.find_repo_root", return_value=temp_repo_dir):
        res = load_environment()
        
    assert os.environ.get("TEST_EXISTING_KEY") == "original_val"
    assert "TEST_EXISTING_KEY" not in res["keys_loaded"]

def test_can_override_when_dana_env_override_true(clean_env, temp_repo_dir):
    os.environ["TEST_EXISTING_KEY"] = "original_val"
    os.environ["DANA_ENV_OVERRIDE"] = "true"
    
    env_file = temp_repo_dir / ".env"
    env_file.write_text("TEST_EXISTING_KEY=new_val\n", encoding="utf-8")
    
    with patch("config.env_loader.find_repo_root", return_value=temp_repo_dir):
        res = load_environment()
        
    assert os.environ.get("TEST_EXISTING_KEY") == "new_val"
    assert "TEST_EXISTING_KEY" in res["keys_loaded"]

def test_ignores_comments_and_blank_lines(clean_env, temp_repo_dir):
    env_file = temp_repo_dir / ".env"
    env_file.write_text("\n# This is a comment\nTEST_COMMENT_KEY=valid_val\n\n  # Nested comment\n", encoding="utf-8")
    
    with patch("config.env_loader.find_repo_root", return_value=temp_repo_dir):
        res = load_environment()
        
    assert os.environ.get("TEST_COMMENT_KEY") == "valid_val"
    assert len(res["keys_loaded"]) == 1
    assert "TEST_COMMENT_KEY" in res["keys_loaded"]

def test_strips_quotes(clean_env, temp_repo_dir):
    env_file = temp_repo_dir / ".env"
    env_file.write_text("KEY_DOUBLE=\"val_double\"\nKEY_SINGLE='val_single'\nKEY_NO=val_no\n", encoding="utf-8")
    
    with patch("config.env_loader.find_repo_root", return_value=temp_repo_dir):
        res = load_environment()
        
    assert os.environ.get("KEY_DOUBLE") == "val_double"
    assert os.environ.get("KEY_SINGLE") == "val_single"
    assert os.environ.get("KEY_NO") == "val_no"

def test_does_not_return_secret_values(clean_env, temp_repo_dir):
    # Ensure key is not present initially
    if "LIVEKIT_API_SECRET" in os.environ:
        del os.environ["LIVEKIT_API_SECRET"]
        
    env_file = temp_repo_dir / ".env"
    env_file.write_text("LIVEKIT_API_SECRET=my_ultra_secret_value\n", encoding="utf-8")
    
    with patch("config.env_loader.find_repo_root", return_value=temp_repo_dir):
        res = load_environment()
        
    # Check that the summary dict does not contain the secret value in any form
    res_str = str(res)
    assert "my_ultra_secret_value" not in res_str
    assert res["secret_keys_masked"] is True
    assert "LIVEKIT_API_SECRET" in res["keys_loaded"]

def test_live_worker_check_only_loads_dotenv():
    with patch("config.env_loader.load_environment") as mock_load, \
         patch("scripts.run_livekit_agent_worker.audit_worker_status") as mock_audit, \
         patch("sys.exit") as mock_exit:
        
        from telephony.livekit_agent_worker import WorkerDependencyStatus
        mock_audit.return_value = WorkerDependencyStatus(
            ready=True,
            status="ready",
            livekit_agents_installed=True,
            livekit_plugins_namespace_available=True,
            openai_plugin_available=True,
            silero_vad_plugin_available=True,
            agent_runtime_available=True,
            required_env_present=True
        )
        
        # We need to reload the script module so import-time code executes under mock
        import importlib
        import scripts.run_livekit_agent_worker
        importlib.reload(scripts.run_livekit_agent_worker)
        
        with patch("sys.argv", ["run_livekit_agent_worker.py", "--check-only"]):
            scripts.run_livekit_agent_worker.main()
            
        assert mock_load.call_count >= 1

def test_smoke_test_cli_loads_dotenv():
    with patch("config.env_loader.load_environment") as mock_load, \
         patch("telephony.live_smoke_test.LiveTelephonySmokeTester.run") as mock_run, \
         patch("sys.exit") as mock_exit:
        
        mock_run.return_value = MagicMock()
        mock_run.return_value.success = True
        mock_run.return_value.model_dump.return_value = {}
        
        import importlib
        import scripts.run_live_telephony_smoke_test
        importlib.reload(scripts.run_live_telephony_smoke_test)
        
        with patch("sys.argv", ["run_live_telephony_smoke_test.py", "--operator", "Jimmy", "--dry-run"]):
            import asyncio
            asyncio.run(scripts.run_live_telephony_smoke_test.main())
            
        assert mock_load.call_count >= 1

def test_readiness_cli_loads_dotenv():
    with patch("config.env_loader.load_environment") as mock_load, \
         patch("telephony.live_telephony_readiness.LiveTelephonyReadinessChecker.run") as mock_run, \
         patch("sys.exit") as mock_exit:
         
        mock_run.return_value = MagicMock()
        mock_run.return_value.ready = True
        mock_run.return_value.model_dump.return_value = {}
        
        import importlib
        import scripts.check_live_telephony_readiness
        importlib.reload(scripts.check_live_telephony_readiness)
        
        with patch("sys.argv", ["check_live_telephony_readiness.py"]):
            import asyncio
            asyncio.run(scripts.check_live_telephony_readiness.main())
            
        assert mock_load.call_count >= 1
