from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mitraa import main
from mitraa.config import get_settings
from mitraa.db.session import get_session
from tests.fakes import FakeLLM, act, call, say


@pytest.fixture
def client_with(session_factory, settings, trash):
    def _make(llm: FakeLLM) -> TestClient:
        def _session():
            s = session_factory()
            try:
                yield s
            finally:
                s.close()

        main.app.dependency_overrides[get_session] = _session
        main.app.dependency_overrides[get_settings] = lambda: settings
        main.app.dependency_overrides[main.get_llm] = lambda: llm
        return TestClient(main.app)

    yield _make
    main.app.dependency_overrides.clear()


def test_tools_endpoint(client_with):
    tools = client_with(FakeLLM()).get("/tools").json()
    by_name = {t["name"]: t for t in tools}
    assert by_name["search_files"]["risk"] == "LOW"
    assert by_name["move_file"]["risk"] == "MEDIUM"
    assert by_name["delete_file"]["risk"] == "HIGH"
    assert "source" in by_name["move_file"]["parameters"]["properties"]


def test_chat_approve_and_audit_flow(client_with, roots):
    target = roots["Documents"] / "old_notes.txt"
    target.write_text("x")
    client = client_with(FakeLLM([act(call("delete_file", {"path": str(target)})), say("Moved it to the Recycle Bin.")]))

    first = client.post("/chat", json={"message": "Delete old_notes.txt"}).json()
    assert first["type"] == "approval_required"
    approval_id = first["pending_approval"]["approval_id"]
    assert target.exists()

    final = client.post("/chat/approve", json={"approval_id": approval_id, "approved": True}).json()
    assert final["type"] == "message" and not target.exists()
    assert client.post("/chat/approve", json={"approval_id": approval_id, "approved": True}).status_code == 409

    entries = client.get("/audit", params={"limit": 10}).json()
    assert entries[0]["tool"] == "delete_file" and entries[0]["status"] == "success"

    conv = client.get(f"/conversations/{first['conversation_id']}").json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant", "tool", "assistant"]
    assert client.get("/conversations").json()[0]["id"] == first["conversation_id"]


def test_unknown_conversation_is_404(client_with):
    response = client_with(FakeLLM()).post("/chat", json={"conversation_id": "nope", "message": "hi"})
    assert response.status_code == 404


def test_missing_llm_config_is_503(client_with, settings, monkeypatch):
    client = client_with(FakeLLM())
    del main.app.dependency_overrides[main.get_llm]  # use the real Nemotron factory
    monkeypatch.setattr(main, "get_settings", lambda: settings.model_copy(update={"nebius_api_key": ""}))
    main._nemotron.cache_clear()
    try:
        response = client.post("/chat", json={"message": "hi"})
    finally:
        main._nemotron.cache_clear()
    assert response.status_code == 503 and "NEBIUS_API_KEY" in response.json()["detail"]
