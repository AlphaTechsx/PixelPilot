from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent

from .gui_adapter import GuiAdapter
from .state_models import UiActionsBridge, UiStateStore

logger = logging.getLogger("pixelpilot.ui.qml_shell")


class QmlDesktopShell:
    def __init__(
        self,
        state_store: UiStateStore,
        actions: UiActionsBridge,
    ) -> None:
        self.state_store = state_store
        self.actions = actions
        self._preview_source = None
        self._adapter: GuiAdapter | None = None

        self.engine = QQmlApplicationEngine()
        root_context = self.engine.rootContext()
        root_context.setContextProperty("uiState", self.state_store)
        root_context.setContextProperty("messageFeed", None)
        root_context.setContextProperty("uiActions", self.actions)

        qml_dir = Path(__file__).resolve().parent / "qml"
        self.main_window = self._create_component(qml_dir / "MainOverlay.qml")
        self.notch_window = self._create_component(qml_dir / "MinimizedNotch.qml")
        self.sidecar_window = self._create_component(qml_dir / "AgentPreviewWindow.qml")

        self.notch_window.hide()
        self.sidecar_window.hide()
        self.show()

    def connect_adapter(self, adapter: GuiAdapter) -> None:
        self._adapter = adapter
        self.engine.rootContext().setContextProperty("messageFeed", adapter.message_feed_model)

    def echo_user_command(self, text: str) -> None:
        if self._adapter is not None:
            self._adapter.add_user_message(text)

    def _create_component(self, path: Path):
        component = QQmlComponent(self.engine, QUrl.fromLocalFile(os.fspath(path)))
        if component.isError():
            errors = "; ".join(str(err.toString()) for err in component.errors())
            raise RuntimeError(f"Failed to load QML component {path.name}: {errors}")
        instance = component.create(self.engine.rootContext())
        if instance is None:
            errors = "; ".join(str(err.toString()) for err in component.errors())
            raise RuntimeError(f"Failed to instantiate QML component {path.name}: {errors}")
        return instance

    def show(self) -> None:
        self.main_window.show()
        self.state_store.set_background_hidden(False)
        self.refresh_agent_preview_visibility()

    def hide(self) -> None:
        self.main_window.hide()
        self.refresh_agent_preview_visibility()

    def close(self) -> None:
        self.main_window.close()
        self.notch_window.close()
        self.sidecar_window.close()

    def isVisible(self) -> bool:
        return bool(self.main_window.isVisible())

    def setWindowIcon(self, icon) -> None:
        for window in (self.main_window, self.notch_window, self.sidecar_window):
            setter = getattr(window, "setIcon", None)
            if callable(setter):
                setter(icon)

    def windowIcon(self):
        getter = getattr(self.main_window, "icon", None)
        return getter() if callable(getter) else None

    def dialog_parent(self):
        return None

    def shortcut_host(self):
        return None

    def _apply_input_transparency(self, window, enable: bool) -> None:
        flags = window.flags()
        if enable:
            flags |= Qt.WindowType.WindowTransparentForInput
        else:
            flags &= ~Qt.WindowType.WindowTransparentForInput
        window.setFlags(flags)
        if window.isVisible():
            window.show()

    def restore_from_background(self) -> None:
        self.state_store.set_background_hidden(False)
        self.notch_window.hide()
        self.main_window.show()
        self.refresh_agent_preview_visibility()

    def minimize_to_background(self) -> None:
        self.state_store.set_background_hidden(True)
        self.main_window.hide()
        self.notch_window.show()
        self.refresh_agent_preview_visibility()

    def toggle_background_visibility(self) -> None:
        if self.state_store.backgroundHidden:
            self.restore_from_background()
        else:
            self.minimize_to_background()

    def toggle_expand(self) -> None:
        self.state_store.set_expanded(not self.state_store.expanded)
        self.refresh_agent_preview_visibility()

    def set_click_through_enabled(self, enable: bool) -> None:
        self.state_store.set_click_through_enabled(enable)
        self._apply_input_transparency(self.main_window, enable)

    def click_through_enabled(self) -> bool:
        return self.state_store.clickThroughEnabled

    def background_hidden(self) -> bool:
        return self.state_store.backgroundHidden

    def attach_agent_preview_source(self, source) -> None:
        self._preview_source = source
        self.state_store.set_agent_preview_available(bool(source))
        self.refresh_agent_preview_visibility()

    def refresh_agent_preview_visibility(self) -> None:
        should_show = bool(
            self._preview_source
            and getattr(self._preview_source, "is_created", False)
            and self.state_store.workspace == "agent"
            and self.state_store.agentViewVisible
            and not self.state_store.backgroundHidden
            and self.main_window.isVisible()
        )
        self.state_store.set_sidecar_visible(should_show)
        if should_show:
            self.sidecar_window.show()
        else:
            self.sidecar_window.hide()

    def prepare_for_screenshot(self) -> dict[str, bool]:
        payload = {
            "restore_main_window": bool(self.main_window.isVisible() and not self.state_store.backgroundHidden),
            "restore_minimized_notch": bool(self.state_store.backgroundHidden and self.notch_window.isVisible()),
        }
        self.main_window.hide()
        self.notch_window.hide()
        self.sidecar_window.hide()
        return payload

    def restore_after_screenshot(self, payload: dict[str, bool]) -> None:
        if bool(payload.get("restore_main_window", False)):
            self.show()
        elif bool(payload.get("restore_minimized_notch", False)):
            self.minimize_to_background()
