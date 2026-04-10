from __future__ import annotations

import logging
import os
import sys
import time
import traceback
from pathlib import Path

from dotenv import load_dotenv
from PySide6.QtCore import QCoreApplication


SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _bootstrap_log_path() -> Path:
    if getattr(sys, "frozen", False):
        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return local_app_data / "PixelPilot" / "logs" / "runtime-bootstrap.log"
    return SRC_ROOT.parent / "logs" / "runtime-bootstrap.log"


BOOTSTRAP_LOG_PATH = _bootstrap_log_path()


def _bootstrap_trace(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    BOOTSTRAP_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with BOOTSTRAP_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} | {message}\n")


load_dotenv()


def main() -> int:
    started_at = time.perf_counter()
    _bootstrap_trace(
        "bootstrap.enter "
        f"frozen={bool(getattr(sys, 'frozen', False))} "
        f"executable={sys.executable} cwd={os.getcwd()}"
    )

    try:
        from core.controller import MainController
        from core.logging_setup import attach_gui_logging, configure_logging
        from runtime.bridge_adapter import ElectronBridgeAdapter
        from runtime.bridge_server import ElectronBridgeServer
        from runtime.service import ElectronRuntimeService
        from runtime.shell_proxy import ElectronShellProxy
        from runtime.state_models import MessageFeedModel, UiStateStore

        _bootstrap_trace("bootstrap.imports_ready")

        app = QCoreApplication(sys.argv)
        _bootstrap_trace("bootstrap.qcoreapplication_ready")

        logger, buffered_gui, log_file_path = configure_logging(adapter=None)
        _bootstrap_trace(f"bootstrap.logging_ready log_file={log_file_path}")
        startup_logger = logging.getLogger("pixelpilot.startup")
        startup_logger.info(
            "STARTUP phase=runtime_process_start status=ok elapsed_ms=%d",
            int((time.perf_counter() - started_at) * 1000),
        )

        state_store = UiStateStore()
        _bootstrap_trace("bootstrap.state_store_ready")
        message_feed_model = MessageFeedModel()
        _bootstrap_trace("bootstrap.message_feed_ready")

        host, port, token = ElectronRuntimeService.resolve_bridge_settings()
        _bootstrap_trace(
            f"bootstrap.bridge_settings_ready host={host} port={port} token_configured={bool(token)}"
        )
        bridge_server = ElectronBridgeServer(host=host, port=port, token=token)
        _bootstrap_trace("bootstrap.bridge_server_ready")
        adapter = ElectronBridgeAdapter(
            bridge_server=bridge_server,
            ui_state_store=state_store,
            message_feed_model=message_feed_model,
        )
        _bootstrap_trace("bootstrap.bridge_adapter_ready")
        shell_proxy = ElectronShellProxy(state_store=state_store, bridge_server=bridge_server)
        _bootstrap_trace("bootstrap.shell_proxy_ready")
        controller = MainController(
            adapter,
            shell_proxy,
            startup_started_at=started_at,
        )
        _bootstrap_trace("bootstrap.controller_ready")
        runtime_service = ElectronRuntimeService(
            app=app,
            controller=controller,
            adapter=adapter,
            state_store=state_store,
            message_feed_model=message_feed_model,
            bridge_server=bridge_server,
            shell_proxy=shell_proxy,
        )
        _bootstrap_trace("bootstrap.runtime_service_ready")

        attach_gui_logging(logger, adapter, buffered_gui)
        _bootstrap_trace("bootstrap.gui_logging_attached")
        adapter.add_activity_message("Runtime starting")
        adapter.add_activity_message(f"Logging to: {log_file_path}")
        adapter.add_activity_message(f"Bridge endpoint: ws://{host}:{port}/control")

        runtime_service.start()
        _bootstrap_trace("bootstrap.runtime_service_started")

        app.aboutToQuit.connect(controller.shutdown)
        exit_code = app.exec()
        _bootstrap_trace(f"bootstrap.app_exec_returned exit_code={exit_code}")
        bridge_server.stop()
        _bootstrap_trace("bootstrap.bridge_server_stopped")
        return int(exit_code)
    except Exception as exc:
        _bootstrap_trace(f"bootstrap.exception {exc.__class__.__name__}: {exc}")
        _bootstrap_trace(traceback.format_exc().rstrip())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
