"""
Redis-backed quota tracking for backend generation and live sessions.

Policy:
- 1000 successful generate requests per user per UTC day
- 60 successful generate requests per user per UTC minute
- Gemini Live usage is limited by total active session seconds per user per UTC day
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import redis.asyncio as redis

DAILY_LIMIT = 1000
MINUTE_LIMIT = 60
LIVE_MAX_CONCURRENT_SESSIONS = max(
    1, int(os.getenv("LIVE_MAX_CONCURRENT_SESSIONS", "2"))
)
LIVE_MAX_ACTIVE_SESSIONS_PER_USER = max(
    1, int(os.getenv("LIVE_MAX_ACTIVE_SESSIONS_PER_USER", "1"))
)
LIVE_SESSION_SECONDS_PER_DAY = max(
    60, int(os.getenv("LIVE_SESSION_SECONDS_PER_DAY", "600"))
)
LIVE_SESSION_LEASE_TTL_SECONDS = max(
    5, int(os.getenv("LIVE_SESSION_LEASE_TTL_SECONDS", "30"))
)
LIVE_SESSION_HEARTBEAT_SECONDS = max(
    1, int(os.getenv("LIVE_SESSION_HEARTBEAT_SECONDS", "10"))
)

WINDOW_DAY = "day"
WINDOW_MINUTE = "minute"
WINDOW_LIVE_CONCURRENT = "concurrent"

RESERVE_SCRIPT = """
local day_key = KEYS[1]
local minute_key = KEYS[2]

local day_limit = tonumber(ARGV[1])
local minute_limit = tonumber(ARGV[2])
local day_ttl = tonumber(ARGV[3])
local minute_ttl = tonumber(ARGV[4])

local day_count = tonumber(redis.call("GET", day_key) or "0")
local minute_count = tonumber(redis.call("GET", minute_key) or "0")

if day_count >= day_limit then
    return {0, "day", day_count, day_limit, math.max(day_limit - day_count, 0), day_ttl, day_count, minute_count}
end

if minute_count >= minute_limit then
    return {0, "minute", minute_count, minute_limit, math.max(minute_limit - minute_count, 0), minute_ttl, day_count, minute_count}
end

local new_day_count = redis.call("INCR", day_key)
redis.call("EXPIRE", day_key, day_ttl)

local new_minute_count = redis.call("INCR", minute_key)
redis.call("EXPIRE", minute_key, minute_ttl)

return {
    1,
    "",
    0,
    0,
    0,
    0,
    new_day_count,
    new_minute_count
}
"""

REFUND_SCRIPT = """
local day_key = KEYS[1]
local minute_key = KEYS[2]

local day_count = tonumber(redis.call("GET", day_key) or "0")
local minute_count = tonumber(redis.call("GET", minute_key) or "0")

if day_count > 0 then
    day_count = tonumber(redis.call("DECR", day_key))
    if day_count <= 0 then
        redis.call("DEL", day_key)
        day_count = 0
    end
end

if minute_count > 0 then
    minute_count = tonumber(redis.call("DECR", minute_key))
    if minute_count <= 0 then
        redis.call("DEL", minute_key)
        minute_count = 0
    end
end

return {day_count, minute_count}
"""

LIVE_RESERVE_SCRIPT = """
local usage_key = KEYS[1]
local global_lease_key = KEYS[2]
local user_lease_key = KEYS[3]

local global_pattern = ARGV[1]
local user_pattern = ARGV[2]
local time_budget = tonumber(ARGV[3])
local global_limit = tonumber(ARGV[4])
local user_limit = tonumber(ARGV[5])
local usage_ttl = tonumber(ARGV[6])
local lease_ttl = tonumber(ARGV[7])
local now_ts = tostring(ARGV[8])

local global_keys = redis.call("KEYS", global_pattern)
local user_keys = redis.call("KEYS", user_pattern)
local global_active = #global_keys
local user_active = #user_keys

if global_active >= global_limit then
    return {0, "concurrent", "live_global", global_active, global_limit, 0, lease_ttl, 0, 0}
end

if user_active >= user_limit then
    return {0, "concurrent", "live_user", user_active, user_limit, 0, lease_ttl, 0, 0}
end

local used_seconds = tonumber(redis.call("GET", usage_key) or "0")

if used_seconds >= time_budget then
    return {0, "day", "live_time", used_seconds, time_budget, math.max(time_budget - used_seconds, 0), usage_ttl, used_seconds, 0}
