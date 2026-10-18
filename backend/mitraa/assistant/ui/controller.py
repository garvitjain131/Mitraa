"""Explicit presentation, audio-session and agent-request lifecycles on the Qt thread."""
import json
import logging
import signal
import threading
from enum import Enum
from PySide6.QtCore import QObject, Signal, Qt, QTimer, Slot
from PySide6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from mitraa.assistant.ui.overlay import Overlay, Presentation

logger = logging.getLogger(__name__)

class State(str, Enum):
    IDLE='idle'
    LOADING_VOICE='loading_voice'
    WAITING_FOR_SPEECH='waiting_for_speech'
    RECORDING_SPEECH='recording_speech'
    TRANSCRIBING='transcribing'
    THINKING='thinking'
    WORKING='working'
    AWAITING_APPROVAL='awaiting_approval'
    SPEAKING='speaking'
    ERROR='error'

LABELS = {'idle':'Ready', 'loading_voice':'Loading voice…', 'waiting_for_speech':'Listening…',
          'recording_speech':'Listening to you…', 'transcribing':'Understanding…', 'thinking':'Thinking…',
          'working':'Working…', 'awaiting_approval':'Permission needed', 'speaking':'Speaking…', 'error':'Needs attention'}

class Bridge(QObject):
    event = Signal(str, object, int)

