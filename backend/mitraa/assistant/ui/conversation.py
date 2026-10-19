"""Presentation-only history and composer. All content is escaped before rendering."""
from datetime import datetime
from html import escape
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget,QTextBrowser,QPlainTextEdit,QPushButton,QLabel,QVBoxLayout,QHBoxLayout
from mitraa.assistant.ui.controls import control_icon

class Composer(QPlainTextEdit):
    send_requested=Signal()
    typing_started=Signal()
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setPlaceholderText('Message Mitraa…'); self.setAccessibleName('Message Mitraa')
        self.setTabChangesFocus(True)
        self.document().setDocumentMargin(2)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.textChanged.connect(self.grow)
        self.grow()
    def grow(self):
        line=self.fontMetrics().lineSpacing()
        lines=max(self.blockCount(),int(self.document().size().height()))
        self.setFixedHeight(min(line*4+20,max(line+20,lines*line+20)))
        if self.hasFocus(): self.typing_started.emit()
    def keyPressEvent(self,event):
        if event.key() in (Qt.Key_Return,Qt.Key_Enter) and not event.modifiers() & Qt.ShiftModifier:
            self.send_requested.emit(); event.accept(); return
        super().keyPressEvent(event)
    def mousePressEvent(self,event):
        # Voice overlays show without activating; typing is an explicit focus action.
        self.window().activateWindow(); self.window().raise_()
        super().mousePressEvent(event)
        self.setFocus(Qt.MouseFocusReason)

class ConversationPanel(QWidget):
    submitted=Signal(str)
    typing_started=Signal()
    older_requested=Signal()
    def __init__(self,parent=None):
        super().__init__(parent)
        layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(7)
        self.older=QPushButton('Load earlier messages'); self.older.clicked.connect(self.older_requested)
        layout.addWidget(self.older); self.older.hide()
        self.messages=QTextBrowser(); self.messages.setOpenLinks(False); self.messages.setOpenExternalLinks(False)
        self.messages.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.messages.setAccessibleName('Conversation messages')
        self.messages.setStyleSheet('QTextBrowser { font-size:12px; color:#e2ecee; }')
        layout.addWidget(self.messages,1)
        self.notice=QLabel(''); self.notice.setWordWrap(True); self.notice.setStyleSheet('color:#8bb4c0;')
        layout.addWidget(self.notice); self.notice.hide()
        self.new_messages=QPushButton('New messages ↓'); self.new_messages.clicked.connect(self.latest)
        layout.addWidget(self.new_messages); self.new_messages.hide()
        self.messages.verticalScrollBar().valueChanged.connect(self.scrolled)
        row=QHBoxLayout(); row.setSpacing(7)
        self.composer=Composer(); self.composer.setStyleSheet('QPlainTextEdit { background:rgba(16,37,44,190); border:1px solid #30505a; border-radius:15px; padding:5px; color:#e2ecee; } QPlainTextEdit:focus { border-color:#38c9d0; }')
        self.composer.typing_started.connect(self.typing_started)
        self.send=QPushButton(); self.send.setFixedSize(30,30); self.send.setIcon(control_icon('send'))
        self.send.setToolTip('Send message'); self.send.setAccessibleName('Send message')
        self.send.setStyleSheet('QPushButton { background:#008c9b; border-color:#08c2d0; } QPushButton:focus { border:2px solid #f0cf8f; } QPushButton:disabled { background:#17363c; border-color:#30505a; }')
        row.addWidget(self.composer,1); row.addWidget(self.send,0,Qt.AlignBottom); layout.addLayout(row)
        self.allowed=True
        self.send.clicked.connect(self.submit); self.composer.send_requested.connect(self.submit)
        self.composer.textChanged.connect(self.update_send); self.update_send()
    def submit(self):
        if self.allowed and self.composer.toPlainText().strip(): self.submitted.emit(self.composer.toPlainText())
    def update_send(self):
        self.send.setEnabled(self.allowed and bool(self.composer.toPlainText().strip()))
    def set_available(self,allowed,pending=False,error=''):
        self.allowed=allowed; self.update_send()
        self.notice.setText(error or ('Waiting for Mitraa…' if pending else ''))
        self.notice.setVisible(bool(self.notice.text()))
    def latest(self):
        bar=self.messages.verticalScrollBar(); bar.setValue(bar.maximum()); self.new_messages.hide()
    def scrolled(self,value):
        if self.messages.verticalScrollBar().maximum()-value<=24: self.new_messages.hide()
    def render(self,records,*,prepend=False,initial=False):
        bar=self.messages.verticalScrollBar(); old=bar.value(); maximum=bar.maximum()
        follow=initial or maximum-old<=24
        parts=[]
        for record in records:
            try: stamp=datetime.fromisoformat(record['created_at']).astimezone().strftime('%H:%M')
            except (ValueError,KeyError): stamp=''
            role='You' if record['role']=='user' else 'Mitraa'
            header=f'<span style="color:#9cbac2;">{role} · {escape(stamp)}</span>'
            content=escape(record['content']).replace('\n','<br>')
            background=' bgcolor="#193039"' if record['role']=='user' else ''
            parts.append(f'<table width="100%" cellspacing="0" cellpadding="9"{background}><tr><td>{header}<br>{content}</td></tr></table><div style="font-size:5px;">&nbsp;</div>')
        self.messages.setHtml(''.join(parts) or '<p style="color:#8bb4c0;">No messages yet.</p>')
        if prepend: bar.setValue(old+bar.maximum()-maximum)
        elif follow: self.latest()
        else: bar.setValue(old); self.new_messages.show()
