"""Middleware for authentication and usage tracking."""

from typing import Optional
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from loguru import logger

from .config import settings
from ..services.usage_tracking.usage_service import UsageTrackingService


class UsageTrackingMiddleware(BaseHTTPMiddleware):
    """Middleware for tracking API usage."""

    def __init__(self, app):
        """Initialize middleware."""
        super().__init__(app)
        self._usage_service = None

    async def _get_usage_service(self) -> UsageTrackingService:
        """Get or create usage tracking service."""
        if self._usage_service is None:
            self._usage_service = await UsageTrackingService.create()
        return self._usage_service

    async def _get_text_length(self, request: Request) -> Optional[int]:
        """Extract text length from request body for TTS endpoints."""
        if request.url.path == "/v1/audio/speech":
            try:
                body = await request.json()
                return len(body.get("input", ""))
            except:
                return None
        return None

    async def dispatch(self, request: Request, call_next):
        """Process the request and validate API key."""
        # Skip validation for non-API routes
        if not request.url.path.startswith("/v1/"):
            return await call_next(request)

        # Skip validation for usage stats endpoint
        if request.url.path == "/v1/usage":
            return await call_next(request)

        try:
            # Get API key from header
            api_key = request.headers.get("X-API-Key")
            if not api_key:
                raise HTTPException(
                    status_code=401,
                    detail={
                        "error": "missing_api_key",
                        "message": "API key is required",
                        "type": "authentication_error",
                    }
                )

            # Get API key prefix (first 8 characters)
            api_key_prefix = api_key[:8]

            # Get text length for character limit validation
            text_length = await self._get_text_length(request)

            # Validate request
            usage_service = await self._get_usage_service()
            is_valid, error_message, request_info = await usage_service.validate_request(
                api_key_prefix,
                text_length=text_length
            )

            if not is_valid:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "invalid_request",
                        "message": error_message,
                        "type": "authorization_error",
                    }
                )

            # Store request info in state for handlers
            request.state.user = request_info["user"]
            request.state.subscription = request_info["subscription"]
            request.state.usage = request_info["usage"]
            request.state.text_length = text_length

            # Process the request
            response = await call_next(request)

            # Track usage after successful request
            if response.status_code == 200 and text_length is not None:
                subscription = request_info["subscription"]
                await usage_service.track_request(
                    subscription_id=subscription["id"],
                    period_start=subscription["current_period_start"],
                    period_end=subscription["current_period_end"],
                    character_count=text_length
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
                }
            ) 