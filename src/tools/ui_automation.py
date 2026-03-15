from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import tempfile
import time
from collections import Counter, deque
from pathlib import Path
from typing import Any, Optional

from PIL import Image

from config import Config

logger = logging.getLogger("pixelpilot.uia")

try:
    import psutil
except Exception:
    psutil = None

try:
    import pyautogui
except Exception:
    pyautogui = None

try:
    import uiautomation as auto

    UIA_IMPORT_ERROR = ""
except Exception as exc:
    auto = None
    UIA_IMPORT_ERROR = str(exc)

_OCR_ENGINE: Any | None = None
_OCR_IMPORT_ERROR: str = ""


def _unavailable_snapshot(workspace: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "workspace": workspace,
        "available": False,
        "error": reason,
        "active_window_title": "",
        "active_window_class": "",
        "elements_count": 0,
        "elements": [],
        "windows_count": 0,
        "windows": [],
    }


def snapshot_signature(snapshot: Optional[dict[str, Any]]) -> str:
    if not snapshot:
        return "blind:none"

    compact_elements = []
    for element in (snapshot.get("elements") or [])[:80]:
        rect = element.get("rect") or {}
        compact_elements.append(
            {
                "ui_element_id": element.get("ui_element_id", ""),
                "name": element.get("name", ""),
                "control_type": element.get("control_type", ""),
                "automation_id": element.get("automation_id", ""),
                "class_name": element.get("class_name", ""),
                "rect": {
                    "left": rect.get("left"),
                    "top": rect.get("top"),
                    "right": rect.get("right"),
                    "bottom": rect.get("bottom"),
                },
            }
        )

    compact_windows = []
    for window in (snapshot.get("windows") or [])[:40]:
        compact_windows.append(
            {
                "window_id": window.get("window_id", ""),
                "title": window.get("title", ""),
                "class_name": window.get("class_name", ""),
                "process_name": window.get("process_name", ""),
                "is_visible": bool(window.get("is_visible", False)),
                "is_minimized": bool(window.get("is_minimized", False)),
            }
        )

    compact = {
        "workspace": snapshot.get("workspace", ""),
        "available": bool(snapshot.get("available", False)),
        "error": snapshot.get("error", ""),
        "active_window_title": snapshot.get("active_window_title", ""),
        "active_window_class": snapshot.get("active_window_class", ""),
        "elements_count": int(snapshot.get("elements_count", 0) or 0),
        "elements": compact_elements,
        "windows": compact_windows,
    }
    raw = json.dumps(compact, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return f"blind:{hashlib.sha1(raw.encode('utf-8')).hexdigest()}"


def _safe_attr(obj: Any, attr: str, default: Any = "") -> Any:
    try:
        return getattr(obj, attr, default)
    except Exception:
        return default


def _rect_to_dict(rect: Any) -> dict[str, int] | None:
    if rect is None:
        return None
    try:
        return {
            "left": int(rect.left),
            "top": int(rect.top),
            "right": int(rect.right),
            "bottom": int(rect.bottom),
        }
    except Exception:
        return None


def _safe_rect_visible(control: Any) -> bool:
    rect = _safe_attr(control, "BoundingRectangle", default=None)
    if rect is None:
        return False
    try:
        return int(rect.right) > int(rect.left) and int(rect.bottom) > int(rect.top)
    except Exception:
        return False


def _window_id_from_handle(handle: int) -> str:
    return f"win_{int(handle):x}"


def _process_name_from_pid(pid: int) -> str:
    if not pid or psutil is None:
        return ""
    try:
        return str(psutil.Process(int(pid)).name() or "")
    except Exception:
        return ""


def _collect_patterns(control: Any) -> list[str]:
    patterns: list[str] = []
    try:
        legacy = control.GetLegacyIAccessiblePattern()
        if legacy and _safe_attr(legacy, "DefaultAction", ""):
            patterns.append("DefaultAction")
    except Exception:
        pass

    for name in ("GetTextPattern", "GetValuePattern", "GetInvokePattern", "SendKeys"):
        if hasattr(control, name):
            patterns.append(name.replace("Get", "").replace("Pattern", ""))
    return patterns


def _score_candidate(
    *,
    name: str,
    control_type: str,
    automation_id: str,
    class_name: str,
    patterns: list[str],
    preferred_terms: list[str],
) -> float:
    score = 0.0
    type_weights = {
        "EditControl": 6.0,
        "TreeItemControl": 5.0,
        "ListItemControl": 5.0,
        "ButtonControl": 4.0,
        "MenuItemControl": 4.0,
        "SplitButtonControl": 4.0,
        "TabItemControl": 3.5,
        "ComboBoxControl": 3.0,
        "CheckBoxControl": 2.5,
        "RadioButtonControl": 2.5,
        "TextControl": 1.5,
        "PaneControl": 1.0,
    }
    score += type_weights.get(control_type, 0.5)

    if "DefaultAction" in patterns:
        score += 2.5
    if "Invoke" in patterns:
        score += 1.5
    if "Text" in patterns:
        score += 1.0
    if "Value" in patterns:
        score += 1.0

    name_l = (name or "").lower()
    auto_l = (automation_id or "").lower()
    class_l = (class_name or "").lower()
    combined = f"{name_l} {auto_l} {class_l}"

    if "search" in combined:
        score += 1.5
    if "address" in combined or "breadcrumb" in combined:
        score += 1.5

    for term in preferred_terms:
        if term and term in combined:
            score += 4.0

    if not (name or automation_id):
        score -= 1.0

    return score


def _base_element_key(node: dict[str, Any]) -> str:
    rect = node.get("rect") or {}
    rect_key = (
        f"{rect.get('left', '')}:{rect.get('top', '')}:"
        f"{rect.get('right', '')}:{rect.get('bottom', '')}"
    )
    raw = "|".join(
        [
            str(node.get("control_type", "")),
            str(node.get("name", "")),
            str(node.get("automation_id", "")),
            str(node.get("class_name", "")),
            rect_key,
        ]
    )
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:10]


