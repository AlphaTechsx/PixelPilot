import math

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient, QConicalGradient
from PySide6.QtWidgets import QWidget


class VoiceVisualizer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumHeight(140)
        self._level = 0.0
        self._angle = 0.0
        self._active = False

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def set_active(self, active: bool):
        self._active = active
        if active:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self.update()

    def set_level(self, level: float):
        level = max(0.0, min(1.0, level))
        self._level = level
        if self._active:
            self.update()

    def _tick(self):
        self._angle = (self._angle + 1.5 + self._level * 6.0) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = self.rect().adjusted(8, 8, -8, -8)
        center = rect.center()
        size = min(rect.width(), rect.height())
        base_radius = size * 0.35
        pulse = 1.0 + (self._level * 0.35)
        radius = base_radius * pulse

        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))

        glow = QRadialGradient(center, radius * 1.8)
        glow.setColorAt(0.0, QColor(5, 127, 202, int(120 + self._level * 80)))
        glow.setColorAt(0.5, QColor(5, 127, 202, int(40 + self._level * 40)))
        glow.setColorAt(1.0, QColor(5, 127, 202, 0))
        painter.setBrush(glow)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center, radius * 1.6, radius * 1.6)

        sphere = QRadialGradient(center.x() - radius * 0.35, center.y() - radius * 0.35, radius * 1.4)
        sphere.setColorAt(0.0, QColor(124, 199, 255, 230))
        sphere.setColorAt(0.4, QColor(5, 127, 202, 210))
        sphere.setColorAt(1.0, QColor(3, 56, 92, 230))
        painter.setBrush(sphere)
        painter.setPen(QPen(QColor(4, 91, 148, 200), 2))
        painter.drawEllipse(center, radius, radius)

        ring = QConicalGradient(center, self._angle)
        ring.setColorAt(0.0, QColor(255, 255, 255, int(40 + self._level * 120)))
        ring.setColorAt(0.15, QColor(5, 127, 202, int(160 + self._level * 80)))
        ring.setColorAt(0.3, QColor(255, 255, 255, 10))
        ring.setColorAt(1.0, QColor(255, 255, 255, 10))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(ring, max(2.0, radius * 0.08)))
        painter.drawEllipse(center, radius * 1.08, radius * 1.08)

        particles = int(6 + self._level * 10)
        for i in range(particles):
            angle = math.radians((self._angle * 2 + i * (360 / particles)) % 360)
            dist = radius * (1.2 + self._level * 0.4)
            px = center.x() + math.cos(angle) * dist
            py = center.y() + math.sin(angle) * dist
            painter.setBrush(QColor(5, 127, 202, int(50 + self._level * 120)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(int(px), int(py), 4, 4)
