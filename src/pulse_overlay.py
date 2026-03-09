from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, Property, QSequentialAnimationGroup, QTimer
from PySide6.QtGui import QPainter, QColor, QRadialGradient


class PulseOverlay(QWidget):
    def __init__(self, parent, on_done):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._radius = 0
        self.on_done = on_done

        # Single pulse: grow then shrink
        self.anim = QSequentialAnimationGroup()

        grow = QPropertyAnimation(self, b"radius")
        grow.setDuration(200)
        grow.setStartValue(0)
        grow.setEndValue(60)
        grow.setEasingCurve(QEasingCurve.Type.OutQuad)

        shrink = QPropertyAnimation(self, b"radius")
        shrink.setDuration(200)
        shrink.setStartValue(60)
        shrink.setEndValue(30)
        shrink.setEasingCurve(QEasingCurve.Type.InQuad)


        self.anim.addAnimation(grow)
        self.anim.addAnimation(shrink)

        self.anim.finished.connect(self._done)

    def get_radius(self): return self._radius
    def set_radius(self, r):
        self._radius = r
        self.update()
    radius = Property(int, get_radius, set_radius)

    def paintEvent(self, event):
        if self._radius == 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = self.rect().center()
        grad = QRadialGradient(center, self._radius)
        grad.setColorAt(0, QColor(0, 255, 153, 120))
        grad.setColorAt(0.6, QColor(0, 255, 153, 40))
        grad.setColorAt(1, QColor(0, 255, 153, 0))
        painter.setBrush(grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center, self._radius, self._radius)

    def play(self):
        self.resize(self.parent().size())
        self.raise_()
        self.show()
        self.anim.start()

    def _done(self):
        self.hide()
        self._radius = 0
        self.on_done()
