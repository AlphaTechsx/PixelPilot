"""
Authentication manager for the client.
Handles login, logout, token storage, and session management.
"""

import os
import json
import requests
from typing import Optional, Dict
from pathlib import Path


class AuthManager:
    """Manages user authentication state and token storage."""

    def __init__(self, backend_url: str = "http://localhost:8000"):
        self.backend_url = backend_url
        self._token: Optional[str] = None
        self._user_id: Optional[str] = None
        self._email: Optional[str] = None

        # Token storage path
        self._storage_dir = Path.home() / ".tolin"
        self._storage_file = self._storage_dir / "auth.json"

        # Load existing token if available
        self._load_token()

    def _load_token(self):
        """Load token from local storage."""
        try:
            if self._storage_file.exists():
                with open(self._storage_file, "r") as f:
                    data = json.load(f)
                    self._token = data.get("access_token")
                    self._user_id = data.get("user_id")
                    self._email = data.get("email")
        except Exception:
            pass

    def _save_token(self):
        """Save token to local storage."""
        try:
            self._storage_dir.mkdir(parents=True, exist_ok=True)
            with open(self._storage_file, "w") as f:
                json.dump(
                    {
                        "access_token": self._token,
                        "user_id": self._user_id,
                        "email": self._email,
                    },
                    f,
                )
        except Exception as e:
            print(f"Warning: Could not save auth token: {e}")

    def _clear_token(self):
        """Clear stored token."""
        self._token = None
        self._user_id = None
        self._email = None
        try:
            if self._storage_file.exists():
                os.remove(self._storage_file)
        except Exception:
            pass

    @property
    def is_logged_in(self) -> bool:
        """Check if user is logged in with a valid token."""
        return self._token is not None

    @property
    def token(self) -> Optional[str]:
        """Get the current access token."""
        return self._token

    @property
    def user_id(self) -> Optional[str]:
        """Get the current user ID."""
        return self._user_id

    @property
    def email(self) -> Optional[str]:
        """Get the current user email."""
        return self._email

    def register(self, email: str, password: str) -> Dict:
        """
        Register a new user.

        Returns:
            Dict with user info on success

        Raises:
            RuntimeError on failure
        """
        try:
            response = requests.post(
                f"{self.backend_url}/auth/register",
                json={"email": email, "password": password},
            )

            if response.status_code == 400:
                data = response.json()
                raise RuntimeError(data.get("detail", "Registration failed"))

            response.raise_for_status()
            data = response.json()

            self._token = data["access_token"]
            self._user_id = data["user_id"]
            self._email = data["email"]
            self._save_token()

            return {
                "user_id": self._user_id,
                "email": self._email,
            }
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Registration failed: {e}")

    def login(self, email: str, password: str) -> Dict:
        """
        Login with email and password.

        Returns:
            Dict with user info on success

        Raises:
            RuntimeError on failure
        """
        try:
            response = requests.post(
                f"{self.backend_url}/auth/login",
                json={"email": email, "password": password},
            )

            if response.status_code == 401:
                raise RuntimeError("Invalid email or password")

            response.raise_for_status()
            data = response.json()

            self._token = data["access_token"]
            self._user_id = data["user_id"]
            self._email = data["email"]
            self._save_token()

            return {
                "user_id": self._user_id,
                "email": self._email,
            }
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Login failed: {e}")

    def logout(self):
        """Logout and clear stored token."""
        self._clear_token()

    def verify_token(self) -> bool:
        """
        Verify that the stored token is still valid.

        Returns:
            True if token is valid, False otherwise
        """
        if not self._token:
            return False

        try:
            response = requests.get(
                f"{self.backend_url}/auth/me",
                headers={"Authorization": f"Bearer {self._token}"},
            )

            if response.status_code == 401:
                self._clear_token()
                return False

            response.raise_for_status()
            return True
        except Exception:
            return False


# Global instance
_auth_manager: Optional[AuthManager] = None


def get_auth_manager() -> AuthManager:
    """Get the global auth manager instance."""
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = AuthManager()
    return _auth_manager
