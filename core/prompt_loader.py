"""Prompt loader for Dana voice agent.

Loads markdown prompt files and YAML configuration, assembles composite
system prompts, and caches results for fast repeated access.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Sentinel default prompts used when files are missing
_DEFAULT_PROMPTS: dict[str, str] = {
    "final_expense_agent": (
        "You are Dana, a friendly outbound final expense qualification agent. "
        "Qualify prospects by confirming identity, age (45-85), state, and "
        "general health, then transfer to a licensed agent."
    ),
    "voice_style_rules": (
        "Speak naturally. One question at a time. Keep answers under two sentences. "
        "Use contractions. No chatbot phrasing."
    ),
    "compliance_guardrails": (
        "Never quote premiums. Never guarantee approval. Never claim government "
        "affiliation. Honor DNC requests immediately."
    ),
}

# The order in which prompt files are assembled into the system prompt
_PROMPT_ORDER: list[str] = [
    "final_expense_agent",
    "voice_style_rules",
    "compliance_guardrails",
]


class PromptLoader:
    """Loads and caches prompt markdown files and YAML configs.

    Parameters
    ----------
    prompts_dir:
        Directory containing ``.md`` prompt files. Defaults to ``prompts/``
        relative to the project root.
    config_dir:
        Directory containing ``.yaml`` config files. Defaults to ``config/``
        relative to the project root.
    project_root:
        The project root directory. When *None*, the parent of the directory
        containing this source file is used (i.e. ``core/`` → project root).
    """

    def __init__(
        self,
        prompts_dir: str | Path | None = None,
        config_dir: str | Path | None = None,
        project_root: str | Path | None = None,
    ) -> None:
        if project_root is None:
            # core/prompt_loader.py → project root is one level up
            project_root = Path(__file__).resolve().parent.parent

        self._project_root = Path(project_root)
        self._prompts_dir = Path(prompts_dir) if prompts_dir else self._project_root / "prompts"
        self._config_dir = Path(config_dir) if config_dir else self._project_root / "config"

        # In-memory caches
        self._prompt_cache: dict[str, str] = {}
        self._config_cache: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_prompt(self, name: str) -> str:
        """Load a single prompt file by name (without extension).

        The file is expected at ``<prompts_dir>/<name>.md``. If the file
        is missing, a built-in default is returned (and a warning logged).

        Results are cached so the file is only read once.

        Parameters
        ----------
        name:
            Prompt file stem, e.g. ``"final_expense_agent"``.

        Returns
        -------
        str
            The prompt text.
        """
        if name in self._prompt_cache:
            return self._prompt_cache[name]

        filepath = self._prompts_dir / f"{name}.md"
        try:
            text = filepath.read_text(encoding="utf-8")
            logger.debug("Loaded prompt '%s' from %s", name, filepath)
        except FileNotFoundError:
            text = _DEFAULT_PROMPTS.get(name, "")
            if text:
                logger.warning(
                    "Prompt file '%s' not found at %s — using built-in default.",
                    name,
                    filepath,
                )
            else:
                logger.warning(
                    "Prompt file '%s' not found and no default available.", name
                )
        except OSError as exc:
            logger.error("Error reading prompt '%s': %s", name, exc)
            text = _DEFAULT_PROMPTS.get(name, "")

        self._prompt_cache[name] = text
        return text

    def build_system_prompt(self) -> str:
        """Build the full composite system prompt.

        Concatenates the core prompt files in the canonical order:

        1. ``final_expense_agent.md``
        2. ``voice_style_rules.md``
        3. ``compliance_guardrails.md``

        Each section is separated by two newlines.

        Returns
        -------
        str
            The assembled system prompt ready to pass to the LLM.
        """
        sections: list[str] = []
        for name in _PROMPT_ORDER:
            content = self.load_prompt(name)
            if content:
                sections.append(content.strip())

        return "\n\n".join(sections)

    def get_config(self, config_name: str) -> dict[str, Any]:
        """Load and return a YAML config file by name (without extension).

        The file is expected at ``<config_dir>/<config_name>.yaml``.
        Results are cached after first load.

        Parameters
        ----------
        config_name:
            Config file stem, e.g. ``"agent_config"`` or
            ``"final_expense_config"``.

        Returns
        -------
        dict
            Parsed YAML as a dictionary.  Returns an empty dict if the
            file is missing or unparseable.
        """
        if config_name in self._config_cache:
            return self._config_cache[config_name]

        filepath = self._config_dir / f"{config_name}.yaml"
        try:
            with open(filepath, encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            logger.debug("Loaded config '%s' from %s", config_name, filepath)
        except FileNotFoundError:
            logger.warning(
                "Config file '%s' not found at %s — returning empty dict.",
                config_name,
                filepath,
            )
            data = {}
        except (yaml.YAMLError, OSError) as exc:
            logger.error("Error loading config '%s': %s", config_name, exc)
            data = {}

        self._config_cache[config_name] = data
        return data

    def clear_cache(self) -> None:
        """Clear all cached prompts and configs, forcing a reload on next access."""
        self._prompt_cache.clear()
        self._config_cache.clear()
        logger.debug("Prompt and config caches cleared.")
