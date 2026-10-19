from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from mitraa.main import app
from mitraa.db.session import get_session


@pytest.fixture
def client(session_factory):
    def provide():
        with session_factory() as session: yield session
    app.dependency_overrides[get_session]=provide
    try: yield TestClient(app)
    finally: app.dependency_overrides.clear()


def test_local_lifecycle_works_without_provider(client):
    first=client.post('/responsibilities',json={'title':'Interview preparation','timezone':'Asia/Kolkata'})
    assert first.status_code==201
    item=first.json(); rid=item['id']
    assert client.get('/responsibilities').json()[0]['id']==rid
    assert client.patch('/responsibilities/'+rid,json={'expected_version':1,'status':'active','next_action':'Read role description'}).status_code==200
    assert client.patch('/responsibilities/'+rid,json={'expected_version':1,'title':'Stale edit'}).status_code==409
    at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    reminder=client.post('/reminders',json={'responsibility_id':rid,'message':'Review role','due_at':at})
    assert reminder.status_code==201
    mid=reminder.json()['id']
    assert client.post('/reminders/'+mid+'/snooze',json={'due_at':at}).status_code==200
    assert client.post('/reminders/'+mid+'/acknowledge').status_code==200
    assert client.get('/responsibilities/'+rid).json()['status']=='active'
    assert client.patch('/responsibilities/'+rid,json={'expected_version':2,'status':'completed'}).status_code==422
    assert client.patch('/responsibilities/'+rid,json={'expected_version':2,'status':'completed','completion_note':'I completed the interview'}).status_code==200
    assert len(client.get('/responsibilities/'+rid+'/history').json())>=4
    assert client.delete('/responsibilities/'+rid).status_code==409
    assert client.delete('/responsibilities/'+rid,params={'confirmed':True,'expected_version':3}).status_code==200
    assert client.get('/responsibilities/'+rid).status_code==404


@pytest.mark.parametrize('data',[{'title':'  '},{'title':'valid','deadline':'2026-10-21T10:00:00'},
    {'title':'valid','timezone':'Bad/Zone'},{'title':'valid','unexpected':1}])
def test_invalid_capture_is_rejected(client,data):
    assert client.post('/responsibilities',json=data).status_code==422
    assert client.get('/responsibilities').json()==[]


def test_preferences_and_unknown_reminders(client):
    assert client.put('/reminder-preferences',json={'timezone':'Asia/Kolkata','quiet_start':'25:00'}).status_code==422
    assert client.put('/reminder-preferences',json={'timezone':'Asia/Kolkata','quiet_enabled':False}).status_code==200
    assert client.get('/reminder-preferences').json()['timezone']=='Asia/Kolkata'
    assert client.get('/follow-up').json()['due']==[]
    assert client.post('/reminders/missing/acknowledge').status_code==404
