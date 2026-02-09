"""
Database connections and dependencies.
Initializes MongoDB and Redis at application startup using FastAPI lifespan.
"""

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
import redis.asyncio as redis
from dotenv import load_dotenv

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")
REDIS_URI = os.getenv("REDIS_URI", "redis://localhost:6379")


# Global connection instances (set during lifespan)
_mongo_client: AsyncIOMotorClient = None
_mongo_db: AsyncIOMotorDatabase = None
_redis_client: redis.Redis = None


@asynccontextmanager
async def lifespan(app):
    """
    FastAPI lifespan context manager.
    Establishes database connections at startup and closes them at shutdown.
    """
    global _mongo_client, _mongo_db, _redis_client

    # Startup
    print("Connecting to MongoDB...")
    _mongo_client = AsyncIOMotorClient(MONGODB_URI)
    _mongo_db = _mongo_client.pixelpilot

    print("Connecting to Redis...")
    _redis_client = redis.from_url(REDIS_URI, decode_responses=True)

    # Verify connections
    try:
        await _mongo_client.admin.command("ping")
        print("MongoDB connected successfully!")
    except Exception as e:
        print(f"MongoDB connection failed: {e}")

    try:
        await _redis_client.ping()
        print("Redis connected successfully!")
    except Exception as e:
        print(f"Redis connection failed: {e}")

    yield  # App runs here

    # Shutdown
    print("Closing database connections...")
    if _mongo_client:
        _mongo_client.close()
    if _redis_client:
        await _redis_client.close()
    print("Database connections closed.")


# FastAPI Dependencies


async def get_db() -> AsyncIOMotorDatabase:
    """Dependency to get MongoDB database."""
    return _mongo_db


async def get_redis() -> redis.Redis:
    """Dependency to get Redis client."""
    return _redis_client
