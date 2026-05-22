"""Tests for the tools package.

Each tool writes to a JSONL file under a temporary directory so tests
are fully isolated and leave no side-effects on the real filesystem.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.base import ToolResult
from tools.escalate_to_human import EscalateToHumanTool
from tools.mark_dnc import MarkDNCTool
from tools.save_lead import SaveLeadTool
from tools.schedule_callback import ScheduleCallbackTool
from tools.tool_registry import ToolRegistry
from tools.transfer_to_agent import TransferToAgentTool


# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------

def _read_jsonl(path: Path) -> list[dict]:
    """Read all JSON lines from *path*."""
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


# ------------------------------------------------------------------
# SaveLeadTool
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_lead_creates_file(tmp_path: Path) -> None:
    """SaveLeadTool should create the output file and write a valid record."""
    out = tmp_path / "data" / "leads.jsonl"
    tool = SaveLeadTool(output_path=out)

    result = await tool.execute({
        "call_id": "call-001",
        "lead_profile": {"name": "Jane Doe", "age": 55},
    })

    assert result.success is True
    assert out.exists()
    records = _read_jsonl(out)
    assert len(records) == 1
    assert records[0]["call_id"] == "call-001"
    assert records[0]["lead_profile"]["name"] == "Jane Doe"
    assert "timestamp" in records[0]


# ------------------------------------------------------------------
# TransferToAgentTool
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transfer_logs_dry_run(tmp_path: Path) -> None:
    """TransferToAgentTool should record a dry_run transfer event."""
    out = tmp_path / "data" / "transfers.jsonl"
    tool = TransferToAgentTool(output_path=out)

    result = await tool.execute({
        "call_id": "call-002",
        "lead_summary": "Qualified lead, age 60, TX",
        "transfer_reason": "Fully qualified",
    })

    assert result.success is True
    records = _read_jsonl(out)
    assert len(records) == 1
    assert records[0]["status"] == "dry_run"
    assert records[0]["call_id"] == "call-002"
    assert records[0]["lead_summary"] == "Qualified lead, age 60, TX"


# ------------------------------------------------------------------
# ScheduleCallbackTool
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_schedule_callback(tmp_path: Path) -> None:
    """ScheduleCallbackTool should persist callback details."""
    out = tmp_path / "data" / "callbacks.jsonl"
    tool = ScheduleCallbackTool(output_path=out)

    result = await tool.execute({
        "call_id": "call-003",
        "lead_name": "John Smith",
        "callback_time": "2026-05-23T10:00:00Z",
        "phone_number": "+15551234567",
        "notes": "Prefers morning",
    })

    assert result.success is True
    records = _read_jsonl(out)
    assert len(records) == 1
    assert records[0]["lead_name"] == "John Smith"
    assert records[0]["phone_number"] == "+15551234567"
    assert records[0]["callback_time"] == "2026-05-23T10:00:00Z"
    assert records[0]["notes"] == "Prefers morning"


# ------------------------------------------------------------------
# MarkDNCTool
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mark_dnc(tmp_path: Path) -> None:
    """MarkDNCTool should record a DNC entry."""
    out = tmp_path / "data" / "dnc.jsonl"
    tool = MarkDNCTool(output_path=out)

    result = await tool.execute({
        "call_id": "call-004",
        "phone_number": "+15559876543",
        "reason": "Requested by lead",
        "requested_by": "Mary Johnson",
    })

    assert result.success is True
    records = _read_jsonl(out)
    assert len(records) == 1
    assert records[0]["phone_number"] == "+15559876543"
    assert records[0]["reason"] == "Requested by lead"
    assert records[0]["requested_by"] == "Mary Johnson"


# ------------------------------------------------------------------
# ToolRegistry
# ------------------------------------------------------------------

def test_tool_registry_lists_all() -> None:
    """ToolRegistry should pre-register all 5 built-in tools."""
    registry = ToolRegistry()
    tools = registry.list_tools()

    names = {t.name for t in tools}
    expected = {
        "save_lead",
        "transfer_to_agent",
        "schedule_callback",
        "mark_dnc",
        "escalate_to_human",
    }
    assert names == expected
    assert len(tools) == 5


@pytest.mark.asyncio
async def test_tool_registry_execute(tmp_path: Path) -> None:
    """ToolRegistry.execute_tool should dispatch to the correct tool."""
    # Build a registry with tools pointing at tmp_path
    registry = ToolRegistry()
    # Override the save_lead tool with one pointing at tmp_path
    registry._tools["save_lead"] = SaveLeadTool(
        output_path=tmp_path / "leads.jsonl"
    )

    result = await registry.execute_tool("save_lead", {
        "call_id": "call-reg-001",
        "lead_profile": {"name": "Test Lead"},
    })

    assert result.success is True
    assert result.data["call_id"] == "call-reg-001"

    # Verify file was actually written
    records = _read_jsonl(tmp_path / "leads.jsonl")
    assert len(records) == 1


@pytest.mark.asyncio
async def test_tool_registry_execute_unknown() -> None:
    """Executing an unknown tool should return a failure ToolResult."""
    registry = ToolRegistry()
    result = await registry.execute_tool("nonexistent_tool", {})

    assert result.success is False
    assert "not found" in result.message.lower()
