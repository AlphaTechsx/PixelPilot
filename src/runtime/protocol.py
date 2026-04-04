from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import Any
from uuid import uuid4


BRIDGE_PROTOCOL_VERSION = 1
SIDE_CAR_HEADER_STRUCT = struct.Struct("!I")
VALID_ENVELOPE_KINDS = {"command", "event", "request", "response", "error"}


@dataclass(slots=True)
class BridgeEnvelope:
    id: str
    kind: str
    method: str
    payload: dict[str, Any]
    protocol_version: int = BRIDGE_PROTOCOL_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "method": self.method,
            "payload": dict(self.payload),
            "protocolVersion": int(self.protocol_version),
        }


def make_envelope(
    kind: str,
    method: str,
    payload: dict[str, Any] | None = None,
    *,
    message_id: str | None = None,
) -> dict[str, Any]:
    envelope = BridgeEnvelope(
        id=str(message_id or uuid4()),
        kind=str(kind or "").strip().lower(),
        method=str(method or "").strip(),
        payload=dict(payload or {}),
    )
    parsed = validate_envelope(envelope.as_dict())
    return parsed


def validate_envelope(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Bridge envelope must be a JSON object.")

    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in VALID_ENVELOPE_KINDS:
        raise ValueError(f"Unsupported bridge envelope kind: {kind or 'missing'}")

    method = str(raw.get("method") or "").strip()
    if not method:
        raise ValueError("Bridge envelope is missing method.")

    payload = raw.get("payload")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("Bridge envelope payload must be an object.")

    message_id = str(raw.get("id") or "").strip() or str(uuid4())

    protocol_version = raw.get("protocolVersion", BRIDGE_PROTOCOL_VERSION)
    try:
        protocol_version = int(protocol_version)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Bridge protocolVersion must be an integer.") from exc

    return {
        "id": message_id,
        "kind": kind,
        "method": method,
        "payload": payload,
        "protocolVersion": protocol_version,
    }


def parse_envelope_text(raw_text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(str(raw_text or ""))
    except json.JSONDecodeError as exc:
        raise ValueError("Bridge message is not valid JSON.") from exc
    return validate_envelope(parsed)


def pack_sidecar_frame(jpeg_bytes: bytes, metadata: dict[str, Any]) -> bytes:
    meta_payload = json.dumps(dict(metadata or {}), separators=(",", ":")).encode("utf-8")
    return SIDE_CAR_HEADER_STRUCT.pack(len(meta_payload)) + meta_payload + bytes(jpeg_bytes or b"")


def unpack_sidecar_frame(packet: bytes) -> tuple[dict[str, Any], bytes]:
    blob = bytes(packet or b"")
    if len(blob) < SIDE_CAR_HEADER_STRUCT.size:
        raise ValueError("Sidecar frame packet is too small.")

    (header_size,) = SIDE_CAR_HEADER_STRUCT.unpack(blob[: SIDE_CAR_HEADER_STRUCT.size])
    meta_start = SIDE_CAR_HEADER_STRUCT.size
    meta_end = meta_start + int(header_size)
    if len(blob) < meta_end:
        raise ValueError("Sidecar frame header is truncated.")

    try:
        metadata = json.loads(blob[meta_start:meta_end].decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Sidecar metadata is not valid JSON.") from exc

    if not isinstance(metadata, dict):
        raise ValueError("Sidecar metadata must be an object.")

    return metadata, blob[meta_end:]
