from __future__ import annotations

import ctypes
import sys
import time
import weakref
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

import numpy as np
from PySide6.QtCore import QObject, QPoint, QRect, QRectF, Qt, QEvent, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPalette
from PySide6.QtWidgets import QComboBox, QFrame, QPushButton, QStyle, QStyleOptionButton, QWidget


if sys.platform.startswith("win"):
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    try:
        dwmapi = ctypes.windll.dwmapi
    except Exception:
        dwmapi = None

    SRCCOPY = 0x00CC0020
    PW_RENDERFULLCONTENT = 0x00000002
    GW_HWNDNEXT = 2
    BI_RGB = 0
    DIB_RGB_COLORS = 0
    DWMWA_CLOAKED = 14

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [
            ("bmiHeader", BITMAPINFOHEADER),
            ("bmiColors", wintypes.DWORD * 3),
        ]


@dataclass(frozen=True)
class GlassSpec:
    role: str
    blur_radius: int
    tint: tuple[int, int, int, int]
    border: tuple[int, int, int, int]
    highlight_top: tuple[int, int, int, int]
    highlight_bottom: tuple[int, int, int, int]
    shadow_alpha: int
    noise_alpha: int


GLASS_SPECS: dict[str, GlassSpec] = {
    "shell": GlassSpec(
        role="shell",
        blur_radius=18,
        tint=(248, 250, 252, 160),
        border=(255, 255, 255, 172),
        highlight_top=(255, 255, 255, 74),
        highlight_bottom=(255, 255, 255, 8),
        shadow_alpha=24,
        noise_alpha=10,
    ),
    "control": GlassSpec(
        role="control",
        blur_radius=12,
        tint=(255, 255, 255, 190),
        border=(255, 255, 255, 184),
        highlight_top=(255, 255, 255, 84),
        highlight_bottom=(255, 255, 255, 10),
        shadow_alpha=18,
        noise_alpha=9,
    ),
    "content": GlassSpec(
        role="content",
        blur_radius=14,
        tint=(255, 255, 255, 205),
        border=(255, 255, 255, 178),
        highlight_top=(255, 255, 255, 78),
        highlight_bottom=(255, 255, 255, 10),
        shadow_alpha=20,
        noise_alpha=8,
    ),
    "notch": GlassSpec(
        role="notch",
        blur_radius=16,
        tint=(248, 250, 252, 170),
        border=(255, 255, 255, 184),
        highlight_top=(255, 255, 255, 92),
        highlight_bottom=(255, 255, 255, 12),
        shadow_alpha=24,
        noise_alpha=10,
    ),
}


_NOISE_CACHE: dict[tuple[int, int], QImage] = {}


def glass_spec(role_or_spec: str | GlassSpec) -> GlassSpec:
    if isinstance(role_or_spec, GlassSpec):
        return role_or_spec
    return GLASS_SPECS.get(str(role_or_spec or "shell").strip().lower(), GLASS_SPECS["shell"])


def _qcolor(color: tuple[int, int, int, int] | QColor) -> QColor:
    if isinstance(color, QColor):
        return QColor(color)
    r, g, b, a = color
    return QColor(int(r), int(g), int(b), int(a))


def _blend(left: QColor, right: QColor, weight: float) -> QColor:
    mix = max(0.0, min(1.0, float(weight)))
    inv = 1.0 - mix
    return QColor(
        int(left.red() * inv + right.red() * mix),
        int(left.green() * inv + right.green() * mix),
        int(left.blue() * inv + right.blue() * mix),
        int(left.alpha() * inv + right.alpha() * mix),
    )


def _qimage_to_rgba_array(image: QImage) -> np.ndarray:
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width = converted.width()
    height = converted.height()
    ptr = converted.bits()
    buffer = ptr[: converted.sizeInBytes()]
    array = np.frombuffer(buffer, dtype=np.uint8)
    array = array.reshape((height, converted.bytesPerLine()))
    return array[:, : width * 4].reshape((height, width, 4)).copy()


def _rgba_array_to_qimage(array: np.ndarray) -> QImage:
    contiguous = np.ascontiguousarray(array.astype(np.uint8, copy=False))
    height, width, _ = contiguous.shape
    image = QImage(contiguous.data, width, height, width * 4, QImage.Format.Format_RGBA8888)
    return image.copy()


