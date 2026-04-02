from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional


logger = logging.getLogger("pixelpilot.app_index_service")


class AppIndexService:
    """Manage app-index warmup outside the startup critical path."""

    def __init__(
        self,
        *,
        cache_path: str,
        auto_refresh: bool,
        include_processes: bool,
        builder: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.cache_path = cache_path
        self.auto_refresh = bool(auto_refresh)
        self.include_processes = bool(include_processes)
        self._builder = builder

        self._lock = threading.RLock()
        self._ready_event = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._indexer = None
        self._state = "idle"
        self._error = ""

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def error(self) -> str:
        with self._lock:
            return self._error

    @property
    def is_ready(self) -> bool:
        return self.state == "ready"

    @property
    def app_count(self) -> int:
        with self._lock:
            indexer = self._indexer
            if indexer is None:
                return 0
            try:
                return len(getattr(indexer, "index", {}) or {})
            except Exception:
                return 0

    def _build_indexer(self):
        if self._builder is not None:
            return self._builder()

        from tools.app_indexer import AppIndexer

        return AppIndexer(
            cache_path=self.cache_path,
            auto_refresh=self.auto_refresh,
            include_processes=self.include_processes,
        )

    def start_warmup(self) -> bool:
        with self._lock:
            if self._state == "ready":
                self._ready_event.set()
                return False
            if self._state == "loading" and self._worker and self._worker.is_alive():
                return False

            self._state = "loading"
            self._error = ""
            self._ready_event.clear()
            self._worker = threading.Thread(
                target=self._build_worker,
                name="PixelPilotAppIndexWarmup",
                daemon=True,
            )
            self._worker.start()
            logger.info("App index warmup started")
            return True

    def ensure_ready(
        self,
        *,
        timeout: Optional[float] = None,
        on_wait: Optional[Callable[[], None]] = None,
    ) -> bool:
        if self.is_ready:
            return True

        self.start_warmup()
        if not self.is_ready and on_wait is not None:
            try:
                on_wait()
            except Exception:
                logger.debug("App index wait callback failed", exc_info=True)

        finished = self._ready_event.wait(timeout=timeout)
        return bool(finished and self.is_ready)

    def find_app(
        self,
        query: str,
        *,
        max_results: int = 5,
        wait: bool = True,
        on_wait: Optional[Callable[[], None]] = None,
        timeout: Optional[float] = None,
    ):
        if wait and not self.ensure_ready(timeout=timeout, on_wait=on_wait):
            return []

        with self._lock:
            indexer = self._indexer
        if indexer is None:
            return []
        return indexer.find_app(query, max_results=max_results)

    def open_app(
        self,
        query: str,
        *,
        desktop_manager=None,
        wait: bool = True,
        on_wait: Optional[Callable[[], None]] = None,
        timeout: Optional[float] = None,
    ) -> bool:
        if wait and not self.ensure_ready(timeout=timeout, on_wait=on_wait):
            return False

        with self._lock:
            indexer = self._indexer
        if indexer is None:
            return False
        return bool(indexer.open_app(query, desktop_manager=desktop_manager))

    def _build_worker(self) -> None:
        started_at = time.perf_counter()
        try:
            indexer = self._build_indexer()
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._indexer = None
                self._state = "error"
                self._error = str(exc)
            logger.exception("App index warmup failed")
        else:
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            count = 0
            try:
                count = len(getattr(indexer, "index", {}) or {})
            except Exception:
                count = 0
            with self._lock:
                self._indexer = indexer
                self._state = "ready"
                self._error = ""
            logger.info(
                "App index warmup ready in %d ms (%d apps)",
                elapsed_ms,
                count,
            )
        finally:
            self._ready_event.set()
