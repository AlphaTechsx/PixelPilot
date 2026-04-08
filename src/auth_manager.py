import json
import os
import secrets
import socket
from pathlib import Path
from typing import Optional
from urllib import error, request
from urllib.parse import urlencode

from config import Config
from secure_auth_store import SecureAuthStore


class AuthManager:
    def __init__(self, backend_url: Optional[str] = None):
        self.backend_url = (backend_url or Config.BACKEND_URL).rstrip("/")
        self.access_token: Optional[str] = None
        self.user_id: Optional[str] = None
        self.email: Optional[str] = None
        self.token_type: str = "bearer"
        base_dir = Path(os.path.expanduser("~")) / ".pixelpilot"
        self._token_path = base_dir / "auth.json"
        self._pending_state_path = base_dir / "pending_auth.json"
        self._secure_store = SecureAuthStore(
            backend_url=self.backend_url,
            legacy_path=self._token_path,
        )
        self._load_token()

    @property
    def is_logged_in(self) -> bool:
        return bool(self.access_token)

    def _load_token(self) -> None:
        try:
            data = self._secure_store.load() or {}
            self.access_token = data.get("access_token")
            self.user_id = data.get("user_id")
            self.email = data.get("email")
            self.token_type = data.get("token_type", "bearer")
        except Exception:
            self.access_token = None
            self.user_id = None
            self.email = None
            self.token_type = "bearer"

    def _save_token(self, token: dict) -> None:
        payload = {
            "access_token": token.get("access_token"),
            "user_id": token.get("user_id"),
            "email": token.get("email"),
            "token_type": token.get("token_type", "bearer"),
        }
        self._secure_store.save(payload)

    def _clear_token(self) -> None:
        self.access_token = None
        self.user_id = None
        self.email = None
        self.token_type = "bearer"
        self._secure_store.clear()

    def _request_json(
        self,
        method: str,
        path: str,
        data: Optional[dict] = None,
        headers: Optional[dict] = None,
    ) -> dict:
        url = f"{self.backend_url}{path}"
        payload = None
        if data is not None:
            payload = json.dumps(data).encode("utf-8")

        req_headers = {"Content-Type": "application/json"}
        if headers:
            req_headers.update(headers)

        req = request.Request(url, data=payload, method=method, headers=req_headers)
        try:
            with request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except error.HTTPError as exc:
            detail = "Request failed"
            try:
                body = exc.read().decode("utf-8")
                if body:
                    parsed = json.loads(body)
                    detail = parsed.get("detail", detail)
            except Exception:
                pass
            raise RuntimeError(str(detail)) from exc
        except error.URLError as exc:
            raise RuntimeError(
                f"Backend unavailable at {self.backend_url}. Is it running?"
            ) from exc
        except TimeoutError as exc:
            raise RuntimeError(
                f"Backend request to {self.backend_url} timed out."
            ) from exc
        except socket.timeout as exc:
            raise RuntimeError(
                f"Backend request to {self.backend_url} timed out."
            ) from exc

    def _set_token(self, token: dict) -> None:
        self.access_token = token.get("access_token")
        self.user_id = token.get("user_id")
        self.email = token.get("email")
        self.token_type = token.get("token_type", "bearer")
        if not self.access_token:
            raise RuntimeError("Authentication failed: no access token returned")
        self._save_token(token)

    def _read_pending_state(self) -> Optional[dict]:
        if not self._pending_state_path.exists():
            return None
        try:
            raw = self._pending_state_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    def _write_pending_state(self, *, state: str, mode: str) -> None:
        self._pending_state_path.parent.mkdir(parents=True, exist_ok=True)
        self._pending_state_path.write_text(
            json.dumps({"state": state, "mode": mode}),
            encoding="utf-8",
        )

    def _clear_pending_state(self) -> None:
        try:
            self._pending_state_path.unlink(missing_ok=True)
        except Exception:
            pass

    def login(self, email: str, password: str) -> None:
        token = self._request_json(
            "POST",
            "/auth/login",
            {"email": email, "password": password},
        )
        self._set_token(token)

    def register(self, email: str, password: str) -> None:
        token = self._request_json(
            "POST",
            "/auth/register",
            {"email": email, "password": password},
        )
        self._set_token(token)

    def verify_token(self) -> bool:
        if not self.access_token:
            return False

        try:
            data = self._request_json(
                "GET",
                "/auth/me",
                headers={"Authorization": f"Bearer {self.access_token}"},
            )
            self.user_id = data.get("user_id", self.user_id)
            self.email = data.get("email", self.email)
            self._save_token(
                {
                    "access_token": self.access_token,
                    "user_id": self.user_id,
                    "email": self.email,
                    "token_type": self.token_type,
                }
            )
            return True
        except RuntimeError:
            return False

    def start_browser_flow(self, mode: str) -> dict:
        web_url = str(Config.WEB_URL or "").rstrip("/")
        if not web_url:
            raise RuntimeError("WEB_URL is not configured.")
        normalized_mode = str(mode or "signin").strip().lower() or "signin"
        if normalized_mode not in {"signin", "signup"}:
            raise RuntimeError("Unsupported auth flow.")

        state = secrets.token_urlsafe(24)
        self._write_pending_state(state=state, mode=normalized_mode)
        path = "/auth/sign-up" if normalized_mode == "signup" else "/auth/sign-in"
        url = f"{web_url}{path}?{urlencode({'desktop_state': state})}"
        return {"url": url, "state": state, "mode": normalized_mode}

    def exchange_desktop_code(self, code: str, state: Optional[str] = None) -> None:
        clean_code = str(code or "").strip()
        if not clean_code:
            raise RuntimeError("Please enter the browser code.")

        pending = self._read_pending_state() or {}
        expected_state = str(state or pending.get("state") or "").strip()
        if not expected_state:
            raise RuntimeError("No browser sign-in is pending. Start the browser flow again.")

        token = self._request_json(
            "POST",
            "/auth/desktop/redeem",
            {"code": clean_code, "state": expected_state},
        )
        self._set_token(token)
        self._clear_pending_state()

    def logout(self) -> None:
        self._clear_token()
        self._clear_pending_state()


_auth_manager: Optional[AuthManager] = None


def get_auth_manager() -> AuthManager:
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = AuthManager()
    return _auth_manager
