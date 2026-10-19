"""Offline desktop workflow/render check; never touches the personal database."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from datetime import datetime,timedelta,timezone
from PySide6.QtCore import QObject,Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication,QScrollArea
from PySide6.QtTest import QTest
from mitraa.assistant.ui.responsibilities import DeskRuntime
from mitraa.db.session import make_engine,make_session_factory
from mitraa.responsibilities import service
from mitraa.responsibilities.schemas import ResponsibilityCreate,ResponsibilityPatch,ReminderCreate,Preferences

app=QApplication.instance() or QApplication([])
for name in ('segoeui.ttf','segoeuib.ttf'):
    path=Path('C:/Windows/Fonts')/name
    if path.exists(): QFontDatabase.addApplicationFont(str(path))
scale=os.environ.get('QT_SCALE_FACTOR','1')
Path('logs').mkdir(exist_ok=True)


class Tray(QObject):
    messageClicked=Signal()
    def isSystemTrayAvailable(self): return False


def until(predicate):
    for _ in range(150):
        app.processEvents()
        if predicate(): return
        QTest.qWait(20)
    assert predicate(),'Local workflow timed out'


with TemporaryDirectory(prefix='responsibilities-',dir='logs') as folder:
    engine=make_engine('sqlite:///'+str(Path(folder)/'smoke.db'))
    factory=make_session_factory(engine)
    with factory() as session:
        item=service.create(session,ResponsibilityCreate(title='Prepare internship application',
            outcome='A reviewed application with the required documents',next_action='Answer the eligibility question',
            timezone='Asia/Kolkata',deadline=datetime.now(timezone.utc)+timedelta(days=2),
            sources=['Requirements supplied by you'],notes='Use verified resume facts. Review before submission.'))
        item=service.patch(session,item['id'],ResponsibilityPatch(expected_version=1,status='waiting_for_user'))
        service.add_reminder(session,ReminderCreate(responsibility_id=item['id'],message='Review supporting documents',
            due_at=datetime.now(timezone.utc)+timedelta(hours=2)))
        service.save_preferences(session,Preferences(timezone='Asia/Kolkata',notifications_enabled=False))
        session.commit()
    tray=Tray(); runtime=DeskRuntime(tray,SimpleNamespace(worker=None),factory=factory)
    runtime.timer.stop()
    try:
        until(lambda:bool(runtime.snapshot))
        runtime.open(); runtime.desk.load_item(item); runtime.worker.submit('detail',id=item['id'])
        until(lambda:bool(runtime.desk.history.toPlainText()))
        runtime.desk.grab().save(f'logs/responsibilities-{scale}.png')
        runtime.desk.resize(820,620); app.processEvents()
        scroll=runtime.desk.findChild(QScrollArea)
        scroll.ensureWidgetVisible(runtime.desk.save_button); app.processEvents()
        assert runtime.desk.save_button.isVisible()
        assert runtime.desk.height()<=640
        runtime.desk.grab().save(f'logs/responsibilities-small-{scale}.png')
        runtime.provider.open_settings()
        until(lambda:runtime.provider.save_button.isEnabled())
        runtime.provider.grab().save(f'logs/provider-settings-{scale}.png')
    finally:
        runtime.shutdown(); runtime.worker.pool.shutdown(wait=True); app.processEvents(); engine.dispose()
print(f'Responsibility desk {scale}x passed: local persistence, activity, inbox, compact layout and provider screen; no AI/audio calls.')