def _box_blur_axis(data: np.ndarray, radius: int, axis: int) -> np.ndarray:
    if radius <= 0:
        return data.astype(np.float32, copy=True)
    pad = [(0, 0)] * data.ndim
    pad[axis] = (radius, radius)
    padded = np.pad(data.astype(np.float32, copy=False), pad, mode="edge")
    cumulative = np.cumsum(padded, axis=axis, dtype=np.float32)
    head_shape = list(cumulative.shape)
    head_shape[axis] = 1
    zeros = np.zeros(head_shape, dtype=np.float32)
    cumulative = np.concatenate([zeros, cumulative], axis=axis)
    window = radius * 2 + 1
    hi = [slice(None)] * data.ndim
    lo = [slice(None)] * data.ndim
    hi[axis] = slice(window, None)
    lo[axis] = slice(None, -window)
    return (cumulative[tuple(hi)] - cumulative[tuple(lo)]) / float(window)


def triple_box_blur_rgba(array: np.ndarray, radius: int) -> np.ndarray:
    radius = max(0, int(radius or 0))
    if radius <= 0:
        return np.ascontiguousarray(array.astype(np.uint8, copy=True))
    result = array.astype(np.float32, copy=False)
    for _ in range(3):
        result = _box_blur_axis(result, radius, axis=1)
        result = _box_blur_axis(result, radius, axis=0)
    return np.ascontiguousarray(np.clip(result, 0, 255).astype(np.uint8))


def blur_qimage(image: QImage, radius: int) -> QImage:
    array = _qimage_to_rgba_array(image)
    blurred = triple_box_blur_rgba(array, radius)
    return _rgba_array_to_qimage(blurred)


def _noise_texture(alpha: int, size: int = 64) -> QImage:
    key = (int(size), int(alpha))
    cached = _NOISE_CACHE.get(key)
    if cached is not None:
        return cached
    rng = np.random.default_rng(1337)
    values = rng.integers(0, max(1, int(alpha)) + 1, size=(size, size, 1), dtype=np.uint8)
    rgb = np.full((size, size, 3), 255, dtype=np.uint8)
    image = _rgba_array_to_qimage(np.concatenate([rgb, values], axis=2))
    _NOISE_CACHE[key] = image
    return image


