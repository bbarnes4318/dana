"""Base tool abstractions for Dana voice agent.

Provides the ``BaseTool`` abstract class that every concrete tool must
implement, and the ``ToolResult`` dataclass returned by all tool
executions.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ToolResult:
    """Standardised result returned by every tool execution.

    Attributes:
        success: Whether the tool executed without error.
        data: Arbitrary key/value payload produced by the tool.
        message: Human-readable summary of the outcome.
        error: Error description when ``success`` is ``False``.
    """

    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    error: Optional[str] = None


class BaseTool(abc.ABC):
    """Abstract base class for all Dana tools.

    Subclasses must implement :pyattr:`name`, :pyattr:`description`,
    and :pymeth:`execute`.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Unique identifier for this tool."""

    @property
    @abc.abstractmethod
    def description(self) -> str:
        """Short human-readable description of what this tool does."""

    @abc.abstractmethod
    async def execute(self, params: dict[str, Any]) -> ToolResult:
        """Run the tool with the given parameters.

        Args:
            params: Tool-specific key/value arguments.

        Returns:
            A ``ToolResult`` describing the outcome.
        """
