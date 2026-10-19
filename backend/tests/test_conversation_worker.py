from queue import Queue
from sqlalchemy import select
from mitraa.assistant.worker import AgentWorker
from mitraa.db.models import Conversation,Message
from mitraa.db.session import make_engine,make_session_factory

def setup_worker(monkeypatch,tmp_path):
    import mitraa.db.session as db
    factory=make_session_factory(make_engine('sqlite:///'+str(tmp_path/'history.db')))
    monkeypatch.setattr(db,'default_session_factory',lambda:factory)
    events=Queue(); worker=AgentWorker(events.put)
    return worker,events,factory

def test_history_paging_filters_current_conversation_and_internal_messages(monkeypatch,tmp_path):
    worker,events,factory=setup_worker(monkeypatch,tmp_path)
    with factory() as session:
        a=Conversation(title='current'); b=Conversation(title='other'); session.add_all([a,b]); session.flush()
        for index in range(90): session.add(Message(conversation_id=a.id,role='user' if index%2 else 'assistant',content=str(index)))
        session.add_all([Message(conversation_id=b.id,role='user',content='private other'),
                         Message(conversation_id=a.id,role='system',content='system prompt'),
                         Message(conversation_id=a.id,role='tool',content='raw tool output'),
                         Message(conversation_id=a.id,role='assistant',content='tool arguments',tool_calls_json='[]')])
        session.commit(); worker.conversation_id=a.id
    try:
        worker.history(3); kind,page,token=events.get(timeout=5)
        assert kind=='history' and token==3 and len(page['messages'])==40 and page['has_more']
        assert [r['content'] for r in page['messages']]==[str(i) for i in range(50,90)]
        assert all(r['created_at'].endswith('+00:00') for r in page['messages'])
        worker.history(3,page['messages'][0]['id']); _,older,_=events.get(timeout=5)
        assert [r['content'] for r in older['messages']]==[str(i) for i in range(10,50)]
    finally: worker.shutdown()

def test_failure_persists_user_without_fake_assistant(monkeypatch,tmp_path):
    worker,events,factory=setup_worker(monkeypatch,tmp_path)
    def unavailable(): raise RuntimeError('Provider unavailable')
    monkeypatch.setattr(worker,'_ensure_agent',unavailable)
    try:
        worker.send('real user message',7)
        assert events.get(timeout=5)[0]=='message_saved'
        assert events.get(timeout=5)[0]=='error'
        with factory() as session:
            rows=list(session.scalars(select(Message)))
            assert [(r.role,r.content) for r in rows]==[('user','real user message')]
    finally: worker.shutdown()

def test_worker_and_orchestrator_save_each_message_once(monkeypatch,tmp_path,settings):
    from mitraa.agent.orchestrator import Orchestrator
    from tests.fakes import FakeLLM,say
    worker,events,factory=setup_worker(monkeypatch,tmp_path)
    def agent():
        if worker._agent is None:
            worker._agent=Orchestrator(FakeLLM([say('Actual assistant reply')]),factory(),settings)
        return worker._agent
    monkeypatch.setattr(worker,'_ensure_agent',agent)
    try:
        worker.send('Ask a real question',1)
        assert events.get(timeout=5)[0]=='message_saved'
        assert events.get(timeout=5)[0]=='message_saved'
        assert events.get(timeout=5)[0]=='response'
        with factory() as session:
            rows=list(session.scalars(select(Message).order_by(Message.id)))
            assert [(r.role,r.content) for r in rows]==[('user','Ask a real question'),('assistant','Actual assistant reply')]
    finally: worker.shutdown()
