"""Tool base class, risk levels, results and execution context."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from mitraa.config import Settings
from mitraa.security.sandbox import Sandbox

if TYPE_CHECKING:
    from mitraa.agent.llm import LLMClient
    from mitraa.db.models import FileAction
    from mitraa.tools.registry import ToolRegistry


class RiskLevel(IntEnum):
    LOW = 0
    MEDIUM = 1
    HIGH = 2
    BLOCKED = 3

    def __str__(self) -> str:
        return self.name


class ToolResult(BaseModel):
    ok: bool
    message: str
    data: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def success(cls, message: str, **data: Any) -> ToolResult:
        return cls(ok=True, message=message, data=data)

    @classmethod
    def fail(cls, message: str, **data: Any) -> ToolResult:
        return cls(ok=False, message=message, data=data)


@dataclass
class ToolContext:
    session: Session
    conversation_id: str
    sandbox: Sandbox
    settings: Settings
    registry: ToolRegistry
    audit_id: str = ""
    approved: bool = False  # True when the user explicitly approved this call
    llm: LLMClient | None = None


@dataclass
class Impact:
    """What a call would touch. Used by the permission engine to adjust risk."""

    paths: list[Path] = field(default_factory=list)  # every resolved path involved
    sources: list[Path] = field(default_factory=list)  # paths that get moved, renamed or deleted
    count: int = 1  # number of files affected
    overwrites: list[Path] = field(default_factory=list)


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    args_model: ClassVar[type[BaseModel]]
    base_risk: ClassVar[RiskLevel]
    mutates: ClassVar[bool] = False

    def impact(self, args: BaseModel, ctx: ToolContext) -> Impact:
        """Resolve the paths a call touches. May raise SandboxViolation."""
        return Impact()

    def risk(self, args: BaseModel, ctx: ToolContext) -> RiskLevel:
        """Argument-sensitive base risk; filesystem effects are evaluated separately."""
        return self.base_risk

    @abstractmethod
    def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult: ...

    def undo(self, record: FileAction, ctx: ToolContext) -> ToolResult:
        return ToolResult.fail(f"{self.name} cannot be undone.")

    def describe(self, args: BaseModel) -> str:
        """One human-readable line for approval prompts."""
        return f"{self.name}({args.model_dump_json()})"

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": _clean_schema(self.args_model.model_json_schema()),
            },
        }


def _clean_schema(node: Any) -> Any:
    """Drop pydantic's auto-generated 'title' metadata to keep schemas compact."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "title" and isinstance(value, str):
                continue
            if key in ("properties", "$defs") and isinstance(value, dict):
                out[key] = {name: _clean_schema(sub) for name, sub in value.items()}
            else:
                out[key] = _clean_schema(value)
        return out
    if isinstance(node, list):
        return [_clean_schema(item) for item in node]
    return node
