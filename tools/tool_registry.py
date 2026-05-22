"""ToolRegistry — central registry for all Dana tools.

Provides registration, lookup, listing, and execution of tools by name.
All five built-in tools are pre-registered on instantiation.
"""

from __future__ import annotations

import logging
from typing import Any

from tools.base import BaseTool, ToolResult
from tools.escalate_to_human import EscalateToHumanTool
from tools.mark_dnc import MarkDNCTool
from tools.save_lead import SaveLeadTool
from tools.schedule_callback import ScheduleCallbackTool
from tools.transfer_to_agent import TransferToAgentTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registry that maps tool names to ``BaseTool`` instances.

    All five built-in tools are automatically registered when the
    registry is created.  Additional tools can be registered at any
    time via :meth:`register`.
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._register_defaults()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def register(self, tool: BaseTool) -> None:
        """Register a tool under its ``name``.

        Raises:
            ValueError: If a tool with the same name is already registered.
        """
        if tool.name in self._tools:
            raise ValueError(
                f"Tool '{tool.name}' is already registered"
            )
        self._tools[tool.name] = tool
        logger.debug("Registered tool: %s", tool.name)

    def get_tool(self, name: str) -> BaseTool:
        """Return the tool registered under *name*.

        Raises:
            KeyError: If no tool with that name exists.
        """
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(f"No tool registered with name '{name}'") from None

    def list_tools(self) -> list[BaseTool]:
        """Return all registered tools (insertion order)."""
        return list(self._tools.values())

    async def execute_tool(
        self, name: str, params: dict[str, Any]
    ) -> ToolResult:
        """Look up *name* and run it with *params*.

        Returns:
            The ``ToolResult`` from the tool, or a failure result if the
            tool is not found or raises an unexpected exception.
        """
        try:
            tool = self.get_tool(name)
        except KeyError as exc:
            return ToolResult(
                success=False,
                message=f"Tool '{name}' not found",
                error=str(exc),
            )

        try:
            return await tool.execute(params)
        except Exception as exc:
            logger.exception("Unexpected error executing tool '%s'", name)
            return ToolResult(
                success=False,
                message=f"Tool '{name}' raised an unexpected error",
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _register_defaults(self) -> None:
        """Pre-register the five built-in tools."""
        for tool in (
            SaveLeadTool(),
            TransferToAgentTool(),
            ScheduleCallbackTool(),
            MarkDNCTool(),
            EscalateToHumanTool(),
        ):
            self.register(tool)
