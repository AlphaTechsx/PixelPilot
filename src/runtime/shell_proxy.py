from __future__ import annotations

import io
import logging
import threading
import time
from typing import Optional

from PIL import Image

from config import Config
from .protocol import pack_sidecar_frame


logger = logging.getLogger("pixelpilot.runtime.shell")


class _SidecarStreamWorker:
    def __init__(self, *, bridge_server, fps: int = 6) -> None:
        self.bridge_server = bridge_server
        self.fps = max(1, int(fps or 6))
        self.desktop_manager = None
        self.active = False
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="PixelPilotSidecarStream", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def set_source(self, desktop_manager) -> None:
        self.desktop_manager = desktop_manager

    def set_active(self, active: bool) -> None:
        self.active = bool(active)

    def _run(self) -> None:
        frame_interval = 1.0 / float(self.fps)
        while not self._stop_event.is_set():
            started_at = time.perf_counter()
            try:
                self._maybe_publish_frame()
            except Exception:  # noqa: BLE001
                logger.debug("Sidecar frame publish failed", exc_info=True)
            elapsed = time.perf_counter() - started_at
            time.sleep(max(0.05, frame_interval - elapsed))

    def _maybe_publish_frame(self) -> None:
        if not self.active or not self.bridge_server.has_sidecar_clients():
            return
        desktop_manager = self.desktop_manager
        if desktop_manager is None or not getattr(desktop_manager, "is_created", False):
            return
        raw_frame = desktop_manager.capture_desktop_raw()
        if not raw_frame:
            return
        data, width, height = raw_frame
        image = Image.frombytes("RGBA", (int(width), int(height)), data, "raw", "BGRA")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=72, optimize=True)
        packet = pack_sidecar_frame(
            buffer.getvalue(),
            {
                "width": int(width),
                "height": int(height),
                "timestamp": time.time(),
            },
        )
        self.bridge_server.publish_sidecar_frame(packet)


class ElectronShellProxy:
    def __init__(self, *, state_store, bridge_server) -> None:
        self.state_store = state_store
        self.bridge_server = bridge_server
        self._preview_source = None
        self._click_through_enabled = False
        self._sidecar_worker = _SidecarStreamWorker(
            bridge_server=bridge_server,
            fps=max(1, int(getattr(Config, "SIDECAR_PREVIEW_FPS", 6) or 6)),
        )

    def shutdown(self) -> None:
        self._sidecar_worker.stop()

    def dialog_parent(self):
        return None

    def isVisible(self) -> bool:
        return not bool(self.state_store.backgroundHidden)

    def background_hidden(self) -> bool:
        return bool(self.state_store.backgroundHidden)

    def prepare_for_screenshot(self) -> dict[str, bool]:
        response = self.bridge_server.request_ui(
            "ui.prepareForScreenshot",
            {},
            timeout_s=10.0,
            allow_missing=True,
        )
        return {
            "restore_main_window": bool(response.get("restore_main_window")),
            "restore_minimized_notch": bool(response.get("restore_minimized_notch")),
        }

    def restore_after_screenshot(self, payload: dict[str, bool]) -> None:
        self.bridge_server.request_ui(
            "ui.restoreAfterScreenshot",
            dict(payload or {}),
            timeout_s=10.0,
            allow_missing=True,
        )

    def set_click_through_enabled(self, enable: bool) -> None:
        self._click_through_enabled = bool(enable)
        self.bridge_server.request_ui(
            "shell.setClickThrough",
            {"enabled": self._click_through_enabled},
            timeout_s=5.0,
            allow_missing=True,
        )

    def click_through_enabled(self) -> bool:
        return bool(self._click_through_enabled)

    def attach_agent_preview_source(self, source) -> None:
        self._preview_source = source
        self.state_store.set_agent_preview_available(bool(source))
        self._sidecar_worker.set_source(source)
        self.refresh_agent_preview_visibility()

    def refresh_agent_preview_visibility(self) -> None:
        should_show = bool(
            self._preview_source
            and getattr(self._preview_source, "is_created", False)
            and self.state_store.workspace == "agent"
            and self.state_store.agentViewVisible
            and not self.state_store.backgroundHidden
        )
        self.state_store.set_sidecar_visible(should_show)
        self._sidecar_worker.set_active(should_show)
        self.bridge_server.publish_event("sidecar.visibility", {"visible": should_show})
