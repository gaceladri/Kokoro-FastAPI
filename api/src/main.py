"""
FastAPI OpenAI Compatible API
"""

import sys
from contextlib import asynccontextmanager
from datetime import datetime

import torch
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from .core.config import settings
from .core.middleware import UsageTrackingMiddleware
from .routers.debug import router as debug_router
from .routers.development import router as dev_router
from .routers.openai_compatible import router as openai_router
from .routers.web_player import router as web_router
from .services.usage_tracking.usage_service import UsageTrackingService


def setup_logger():
    """Configure loguru logger with custom formatting"""
    config = {
        "handlers": [
            {
                "sink": sys.stdout,
                "format": "<green>{time:hh:mm:ss A}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
                "level": "DEBUG" if settings.debug else "INFO",
            },
        ],
    }
    logger.remove()
    logger.configure(**config)
    logger.level("ERROR", color="<red>")


# Configure logger
setup_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for model initialization"""
    from .inference.model_manager import get_manager
    from .inference.voice_manager import get_manager as get_voice_manager
    from .services.temp_manager import cleanup_temp_files
    from .services.usage_tracking.usage_service import UsageTrackingService

    # Clean old temp files on startup
    await cleanup_temp_files()

    logger.info("Loading TTS model and voice packs...")

    try:
        # Initialize managers
        model_manager = await get_manager()
        voice_manager = await get_voice_manager()

        # Initialize model with warmup and get status
        device, model, voicepack_count = await model_manager.initialize_with_warmup(
            voice_manager
        )

        # Initialize usage tracking if enabled
        if settings.enable_usage_tracking:
            await UsageTrackingService.create()
            logger.info("Usage tracking service initialized")

    except Exception as e:
        logger.error(f"Failed to initialize model: {e}")
        raise

    boundary = "░" * 2 * 12
    startup_msg = f"""

{boundary}

    ╔═╗┌─┐┌─┐┌┬┐
    ╠╣ ├─┤└─┐ │ 
    ╚  ┴ ┴└─┘ ┴
    ╦╔═┌─┐┬┌─┌─┐
    ╠╩╗│ │├┴┐│ │
    ╩ ╩└─┘┴ ┴└─┘

