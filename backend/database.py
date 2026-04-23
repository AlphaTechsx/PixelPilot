"""
Database connections and dependencies.
Initializes MongoDB and Redis at application startup using FastAPI lifespan.
"""

import os
from contextlib import asynccontextmanager
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
import redis.asyncio as redis
from dotenv import load_dotenv

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")
REDIS_URI = os.getenv("REDIS_URI")

_mongo_client: AsyncIOMotorClient = None
_mongo_db: AsyncIOMotorDatabase = None
_redis_client: redis.Redis = None


def _require_env(name: str) -> str:
    value = str(os.getenv(name, "")).strip()
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def _validate_startup_configuration() -> tuple[str, str]:
    mongodb_uri = _require_env("MONGODB_URI")
    redis_uri = _require_env("REDIS_URI")

    import auth

    auth.ensure_auth_configuration()

    provider = str(os.getenv("PIXELPILOT_MODEL_PROVIDER") or os.getenv("AI_PROVIDER") or "gemini").strip().lower()
    required_key = {
        "gemini": "GEMINI_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "claude": "ANTHROPIC_API_KEY",
        "xai": "XAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "openai_compatible": "OPENAI_COMPATIBLE_API_KEY",
        "vercel_ai_gateway": "VERCEL_AI_GATEWAY_API_KEY",
    }.get(provider)
    if required_key and not str(os.getenv(required_key, "")).strip():
        raise RuntimeError(f"Missing required env var: {required_key}")

    return mongodb_uri, redis_uri
import asyncio

@asynccontextmanager
async def lifespan(app):
    """
    FastAPI lifespan context manager.
    Establishes database connections at startup and closes them at shutdown.
    """
    global _mongo_client, _mongo_db, _redis_client
    mongodb_uri, redis_uri = _validate_startup_configuration()

    print("Connecting to MongoDB...")
    _mongo_client = AsyncIOMotorClient(mongodb_uri, serverSelectionTimeoutMS=5000)
    _mongo_db = _mongo_client.pixelpilot

    print("Connecting to Redis...")
    _redis_client = redis.from_url(
        redis_uri,
        decode_responses=True,
        socket_timeout=5.0,
        socket_connect_timeout=5.0,
    )

    try:
        await asyncio.wait_for(_mongo_client.admin.command("ping"), timeout=5.0)
        print("MongoDB connected successfully!")
    except Exception as e:
        print(f"FAILED to connect to MongoDB: {e}")
        raise RuntimeError(f"Database connection timeout (MongoDB): {e}")

    import auth

    await auth.ensure_auth_indexes(_mongo_db)

    try:
        await asyncio.wait_for(_redis_client.ping(), timeout=5.0)
        print("Redis connected successfully!")
    except Exception as e:
        print(f"FAILED to connect to Redis: {e}")
        raise RuntimeError(f"Database connection timeout (Redis): {e}")

    yield

    print("Closing database connections...")
    if _mongo_client:
        _mongo_client.close()
    if _redis_client:
        await _redis_client.close()
    print("Database connections closed.")

async def get_db() -> AsyncIOMotorDatabase:
    """Dependency to get MongoDB database."""
    return _mongo_db


async def get_redis() -> redis.Redis:
    """Dependency to get Redis client."""
    return _redis_client