class Controller(QObject):
    def __init__(self, overlay, preview=False, worker=None):
        super().__init__()
        self.overlay=overlay; self.preview=preview
        self.bridge=Bridge(self); self.bridge.event.connect(self.handle,Qt.QueuedConnection)
        self.epoch=0; self.request_id=0
        self.busy=False; self.pending=None; self.pending_info=None
        self.state=State.IDLE
        self.voice_input=None; self.voice=None; self.speaker=None; self.listener=None
        self.inputs=[]; self.session_open=False; self.submitted_sessions=set()
        self.transcript=''; self.result_text=''; self.result_card=None; self.progress=''
        self.recording_limited=False; self.preview_index=0; self.muted=False
        self.voice_builder=None; self.voice_settings=None; self.voice_initializing=False
        self.closed=False; self.escape_hook=None; self.use_escape_hook=False
        self.orb_restart=QTimer(self); self.orb_restart.setSingleShot(True)
        self.orb_restart.timeout.connect(self.restart_expanded_listening)
        if preview: self.worker=None
        elif worker is not None: self.worker=worker
        else:
            from mitraa.assistant.worker import AgentWorker
            self.worker=AgentWorker(lambda event: self.bridge.event.emit(*event))
        overlay.dismiss_requested.connect(self.dismiss)
        overlay.keyboard_requested.connect(self.type_request)
        overlay.mic_requested.connect(self.listen)
        overlay.submitted.connect(self.submit)
        overlay.decision.connect(self.decide)
        overlay.orb_clicked.connect(self.on_orb_click)
        overlay.orb_double_clicked.connect(self.on_orb_double_click)
        overlay.preview_next.connect(self.next_preview)
        overlay.visibility_changed.connect(self.sync_escape_hook)

    def set_state(self,state):
        self.state=State(state); self.overlay.orb.state=self.state.value
        self.render_status()

    def render_status(self):
        status=LABELS[self.state.value]
        if self.recording_limited and self.state in (State.TRANSCRIBING,State.THINKING): status+=' · recording limit'
        content=self.progress if self.state==State.WORKING else (self.result_text or self.transcript)
        self.overlay.set_status(status,content,waveform=self.state in (State.WAITING_FOR_SPEECH,State.RECORDING_SPEECH))
        self.overlay.buttons[0].setEnabled(not self.busy and self.pending is None)
        self.overlay.buttons[1].setEnabled(not self.busy and self.pending is None)
        self.overlay.orb.update()

    def sync_escape_hook(self,visible):
        if not self.use_escape_hook: return
        if not visible:
            if self.escape_hook: self.escape_hook.stop(); self.escape_hook=None
        elif self.escape_hook is None:
            from pynput import keyboard
            self.escape_hook=keyboard.Listener(on_press=lambda key: self.bridge.event.emit('escape',None,-1) if key==keyboard.Key.esc else None)
            self.escape_hook.start()

    def expand(self):
        # Presentation only: no cancellation, transcription, or submission here.
        self.overlay.present(Presentation.EXPANDED_DOCK)
        self.render_status()

    def stop_listening(self):
        if self.session_open:
            self.invalidate_audio()
            self.set_state(State.IDLE)

    def on_orb_click(self):
        if self.overlay.presentation == Presentation.EXPANDED_DOCK:
            if self.session_open:
                self.stop_listening()
            elif not self.busy and self.pending is None:
                # Wait out Qt's double-click window before opening the microphone.
                self.orb_restart.start(QApplication.doubleClickInterval())
        elif self.pending:
            self.show_approval()
        else:
            self.expand()
            if self.result_card and not self.busy and not self.session_open:
                title,text,approval,details=self.result_card
                self.overlay.show_card(title,text,approval=approval,details=details)

    def on_orb_double_click(self):
        self.orb_restart.stop()
        self.stop_listening()
        self.overlay.present(Presentation.ORB_ONLY)
        self.render_status()

    def restart_expanded_listening(self):
        if (not self.closed and self.overlay.isVisible()
                and self.overlay.presentation == Presentation.EXPANDED_DOCK
                and not self.session_open and not self.busy and self.pending is None):
            self.listen(expanded=True)

    def summon(self,*_):
        if self.pending:
            self.show_approval(); return
        if self.busy:
            self.expand(); return
        if self.session_open:
            if not self.overlay.isVisible(): self.overlay.present(Presentation.ORB_ONLY)
            return
        self.listen()

    def invalidate_audio(self):
        self.orb_restart.stop()
        self.epoch+=1; self.session_open=False
        if self.voice_input: self.voice_input.cancel()
        if self.speaker: self.speaker.stop()
        self.overlay.orb.level=0.; self.overlay.waveform.clear()

    def dismiss(self):
        self.invalidate_audio()
        self.overlay.present(Presentation.HIDDEN)
        # Agent execution and pending IDs deliberately survive hiding.
        if not self.busy and not self.pending: self.set_state(State.IDLE)

    def type_request(self):
        if self.pending or self.busy: return
        self.invalidate_audio(); self.transcript=''; self.result_text=''
        self.set_state(State.IDLE); self.overlay.set_typing()

    def ensure_voice(self):
        if self.closed or self.preview: return False
        if self.voice is not None: return True
        if self.voice_initializing: return False
        # Even importing NumPy / provider settings must not delay the first orb paint.
        def build():
            try:
                from mitraa.assistant.voice import build_voice
                from mitraa.config import get_settings
                settings=get_settings(); voice=build_voice(settings)
                if self.closed:
                    if voice.speaker: voice.speaker.close()
                    return
                self.bridge.event.emit('voice_initialized',(voice,settings),-1)
            except Exception as exc:
                if not self.closed: self.bridge.event.emit('voice_init_error',str(exc),-1)
        self.voice_initializing=True
        self.voice_builder=threading.Thread(target=build,name='mitraa-voice-init',daemon=True)
        self.voice_builder.start()
        return False

    def start_voice_input(self):
        if not self.session_open or not self.overlay.isVisible() or self.closed: return
        if not self.voice.transcriber:
            self.session_open=False
            self.show_error('Voice unavailable','Voice input is disabled. Expand the orb and use the keyboard to type.')
            return
        from mitraa.assistant.voice import VoiceInput
        predecessor=self.voice_input.capture_closed if self.voice_input else None
        token=self.epoch
        self.voice_input=VoiceInput(self.voice.transcriber,lambda event:self.bridge.event.emit(*event,token),
                                   settings=self.voice_settings,playback_idle=self.speaker.idle if self.speaker else None,
                                   predecessor=predecessor)
        self.inputs=[item for item in self.inputs if item.active]
        self.inputs.append(self.voice_input); self.voice_input.start()

    def listen(self, *, expanded=False):
        if self.busy or self.pending:
            self.summon(); return
        if self.session_open: return
        self.invalidate_audio(); self.session_open=True
        self.transcript=''; self.result_text=''; self.result_card=None; self.progress=''; self.recording_limited=False
        self.overlay.typing=False; self.overlay.hide_entry()
        self.overlay.clear_card()
        self.overlay.present(Presentation.EXPANDED_DOCK if expanded else Presentation.ORB_ONLY)
        if self.preview:
            self.set_state(State.WAITING_FOR_SPEECH); return
        self.set_state(State.LOADING_VOICE)
        if self.ensure_voice(): self.start_voice_input()

    def submit(self,text,voice=False):
        text=text.strip()
        if not text or self.busy or self.pending: return
        if voice:
            if not self.session_open or self.epoch in self.submitted_sessions: return
            self.submitted_sessions.add(self.epoch)
        self.invalidate_audio()
        self.overlay.typing=False; self.overlay.hide_entry(); self.overlay.clear_card()
        self.transcript=text; self.result_text=''; self.overlay.clear_entry(); self.result_card=None
        self.expand(); self.set_state(State.THINKING)
        if self.preview:
            self.result_text='[Preview] No request was sent.'; self.set_state(State.IDLE); return
        self.busy=True; self.request_id+=1; self.render_status()
        self.worker.send(text,self.request_id)

    def decide(self,approved):
        if self.pending is None or self.busy: return
        approval_id=self.pending
        self.pending=None; self.pending_info=None; self.overlay.clear_card()
        self.result_card=None
        if self.preview:
            self.result_text='[Preview] Decision recorded; no tools executed.'; self.set_state(State.IDLE); return
        self.busy=True; self.request_id+=1
        self.set_state(State.WORKING)
        self.worker.decide(approval_id,approved,self.request_id)

    def say(self,text):
        if not self.overlay.isVisible() or not self.speaker or self.session_open: return
        token=self.epoch
        self.speaker.post=lambda event:self.bridge.event.emit(*event,token)
        self.speaker.say(text)

    def show_error(self,title,text,details=False):
        self.session_open=False; self.result_text=title; self.result_card=(title,text,False,details)
        self.set_state(State.ERROR)
        self.overlay.show_card(title,text,details=details)

    def show_approval(self):
        info=self.pending_info
        if info is None: return
        text=f'{info.description}\n\nRisk: {info.risk}\nAffected paths / arguments:\n{json.dumps(info.args,indent=2,default=str)}\n\nReasons:\n'+'\n'.join(info.reasons)
        self.set_state(State.AWAITING_APPROVAL)
        self.overlay.deny.setEnabled(True); self.overlay.allow.setEnabled(True)
        self.overlay.show_card('Permission required' if not self.preview else 'Preview · sample permission',text,approval=True)

    @Slot(str,object,int)
    def handle(self,kind,payload,token=-1):
        if self.closed: return
        if kind=='voice_initialized':
            self.voice_initializing=False
            self.voice,self.voice_settings=payload; self.speaker=self.voice.speaker
            if self.speaker: self.speaker.enabled=not self.muted
            self.start_voice_input()
            return
        if kind=='voice_init_error':
            self.voice_initializing=False
            if self.session_open and self.overlay.isVisible(): self.show_error('Voice unavailable',str(payload))
            return
        if kind.startswith(('voice_','speech_')) and (token!=self.epoch or not self.overlay.isVisible()): return
        if kind in ('step','response','error') and token!=self.request_id: return
        if kind=='summon': self.summon()
        elif kind=='escape': self.dismiss()
        elif kind=='voice_state':
            self.set_state(State(payload))
            if payload=='transcribing': self.expand()
        elif kind=='voice_level':
            self.overlay.orb.level=payload; self.overlay.waveform.feed(payload)
        elif kind=='voice_endpoint':
            self.recording_limited=payload=='max'; self.expand(); self.set_state(State.TRANSCRIBING)
        elif kind=='voice_text': self.submit(payload,voice=True)
        elif kind=='voice_none': self.show_error('I didn’t catch anything',str(payload))
        elif kind=='voice_error': self.show_error('Voice unavailable',str(payload))
        elif kind=='speech_error': self.show_error('Speech playback unavailable',str(payload),details=True)
        elif kind=='speech_started':
            if not self.session_open: self.set_state(State.SPEAKING)
        elif kind in ('speech_finished','speech_stopped'):
            if not self.session_open: self.set_state(State.AWAITING_APPROVAL if self.pending else State.IDLE)
        elif kind=='step':
            self.progress=f'{payload.tool}: {payload.summary}'; self.set_state(State.WORKING)
        elif kind=='response':
            self.busy=False
            info=payload.pending_approval
            if payload.type=='approval_required' and info:
                self.pending=info.approval_id; self.pending_info=info
                self.set_state(State.AWAITING_APPROVAL)
                if self.overlay.isVisible(): self.show_approval()
            else:
                self.result_text=payload.text; self.result_card=None; self.set_state(State.IDLE)
                if len(payload.text)>100 or '\n' in payload.text:
                    self.result_card=('Mitraa',payload.text,False,False)
                    if self.overlay.isVisible(): self.overlay.show_card('Mitraa',payload.text)
                self.say(payload.text)
        elif kind=='error':
            self.busy=False
            info=payload if isinstance(payload,dict) else {'message':str(payload),'details':''}
            title='Connect your AI provider' if info.get('provider_setup') else 'Request failed'
            text=('Add NEBIUS_API_KEY, NEBIUS_BASE_URL and NEMOTRON_MODEL to backend/.env, then restart.\n\n'+info.get('details','')) if info.get('provider_setup') else info.get('message','Please try again.')+'\n\n'+info.get('details','')
            self.result_text=title; self.result_card=(title,text,False,True); self.set_state(State.ERROR)
            if self.overlay.isVisible(): self.overlay.show_card(title,text,details=True)

    def new_chat(self):
        if self.busy or self.pending: return
        self.invalidate_audio()
        if self.worker: self.worker.new_conversation()
        self.transcript=''; self.result_text=''; self.result_card=None
        self.overlay.clear_card(); self.set_state(State.IDLE)

    def next_preview(self):
        if self.pending: return  # A preview decision also needs an explicit button.
        from types import SimpleNamespace
        demos=['endpoint','thinking','working','speaking','response','error','approval','typing','circle']
        demo=demos[self.preview_index%len(demos)]; self.preview_index+=1
        self.overlay.clear_card()
        if demo=='circle': self.listen()
        elif demo=='typing': self.type_request()
        elif demo=='endpoint':
            self.session_open=False; self.expand(); self.transcript='[Preview] Plan my project'; self.set_state(State.TRANSCRIBING)
        elif demo=='thinking': self.set_state(State.THINKING); self.expand()
        elif demo=='working': self.progress='[Preview] Building a task schedule'; self.set_state(State.WORKING); self.expand()
        elif demo=='speaking': self.result_text='[Preview] Sample playback appearance · no audio'; self.set_state(State.SPEAKING); self.expand()
        elif demo=='response':
            self.set_state(State.IDLE); self.overlay.show_card('Preview · sample response','Your project has three tasks. This is simulated content; no model, microphone, or tools were used.')
        elif demo=='error': self.show_error('Preview · sample error','No microphone is opened in preview. Use the real microphone control in normal mode.')
        elif demo=='approval':
            self.pending='preview-id'; self.pending_info=SimpleNamespace(description='[Preview] Move Documents/sample.txt to Downloads/sample.txt',risk='HIGH',args={'source':'Documents/sample.txt','destination':'Downloads/sample.txt'},reasons=['Destination exists; overwrite requires explicit permission.'])
            self.show_approval()

    def shutdown(self):
        if self.closed: return
        self.closed=True; self.invalidate_audio()
        if self.listener: self.listener.stop()
        self.sync_escape_hook(False)
        for item in self.inputs: item.close()
        if self.speaker: self.speaker.close()
        if self.worker: self.worker.shutdown()
        self.overlay.close()


