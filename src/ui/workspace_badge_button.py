from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QPushButton


class WorkspaceBadgeButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setText("")
        self.setFlat(True)
        self.setCheckable(False)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._workspace = "user"
        self._clickable = False
        self._agent_view_shown = False
        self._hovered = False
        self.setProperty("workspace", self._workspace)

    def workspace(self) -> str:
        return self._workspace

    def set_workspace(self, workspace: str) -> None:
        key = (workspace or "user").strip().lower() or "user"
        if key not in {"user", "agent"}:
            key = "user"
        if key == self._workspace:
            return
        self._workspace = key
        self.setProperty("workspace", key)
        self.update()

    def set_clickable(self, clickable: bool) -> None:
        target = bool(clickable)
        if target == self._clickable:
            return
        self._clickable = target
        self.setCursor(Qt.CursorShape.PointingHandCursor if target else Qt.CursorShape.ArrowCursor)
        self.update()

    def set_agent_view_shown(self, shown: bool) -> None:
        target = bool(shown)
        if target == self._agent_view_shown:
            return
        self._agent_view_shown = target
        self.update()

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        return super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        return super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = self.rect().adjusted(1, 1, -1, -1)
        bg_color, border_color, glyph_color = self._palette()

        painter.setPen(QPen(border_color, 1.25))
        painter.setBrush(bg_color)
        painter.drawRoundedRect(rect, 10, 10)

        core_rect = rect.adjusted(9, 6, -9, -6)
        icon_pen = QPen(glyph_color, 1.45, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(icon_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if self._workspace == "agent":
            self._draw_agent_icon(painter, core_rect)
        else:
            self._draw_user_icon(painter, core_rect)

    def _draw_user_icon(self, painter: QPainter, rect: QRectF) -> None:
        cx = rect.center().x()
        h = rect.height()
        w = rect.width()

        head_radius = max(2.2, h * 0.18)
        head_center = QPointF(cx, rect.top() + h * 0.32)
        painter.drawEllipse(head_center, head_radius, head_radius)

        shoulders = QPainterPath()
        left = QPointF(cx - w * 0.30, rect.bottom() - h * 0.10)
        right = QPointF(cx + w * 0.30, rect.bottom() - h * 0.10)
        control = QPointF(cx, rect.top() + h * 0.56)
        shoulders.moveTo(left)
        shoulders.quadTo(control, right)
        painter.drawPath(shoulders)

    def _draw_agent_icon(self, painter: QPainter, rect: QRectF) -> None:
        cx = rect.center().x()
        h = rect.height()
        w = rect.width()

        head = QRectF(
            cx - w * 0.33,
            rect.top() + h * 0.22,
            w * 0.66,
            h * 0.58,
        )
        painter.drawRoundedRect(head, 2.2, 2.2)

        antenna_top = QPointF(cx, head.top() - h * 0.18)
        antenna_base = QPointF(cx, head.top())
        painter.drawLine(antenna_top, antenna_base)
        painter.drawEllipse(antenna_top, max(1.0, h * 0.06), max(1.0, h * 0.06))

        eye_radius = max(1.0, h * 0.07)
        eye_y = head.top() + head.height() * 0.44
        left_eye = QPointF(cx - w * 0.14, eye_y)
        right_eye = QPointF(cx + w * 0.14, eye_y)
        painter.drawEllipse(left_eye, eye_radius, eye_radius)
        painter.drawEllipse(right_eye, eye_radius, eye_radius)

        mouth_y = head.bottom() - head.height() * 0.20
        painter.drawLine(
            QPointF(cx - w * 0.13, mouth_y),
            QPointF(cx + w * 0.13, mouth_y),
        )

    def _palette(self) -> tuple[QColor, QColor, QColor]:
        if not self.isEnabled():
            return (QColor("#f4f6f8"), QColor("#e3e7ee"), QColor("#a1acba"))

        bg = QColor("#ffffff")
        border = QColor("#d2dae5")
        glyph = QColor("#64748b")

        if self._agent_view_shown:
            bg = QColor("#f8fafc")
            border = QColor("#94a3b8")
            glyph = QColor("#475569")
        elif self._hovered and self._clickable:
            bg = QColor("#f8fafc")
            border = QColor("#94a3b8")
            glyph = QColor("#5b6678")

        if self.isDown() and self._clickable:
            bg = QColor("#eef2f7")
            border = QColor("#94a3b8")
            glyph = QColor("#475569")

        return (bg, border, glyph)