def _normalize_terms(preferred_terms: Optional[list[str]]) -> list[str]:
    return [term.lower().strip() for term in (preferred_terms or []) if str(term).strip()]


def _safe_foreground_control(max_attempts: int = 8, delay: float = 0.06) -> Any:
    if auto is None:
        raise RuntimeError(f"uiautomation unavailable: {UIA_IMPORT_ERROR}")

    last_exc: Exception | None = None
    for _ in range(max_attempts):
        try:
            control = auto.GetForegroundControl()
            if control:
                return control
        except Exception as exc:
            last_exc = exc
        time.sleep(delay)

    try:
        return auto.GetRootControl()
    except Exception:
        if last_exc:
            raise last_exc
        raise RuntimeError("Unable to resolve foreground/root control")


def _window_matches_filters(
    window: dict[str, Any],
    *,
    title_contains: str,
    process_name: str,
    visible_only: bool,
) -> bool:
    if visible_only and not bool(window.get("is_visible", False)):
        return False

    title_filter = (title_contains or "").strip().lower()
    if title_filter and title_filter not in str(window.get("title", "")).lower():
        return False

    process_filter = (process_name or "").strip().lower()
    if process_filter:
        process = str(window.get("process_name", "")).lower()
        if process_filter != process and process_filter not in process:
            return False

    return True


