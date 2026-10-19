"""Native, API-free conversation layout smoke check; run from backend."""
import os
from pathlib import Path
from PySide6.QtCore import QPoint,Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtGui import QFontDatabase
from mitraa.assistant.ui.overlay import Overlay
from mitraa.assistant.ui.controller import Controller

app=QApplication.instance() or QApplication([])
if app.platformName()=='offscreen':
    for font in ('segoeui.ttf','segoeuib.ttf'):
        path=Path('C:/Windows/Fonts')/font
        if path.exists(): QFontDatabase.addApplicationFont(str(path))
o=Overlay(True); c=Controller(o,True)
scale=os.environ.get('QT_SCALE_FACTOR','1'); folder=Path('logs'); folder.mkdir(exist_ok=True)
try:
    o._origin=QPoint(o.active_screen().availableGeometry().left()+120,o.active_screen().availableGeometry().bottom()-110)
    c.preview_index=4; c.next_preview(); QTest.qWait(400)
    o.grab().save(str(folder/f'conversation-compact-{scale}.png'))
    center=o.orb.mapToGlobal(o.orb.rect().center())
    o.toggle_history(); QTest.qWait(300)
    assert o.orb.mapToGlobal(o.orb.rect().center())==center
    panel=o.conversation
    assert panel.isVisible() and panel.messages.verticalScrollBar().maximum()>0
    QTest.mouseClick(panel.composer.viewport(),Qt.LeftButton); QTest.qWait(100)
    QTest.keyClicks(panel.composer,'Unsent draft')
    assert panel.composer.hasFocus()
    o.grab().save(str(folder/f'conversation-expanded-{scale}.png'))
    o.toggle_history(); QTest.qWait(280); o.toggle_history(); QTest.qWait(280)
    assert panel.composer.toPlainText()=='Unsent draft'
    panel.submit(); QTest.qWait(450)
    assert not c.busy and '[Preview]' in c.result_text
    c.preview_index=6; c.next_preview(); QTest.qWait(300)
    assert c.pending and o.deny.isVisible() and not panel.send.isEnabled()
    assert o.deny.hasFocus()
    o.grab().save(str(folder/f'conversation-approval-{scale}.png'))
    c.decide(False)
    area=o.active_screen().availableGeometry(); o._origin=QPoint(area.left()+30,area.top()+310)
    o.refresh(); QTest.qWait(100)
    assert o.y()>=area.top() and o.card.height()<=o.history_limit()
    o.grab().save(str(folder/f'conversation-small-{scale}.png'))
    print(f'Conversation smoke ({app.platformName()}) {scale}x passed: compact, expanded, anchored orb, focused composer, draft, preview reply, approval, small-height cap.')
finally:
    c.shutdown(); app.processEvents()
