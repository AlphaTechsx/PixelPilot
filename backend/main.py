from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
import redis.asyncio as redis
import uvicorn
import service
import logging
import auth
import rate_limiter
from database import lifespan, get_db, get_redis

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backend")

app = FastAPI(title="PixelPilot AI Backend", version="1.0.0", lifespan=lifespan)
security = HTTPBearer()


# Request/Response models
class GenerateRequest(BaseModel):
    model: str
    contents: List[Dict[str, Any]]
    config: Optional[Dict[str, Any]] = None


class GenerateResponse(BaseModel):
    text: str
    remaining_requests: Optional[int] = None


# Auth dependency
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """Validate JWT token and return user info."""
    token = credentials.credentials
    user = auth.verify_access_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user


# ============ Auth Endpoints ============


@app.post("/auth/register", response_model=auth.TokenResponse)
async def register(
    request: auth.RegisterRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Register a new user."""
    try:
        user = await auth.register_user(request.email, request.password, db)
        token = auth.create_access_token(user["user_id"], user["email"])
        return auth.TokenResponse(
            access_token=token,
            user_id=user["user_id"],
            email=user["email"],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Registration error: {e}")
        raise HTTPException(status_code=500, detail="Registration failed")


@app.post("/auth/login", response_model=auth.TokenResponse)
async def login(
    request: auth.LoginRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Login and get access token."""
    user = await auth.authenticate_user(request.email, request.password, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = auth.create_access_token(user["user_id"], user["email"])
    return auth.TokenResponse(
        access_token=token,
        user_id=user["user_id"],
        email=user["email"],
    )


@app.get("/auth/me", response_model=auth.UserInfo)
async def get_me(user: dict = Depends(get_current_user)):
    """Get current user info."""
    return auth.UserInfo(user_id=user["user_id"], email=user["email"])


# ============ Generation Endpoint (Protected) ============


@app.post("/v1/generate")
async def generate(
    request: GenerateRequest,
    user: dict = Depends(get_current_user),
    redis_client: redis.Redis = Depends(get_redis),
):
    """Generate content using Gemini API. Requires authentication."""
    user_id = user["user_id"]

    # Check rate limit
    allowed, current, limit = await rate_limiter.check_rate_limit(user_id, redis_client)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Daily limit exceeded ({limit} requests). Resets at midnight UTC.",
            headers={"X-RateLimit-Limit": str(limit), "X-RateLimit-Remaining": "0"},
        )

    try:
        logger.info(
            f"Generating content for user {user_id} with model: {request.model}"
        )
        result = await service.generate_content(
            service.GenerationRequest(
                model=request.model, contents=request.contents, config=request.config
            )
        )

        # Increment usage after successful call
        await rate_limiter.increment_usage(user_id, redis_client)
        remaining = await rate_limiter.get_remaining_requests(user_id, redis_client)

        # Add remaining requests to response
        if isinstance(result, dict):
            result["remaining_requests"] = remaining

        return result
    except Exception as e:
        status_code = 500
        if hasattr(e, "code"):
            status_code = e.code
        elif hasattr(e, "status_code"):
            status_code = e.status_code

        logger.error(f"Generation error: {e}")
        raise HTTPException(status_code=status_code, detail=str(e))


# ============ Health Check ============


@app.get("/health")
async def health_check():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
