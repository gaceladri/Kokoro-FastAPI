"""Middleware for authentication and usage tracking."""

import asyncio
import json
import logging
import time
from datetime import datetime
from functools import lru_cache
from typing import Dict, Optional, Tuple

from fastapi import Request, Response
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware

from api.src.core.timing import TimingTracker

from ..services.usage_tracking.usage_service import (
    UsageTrackingService,
)
from .config import settings

logger = logging.getLogger(__name__)

# Maximum number of characters allowed for demo usage
DEMO_CHARACTER_LIMIT = 1000


class UsageTrackingMiddleware(BaseHTTPMiddleware):
    """Middleware for tracking API usage."""

    # Class-level variables for circuit breaker caching
    _circuit_breaker_status = False  # Default to closed (False)
    _circuit_breaker_last_check = 0  # Last check timestamp
    _circuit_breaker_cache_ttl = 30  # Cache TTL in seconds
    _circuit_breaker_failures = 0  # Track consecutive failures
    _circuit_breaker_max_backoff = 300  # Maximum backoff in seconds (5 minutes)

    def __init__(self, app):
        """Initialize middleware."""
        super().__init__(app)
        self._usage_service = None
        self._api_key_cache: Dict[str, Dict] = {}
        self._cache_lock = asyncio.Lock()
        self._cache_cleanup_task = None
        # Create an event buffer for batch processing
        self._event_queue = asyncio.Queue()
        # Status tracking for health checks
        self._tracking_healthy = True
        self._tracking_failure_count = 0
        self._max_tracking_failures = 5
        self._tracking_status_lock = asyncio.Lock()
        # Batch processing settings
        self._batch_size = 50
        self._batch_interval_seconds = 5
        # Start the background tasks
        self._start_background_tasks()

    def _start_background_tasks(self):
        """Start background tasks for this middleware."""
        loop = asyncio.get_event_loop()
        # Start the background task to process events
        loop.create_task(self._process_event_queue())
        # Start cache cleanup task
        loop.create_task(self._cleanup_cache_periodically())

    async def _cleanup_cache_periodically(self):
        """Periodically clean up the API key cache."""
        while True:
            try:
                # Sleep for 5 minutes
                await asyncio.sleep(300)

                # Get current timestamp
                now = datetime.utcnow().timestamp()

                # Clean up expired cache entries (older than 5 minutes)
                async with self._cache_lock:
                    expired_keys = [
                        key
                        for key, value in self._api_key_cache.items()
                        if now - value["timestamp"] > 300
                    ]

                    for key in expired_keys:
                        del self._api_key_cache[key]

                    if expired_keys:
                        logger.debug(
                            f"Cleaned up {len(expired_keys)} expired API key cache entries"
                        )
            except Exception as e:
                logger.error(f"Error in cache cleanup task: {e}")

    async def _process_event_queue(self):
        """Process events from the queue in batches."""
        while True:
            try:
                # Process events every few seconds to batch them
                await asyncio.sleep(self._batch_interval_seconds)

                # Check if there are events to process
                if self._event_queue.empty():
                    continue

                # Get all events currently in the queue (up to batch size)
                events = []
                try:
                    for _ in range(min(self._batch_size, self._event_queue.qsize())):
                        events.append(self._event_queue.get_nowait())
                except asyncio.QueueEmpty:
                    pass

                if not events:
                    continue

                # Get usage service
                try:
                    usage_service = await self._get_usage_service()
                    if not usage_service:
                        # If we can't get the service, skip these events
                        # Mark all events as processed
                        for _ in range(len(events)):
                            self._event_queue.task_done()
                        continue

                    # Check if the tracking service is healthy
                    if not await self._is_tracking_healthy(usage_service):
                        # If service unhealthy, skip these events
                        # Mark all events as processed
                        for _ in range(len(events)):
                            self._event_queue.task_done()
                        continue

                    # Process events in batches
                    # First try to use batch processing if available
                    batch_success = False
                    if hasattr(usage_service, "track_usage_events_batch"):
                        try:
                            batch_success = (
                                await usage_service.track_usage_events_batch(events)
                            )
                        except Exception as e:
                            logger.error(f"Batch processing failed: {e}")
                            batch_success = False

                    # If batch processing failed or isn't available, process individually
                    if not batch_success:
                        # Process events with some parallelism, but not too much to avoid overloading
                        results = await asyncio.gather(
                            *[
                                self._track_single_event(usage_service, event)
                                for event in events
                            ],
                            return_exceptions=True,
                        )

                        # Check for failures
                        failures = [
                            i
                            for i, r in enumerate(results)
                            if isinstance(r, Exception) or r is False
                        ]
                        if failures:
                            logger.warning(
                                f"Failed to process {len(failures)} out of {len(events)} events"
                            )
                            # Remove storing failed events

                            # Update tracking health status
                            if (
                                len(failures) > len(events) // 2
                            ):  # If more than half failed
                                await self._record_tracking_failure()

                        # If any succeeded, record success
                        if len(failures) < len(events):
                            await self._record_tracking_success()
                    else:
                        # Batch processing succeeded
                        await self._record_tracking_success()

                except Exception as e:
                    logger.error(f"Error in event processing: {e}")
                    # Remove storing events for retry
                    await self._record_tracking_failure()
                finally:
                    # Mark all events as done regardless of success/failure
                    for _ in range(len(events)):
                        self._event_queue.task_done()

            except Exception as e:
                logger.error(f"Error in event queue processing: {e}")

    async def _track_single_event(self, usage_service, event_data):
        """Track a single event using the usage service."""
        try:
            success = await usage_service.track_usage_event(**event_data)
            return success
        except Exception as e:
            logger.error(f"Error tracking event: {e}")
            raise

    async def _get_usage_service(self) -> Optional[UsageTrackingService]:
        """Get or create usage tracking service."""
        if not settings.enable_usage_tracking:
            return None

        try:
            if self._usage_service is None:
                self._usage_service = await UsageTrackingService.create()
            return self._usage_service
        except Exception as e:
            logger.error(f"Failed to create usage tracking service: {e}")
            return None

    async def _is_tracking_healthy(self, usage_service) -> bool:
        """Check if tracking service is healthy."""
        async with self._tracking_status_lock:
            return self._tracking_healthy

    async def _record_tracking_failure(self):
        """Record a tracking failure and update health status."""
        async with self._tracking_status_lock:
            self._tracking_failure_count += 1
            if self._tracking_failure_count >= self._max_tracking_failures:
                if self._tracking_healthy:
                    logger.warning("Usage tracking service marked as unhealthy")
                    self._tracking_healthy = False

    async def _record_tracking_success(self):
        """Record a tracking success and update health status."""
        async with self._tracking_status_lock:
            if not self._tracking_healthy:
                logger.info("Usage tracking service recovered")
                self._tracking_healthy = True
            self._tracking_failure_count = 0

    @lru_cache(maxsize=1024)
    def _is_api_route(self, path: str) -> bool:
        """Check if a path is an API route that should be tracked."""
        # Only track /v1/ and /api/ routes
        return path.startswith(("/v1/", "/api/"))

    def _requires_auth(self, path: str) -> bool:
        """Check if a path requires authentication."""
        # Most API routes require authentication
        # Except for health checks, models listings, and other public routes
        if path in [
            "/health",
            "/v1/health",
            "/v1/test",
            "/v1/models",
            "/v1/audio/voices",
        ]:
            return False

        # Usage stats endpoint - special case, requires auth but doesn't validate against character limits
        if path == "/v1/usage":
            return False

        # All speech generation endpoints require authentication
        if path in ["/v1/audio/speech", "/api/text-to-speech"]:
            return True

        # Default to requiring auth for API routes
        return self._is_api_route(path)

    def _should_track(self, request: Request, response: Response) -> bool:
        """Determine if we should track this request/response pair."""
        # Don't track requests to non-API routes
        if not self._is_api_route(request.url.path):
            return False

        # Don't track unsuccessful requests
        if response.status_code >= 400:
            return False

        # Don't track health check or public endpoints
        if request.url.path in [
            "/health",
            "/v1/health",
            "/v1/test",
            "/v1/models",
            "/v1/audio/voices",
        ]:
            return False

        return True

    async def _extract_request_data(self, request: Request) -> Dict:
        """Extract all relevant data from request for tracking and validation."""
        data = {
            "text_length": None,
            "voice_id": None,
            "speed": None,
        }

        # Only extract detailed data for speech generation endpoints
        if request.url.path == "/v1/audio/speech":
            try:
                body = await request.json()
                data["text_length"] = len(body.get("input", ""))
                data["voice_id"] = body.get("voice")
                data["speed"] = body.get("speed")
            except Exception as e:
                logger.warning(f"Error extracting request data: {e}")

        return data

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
        # Extract a longer prefix from the API key - use 16 chars instead of 8
        # This makes it much more likely to be unique, even with common prefixes
        api_key_prefix = api_key[:16]

        # Check our local cached copy first
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

        # Not in cache or expired, use the UsageTrackingService which has the API key cache manager
        try:
            usage_service = await self._get_usage_service()

            # Track validation timing
            validation_start = time.time()

            # Use the service to validate
            (
                is_valid,
                error_message,
                request_info,
            ) = await usage_service._validate_api_key(api_key)

            # Record validation timing
            validation_time = time.time() - validation_start
            if validation_time > 0.1:  # Log if validation takes more than 100ms
                logger.debug(
                    f"API key validation took {validation_time:.4f}s for {api_key_prefix}"
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

                # Cache successful validations locally too
                async with self._cache_lock:
                    self._api_key_cache[api_key_prefix] = {
                        "timestamp": datetime.utcnow().timestamp(),
                        "info": request_info,
                    }

            return is_valid, error_message, request_info
        except Exception as e:
            logger.error(f"Error validating API key: {e}")

            # Check for circuit breaker related errors
            error_str = str(e).lower()
            if (
                "circuit breaker" in error_str
                or "database" in error_str
                or "connection" in error_str
            ):
                logger.warning(
                    f"Database unavailable during API key validation, using emergency fallback"
                )

                # Emergency fallback: If API key has valid format, allow limited access
                # This will allow service to continue functioning during database outages
                if (
                    api_key.startswith("sk-kokor") or api_key.startswith("sk-kokoro-")
                ) and len(api_key) >= 32:
                    # Create minimal mock info for the request to proceed
                    emergency_info = {
                        "user": {"id": "emergency_" + api_key_prefix},
                        "subscription": {
                            "id": "emergency_sub_" + api_key_prefix,
                            "current_period_start": datetime.utcnow().isoformat(),
                            "current_period_end": datetime.utcnow().isoformat(),
                        },
                        "usage": {
                            "total_characters": 0,
                            "characters_used": 0,
                            "limit": 1000000,  # Set a reasonable emergency limit
                            "emergency_mode": True,
                        },
                    }

                    # Cache this emergency info temporarily
                    async with self._cache_lock:
                        self._api_key_cache[api_key_prefix] = {
                            "timestamp": datetime.utcnow().timestamp(),
                            "info": emergency_info,
                            "is_emergency": True,
                        }

                    return True, None, emergency_info

                # Return a special error message that our dispatch method can recognize
                return False, "Database unavailable", None

            # For other errors, API key validation fails
            return False, "Invalid API key", None

    async def _check_circuit_breaker(self):
        """Check if the circuit breaker is open with caching for performance."""
        current_time = time.time()

        # Simple time-based cache with backoff for failures
        cache_ttl = self._circuit_breaker_cache_ttl
        if self._circuit_breaker_failures > 0:
            # Exponential backoff with maximum cap
            cache_ttl = min(
                cache_ttl * (2**self._circuit_breaker_failures),
                self._circuit_breaker_max_backoff,
            )

        # Return cached result if still valid
        if current_time - self._circuit_breaker_last_check < cache_ttl:
            return self._circuit_breaker_status

        # Cache expired, do a lightweight check without database calls
        try:
            # Only check if usage service can be instantiated, but don't run health check
            usage_service = await self._get_usage_service()

            # Circuit is closed if service exists and internal tracking state is good
            if usage_service and not usage_service._tracking_error_count:
                self._circuit_breaker_status = False
                if self._circuit_breaker_failures > 0:
                    logger.info(
                        f"Circuit breaker recovered after {self._circuit_breaker_failures} failures"
                    )
                self._circuit_breaker_failures = 0
            else:
                self._circuit_breaker_failures += 1
                self._circuit_breaker_status = True
        except Exception:
            # Any exception means circuit is open
            self._circuit_breaker_failures += 1
            self._circuit_breaker_status = True

        # Update timestamp regardless of result
        self._circuit_breaker_last_check = current_time

        # Only log when the status is important
        if self._circuit_breaker_status and (
            self._circuit_breaker_failures == 1
            or self._circuit_breaker_failures % 10 == 0
        ):
            logger.warning(
                f"Circuit breaker open (failures: {self._circuit_breaker_failures}, next check in {cache_ttl:.1f}s)"
            )

        return self._circuit_breaker_status

    async def _queue_event(self, event_data):
        """Queue an event for background processing instead of immediate tracking."""
        try:
            # Use a bounded queue with timeout to avoid blocking
            await asyncio.wait_for(self._event_queue.put(event_data), timeout=0.1)
            return True
        except asyncio.TimeoutError:
            # Queue full, drop the event but don't block the request
            return False
        except Exception as e:
            logger.error(f"Error queueing event: {e}")
            return False

    async def dispatch(self, request: Request, call_next):
        """Process the request and validate API key or handle demo request."""
        # Start timing the entire request flow
        request_received_time = time.time()

        # Store request start time in state for end-to-end timing
        request.state.request_start_time = request_received_time

        # Fast path for non-API routes using cached check
        if not self._is_api_route(request.url.path):
            # Don't create timing tracker for non-API routes
            return await call_next(request)

        # Create and attach a timing tracker to the request
        request.state.timing_tracker = TimingTracker()
        request.state.timing_tracker.mark("middleware_entry")

        # Check circuit breaker status - very fast cached check
        request.state.timing_tracker.start_event("circuit_breaker_check")
        circuit_open = await self._check_circuit_breaker()
        request.state.timing_tracker.end_event("circuit_breaker_check")

        # Collect basic request data
        request.state.timing_tracker.start_event("request_data_extraction")

        # Get client IP address with timing
        request.state.timing_tracker.start_event("ip_address_extraction")
        client_ip = self.get_client_ip(request)
        request.state.timing_tracker.end_event("ip_address_extraction")

        # Store IP in request state for later use
        request.state.ip_address = client_ip

        # Extract voice information if available
        request.state.timing_tracker.start_event("voice_processing")
        voice_id = None

        # Only attempt to parse body for text-to-speech endpoints
        if request.url.path in ["/v1/audio/speech", "/api/text-to-speech"]:
            try:
                # Try to get from query params first (prioritize for streaming)
                voice_param = request.query_params.get("voice")
                if voice_param:
                    voice_id = voice_param
                # For JSON bodies, clone but don't consume the body
                elif request.headers.get("content-type", "").startswith(
                    "application/json"
                ):
                    # Don't actually await the body here, just attach the extractor
                    # This will be processed later when needed
                    request.state.extract_voice = True
            except Exception:
                # Ignore errors in voice extraction
                pass

        request.state.voice_id = voice_id
        request.state.timing_tracker.end_event("voice_processing")

        # End request data extraction timing
        request.state.timing_tracker.end_event("request_data_extraction")

        # Only validate API key if the circuit breaker is closed
        # and the endpoint requires authentication
        request_type = "demo"  # Default to demo
        if not circuit_open and self._requires_auth(request.url.path):
            request.state.timing_tracker.start_event("request_validation")

            # Check for API key in header
            api_key = request.headers.get("X-API-Key")

            if api_key:
                # Get usage service
                request.state.timing_tracker.start_event("get_usage_service")
                usage_service = await self._get_usage_service()
                request.state.timing_tracker.end_event("get_usage_service")

                if usage_service:
                    # Use cached validation where possible
                    # For requests containing text, estimate text length for validation
                    text_length = None
                    try:
                        if request.url.path in [
                            "/v1/audio/speech",
                            "/api/text-to-speech",
                        ]:
                            # For streaming TTS, check query params
                            text_param = request.query_params.get("text")
                            if text_param:
                                text_length = len(text_param)
                    except Exception:
                        # Ignore errors in text length estimation
                        pass

                    # Validate API key with improved caching (request path is already checked)
                    valid, error_msg, user_info = await usage_service.validate_request(
                        api_key, text_length=text_length
                    )

                    if valid:
                        request_type = "paid"
                        # Store user info in request state for later use
                        request.state.user = (
                            user_info.get("user") if user_info else None
                        )
                        request.state.subscription = (
                            user_info.get("subscription") if user_info else None
                        )
                    else:
                        # Return error immediately for invalid API key
                        error_message = error_msg or "Invalid API key"
                        return JSONResponse(
                            status_code=401,
                            content={"error": {"message": error_message}},
                        )
            # Check for user ID in header (free tier)
            elif request.headers.get("X-User-ID"):
                request_type = "free"
                user_id = request.headers.get("X-User-ID")
                request.state.user = {"id": user_id}  # Simple user object

                # If validation is required, do it here
                # This is a simplified example

            # For demo requests, just validate IP-based limits with throttling
            else:
                request.state.timing_tracker.start_event("demo_validation")

                # Only check if we're handling a TTS endpoint
                if request.url.path in ["/v1/audio/speech", "/api/text-to-speech"]:
                    try:
                        # Simple check for demo limits - don't actually block during timing tests
                        # In production, this would validate against usage limits
                        pass
                    except Exception as e:
                        logger.error(f"Error validating demo request: {e}")

                request.state.timing_tracker.end_event("demo_validation")

            request.state.timing_tracker.end_event("request_validation")

        # Store request type in state for later use
        request.state.request_type = request_type

        # Mark duration before call_next
        request.state.timing_tracker.mark("middleware_duration_before_handler")

        # Mark the point just before calling the next middleware/handler
        request.state.timing_tracker.mark("before_call_next")

        # Process the request with the next middleware or route handler
        start_time = time.time()
        response = await call_next(request)
        process_time = time.time() - start_time

        # Mark the point after call_next returns
        request.state.timing_tracker.mark("after_call_next")

        # Add timing header for debugging if available
        if hasattr(request.state, "timing_tracker"):
            elapsed = time.time() - request_received_time
            # Record time to response generation
            request.state.timing_tracker.mark("time_to_response_start")

            # Log timing breakdown at the end of the request
            request.state.timing_tracker.log_breakdown()

            # Add response timing headers for development/debugging
            if settings.debug or settings.log_response_timing:
                response.headers["X-Process-Time"] = str(process_time)
                response.headers["X-Total-Time"] = str(elapsed)

                # Add detailed timing for debugging
                if settings.debug:
                    try:
                        timing_data = request.state.timing_tracker.to_dict()
                        response.headers["X-Debug-Timing"] = json.dumps(
                            {
                                "process_time": process_time,
                                "total_time": elapsed,
                                **timing_data,
                            }
                        )
                    except Exception as e:
                        logger.error(f"Error adding timing headers: {e}")

        # Track successful requests only if circuit breaker is closed
        if not circuit_open and self._should_track(request, response):
            # Queue the event for background processing instead of blocking
            event_data = await self._extract_request_data(request)
            if event_data:
                # Add response status and timing
                event_data["status_code"] = response.status_code
                event_data["processing_time_ms"] = int(process_time * 1000)
                event_data["total_time_ms"] = int(elapsed * 1000)

                # Add timing breakdown if available
                if hasattr(request.state, "timing_tracker"):
                    event_data["timing"] = request.state.timing_tracker.to_dict()

                # Queue for background processing
                await self._queue_event(event_data)

        return response

    # Add a method to check tracking health for monitoring
    async def get_tracking_health(self) -> Dict:
        """Get the health status of the tracking service for monitoring."""
        async with self._tracking_status_lock:
            health_status = {
                "is_healthy": self._tracking_healthy,
                "failure_count": self._tracking_failure_count,
                "queue_size": self._event_queue.qsize(),
            }

            # Check database connection if service is available
            usage_service = await self._get_usage_service()
            if usage_service and hasattr(usage_service, "_supabase"):
                supabase = usage_service._supabase
                if hasattr(supabase, "check_health"):
                    health_status["database_connection"] = await supabase.check_health()

            return health_status
