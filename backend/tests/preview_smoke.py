"""Temporary native preview entry-point smoke check."""
import os
import sys
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from mitraa.assistant.app import main
from mitraa.assistant.ui.overlay import Overlay,Presentation
app=QApplication([]); scale=os.environ.get('QT_SCALE_FACTOR','1'); state={}; failures=[]
folder=Path('logs'); folder.mkdir(exist_ok=True)
def run(callback):
    try: callback()
    except Exception as exc: failures.append(callback.__name__ + ': ' + repr(exc)); app.quit()
def circle():
    o=next(w for w in app.topLevelWidgets() if isinstance(w,Overlay)); state['o']=o; state['orb']=o.orb; state['phase']=o.orb.phase
    assert o.presentation==Presentation.ORB_ONLY and o.width()==88
    o.grab().save(str(folder/f'adaptive-circle-{scale}.png')); o.orb.clicked.emit(); QTimer.singleShot(700,lambda:run(dock))
def dock():
    o=state['o']; assert o.presentation==Presentation.EXPANDED_DOCK and o.width()==456
    assert o.orb is state['orb'] and (o.orb.reduced or o.orb.phase>state['phase'])
    o.grab().save(str(folder/f'adaptive-dock-{scale}.png')); o.preview_next.emit(); QTimer.singleShot(250,lambda:run(endpoint))
def endpoint():
    o=state['o']; assert 'Understanding' in o.status.toolTip()
    o.grab().save(str(folder/f'adaptive-understanding-{scale}.png')); o.keyboard_requested.emit(); QTimer.singleShot(250,lambda:run(typing))
def typing():
    o=state['o']; assert o.entry.isVisible()
    o.grab().save(str(folder/f'adaptive-typing-{scale}.png')); o.dismiss_requested.emit(); assert not o.isVisible(); state['completed']=True; QTimer.singleShot(100,app.quit)
QTimer.singleShot(150,lambda:run(circle))
QTimer.singleShot(20000,app.quit)
result=main(['--preview'])
assert not failures,failures
assert state.get('completed'), 'Preview smoke timed out before completion'
assert result==0
assert 'faster_whisper' not in sys.modules and 'mitraa.assistant.worker' not in sys.modules and 'sounddevice' not in sys.modules
print(f'Native preview {scale}x passed: circle, dock, expansion, endpoint, typing, dismissal, Qt shutdown; no audio/agent imports.')


