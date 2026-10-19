from datetime import datetime,timezone
from types import SimpleNamespace as NS
from PySide6.QtCore import Qt,QPoint
from PySide6.QtTest import QTest
from tests.test_orb_ui import ui
from mitraa.assistant.ui.controller import State

def open_panel(o):
    o.show_card('Mitraa','Hello!')
    o.toggle_history()
    return o.conversation

def test_expand_collapse_preserves_draft_and_audio(ui):
    app,o,c,w=ui
    c.session_open=True; c.set_state(State.RECORDING_SPEECH)
    o._origin=QPoint(o._origin.x(),o.active_screen().availableGeometry().bottom()-100)
    o.show_card('Mitraa','Hello!'); center=o.orb.mapToGlobal(o.orb.rect().center())
    panel=open_panel(o); panel.composer.setPlainText('unfinished draft')
    # Expansion itself does not stop recording.
    assert c.session_open and c.state==State.RECORDING_SPEECH
    assert o.history_button.accessibleName()=='Collapse conversation'
    o.toggle_history(); o.toggle_history()
    assert panel.composer.toPlainText()=='unfinished draft'
    assert o.orb.mapToGlobal(o.orb.rect().center())==center
    assert not w.sent

def test_composer_enter_shift_enter_and_submission_guards(ui):
    app,o,c,w=ui; panel=open_panel(o)
    panel.composer.setFocus(); QTest.keyClicks(panel.composer,'Hello')
    QTest.keyClick(panel.composer,Qt.Key_Return,Qt.ShiftModifier)
    QTest.keyClicks(panel.composer,'Mitraa')
    assert panel.composer.toPlainText()=='Hello\nMitraa' and not w.sent
    QTest.keyClick(panel.composer,Qt.Key_Return); QTest.keyClick(panel.composer,Qt.Key_Return)
    assert w.sent==[('Hello\nMitraa',1)] and panel.composer.toPlainText()==''
    panel.composer.setPlainText('keep while busy'); panel.submit()
    assert panel.composer.toPlainText()=='keep while busy' and not panel.send.isEnabled()
    assert len(c.history_records)==1

def test_empty_input_and_growing_composer(ui):
    app,o,c,w=ui; panel=open_panel(o)
    single=panel.composer.height()
    panel.composer.setPlainText(' \n  '); panel.submit()
    assert not w.sent and not panel.send.isEnabled()
    panel.composer.setPlainText('one\ntwo\nthree\nfour\nfive')
    assert panel.composer.height()>single
    assert panel.composer.height()<=panel.composer.fontMetrics().lineSpacing()*4+20

def test_failure_retains_messages_and_draft_without_fake_reply(ui):
    app,o,c,w=ui; panel=open_panel(o)
    c.submit('hello'); panel.composer.setPlainText('unsent follow up')
    c.handle('error',dict(message='Please reconnect.',provider_setup=False),1)
    assert len(c.history_records)==1 and c.history_records[0]['role']=='user'
    assert panel.composer.toPlainText()=='unsent follow up'
    assert panel.notice.text()=='Please reconnect.'

def test_expand_is_keyboard_accessible(ui):
    app,o,c,w=ui; o.show_card('Mitraa','Hello!')
    o.activateWindow(); o.history_button.setFocus()
    QTest.keyClick(o.history_button,Qt.Key_Space)
    assert o.history_expanded and o.history_button.accessibleName()=='Collapse conversation'

def test_typing_cancels_capture_but_expand_does_not(ui):
    app,o,c,w=ui; c.session_open=True; c.set_state(State.RECORDING_SPEECH)
    stopped=[]; c.voice_input=NS(cancel=lambda:stopped.append(True))
    panel=open_panel(o); assert not stopped
    panel.composer.setFocus(); QTest.keyClicks(panel.composer,'hi')
    assert stopped and not c.session_open
    c.voice_input=None

def test_history_merge_safe_render_and_stale_conversation(ui):
    app,o,c,w=ui; panel=open_panel(o); c.submit('hello')
    row=dict(id=5,role='user',content='<img src="file:///secret">\n& hello',created_at=datetime.now(timezone.utc).isoformat(),request_id=1)
    c.handle('message_saved',dict(conversation_id='current',message=row),1)
    c.handle('history',dict(conversation_id='current',messages=[{**row,'request_id':None}],has_more=False,before=None),c.history_generation)
    assert len(c.history_records)==1
    assert '<img src="file:///secret">' in panel.messages.toPlainText()
    assert '&lt;img' in panel.messages.toHtml()
    c.handle('history',dict(conversation_id='other',messages=[{**row,'id':99}],has_more=False,before=None),c.history_generation)
    assert len(c.history_records)==1
    c.busy=False; c.new_chat()
    c.handle('history',dict(conversation_id='current',messages=[row],has_more=False,before=None),c.history_generation-1)
    assert not c.history_records and not panel.composer.toPlainText()

