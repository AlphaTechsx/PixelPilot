"""
Rate limiter module using Redis.
Limits each user to 200 Gemini API requests per day.
"""

from datetime import datetime, timezone
from typing import Tuple
import redis.asyncio as redis

DAILY_LIMIT = 5


def _get_rate_limit_key(user_id: str) -> str:
    """Generate Redis key for user's daily rate limit."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"ratelimit:{user_id}:{today}"


async def check_rate_limit(
    user_id: str, redis_client: redis.Redis
) -> Tuple[bool, int, int]:
    """
    Check if user is within rate limit.

    Returns:
        Tuple of (allowed: bool, current_count: int, limit: int)
    """
    key = _get_rate_limit_key(user_id)

    current = await redis_client.get(key)
    current_count = int(current) if current else 0

    allowed = current_count < DAILY_LIMIT
    return allowed, current_count, DAILY_LIMIT


async def increment_usage(user_id: str, redis_client: redis.Redis) -> int:
    """
    Increment user's request count for today.
    Returns the new count.
    """
    key = _get_rate_limit_key(user_id)

    # Increment and set TTL to 24 hours if new key
    pipe = redis_client.pipeline()
    pipe.incr(key)
    pipe.expire(key, 60 * 60 * 24)  # 24 hours TTL
    results = await pipe.execute()

    new_count = results[0]
    return new_count


async def get_remaining_requests(user_id: str, redis_client: redis.Redis) -> int:
    """Get the number of remaining requests for today."""
    allowed, current, limit = await check_rate_limit(user_id, redis_client)
    return max(0, limit - current)
