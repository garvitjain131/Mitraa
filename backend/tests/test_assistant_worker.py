"""Database/orchestrator ownership and request-tagged desktop results."""
import threading
from types import SimpleNamespace
from sqlalchemy.orm import Session, sessionmaker
from mitraa.assistant.worker import AgentWorker
from mitraa.db.models import Message
from mitraa.db.session import make_engine


def test_agent_and_database_stay_on_one_worker_and_results_are_tagged(monkeypatch,tmp_path):
    import mitraa.agent.llm as llm_module
    import mitraa.agent.orchestrator as agent_module
    import mitraa.db.session as session_module
    threads=[]; events=[]; finished=threading.Event(); closed=threading.Event()
    main=threading.get_ident()
    class TrackedSession(Session):
        def __init__(self,*args,**kwargs):
            threads.append(threading.get_ident()); super().__init__(*args,**kwargs)
        def close(self):
            threads.append(threading.get_ident()); super().close(); closed.set()
    factory=sessionmaker(bind=make_engine('sqlite:///'+str(tmp_path/'worker.db')),
                         class_=TrackedSession,expire_on_commit=False)
    class Agent:
        def __init__(self,llm,session,settings,on_step):
            threads.append(threading.get_ident()); self.session=session; self.on_step=on_step
        def run(self,conversation_id,text,*,save_user=True):
            assert not save_user
            threads.append(threading.get_ident()); self.cid=conversation_id
            self.on_step(SimpleNamespace(tool='test',summary='sample'))
            self.session.add(Message(conversation_id=conversation_id,role='assistant',content='Prepared.'))
            self.session.commit()
            return SimpleNamespace(type='message',text='Prepared.',conversation_id=conversation_id,pending_approval=None)
        def resume(self,approval_id,approved):
            threads.append(threading.get_ident())
            return SimpleNamespace(type='message',text='Decision recorded.',conversation_id=self.cid,pending_approval=None)
    monkeypatch.setattr(llm_module,'NemotronClient',lambda _:object())
    monkeypatch.setattr(session_module,'default_session_factory',lambda:factory)
    monkeypatch.setattr(agent_module,'Orchestrator',Agent)
    def post(event):
        events.append(event)
        if event[0]=='response' and event[2]==8: finished.set()
    worker=AgentWorker(post)
    try:
        worker.send('Please plan my day',7); worker.decide('approval',False,8)
        assert finished.wait(10)
        assert [(kind,tag) for kind,_,tag in events if kind in {'step','response','error'}]==[
            ('step',7),('response',7),('response',8)]
        assert worker.conversation_id is not None
    finally:
        closed.clear(); worker.shutdown(); assert closed.wait(5)
    assert len(set(threads))==1 and threads[0]!=main
