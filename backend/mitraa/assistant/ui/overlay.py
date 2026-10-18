"""Transparent orb/dock shell. Presentation changes never operate the agent."""
import math
from enum import Enum
from PySide6.QtCore import Qt, QPoint, QRectF, QTimer, Signal, Property, QPropertyAnimation, QEasingCurve, QSettings
from PySide6.QtGui import QPainter, QPen, QColor, QLinearGradient, QPainterPath, QRegion, QCursor
from PySide6.QtWidgets import (QWidget, QFrame, QLabel, QPushButton, QLineEdit, QTextBrowser,
                             QVBoxLayout, QHBoxLayout, QGraphicsOpacityEffect, QApplication)
from mitraa.assistant.ui.orb import Orb
from mitraa.assistant.ui.controls import Waveform, control_icon

class Presentation(str, Enum):
    HIDDEN = 'hidden'
    CIRCLE_ONLY = 'circle_only'
    ORB_ONLY = 'circle_only'  # readable alias for the shared orb component
    EXPANDED_DOCK = 'expanded_dock'

STYLE = '''
QWidget { color: #d7e6e8; font-family: "Segoe UI"; font-size: 11px; }
QLabel#name { color: #f2f6f6; font-size: 13px; font-weight: 600; }
QLabel#state { color: #8bb4c0; font-size: 11px; }
QFrame#card { background: rgba(10,25,31,246); border: 1px solid #34575b; border-radius: 14px; }
QLabel#heading { color: #f1f7f8; font-size: 12px; font-weight: 600; }
QPushButton { background: transparent; border: 1px solid #2b4148; border-radius: 15px; padding: 4px; }
QPushButton:hover, QPushButton:focus { background: #20454d; border-color: #4a8b96; }
QPushButton:disabled { color: #52686d; border-color: #243238; }
QPushButton#mic { border-color: #00a3af; }
QLineEdit { background: rgba(12,32,40,180); border: 1px solid #38606a; border-radius: 7px; padding: 5px; }
QTextBrowser { background: transparent; border: none; color: #bbced3; selection-background-color: #28616b; }
QScrollBar:vertical { background: transparent; width: 5px; margin: 2px 0; }
QScrollBar::handle:vertical { background: #38545e; border-radius: 2px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
'''

