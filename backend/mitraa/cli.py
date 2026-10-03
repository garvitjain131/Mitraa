"""Interactive terminal chat for testing: python -m mitraa.cli"""

from __future__ import annotations

import sys

from mitraa import audit
from mitraa.agent.llm import LLMError, NemotronClient
from mitraa.agent.orchestrator import AgentResponse, Orchestrator, Step
from mitraa.config import get_settings
from mitraa.db.session import default_session_factory

_ICONS = {"success": "✓", "error": "✗", "blocked": "⛔", "denied": "✗", "pending": "⏳"}

HELP = """Commands:
  /new     start a new conversation
  /audit   show the last 10 audit entries
  /quit    exit"""


def _print_step(step: Step) -> None:
    icon = _ICONS.get(step.status, "•")
    print(f"  {icon} {step.tool} [{step.risk}] {step.summary}")


def _ask_approval(response: AgentResponse) -> bool:
    info = response.pending_approval
    assert info is not None
    print(f"\n  ⚠  Mitraa wants to: {info.description}")
    print(f"     Risk: {info.risk}")
    for reason in info.reasons:
        print(f"     - {reason}")
    try:
        answer = input("  Allow? [y/N] ").strip().lower()
    except EOFError:
        answer = ""
    return answer in ("y", "yes")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    settings = get_settings()
    try:
        llm = NemotronClient(settings)
    except LLMError as exc:
        print(f"Cannot start: {exc}")
        return 1

    session = default_session_factory()()
    agent = Orchestrator(llm, session, settings, on_step=_print_step)
    conversation_id: str | None = None
    print(f"Mitraa (model: {settings.nemotron_model})")
    print(f"Allowed folders: {agent.sandbox.describe_roots()}")
    print("Type /help for commands.\n")

    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in ("/quit", "/exit"):
            break
        if text == "/help":
            print(HELP)
            continue
        if text == "/new":
            conversation_id = None
            print("(new conversation)")
            continue
        if text == "/audit":
            for row in reversed(audit.recent(session, 10)):
                print(f"  {row.created_at:%H:%M:%S} {row.status:8} {row.risk:7} {row.tool} — {row.result_summary[:80]}")
            continue

        response = agent.run(conversation_id, text)
        conversation_id = response.conversation_id
        while response.type == "approval_required" and response.pending_approval:
            approved = _ask_approval(response)
            response = agent.resume(response.pending_approval.approval_id, approved)
        print(f"\nmitraa> {response.text}\n")

    session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
