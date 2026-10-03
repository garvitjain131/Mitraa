"""Audit log: every tool call the model makes is recorded in tool_calls."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import literal_column, select
from sqlalchemy.orm import Session

from mitraa.db.models import ToolCall

STATUSES = ("success", "error", "denied", "pending", "blocked")


def start(
    session: Session,
    conversation_id: str,
    tool: str,
    args: dict[str, Any] | str,
    risk: str,
    llm_call_id: str | None = None,
    status: str = "pending",
) -> ToolCall:
    row = ToolCall(
        conversation_id=conversation_id,
        llm_call_id=llm_call_id,
        tool=tool,
        args_json=args if isinstance(args, str) else json.dumps(args, ensure_ascii=False),
        risk=risk,
        status=status,
    )
    session.add(row)
    session.flush()
    return row


def finish(row: ToolCall, status: str, summary: str, duration_ms: int = 0) -> None:
    assert status in STATUSES, status
    row.status = status
    row.result_summary = summary[:1000]
    row.duration_ms = duration_ms


def recent(session: Session, limit: int = 50, conversation_id: str | None = None) -> list[ToolCall]:
    query = select(ToolCall).order_by(ToolCall.created_at.desc(), literal_column("rowid").desc()).limit(limit)
    if conversation_id:
        query = query.where(ToolCall.conversation_id == conversation_id)
    return list(session.scalars(query))


def to_dict(row: ToolCall) -> dict[str, Any]:
    try:
        args = json.loads(row.args_json)
    except json.JSONDecodeError:
        args = row.args_json
    return {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "tool": row.tool,
        "args": args,
        "risk": row.risk,
        "status": row.status,
        "result_summary": row.result_summary,
        "duration_ms": row.duration_ms,
        "timestamp": row.created_at.isoformat(),
    }