end

redis.call("SET", global_lease_key, now_ts, "EX", lease_ttl)
redis.call("SET", user_lease_key, now_ts, "EX", lease_ttl)

return {1, "", "live_time", used_seconds, time_budget, math.max(time_budget - used_seconds, 0), 0, used_seconds, 0}
"""

LIVE_REFUND_SCRIPT = """
local usage_key = KEYS[1]
local global_lease_key = KEYS[2]
local user_lease_key = KEYS[3]

redis.call("DEL", global_lease_key)
redis.call("DEL", user_lease_key)

local used_seconds = tonumber(redis.call("GET", usage_key) or "0")
return {used_seconds}
"""

LIVE_RELEASE_SCRIPT = """
local usage_key = KEYS[1]
local global_lease_key = KEYS[2]
local user_lease_key = KEYS[3]

local usage_ttl = tonumber(ARGV[1])
local now_ts = tonumber(ARGV[2])

local last_ts = tonumber(redis.call("GET", global_lease_key) or redis.call("GET", user_lease_key) or tostring(now_ts))
local delta = math.max(0, now_ts - last_ts)
local used_seconds = tonumber(redis.call("GET", usage_key) or "0")
if delta > 0 then
    used_seconds = tonumber(redis.call("INCRBY", usage_key, delta))
    redis.call("EXPIRE", usage_key, usage_ttl)
end

local removed = 0
removed = removed + redis.call("DEL", global_lease_key)
removed = removed + redis.call("DEL", user_lease_key)
return {removed, used_seconds}
"""

LIVE_REFRESH_SCRIPT = """
local usage_key = KEYS[1]
local global_lease_key = KEYS[2]
local user_lease_key = KEYS[3]
local lease_ttl = tonumber(ARGV[1])
local usage_ttl = tonumber(ARGV[2])
local now_ts = tonumber(ARGV[3])

local last_ts = tonumber(redis.call("GET", global_lease_key) or redis.call("GET", user_lease_key) or tostring(now_ts))
local delta = math.max(0, now_ts - last_ts)
local used_seconds = tonumber(redis.call("GET", usage_key) or "0")
if delta > 0 then
    used_seconds = tonumber(redis.call("INCRBY", usage_key, delta))
    redis.call("EXPIRE", usage_key, usage_ttl)
end

local refreshed = 0
if redis.call("EXISTS", global_lease_key) == 1 then
    redis.call("SET", global_lease_key, tostring(now_ts), "EX", lease_ttl)
    refreshed = refreshed + 1
end
if redis.call("EXISTS", user_lease_key) == 1 then
    redis.call("SET", user_lease_key, tostring(now_ts), "EX", lease_ttl)
    refreshed = refreshed + 1
end