def test_saved_assistant_and_live_response_do_not_duplicate_on_history_reload(ui):
    app,o,c,w=ui; panel=open_panel(o); c.submit('hello')
    rows=[dict(id=i,role=role,content=text,created_at='2026-10-19T10:00:00+00:00',request_id=1)
          for i,role,text in [(5,'user','hello'),(6,'assistant','Hello!')]]
    for row in rows: c.handle('message_saved',dict(conversation_id='one',message=row),1)
    c.handle('response',NS(type='message',pending_approval=None,text='Hello!'),1)
    c.handle('history',dict(conversation_id='one',messages=[{**r,'request_id':None} for r in rows],has_more=False,before=None),c.history_generation)
    assert len(c.history_records)==2 and [r['id'] for r in c.history_records]==[5,6]
    c.handle('response',NS(type='message',pending_approval=None,text='Hello!'),1)
    assert len(c.history_records)==2

def test_real_worker_roundtrip_and_history_reload(ui,monkeypatch,tmp_path):
    from mitraa.assistant.worker import AgentWorker
    from mitraa.db.session import make_engine,make_session_factory
    import mitraa.db.session as db
    factory=make_session_factory(make_engine('sqlite:///'+str(tmp_path/'roundtrip.db')))
    monkeypatch.setattr(db,'default_session_factory',lambda:factory)
    app,o,c,_=ui; panel=open_panel(o)
    c.worker=AgentWorker(lambda event:c.bridge.event.emit(*event))
    for text in ('hello','what is your name?'):
        panel.composer.setPlainText(text); panel.submit()
        for _ in range(100):
            QTest.qWait(25)
            if not c.busy: break
        assert not c.busy
    c.open_history()
    for _ in range(100):
        QTest.qWait(25)
        if not c.history_loading: break
    assert not c.history_loading and len(c.history_records)==4
    assert len({r['id'] for r in c.history_records})==4
    assert [r['content'] for r in c.history_records]==['hello','Hello!','what is your name?','Mitraa.']

def test_approval_remains_actionable_and_blocks_composer(ui):
    app,o,c,w=ui; panel=open_panel(o); c.submit('move file')
    info=NS(approval_id='approval-id',description='Overwrite existing file',risk='HIGH',args={'path':'Documents/a.txt'},reasons=['Overwrite'])
    c.handle('response',NS(type='approval_required',pending_approval=info),1)
    app.processEvents()
    assert o.history_expanded and o.deny.isVisible() and o.allow.isVisible()
    assert 'Documents/a.txt' in o.body.toPlainText()
    panel.composer.setPlainText('keep draft'); panel.submit()
    o.toggle_history(); o.toggle_history()
    assert panel.composer.toPlainText()=='keep draft' and not w.decisions and len(w.sent)==1
    o.deny.click(); o.allow.click()
    assert w.decisions==[('approval-id',False,2)]

def test_scrolling_new_messages_and_prepend_preserve_reader(ui):
    app,o,c,w=ui; panel=open_panel(o); panel.resize(320,300)
    rows=[dict(id=i,role='user' if i%2 else 'assistant',content=('text '*20)+str(i),created_at='2026-10-19T10:00:00+00:00') for i in range(50,100)]
    panel.render(rows,initial=True); app.processEvents()
    bar=panel.messages.verticalScrollBar(); assert bar.maximum()>0
    bar.setValue(bar.maximum()//3); before=bar.value()
    panel.render(rows+[dict(rows[-1],id=100,content='New response')]); app.processEvents()
    assert bar.value()==before and panel.new_messages.isVisible()
    oldmax=bar.maximum(); before=bar.value()
    older=[dict(rows[0],id=i,content='older '*20) for i in range(40,50)]
    panel.render(older+rows+[dict(rows[-1],id=100,content='New response')],prepend=True)
    assert bar.value()==before+bar.maximum()-oldmax
    panel.latest(); assert bar.value()==bar.maximum()

def test_small_work_area_height_and_hidden_response(ui):
    app,o,c,w=ui
    area=o.active_screen().availableGeometry(); o._origin=QPoint(area.left()+60,area.top()+300)
    panel=open_panel(o); app.processEvents()
    assert o.y()>=area.top() and o.card.height()<=o.history_limit()
    panel.composer.setPlainText('keep this')
    c.submit('hello'); c.dismiss()
    c.handle('response',NS(type='message',pending_approval=None,text='Hello!'),1)
    assert not o.isVisible() and len(c.history_records)==2
    assert panel.composer.toPlainText()=='keep this'

def test_preview_composer_is_simulated_and_keeps_history(ui):
    from mitraa.assistant.ui.overlay import Overlay
    from mitraa.assistant.ui.controller import Controller
    app,_,_,_=ui
    o=Overlay(True); o.orb.reduced=True; c=Controller(o,True)
    try:
        panel=open_panel(o); panel.composer.setPlainText('hello'); panel.submit()
        assert c.busy and c.worker is None
        QTest.qWait(450)
        assert not c.busy and len(c.history_records)==2
        assert '[Preview]' in c.history_records[-1]['content']
    finally: c.shutdown()
