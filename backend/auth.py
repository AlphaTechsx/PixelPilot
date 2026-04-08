"""
Authentication helpers for email/password, OAuth account linking, and desktop handoff codes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import bcrypt
import jwt
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, EmailStr

JWT_SECRET = os.getenv("JWT_SECRET", "default_secret_change_me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24 * 7  # 1 week

OAUTH_STATE_TTL_SECONDS = 60 * 10
DESKTOP_CODE_TTL_SECONDS = 60 * 5


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_email(value: str) -> str:
    return str(value or "").strip().lower()


def _stringify_object_id(value: Any) -> str:
    return str(value if not isinstance(value, ObjectId) else value)


def _user_response(user_doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": _stringify_object_id(user_doc["_id"]),
        "email": str(user_doc["email"]),
        "email_verified": bool(user_doc.get("email_verified", False)),
    }


def _token_payload(user_id: str, email: str) -> dict[str, Any]:
    expires = utcnow() + timedelta(hours=JWT_EXPIRY_HOURS)
    return {
        "sub": str(user_id),
        "email": _normalize_email(email),
        "exp": expires,
        "iat": utcnow(),
    }


def users_collection(db: AsyncIOMotorDatabase):
    return db.users


def oauth_identities_collection(db: AsyncIOMotorDatabase):
    return db.oauth_identities


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str


class UserInfo(BaseModel):
    user_id: str
    email: str


class DesktopCodeIssueRequest(BaseModel):
    state: str


class DesktopCodeIssueResponse(BaseModel):
    code: str
    expiresIn: int = DESKTOP_CODE_TTL_SECONDS


class DesktopCodeRedeemRequest(BaseModel):
    code: str
    state: str


async def ensure_auth_indexes(db: AsyncIOMotorDatabase) -> None:
    await users_collection(db).create_index("email", unique=True)
    await oauth_identities_collection(db).create_index(
        [("provider", 1), ("provider_subject", 1)],
        unique=True,
    )


async def register_user(
    email: str,
    password: str,
    db: AsyncIOMotorDatabase,
    *,
    email_verified: bool = False,
) -> dict[str, Any]:
    users = users_collection(db)
    normalized_email = _normalize_email(email)

    existing = await users.find_one({"email": normalized_email})
    if existing:
        raise ValueError("User with this email already exists")

    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(str(password).encode("utf-8"), salt)
    now = utcnow()
    user_doc = {
        "email": normalized_email,
        "password_hash": hashed.decode("utf-8"),
        "email_verified": bool(email_verified),
        "created_at": now,
        "updated_at": now,
        "last_login_at": now,
        "is_active": True,
    }
    result = await users.insert_one(user_doc)
    user_doc["_id"] = result.inserted_id
    return _user_response(user_doc)


async def authenticate_user(
    email: str,
    password: str,
    db: AsyncIOMotorDatabase,
) -> Optional[dict[str, Any]]:
    users = users_collection(db)
    user = await users.find_one({"email": _normalize_email(email)})
    if not user:
        return None

    password_hash = str(user.get("password_hash") or "")
    if not password_hash:
        return None

    if not bcrypt.checkpw(str(password).encode("utf-8"), password_hash.encode("utf-8")):
        return None

    await users.update_one(
        {"_id": user["_id"]},
        {"$set": {"updated_at": utcnow(), "last_login_at": utcnow()}},
    )
    user["last_login_at"] = utcnow()
    return _user_response(user)


def create_access_token(user_id: str, email: str) -> str:
    return jwt.encode(_token_payload(user_id, email), JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_access_token(token: str) -> Optional[dict[str, Any]]:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return {
            "user_id": str(payload["sub"]),
            "email": str(payload["email"]),
        }
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def token_response_for_user(user_id: str, email: str) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(user_id, email),
        user_id=str(user_id),
        email=_normalize_email(email),
    )


async def get_user_by_id(user_id: str, db: AsyncIOMotorDatabase) -> Optional[dict[str, Any]]:
    users = users_collection(db)
    try:
        user = await users.find_one({"_id": ObjectId(user_id)})
    except Exception:
        return None

    if not user:
        return None
    return _user_response(user)


async def find_or_create_oauth_user(
    *,
    provider: str,
    provider_subject: str,
    email: str,
    email_verified: bool,
    profile: dict[str, Any],
    db: AsyncIOMotorDatabase,
) -> dict[str, Any]:
    if not email_verified:
        raise ValueError("OAuth email must be verified before linking")

    provider_name = str(provider or "").strip().lower()
    subject = str(provider_subject or "").strip()
    normalized_email = _normalize_email(email)
    if not provider_name or not subject:
        raise ValueError("OAuth provider identity is incomplete")
    if not normalized_email:
        raise ValueError("OAuth provider did not return an email address")

    users = users_collection(db)
    identities = oauth_identities_collection(db)
    now = utcnow()

    existing_identity = await identities.find_one(
        {"provider": provider_name, "provider_subject": subject}
    )
    if existing_identity:
        user = await users.find_one({"_id": existing_identity["user_id"]})
        if not user:
            raise ValueError("Linked OAuth account is missing its user record")
        await users.update_one(
            {"_id": user["_id"]},
            {
                "$set": {
                    "email": normalized_email,
                    "email_verified": True,
                    "updated_at": now,
                    "last_login_at": now,
                }
            },
        )
        await identities.update_one(
            {"_id": existing_identity["_id"]},
            {
                "$set": {
                    "email": normalized_email,
                    "email_verified": True,
                    "profile": dict(profile or {}),
                    "updated_at": now,
                }
            },
        )
        user["email"] = normalized_email
        user["email_verified"] = True
        return _user_response(user)

    user = await users.find_one({"email": normalized_email})
    if user:
        await users.update_one(
            {"_id": user["_id"]},
            {"$set": {"email_verified": True, "updated_at": now, "last_login_at": now}},
        )
    else:
        user_doc = {
            "email": normalized_email,
            "password_hash": None,
            "email_verified": True,
            "created_at": now,
            "updated_at": now,
            "last_login_at": now,
            "is_active": True,
        }
        result = await users.insert_one(user_doc)
        user_doc["_id"] = result.inserted_id
        user = user_doc

    await identities.insert_one(
        {
            "user_id": user["_id"],
            "provider": provider_name,
            "provider_subject": subject,
            "email": normalized_email,
            "email_verified": True,
            "profile": dict(profile or {}),
            "created_at": now,
            "updated_at": now,
        }
    )
    return _user_response(user)


def create_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    verifier = verifier[:128]
    if len(verifier) < 43:
        verifier = f"{verifier}{secrets.token_urlsafe(43)}"[:43]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("utf-8")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


async def store_google_oauth_state(
    redis_client,
    *,
    state: str,
    code_verifier: str,
    desktop_state: str,
    mode: str,
) -> None:
    payload = json.dumps(
        {
            "code_verifier": str(code_verifier),
            "desktop_state": str(desktop_state),
            "mode": str(mode or "signin"),
            "created_at": utcnow().isoformat(),
        }
    )
    await redis_client.setex(
        f"pixelpilot:oauth:google:state:{state}",
        OAUTH_STATE_TTL_SECONDS,
        payload,
    )


async def pop_google_oauth_state(redis_client, state: str) -> Optional[dict[str, Any]]:
    key = f"pixelpilot:oauth:google:state:{state}"
    raw = await redis_client.get(key)
    if raw is None:
        return None
    await redis_client.delete(key)
    try:
        return json.loads(raw)
    except Exception:
        return None


async def issue_desktop_code(
    redis_client,
    *,
    user_id: str,
    email: str,
    state: str,
) -> DesktopCodeIssueResponse:
    code = secrets.token_urlsafe(24)
    payload = {
        "access_token": create_access_token(user_id, email),
        "token_type": "bearer",
        "user_id": str(user_id),
        "email": _normalize_email(email),
        "state": str(state),
        "created_at": utcnow().isoformat(),
    }
    await redis_client.setex(
        f"pixelpilot:desktop:code:{code}",
        DESKTOP_CODE_TTL_SECONDS,
        json.dumps(payload),
    )
    return DesktopCodeIssueResponse(code=code)


async def redeem_desktop_code(
    redis_client,
    *,
    code: str,
    state: str,
) -> Optional[TokenResponse]:
    key = f"pixelpilot:desktop:code:{code}"
    raw = await redis_client.get(key)
    if raw is None:
        return None
    await redis_client.delete(key)
    try:
        payload = json.loads(raw)
    except Exception:
        return None

    if str(payload.get("state") or "") != str(state or ""):
        return None

    return TokenResponse(
        access_token=str(payload.get("access_token") or ""),
        token_type=str(payload.get("token_type") or "bearer"),
        user_id=str(payload.get("user_id") or ""),
        email=_normalize_email(str(payload.get("email") or "")),
    )
