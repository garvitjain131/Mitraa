"""Nemotron client (OpenAI SDK pointed at Nebius Token Factory)."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import openai

from mitraa.config import Settings

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
# Some Nemotron deployments emit tool calls as text instead of structured tool_calls.
_INLINE_TOOLCALL = re.compile(r"<(TOOLCALL|tool_call)>(.*?)</\1>", re.DOTALL | re.IGNORECASE)


class LLMError(Exception):
    pass


@dataclass
class ToolCallRequest:
    id: str
    name: str
    raw_arguments: str
    arguments: dict[str, Any] | None = None
    error: str | None = None  # set when arguments are not a valid JSON object

    def to_openai(self) -> dict[str, Any]:
        return {"id": self.id, "type": "function", "function": {"name": self.name, "arguments": self.raw_arguments}}

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "raw_arguments": self.raw_arguments}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ToolCallRequest:
        return parse_tool_call(data["id"], data["name"], data["raw_arguments"])


@dataclass
class LLMReply:
    content: str = ""
    tool_calls: list[ToolCallRequest] = field(default_factory=list)


class LLMClient(Protocol):
    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMReply: ...


def parse_tool_call(call_id: str, name: str, raw: str | None) -> ToolCallRequest:
    raw = raw if raw and raw.strip() else "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return ToolCallRequest(call_id, name, raw, error=f"arguments are not valid JSON ({exc.msg} at char {exc.pos})")
    if not isinstance(parsed, dict):
        return ToolCallRequest(call_id, name, raw, error="arguments must be a JSON object")
    return ToolCallRequest(call_id, name, raw, arguments=parsed)


def extract_inline_tool_calls(content: str) -> tuple[list[ToolCallRequest], str]:
    calls: list[ToolCallRequest] = []
    for _tag, body in _INLINE_TOOLCALL.findall(content):
        try:
            items = json.loads(body)
        except json.JSONDecodeError:
            continue
        for item in items if isinstance(items, list) else [items]:
            if isinstance(item, dict) and "name" in item:
                args = item.get("arguments", item.get("parameters", {}))
                raw = args if isinstance(args, str) else json.dumps(args)
                calls.append(parse_tool_call(f"call_{uuid.uuid4().hex[:12]}", str(item["name"]), raw))
    if calls:
        content = _INLINE_TOOLCALL.sub("", content).strip()
    return calls, content


class NemotronClient:
    def __init__(self, settings: Settings) -> None:
        missing = [
            name
            for name, value in (
                ("NEBIUS_API_KEY", settings.nebius_api_key),
                ("NEBIUS_BASE_URL", settings.nebius_base_url),
                ("NEMOTRON_MODEL", settings.nemotron_model),
            )
            if not value
        ]
        if missing:
            raise LLMError(f"Missing settings in backend/.env: {', '.join(missing)}")
        self.model = settings.nemotron_model
        # The SDK retries 408/409/429/5xx and connection errors with exponential backoff + jitter.
        self._client = openai.OpenAI(
            base_url=settings.nebius_base_url,
            api_key=settings.nebius_api_key,
            timeout=settings.mitraa_llm_timeout,
            max_retries=settings.mitraa_llm_retries,
        )

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMReply:
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0.2}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        try:
            response = self._client.chat.completions.create(**kwargs)
        except openai.APIStatusError as exc:
            raise LLMError(f"Nebius returned HTTP {exc.status_code}: {_short(exc.message)}") from exc
        except openai.APITimeoutError as exc:
            raise LLMError("The model request timed out.") from exc
        except openai.APIError as exc:
            raise LLMError(f"Model request failed: {_short(str(exc))}") from exc

        if not response.choices:
            raise LLMError("The model returned no choices.")
        message = response.choices[0].message
        content = _THINK_BLOCK.sub("", message.content or "").strip()
        calls = [
            parse_tool_call(tc.id or f"call_{uuid.uuid4().hex[:12]}", tc.function.name, tc.function.arguments)
            for tc in (message.tool_calls or [])
            if getattr(tc, "function", None) is not None
        ]
        if not calls:
            calls, content = extract_inline_tool_calls(content)
        return LLMReply(content=content, tool_calls=calls)

    def plan_json(self, prompt: str) -> str:
        try:
            response = self._client.chat.completions.create(
                model=self.model, messages=[{"role": "user", "content": prompt}],
                temperature=0.3, response_format={"type": "json_object"},
            )
        except openai.APIError as exc:
            raise LLMError(f"Plan generation failed: {_short(str(exc))}") from exc
        return _THINK_BLOCK.sub("", response.choices[0].message.content or "").strip()


def _short(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[:limit] + "…"
