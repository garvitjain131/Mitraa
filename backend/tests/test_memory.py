from mitraa.agent.orchestrator import Orchestrator
from mitraa.db.models import Conversation
from mitraa.memory.retrieval import top_k
from mitraa.memory.store import matches, remember
from mitraa.security.permissions import evaluate
from mitraa.tools.memory_tools import Forget, ForgetArgs
from mitraa.tools.base import RiskLevel
from tests.fakes import FakeLLM, say


def test_upsert_and_retrieval(session, session_factory):
    first = remember(session, "project", "current_project", "NOVA hackathon")
    remember(session, "preference", "language", "Hinglish")
    again = remember(session, "project", "current_project", "Mitraa hackathon")
    session.commit()
    assert first.id == again.id
    assert top_k(session, "What am I building?", 1)[0].value == "Mitraa hackathon"
    assert len(matches(session, "hackathon")) == 1
    session.commit()
    with session_factory() as reopened:
        assert top_k(reopened, "my project", 1)[0].key == "current_project"


def test_orchestrator_injects_saved_memory(session, settings, sandbox):
    remember(session, "project", "current_project", "NOVA")
    session.commit()
    llm = FakeLLM([say("You are building NOVA.")])
    result = Orchestrator(llm, session, settings, sandbox=sandbox).run(None, "What am I building?")
    assert result.text == "You are building NOVA."
    assert "[project] current_project: NOVA" in llm.requests[0][0]["content"]


def test_forget_more_than_three_matches_is_high(make_ctx):
    ctx = make_ctx()
    for i in range(4):
        remember(ctx.session, "fact", f"hackathon_{i}", f"detail {i}")
    assessment = evaluate(Forget(), ForgetArgs(key_or_query="hackathon"), ctx)
    assert assessment.level == RiskLevel.HIGH