def _scan_windows(
    *,
    max_windows: int,
    title_contains: str = "",
    process_name: str = "",
    visible_only: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = auto.GetRootControl()
    if root is None:
        return [], {}

    windows: list[dict[str, Any]] = []
    control_index: dict[str, Any] = {}

    for control in root.GetChildren() or []:
        handle = int(_safe_attr(control, "NativeWindowHandle", default=0) or 0)
        if handle <= 0:
            continue

        rect = _rect_to_dict(_safe_attr(control, "BoundingRectangle", default=None)) or {}
        width = int(rect.get("right", 0)) - int(rect.get("left", 0))
        height = int(rect.get("bottom", 0)) - int(rect.get("top", 0))
        is_visible = width > 1 and height > 1
        is_offscreen = bool(_safe_attr(control, "IsOffscreen", default=False))
        is_minimized = bool(not is_visible and is_offscreen)
        pid = int(_safe_attr(control, "ProcessId", default=0) or 0)
        process = _process_name_from_pid(pid)

        window = {
            "window_id": _window_id_from_handle(handle),
            "handle": handle,
            "title": str(_safe_attr(control, "Name", default="") or ""),
            "class_name": str(_safe_attr(control, "ClassName", default="") or ""),
            "process_name": process,
            "process_id": pid,
            "is_visible": bool(is_visible),
            "is_minimized": bool(is_minimized),
            "rect": rect,
        }
        if not _window_matches_filters(
            window,
            title_contains=title_contains,
            process_name=process_name,
            visible_only=visible_only,
        ):
            continue

        windows.append(window)
        control_index[window["window_id"]] = control

    windows.sort(
        key=lambda item: (
            not bool(item.get("is_visible", False)),
            not bool(item.get("title", "")),
            str(item.get("title", "")).lower(),
        )
    )
    windows = windows[: max(1, int(max_windows or 1))]
    allowed_ids = {w["window_id"] for w in windows}
    control_index = {
        window_id: control
        for window_id, control in control_index.items()
        if window_id in allowed_ids
    }
    return windows, control_index


def _annotate_candidates(
    candidates: list[tuple[float, dict[str, Any], Any]],
    *,
    workspace: str,
    title: str,
    class_name: str,
    max_nodes: int,
    windows: Optional[list[dict[str, Any]]] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    base_keys = [_base_element_key(node) for _, node, _ in candidates]
    counts = Counter(base_keys)
    seen: dict[str, int] = {}
    nodes: list[dict[str, Any]] = []
    control_index: dict[str, Any] = {}

    for (score, node, control), base_key in zip(candidates, base_keys):
        seen[base_key] = seen.get(base_key, 0) + 1
        suffix = f"_{seen[base_key]}" if counts[base_key] > 1 else ""
        ui_element_id = f"el_{base_key}{suffix}"

        annotated = dict(node)
        annotated["ui_element_id"] = ui_element_id
        annotated["rank_score"] = round(float(score), 3)
        nodes.append(annotated)
        control_index[ui_element_id] = control

    snapshot_nodes = nodes[:max_nodes]
    snapshot_windows = list(windows or [])
    snapshot = {
        "schema_version": 1,
        "workspace": workspace,
        "available": True,
        "error": "",
        "active_window_title": title,
        "active_window_class": class_name,
        "elements_count": len(snapshot_nodes),
        "elements": snapshot_nodes,
        "windows_count": len(snapshot_windows),
        "windows": snapshot_windows,
    }
    return snapshot, control_index


def _scan_snapshot(
    *,
    workspace: str,
    max_nodes: int,
    preferred_terms: Optional[list[str]] = None,
    scan_limit: Optional[int] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    preferred_terms = _normalize_terms(preferred_terms)
    window = _safe_foreground_control()
    title = _safe_attr(window, "Name", default="")
    class_name = _safe_attr(window, "ClassName", default="")

    queue: deque[Any] = deque([window])
    scanned = 0
    limit = scan_limit or max(max_nodes * 15, 300)
    candidates: list[tuple[float, dict[str, Any], Any]] = []

    while queue and scanned < limit:
        current = queue.popleft()
        scanned += 1

        if _safe_rect_visible(current):
            name = _safe_attr(current, "Name", default="")
            control_type = _safe_attr(current, "ControlTypeName", default="")
            automation_id = _safe_attr(current, "AutomationId", default="")
            current_class = _safe_attr(current, "ClassName", default="")
            patterns = _collect_patterns(current)

            node = {
                "name": name,
                "control_type": control_type,
                "automation_id": automation_id,
                "class_name": current_class,
                "native_window_handle": int(
                    _safe_attr(current, "NativeWindowHandle", default=0) or 0
                ),
                "patterns": patterns,
                "rect": _rect_to_dict(
                    _safe_attr(current, "BoundingRectangle", default=None)
                ),
            }
            score = _score_candidate(
                name=name,
                control_type=control_type,
                automation_id=automation_id,
                class_name=current_class,
                patterns=patterns,
                preferred_terms=preferred_terms,
            )
            candidates.append((score, node, current))

        try:
            for child in current.GetChildren():
                queue.append(child)
        except Exception:
            continue

    candidates.sort(key=lambda item: item[0], reverse=True)
    windows, _ = _scan_windows(
        max_windows=Config.UIA_MAX_WINDOWS,
        visible_only=False,
    )
    return _annotate_candidates(
        candidates,
        workspace=workspace,
        title=title,
        class_name=class_name,
        max_nodes=max_nodes,
        windows=windows,
    )


def _run_in_workspace(
    workspace_name: str,
    desktop_manager: Any,
    func,
    *args,
    **kwargs,
):
    if workspace_name == "agent":
        if not desktop_manager or not getattr(desktop_manager, "is_created", False):
            raise RuntimeError("Agent Desktop unavailable")
        result = desktop_manager.run_on_desktop(func, *args, **kwargs)
        if result is None:
            raise RuntimeError("Agent Desktop UIA call returned no result")
        return result
    return func(*args, **kwargs)


def _apply_focus(control: Any) -> tuple[bool, str]:
    try:
        control.SetFocus()
        time.sleep(0.03)
        return True, "SetFocus"
    except Exception:
        pass

    try:
        control.SetActive()
        time.sleep(0.03)
        return True, "SetActive"
    except Exception:
        return False, ""


def _apply_window_show_state(handle: int, *, restore: bool, maximize: bool) -> None:
    if handle <= 0:
        return
    try:
        user32 = ctypes.windll.user32
    except Exception:
        return

    try:
        if restore:
            try:
                is_minimized = bool(user32.IsIconic(handle))
            except Exception:
                is_minimized = False
            if is_minimized:
                # SW_RESTORE only for minimized windows; keep maximized state intact.
                user32.ShowWindow(handle, 9)
        if maximize:
            # SW_MAXIMIZE
            user32.ShowWindow(handle, 3)
        user32.SetForegroundWindow(handle)
    except Exception:
        pass


def _resolve_element_control(
    workspace: str,
    target_id: str,
) -> tuple[Optional[Any], Optional[dict[str, int]]]:
    _, control_index = _scan_snapshot(
        workspace=workspace,
        max_nodes=max(Config.UIA_MAX_ELEMENTS * 3, 360),
        preferred_terms=None,
        scan_limit=max(Config.UIA_MAX_ELEMENTS * 30, 1200),
    )
    control = control_index.get(target_id)
    if control is None:
        return None, None
    rect = _rect_to_dict(_safe_attr(control, "BoundingRectangle", default=None))
    return control, rect


def _extract_text_from_control(control: Any) -> tuple[str, str]:
    try:
        text_pattern = control.GetTextPattern()
        if text_pattern and text_pattern.DocumentRange:
            text = str(text_pattern.DocumentRange.GetText(-1) or "").strip()
            if text:
                return text, "TextPattern.DocumentRange"
    except Exception:
        pass

    try:
        value_pattern = control.GetValuePattern()
        if value_pattern:
            text = str(value_pattern.Value or "").strip()
            if text:
                return text, "ValuePattern.Value"
    except Exception:
        pass

    try:
        legacy = control.GetLegacyIAccessiblePattern()
        if legacy:
            value_text = str(getattr(legacy, "Value", "") or "").strip()
            if value_text:
                return value_text, "LegacyIAccessible.Value"
            name_text = str(getattr(legacy, "Name", "") or "").strip()
            if name_text:
                return name_text, "LegacyIAccessible.Name"
    except Exception:
        pass

    name = str(getattr(control, "Name", "") or "").strip()
    if name:
        return name, "Control.Name"

    return "", ""


def _control_unique_key(control: Any) -> tuple[Any, ...]:
    rect = _rect_to_dict(_safe_attr(control, "BoundingRectangle", default=None)) or {}
    return (
        int(_safe_attr(control, "NativeWindowHandle", default=0) or 0),
        str(_safe_attr(control, "Name", default="") or ""),
        str(_safe_attr(control, "ControlTypeName", default="") or ""),
        str(_safe_attr(control, "AutomationId", default="") or ""),
        str(_safe_attr(control, "ClassName", default="") or ""),
        rect.get("left", 0),
        rect.get("top", 0),
        rect.get("right", 0),
        rect.get("bottom", 0),
    )


def _seed_controls(
    *,
    target: str,
    ui_element_id: Optional[str],
    control_index: dict[str, Any],
) -> list[tuple[str, Any]]:
    target = (target or "auto").strip().lower()
    if target not in {"auto", "focused", "window", "element"}:
        target = "auto"

    seeds: list[tuple[str, Any]] = []
    if target in {"auto", "element"} and ui_element_id:
        control = control_index.get(ui_element_id)
        if control is not None:
            seeds.append((f"element:{ui_element_id}", control))

    if target in {"auto", "focused"}:
        try:
            control = auto.GetFocusedControl()
            if control:
                seeds.append(("focused", control))
        except Exception:
            pass

    if target in {"auto", "window"}:
        try:
            control = auto.GetForegroundControl()
            if control:
                seeds.append(("window", control))
        except Exception:
            pass

    unique: list[tuple[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for source, control in seeds:
        marker = _control_unique_key(control)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append((source, control))
    return unique


def _scan_text_from_control(control: Any, max_chars: int) -> tuple[str, str]:
    queue: deque[Any] = deque([control])
    visited: set[tuple[Any, ...]] = set()
    best_text = ""
    best_source = ""
    scanned = 0

    while queue and scanned < 220:
        current = queue.popleft()
        marker = _control_unique_key(current)
        if marker in visited:
            continue
        visited.add(marker)
        scanned += 1

        text, source = _extract_text_from_control(current)
        if len(text) > len(best_text):
            best_text = text
            best_source = source

        try:
            for child in current.GetChildren():
                queue.append(child)
        except Exception:
            continue

    if len(best_text) > max_chars:
        best_text = best_text[:max_chars]
    return best_text, best_source


def ensure_foreground_focus(
    workspace: str,
    desktop_manager: Any,
) -> dict[str, Any]:
    workspace = (workspace or "user").strip().lower()
    if not Config.ENABLE_UIA_BLIND_MODE:
        return {"success": False, "reason": "disabled", "workspace": workspace}
    if auto is None:
        return {
            "success": False,
            "reason": "import_error",
            "workspace": workspace,
            "error": UIA_IMPORT_ERROR,
        }

    def _focus() -> dict[str, Any]:
        try:
            foreground = auto.GetForegroundControl()
        except Exception:
            foreground = None

        if foreground:
            success, method = _apply_focus(foreground)
            if success:
                return {
                    "success": True,
                    "reason": "ok",
                    "workspace": workspace,
                    "method": method,
                }

        try:
            root = auto.GetRootControl()
        except Exception:
            root = None

        if root:
            success, method = _apply_focus(root)
            if success:
                return {
                    "success": True,
                    "reason": "ok",
                    "workspace": workspace,
                    "method": method,
                }

        return {"success": False, "reason": "no_focus_target", "workspace": workspace}

    try:
        return _run_in_workspace(workspace, desktop_manager, _focus)
    except Exception as exc:
        return {
            "success": False,
            "reason": "runtime_error",
            "workspace": workspace,
            "error": str(exc),
        }


def focus_element(
    workspace: str,
    desktop_manager: Any,
    ui_element_id: str,
) -> dict[str, Any]:
    workspace = (workspace or "user").strip().lower()
    target_id = str(ui_element_id or "").strip()
    if not target_id:
        return {"success": False, "reason": "missing_id", "workspace": workspace}
    if not Config.ENABLE_UIA_BLIND_MODE:
        return {"success": False, "reason": "disabled", "workspace": workspace}
    if auto is None:
        return {
            "success": False,
            "reason": "import_error",
            "workspace": workspace,
            "error": UIA_IMPORT_ERROR,
        }

    def _focus() -> dict[str, Any]:
        _, control_index = _scan_snapshot(
            workspace=workspace,
            max_nodes=max(Config.UIA_MAX_ELEMENTS * 3, 360),
            preferred_terms=None,
            scan_limit=max(Config.UIA_MAX_ELEMENTS * 30, 1200),
        )
        control = control_index.get(target_id)
        if control is None:
            return {
                "success": False,
                "reason": "not_found",
                "workspace": workspace,
                "ui_element_id": target_id,
            }

        rect = _rect_to_dict(_safe_attr(control, "BoundingRectangle", default=None))
        success, method = _apply_focus(control)
        return {
            "success": success,
            "reason": "ok" if success else "focus_failed",
            "workspace": workspace,
            "ui_element_id": target_id,
            "method": method,
            "rect": rect,
        }

    try:
        return _run_in_workspace(workspace, desktop_manager, _focus)
    except Exception as exc:
        return {
            "success": False,
            "reason": "runtime_error",
            "workspace": workspace,
            "ui_element_id": target_id,
            "error": str(exc),
        }


def activate_element(
    workspace: str,
    desktop_manager: Any,
    ui_element_id: str,
) -> dict[str, Any]:
    workspace = (workspace or "user").strip().lower()
    target_id = str(ui_element_id or "").strip()
    if not target_id:
        return {"success": False, "reason": "missing_id", "workspace": workspace}
    if not Config.ENABLE_UIA_BLIND_MODE:
        return {"success": False, "reason": "disabled", "workspace": workspace}
    if auto is None:
        return {
            "success": False,
            "reason": "import_error",
            "workspace": workspace,
            "error": UIA_IMPORT_ERROR,
        }

    def _activate() -> dict[str, Any]:
        control, rect = _resolve_element_control(workspace, target_id)
        if control is None:
            return {
                "success": False,
                "reason": "not_found",
                "workspace": workspace,
                "ui_element_id": target_id,
            }

        _apply_focus(control)
        try:
            invoke = control.GetInvokePattern()
            if invoke:
                invoke.Invoke()
                return {
                    "success": True,
                    "reason": "ok",
                    "workspace": workspace,
                    "ui_element_id": target_id,
                    "method": "InvokePattern.Invoke",
                    "rect": rect,
                }
        except Exception:
            pass

        try:
            legacy = control.GetLegacyIAccessiblePattern()
            if legacy:
                legacy.DoDefaultAction()
                return {
                    "success": True,
                    "reason": "ok",
                    "workspace": workspace,
                    "ui_element_id": target_id,
                    "method": "LegacyIAccessible.DoDefaultAction",
                    "rect": rect,
                }
        except Exception:
            pass

        for key in ("{Enter}", " "):
            try:
                control.SendKeys(key, waitTime=0.01)
                return {
                    "success": True,
                    "reason": "ok",
                    "workspace": workspace,
                    "ui_element_id": target_id,
                    "method": f"SendKeys({key})",
                    "rect": rect,
                }
            except Exception:
                continue

        return {
            "success": False,
            "reason": "activation_failed",
            "workspace": workspace,
            "ui_element_id": target_id,
            "rect": rect,
        }

    try:
        return _run_in_workspace(workspace, desktop_manager, _activate)
    except Exception as exc:
        return {
            "success": False,
            "reason": "runtime_error",
            "workspace": workspace,
            "ui_element_id": target_id,
            "error": str(exc),
        }


def list_windows(
    workspace: str,
    desktop_manager: Any,
    *,
    title_contains: str = "",
    process_name: str = "",
    visible_only: bool = False,
    max_windows: Optional[int] = None,
) -> dict[str, Any]:
    workspace = (workspace or "user").strip().lower()
    if not Config.ENABLE_UIA_BLIND_MODE:
        return {
            "available": False,
            "status": "error",
            "reason": "disabled",
            "workspace": workspace,
            "windows": [],
        }
    if auto is None:
        return {
            "available": False,
            "status": "error",
            "reason": "import_error",
            "workspace": workspace,
            "error": UIA_IMPORT_ERROR,
            "windows": [],
        }

    safe_max_windows = max(1, int(max_windows or Config.UIA_MAX_WINDOWS))

    def _list() -> dict[str, Any]:
        windows, _ = _scan_windows(
            max_windows=safe_max_windows,
            title_contains=title_contains or "",
            process_name=process_name or "",
            visible_only=bool(visible_only),
        )
        return {
            "available": True,
            "status": "ok",
            "reason": "ok",
            "workspace": workspace,
            "windows_count": len(windows),
            "windows": windows,
        }

    try:
        return _run_in_workspace(workspace, desktop_manager, _list)
    except Exception as exc:
        return {
            "available": False,
            "status": "error",
            "reason": "runtime_error",
            "workspace": workspace,
            "error": str(exc),
            "windows": [],
        }


def focus_window(
    workspace: str,
    desktop_manager: Any,
    *,
    window_id: Optional[str] = None,
    title_contains: str = "",
    process_name: str = "",
    restore: bool = True,
    maximize: bool = False,
) -> dict[str, Any]:
    workspace = (workspace or "user").strip().lower()
    target_window_id = str(window_id or "").strip()
    if not Config.ENABLE_UIA_BLIND_MODE:
        return {"success": False, "reason": "disabled", "workspace": workspace}
    if auto is None:
        return {
            "success": False,
            "reason": "import_error",
            "workspace": workspace,
            "error": UIA_IMPORT_ERROR,
        }

    def _focus_window() -> dict[str, Any]:
        windows, window_controls = _scan_windows(
            max_windows=max(Config.UIA_MAX_WINDOWS * 2, 50),
            title_contains=title_contains or "",
            process_name=process_name or "",
            visible_only=False,
        )
        target: Optional[dict[str, Any]] = None
        control: Any = None
        if target_window_id:
            target = next(
                (window for window in windows if window.get("window_id") == target_window_id),
                None,
            )
            control = window_controls.get(target_window_id)
        elif windows:
            target = windows[0]
            control = window_controls.get(str(target.get("window_id") or ""))

        if target is None or control is None:
            return {
                "success": False,
                "reason": "not_found",
                "workspace": workspace,
                "window_id": target_window_id or None,
            }

        handle = int(target.get("handle") or 0)
        _apply_window_show_state(handle, restore=bool(restore), maximize=bool(maximize))
        success, method = _apply_focus(control)
        return {
            "success": bool(success),
            "reason": "ok" if success else "focus_failed",
            "workspace": workspace,
            "window": target,
            "window_id": target.get("window_id"),
            "method": method,
        }

    try:
        return _run_in_workspace(workspace, desktop_manager, _focus_window)
    except Exception as exc:
        return {
            "success": False,
            "reason": "runtime_error",
            "workspace": workspace,
            "window_id": target_window_id or None,
            "error": str(exc),
        }


def get_snapshot(
    workspace: str,
    desktop_manager: Any,
    max_nodes: int,
    preferred_terms: Optional[list[str]] = None,
) -> dict[str, Any]:
    workspace = (workspace or "user").strip().lower()
    if not Config.ENABLE_UIA_BLIND_MODE:
        return _unavailable_snapshot(workspace, "UIA blind mode disabled")
    if auto is None:
        return _unavailable_snapshot(workspace, f"uiautomation unavailable: {UIA_IMPORT_ERROR}")

    try:
        snapshot, _ = _run_in_workspace(
            workspace,
            desktop_manager,
            _scan_snapshot,
            workspace=workspace,
            max_nodes=max_nodes,
            preferred_terms=preferred_terms,
        )
        return snapshot
    except Exception as exc:
        logger.debug("UIA snapshot failed on %s workspace: %s", workspace, exc)
        return _unavailable_snapshot(workspace, str(exc))


def get_element_rect(
    workspace: str,
    desktop_manager: Any,
    ui_element_id: str,
) -> dict[str, int] | None:
    workspace = (workspace or "user").strip().lower()
    target_id = str(ui_element_id or "").strip()
    if not target_id or not Config.ENABLE_UIA_BLIND_MODE or auto is None:
        return None

    def _resolve() -> dict[str, int] | None:
        control, rect = _resolve_element_control(workspace, target_id)
        if control is None:
            return None
        return rect

    try:
        return _run_in_workspace(workspace, desktop_manager, _resolve)
    except Exception:
        return None


def _clean_text(text: str) -> str:
    lines = [line.rstrip() for line in (text or "").splitlines()]
    cleaned: list[str] = []
    blank_run = 0
    for line in lines:
        if line:
            blank_run = 0
            cleaned.append(line)
            continue
        blank_run += 1
        if blank_run <= 1:
            cleaned.append("")
    return "\n".join(cleaned).strip()


def _noise_ratio(text: str) -> float:
    if not text:
        return 1.0

    noise_count = 0
    for ch in text:
        code = ord(ch)
        if ch in {"\ufffc", "\u200c", "\u034f"}:
            noise_count += 1
            continue
        if code < 32 and ch not in {"\n", "\t", "\r"}:
            noise_count += 1
    return noise_count / max(1, len(text))


def _uia_needs_ocr_fallback(
    *,
    text: str,
    source: str,
    min_chars: int,
    max_noise_ratio: float,
) -> tuple[bool, str, float]:
    ratio = _noise_ratio(text)
    if not text:
        return True, "no_uia_text", ratio
    if len(text) < min_chars:
        return True, "uia_text_too_short", ratio
    if ratio > max_noise_ratio:
        return True, "uia_text_too_noisy", ratio
    if source in {"Control.Name", "LegacyIAccessible.Name"} and len(text) < (min_chars * 2):
        return True, "uia_source_low_confidence", ratio
    return False, "uia_text_good", ratio


def _import_ocr_engine() -> tuple[Any | None, str]:
    global _OCR_ENGINE
    global _OCR_IMPORT_ERROR

    if _OCR_ENGINE is not None:
        return _OCR_ENGINE, ""
    if _OCR_IMPORT_ERROR:
        return None, _OCR_IMPORT_ERROR

    try:
        from rapidocr_onnxruntime import RapidOCR

        _OCR_ENGINE = RapidOCR()
        return _OCR_ENGINE, ""
    except Exception as exc:
        _OCR_IMPORT_ERROR = str(exc)
        return None, _OCR_IMPORT_ERROR


def _capture_workspace_image(workspace: str, desktop_manager: Any) -> Optional[Image.Image]:
    if workspace == "agent" and desktop_manager and getattr(desktop_manager, "is_created", False):
        try:
            img = desktop_manager.capture_desktop()
            if img is not None:
                return img.convert("RGB")
        except Exception:
            pass

    if pyautogui is None:
        return None
    try:
        return pyautogui.screenshot().convert("RGB")
    except Exception:
        return None


def _extract_text_with_ocr(
    *,
    workspace: str,
    desktop_manager: Any,
    max_chars: int,
) -> tuple[str, dict[str, Any]]:
    engine, import_error = _import_ocr_engine()
    if engine is None:
        return "", {
            "available": False,
            "provider": "RapidOCR",
            "error": f"rapidocr_onnxruntime not available: {import_error}",
        }

    image = _capture_workspace_image(workspace, desktop_manager)
    if image is None:
        return "", {
            "available": True,
            "provider": "RapidOCR",
            "error": "screen_capture_failed",
        }

    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            image.save(tmp, format="PNG")
            temp_path = Path(tmp.name)

        ocr_result, _ = engine(str(temp_path))
        lines: list[str] = []
        for item in ocr_result or []:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                maybe_text = item[1]
                if isinstance(maybe_text, str) and maybe_text.strip():
                    lines.append(maybe_text.strip())

        text = _clean_text("\n".join(lines))
        if len(text) > max_chars:
            text = text[:max_chars]

        return text, {
            "available": True,
            "provider": "RapidOCR",
            "line_count": len(lines),
        }
    except Exception as exc:
        return "", {
            "available": True,
            "provider": "RapidOCR",
            "error": f"OCR execution failed: {exc}",
        }
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass


def read_text(
    workspace: str,
    desktop_manager: Any,
    target: str,
    ui_element_id: Optional[str] = None,
    max_chars: int = 4000,
    *,
    use_ocr_fallback: bool = False,
    force_ocr: bool = False,
    ocr_min_chars: Optional[int] = None,
    ocr_max_noise_ratio: Optional[float] = None,
) -> dict[str, Any]:
    workspace = (workspace or "user").strip().lower()
    safe_target = (target or "auto").strip().lower()
    safe_max_chars = max(1, int(max_chars or Config.UIA_TEXT_MAX_CHARS))
    safe_ocr_min_chars = max(
        40,
        min(int(ocr_min_chars or Config.UIA_TEXT_OCR_MIN_CHARS), 2000),
    )
    safe_ocr_max_noise_ratio = max(
        0.01,
        min(float(ocr_max_noise_ratio or Config.UIA_TEXT_OCR_MAX_NOISE_RATIO), 0.95),
    )

    if not Config.ENABLE_UIA_BLIND_MODE:
        return {
            "available": False,
            "status": "error",
            "reason": "disabled",
            "text": "",
            "source": "",
            "workspace": workspace,
        }
    if auto is None:
        return {
            "available": False,
            "status": "error",
            "reason": "import_error",
            "text": "",
            "source": "",
            "workspace": workspace,
            "error": UIA_IMPORT_ERROR,
        }

    def _read() -> dict[str, Any]:
        snapshot, control_index = _scan_snapshot(
            workspace=workspace,
            max_nodes=max(Config.UIA_MAX_ELEMENTS * 2, 240),
            preferred_terms=None,
            scan_limit=max(Config.UIA_MAX_ELEMENTS * 25, 900),
        )
        seeds = _seed_controls(
            target=safe_target,
            ui_element_id=ui_element_id,
            control_index=control_index,
        )
        if not seeds:
            return {
                "available": True,
                "status": "error",
                "reason": "no_target",
                "text": "",
                "source": "",
                "workspace": workspace,
                "target": safe_target,
                "ui_element_id": ui_element_id,
                "active_window_title": snapshot.get("active_window_title", ""),
                "active_window_class": snapshot.get("active_window_class", ""),
            }

        best_text = ""
        best_source = ""
        best_seed = ""
        for seed_source, control in seeds:
            text, source = _scan_text_from_control(control, safe_max_chars)
            if len(text) > len(best_text):
                best_text = text
                best_source = source
                best_seed = seed_source

        needs_fallback, fallback_reason, ui_noise_ratio = _uia_needs_ocr_fallback(
            text=best_text,
            source=best_source,
            min_chars=safe_ocr_min_chars,
            max_noise_ratio=safe_ocr_max_noise_ratio,
        )
        should_use_ocr = bool(force_ocr or (use_ocr_fallback and needs_fallback))
        if should_use_ocr:
            ocr_text, ocr_meta = _extract_text_with_ocr(
                workspace=workspace,
                desktop_manager=desktop_manager,
                max_chars=safe_max_chars,
            )
            if ocr_text:
                return {
                    "available": True,
                    "status": "ok",
                    "reason": "ok",
                    "text": ocr_text,
                    "source": "OCR.RapidOCR",
                    "workspace": workspace,
                    "target": safe_target,
                    "ui_element_id": ui_element_id,
                    "seed_source": "screen",
                    "active_window_title": snapshot.get("active_window_title", ""),
                    "active_window_class": snapshot.get("active_window_class", ""),
                    "fallback": {
                        "applied": True,
                        "reason": "forced" if force_ocr else fallback_reason,
                        "uia_source": best_source,
                        "uia_text_length": len(best_text),
                        "uia_noise_ratio": round(ui_noise_ratio, 4),
                        "ocr": ocr_meta,
                    },
                }
            if not best_text:
                return {
                    "available": True,
                    "status": "error",
                    "reason": "no_text",
                    "text": "",
                    "source": "",
                    "workspace": workspace,
                    "target": safe_target,
                    "ui_element_id": ui_element_id,
                    "seed_source": best_seed,
                    "active_window_title": snapshot.get("active_window_title", ""),
                    "active_window_class": snapshot.get("active_window_class", ""),
                    "fallback": {
                        "applied": True,
                        "reason": "forced" if force_ocr else fallback_reason,
                        "uia_source": best_source,
                        "uia_text_length": 0,
                        "uia_noise_ratio": round(ui_noise_ratio, 4),
                        "ocr": ocr_meta,
                    },
                }

        status = "ok" if best_text else "error"
        reason = "ok" if best_text else "no_text"
        return {
            "available": True,
            "status": status,
            "reason": reason,
            "text": best_text,
            "source": best_source,
            "workspace": workspace,
            "target": safe_target,
            "ui_element_id": ui_element_id,
            "seed_source": best_seed,
            "active_window_title": snapshot.get("active_window_title", ""),
            "active_window_class": snapshot.get("active_window_class", ""),
            "fallback": {
                "applied": bool(should_use_ocr),
                "reason": "forced" if force_ocr else fallback_reason,
                "uia_noise_ratio": round(ui_noise_ratio, 4),
            },
        }

    try:
        return _run_in_workspace(workspace, desktop_manager, _read)
    except Exception as exc:
        return {
            "available": False,
            "status": "error",
            "reason": "runtime_error",
            "text": "",
            "source": "",
            "workspace": workspace,
            "target": safe_target,
            "ui_element_id": ui_element_id,
            "error": str(exc),
        }
