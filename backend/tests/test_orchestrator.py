"""Orchestrator tests with a scripted FakeLLM (covers acceptance tests 1-7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import literal_column, select

from mitraa.agent.llm import LLMError
from mitraa.agent.orchestrator import ApprovalError, Orchestrator
from mitraa.db.models import Message, PendingApproval, ToolCall
from tests.fakes import FakeLLM, act, assert_valid_history, call, say


def _touch(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def make_agent(session, settings, sandbox, registry):
    def _make(llm, **overrides):
        s = settings.model_copy(update=overrides) if overrides else settings
        return Orchestrator(llm, session, s, registry=registry, sandbox=sandbox)

    return _make


def _audit(session) -> list[ToolCall]:
    return list(session.scalars(select(ToolCall).order_by(ToolCall.created_at, literal_column("rowid"))))


# 1. "Move resume.pdf from Documents to Downloads"
def test_move_end_to_end(make_agent, roots, session):
    src = _touch(roots["Documents"] / "resume.pdf")
    llm = FakeLLM([
        act(call("search_files", {"query": "resume.pdf", "folder": "Documents"})),
        act(call("move_file", {"source": str(src), "destination": "Downloads"})),
        say(f"Moved resume.pdf to {roots['Downloads']}. Say 'undo' to revert."),
    ])
    response = make_agent(llm).run(None, "Move resume.pdf from Documents to Downloads")

    assert response.type == "message"
    assert (roots["Downloads"] / "resume.pdf").exists() and not src.exists()
    assert [s.tool for s in response.steps] == ["search_files", "move_file"]
    assert all(s.status == "success" for s in response.steps)
    move_result = json.loads(llm.last_tool_messages()[-1]["content"])
    assert move_result["data"]["destination"] == str(roots["Downloads"] / "resume.pdf")
    assert_valid_history(llm.requests[-1])


def test_system_prompt_names_agent_and_roots(make_agent, roots):
    llm = FakeLLM([say("hi")])
    make_agent(llm).run(None, "hello")
    system = llm.requests[0][0]
    assert system["role"] == "system" and "You are Mitraa" in system["content"]
    assert str(roots["Documents"]) in system["content"]


# 2. "Move resume" with two matches: the model sees both and asks
def test_ambiguous_search_returns_both_matches(make_agent, roots):
    _touch(roots["Documents"] / "resume.pdf")
    _touch(roots["Desktop"] / "resume_old.docx")
    llm = FakeLLM([
        act(call("search_files", {"query": "resume"})),
        say("I found two: 1. Documents/resume.pdf 2. Desktop/resume_old.docx. Which one?"),
    ])
    response = make_agent(llm).run(None, "Move resume")
    results = json.loads(llm.last_tool_messages()[-1]["content"])["data"]["results"]
    assert len(results) == 2
    assert response.type == "message" and "Which" in response.text
    assert (roots["Documents"] / "resume.pdf").exists()


# 3. Delete pauses for approval, then goes to the Recycle Bin
def test_delete_requires_approval_then_trashes(make_agent, roots, session, trash):
    target = _touch(roots["Documents"] / "old_notes.txt")
    llm = FakeLLM([
        act(call("delete_file", {"path": str(target)})),
        say("Moved old_notes.txt to the Recycle Bin."),
    ])
    agent = make_agent(llm)
    response = agent.run(None, "Delete old_notes.txt")

    assert response.type == "approval_required"
    assert response.pending_approval and response.pending_approval.risk == "HIGH"
    assert target.exists() and trash == []
    assert _audit(session)[-1].status == "pending"
    assert len(llm.requests) == 1

    final = agent.resume(response.pending_approval.approval_id, approved=True)
    assert final.type == "message"
    assert not target.exists() and trash == [target]
    assert _audit(session)[-1].status == "success"
    assert_valid_history(llm.requests[-1])


def test_denied_action_is_not_executed(make_agent, roots, session, trash):
    target = _touch(roots["Documents"] / "old_notes.txt")
    llm = FakeLLM([act(call("delete_file", {"path": str(target)})), say("OK, I left it alone.")])
    agent = make_agent(llm)
    response = agent.run(None, "Delete old_notes.txt")
    final = agent.resume(response.pending_approval.approval_id, approved=False)

    assert target.exists() and trash == []
    assert _audit(session)[-1].status == "denied"
    assert "denied" in llm.last_tool_messages()[-1]["content"]
    assert final.text == "OK, I left it alone."


def test_approval_cannot_be_reused(make_agent, roots, trash):
    target = _touch(roots["Documents"] / "a.txt")
    llm = FakeLLM([act(call("delete_file", {"path": str(target)})), say("done")])
    agent = make_agent(llm)
    approval_id = agent.run(None, "delete a.txt").pending_approval.approval_id
    agent.resume(approval_id, approved=True)
    with pytest.raises(ApprovalError):
        agent.resume(approval_id, approved=True)
    with pytest.raises(ApprovalError):
        agent.resume("does-not-exist", approved=True)


def test_new_message_expires_pending_approval(make_agent, roots, session, trash):
    target = _touch(roots["Documents"] / "a.txt")
    llm = FakeLLM([
        act(call("delete_file", {"path": str(target)}), call("list_folder", {"path": "Documents"})),
        say("Never mind then."),
    ])
    agent = make_agent(llm)
    first = agent.run(None, "delete a.txt and list documents")
    agent.run(first.conversation_id, "actually, don't")

    assert target.exists()
    pending = session.scalars(select(PendingApproval)).one()
    assert pending.status == "expired"
    assert_valid_history(llm.requests[-1])


def test_remaining_calls_run_after_approval(make_agent, roots, trash):
    target = _touch(roots["Documents"] / "a.txt")
    _touch(roots["Documents"] / "b.txt")
    llm = FakeLLM([
        act(
            call("delete_file", {"path": str(target)}),
            call("move_file", {"source": "Documents/b.txt", "destination": "Desktop"}),
        ),
        say("Deleted a.txt and moved b.txt."),
    ])
    agent = make_agent(llm)
    paused = agent.run(None, "delete a and move b")
    assert (roots["Documents"] / "b.txt").exists()  # nothing after the paused call has run yet
    final = agent.resume(paused.pending_approval.approval_id, approved=True)
    assert [s.tool for s in final.steps] == ["delete_file", "move_file"]
    assert (roots["Desktop"] / "b.txt").exists()
    assert_valid_history(llm.requests[-1])


def test_overwrite_requires_approval(make_agent, roots):
    _touch(roots["Documents"] / "a.txt", "new")
    _touch(roots["Downloads"] / "a.txt", "old")
    llm = FakeLLM([act(call("move_file", {"source": "Documents/a.txt", "destination": "Downloads"}))])
    response = make_agent(llm).run(None, "move a.txt to downloads")
    assert response.type == "approval_required"
    assert any("replace" in r for r in response.pending_approval.reasons)


def test_bulk_move_requires_approval(make_agent, roots):
    sources = [str(_touch(roots["Documents"] / f"f{i}.txt")) for i in range(6)]
    llm = FakeLLM([act(call("move_files", {"sources": sources, "destination_folder": "Desktop"}))])
    response = make_agent(llm).run(None, "move all f files to desktop")
    assert response.type == "approval_required"
    assert all(Path(s).exists() for s in sources)


# 4. "Undo that" restores the last move
def test_undo_in_follow_up_message(make_agent, roots):
    _touch(roots["Documents"] / "resume.pdf")
    llm = FakeLLM([
        act(call("move_file", {"source": "Documents/resume.pdf", "destination": "Downloads"})),
        say("Moved."),
        act(call("undo_last_action", {})),
        say("Put it back."),
    ])
    agent = make_agent(llm)
    first = agent.run(None, "move resume.pdf to downloads")
    assert (roots["Downloads"] / "resume.pdf").exists()
    agent.run(first.conversation_id, "undo that")
    assert (roots["Documents"] / "resume.pdf").exists()
    assert not (roots["Downloads"] / "resume.pdf").exists()


# 5. Sandbox escapes are refused
@pytest.mark.parametrize("path", ["C:\\Windows\\System32\\drivers\\etc\\hosts", "..\\..\\", "Documents/../../x"])
def test_escape_is_blocked_and_reported(make_agent, session, path):
    llm = FakeLLM([act(call("list_folder", {"path": path})), say("That folder is outside my permitted locations.")])
    response = make_agent(llm).run(None, f"list {path}")
    assert response.steps[0].status == "blocked"
    assert "Blocked" in llm.last_tool_messages()[-1]["content"]
    assert _audit(session)[-1].status == "blocked"


def test_moving_system_file_is_blocked(make_agent, roots, session):
    llm = FakeLLM([
        act(call("move_file", {"source": "C:\\Windows\\System32\\notepad.exe", "destination": "Desktop"})),
        say("I can't touch system folders."),
    ])
    response = make_agent(llm).run(None, "move notepad to desktop")
    assert response.steps[0].status == "blocked"
    assert not (roots["Desktop"] / "notepad.exe").exists()


# 6. Every action appears in the audit log
def test_every_call_is_audited(make_agent, roots, session):
    _touch(roots["Documents"] / "a.txt")
    llm = FakeLLM([
        act(call("search_files", {"query": "a"})),
        act(call("no_such_tool", {})),
        act(call("read_file", {"path": "Documents/a.txt"})),
        say("done"),
    ])
    make_agent(llm).run(None, "read a")
    rows = _audit(session)
    assert [r.tool for r in rows] == ["search_files", "no_such_tool", "read_file"]
    assert [r.status for r in rows] == ["success", "error", "success"]
    assert all(r.duration_ms >= 0 for r in rows)


# 7. MAX_STEPS stops runaway loops
def test_max_steps_enforced(make_agent):
    llm = FakeLLM(repeat=act(call("list_folder", {"path": "Documents"})))
    response = make_agent(llm, mitraa_max_steps=3).run(None, "loop forever")
    assert len(llm.requests) == 3
    assert response.type == "message" and "stopped after 3 steps" in response.text


def test_max_steps_counts_across_approval(make_agent, roots, trash):
    target = _touch(roots["Documents"] / "a.txt")
    llm = FakeLLM(
        [act(call("list_folder", {"path": "Documents"})), act(call("delete_file", {"path": str(target)}))],
        repeat=act(call("list_folder", {"path": "Documents"})),
    )
    agent = make_agent(llm, mitraa_max_steps=3)
    paused = agent.run(None, "go")
    final = agent.resume(paused.pending_approval.approval_id, approved=True)
    assert len(llm.requests) == 3 and "stopped after 3 steps" in final.text


# Malformed tool-call arguments
def test_malformed_args_are_repaired_once(make_agent, roots):
    _touch(roots["Documents"] / "resume.pdf")
    llm = FakeLLM([
        act(call("move_file", '{"source": "Documents/resume.pdf", "destination": ')),
        act(call("move_file", {"source": "Documents/resume.pdf", "destination": "Downloads"})),
        say("Moved."),
    ])
    response = make_agent(llm).run(None, "move resume")
    assert (roots["Downloads"] / "resume.pdf").exists()
    first_error = [m for m in llm.requests[1] if m["role"] == "tool"][-1]["content"]
    assert "Invalid arguments" in first_error and "Fix and retry" in first_error
    assert response.text == "Moved."


def test_schema_violation_is_reported(make_agent):
    llm = FakeLLM([act(call("move_file", {"source": "Documents/a.txt"})), say("ok")])
    make_agent(llm).run(None, "move a")
    content = llm.last_tool_messages()[-1]["content"]
    assert "Invalid arguments" in content and "destination" in content


def test_repeated_malformed_args_stop_the_run(make_agent, session):
    llm = FakeLLM(repeat=act(call("move_file", "{not json")))
    response = make_agent(llm).run(None, "move it")
    assert len(llm.requests) == 2
    assert "couldn't put together a valid request" in response.text
    messages = session.scalars(select(Message).order_by(Message.id)).all()
    assert messages[-1].role == "assistant"


def test_relative_path_is_fed_back_as_error(make_agent, session):
    llm = FakeLLM([act(call("read_file", {"path": "resume.pdf"})), say("Let me search for it.")])
    response = make_agent(llm).run(None, "read resume")
    assert response.steps[0].status == "error"
    assert "search_files" in llm.last_tool_messages()[-1]["content"]


def test_llm_failure_returns_friendly_message(make_agent):
    class Broken:
        def chat(self, messages, tools):
            raise LLMError("Nebius returned HTTP 503")

    response = make_agent(Broken()).run(None, "hello")
    assert response.type == "message" and "503" in response.text


def test_messages_are_persisted(make_agent, roots, session):
    llm = FakeLLM([act(call("list_folder", {"path": "Documents"})), say("Empty.")])
    response = make_agent(llm).run(None, "what's in documents?")
    roles = [m.role for m in session.scalars(select(Message).where(Message.conversation_id == response.conversation_id).order_by(Message.id))]
    assert roles == ["user", "assistant", "tool", "assistant"]


def test_history_survives_new_orchestrator(make_agent, roots):
    llm = FakeLLM([say("first"), say("second")])
    first = make_agent(llm).run(None, "one")
    make_agent(llm).run(first.conversation_id, "two")
    contents = [m["content"] for m in llm.requests[-1] if m["role"] in ("user", "assistant")]
    assert contents == ["one", "first", "two"]
