from __future__ import annotations

from types import SimpleNamespace

import pytest

from mitraa.agent import llm as llm_module
from mitraa.agent.llm import LLMError, NemotronClient, extract_inline_tool_calls, parse_tool_call
from mitraa.config import Settings


def test_parse_valid_and_invalid_arguments():
    ok = parse_tool_call("1", "move_file", '{"source": "a", "destination": "b"}')
    assert ok.arguments == {"source": "a", "destination": "b"} and ok.error is None
    bad = parse_tool_call("2", "move_file", '{"source": ')
    assert bad.arguments is None and "not valid JSON" in bad.error
    not_obj = parse_tool_call("3", "move_file", "[1, 2]")
    assert "JSON object" in not_obj.error
    empty = parse_tool_call("4", "undo_last_action", "")
    assert empty.arguments == {}


def test_inline_toolcall_extraction():
    content = 'Sure.<TOOLCALL>[{"name": "search_files", "arguments": {"query": "resume"}}]</TOOLCALL>'
    calls, rest = extract_inline_tool_calls(content)
    assert [c.name for c in calls] == ["search_files"] and calls[0].arguments == {"query": "resume"}
    assert rest == "Sure."


def test_missing_config_raises():
    with pytest.raises(LLMError, match="NEBIUS_API_KEY"):
        NemotronClient(Settings(_env_file=None, nebius_api_key="", nebius_base_url="http://x", nemotron_model="m"))


def test_client_parses_response_and_strips_think(monkeypatch):
    settings = Settings(_env_file=None, nebius_api_key="k", nebius_base_url="http://localhost:1/v1", nemotron_model="m")
    client = NemotronClient(settings)
    tool_call = SimpleNamespace(id=None, function=SimpleNamespace(name="list_folder", arguments='{"path": "Documents"}'))
    message = SimpleNamespace(content="<think>planning</think>Looking.", tool_calls=[tool_call])
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)
    reply = client.chat([{"role": "user", "content": "hi"}], [{"type": "function", "function": {"name": "x"}}])
    assert reply.content == "Looking."
    assert reply.tool_calls[0].name == "list_folder" and reply.tool_calls[0].id.startswith("call_")
    assert captured["temperature"] == 0.2 and captured["tool_choice"] == "auto"


def test_client_wraps_api_errors(monkeypatch):
    settings = Settings(_env_file=None, nebius_api_key="k", nebius_base_url="http://localhost:1/v1", nemotron_model="m",
                        mitraa_llm_retries=0, mitraa_llm_timeout=2)
    client = NemotronClient(settings)

    def boom(**kwargs):
        raise llm_module.openai.APIConnectionError(request=None)

    monkeypatch.setattr(client._client.chat.completions, "create", boom)
    with pytest.raises(LLMError):
        client.chat([], [])
