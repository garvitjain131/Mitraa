"""Vector controls and a microphone-driven waveform; no emoji glyphs."""
from collections import deque
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QIcon, QPixmap
from PySide6.QtWidgets import QWidget


def control_icon(kind):
    image = QPixmap(24, 24); image.fill(Qt.transparent)
    p = QPainter(image); p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor('#14dbe7' if kind == 'microphone' else '#d8e4e6'), 1.5, Qt.SolidLine, Qt.RoundCap)); p.setBrush(Qt.NoBrush)
    if kind == 'keyboard':
        p.drawRoundedRect(QRectF(3, 5, 18, 14), 2, 2)
        for y in (9, 12):
            for x in (7, 10, 13, 16): p.drawPoint(x, y)
        p.drawLine(8, 16, 16, 16)
    elif kind == 'microphone':
        p.drawRoundedRect(QRectF(9, 3, 6, 11), 3, 3)
        p.drawArc(QRectF(6, 7, 12, 11), 180 * 16, 180 * 16)
        p.drawLine(12, 18, 12, 22)
    else:
        p.drawLine(6, 6, 18, 18); p.drawLine(18, 6, 6, 18)
    p.end(); return QIcon(image)

class Waveform(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent); self.levels = deque([0.] * 32, maxlen=32)
        self.setFixedHeight(20)

    def feed(self, level):
        self.levels.append(min(1, (max(0, level) / .08) ** .5)); self.update()

    def clear(self):
        self.levels = deque([0.] * 32, maxlen=32); self.update()

    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor('#00dbe8'), 2, Qt.SolidLine, Qt.RoundCap))
        step = max(2, self.width() / 33)
        for i, level in enumerate(self.levels):
            height = 1 + level * 15
            x = (i + 1) * step
            p.drawLine(int(x), int((20-height)/2), int(x), int((20+height)/2))
