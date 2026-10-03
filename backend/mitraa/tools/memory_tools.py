from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from mitraa.db.models import Message
from mitraa.memory import store
from mitraa.tools.base import Impact, RiskLevel, Tool, ToolContext, ToolResult


class RememberArgs(BaseModel):
    type: Literal["preference", "goal", "project", "fact", "decision"]
    key: str = Field(min_length=1)
    value: str = Field(min_length=1)


class Remember(Tool):
    name = "remember"
    description = "Save a lasting user fact, preference, goal, project or decision for future sessions. Do not save small talk."
    args_model = RememberArgs
    base_risk = RiskLevel.LOW

    def run(self, args: RememberArgs, ctx: ToolContext) -> ToolResult:
        source_id = ctx.session.scalar(select(Message.id).where(Message.conversation_id == ctx.conversation_id,
                                                                 Message.role == "user").order_by(Message.id.desc()).limit(1))
        row = store.remember(ctx.session, args.type, args.key, args.value, source_id)
        return ToolResult.success(f"Remembered {row.key}: {row.value}", memory=store.as_dict(row))


class ForgetArgs(BaseModel):
    key_or_query: str = Field(min_length=1)


class Forget(Tool):
    name = "forget"
    description = "Forget one memory matching its key or value. If several match, ask the user to identify one."
    args_model = ForgetArgs
    base_risk = RiskLevel.LOW

    def risk(self, args: ForgetArgs, ctx: ToolContext) -> RiskLevel:
        return RiskLevel.HIGH if len(store.matches(ctx.session, args.key_or_query)) > 3 else RiskLevel.LOW

    def impact(self, args: ForgetArgs, ctx: ToolContext) -> Impact:
        return Impact(count=len(store.matches(ctx.session, args.key_or_query)))

    def run(self, args: ForgetArgs, ctx: ToolContext) -> ToolResult:
        matches = store.matches(ctx.session, args.key_or_query)
        if not matches:
            return ToolResult.fail("No matching memory found.")
        if len(matches) != 1:
            return ToolResult.fail("Several memories match; ask which one to forget.", matches=[store.as_dict(m) for m in matches])
        row = matches[0]
        key = row.key
        ctx.session.delete(row)
        ctx.session.flush()
        return ToolResult.success(f"Forgot {key}.")


class ListMemoriesArgs(BaseModel):
    type: Literal["preference", "goal", "project", "fact", "decision"] | None = None


class ListMemories(Tool):
    name = "list_memories"
    description = "List saved user memories, optionally filtered by type."
    args_model = ListMemoriesArgs
    base_risk = RiskLevel.LOW

    def run(self, args: ListMemoriesArgs, ctx: ToolContext) -> ToolResult:
        rows = [store.as_dict(m) for m in store.list_memories(ctx.session, args.type)]
        return ToolResult.success(f"Found {len(rows)} memories.", memories=rows)


ALL_MEMORY_TOOLS = [Remember(), Forget(), ListMemories()]
