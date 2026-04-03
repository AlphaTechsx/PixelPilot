import math

from PySide6.QtCore import QPointF, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QPushButton

from .glass import BackdropController, paint_glass_background


class AnimatedMicButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setText("")
        self.setFlat(True)
        self.setCheckable(False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._visual_state = "idle"
        self._level = 0.0
        self._phase = 0.0
        self._ripple_phase = 0.0
        self._hovered = False
        self._backdrop_controller: BackdropController | None = None

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(16)
        self._tick_timer.timeout.connect(self._tick)

    def set_backdrop_controller(self, controller: BackdropController | None) -> None:
        self._backdrop_controller = controller
        if controller is not None:
            controller.register_widget(self)
        self.update()

    def visual_state(self) -> str:
        return self._visual_state

    def level(self) -> float:
        return self._level

    def set_visual_state(self, state: str) -> None:
        target = (state or "idle").strip().lower() or "idle"
        if target not in {"idle", "listening_user", "speaking_assistant", "disabled"}:
            target = "idle"
        if self._visual_state == target:
            self._sync_timer()
            self.update()
            return
        self._visual_state = target
        self._sync_timer()
        self.update()

    def set_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, float(level or 0.0)))
        self._sync_timer()
        self.update()

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        return super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        return super().leaveEvent(event)

    def _sync_timer(self) -> None:
        active = self._visual_state in {"listening_user", "speaking_assistant"} or self._level > 0.01
        if active and not self._tick_timer.isActive():
            self._tick_timer.start()
        elif not active and self._tick_timer.isActive():
            self._tick_timer.stop()

    def _tick(self) -> None:
        if self._visual_state == "speaking_assistant":
            self._phase = (self._phase + 4.0 + self._level * 8.0) % 360
            self._ripple_phase = (self._ripple_phase + 0.08 + self._level * 0.12) % 1.0
        else:
            self._phase = (self._phase + 2.2 + self._level * 6.0) % 360
            self._ripple_phase = (self._ripple_phase + 0.04 + self._level * 0.05) % 1.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = self.rect().adjusted(1, 1, -1, -1)
        base_radius = min(rect.width(), rect.height()) * 0.33
        base_level = self._effective_level()
        pulse = 1.0 + math.sin(math.radians(self._phase)) * 0.05 + base_level * 0.08

        bg_color, border_color, glyph_color, glow_color = self._palette_for_state()
        glass_accent = glow_color if self.isEnabled() else QColor("#cbd5e1")
        if self._hovered and self._visual_state == "idle":
            glass_accent = self._blend(glass_accent, QColor("#dbe4ef"), 0.35)

        paint_glass_background(
            painter,
            self,
            rect,
            controller=self._backdrop_controller,
            role="control",
            corner_radius=11.0,
            accent=glass_accent,
        )

        center = rect.center()
        if self._visual_state == "listening_user":
            self._paint_listening_glow(painter, center, base_radius, pulse, glow_color, base_level)
        elif self._visual_state == "speaking_assistant":
            self._paint_speaking_ripples(painter, center, base_radius, pulse, glow_color, base_level)

        self._paint_core(painter, center, base_radius, pulse, bg_color, border_color, glyph_color)

    def _paint_core(
        self,
        painter: QPainter,
        center,
        base_radius: float,
        pulse: float,
        bg_color: QColor,
        border_color: QColor,
        glyph_color: QColor,
    ) -> None:
        radius = base_radius * pulse
        core_rect = self.rect().adjusted(7, 4, -7, -4)
        core_rect.moveCenter(center)
        core_fill = self._blend(bg_color, QColor("#ffffff"), 0.18)
        core_fill.setAlpha(84 if self.isEnabled() else 60)
        core_border = self._blend(border_color, QColor("#ffffff"), 0.18)
        core_border.setAlpha(140 if self.isEnabled() else 110)
        painter.setPen(QPen(core_border, 1.2))
        painter.setBrush(core_fill)
        painter.drawRoundedRect(core_rect, 10, 10)

        icon_rect = core_rect.adjusted(6, 3, -6, -3)
        path = self._microphone_path(icon_rect)
        painter.setPen(QPen(glyph_color, 1.9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        wave_pen = QPen(glyph_color, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        wave_pen.setColor(self._blend(glyph_color, QColor("#ffffff"), 0.15))
        painter.setPen(wave_pen)
        arc_margin = max(1.5, radius * 0.18)
        wave_rect = icon_rect.adjusted(-arc_margin, -arc_margin, arc_margin, arc_margin)
        start_angle = int((25 + math.sin(math.radians(self._phase)) * 4) * 16)
        span_angle = int((130 + self._effective_level() * 22) * 16)
        painter.drawArc(wave_rect, start_angle, span_angle)

    def _paint_listening_glow(
        self,
        painter: QPainter,
        center,
        base_radius: float,
        pulse: float,
        glow_color: QColor,
        level: float,
    ) -> None:
        outer_radius = base_radius * (1.3 + level * 0.35) * pulse
        ring_pen = QPen(glow_color, 2.2 + level * 1.5)
        ring_pen.setColor(QColor(glow_color.red(), glow_color.green(), glow_color.blue(), int(110 + level * 90)))
        painter.setPen(ring_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, outer_radius, outer_radius)

    def _paint_speaking_ripples(
        self,
        painter: QPainter,
        center,
        base_radius: float,
        pulse: float,
        glow_color: QColor,
        level: float,
    ) -> None:
        for offset, width in ((0.0, 1.6), (0.32, 1.2)):
            phase = (self._ripple_phase + offset) % 1.0
            radius = base_radius * (1.05 + phase * 0.95 + level * 0.28) * pulse
            alpha = int(max(0, 130 * (1.0 - phase)))
            pen = QPen(QColor(glow_color.red(), glow_color.green(), glow_color.blue(), alpha), width)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, radius, radius)

    def _microphone_path(self, rect) -> QPainterPath:
        cx = rect.center().x()
        top = rect.top() + rect.height() * 0.12
        bottom = rect.bottom() - rect.height() * 0.18
        capsule_width = rect.width() * 0.34
        capsule_height = rect.height() * 0.5
        capsule_rect = rect.adjusted(
            (rect.width() - capsule_width) / 2,
            rect.height() * 0.05,
            -(rect.width() - capsule_width) / 2,
            -(rect.height() - capsule_height) * 0.48,
        )

        path = QPainterPath()
        path.addRoundedRect(capsule_rect, capsule_width / 2, capsule_width / 2)
        path.moveTo(QPointF(cx, capsule_rect.bottom()))
        path.lineTo(QPointF(cx, bottom - rect.height() * 0.12))
        path.moveTo(QPointF(cx - rect.width() * 0.18, bottom))
        path.lineTo(QPointF(cx + rect.width() * 0.18, bottom))
        path.moveTo(QPointF(cx - rect.width() * 0.22, capsule_rect.bottom() - rect.height() * 0.04))
        path.quadTo(
            QPointF(cx, bottom - rect.height() * 0.02),
            QPointF(cx + rect.width() * 0.22, capsule_rect.bottom() - rect.height() * 0.04),
        )
        return path

    def _effective_level(self) -> float:
        if self._visual_state == "speaking_assistant":
            return max(0.26, self._level)
        if self._visual_state == "listening_user":
            return max(0.1, self._level)
        return self._level

    def _palette_for_state(self) -> tuple[QColor, QColor, QColor, QColor]:
        if not self.isEnabled() or self._visual_state == "disabled":
            return (
                QColor("#f1f5f9"),
                QColor("#d5dce7"),
                QColor("#94a3b8"),
                QColor("#cbd5e1"),
            )
        if self._visual_state == "listening_user":
            return (
                QColor("#e7f8ef"),
                QColor("#86efac"),
                QColor("#166534"),
                QColor("#22c55e"),
            )
        if self._visual_state == "speaking_assistant":
            return (
                QColor("#e7f0ff"),
                QColor("#93c5fd"),
                QColor("#1d4ed8"),
                QColor("#3b82f6"),
            )
        return (
            QColor("#eef2f7"),
            QColor("#d5dce7"),
            QColor("#475569"),
            QColor("#94a3b8"),
        )

    @staticmethod
    def _blend(left: QColor, right: QColor, weight: float) -> QColor:
        mix = max(0.0, min(1.0, float(weight)))
        inv = 1.0 - mix
        return QColor(
            int(left.red() * inv + right.red() * mix),
            int(left.green() * inv + right.green() * mix),
            int(left.blue() * inv + right.blue() * mix),
            int(left.alpha() * inv + right.alpha() * mix),
        )
