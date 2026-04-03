import math

from PySide6.QtCore import QEvent, QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QPushButton

from .glass import BackdropController, paint_glass_background


class AnimatedLiveButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setText("")
        self.setFlat(True)
        self.setCheckable(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._visual_state = "off"
        self._phase = 0.0
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

    def set_visual_state(self, state: str) -> None:
        target = (state or "off").strip().lower() or "off"
        valid = {
            "disabled",
            "off",
            "ready",
            "connected",
            "connecting",
            "thinking",
            "waiting",
            "acting",
            "interrupted",
        }
        if target not in valid:
            target = "off"
        if target == self._visual_state:
            self._sync_timer()
            self.update()
            return
        self._visual_state = target
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

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            self._sync_timer()
            self.update()

    def _sync_timer(self) -> None:
        active = self.isEnabled() and self._visual_state in {"connecting", "thinking", "waiting", "acting"}
        if active and not self._tick_timer.isActive():
            self._tick_timer.start()
        elif not active and self._tick_timer.isActive():
            self._tick_timer.stop()

    def _tick(self) -> None:
        speed = {
            "connecting": 8.0,
            "thinking": 4.8,
            "waiting": 2.6,
            "acting": 9.2,
        }.get(self._visual_state, 0.0)
        self._phase = (self._phase + speed) % 360.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = self.rect().adjusted(1, 1, -1, -1)
        bg_color, border_color, glyph_color, accent_color = self._palette_for_state()

        glass_accent = accent_color if self.isEnabled() else QColor("#cbd5e1")
        if self._hovered and self.isEnabled():
            glass_accent = self._blend(glass_accent, QColor("#ffffff"), 0.16)
        if self.isDown() and self.isEnabled():
            glass_accent = self._blend(glass_accent, QColor("#0f172a"), 0.12)

        paint_glass_background(
            painter,
            self,
            QRectF(rect),
            controller=self._backdrop_controller,
            role="control",
            corner_radius=11.0,
            accent=glass_accent,
        )

        core_rect = rect.adjusted(7, 4, -7, -4)
        core_fill = self._blend(bg_color, QColor("#ffffff"), 0.22 if self._hovered else 0.14)
        core_fill.setAlpha(86 if self.isEnabled() else 66)
        core_border = self._blend(border_color, QColor("#ffffff"), 0.18)
        core_border.setAlpha(138 if self.isEnabled() else 112)
        painter.setPen(QPen(core_border, 1.0))
        painter.setBrush(core_fill)
        painter.drawRoundedRect(core_rect, 9, 9)

        center = core_rect.center()
        radius = min(core_rect.width(), core_rect.height()) * 0.23
        state = self._visual_state
        if not self.isEnabled():
            state = "disabled"

        if state == "disabled":
            self._draw_disabled_icon(painter, center, radius, glyph_color)
        elif state == "off":
            self._draw_off_icon(painter, center, radius, glyph_color)
        elif state == "ready":
            self._draw_ready_icon(painter, center, radius, glyph_color, accent_color)
        elif state == "connected":
            self._draw_connected_icon(painter, center, radius, glyph_color, accent_color)
        elif state == "connecting":
            self._draw_connecting_icon(painter, center, radius, glyph_color, accent_color)
        elif state == "thinking":
            self._draw_thinking_icon(painter, center, radius, glyph_color, accent_color)
        elif state == "waiting":
            self._draw_waiting_icon(painter, center, radius, glyph_color)
        elif state == "acting":
            self._draw_acting_icon(painter, center, radius, glyph_color, accent_color)
        else:
            self._draw_interrupted_icon(painter, center, radius, glyph_color)

        if self.hasFocus():
            focus_pen = QPen(self._blend(accent_color, QColor("#ffffff"), 0.2), 1.0)
            focus_pen.setStyle(Qt.PenStyle.DotLine)
            painter.setPen(focus_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 9, 9)

    def _draw_off_icon(self, painter: QPainter, center: QPointF, radius: float, glyph: QColor) -> None:
        ring = QRectF(
            center.x() - radius * 1.45,
            center.y() - radius * 1.45,
            radius * 2.9,
            radius * 2.9,
        )
        painter.setPen(QPen(glyph, 1.75, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(ring, 42 * 16, 276 * 16)
        painter.drawLine(QPointF(center.x(), ring.top() - 0.2), QPointF(center.x(), center.y() - radius * 0.18))

    def _draw_ready_icon(
        self,
        painter: QPainter,
        center: QPointF,
        radius: float,
        glyph: QColor,
        accent: QColor,
    ) -> None:
        ring = QRectF(
            center.x() - radius * 1.35,
            center.y() - radius * 1.35,
            radius * 2.7,
            radius * 2.7,
        )
        painter.setPen(QPen(glyph, 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(ring)

        spark = QPointF(center.x() + radius * 1.05, center.y() - radius * 1.02)
        spark_radius = max(1.3, radius * 0.34)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        painter.drawEllipse(spark, spark_radius, spark_radius)

        painter.setPen(QPen(accent, 1.15, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        cross = spark_radius * 1.55
        painter.drawLine(
            QPointF(spark.x() - cross, spark.y()),
            QPointF(spark.x() + cross, spark.y()),
        )
        painter.drawLine(
            QPointF(spark.x(), spark.y() - cross),
            QPointF(spark.x(), spark.y() + cross),
        )

    def _draw_connected_icon(
        self,
        painter: QPainter,
        center: QPointF,
        radius: float,
        glyph: QColor,
        accent: QColor,
    ) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glyph)
        core_radius = max(1.45, radius * 0.43)
        painter.drawEllipse(center, core_radius, core_radius)

        for scale, alpha in ((1.16, 215), (1.82, 150)):
            ring_color = QColor(accent.red(), accent.green(), accent.blue(), alpha)
            pen = QPen(ring_color, 1.45, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            ring = QRectF(
                center.x() - radius * scale,
                center.y() - radius * scale,
                radius * scale * 2,
                radius * scale * 2,
            )
            painter.drawArc(ring, 32 * 16, 112 * 16)
            painter.drawArc(ring, 212 * 16, 112 * 16)

    def _draw_connecting_icon(
        self,
        painter: QPainter,
        center: QPointF,
        radius: float,
        glyph: QColor,
        accent: QColor,
    ) -> None:
        spinner = QRectF(
            center.x() - radius * 1.9,
            center.y() - radius * 1.9,
            radius * 3.8,
            radius * 3.8,
        )
        start = int(self._phase * 16)
        pen = QPen(accent, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(spinner, start, 225 * 16)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glyph)
        painter.drawEllipse(center, max(1.3, radius * 0.42), max(1.3, radius * 0.42))

    def _draw_thinking_icon(
        self,
        painter: QPainter,
        center: QPointF,
        radius: float,
        glyph: QColor,
        accent: QColor,
    ) -> None:
        active_index = int((self._phase / 120.0) % 3)
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            is_active = i == active_index
            color = self._blend(glyph, accent, 0.58 if is_active else 0.2)
            dot_radius = radius * (0.46 if is_active else 0.34)
            x = center.x() + (i - 1) * radius * 1.2
            painter.setBrush(color)
            painter.drawEllipse(QPointF(x, center.y()), dot_radius, dot_radius)

    def _draw_waiting_icon(self, painter: QPainter, center: QPointF, radius: float, glyph: QColor) -> None:
        clock = QRectF(
            center.x() - radius * 1.5,
            center.y() - radius * 1.5,
            radius * 3,
            radius * 3,
        )
        painter.setPen(QPen(glyph, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(clock)

        angle = math.radians((self._phase * 0.9) % 360)
        hand = QPointF(
            center.x() + math.cos(angle) * radius * 0.95,
            center.y() - math.sin(angle) * radius * 0.95,
        )
        painter.setPen(QPen(glyph, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(center, hand)
        painter.drawLine(center, QPointF(center.x(), center.y() - radius * 0.58))

    def _draw_acting_icon(
        self,
        painter: QPainter,
        center: QPointF,
        radius: float,
        glyph: QColor,
        accent: QColor,
    ) -> None:
        arc = QRectF(
            center.x() - radius * 2.0,
            center.y() - radius * 2.0,
            radius * 4.0,
            radius * 4.0,
        )
        pen = QPen(accent, 1.55, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(arc, int((self._phase * 1.45) * 16), 138 * 16)

        x = center.x()
        y = center.y()
        bolt = QPainterPath()
        bolt.moveTo(QPointF(x - radius * 0.26, y - radius * 1.06))
        bolt.lineTo(QPointF(x + radius * 0.33, y - radius * 0.28))
        bolt.lineTo(QPointF(x - radius * 0.04, y - radius * 0.06))
        bolt.lineTo(QPointF(x + radius * 0.24, y + radius * 1.0))
        bolt.lineTo(QPointF(x - radius * 0.46, y + radius * 0.14))
        bolt.lineTo(QPointF(x - radius * 0.11, y - radius * 0.08))
        bolt.closeSubpath()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glyph)
        painter.drawPath(bolt)

    def _draw_interrupted_icon(self, painter: QPainter, center: QPointF, radius: float, glyph: QColor) -> None:
        ring = QRectF(
            center.x() - radius * 1.5,
            center.y() - radius * 1.5,
            radius * 3,
            radius * 3,
        )
        painter.setPen(QPen(glyph, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(ring)

        bar_w = max(1.45, radius * 0.34)
        bar_h = radius * 1.58
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glyph)
        for dx in (-radius * 0.42, radius * 0.42):
            bar = QRectF(center.x() + dx - bar_w / 2, center.y() - bar_h / 2, bar_w, bar_h)
            painter.drawRoundedRect(bar, 0.8, 0.8)

    def _draw_disabled_icon(self, painter: QPainter, center: QPointF, radius: float, glyph: QColor) -> None:
        self._draw_off_icon(painter, center, radius, glyph)
        painter.setPen(QPen(glyph, 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(
            QPointF(center.x() - radius * 1.55, center.y() + radius * 1.42),
            QPointF(center.x() + radius * 1.55, center.y() - radius * 1.42),
        )

    def _palette_for_state(self) -> tuple[QColor, QColor, QColor, QColor]:
        if not self.isEnabled() or self._visual_state == "disabled":
            return (
                QColor("#f4f6f8"),
                QColor("#e3e7ee"),
                QColor("#94a3b8"),
                QColor("#cbd5e1"),
            )
        if self._visual_state == "off":
            return (
                QColor("#ffffff"),
                QColor("#d2dae5"),
                QColor("#64748b"),
                QColor("#94a3b8"),
            )
        if self._visual_state == "ready":
            return (
                QColor("#ecfeff"),
                QColor("#67e8f9"),
                QColor("#0f766e"),
                QColor("#06b6d4"),
            )
        if self._visual_state == "connected":
            return (
                QColor("#ecfdf3"),
                QColor("#86efac"),
                QColor("#166534"),
                QColor("#22c55e"),
            )
        if self._visual_state in {"connecting", "thinking"}:
            return (
                QColor("#eff6ff"),
                QColor("#93c5fd"),
                QColor("#1d4ed8"),
                QColor("#3b82f6"),
            )
        if self._visual_state == "waiting":
            return (
                QColor("#f8fafc"),
                QColor("#d3dce8"),
                QColor("#475569"),
                QColor("#94a3b8"),
            )
        return (
            QColor("#fff7ed"),
            QColor("#fdba74"),
            QColor("#c2410c"),
            QColor("#fb923c"),
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
