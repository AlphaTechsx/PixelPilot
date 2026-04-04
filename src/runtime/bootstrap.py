from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from PySide6.QtCore import QCoreApplication


SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.controller import MainController
from core.logging_setup import attach_gui_logging, configure_logging
from runtime.bridge_adapter import ElectronBridgeAdapter
from runtime.bridge_server import ElectronBridgeServer
from runtime.service import ElectronRuntimeService
from runtime.shell_proxy import ElectronShellProxy
from runtime.state_models import MessageFeedModel, UiStateStore


load_dotenv()


def main() -> int:
    started_at = time.perf_counter()
    app = QCoreApplication(sys.argv)

    logger, buffered_gui, log_file_path = configure_logging(adapter=None)
    startup_logger = logging.getLogger("pixelpilot.startup")
    startup_logger.info(
        "STARTUP phase=runtime_process_start status=ok elapsed_ms=%d",
        int((time.perf_counter() - started_at) * 1000),
    )

    state_store = UiStateStore()
    message_feed_model = MessageFeedModel()

    host, port, token = ElectronRuntimeService.resolve_bridge_settings()
    bridge_server = ElectronBridgeServer(host=host, port=port, token=token)
    adapter = ElectronBridgeAdapter(
        bridge_server=bridge_server,
        ui_state_store=state_store,
        message_feed_model=message_feed_model,
    )
    shell_proxy = ElectronShellProxy(state_store=state_store, bridge_server=bridge_server)
    controller = MainController(
        adapter,
        shell_proxy,
        startup_started_at=started_at,
    )
    runtime_service = ElectronRuntimeService(
        app=app,
        controller=controller,
        adapter=adapter,
        state_store=state_store,
        message_feed_model=message_feed_model,
        bridge_server=bridge_server,
        shell_proxy=shell_proxy,
    )

    attach_gui_logging(logger, adapter, buffered_gui)
    adapter.add_activity_message("Runtime starting")
    adapter.add_activity_message(f"Logging to: {log_file_path}")
    adapter.add_activity_message(f"Bridge endpoint: ws://{host}:{port}/control")

    runtime_service.start()

    app.aboutToQuit.connect(controller.shutdown)
    exit_code = app.exec()
    bridge_server.stop()
    return int(exit_code)
