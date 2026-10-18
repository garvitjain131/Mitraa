"""Adaptive presentation and execution contracts with no audio/provider access."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from types import SimpleNamespace as NS
import pytest
from PySide6.QtCore import Qt,QPoint
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from mitraa.assistant.ui.controller import Controller,State
from mitraa.assistant.ui.overlay import Overlay,Presentation

@pytest.fixture
def ui():
    app=QApplication.instance() or QApplication([])
    o=Overlay(); o.orb.reduced=True
    class Worker:
        def __init__(self): self.sent=[]; self.decisions=[]
        def send(self,text,request_id): self.sent.append((text,request_id))
        def decide(self,*args): self.decisions.append(args)
        def shutdown(self): pass
        def new_conversation(self): pass
    w=Worker(); c=Controller(o,worker=w)
    o.present(Presentation.ORB_ONLY,False); app.processEvents()
    yield app,o,c,w
    c.shutdown(); app.processEvents()


def test_orb_only_has_no_dock_text_or_input(ui):
    app,o,c,w=ui
    assert o.width()==88 and o.orb.width()==72
    assert not o.content.isVisible() and not o.entry.isVisible() and not o.card.isVisible()
    assert not o.mask().contains(QPoint(0,0))


def test_orb_click_expands_without_touching_audio_or_phase(ui):
    app,o,c,w=ui
    c.session_open=True; c.set_state(State.RECORDING_SPEECH)
    class Audio:
        def cancel(self): pytest.fail('expansion cancelled audio')
    c.voice_input=Audio()
    epoch=c.epoch; clock=o.orb.clock
    center=o.orb.mapToGlobal(o.orb.rect().center())
    # Place sufficiently far left so no work-area shift is necessary.
    o._origin=QPoint(QApplication.primaryScreen().availableGeometry().left()+50,o._origin.y()); o.refresh()
    center=o.orb.mapToGlobal(o.orb.rect().center())
    QTest.mouseClick(o.orb,Qt.LeftButton); app.processEvents()
    assert o.presentation==Presentation.EXPANDED_DOCK and o.width()==456
    assert c.state==State.RECORDING_SPEECH and c.epoch==epoch and c.session_open
    assert o.orb.clock is clock and o.orb.mapToGlobal(o.orb.rect().center())==center
    assert not w.sent
    c.voice_input=None


def test_typing_replaces_center_and_enter_sends_once(ui):
    app,o,c,w=ui
    c.type_request(); app.processEvents()
    assert o.entry.isVisible() and o.entry.parent() is o.content
    assert o.entry.hasFocus() and not o.card.isVisible()
    o.entry.setText('Hello'); QTest.keyClick(o.entry,Qt.Key_Return); QTest.keyClick(o.entry,Qt.Key_Return)
    assert w.sent==[('Hello',1)] and c.busy
    assert not o.entry.isVisible()


def test_voice_and_typed_submission_cannot_double_send(ui):
    app,o,c,w=ui
    c.session_open=True; c.set_state(State.TRANSCRIBING); token=c.epoch
    c.handle('voice_text','Move my notes',token)
    c.handle('voice_text','Move my notes',token); c.submit('Another request')
    assert w.sent==[('Move my notes',1)]


def test_stale_audio_and_stale_request_ignored(ui):
    app,o,c,w=ui
    c.session_open=True; old=c.epoch; c.dismiss(); c.expand()
    c.handle('voice_text','old command',old); c.handle('speech_started',None,old)
    assert not w.sent and c.state==State.IDLE
    c.submit('Hello')
    c.handle('response',NS(type='message',pending_approval=None,text='stale response'),0)
    assert c.busy and not c.result_text


def test_hidden_inflight_result_preserved(ui):
    app,o,c,w=ui
    c.submit('Hello'); c.dismiss()
    assert c.busy and c.state==State.THINKING and o.presentation==Presentation.HIDDEN
    c.handle('response',NS(type='message',pending_approval=None,text='Actual response'),c.request_id)
    assert not c.busy and c.result_text=='Actual response' and not o.isVisible()


def test_endpoint_expands_and_limit_is_clear(ui):
    app,o,c,w=ui
    c.session_open=True; c.set_state(State.RECORDING_SPEECH)
    c.handle('voice_endpoint','max',c.epoch)
    assert c.state==State.TRANSCRIBING and o.presentation==Presentation.EXPANDED_DOCK
    assert 'limit' in o.status.toolTip() and not w.sent


def test_no_speech_is_retry_and_never_calls_agent(ui):
    app,o,c,w=ui
    c.session_open=True; c.handle('voice_none','Tap the microphone to retry.',c.epoch)
    assert not c.session_open and not w.sent
    assert o.presentation==Presentation.EXPANDED_DOCK and o.card.isVisible()
    assert 'catch' in o.title.text()


def test_approval_survives_hide_and_requires_explicit_once(ui):
    app,o,c,w=ui
    c.submit('Move a file')
    info=NS(approval_id='real-id',description='Move file',risk='HIGH',args={'path':'Documents/a.txt'},reasons=['Overwrite'])
    c.handle('response',NS(type='approval_required',pending_approval=info),c.request_id); app.processEvents()
    assert c.pending=='real-id' and o.deny.hasFocus()
    assert 'Documents/a.txt' in o.body.toPlainText()
    c.dismiss(); assert c.pending=='real-id' and not w.decisions and not o.isVisible()
    c.summon(); app.processEvents(); assert o.card.isVisible()
    o.deny.click(); o.allow.click()
    assert w.decisions==[('real-id',False,2)]


def test_preview_has_no_provider_microphone_or_downloads(monkeypatch):
    app=QApplication.instance() or QApplication([])
    import mitraa.assistant.voice as voice
    import mitraa.assistant.worker as worker
    monkeypatch.setattr(voice,'build_voice',lambda *_:pytest.fail('preview opened audio'))
    monkeypatch.setattr(worker,'NemotronClient',lambda *_:pytest.fail('preview provider'))
    o=Overlay(True); o.orb.reduced=True; c=Controller(o,True); c.listen()
    assert o.presentation==Presentation.ORB_ONLY
    c.expand(); c.next_preview(); assert c.state==State.TRANSCRIBING
    for _ in range(10):
        c.next_preview(); app.processEvents()
        if c.pending: c.decide(False)
    assert c.worker is None and c.voice is None and c.speaker is None
    c.shutdown()


def test_cards_resize_and_details_expand(ui):
    app,o,c,w=ui
    o.show_card('Long reply','A longer explanation. '*120); app.processEvents(); tall=o.card.height()
    assert o.body.height()<=180
    o.show_card('Mitraa','Done.'); app.processEvents()
    assert o.card.height()<tall and o.card.height()<110
    o.show_card('Request failed','Technical context\n'*8,details=True); app.processEvents(); collapsed=o.card.height()
    o.details.click(); app.processEvents(); assert o.card.height()>collapsed
    o.details.click(); app.processEvents(); assert o.card.height()==collapsed


def test_expansion_clamps_to_work_area_and_card_keeps_orb_anchored(ui):
    app,o,c,w=ui
    area=QApplication.primaryScreen().availableGeometry()
    o._origin=QPoint(area.right()-75,area.bottom()-90); o.refresh(); c.expand(); app.processEvents()
    assert area.contains(o.geometry())
    center=o.orb.mapToGlobal(o.orb.rect().center())
    o.show_card('Mitraa','A short response'); app.processEvents()
    assert o.orb.mapToGlobal(o.orb.rect().center())==center
    o._origin=QPoint(-100000,-100000); o.clamp(); assert area.contains(o.geometry())


def test_animation_intermediate_width_keeps_same_orb(ui):
    app,o,c,w=ui
    o._origin=QPoint(60,300); o.refresh(); orb=o.orb
    o.present(Presentation.EXPANDED_DOCK,False)
    o.set_expansion(.5)
    assert o.width()==272 and o.orb is orb and o.fade.opacity()==.5

def test_repeated_summon_does_not_restart_capture_or_work(ui):
    app,o,c,w=ui
    c.session_open=True; c.set_state(State.WAITING_FOR_SPEECH); epoch=c.epoch
    c.summon(); c.summon()
    assert c.epoch==epoch and not w.sent and o.presentation==Presentation.ORB_ONLY
    c.session_open=False; c.submit('Hello'); epoch=c.epoch
    c.summon(); c.summon()
    assert c.epoch==epoch and w.sent==[('Hello',1)] and o.presentation==Presentation.EXPANDED_DOCK
