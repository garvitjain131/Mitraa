"""Database/orchestrator ownership and request-tagged desktop results."""
import threading
from types import SimpleNamespace
from mitraa.assistant.worker import AgentWorker


def test_agent_and_database_stay_on_one_worker_and_results_are_tagged(monkeypatch):
    import mitraa.assistant.worker as module
    threads=[]; events=[]; finished=threading.Event(); closed=threading.Event()
    main=threading.get_ident()
    class Session:
        def close(self): threads.append(threading.get_ident()); closed.set()
    def factory():
        threads.append(threading.get_ident()); return Session()
    class Agent:
        def __init__(self,llm,session,settings,on_step):
            threads.append(threading.get_ident()); self.session=session; self.on_step=on_step
        def run(self,conversation_id,text):
            threads.append(threading.get_ident()); self.on_step(SimpleNamespace(tool='test',summary='sample'))
            return SimpleNamespace(conversation_id='persisted')
        def resume(self,approval_id,approved):
            threads.append(threading.get_ident()); return SimpleNamespace(conversation_id='persisted')
    monkeypatch.setattr(module,'NemotronClient',lambda _:object())
    monkeypatch.setattr(module,'default_session_factory',lambda:factory)
    monkeypatch.setattr(module,'Orchestrator',Agent)
    def post(event):
        events.append(event)
        if event[0]=='response' and event[2]==8: finished.set()
    worker=AgentWorker(post)
    worker.send('hello',7); worker.decide('approval',False,8)
    assert finished.wait(3)
    worker.shutdown(); assert closed.wait(3)
    assert len(set(threads))==1 and threads[0]!=main
    assert [(kind,tag) for kind,_,tag in events]==[('step',7),('response',7),('response',8)]
    assert worker.conversation_id=='persisted'
