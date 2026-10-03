from __future__ import annotations

import json
from typing import Any

from mitraa.agent.llm import LLMReply, ToolCallRequest, parse_tool_call


def call(name: str, args: dict[str, Any] | str | None = None, call_id: str | None = None) -> ToolCallRequest:
    raw = args if isinstance(args, str) else json.dumps(args or {})
    call.counter += 1
    return parse_tool_call(call_id or f"call_{call.counter}", name, raw)


call.counter = 0


def say(text: str) -> LLMReply:
    return LLMReply(content=text)


def act(*calls: ToolCallRequest, text: str = "") -> LLMReply:
    return LLMReply(content=text, tool_calls=list(calls))


class FakeLLM:
    """Returns scripted replies in order. Records every request it receives."""

    def __init__(self, replies: list[LLMReply] | None = None, repeat: LLMReply | None = None) -> None:
        self.replies = list(replies or [])
        self.repeat = repeat
        self.requests: list[list[dict[str, Any]]] = []

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMReply:
        self.requests.append([dict(m) for m in messages])
        if self.replies:
            return self.replies.pop(0)
        if self.repeat is not None:
            fresh = [parse_tool_call(f"{c.id}_{len(self.requests)}", c.name, c.raw_arguments) for c in self.repeat.tool_calls]
            return LLMReply(content=self.repeat.content, tool_calls=fresh)
        return say("(no more scripted replies)")

    def last_tool_messages(self) -> list[dict[str, Any]]:
        return [m for m in self.requests[-1] if m["role"] == "tool"]


def assert_valid_history(messages: list[dict[str, Any]]) -> None:
    """Every assistant tool_call must be answered by exactly one tool message before the next assistant/user turn."""
    open_ids: set[str] = set()
    for message in messages:
        if message["role"] == "assistant":
            assert not open_ids, f"unanswered tool calls: {open_ids}"
            open_ids = {c["id"] for c in message.get("tool_calls") or []}
        elif message["role"] == "tool":
            assert message["tool_call_id"] in open_ids, message
            open_ids.discard(message["tool_call_id"])
        elif message["role"] == "user":
            assert not open_ids, f"unanswered tool calls: {open_ids}"
    assert not open_ids, f"unanswered tool calls: {open_ids}"
