"""
Authentication module for user management with JWT tokens.
Uses MongoDB for user storage and bcrypt for password hashing.
"""

import os
import bcrypt
import jwt
from datetime import datetime, timedelta
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, EmailStr
from dotenv import load_dotenv

load_dotenv()

JWT_SECRET = os.getenv("JWT_SECRET", "default_secret_change_me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24 * 7  # 1 week


# Request/Response models
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


# Auth functions that accept db as dependency
async def register_user(email: str, password: str, db: AsyncIOMotorDatabase) -> dict:
    """Register a new user. Returns user info or raises exception."""
    users = db.users

    # Check if user exists
    existing = await users.find_one({"email": email.lower()})
    if existing:
        raise ValueError("User with this email already exists")

    # Hash password
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)

    # Create user
    user_doc = {
        "email": email.lower(),
        "password_hash": hashed.decode("utf-8"),
        "created_at": datetime.utcnow(),
        "is_active": True,
    }
    result = await users.insert_one(user_doc)

    return {
        "user_id": str(result.inserted_id),
        "email": email.lower(),
    }


async def authenticate_user(
    email: str, password: str, db: AsyncIOMotorDatabase
) -> Optional[dict]:
    """Authenticate user and return user info if valid."""
    users = db.users

    user = await users.find_one({"email": email.lower()})
    if not user:
        return None

    # Verify password
    if not bcrypt.checkpw(
        password.encode("utf-8"), user["password_hash"].encode("utf-8")
    ):
        return None

    return {
        "user_id": str(user["_id"]),
        "email": user["email"],
    }


def create_access_token(user_id: str, email: str) -> str:
    """Create a JWT access token."""
    expires = datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS)
    payload = {
        "sub": user_id,
        "email": email,
        "exp": expires,
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_access_token(token: str) -> Optional[dict]:
    """Verify JWT token and return payload if valid."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return {
            "user_id": payload["sub"],
            "email": payload["email"],
        }
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


async def get_user_by_id(user_id: str, db: AsyncIOMotorDatabase) -> Optional[dict]:
    """Get user info by ID."""
    from bson import ObjectId

    users = db.users

    try:
        user = await users.find_one({"_id": ObjectId(user_id)})
        if user:
            return {
                "user_id": str(user["_id"]),
                "email": user["email"],
            }
    except Exception:
        pass
    return None