return {refreshed, used_seconds}
"""


@dataclass(frozen=True)
class RateLimitReservation:
    allowed: bool
    scope: str
    window: Optional[str]
    current: int
    limit: int
    remaining: int
    retry_after_seconds: int
    daily_remaining: int
    minute_remaining: int
    day_key: str
    minute_key: str


@dataclass(frozen=True)
class LiveSessionReservation:
    allowed: bool
    window: Optional[str]
    scope: str
    current: int
    limit: int
    remaining: int
    retry_after_seconds: int
    daily_remaining: int
    minute_remaining: int
    session_id: str
    user_id: str
    usage_key: str
    global_lease_key: str
    user_lease_key: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _seconds_until_next_utc_minute(now: datetime) -> int:
    next_minute = (now.replace(second=0, microsecond=0) + timedelta(minutes=1))
    return max(1, int((next_minute - now).total_seconds()))


def _seconds_until_next_utc_midnight(now: datetime) -> int:
    next_midnight = (now + timedelta(days=1)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return max(1, int((next_midnight - now).total_seconds()))


def _get_rate_limit_keys(
    user_id: str,
    *,
    scope: str = "generate",
    now: Optional[datetime] = None,
) -> tuple[str, str]:
    current_time = now or _utc_now()
    scope_key = str(scope or "generate").strip().lower() or "generate"
    day_key = current_time.strftime(
        f"ratelimit:{scope_key}:{WINDOW_DAY}:{user_id}:%Y-%m-%d"
    )
    minute_key = current_time.strftime(
        f"ratelimit:{scope_key}:{WINDOW_MINUTE}:{user_id}:%Y-%m-%dT%H:%M"
    )
    return day_key, minute_key


def _get_live_rate_limit_keys(
    user_id: str,
    session_id: str,
    *,
    now: Optional[datetime] = None,
) -> tuple[str, str, str, str, str]:
    current_time = now or _utc_now()
    usage_key = current_time.strftime(
        f"ratelimit:live:usage:{WINDOW_DAY}:{user_id}:%Y-%m-%d"
    )
    global_lease_key = f"ratelimit:live:lease:global:{session_id}"
    user_lease_key = f"ratelimit:live:lease:user:{user_id}:{session_id}"
    global_pattern = "ratelimit:live:lease:global:*"
    user_pattern = f"ratelimit:live:lease:user:{user_id}:*"
    return (
        usage_key,
        global_lease_key,
        user_lease_key,
        global_pattern,
        user_pattern,
    )


def _to_int(value: object) -> int:
    return int(value or 0)


def _normalize_reservation(
    raw_result: list[object],
    *,
    scope: str,
    daily_limit: int,
    minute_limit: int,
    day_key: str,
    minute_key: str,
) -> RateLimitReservation:
    allowed = bool(_to_int(raw_result[0]))
    window = str(raw_result[1] or "") or None
    current = _to_int(raw_result[2])
    limit = _to_int(raw_result[3])
    remaining = _to_int(raw_result[4])
    retry_after_seconds = _to_int(raw_result[5])
    day_count = _to_int(raw_result[6])
    minute_count = _to_int(raw_result[7])

    daily_remaining = max(0, daily_limit - day_count)
    minute_remaining = max(0, minute_limit - minute_count)

    if allowed:
        current = 0
        limit = 0
        remaining = daily_remaining
        retry_after_seconds = 0
        window = None

    return RateLimitReservation(
        allowed=allowed,
        scope=str(scope or "generate"),
        window=window,
        current=current,
        limit=limit,
        remaining=remaining,
        retry_after_seconds=retry_after_seconds,
        daily_remaining=daily_remaining,
        minute_remaining=minute_remaining,
        day_key=day_key,
        minute_key=minute_key,
    )


def _normalize_live_reservation(
    raw_result: list[object],
    *,
    user_id: str,
    session_id: str,
    usage_key: str,
    global_lease_key: str,
    user_lease_key: str,
) -> LiveSessionReservation:
    allowed = bool(_to_int(raw_result[0]))
    window = str(raw_result[1] or "") or None
    scope = str(raw_result[2] or "live")
    current = _to_int(raw_result[3])
    limit = _to_int(raw_result[4])
    remaining = _to_int(raw_result[5])
    retry_after_seconds = _to_int(raw_result[6])
    used_seconds = _to_int(raw_result[7])

    daily_remaining = max(0, LIVE_SESSION_SECONDS_PER_DAY - used_seconds)
    minute_remaining = 0

    if allowed:
        current = used_seconds
        limit = LIVE_SESSION_SECONDS_PER_DAY
        remaining = daily_remaining
        retry_after_seconds = 0
        window = None
        scope = "live_time"

    return LiveSessionReservation(
        allowed=allowed,
        window=window,
        scope=scope,
        current=current,
        limit=limit,
        remaining=remaining,
        retry_after_seconds=retry_after_seconds,
        daily_remaining=daily_remaining,
        minute_remaining=minute_remaining,
        session_id=session_id,
        user_id=user_id,
        usage_key=usage_key,
        global_lease_key=global_lease_key,
        user_lease_key=user_lease_key,
    )


async def reserve_generate_request(
    user_id: str,
    redis_client: redis.Redis,
) -> RateLimitReservation:
    """
    Atomically reserve quota for a generate request.

    A successful reservation must be refunded if the downstream generation fails.
    """
    now = _utc_now()
    day_key, minute_key = _get_rate_limit_keys(user_id, scope="generate", now=now)
    day_ttl = _seconds_until_next_utc_midnight(now)
    minute_ttl = _seconds_until_next_utc_minute(now)

    raw_result = await redis_client.eval(
        RESERVE_SCRIPT,
        2,
        day_key,
        minute_key,
        DAILY_LIMIT,
        MINUTE_LIMIT,
        day_ttl,
        minute_ttl,
    )
    return _normalize_reservation(
        raw_result,
        scope="generate",
        daily_limit=DAILY_LIMIT,
        minute_limit=MINUTE_LIMIT,
        day_key=day_key,
        minute_key=minute_key,
    )


async def refund_generate_request(
    reservation: RateLimitReservation,
    redis_client: redis.Redis,
) -> RateLimitReservation:
    """
    Refund a previously reserved generate request after downstream failure.
    """
    raw_result = await redis_client.eval(
        REFUND_SCRIPT,
        2,
        reservation.day_key,
        reservation.minute_key,
    )
    day_count = _to_int(raw_result[0])
    minute_count = _to_int(raw_result[1])

    return RateLimitReservation(
        allowed=True,
        scope="generate",
        window=None,
        current=0,
        limit=0,
        remaining=max(0, DAILY_LIMIT - day_count),
        retry_after_seconds=0,
        daily_remaining=max(0, DAILY_LIMIT - day_count),
        minute_remaining=max(0, MINUTE_LIMIT - minute_count),
        day_key=reservation.day_key,
        minute_key=reservation.minute_key,
    )


async def reserve_live_session_start(
    user_id: str,
    session_id: str,
    redis_client: redis.Redis,
) -> LiveSessionReservation:
    now = _utc_now()
    (
        usage_key,
        global_lease_key,
        user_lease_key,
        global_pattern,
        user_pattern,
    ) = _get_live_rate_limit_keys(user_id, session_id, now=now)
    usage_ttl = _seconds_until_next_utc_midnight(now)
    now_ts = int(now.timestamp())

    raw_result = await redis_client.eval(
        LIVE_RESERVE_SCRIPT,
        3,
        usage_key,
        global_lease_key,
        user_lease_key,
        global_pattern,
        user_pattern,
        LIVE_SESSION_SECONDS_PER_DAY,
        LIVE_MAX_CONCURRENT_SESSIONS,
        LIVE_MAX_ACTIVE_SESSIONS_PER_USER,
        usage_ttl,
        LIVE_SESSION_LEASE_TTL_SECONDS,
        now_ts,
    )
    return _normalize_live_reservation(
        raw_result,
        user_id=user_id,
        session_id=session_id,
        usage_key=usage_key,
        global_lease_key=global_lease_key,
        user_lease_key=user_lease_key,
    )


async def refund_live_session_start(
    reservation: LiveSessionReservation,
    redis_client: redis.Redis,
) -> LiveSessionReservation:
    raw_result = await redis_client.eval(
        LIVE_REFUND_SCRIPT,
        3,
        reservation.usage_key,
        reservation.global_lease_key,
        reservation.user_lease_key,
    )
    used_seconds = _to_int(raw_result[0])
    return LiveSessionReservation(
        allowed=True,
        window=None,
        scope="live_time",
        current=0,
        limit=LIVE_SESSION_SECONDS_PER_DAY,
        remaining=max(0, LIVE_SESSION_SECONDS_PER_DAY - used_seconds),
        retry_after_seconds=0,
        daily_remaining=max(0, LIVE_SESSION_SECONDS_PER_DAY - used_seconds),
        minute_remaining=0,
        session_id=reservation.session_id,
        user_id=reservation.user_id,
        usage_key=reservation.usage_key,
        global_lease_key=reservation.global_lease_key,
        user_lease_key=reservation.user_lease_key,
    )


async def release_live_session(
    reservation: LiveSessionReservation,
    redis_client: redis.Redis,
) -> bool:
    now = _utc_now()
    raw_result = await redis_client.eval(
        LIVE_RELEASE_SCRIPT,
        3,
        reservation.usage_key,
        reservation.global_lease_key,
        reservation.user_lease_key,
        _seconds_until_next_utc_midnight(now),
        int(now.timestamp()),
    )
    return bool(_to_int(raw_result[0]))


async def refresh_live_session_lease(
    reservation: LiveSessionReservation,
    redis_client: redis.Redis,
) -> bool:
    now = _utc_now()
    raw_result = await redis_client.eval(
        LIVE_REFRESH_SCRIPT,
        3,
        reservation.usage_key,
        reservation.global_lease_key,
        reservation.user_lease_key,
        LIVE_SESSION_LEASE_TTL_SECONDS,
        _seconds_until_next_utc_midnight(now),
        int(now.timestamp()),
    )
    return _to_int(raw_result[0]) == 2


async def has_active_live_session(
    user_id: str,
    redis_client: redis.Redis,
) -> bool:
    user_pattern = f"ratelimit:live:lease:user:{user_id}:*"
    return bool(await redis_client.keys(user_pattern))
