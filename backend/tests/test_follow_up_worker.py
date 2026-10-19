from queue import Queue
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from mitraa.assistant.follow_up import FollowUpWorker
from mitraa.db.models import Responsibility


def test_invalid_atomic_capture_rolls_back_and_next_job_recovers(session_factory):
    events=Queue(); worker=FollowUpWorker(lambda *event:events.put(event),factory=session_factory)
    try:
        worker.submit('create',data={'title':'Must roll back'},reminder={'message':'Invalid time',
            'due_at':(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()})
        op,payload,error=events.get(timeout=5)
        assert op=='create' and payload is None and 'future' in error
        with session_factory() as session: assert list(session.scalars(select(Responsibility)))==[]
        worker.submit('create',data={'title':'Real responsibility'})
        _,payload,error=events.get(timeout=5)
        assert not error and payload['responsibilities'][0]['title']=='Real responsibility'
    finally: worker.shutdown()


def test_separate_worker_recovers_saved_responsibilities(session_factory):
    events=Queue(); worker=FollowUpWorker(lambda *event:events.put(event),timezone='Asia/Kolkata',factory=session_factory)
    worker.submit('create',data={'title':'Survive restart','next_action':'Review requirements'})
    assert not events.get(timeout=5)[2]; worker.shutdown()
    restarted=FollowUpWorker(lambda *event:events.put(event),factory=session_factory)
    try:
        restarted.submit('snapshot'); _,payload,error=events.get(timeout=5)
        assert not error and payload['responsibilities'][0]['next_action']=='Review requirements'
        assert payload['preferences']['timezone']=='Asia/Kolkata'
    finally: restarted.shutdown()
