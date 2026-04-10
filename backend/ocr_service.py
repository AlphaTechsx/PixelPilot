from __future__ import annotations

import io
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger("backend.ocr")

OCR_USE_GPU = (os.getenv("OCR_USE_GPU", "auto").strip().lower() or "auto")
OCR_MODEL_DIR = Path(
    os.getenv("TORCHFREE_OCR_MODULE_PATH")
    or os.getenv("EASYOCR_MODEL_DIR")
    or str(Path(__file__).resolve().parent / ".easyocr-onnx")
)

_READER_LOCK = threading.Lock()
_READERS: dict[str, Any] = {}


def _import_easyocr_onnx():
    import torchfree_ocr
    from torchfree_ocr import utils as torchfree_utils

    progress_factory = getattr(torchfree_utils, "printProgressBar", None)
    if callable(progress_factory) and not getattr(progress_factory, "_pixelpilot_ascii", False):
        def ascii_progress_bar(
            prefix: str = "",
            suffix: str = "",
            decimals: int = 1,
            length: int = 100,
            fill: str = "#",
        ):
            return progress_factory(
                prefix=prefix,
                suffix=suffix,
                decimals=decimals,
                length=length,
                fill=fill,
            )

        ascii_progress_bar._pixelpilot_ascii = True
        torchfree_utils.printProgressBar = ascii_progress_bar

    return torchfree_ocr


def _device_name() -> str:
    return "cpu"


def _validate_device_config() -> None:
    mode = OCR_USE_GPU
    if mode not in {"auto", "off", "require"}:
        mode = "auto"
    if mode == "require":
        raise RuntimeError(
            "EasyOCR-ONNX is CPU-only; OCR_USE_GPU=require is not supported."
        )


def _create_reader(lang: str):
    os.environ.setdefault("TORCHFREE_OCR_MODULE_PATH", str(OCR_MODEL_DIR))
    torchfree_ocr = _import_easyocr_onnx()

    OCR_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Initializing backend EasyOCR-ONNX reader (lang=%s, device=%s)",
        lang,
        _device_name(),
    )
    return torchfree_ocr.Reader([lang])


def _get_reader(lang: str) -> tuple[Any, str]:
    _validate_device_config()
    key = (lang or "en").strip().lower() or "en"
    reader = _READERS.get(key)
    if reader is not None:
        return reader, _device_name()

    with _READER_LOCK:
        reader = _READERS.get(key)
        if reader is None:
            reader = _create_reader(key)
            _READERS[key] = reader
    return reader, _device_name()


def prefetch_reader(lang: str = "en") -> str:
    _, device = _get_reader(lang)
    return device


def validate_image_bytes(image_bytes: bytes, mime_type: str) -> None:
    safe_mime = str(mime_type or "").strip().lower()
    if not safe_mime.startswith("image/"):
        raise ValueError("mime_type must be an image/* type")
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.verify()
    except UnidentifiedImageError as exc:
        raise ValueError("Unsupported image payload") from exc
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid image payload: {exc}") from exc


def run_easyocr_array(np_image: np.ndarray, *, lang: str = "en") -> dict[str, Any]:
    total_start = time.perf_counter()
    reader, device = _get_reader(lang)

    ocr_start = time.perf_counter()
    raw_results = reader.readtext(np_image)
    ocr_ms = int((time.perf_counter() - ocr_start) * 1000)

    results: list[dict[str, Any]] = []
    for item in raw_results or []:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        bbox, text, confidence = item[0], item[1], item[2]
        normalized_bbox: list[list[float]] = []
        for point in bbox or []:
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                normalized_bbox.append([float(point[0]), float(point[1])])
        if not normalized_bbox:
            continue
        results.append(
            {
                "bbox": normalized_bbox,
                "text": str(text or ""),
                "confidence": float(confidence or 0.0),
            }
        )

    total_ms = int((time.perf_counter() - total_start) * 1000)
    return {
        "provider": "easyocr-onnx",
        "device": device,
        "hosted": True,
        "results": results,
        "timings_ms": {
            "ocr": ocr_ms,
            "total": total_ms,
        },
    }


def run_easyocr(image_bytes: bytes, *, lang: str = "en") -> dict[str, Any]:
    total_start = time.perf_counter()
    decode_start = time.perf_counter()
    with Image.open(io.BytesIO(image_bytes)) as image:
        rgb_image = image.convert("RGB")
        np_image = np.array(rgb_image)[:, :, ::-1]
    decode_ms = int((time.perf_counter() - decode_start) * 1000)

    payload = run_easyocr_array(np_image, lang=lang)
    timings_ms = dict(payload.get("timings_ms") or {})
    timings_ms["decode"] = decode_ms
    timings_ms["total"] = int((time.perf_counter() - total_start) * 1000)
    payload["timings_ms"] = timings_ms
    return payload
