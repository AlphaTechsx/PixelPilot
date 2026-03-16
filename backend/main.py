from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
import redis.asyncio as redis
import json
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


async def _generate_with_rate_limit(
    request: GenerateRequest,
    user_id: str,
    redis_client: redis.Redis,
) -> Dict[str, Any]:
    if redis_client is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    allowed, current, limit = await rate_limiter.check_rate_limit(user_id, redis_client)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "message": f"Daily limit exceeded ({limit} requests). Resets at midnight UTC.",
                "limit": limit,
                "remaining": 0,
            },
        )

    logger.info(f"Generating content for user {user_id} with model: {request.model}")
    result = await service.generate_content(
        service.GenerationRequest(
            model=request.model,
            contents=request.contents,
            config=request.config,
        )
    )

    await rate_limiter.increment_usage(user_id, redis_client)
    remaining = await rate_limiter.get_remaining_requests(user_id, redis_client)

    if isinstance(result, dict):
        result["remaining_requests"] = remaining
        return result

    return {"text": str(result), "remaining_requests": remaining}


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
    try:
        return await _generate_with_rate_limit(request, user["user_id"], redis_client)
    except HTTPException as e:
        headers = None
        if e.status_code == 429 and isinstance(e.detail, dict):
            headers = {
                "X-RateLimit-Limit": str(e.detail.get("limit", 0)),
                "X-RateLimit-Remaining": str(e.detail.get("remaining", 0)),
            }
        raise HTTPException(status_code=e.status_code, detail=e.detail, headers=headers)
    except Exception as e:
        status_code = 500
        if hasattr(e, "code"):
            status_code = e.code
        elif hasattr(e, "status_code"):
            status_code = e.status_code

        logger.error(f"Generation error: {e}")
        raise HTTPException(status_code=status_code, detail=str(e))


@app.websocket("/ws/generate")
async def ws_generate(websocket: WebSocket):
    await websocket.accept()

    user = None
    redis_client = await get_redis()

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json(
                    {"type": "error", "code": 400, "detail": "Invalid JSON payload"}
                )
                continue

            msg_type = message.get("type")

            if msg_type == "auth":
                token = str(message.get("token") or "").strip()
                user = auth.verify_access_token(token)
                if not user:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "code": 401,
                            "detail": "Invalid or expired token",
                        }
                    )
                    await websocket.close(code=1008)
                    return
                await websocket.send_json(
                    {"type": "auth_ok", "user_id": user.get("user_id")}
                )
                continue

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if msg_type != "generate":
                await websocket.send_json(
                    {"type": "error", "code": 400, "detail": "Unknown message type"}
                )
                continue

            if not user:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": 401,
                        "detail": "Authenticate first with {'type':'auth','token':'...'}",
                    }
                )
                continue

            request_payload = message.get("request", {})
            try:
                generate_request = GenerateRequest.model_validate(request_payload)
            except Exception as e:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": 422,
                        "detail": f"Invalid generate request: {e}",
                    }
                )
                continue

            try:
                result = await _generate_with_rate_limit(
                    generate_request,
                    user["user_id"],
                    redis_client,
                )
                await websocket.send_json({"type": "generate_result", "data": result})
            except HTTPException as e:
                await websocket.send_json(
                    {"type": "error", "code": e.status_code, "detail": e.detail}
                )
            except Exception as e:
                logger.error(f"WebSocket generation error: {e}")
                await websocket.send_json(
                    {"type": "error", "code": 500, "detail": str(e)}
                )
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket connection error: {e}")


# ============ Health Check ============


@app.get("/health")
async def health_check():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