def run_qt(preview=False, startup_settings=None, on_ready=None):
    # Overlap native hook imports with Qt/font initialization on cold launch.
    hotkey_errors=[]
    hotkey_import=None
    if not preview:
        def preload_hotkey():
            try:
                import pynput.keyboard
            except Exception as exc:
                hotkey_errors.append(exc)
        hotkey_import=threading.Thread(target=preload_hotkey,name='mitraa-hotkey-import',daemon=True)
        hotkey_import.start()
    app=QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    overlay=Overlay(preview); controller=Controller(overlay,preview)
    tray=QSystemTrayIcon(app)
    pixmap=QPixmap(32,32); pixmap.fill(Qt.transparent)
    painter=QPainter(pixmap); painter.setRenderHint(QPainter.Antialiasing); painter.setBrush(QColor('#12434b')); painter.setPen(QColor('#39dce2')); painter.drawEllipse(3,3,26,26); painter.end()
    tray.setIcon(QIcon(pixmap)); tray.setToolTip('Mitraa · PREVIEW · no microphone' if preview else 'Mitraa · hold Ctrl alone for 3 seconds')
    menu=QMenu()
    def action(label,callback,checkable=False,checked=False):
        item=QAction(label,menu); item.setCheckable(checkable); item.setChecked(checked); item.triggered.connect(callback); menu.addAction(item); return item
    action('Open (listen)' if not preview else 'Preview: circle-only listening',controller.summon)
    action('New conversation',controller.new_chat)
    def mute(checked):
        controller.muted=checked; overlay.orb.muted=checked
        if controller.speaker:
            controller.speaker.enabled=not checked
            if checked: controller.speaker.stop()
        overlay.orb.update()
    action('Mute speech output',mute,True)
    def reduce(checked):
        overlay.orb.reduced=checked; overlay.preferences.setValue('reduced_motion',checked); overlay.orb.update()
        if checked:
            overlay.animation.stop(); overlay.set_expansion(1. if overlay.presentation==Presentation.EXPANDED_DOCK else 0.)
    action('Reduced motion',reduce,True,overlay.orb.reduced)
    if preview:
        action('Preview: next / simulate speech end',controller.next_preview)
        action('Preview: typing',controller.type_request)
        controller.listen()
    else:
        from mitraa.assistant.startup import load_startup_settings
        from mitraa.assistant.hotkey import HoldDetector,HotkeyListener,parse_hotkeys
        settings=startup_settings or load_startup_settings()
        detector=HoldDetector(settings.mitraa_hold_seconds,lambda key:controller.bridge.event.emit('summon',key,-1))
        hotkey_import.join()
        if hotkey_errors: raise RuntimeError('Hotkey initialization failed.') from hotkey_errors[0]
        controller.listener=HotkeyListener(parse_hotkeys(settings.mitraa_hotkey),detector)
        controller.use_escape_hook=True
        controller.listener.start()
        action('Pause hotkey',lambda checked:setattr(detector,'enabled',not checked),True)
        if settings.mitraa_voice_preload:
            QTimer.singleShot(250,controller.ensure_voice)  # no capture until an explicit session
    action('Quit',app.quit)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason:controller.summon() if reason==QSystemTrayIcon.Trigger else None)
    tray.show()
    app.aboutToQuit.connect(controller.shutdown); app.aboutToQuit.connect(tray.hide)
    signal.signal(signal.SIGINT,lambda *_:app.quit())
    timer=QTimer(); timer.timeout.connect(lambda:None); timer.start(250)
    if on_ready is not None: QTimer.singleShot(0,on_ready)
    return app.exec()
