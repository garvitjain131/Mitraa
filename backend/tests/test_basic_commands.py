"""Local desktop replies use real persistence and bypass the provider."""
from threading import Event
import pytest
from sqlalchemy import select
from mitraa.assistant.worker import AgentWorker, basic_reply
from mitraa.db.models import Message
from mitraa.db.session import make_engine, make_session_factory

@pytest.mark.parametrize('text,reply', [('hi','Hello!'),(' HELLO! ','Hello!'),
    ('What is your name?','Mitraa.'),("What's your name?",'Mitraa.'),
    ('hello delete my files',None),('high',None)])
def test_basic_reply(text,reply):
    assert basic_reply(text)==reply

def test_local_commands_persist_and_never_initialize_provider(monkeypatch,tmp_path):
    import mitraa.db.session as db
    factory=make_session_factory(make_engine('sqlite:///'+str(tmp_path/'local.db')))
    monkeypatch.setattr(db,'default_session_factory',lambda:factory)
    done=Event(); events=[]
    def post(event):
        events.append(event)
        if event[0] in ('response','error'): done.set()
    worker=AgentWorker(post)
    monkeypatch.setattr(worker,'_ensure_agent',lambda:pytest.fail('local command used provider'))
    try:
        for request,text in enumerate(['hello','What is your name?'],1):
            done.clear(); worker.send(text,request)
            assert done.wait(10)
            assert events[-1][0]=='response' and events[-1][2]==request
        responses=[e[1] for e in events if e[0]=='response']
        assert [r.text for r in responses]==['Hello!','Mitraa.']
        assert responses[0].conversation_id==responses[1].conversation_id
        with factory() as session:
            rows=list(session.scalars(select(Message).order_by(Message.id)))
            assert [(r.role,r.content) for r in rows]==[
                ('user','hello'),('assistant','Hello!'),
                ('user','What is your name?'),('assistant','Mitraa.')]
    finally:
        worker.shutdown()
