"""
FastAPI OpenAI Compatible API
"""

import sys
from contextlib import asynccontextmanager

import torch
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
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
                "format": "<fg #2E8B57>{time:hh:mm:ss A}</fg #2E8B57> | "
                "{level: <8} | "
                "<fg #4169E1>{module}:{line}</fg #4169E1> | "
                "{message}",
                "colorize": True,
                "level": "DEBUG",
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
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


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
                is_valid, _, request_info = await usage_service._validate_api_key(api_key)
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
            
            # Get user statistics
            user_id = user_info["id"]
            stats = await usage_service.get_user_statistics(user_id)
            
            # Ensure consistent field naming (characters_used renamed to total_characters in response)
            if stats and "characters_used" in stats and "total_characters" not in stats:
                stats["total_characters"] = stats["characters_used"]
            
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
            
            # Get user statistics
            stats = await usage_service.get_user_statistics(user_id)
            
            # Ensure consistent field naming
            if stats and "characters_used" in stats and "total_characters" not in stats:
                stats["total_characters"] = stats["characters_used"]
                
        else:
            # Demo statistics (IP-based)
            client_ip = getattr(request.state, "ip_address", None)
            if not client_ip:
                client_ip = request.client.host
            
            stats = await usage_service.get_demo_usage_stats(client_ip)
        
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
