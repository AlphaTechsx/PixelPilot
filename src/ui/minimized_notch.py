from __future__ import annotations

import math
import time
from typing import Callable, Iterable, Optional

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen, QScreen
from PySide6.QtWidgets import QApplication, QWidget

from .glass import BackdropController, paint_glass_background


class MinimizedNotchWindow(QWidget):
    WIDTH = 404
    HEIGHT = 44
    TOP_MARGIN = 10
    ESSENTIAL_ACTIVITY_MARKERS = (
        "taking screenshot",
        "waiting ",
        "pressing key",
        "typing:",
        "clicking ",
        "opening application",
        "opening app:",
        "launching ",
        "live action ",
        "task completed",
        "task verified",
        "verifying task completion",
        "planning next action",
        "planning action",
        "executing sequence",
        "sequence step",
        "next action:",
        "steering update",
        "updated the steering",
        "updated the pending steering",
        "stopping...",
        "nothing to stop",
    )

    def __init__(self, *, enable_backdrop: bool = True) -> None:
        super().__init__(None)
        self.setWindowTitle("PixelPilotStatusNotch")

        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        if hasattr(Qt.WindowType, "WindowDoesNotAcceptFocus"):
            flags |= Qt.WindowType.WindowDoesNotAcceptFocus
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedSize(self.WIDTH, self.HEIGHT)

        font = self.font()
        font.setPointSize(9)
        font.setWeight(QFont.Weight.Medium)
        self.setFont(font)

        self._screen: Optional[QScreen] = None
        self._live_available = True
        self._live_state = "disconnected"
        self._live_voice_active = False
        self._status_text = "Pixel Pilot hidden"
        self._status_deadline = 0.0
        self._phase = 0.0
        self._progress_phase = 0.0
        self._user_audio_level = 0.0
        self._assistant_audio_level = 0.0
        self._user_visual_level = 0.0
        self._assistant_visual_level = 0.0
        self._excluded_hwnds_provider: Iterable[int] | Callable[[], Iterable[int]] | None = None
        self._backdrop_controller: BackdropController | None = None
        if enable_backdrop:
            self._backdrop_controller = BackdropController(self, steady_fps=8, burst_fps=12)
            self._backdrop_controller.register_widget(self)

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(16)
        self._tick_timer.timeout.connect(self._tick)

        self.hide()

    def set_excluded_hwnds_provider(self, provider: Iterable[int] | Callable[[], Iterable[int]] | None) -> None:
        self._excluded_hwnds_provider = provider
        if self._backdrop_controller is not None:
            self._backdrop_controller.set_excluded_hwnds(provider)

    def show_for_screen(self, screen: Optional[QScreen] = None) -> None:
        if screen is not None:
            self._screen = screen
        self.reposition()
        if not self.isVisible():
            self.show()
        if not self._tick_timer.isActive():
            self._tick_timer.start()
        if self._backdrop_controller is not None:
            self._backdrop_controller.trigger_burst()
            self._backdrop_controller.refresh_now()
        self.update()

    def hide_notch(self) -> None:
        self.hide()
        self._tick_timer.stop()

    def reposition(self, screen: Optional[QScreen] = None) -> None:
        target = screen or self._screen or QApplication.primaryScreen() or QGuiApplication.primaryScreen()
        if target is None:
            return
        self._screen = target
        geometry = target.availableGeometry()
        x = geometry.x() + (geometry.width() - self.width()) // 2
        y = geometry.y() + self.TOP_MARGIN
        self.move(x, y)

    def set_live_availability(self, available: bool, reason: str = "") -> None:
        self._live_available = bool(available)
        if not self._live_available:
            self._set_status(reason or "Gemini Live unavailable", duration_s=6.0)
        elif not self._status_is_active():
            self._use_default_status()

    def set_live_session_state(self, state: str) -> None:
        self._live_state = (state or "disconnected").strip().lower() or "disconnected"
        if not self._status_is_active():
            self._use_default_status()
            return
        if self._live_state in {"connecting", "thinking", "waiting", "acting"}:
            self.update()

    def set_live_voice_active(self, active: bool) -> None:
        self._live_voice_active = bool(active)
        if not self._live_voice_active and not self._status_is_active():
            self._use_default_status()

    def on_live_audio_level(self, level: float) -> None:
        self._user_audio_level = max(0.0, min(1.0, float(level or 0.0)))
        self._user_visual_level = max(self._user_visual_level, self._user_audio_level)
        self.update()

    def on_assistant_audio_level(self, level: float) -> None:
        self._assistant_audio_level = max(0.0, min(1.0, float(level or 0.0)))
        self._assistant_visual_level = max(self._assistant_visual_level, self._assistant_audio_level)
        self.update()

    def on_live_action_state(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            return
        message = str(payload.get("message") or "").strip()
        if message:
            self._set_status(message, duration_s=5.0)

    def on_live_transcript(self, speaker: str, text: str, final: bool) -> None:
        clean = self._normalize_text(text)
        if not clean:
            return
        duration_s = 3.5 if final else 1.2
        prefix = "You" if str(speaker or "").strip().lower() == "user" else "Pixie"
        self._set_status(f"{prefix}: {clean}", duration_s=duration_s)

    def on_user_command(self, text: str) -> None:
        clean = self._normalize_text(text)
        if clean:
            self._set_status(clean, duration_s=5.0)

    def add_activity_message(self, message: str) -> None:
        clean = self._normalize_text(message)
        if clean and self._should_show_activity(clean):
            self._set_status(clean, duration_s=4.5)

    def add_output_message(self, message: str) -> None:
        clean = self._normalize_text(message)
        if clean and self._should_show_output(clean):
            self._set_status(clean, duration_s=6.0)

    def add_final_answer(self, message: str) -> None:
        clean = self._normalize_text(message)
        if clean:
            self._set_status(clean, duration_s=8.0)

    def add_error_message(self, message: str) -> None:
        clean = self._normalize_text(message)
        if clean:
            self._set_status(clean, duration_s=6.0)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        mode = self._visual_mode()
        accent = self._accent_color(mode)
        glow_level = self._glow_level(mode)

        if glow_level > 0.01:
            for spread, alpha in ((15.0, 18), (9.0, 28), (5.0, 44)):
                glow_rect = rect.adjusted(-spread, -spread * 0.65, spread, spread * 0.85)
                glow_color = QColor(accent.red(), accent.green(), accent.blue(), int(alpha * min(1.0, glow_level)))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(glow_color)
                painter.drawRoundedRect(glow_rect, 16, 16)

        for spread, alpha in ((10.0, 10), (6.0, 16), (3.0, 24)):
            shadow_rect = rect.adjusted(-spread, -spread * 0.35, spread, spread * 0.65)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(15, 23, 42, alpha))
            painter.drawRoundedRect(shadow_rect, 16, 16)

        shell_path = self._notch_path(rect)
        paint_glass_background(
            painter,
            self,
            rect,
            controller=self._backdrop_controller,
            role="notch",
            corner_radius=15.0,
            accent=accent,
            draw_shadow=False,
        )

        inner_rect = rect.adjusted(4.0, 4.0, -4.0, -5.0)
        inner_gradient = QLinearGradient(inner_rect.topLeft(), inner_rect.bottomLeft())
        inner_gradient.setColorAt(0.0, self._blend(QColor(255, 255, 255, 92), accent, 0.12 if mode != "idle" else 0.04))
        inner_gradient.setColorAt(1.0, self._blend(QColor(255, 255, 255, 18), accent, 0.06 if mode != "idle" else 0.02))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(inner_gradient)
        painter.drawPath(self._notch_path(inner_rect))

        text_rect = rect.adjusted(16.0, 4.0, -16.0, -8.0)
        text_color = QColor("#0f172a")
        painter.setPen(text_color)
        metrics = QFontMetrics(self.font())
        label = metrics.elidedText(
            self._status_text or self._default_status_text(),
            Qt.TextElideMode.ElideRight,
            int(text_rect.width()),
        )
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)

        self._paint_progress_bar(painter, rect, accent)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.reposition()
        if not self._tick_timer.isActive():
            self._tick_timer.start()
        if self._backdrop_controller is not None:
            self._backdrop_controller.trigger_burst()
            QTimer.singleShot(0, self._backdrop_controller.refresh_now)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._tick_timer.stop()

    def _tick(self) -> None:
        self._phase = (self._phase + 2.1) % 360.0
        self._progress_phase = (self._progress_phase + 0.018) % 1.0
        self._user_visual_level = max(self._user_audio_level, self._user_visual_level * 0.84)
        self._assistant_visual_level = max(self._assistant_audio_level, self._assistant_visual_level * 0.84)
        if not self._status_is_active():
            self._use_default_status()
        self.update()
        if self._backdrop_controller is not None and self.isVisible() and (self._is_busy() or self._visual_mode() != "idle"):
            self._backdrop_controller.trigger_burst()

    def _paint_progress_bar(self, painter: QPainter, rect: QRectF, accent: QColor) -> None:
        bar_rect = QRectF(rect.left() + 16.0, rect.bottom() - 5.5, rect.width() - 32.0, 2.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 58))
        painter.drawRoundedRect(bar_rect, 1.6, 1.6)

        if not self._is_busy():
            static_color = QColor(accent.red(), accent.green(), accent.blue(), 76)
            painter.setBrush(static_color)
            painter.drawRoundedRect(QRectF(bar_rect.left(), bar_rect.top(), bar_rect.width() * 0.18, bar_rect.height()), 1.6, 1.6)
            return

        sweep_width = max(38.0, bar_rect.width() * 0.24)
        travel = max(1.0, bar_rect.width() - sweep_width)
        x = bar_rect.left() + travel * self._progress_phase
        sweep_rect = QRectF(x, bar_rect.top(), sweep_width, bar_rect.height())
        sweep_color = QColor(accent.red(), accent.green(), accent.blue(), 142)
        painter.setBrush(sweep_color)
        painter.drawRoundedRect(sweep_rect, 1.6, 1.6)

    def _status_is_active(self) -> bool:
        return self._status_deadline > time.monotonic()

    def _set_status(self, text: str, *, duration_s: float) -> None:
        clean = self._normalize_text(text)
        if not clean:
            return
        self._status_text = clean
        self._status_deadline = time.monotonic() + max(0.1, float(duration_s or 0.0))
        self.update()

    def _use_default_status(self) -> None:
        self._status_deadline = 0.0
        self._status_text = self._default_status_text()
        self.update()

    def _default_status_text(self) -> str:
        if not self._live_available:
            return "Gemini Live unavailable"

        state_map = {
            "connecting": "Connecting to Gemini Live...",
            "thinking": "Thinking...",
            "waiting": "Waiting for the current action...",
            "acting": "Working on the task...",
            "interrupted": "Interrupted. Waiting for your next instruction...",
            "listening": "Pixel Pilot hidden and ready.",
        }
        if self._live_voice_active:
            return "Listening..."
        return state_map.get(self._live_state, "Pixel Pilot hidden.")

    def _is_busy(self) -> bool:
        return self._live_state in {"connecting", "thinking", "waiting", "acting"}

    def _visual_mode(self) -> str:
        if self._assistant_visual_level > 0.035:
            return "assistant"
        if self._live_voice_active:
            return "user"
        if self._user_visual_level > 0.035:
            return "user"
        return "idle"

    def _glow_level(self, mode: str) -> float:
        if mode == "assistant":
            level = max(0.18, self._assistant_visual_level)
            return min(1.0, level + (math.sin(math.radians(self._phase * 1.4)) + 1.0) * 0.08)
        if mode == "user":
            level = max(0.15, self._user_visual_level)
            return min(1.0, level + (math.sin(math.radians(self._phase)) + 1.0) * 0.06)
        return 0.0

    @staticmethod
    def _accent_color(mode: str) -> QColor:
        if mode == "assistant":
            return QColor("#3b82f6")
        if mode == "user":
            return QColor("#22c55e")
        return QColor("#94a3b8")

    @staticmethod
    def _normalize_text(text: str) -> str:
        return " ".join(str(text or "").split())

    @classmethod
    def _should_show_activity(cls, text: str) -> bool:
        low = str(text or "").strip().lower()
        if not low:
            return False
        blocked = (
            "startup ",
            "startup phase=",
            "logging to:",
            "gateway listening",
            "pixel pilot gui shown",
            "ai agent initialized",
            "loaded app index",
            "app index warmup",
            "warn:",
            "gemini live unavailable",
        )
        if any(marker in low for marker in blocked):
            return False
        return any(marker in low for marker in cls.ESSENTIAL_ACTIVITY_MARKERS)

    @classmethod
    def _should_show_output(cls, text: str) -> bool:
        low = str(text or "").strip().lower()
        if not low:
            return False
        blocked_prefixes = (
            "startup ",
            "logging to:",
            "gateway listening",
            "pixel pilot gui shown",
            "loaded app index",
            "app index warmup",
            "ai agent initialized",
        )
        if low.startswith(blocked_prefixes):
            return False
        return False

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

    @staticmethod
    def _notch_path(rect: QRectF) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(rect, 15.0, 15.0)
        return path
