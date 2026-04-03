from __future__ import annotations

import logging

from config import Config
from .qml_shell import QmlDesktopShell
from .state_models import UiActionsBridge, UiStateStore
from .widget_shell import WidgetDesktopShell

logger = logging.getLogger("pixelpilot.ui.shell")


def create_desktop_shell(
    state_store: UiStateStore,
    actions: UiActionsBridge,
):
    if Config.UI_PREFER_QML_SHELL:
        try:
            shell = QmlDesktopShell(state_store, actions)
            logger.info("Using QML desktop shell")
            return shell
        except Exception as exc:
            if Config.UI_REQUIRE_QML_SHELL:
                raise
            logger.warning("QML desktop shell unavailable, falling back to widget shell: %s", exc)

    shell = WidgetDesktopShell(state_store, actions)
    logger.info("Using widget desktop shell fallback")
    return shell