def _visible_screen_for_widget(widget: QWidget):
    top_left = widget.mapToGlobal(QPoint(0, 0))
    center = QPoint(top_left.x() + max(1, widget.width()) // 2, top_left.y() + max(1, widget.height()) // 2)
    return widget.screen() or QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()


def _window_rect_in_global(window: QWidget) -> QRect:
    top_left = window.mapToGlobal(QPoint(0, 0))
    return QRect(top_left, window.size())


def _rect_intersects(left: int, top: int, right: int, bottom: int, region: QRect) -> bool:
    return not (right <= region.left() or left >= region.right() + 1 or bottom <= region.top() or top >= region.bottom() + 1)


def _window_is_cloaked(hwnd: int) -> bool:
    if not sys.platform.startswith("win") or dwmapi is None:
        return False
    try:
        cloaked = wintypes.DWORD()
        result = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        return result == 0 and bool(cloaked.value)
    except Exception:
        return False


def _enumerate_z_order_windows() -> list[int]:
    if not sys.platform.startswith("win"):
        return []
    handles: list[int] = []
    hwnd = user32.GetTopWindow(None)
    while hwnd:
        handles.append(int(hwnd))
        hwnd = user32.GetWindow(hwnd, GW_HWNDNEXT)
    return handles


def _capture_region_windows(region: QRect, excluded_hwnds: set[int]) -> Optional[QImage]:
    if not sys.platform.startswith("win"):
        return None

    width = max(1, int(region.width()))
    height = max(1, int(region.height()))
    desktop_hwnd = user32.GetDesktopWindow()
    screen_dc = user32.GetDC(desktop_hwnd)
    if not screen_dc:
        return None

    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    if not mem_dc:
        user32.ReleaseDC(desktop_hwnd, screen_dc)
        return None

    bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
    if not bitmap:
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(desktop_hwnd, screen_dc)
        return None

    old_bitmap = gdi32.SelectObject(mem_dc, bitmap)
    try:
        gdi32.PatBlt(mem_dc, 0, 0, width, height, 0x00000042)
        any_drawn = False
        handles = _enumerate_z_order_windows()
        for hwnd in reversed(handles):
            if not hwnd or hwnd in excluded_hwnds:
                continue
            if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                continue
            if _window_is_cloaked(hwnd):
                continue

            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                continue
            if rect.right <= rect.left or rect.bottom <= rect.top:
                continue
            if not _rect_intersects(rect.left, rect.top, rect.right, rect.bottom, region):
                continue

            temp_dc = gdi32.CreateCompatibleDC(screen_dc)
            if not temp_dc:
                continue
            temp_bitmap = gdi32.CreateCompatibleBitmap(screen_dc, rect.right - rect.left, rect.bottom - rect.top)
            if not temp_bitmap:
                gdi32.DeleteDC(temp_dc)
                continue

            old_temp = gdi32.SelectObject(temp_dc, temp_bitmap)
            try:
                success = bool(user32.PrintWindow(hwnd, temp_dc, PW_RENDERFULLCONTENT))
                if not success:
                    success = bool(user32.PrintWindow(hwnd, temp_dc, 0))
                if not success:
                    continue

                inter_left = max(rect.left, region.left())
                inter_top = max(rect.top, region.top())
                inter_right = min(rect.right, region.right() + 1)
                inter_bottom = min(rect.bottom, region.bottom() + 1)
                if inter_right <= inter_left or inter_bottom <= inter_top:
                    continue

                src_x = inter_left - rect.left
                src_y = inter_top - rect.top
                dest_x = inter_left - region.left()
                dest_y = inter_top - region.top()
                draw_w = inter_right - inter_left
                draw_h = inter_bottom - inter_top
                if gdi32.BitBlt(mem_dc, dest_x, dest_y, draw_w, draw_h, temp_dc, src_x, src_y, SRCCOPY):
                    any_drawn = True
            finally:
                gdi32.SelectObject(temp_dc, old_temp)
                gdi32.DeleteObject(temp_bitmap)
                gdi32.DeleteDC(temp_dc)

        if not any_drawn:
            return None

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        buffer_size = width * height * 4
        buffer = ctypes.create_string_buffer(buffer_size)
        if not gdi32.GetDIBits(mem_dc, bitmap, 0, height, buffer, ctypes.byref(bmi), DIB_RGB_COLORS):
            return None
        image = QImage(buffer.raw, width, height, QImage.Format.Format_ARGB32).copy()
        return image
    finally:
        gdi32.SelectObject(mem_dc, old_bitmap)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(desktop_hwnd, screen_dc)


def _fallback_capture(region: QRect) -> Optional[QImage]:
    screen = QGuiApplication.screenAt(region.center()) or QGuiApplication.primaryScreen()
    if screen is None:
        return None
    pixmap = screen.grabWindow(0, region.x(), region.y(), region.width(), region.height())
    if pixmap.isNull():
        return None
    return pixmap.toImage()


def _placeholder_capture(size_rect: QRect) -> QImage:
    width = max(1, int(size_rect.width()))
    height = max(1, int(size_rect.height()))
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(QColor(236, 242, 249, 255))
    painter = QPainter(image)
    gradient = QLinearGradient(0, 0, 0, height)
    gradient.setColorAt(0.0, QColor(248, 250, 252, 255))
    gradient.setColorAt(1.0, QColor(226, 232, 240, 255))
    painter.fillRect(image.rect(), gradient)
    painter.end()
    return image


def _running_offscreen() -> bool:
    try:
        return (QGuiApplication.platformName() or "").strip().lower() in {"offscreen", "minimal", "minimalegl"}
    except Exception:
        return False


class BackdropController(QObject):
    def __init__(
        self,
        window: QWidget,
        excluded_hwnds: Iterable[int] | Callable[[], Iterable[int]] | None = None,
        *,
        steady_fps: int = 8,
        burst_fps: int = 12,
        burst_duration_ms: int = 800,
        capture_func: Optional[Callable[[QRect], Optional[QImage]]] = None,
    ) -> None:
        super().__init__(window)
        self.window = window
        self._excluded_hwnds = excluded_hwnds
        self._capture_func = capture_func
        self._steady_interval_ms = max(16, int(round(1000 / max(1, int(steady_fps or 8)))))
        self._burst_interval_ms = max(16, int(round(1000 / max(1, int(burst_fps or 12)))))
        self._burst_duration_ms = max(0, int(burst_duration_ms))
        self._burst_deadline = 0.0
        self._targets: weakref.WeakSet[QWidget] = weakref.WeakSet()
        self._capture_image: Optional[QImage] = None
        self._capture_rect = QRect()
        self._capture_generation = 0
        self._capture_dpr = 1.0
        self._processed_cache: dict[tuple[int, str], QImage] = {}

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timeout)

        self.window.installEventFilter(self)
        self.register_widget(window)
        QTimer.singleShot(0, self._sync_timer)

    @property
    def capture_generation(self) -> int:
        return self._capture_generation

    def register_widget(self, widget: QWidget) -> None:
        self._targets.add(widget)

    def set_excluded_hwnds(self, excluded_hwnds: Iterable[int] | Callable[[], Iterable[int]] | None) -> None:
        self._excluded_hwnds = excluded_hwnds
        self.invalidate(trigger_burst=True)

    def invalidate(self, *, trigger_burst: bool = False) -> None:
        self._capture_image = None
        self._capture_rect = QRect()
        self._processed_cache.clear()
        if trigger_burst:
            self.trigger_burst()

    def trigger_burst(self) -> None:
        if self._burst_duration_ms > 0:
            self._burst_deadline = time.monotonic() + (self._burst_duration_ms / 1000.0)
        self._sync_timer()

    def refresh_now(self) -> None:
        if not self.window.isVisible():
            return
        self._capture_backdrop()
        self._notify_targets()
        self._sync_timer()

    def eventFilter(self, watched, event):
        if watched is self.window:
            if event.type() in {
                QEvent.Type.Show,
                QEvent.Type.Move,
                QEvent.Type.Resize,
                QEvent.Type.WinIdChange,
                QEvent.Type.ZOrderChange,
            }:
                self.invalidate(trigger_burst=True)
                QTimer.singleShot(0, self.refresh_now)
            elif event.type() == QEvent.Type.Hide:
                self._sync_timer()
        return super().eventFilter(watched, event)

    def _notify_targets(self) -> None:
        for target in list(self._targets):
            try:
                if target.isVisible():
                    target.update()
            except RuntimeError:
                continue

    def _current_interval(self) -> int:
        if time.monotonic() < self._burst_deadline:
            return self._burst_interval_ms
        return self._steady_interval_ms

    def _sync_timer(self) -> None:
        if not self.window.isVisible():
            self._timer.stop()
            return
        interval = self._current_interval()
        if self._timer.interval() != interval:
            self._timer.setInterval(interval)
        if not self._timer.isActive():
            self._timer.start()

    def _on_timeout(self) -> None:
        if not self.window.isVisible():
            self._timer.stop()
            return
        self._capture_backdrop()
        self._notify_targets()
        self._sync_timer()

    def _device_pixel_ratio_for_window(self) -> float:
        screen = _visible_screen_for_widget(self.window)
        if screen is None:
            return 1.0
        try:
            return float(screen.devicePixelRatio() or 1.0)
        except Exception:
            return 1.0

    def _excluded_hwnd_set(self) -> set[int]:
        excluded: set[int] = set()
        raw = self._excluded_hwnds() if callable(self._excluded_hwnds) else self._excluded_hwnds
        if raw:
            for handle in raw:
                try:
                    value = int(handle)
                except Exception:
                    continue
                if value:
                    excluded.add(value)
        try:
            excluded.add(int(self.window.winId()))
        except Exception:
            pass
        return excluded

    def _capture_backdrop(self) -> None:
        rect = _window_rect_in_global(self.window)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        dpr = self._device_pixel_ratio_for_window()
        if rect != self._capture_rect or abs(dpr - self._capture_dpr) > 0.001:
            self._processed_cache.clear()

        image = None
        if callable(self._capture_func):
            image = self._capture_func(QRect(rect))
        elif _running_offscreen():
            image = _placeholder_capture(rect)
        else:
            try:
                image = _capture_region_windows(rect, self._excluded_hwnd_set())
            except Exception:
                image = None
            if image is None:
                image = _fallback_capture(rect)
        if image is None or image.isNull():
            return

        if image.size() != rect.size():
            image = image.scaled(rect.size(), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)

        self._capture_image = image.convertToFormat(QImage.Format.Format_RGBA8888)
        self._capture_rect = QRect(rect)
        self._capture_dpr = dpr
        self._capture_generation += 1
        self._processed_cache.clear()

    def glass_image(self, role_or_spec: str | GlassSpec) -> Optional[QImage]:
        spec = glass_spec(role_or_spec)
        if self._capture_image is None or self._capture_image.isNull():
            self._capture_backdrop()
        if self._capture_image is None or self._capture_image.isNull():
            return None

        key = (self._capture_generation, spec.role)
        cached = self._processed_cache.get(key)
        if cached is not None:
            return cached

        source = self._capture_image
        small_width = max(1, int(round(source.width() * 0.25)))
        small_height = max(1, int(round(source.height() * 0.25)))
        small = source.scaled(
            small_width,
            small_height,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        blurred_small = blur_qimage(small, spec.blur_radius)
        blurred = blurred_small.scaled(
            source.width(),
            source.height(),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ).convertToFormat(QImage.Format.Format_RGBA8888)

        painter = QPainter(blurred)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.fillRect(blurred.rect(), _qcolor(spec.tint))
        painter.end()

        self._processed_cache[key] = blurred
        return blurred

    def source_rect_for(self, widget: QWidget, rect: QRectF) -> QRectF:
        top_left = widget.mapTo(self.window, QPoint(int(round(rect.left())), int(round(rect.top()))))
        return QRectF(float(top_left.x()), float(top_left.y()), float(rect.width()), float(rect.height()))

    def cached_role_count(self) -> int:
        return len(self._processed_cache)


def paint_glass_background(
    painter: QPainter,
    widget: QWidget,
    rect: QRectF,
    *,
    controller: Optional[BackdropController],
    role: str | GlassSpec = "shell",
    corner_radius: float = 16.0,
    accent: Optional[QColor] = None,
    draw_shadow: bool = True,
) -> None:
    rect = QRectF(rect)
    spec = glass_spec(role)
    base_border = _qcolor(spec.border)
    highlight_top = _qcolor(spec.highlight_top)
    highlight_bottom = _qcolor(spec.highlight_bottom)
    tint_overlay = _qcolor(spec.tint)

    if accent is not None and accent.isValid():
        base_border = _blend(base_border, accent, 0.18)
        highlight_top = _blend(highlight_top, accent, 0.12)
        highlight_bottom = _blend(highlight_bottom, accent, 0.08)
        tint_overlay = _blend(tint_overlay, accent, 0.08)

    path = QPainterPath()
    path.addRoundedRect(rect, corner_radius, corner_radius)

    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if draw_shadow and spec.shadow_alpha > 0:
            for spread, alpha in ((7.0, int(spec.shadow_alpha * 0.5)), (4.0, spec.shadow_alpha), (2.0, int(spec.shadow_alpha * 1.25))):
                shadow_rect = rect.adjusted(-0.5, spread * 0.15, 0.5, spread * 0.45)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(15, 23, 42, max(0, min(255, alpha))))
                painter.drawRoundedRect(shadow_rect, corner_radius + 1.5, corner_radius + 1.5)

        glass_image = controller.glass_image(spec) if controller is not None else None
        if glass_image is not None and not glass_image.isNull():
            source_rect = controller.source_rect_for(widget, rect)
            painter.setClipPath(path)
            painter.drawImage(rect, glass_image, source_rect)
            painter.setClipping(False)
        else:
            painter.fillPath(path, tint_overlay)

        painter.fillPath(path, QColor(tint_overlay.red(), tint_overlay.green(), tint_overlay.blue(), max(12, int(tint_overlay.alpha() * 0.18))))

        highlight = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        highlight.setColorAt(0.0, highlight_top)
        highlight.setColorAt(1.0, highlight_bottom)
        painter.fillPath(path, highlight)

        inner_rect = rect.adjusted(1.5, 1.5, -1.5, -1.5)
        inner_gradient = QLinearGradient(inner_rect.topLeft(), inner_rect.bottomLeft())
        inner_gradient.setColorAt(0.0, QColor(255, 255, 255, 48))
        inner_gradient.setColorAt(1.0, QColor(255, 255, 255, 0))
        inner_path = QPainterPath()
        inner_path.addRoundedRect(inner_rect, max(1.0, corner_radius - 1.5), max(1.0, corner_radius - 1.5))
        painter.fillPath(inner_path, inner_gradient)

        noise = _noise_texture(spec.noise_alpha)
        painter.setClipPath(path)
        painter.drawTiledPixmap(rect.toRect(), QPixmap.fromImage(noise))
        painter.setClipping(False)

        painter.setPen(QPen(base_border, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
    finally:
        painter.restore()


class _GlassWidgetMixin:
    def __init__(self) -> None:
        self._backdrop_controller: Optional[BackdropController] = None
        self._glass_role = "shell"
        self._glass_radius = 16.0

    def set_backdrop_controller(self, controller: Optional[BackdropController]) -> None:
        self._backdrop_controller = controller
        if controller is not None:
            controller.register_widget(self)
        self.update()

    def set_glass_role(self, role: str) -> None:
        self._glass_role = str(role or "shell").strip().lower() or "shell"
        self.update()

    def set_glass_radius(self, radius: float) -> None:
        self._glass_radius = float(radius)
        self.update()


class GlassFrame(QFrame, _GlassWidgetMixin):
    def __init__(self, parent: Optional[QWidget] = None, *, role: str = "shell", radius: float = 16.0) -> None:
        QFrame.__init__(self, parent)
        _GlassWidgetMixin.__init__(self)
        self._glass_role = role
        self._glass_radius = radius
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        paint_glass_background(
            painter,
            self,
            QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
            controller=self._backdrop_controller,
            role=self._glass_role,
            corner_radius=self._glass_radius,
        )


class GlassButton(QPushButton, _GlassWidgetMixin):
    def __init__(self, text: str = "", parent: Optional[QWidget] = None, *, role: str = "control", radius: float = 10.0) -> None:
        QPushButton.__init__(self, text, parent)
        _GlassWidgetMixin.__init__(self)
        self._glass_role = role
        self._glass_radius = radius
        self._tone = "default"
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFlat(True)

    def set_tone(self, tone: str) -> None:
        self._tone = str(tone or "default").strip().lower() or "default"
        self.update()

    def _accent_for_state(self) -> Optional[QColor]:
        if not self.isEnabled():
            return QColor("#cbd5e1")
        if self._tone == "danger":
            return QColor("#fca5a5") if self.underMouse() or self.isDown() else QColor("#fecaca")
        if self._tone == "warm":
            return QColor("#fdba74") if self.underMouse() or self.isDown() else QColor("#fed7aa")
        if self.underMouse() or self.isDown():
            return QColor("#cbd5e1")
        return None

    def _text_color(self) -> QColor:
        if not self.isEnabled():
            return QColor("#94a3b8")
        if self._tone == "danger":
            return QColor("#7f1d1d") if (self.underMouse() or self.isDown()) else QColor("#9f1239")
        if self._tone == "warm":
            return QColor("#9a3412")
        return QColor("#334155")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        paint_glass_background(
            painter,
            self,
            rect,
            controller=self._backdrop_controller,
            role=self._glass_role,
            corner_radius=self._glass_radius,
            accent=self._accent_for_state(),
        )

        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.rect = self.rect()
        palette = option.palette
        palette.setColor(QPalette.ColorRole.ButtonText, self._text_color())
        option.palette = palette

        painter.setPen(self._text_color())
        self.style().drawControl(QStyle.ControlElement.CE_PushButtonLabel, option, painter, self)

        if self.hasFocus():
            focus_pen = QPen(QColor(255, 255, 255, 160), 1.0, Qt.PenStyle.DotLine)
            painter.setPen(focus_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), max(1.0, self._glass_radius - 2), max(1.0, self._glass_radius - 2))


class GlassComboBox(QComboBox, _GlassWidgetMixin):
    def __init__(self, parent: Optional[QWidget] = None, *, role: str = "control", radius: float = 8.0) -> None:
        QComboBox.__init__(self, parent)
        _GlassWidgetMixin.__init__(self)
        self._glass_role = role
        self._glass_radius = radius
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        accent = QColor("#cbd5e1") if self.underMouse() else None
        paint_glass_background(
            painter,
            self,
            rect,
            controller=self._backdrop_controller,
            role=self._glass_role,
            corner_radius=self._glass_radius,
            accent=accent,
        )

        text_color = QColor("#1f2937") if self.isEnabled() else QColor("#94a3b8")
        metrics = self.fontMetrics()
        arrow_w = 18
        text_rect = self.rect().adjusted(10, 0, -(arrow_w + 8), 0)
        text = metrics.elidedText(self.currentText(), Qt.TextElideMode.ElideRight, text_rect.width())
        painter.setPen(text_color)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)

        arrow_color = QColor("#64748b") if self.isEnabled() else QColor("#94a3b8")
        painter.setPen(QPen(arrow_color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        mid_x = self.width() - 12
        mid_y = self.height() // 2 - 1
        painter.drawLine(mid_x - 4, mid_y - 1, mid_x, mid_y + 3)
        painter.drawLine(mid_x, mid_y + 3, mid_x + 4, mid_y - 1)

        if self.hasFocus():
            painter.setPen(QPen(QColor(255, 255, 255, 160), 1.0, Qt.PenStyle.DotLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), max(1.0, self._glass_radius - 2), max(1.0, self._glass_radius - 2))
