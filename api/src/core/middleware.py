"""Middleware for authentication and usage tracking."""

import asyncio
from datetime import datetime
from functools import lru_cache
from typing import Dict, Optional, Tuple
import time

from fastapi import HTTPException, Request
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware

from ..services.usage_tracking.usage_service import (
    UsageTrackingService,
    parse_iso_datetime,
)
from .config import settings


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
        # Only validate text length for speech generation endpoints
        if request.url.path == "/v1/audio/speech":
            try:
                body = await request.json()
                return len(body.get("input", ""))
            except Exception as e:
                logger.warning(f"Error extracting text length: {e}")
                return None

        # Other endpoints either don't have text or handle validation themselves
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
                    # Ensure usage data has the proper fields
                    if "info" in cached_info and "usage" in cached_info["info"]:
                        usage = cached_info["info"]["usage"]
                        if (
                            usage
                            and "total_characters" in usage
                            and "characters_used" not in usage
                        ):
                            usage["characters_used"] = usage["total_characters"]
                        elif (
                            usage
                            and "characters_used" in usage
                            and "total_characters" not in usage
                        ):
                            usage["total_characters"] = usage["characters_used"]
                    return True, None, cached_info["info"]

        # Not in cache or expired, validate with service
        usage_service = await self._get_usage_service()
        is_valid, error_message, request_info = await usage_service.validate_request(
            api_key_prefix
        )

        if is_valid:
            # Ensure consistent field naming
            if request_info and "usage" in request_info:
                usage = request_info["usage"]
                if (
                    usage
                    and "characters_used" in usage
                    and "total_characters" not in usage
                ):
                    usage["total_characters"] = usage["characters_used"]

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

        # For time series tracking
        request_start_time = time.time()
        text_length = None
        
        try:
            # Get API key and text length concurrently
            api_key = request.headers.get("X-API-Key")
            user_id = request.headers.get(
                "X-User-ID"
            )  # Add user ID header for free tier
            text_length_task = asyncio.create_task(self._get_text_length(request))

            # Determine request type based on headers
            if api_key:
                # Paid tier with API key
                request_type = "paid"
            elif user_id:
                # Free tier with user ID
                request_type = "free"
            else:
                # Demo request (no auth)
                request_type = "demo"

            # Skip text length validation for endpoints that don't require it
            if request.url.path in ["/v1/audio/voices", "/v1/models"]:
                # These endpoints don't require text length validation
                logger.debug(f"Skipping text length validation for {request.url.path}")
                response = await call_next(request)
                return response

            # Get text length for all request types
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

            # Get usage service
            usage_service = await self._get_usage_service()

            # Handle request based on type
            if request_type == "demo":
                # Demo request (no auth)
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
                request.state.request_type = "demo"
                request.state.text_length = text_length
                request.state.ip_address = ip_address
            elif request_type == "free":
                # Free tier with user ID
                (
                    is_valid,
                    error_message,
                    context,
                ) = await usage_service.validate_free_tier_request(user_id, text_length)

                if not is_valid:
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": "free_tier_limit_exceeded",
                            "message": error_message,
                            "type": "authorization_error",
                        },
                    )

                # Set free tier state
                request.state.request_type = "free"
                request.state.user = {"id": user_id}
                request.state.text_length = text_length
                request.state.free_tier_context = context
                request.state.api_key_prefix = None
            else:
                # Paid tier with API key
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
                request.state.request_type = "paid"
                request.state.user = request_info["user"]
                request.state.subscription = request_info["subscription"]
                request.state.usage = request_info["usage"]
                request.state.text_length = text_length
                request.state.api_key_prefix = api_key[:8]

            # Process request
            response = await call_next(request)
            
            # Time-series tracking for all requests
            if settings.enable_usage_tracking:
                # Calculate processing time
                processing_time_ms = int((time.time() - request_start_time) * 1000)
                
                # Get text length for tracking if not already determined
                if text_length is None:
                    try:
                        text_length = getattr(request.state, "text_length", 0)
                    except:
                        # Default to 0 if not available
                        text_length = 0
                
                # Track the detailed event asynchronously
                usage_service = await self._get_usage_service()
                asyncio.create_task(
                    usage_service.track_usage_event(
                        request=request,
                        endpoint=request.url.path,
                        http_method=request.method,
                        status_code=response.status_code,
                        character_count=text_length or 0,  # Ensure we have a value
                        processing_time_ms=processing_time_ms
                    )
                )

            # Asynchronously track successful requests (for billing/quota)
            if response.status_code == 200 and text_length is not None:
                request_type = getattr(request.state, "request_type", None)

                if request_type == "demo":
                    # Track demo request
                    asyncio.create_task(
                        usage_service.track_demo_request(
                            request.state.ip_address, text_length
                        )
                    )
                elif request_type == "free":
                    # Track free tier request
                    context = request.state.free_tier_context
                    asyncio.create_task(
                        usage_service.track_free_tier_usage(
                            user_id=request.state.user["id"],
                            period_start=context["period_start"],
                            period_end=context["period_end"],
                            character_count=text_length,
                        )
                    )
                elif request_type == "paid":
                    # Track paid subscription request
                    subscription = request.state.subscription
                    asyncio.create_task(
                        usage_service.track_request(
                            subscription_id=subscription["id"],
                            period_start=parse_iso_datetime(
                                subscription["current_period_start"]
                            ),
                            period_end=parse_iso_datetime(
                                subscription["current_period_end"]
                            ),
                            character_count=text_length,
                        )
                    )

            return response

        except HTTPException as e:
            # For time series tracking of errors too
            if settings.enable_usage_tracking:
                try:
                    # Calculate processing time
                    processing_time_ms = int((time.time() - request_start_time) * 1000)
                    
                    # Get text length for tracking if not already determined
                    if text_length is None:
                        try:
                            text_length = getattr(request.state, "text_length", 0)
                        except:
                            # Default to 0 if not available
                            text_length = 0
                    
                    # Track the error event asynchronously
                    usage_service = await self._get_usage_service()
                    asyncio.create_task(
                        usage_service.track_usage_event(
                            request=request,
                            endpoint=request.url.path,
                            http_method=request.method,
                            status_code=e.status_code,
                            character_count=text_length or 0,  # Ensure we have a value
                            processing_time_ms=processing_time_ms
                        )
                    )
                except Exception as tracking_error:
                    # Don't let tracking errors affect the main error response
                    logger.error(f"Error tracking failed request: {tracking_error}")
            
            raise e
        except Exception as e:
            # Also track unexpected errors
            if settings.enable_usage_tracking:
                try:
                    # Calculate processing time
                    processing_time_ms = int((time.time() - request_start_time) * 1000)
                    
                    # Get text length for tracking if not already determined
                    if text_length is None:
                        try:
                            text_length = getattr(request.state, "text_length", 0)
                        except:
                            # Default to 0 if not available
                            text_length = 0
                    
                    # Track the error event asynchronously
                    usage_service = await self._get_usage_service()
                    asyncio.create_task(
                        usage_service.track_usage_event(
                            request=request,
                            endpoint=request.url.path,
                            http_method=request.method,
                            status_code=500,  # Internal server error
                            character_count=text_length or 0,  # Ensure we have a value
                            processing_time_ms=processing_time_ms
                        )
                    )
                except Exception as tracking_error:
                    # Don't let tracking errors affect the main error response
                    logger.error(f"Error tracking failed request: {tracking_error}")
            
            logger.error(f"Error in usage tracking middleware: {e}")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "internal_error",
                    "message": "An internal error occurred",
                    "type": "server_error",
                },
            )
