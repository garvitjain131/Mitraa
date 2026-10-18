"""One shared glass orb; independently moving procedural light layers."""
import math
from PySide6.QtCore import Qt, QRectF, QTimer, Signal, QPointF, QElapsedTimer
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient, QPainterPath, QLinearGradient
from PySide6.QtWidgets import QWidget

class Orb(QWidget):
    clicked = Signal()
    double_clicked = Signal()
    dragged = Signal(object)
    released = Signal()

    def __init__(self):
        super().__init__()
        self.setFixedSize(72, 72)
        self.state = 'idle'
        self.level = 0.0
        self.reduced = False
        self.muted = False
        self.anchor = None
        self.dragging = False
        self.clock = QElapsedTimer(); self.clock.start()
        self.timer = QTimer(self); self.timer.timeout.connect(self.tick); self.timer.start(33)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip('Click to expand · drag to move')
        self.setAccessibleName('Mitraa orb: click to expand')

    @property
    def phase(self):
        return 0.0 if self.reduced else self.clock.elapsed() / 1000

    def tick(self):
        if self.isVisible() and not self.reduced:
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.anchor = event.globalPosition().toPoint()
            self.dragging = False

    def mouseMoveEvent(self, event):
        if self.anchor is not None:
            point = event.globalPosition().toPoint()
            delta = point - self.anchor
            if self.dragging or delta.manhattanLength() > 4:
                self.dragging = True
                self.dragged.emit(delta)
                self.anchor = point

    def mouseReleaseEvent(self, event):
        if self.anchor is not None:
            if not self.dragging: self.clicked.emit()
            self.anchor = None
            self.released.emit()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            # Qt already delivered the first click. Suppress the second release
            # so collapsing cannot immediately reopen the dock.
            self.anchor = None
            self.dragging = False
            self.double_clicked.emit()
            event.accept()

    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        p.translate(36, 36)
        t = self.phase
        audio = min(1, (max(0, self.level) / .08) ** .5)
        recording = self.state == 'recording_speech'
        pulse = .5 + .5 * math.sin(t * (7 if self.state == 'speaking' else 1.7))
        intensity = .3 + (.7 * audio if recording else .15 * pulse)
        teal = QColor('#64a6a5' if self.muted else '#00e5ef')
        amber = QColor('#ffbd62')
        halo = QRadialGradient(0, 0, 36)
        halo.setColorAt(0, QColor(0, 0, 0, 0)); halo.setColorAt(.84, QColor(0, 0, 0, 0))
        halo.setColorAt(.91, QColor(0, 210, 222, int(35 + 65 * intensity))); halo.setColorAt(1, Qt.transparent)
        p.setPen(Qt.NoPen); p.setBrush(halo); p.drawEllipse(QRectF(-36, -36, 72, 72))
        glass = QRadialGradient(-12, -16, 58)
        glass.setColorAt(0, QColor(31, 76, 83, 240)); glass.setColorAt(.45, QColor(3, 17, 24, 248)); glass.setColorAt(1, QColor(5, 31, 35, 245))
        p.setBrush(glass); p.setPen(QPen(QColor(59, 161, 168, 170), .7)); p.drawEllipse(QRectF(-32.5, -32.5, 65, 65))
        p.save()
        clip = QPainterPath(); clip.addEllipse(QRectF(-32, -32, 64, 64)); p.setClipPath(clip)
        # These are real procedural layers, not claimed layers from a flattened PNG.
        for color, rotation, count in [(teal, t * 360 / 14, 20), (amber, 135 - t * 360 / 22, 14)]:
            p.save(); p.rotate(rotation)
            for i in range(count):
                path = QPainterPath()
                for j in range(85):
                    a = j / 84 * math.tau
                    r = 22 + 6 * math.sin(2 * a + i * .24) + 3 * math.sin(3 * a + i * .43)
                    point = QPointF(r * math.cos(a + i * .043), r * math.sin(a + i * .043))
                    if j == 0: path.moveTo(point)
                    else: path.lineTo(point)
                ink = QColor(color); ink.setAlpha(min(210, int((30 + i * 5) * (.65 + intensity))))
                p.setPen(QPen(ink, .38 + .028 * i)); p.setBrush(Qt.NoBrush); p.drawPath(path)
            # Broad translucent current beneath the fine filaments.
            for i in range(4):
                wisp = QPainterPath(QPointF(-10-i, -29))
                wisp.cubicTo(32, -32, 39, 7+i, 11, 18)
                wisp.cubicTo(-13, 31, -34, 4, -17, -11)
                ink = QColor(color); ink.setAlpha(18 + i*8)
                p.setPen(QPen(ink, 2.8-i*.5)); p.drawPath(wisp)
            spark = QRadialGradient(17, -17, 9)
            light = QColor(color); light.setAlpha(175)
            spark.setColorAt(0,light); spark.setColorAt(.2,QColor(color.red(),color.green(),color.blue(),70)); spark.setColorAt(1,Qt.transparent)
            p.setBrush(spark); p.setPen(Qt.NoPen); p.drawEllipse(QRectF(8,-26,18,18))
            p.restore()
        p.restore()
        # Steady glass edge and highlight, never rotating with the currents.
        edge = QLinearGradient(-28, -28, 28, 28)
        edge.setColorAt(0, QColor('#7ce3e7')); edge.setColorAt(.4, QColor('#1a6268')); edge.setColorAt(.8, QColor('#f2bd6a')); edge.setColorAt(1, QColor('#25585e'))
        p.setBrush(Qt.NoBrush); p.setPen(QPen(edge, .8)); p.drawEllipse(QRectF(-32, -32, 64, 64))
        glint = QRadialGradient(-12, -26, 38)
        glint.setColorAt(0, QColor(181, 247, 250, 110)); glint.setColorAt(.8, Qt.transparent)
        p.setPen(Qt.NoPen); p.setBrush(glint); p.drawEllipse(QRectF(-28, -30, 52, 21))
        accent = '#ffb85e' if self.state == 'awaiting_approval' else ('#f18c87' if self.state == 'error' else '#52d8e0')
        p.setPen(QPen(QColor(accent), 1 if not recording else 1 + audio)); p.setBrush(Qt.NoBrush)
        if self.state in ('loading_voice', 'transcribing', 'thinking', 'working'):
            p.drawArc(QRectF(-34, -34, 68, 68), int((0 if self.reduced else -t * 80) * 16), 65 * 16)
        elif self.state in ('recording_speech', 'speaking', 'awaiting_approval', 'error'):
            p.drawArc(QRectF(-34, -34, 68, 68), -30 * 16, int((100 + 95 * (audio if recording else pulse)) * 16))
        if self.muted:
            p.setPen(QPen(QColor('#96acb0'), 1)); p.drawLine(-5, 3, 5, -3)