{boundary}
                """
    startup_msg += f"\nModel warmed up on {device}: {model}"
    startup_msg += f"CUDA: {torch.cuda.is_available()}"
    startup_msg += f"\n{voicepack_count} voice packs loaded"

    # Add web player info if enabled
    if settings.enable_web_player:
        startup_msg += (
            f"\n\nBeta Web Player: http://{settings.host}:{settings.port}/web/"
        )
        startup_msg += f"\nor http://localhost:{settings.port}/web/"
    else:
        startup_msg += "\n\nWeb Player: disabled"

    startup_msg += f"\n{boundary}\n"
    logger.info(startup_msg)

    yield


# Initialize FastAPI app
app = FastAPI(
    title=settings.api_title,
    description=settings.api_description,
    version=settings.api_version,
    lifespan=lifespan,
    openapi_url="/openapi.json",  # Explicitly enable OpenAPI schema
)

# Add CORS middleware if enabled
if settings.cors_enabled:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Add usage tracking middleware if enabled
if settings.enable_usage_tracking:
    app.add_middleware(UsageTrackingMiddleware)

# Include routers
app.include_router(openai_router, prefix="/v1")
app.include_router(dev_router)  # Development endpoints
app.include_router(debug_router)  # Debug endpoints
if settings.enable_web_player:
    app.include_router(web_router, prefix="/web")  # Web player static files


# Health check endpoint
@app.get("/health", tags=["System"])
async def health_check(request: Request):
    """System health check endpoint."""
    # Create basic response
    health_response = {
        "status": "ok",
        "services": {
            "api": "ok",
        },
        "usage_tracking": None,
    }

    # Check usage tracking health if enabled
    if settings.enable_usage_tracking:
        try:
            # Get the middleware instance from the app middleware
            for middleware in request.app.user_middleware:
                if (
                    isinstance(middleware.cls, type)
                    and middleware.cls.__name__ == "UsageTrackingMiddleware"
                ):
                    # Get the instance
                    for md in request.app.middleware_stack.middlewares:
                        if isinstance(md, UsageTrackingMiddleware):
                            # Get tracking health status
                            tracking_health = await md.get_tracking_health()
                            health_response["usage_tracking"] = tracking_health

                            # Update overall status if tracking is unhealthy
                            if tracking_health and not tracking_health.get(
                                "is_healthy", True
                            ):
                                health_response["status"] = "degraded"
                                health_response["services"]["usage_tracking"] = (
                                    "degraded"
                                )
                            else:
                                health_response["services"]["usage_tracking"] = "ok"
                            break
                    break
        except Exception as e:
            logger.error(f"Error checking tracking health: {e}")
            health_response["status"] = "degraded"
            health_response["services"]["usage_tracking"] = "error"
            health_response["usage_tracking"] = {"error": str(e)}

    # Add additional service checks as needed
    # For example, check database connection directly

    # If overall status is not ok, return appropriate status code
    response_status = status.HTTP_200_OK
    if health_response["status"] != "ok":
        response_status = status.HTTP_503_SERVICE_UNAVAILABLE

    return health_response


@app.get("/v1/test")
async def test_endpoint():
    """Test endpoint to verify routing"""
    return {"status": "ok"}


# Usage statistics endpoint
@app.get("/v1/usage")
async def get_usage_stats(
    request: Request,
    usage_service: UsageTrackingService = Depends(UsageTrackingService.create),
):
    """Get usage statistics for the current user."""
    if not settings.enable_usage_tracking:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "usage_tracking_disabled",
                "message": "Usage tracking is not enabled",
                "type": "invalid_request_error",
            },
        )

    try:
        # Check if we're in emergency mode (set in middleware)
        is_emergency_mode = getattr(request.state, "is_emergency_mode", False)

        # If emergency mode not already set, try to detect circuit breaker
        if not is_emergency_mode:
            try:
                # Check circuit breaker status
                health_status = await usage_service.check_health()
                if not health_status.get("healthy", False):
                    is_emergency_mode = True
                    logger.warning(
                        "Circuit breaker detected as open in usage endpoint. Enabling emergency mode."
                    )
            except Exception as e:
                # Failed to check health, assume emergency mode
                logger.error(
                    f"Failed to check health, assuming circuit breaker is open: {e}"
                )
                is_emergency_mode = True

        # If in emergency mode, return basic stats without hitting the database
        if is_emergency_mode:
            logger.warning(
                "Returning emergency usage statistics due to database unavailability"
            )
            api_key = request.headers.get("X-API-Key", "")
            api_key_prefix = api_key[:8] if api_key else ""
            user_id = (
                getattr(request.state, "user", {}).get("id")
                or request.headers.get("X-User-ID")
                or f"emergency_{api_key_prefix}"
            )

            return {
                "status": "degraded",
                "message": "Database temporarily unavailable. Limited statistics provided.",
                "emergency_mode": True,
                "user_id": user_id,
                "period_start": datetime.now().isoformat(),
                "period_end": datetime.now().isoformat(),
                "total_characters": 0,
                "characters_used": 0,
                "limit": 1000000,
                "remaining": 1000000,
                "service_status": "Database connection error - circuit breaker open",
            }

        # Get request type from request state (set by middleware)
        request_type = getattr(request.state, "request_type", None)

        # If request type is not set, determine it based on headers
        if not request_type:
            api_key = request.headers.get("X-API-Key")
            user_id = request.headers.get("X-User-ID")

            if api_key:
                request_type = "paid"
            elif user_id:
                request_type = "free"
            else:
                request_type = "demo"

        # Get usage statistics based on request type
        if request_type == "paid":
            # Paid tier statistics
            user_info = getattr(request.state, "user", None)

            try:
                if not user_info:
                    # Try to get user info from API key
                    api_key = request.headers.get("X-API-Key", "")
                    api_key_prefix = api_key[:8] if api_key else None

                    if not api_key_prefix:
                        raise HTTPException(
                            status_code=401,
                            detail={
                                "error": "missing_api_key",
                                "message": "API key is required",
                                "type": "authentication_error",
                            },
                        )

                    # Validate API key to get user info
                    is_valid, _, request_info = await usage_service._validate_api_key(
                        api_key, skip_limit_check=True
                    )
                    if not is_valid or not request_info:
                        raise HTTPException(
                            status_code=403,
                            detail={
                                "error": "invalid_api_key",
                                "message": "Invalid API key",
                                "type": "authentication_error",
                            },
                        )

                    user_info = request_info["user"]

                # Get user statistics with enhanced metrics
                user_id = user_info["id"]
                stats = await usage_service.get_user_statistics(user_id)
            except Exception as e:
                # Check if this is a database/circuit breaker error
                error_str = str(e).lower()
                if (
                    "circuit breaker" in error_str
                    or "database" in error_str
                    or "connection" in error_str
                ):
                    logger.warning(
                        f"Database unavailable during usage statistics retrieval: {e}"
                    )
                    # Return emergency fallback statistics
                    stats = {
                        "status": "degraded",
                        "message": "Database temporarily unavailable. Limited statistics provided.",
                        "emergency_mode": True,
                        "period_start": datetime.now().isoformat(),
                        "period_end": datetime.now().isoformat(),
                        "total_characters": 0,
                        "characters_used": 0,
                        "limit": 1000000,
                        "remaining": 1000000,
                        "service_status": "Database connection error - circuit breaker open",
                    }
                else:
                    # For other errors, re-raise
                    raise

            # Add audio metrics if available
            if stats:
                # Compute average audio length per character
                if (
                    stats.get("total_characters", 0) > 0
                    and stats.get("total_audio_ms", 0) > 0
                ):
                    stats["avg_ms_per_character"] = (
                        stats["total_audio_ms"] / stats["total_characters"]
                    )

                # Format audio durations
                if "total_audio_ms" in stats:
                    stats["total_audio_seconds"] = round(
                        stats["total_audio_ms"] / 1000, 2
                    )

                # Add voice usage breakdown if available
                voice_stats = await usage_service.get_voice_usage_statistics(user_id)
                if voice_stats:
                    stats["voice_usage"] = voice_stats

                # Add API performance metrics if available
                perf_stats = await usage_service.get_performance_statistics(user_id)
                if perf_stats:
                    stats["performance"] = perf_stats

        elif request_type == "free":
            # Free tier statistics
            user_id = getattr(request.state, "user", {}).get("id")
            if not user_id:
                user_id = request.headers.get("X-User-ID")

            if not user_id:
                raise HTTPException(
                    status_code=401,
                    detail={
                        "error": "missing_user_id",
                        "message": "User ID is required for free tier",
                        "type": "authentication_error",
                    },
                )

            # Get user statistics with enhanced metrics
            stats = await usage_service.get_user_statistics(user_id)

            # Add audio metrics if available
            if stats:
                # Compute average audio length per character
                if (
                    stats.get("total_characters", 0) > 0
                    and stats.get("total_audio_ms", 0) > 0
                ):
                    stats["avg_ms_per_character"] = (
                        stats["total_audio_ms"] / stats["total_characters"]
                    )

                # Format audio durations
                if "total_audio_ms" in stats:
                    stats["total_audio_seconds"] = round(
                        stats["total_audio_ms"] / 1000, 2
                    )

                # Add voice usage breakdown if available
                voice_stats = await usage_service.get_voice_usage_statistics(user_id)
                if voice_stats:
                    stats["voice_usage"] = voice_stats
        else:
            # Demo statistics (IP-based)
            client_ip = getattr(request.state, "ip_address", None)
            if not client_ip:
                client_ip = request.client.host

            stats = await usage_service.get_demo_usage_stats(client_ip)

            # Add enhanced metrics for demo tier too
            if stats:
                # Add audio metrics if available
                if (
                    stats.get("total_characters", 0) > 0
                    and stats.get("total_audio_ms", 0) > 0
                ):
                    stats["avg_ms_per_character"] = (
                        stats["total_audio_ms"] / stats["total_characters"]
                    )

                # Format audio durations
                if "total_audio_ms" in stats:
                    stats["total_audio_seconds"] = round(
                        stats["total_audio_ms"] / 1000, 2
                    )

        if stats is None:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "usage_stats_error",
                    "message": "Failed to retrieve usage statistics",
                    "type": "server_error",
                },
            )

        return stats
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting usage stats: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "server_error",
                "message": "Failed to retrieve usage statistics",
                "type": "server_error",
            },
        )


if __name__ == "__main__":
    uvicorn.run("api.src.main:app", host=settings.host, port=settings.port, reload=True)
