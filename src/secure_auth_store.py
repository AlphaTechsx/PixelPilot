from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional


class SecureAuthStore:
    def __init__(self, *, backend_url: str, legacy_path: Path) -> None:
        self.backend_url = str(backend_url or "").strip()
        digest = hashlib.sha256(self.backend_url.encode("utf-8")).hexdigest()[:16]
        self.service_name = f"PixelPilot.Auth.{digest}"
        self.username = "session"
        self.legacy_path = Path(legacy_path)

    def _load_keyring(self):
        try:
            import keyring
            from keyring.errors import PasswordDeleteError
        except Exception as exc:
            raise RuntimeError(
                "Secure Windows credential storage is unavailable. Install the keyring dependency."
            ) from exc
        return keyring, PasswordDeleteError

    def is_available(self) -> bool:
        try:
            self._load_keyring()
            return True
        except RuntimeError:
            return False

    def load(self) -> Optional[dict[str, Any]]:
        if not self.is_available():
            return None
        self.migrate_legacy_token()
        keyring, _ = self._load_keyring()
        raw = keyring.get_password(self.service_name, self.username)
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def save(self, payload: dict[str, Any]) -> None:
        keyring, _ = self._load_keyring()
        keyring.set_password(
            self.service_name,
            self.username,
            json.dumps(dict(payload or {})),
        )

    def clear(self) -> None:
        if not self.is_available():
            return
        keyring, password_delete_error = self._load_keyring()
        try:
            keyring.delete_password(self.service_name, self.username)
        except password_delete_error:
            return

    def migrate_legacy_token(self) -> None:
        if not self.is_available():
            return
        if not self.legacy_path.exists():
            return

        raw = self.legacy_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
        if isinstance(payload, dict) and payload.get("access_token"):
            self.save(payload)
        self.legacy_path.unlink(missing_ok=True)