class Overlay(QWidget):
    dismiss_requested = Signal()
    keyboard_requested = Signal()
    mic_requested = Signal()
    submitted = Signal(str)
    decision = Signal(bool)
    preview_next = Signal()
    orb_clicked = Signal()
    visibility_changed = Signal(bool)

    def __init__(self, preview=False):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setWindowTitle('Mitraa adaptive dock' + (' · Preview' if preview else ''))
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet(STYLE)
        self.preferences = QSettings('Mitraa', 'DesktopPreview' if preview else 'Desktop')
        self.presentation = Presentation.HIDDEN
        self._progress = 0.
        self._origin = None
        self.dock_width = 456
        self.row_height = 88
        self.base_y = 0
        self._card_visible = False
        self.typing = False
        self.animation = QPropertyAnimation(self, b'expansion', self)
        self.animation.setDuration(300); self.animation.setEasingCurve(QEasingCurve.InOutCubic)
        self.orb = Orb(); self.orb.setParent(self)
        self.orb.reduced = self.preferences.value('reduced_motion', False, type=bool)
        self.orb.clicked.connect(self.orb_clicked.emit)
        self.orb.dragged.connect(self.drag_by); self.orb.released.connect(self.save_position)
        self.content = QWidget(self)
        self.fade = QGraphicsOpacityEffect(self.content); self.content.setGraphicsEffect(self.fade)
        self.name = QLabel('Mitraa' + (' · Preview' if preview else ''), self.content); self.name.setObjectName('name')
        self.status = QLabel('Ready', self.content); self.status.setObjectName('state')
        self.preview_text = QLabel('', self.content)
        self.waveform = Waveform(self.content)
        self.entry = QLineEdit(self.content); self.entry.setPlaceholderText('Type a request…'); self.entry.hide()
        self.entry.returnPressed.connect(lambda: self.submitted.emit(self.entry.text()))
        self.buttons = []
        for kind, tip, signal in [('keyboard','Type a request',self.keyboard_requested), ('microphone','Start a new utterance',self.mic_requested), ('dismiss','Dismiss',self.dismiss_requested)]:
            button = QPushButton(self.content); button.setFixedSize(30,30)
            button.setIcon(control_icon(kind)); button.setToolTip(tip); button.setAccessibleName(tip)
            if kind == 'microphone': button.setObjectName('mic')
            button.clicked.connect(signal.emit); self.buttons.append(button)
        self.card = QFrame(self); self.card.setObjectName('card'); self.card.setFixedWidth(360)
        layout = QVBoxLayout(self.card); layout.setContentsMargins(14,13,14,13); layout.setSpacing(9)
        self.title = QLabel(); self.title.setObjectName('heading'); self.title.setWordWrap(True); layout.addWidget(self.title)
        self.body = QTextBrowser(); self.body.setOpenExternalLinks(False); self.body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body.document().setDocumentMargin(0); layout.addWidget(self.body)
        self.details = QPushButton('Technical details'); self.details.clicked.connect(self.toggle_details); layout.addWidget(self.details)
        decisions = QHBoxLayout(); self.deny = QPushButton('Deny'); self.allow = QPushButton('Allow')
        self.allow.setStyleSheet('color: #ffc985; border-color: #83633e;')
        decisions.addWidget(self.deny); decisions.addWidget(self.allow); layout.addLayout(decisions)
        self.deny.clicked.connect(lambda: self.decision.emit(False)); self.allow.clicked.connect(lambda: self.decision.emit(True))
        self.card.hide(); self.content.hide()
        self.resize(88,88); self.orb.move(8,8)
        app = QApplication.instance()
        app.screenAdded.connect(self.screen_added); app.screenRemoved.connect(lambda _: self.refresh())
        for screen in app.screens(): self.screen_added(screen)

    def screen_added(self, screen):
        screen.availableGeometryChanged.connect(lambda _: self.refresh())
        screen.logicalDotsPerInchChanged.connect(lambda _: self.refresh())

    def get_expansion(self): return self._progress
    def set_expansion(self, value):
        self._progress = value
        self.refresh()
    expansion = Property(float, get_expansion, set_expansion)

    def active_screen(self):
        # Qt screen names match Windows display device names, avoiding physical /
        # logical coordinate mixing on monitors with different DPI.
        import sys
        if sys.platform == 'win32':
            try:
                import ctypes
                from ctypes import wintypes
                class MonitorInfo(ctypes.Structure):
                    _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT), ('work', wintypes.RECT), ('flags', wintypes.DWORD), ('device', wintypes.WCHAR * 32)]
                user = ctypes.windll.user32
                user.GetForegroundWindow.restype = wintypes.HWND
                user.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]; user.MonitorFromWindow.restype = wintypes.HANDLE
                user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
                info = MonitorInfo(); info.size = ctypes.sizeof(info)
                monitor = user.MonitorFromWindow(user.GetForegroundWindow(), 2)
                if user.GetMonitorInfoW(monitor, ctypes.byref(info)):
                    for screen in QApplication.screens():
                        if screen.name() == info.device: return screen
            except (AttributeError, OSError):
                pass
        return QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()

    def initialize_position(self):
        if self._origin is None:
            saved = self.preferences.value('adaptive_position')
            area = self.active_screen().availableGeometry()
            self._origin = saved if isinstance(saved, QPoint) else QPoint(area.center().x()-36, area.bottom()-self.row_height-16+8)

    def refresh(self):
        if not hasattr(self, 'card'): return
        self.initialize_position()
        screen = QApplication.screenAt(self._origin + QPoint(36,36)) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        self.dock_width = min(456, area.width())
        # Use font metrics to keep the center usable when accessibility font size rises.
        self.row_height = max(88, self.fontMetrics().height()*4+16)
        width = round(88 + (self.dock_width-88)*self._progress)
        card_height = self.fit_body() if self._card_visible else 0
        self.base_y = card_height+10 if self._card_visible else 0
        origin_x = self._origin.x()-8
        full_shift = min(0, area.right()+1 - (origin_x+self.dock_width))
        left = origin_x + round(full_shift*self._progress)
        height = self.base_y+self.row_height
        top = self._origin.y()-8-self.base_y
        left = max(area.left(), min(left, area.right()-width+1))
        top = max(area.top(), min(top, area.bottom()-height+1))
        self.setGeometry(left,top,width,height)
        self.orb.setGeometry(8,self.base_y+(self.row_height-72)//2,72,72)
        self.content.setGeometry(88,self.base_y+10,self.dock_width-100,self.row_height-20)
        center_width = self.content.width()-112
        self.name.setGeometry(0,0,center_width,self.fontMetrics().height()+7)
        self.status.setGeometry(0,25,center_width,17)
        self.preview_text.setGeometry(0,43,center_width,18)
        self.waveform.setGeometry(0,43,center_width,20)
        self.entry.setGeometry(0,29,center_width,30)
        for i, button in enumerate(self.buttons):
            button.move(self.content.width()-104+i*35, (self.content.height()-30)//2)
        self.content.setVisible(self._progress > 0)
        self.fade.setOpacity(self._progress)
        self.card.setGeometry(max(8,self.dock_width-368),0,360,card_height)
        self.card.setVisible(self._card_visible and self._progress > .85)
        self.orb.raise_()
        self.update_mask(); self.update()

    def update_mask(self):
        region = QRegion(self.orb.geometry(), QRegion.Ellipse)
        if self._progress > 0:
            path = QPainterPath(); path.addRoundedRect(QRectF(36,self.base_y+5,self.width()-40,self.row_height-10),36,36)
            region |= QRegion(path.toFillPolygon().toPolygon())
        if self.card.isVisible():
            path = QPainterPath(); path.addRoundedRect(QRectF(self.card.geometry()),14,14)
            region |= QRegion(path.toFillPolygon().toPolygon())
        self.setMask(region)

    def paintEvent(self, event):
        if self._progress <= 0: return
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing); p.setOpacity(self._progress)
        rect = QRectF(36,self.base_y+5,self.width()-40,self.row_height-10)
        fill = QLinearGradient(0,rect.top(),0,rect.bottom())
        fill.setColorAt(0,QColor(20,43,49,245)); fill.setColorAt(.45,QColor(7,18,25,246)); fill.setColorAt(1,QColor(13,34,39,245))
        p.setBrush(fill); p.setPen(Qt.NoPen); p.drawRoundedRect(rect,36,36)
        # The reference has a teal upper rim, a teal lower center, and amber
        # returns at both lower corners. Layered strokes give an inward glow
        # while keeping transparent space outside the dock click-through.
        for lower, stops in [
            (False, [(0,'#8adfe2'),(.18,'#05dce8'),(.65,'#00d9e7'),(.86,'#c3d1bd'),(1,'#ffcd87')]),
            (True, [(0,'#ffc66e'),(.18,'#f4bb60'),(.38,'#00dce5'),(.7,'#00e0e8'),(1,'#ffbb62')]),
        ]:
            p.save()
            half = rect.height()/2
            p.setClipRect(QRectF(rect.left()-4,rect.top()+half if lower else rect.top()-4,rect.width()+8,half+4))
            p.setBrush(Qt.NoBrush)
            for width, alpha in [(7,22),(3.5,60),(1.1,235)]:
                edge=QLinearGradient(rect.left(),0,rect.right(),0)
                for position, hex_color in stops:
                    color=QColor(hex_color); color.setAlpha(alpha); edge.setColorAt(position,color)
                p.setPen(QPen(edge,width)); p.drawRoundedRect(rect,36,36)
            p.restore()

    def present(self, presentation, animate=True):
        presentation = Presentation(presentation)
        self.animation.stop()
        self.presentation = presentation
        if presentation == Presentation.HIDDEN:
            self.hide(); return
        if not self.isVisible(): self.show()
        target = 1. if presentation == Presentation.EXPANDED_DOCK else 0.
        if target == 0:
            self._card_visible = False; self.typing = False; self.entry.hide()
        if animate and not self.orb.reduced and abs(self._progress-target) > .01:
            self.animation.setStartValue(self._progress); self.animation.setEndValue(target); self.animation.start()
        else:
            self.set_expansion(target)
        self.refresh()

    def summon(self, focus=False):
        self.present(Presentation.EXPANDED_DOCK)
        if focus: self.activateWindow(); self.raise_()

    def set_status(self, text, content='', waveform=False):
        self.status.setText(text); self.status.setToolTip(text)
        self.status.setText(self.status.fontMetrics().elidedText(text,Qt.ElideRight,max(80,self.content.width()-112)))
        self.preview_text.setText(self.preview_text.fontMetrics().elidedText(content,Qt.ElideRight,max(80,self.content.width()-112)))
        self.preview_text.setToolTip(content)
        self.waveform.setVisible(waveform and not self.typing)
        self.preview_text.setVisible(not waveform and not self.typing)
        self.status.setVisible(not self.typing)

    def set_typing(self):
        self._card_visible=False; self.typing=True
        self.present(Presentation.EXPANDED_DOCK)
        self.entry.show(); self.status.hide(); self.waveform.hide(); self.preview_text.hide()
        self.activateWindow(); self.raise_(); self.entry.setFocus(Qt.OtherFocusReason)

    def fit_body(self):
        width=332; document=self.body.document(); document.setTextWidth(width)
        height=math.ceil(document.size().height())
        if height>180:
            document.setTextWidth(width-8); height=math.ceil(document.size().height())
        self.body.setFixedHeight(max(20,min(180,height+4)))
        self.title.setFixedHeight(max(self.title.fontMetrics().height(),self.title.heightForWidth(width)))
        self.card.layout().invalidate()
        return self.card.layout().sizeHint().height()

    def toggle_details(self):
        self.body.setVisible(not self.body.isVisible())
        self.details.setText('Hide details' if self.body.isVisible() else 'Technical details')
        self.refresh()

    def show_card(self,title,text='',approval=False,details=False):
        self._card_visible=True; self.typing=False; self.entry.hide()
        self.title.setText(title); self.body.setPlainText(text)
        self.body.setVisible(bool(text) and not details); self.details.setVisible(details); self.details.setText('Technical details')
        self.deny.setVisible(approval); self.allow.setVisible(approval)
        self.present(Presentation.EXPANDED_DOCK)
        if approval:
            # Reveal immediately for keyboard access; decisions never wait on animation.
            self.animation.stop(); self.set_expansion(1.)
            self.activateWindow(); self.raise_(); self.deny.setFocus(Qt.OtherFocusReason)

    def clear_card(self):
        self._card_visible=False; self.card.hide(); self.refresh()

    def drag_by(self, delta):
        self.initialize_position(); self._origin += delta; self.refresh()

    def clamp(self):
        self.refresh()
        self._origin = self.mapToGlobal(self.orb.pos())

    def save_position(self):
        self.clamp(); self.preferences.setValue('adaptive_position',self._origin)

    def showEvent(self,event):
        super().showEvent(event); self.visibility_changed.emit(True)
    def hideEvent(self,event):
        super().hideEvent(event); self.visibility_changed.emit(False)
    def keyPressEvent(self,event):
        if event.key()==Qt.Key_Escape: self.dismiss_requested.emit()
        else: super().keyPressEvent(event)
