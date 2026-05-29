"""Tests for core.prompt_loader.PromptLoader."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from core.prompt_loader import PromptLoader


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def prompt_dir(tmp_path: Path) -> Path:
    """Create a temporary prompts directory with sample markdown files."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()

    (prompts / "final_expense_agent.md").write_text(
        "# Dana\nYou are Dana, a final expense qualification agent.",
        encoding="utf-8",
    )
    (prompts / "voice_style_rules.md").write_text(
        "# Voice Rules\nAsk one question at a time.",
        encoding="utf-8",
    )
    (prompts / "compliance_guardrails.md").write_text(
        "# Compliance\nNever quote exact premiums.",
        encoding="utf-8",
    )
    return prompts


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    """Create a temporary config directory with sample YAML files."""
    configs = tmp_path / "config"
    configs.mkdir()

    agent_cfg = {
        "agent_name": "Dana",
        "voice": {"tts_engine": "kokoro", "speed": 1.0},
        "llm": {"model": "meta-llama/Meta-Llama-3.1-8B-Instruct", "temperature": 0.7},
    }
    (configs / "agent_config.yaml").write_text(
        yaml.dump(agent_cfg, default_flow_style=False),
        encoding="utf-8",
    )

    fe_cfg = {
        "qualification": {
            "disqualifier_age_min": 45,
            "disqualifier_age_max": 85,
            "max_objection_attempts": 2,
        },
        "supported_states": ["FL", "TX", "CA"],
    }
    (configs / "final_expense_config.yaml").write_text(
        yaml.dump(fe_cfg, default_flow_style=False),
        encoding="utf-8",
    )
    return configs


@pytest.fixture()
def loader(prompt_dir: Path, config_dir: Path) -> PromptLoader:
    """Return a PromptLoader pointed at the temp directories."""
    return PromptLoader(prompts_dir=prompt_dir, config_dir=config_dir)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLoadPrompts:
    """Test loading individual prompt files."""

    def test_load_prompts(self, loader: PromptLoader) -> None:
        """All three prompt files should load successfully."""
        agent = loader.load_prompt("final_expense_agent")
        voice = loader.load_prompt("voice_style_rules")
        compliance = loader.load_prompt("compliance_guardrails")

        assert "Dana" in agent
        assert "one question" in voice.lower()
        assert "premiums" in compliance.lower()

    def test_each_prompt_is_nonempty(self, loader: PromptLoader) -> None:
        for name in ("final_expense_agent", "voice_style_rules", "compliance_guardrails"):
            assert len(loader.load_prompt(name)) > 0


class TestBuildSystemPrompt:
    """Test the composite system prompt builder."""

    def test_build_system_prompt(self, loader: PromptLoader) -> None:
        """Combined prompt should contain content from all three files."""
        prompt = loader.build_system_prompt()

        assert "Dana" in prompt
        assert "one question" in prompt.lower()
        assert "premiums" in prompt.lower()

    def test_sections_separated_by_newlines(self, loader: PromptLoader) -> None:
        """Each section should be separated by a double newline."""
        prompt = loader.build_system_prompt()
        # There should be exactly 2 double-newline separators for 3 sections
        assert prompt.count("\n\n") >= 2


class TestMissingFileUsesDefault:
    """Test graceful fallback when prompt files are missing."""

    def test_missing_file_uses_default(self, config_dir: Path, tmp_path: Path) -> None:
        """When a prompt file is missing, the built-in default should be used."""
        empty_prompts = tmp_path / "empty_prompts"
        empty_prompts.mkdir()

        loader = PromptLoader(prompts_dir=empty_prompts, config_dir=config_dir)
        prompt = loader.load_prompt("final_expense_agent")

        # Should get the default prompt, not an empty string
        assert len(prompt) > 0
        assert "Alex" in prompt

    def test_missing_unknown_prompt_returns_empty(self, config_dir: Path, tmp_path: Path) -> None:
        """A missing file with no built-in default should return empty string."""
        empty_prompts = tmp_path / "empty_prompts"
        empty_prompts.mkdir()

        loader = PromptLoader(prompts_dir=empty_prompts, config_dir=config_dir)
        result = loader.load_prompt("nonexistent_prompt")
        assert result == ""

    def test_build_system_prompt_with_missing_files(
        self, config_dir: Path, tmp_path: Path
    ) -> None:
        """build_system_prompt should still work when files are missing (uses defaults)."""
        empty_prompts = tmp_path / "empty_prompts"
        empty_prompts.mkdir()

        loader = PromptLoader(prompts_dir=empty_prompts, config_dir=config_dir)
        prompt = loader.build_system_prompt()

        assert len(prompt) > 0
        assert "Alex" in prompt


class TestConfigLoading:
    """Test YAML configuration loading."""

    def test_config_loading(self, loader: PromptLoader) -> None:
        """agent_config.yaml should load and contain expected keys."""
        cfg = loader.get_config("agent_config")

        assert cfg["agent_name"] == "Dana"
        assert cfg["voice"]["tts_engine"] == "kokoro"
        assert cfg["llm"]["temperature"] == 0.7

    def test_final_expense_config(self, loader: PromptLoader) -> None:
        """final_expense_config.yaml should load with correct values."""
        cfg = loader.get_config("final_expense_config")

        assert cfg["qualification"]["disqualifier_age_min"] == 45
        assert cfg["qualification"]["disqualifier_age_max"] == 85
        assert "FL" in cfg["supported_states"]

    def test_missing_config_returns_empty_dict(self, loader: PromptLoader) -> None:
        """A missing config file should return an empty dict, not raise."""
        cfg = loader.get_config("nonexistent_config")
        assert cfg == {}


class TestPromptCaching:
    """Test that prompts and configs are cached after first load."""

    def test_prompt_caching(self, loader: PromptLoader, prompt_dir: Path) -> None:
        """Loading the same prompt twice should return the cached version."""
        first = loader.load_prompt("final_expense_agent")

        # Modify the file on disk after first load
        (prompt_dir / "final_expense_agent.md").write_text(
            "# Modified\nThis content has changed.",
            encoding="utf-8",
        )

        second = loader.load_prompt("final_expense_agent")

        # Should still get the original cached version
        assert first == second
        assert "Dana" in second  # original content, not "Modified"

    def test_config_caching(self, loader: PromptLoader, config_dir: Path) -> None:
        """Loading the same config twice should return the cached version."""
        first = loader.get_config("agent_config")

        # Modify on disk
        (config_dir / "agent_config.yaml").write_text(
            yaml.dump({"agent_name": "NotDana"}),
            encoding="utf-8",
        )

        second = loader.get_config("agent_config")
        assert first == second
        assert second["agent_name"] == "Dana"

    def test_clear_cache_forces_reload(
        self, loader: PromptLoader, prompt_dir: Path
    ) -> None:
        """After clearing cache, the next load should read from disk again."""
        loader.load_prompt("final_expense_agent")

        (prompt_dir / "final_expense_agent.md").write_text(
            "# Updated\nFresh content after cache clear.",
            encoding="utf-8",
        )

        loader.clear_cache()
        reloaded = loader.load_prompt("final_expense_agent")

        assert "Fresh content" in reloaded
