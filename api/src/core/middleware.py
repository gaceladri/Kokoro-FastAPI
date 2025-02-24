"""Middleware for authentication and usage tracking."""

from typing import Optional, Dict, Tuple
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from loguru import logger
from datetime import datetime
import asyncio
from functools import lru_cache

from .config import settings
from ..services.usage_tracking.usage_service import UsageTrackingService


class UsageTrackingMiddleware(BaseHTTPMiddleware):
    """Middleware for tracking API usage."""

    def __init__(self, app):
        """Initialize middleware."""
        super().__init__(app)
        self._usage_service = None
        self._api_key_cache: Dict[str, Dict] = {}
        self._cache_lock = asyncio.Lock()
        self._cache_cleanup_task = None

    async def _get_usage_service(self) -> UsageTrackingService:
        """Get or create usage tracking service."""
        if self._usage_service is None:
            self._usage_service = await UsageTrackingService.create()
        return self._usage_service

    @lru_cache(maxsize=1024)
    def _is_api_route(self, path: str) -> bool:
        """Check if route requires API validation."""
        return path.startswith("/v1/") and path != "/v1/usage"

    async def _get_text_length(self, request: Request) -> Optional[int]:
        """Extract text length from request body for TTS endpoints."""
        if request.url.path == "/v1/audio/speech":
            try:
                body = await request.json()
                return len(body.get("input", ""))
            except:
                return None
        return None

    def get_client_ip(self, request: Request) -> str:
        """Get client IP address from request."""
        # Check for X-Forwarded-For header (useful if behind a proxy)
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # X-Forwarded-For can contain multiple IPs; take the first one (client IP)
            return forwarded_for.split(",")[0].strip()
        return request.client.host

    async def _validate_api_key(
        self, api_key: str
    ) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """Validate API key with caching."""
        api_key_prefix = api_key[:8]

        async with self._cache_lock:
            cached_info = self._api_key_cache.get(api_key_prefix)
            if cached_info:
                # Check if cache is still valid (5 minutes)
                if datetime.utcnow().timestamp() - cached_info["timestamp"] < 300:
                    return True, None, cached_info["info"]

        # Not in cache or expired, validate with service
        usage_service = await self._get_usage_service()
        is_valid, error_message, request_info = await usage_service.validate_request(
            api_key_prefix
        )

        if is_valid:
            # Cache successful validations
            async with self._cache_lock:
                self._api_key_cache[api_key_prefix] = {
                    "timestamp": datetime.utcnow().timestamp(),
                    "info": request_info,
                }

        return is_valid, error_message, request_info

    async def dispatch(self, request: Request, call_next):
        """Process the request and validate API key or handle demo request."""
        # Fast path for non-API routes using cached check
        if not self._is_api_route(request.url.path):
            return await call_next(request)

        try:
            # Get API key and text length concurrently
            api_key = request.headers.get("X-API-Key")
            text_length_task = asyncio.create_task(self._get_text_length(request))

            # Handle demo request for speech endpoint
            if request.url.path == "/v1/audio/speech" and not api_key:
                text_length = await text_length_task
                if text_length is None:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": "invalid_request",
                            "message": "Unable to determine text length",
                            "type": "validation_error",
                        },
                    )

                # Validate demo request
                usage_service = await self._get_usage_service()
                ip_address = self.get_client_ip(request)
                is_allowed, message = await usage_service.validate_demo_request(
                    ip_address, text_length
                )

                if not is_allowed:
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": "demo_limit_exceeded",
                            "message": message,
                            "type": "authorization_error",
                        },
                    )

                # Set demo state
                request.state.is_demo = True
                request.state.text_length = text_length
                request.state.ip_address = ip_address
            else:
                # Handle authenticated request
                if not api_key:
                    raise HTTPException(
                        status_code=401,
                        detail={
                            "error": "missing_api_key",
                            "message": "API key is required",
                            "type": "authentication_error",
                        },
                    )

                # Validate API key with caching
                is_valid, error_message, request_info = await self._validate_api_key(
                    api_key
                )
                text_length = await text_length_task

                if not is_valid:
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": "invalid_request",
                            "message": error_message,
                            "type": "authorization_error",
                        },
                    )

                # Set request state
                request.state.user = request_info["user"]
                request.state.subscription = request_info["subscription"]
                request.state.usage = request_info["usage"]
                request.state.text_length = text_length

            # Process request
            response = await call_next(request)

            # Asynchronously track successful requests
            if response.status_code == 200 and text_length is not None:
                usage_service = await self._get_usage_service()
                if getattr(request.state, "is_demo", False):
                    asyncio.create_task(
                        usage_service.track_demo_request(request.state.ip_address)
                    )
                else:
                    subscription = request_info["subscription"]
                    asyncio.create_task(
                        usage_service.track_request(
                            subscription_id=subscription["id"],
                            period_start=datetime.fromisoformat(
                                subscription["current_period_start"]
                            ),
                            period_end=datetime.fromisoformat(
                                subscription["current_period_end"]
                            ),
                            character_count=text_length,
                        )
                    )

            return response

        except HTTPException as e:
            raise e
        except Exception as e:
            logger.error(f"Error in usage tracking middleware: {e}")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "internal_error",
                    "message": "An internal error occurred",
                    "type": "server_error",
                },
            )
