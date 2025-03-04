"""Supabase client for usage tracking."""

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from supabase import AsyncClient, acreate_client

from ...core.config import settings


class SupabaseClient:
    """Supabase client singleton for usage tracking."""

    _instance: Optional["SupabaseClient"] = None
    _client: Optional[AsyncClient] = None

    # Circuit breaker properties
    _circuit_open: bool = False
    _consecutive_failures: int = 0
    _last_failure_time: float = 0
    _failure_threshold: int = 3
    _recovery_timeout: int = 30  # seconds

    def __init__(self):
        """Initialize Supabase client."""
        if not settings.supabase_url or not settings.supabase_key:
            logger.warning("Supabase credentials not configured")
            return

    async def initialize(self):
        """Asynchronously initialize the Supabase client."""
        if self._client is not None:
            return

        if not settings.supabase_url or not settings.supabase_key:
            logger.warning("Supabase credentials not configured")
            return

        try:
            self._client = await acreate_client(
                settings.supabase_url, settings.supabase_key
            )
            logger.info("Supabase client initialized")
            # Reset circuit breaker state on successful initialization
            self._circuit_open = False
            self._consecutive_failures = 0
        except Exception as e:
            logger.error(f"Failed to initialize Supabase client: {e}")
            self._record_failure()

    def _record_failure(self):
        """Record a database failure and possibly open the circuit."""
        self._consecutive_failures += 1
        self._last_failure_time = time.time()

        if self._consecutive_failures >= self._failure_threshold:
            if not self._circuit_open:
                logger.warning(
                    f"Circuit breaker opened after {self._consecutive_failures} consecutive failures"
                )
                self._circuit_open = True

    def _record_success(self):
        """Record a successful database operation and reset failure counter."""
        if self._consecutive_failures > 0:
            self._consecutive_failures = 0

        if self._circuit_open:
            logger.info("Circuit breaker closed after successful operation")
            self._circuit_open = False

    def _should_attempt_operation(self) -> bool:
        """Check if we should attempt a database operation based on circuit state."""
        # If circuit is closed, always attempt
        if not self._circuit_open:
            return True

        # If circuit is open, check if we've waited long enough to try again
        current_time = time.time()
        if current_time - self._last_failure_time > self._recovery_timeout:
            logger.info(f"Attempting recovery after {self._recovery_timeout}s timeout")
            return True

        return False

    async def _execute_with_circuit_breaker(self, operation_func, *args, **kwargs):
        """Execute a database operation with circuit breaker protection."""
        if not self._should_attempt_operation():
            logger.warning("Circuit breaker open, skipping database operation")
            return None

        if not self._client:
            logger.warning("Supabase client not initialized")
            return None

        try:
            # Set a timeout for the operation to prevent hanging
            operation_start = time.time()
            result = await asyncio.wait_for(
                operation_func(*args, **kwargs),
                timeout=5.0,  # 5 second timeout
            )
            operation_time = time.time() - operation_start
            logger.info(f"Database operation completed in {operation_time:.4f}s")

            self._record_success()
            return result
        except (asyncio.TimeoutError, Exception) as e:
            self._record_failure()
            if isinstance(e, asyncio.TimeoutError):
                logger.error("Database operation timed out")
            else:
                logger.error(f"Database operation failed: {e}")
            return None

    @classmethod
    async def get_instance(cls) -> "SupabaseClient":
        """Get Supabase client instance."""
        if cls._instance is None:
            cls._instance = cls()
            await cls._instance.initialize()
        return cls._instance

    async def validate_api_key(self, api_key_prefix: str) -> Optional[Dict]:
        """Validate an API key and return user information.

        Args:
            api_key_prefix: Prefix of the API key (16 characters)

        Returns:
            Optional[Dict]: User and subscription info if valid, None if invalid
        """

        async def _operation():
            if not self._client:
                return None

            validation_start = time.time()

            # Get API key info
            query_start = time.time()
            query = (
                self._client.table("api_keys")
                .select("*, users(*)")
                .eq("key_prefix", api_key_prefix)
                .eq("is_active", True)
                .order("last_used_at", desc=True)  # Order by most recently used
                .order("created_at", desc=True)  # Then by most recently created
                .limit(1)  # Take only the most recent one
            )

            response = await query.execute()
            query_time = time.time() - query_start
            logger.info(f"Supabase API key query took {query_time:.4f}s")

            if not response.data or len(response.data) == 0:
                logger.warning(f"Invalid API key prefix: {api_key_prefix}")
                return None

            api_key_data = response.data[0]

            # Update last_used_at
            update_start = time.time()
            update_query = (
                self._client.table("api_keys")
                .update({"last_used_at": datetime.utcnow().isoformat()})
                .eq("id", api_key_data["id"])
            )

            await update_query.execute()
            update_time = time.time() - update_start
            logger.info(f"Supabase API key update took {update_time:.4f}s")

            total_time = time.time() - validation_start
            logger.info(f"Total Supabase API key validation took {total_time:.4f}s")

            return api_key_data

        return await self._execute_with_circuit_breaker(_operation)

    async def get_active_subscription(self, user_id: str) -> Optional[Dict]:
        """Get active subscription for a user."""

        async def _operation():
            if not self._client:
                return None

            # Get active subscription with product info
            query = (
                self._client.table("subscriptions")
                .select("*, products(*)")
                .eq("user_id", user_id)
                .eq("status", "active")
                .limit(1)
            )

            response = await query.execute()

            if not response.data or len(response.data) == 0:
                return None

            return response.data[0]

        return await self._execute_with_circuit_breaker(_operation)

    async def get_subscription(self, subscription_id: str) -> Optional[Dict]:
        """Get subscription by ID.

        Args:
            subscription_id: Subscription identifier

        Returns:
            Optional[Dict]: Subscription info if exists, None otherwise
        """
        if not self._client:
            logger.warning("Supabase client not initialized")
            return None

        try:
            # Get subscription with product info
            query = (
                self._client.table("subscriptions")
                .select("*, products(*)")
                .eq("id", subscription_id)
                .limit(1)
            )

            response = await query.execute()

            if not response.data or len(response.data) == 0:
                return None

            return response.data[0]

        except Exception as e:
            logger.error(f"Failed to get subscription: {e}")
            return None

    async def track_usage(
        self,
        subscription_id: str,
        period_start: datetime,
        period_end: datetime,
        character_count: int,
        request_count: int = 1,
    ) -> bool:
        """Track usage for a subscription period.

        Args:
            subscription_id: Subscription identifier
            period_start: Start of subscription period
            period_end: End of subscription period
            character_count: Number of characters processed
            request_count: Number of requests to add (default: 1)

        Returns:
            bool: True if tracking successful, False otherwise
        """
        if not self._client:
            logger.warning("Supabase client not initialized")
            return False

        try:
            # Try to get existing usage record
            query = (
                self._client.table("usage")
                .select("*")
                .eq("subscription_id", subscription_id)
                .eq("period_start", period_start.isoformat())
                .limit(1)
            )

            response = await query.execute()

            now = datetime.utcnow()

            if response.data and len(response.data) > 0:
                # Update existing record
                usage_data = response.data[0]
                update_query = (
                    self._client.table("usage")
                    .update(
                        {
                            "total_requests": usage_data["total_requests"]
                            + request_count,
                            "characters_used": usage_data.get("characters_used", 0)
                            + character_count,
                            "last_request_at": now.isoformat(),
                        }
                    )
                    .eq("id", usage_data["id"])
                )

                await update_query.execute()
            else:
                # Create new record
                insert_query = self._client.table("usage").insert(
                    {
                        "subscription_id": subscription_id,
                        "period_start": period_start.isoformat(),
                        "period_end": period_end.isoformat(),
                        "total_requests": request_count,
                        "characters_used": character_count,
                        "last_request_at": now.isoformat(),
                        "reported_to_stripe": False,
                    }
                )

                await insert_query.execute()

            return True

        except Exception as e:
            logger.error(f"Failed to track usage: {e}")
            return False

    async def get_period_usage(
        self, subscription_id: str, period_start: datetime, period_end: datetime
    ) -> Optional[Dict]:
        """Get usage statistics for a subscription period.

        Args:
            subscription_id: Subscription identifier
            period_start: Start of subscription period
            period_end: End of subscription period

        Returns:
            Optional[Dict]: Usage statistics or None if error
        """
        if not self._client:
            logger.warning("Supabase client not initialized")
            return None

        try:
            # Get usage for period
            query = (
                self._client.table("usage")
                .select("*")
                .eq("subscription_id", subscription_id)
                .eq("period_start", period_start.isoformat())
                .limit(1)
            )

            response = await query.execute()

            if not response.data or len(response.data) == 0:
                return {
                    "total_requests": 0,
                    "characters_used": 0,
                    "last_request_at": None,
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "reported_to_stripe": False,
                }

            return response.data[0]

        except Exception as e:
            logger.error(f"Failed to get usage: {e}")
            return None

    async def get_free_tier_usage(
        self,
        user_id: str,
        period_start: datetime,
        period_end: Optional[datetime] = None,
    ) -> Optional[Dict]:
        """Get free tier usage for a user in a specific period.

        Args:
            user_id: User identifier (can be a regular user ID or a demo user ID)
            period_start: Start of period
            period_end: End of period (optional)

        Returns:
            Optional[Dict]: Usage statistics or None if error
        """
        if not self._client:
            logger.warning("Supabase client not initialized")
            return None

        try:
            # Format period_start as ISO string for query
            period_start_iso = period_start.isoformat()

            # Get free usage for period
            query = (
                self._client.table("free_usage")
                .select("*")
                .eq("user_id", user_id)
                .eq("period_start", period_start_iso)
            )

            # Add period_end filter if provided
            if period_end:
                query = query.eq("period_end", period_end.isoformat())

            query = query.limit(1)
            response = await query.execute()

            if not response.data or len(response.data) == 0:
                # Return empty usage record with defaults
                return {
                    "total_requests": 0,
                    "characters_used": 0,
                    "last_request_at": None,
                    "period_start": period_start_iso,
                    "period_end": period_end.isoformat() if period_end else None,
                }

            return response.data[0]

        except Exception as e:
            logger.error(f"Failed to get free tier usage: {e}")
            return None

    async def track_free_tier_usage(
        self,
        user_id: str,
        period_start: datetime,
        period_end: datetime,
        character_count: int,
        request_count: int = 1,
    ) -> bool:
        """Track usage for a free tier user.

        Args:
            user_id: User identifier (can be a regular user ID or a demo user ID)
            period_start: Start of period
            period_end: End of period
            character_count: Number of characters processed
            request_count: Number of requests to add (default: 1)

        Returns:
            bool: True if tracking successful, False otherwise
        """
        if not self._client:
            logger.warning("Supabase client not initialized")
            return False

        try:
            # Handle demo users (with prefix "demo:")
            is_demo_user = user_id.startswith("demo:")

            # Verify user exists before proceeding
            if not is_demo_user:  # Skip for demo users which are handled below
                # Check if user exists
                user_query = (
                    self._client.table("users").select("id").eq("id", user_id).limit(1)
                )
                user_result = await user_query.execute()

                if not user_result.data or len(user_result.data) == 0:
                    logger.error(
                        f"Cannot track free tier usage: User {user_id} does not exist"
                    )
                    return False

            # Try to get existing usage record
            query = (
                self._client.table("free_usage")
                .select("*")
                .eq("user_id", user_id)
                .eq("period_start", period_start.isoformat())
                .limit(1)
            )

            response = await query.execute()

            now = datetime.utcnow()

            if response.data and len(response.data) > 0:
                # Update existing record
                usage_data = response.data[0]
                update_query = (
                    self._client.table("free_usage")
                    .update(
                        {
                            "total_requests": usage_data["total_requests"]
                            + request_count,
                            "characters_used": usage_data.get("characters_used", 0)
                            + character_count,
                            "last_request_at": now.isoformat(),
                        }
                    )
                    .eq("id", usage_data["id"])
                )

                await update_query.execute()
            else:
                # For demo users, we need to create a user record first if it doesn't exist
                if is_demo_user and not await self._ensure_demo_user_exists(user_id):
                    logger.warning(f"Failed to create demo user record for {user_id}")
                    return False

                # Create new record
                insert_query = self._client.table("free_usage").insert(
                    {
                        "user_id": user_id,
                        "period_start": period_start.isoformat(),
                        "period_end": period_end.isoformat(),
                        "total_requests": request_count,
                        "characters_used": character_count,
                        "last_request_at": now.isoformat(),
                    }
                )

                await insert_query.execute()

            return True

        except Exception as e:
            logger.error(f"Failed to track free tier usage: {e}")
            return False

    async def _ensure_demo_user_exists(self, demo_user_id: str) -> bool:
        """Ensure a demo user record exists in the users table.

        Args:
            demo_user_id: Demo user ID (format: "demo:ip_address")

        Returns:
            bool: True if user exists or was created, False otherwise
        """
        if not self._client:
            return False

        try:
            # Check if user already exists
            query = (
                self._client.table("users").select("id").eq("id", demo_user_id).limit(1)
            )

            response = await query.execute()

            if response.data and len(response.data) > 0:
                return True

            # Create demo user record
            ip_address = demo_user_id.split(":", 1)[1]
            insert_query = self._client.table("users").insert(
                {
                    "id": demo_user_id,
                    "email": f"demo_{ip_address}@example.com",  # Placeholder email
                    "full_name": f"Demo User ({ip_address})",
                }
            )

            await insert_query.execute()

            return True
        except Exception as e:
            logger.error(f"Failed to ensure demo user exists: {e}")
            return False

    async def track_usage_event(
        self,
        endpoint: str,
        http_method: str,
        status_code: int,
        character_count: int,
        request_type: str,
        user_id: Optional[str] = None,
        subscription_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        processing_time_ms: Optional[int] = None,
        user_agent: Optional[str] = None,
        referrer: Optional[str] = None,
        voice_id: Optional[str] = None,
        audio_duration_ms: Optional[int] = None,
        word_count: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None,
    ) -> bool:
        """Track a usage event with circuit breaker pattern."""
        try:
            # Generate request ID if not provided
            if not request_id:
                request_id = str(uuid.uuid4())

            # Current time in ISO format
            timestamp = datetime.utcnow().isoformat()

            # Execute the function with the circuit breaker pattern
            return await self._execute_with_circuit_breaker(
                self._track_usage_event_impl,
                endpoint=endpoint,
                http_method=http_method,
                status_code=status_code,
                character_count=character_count,
                request_type=request_type,
                user_id=user_id,
                subscription_id=subscription_id,
                ip_address=ip_address,
                processing_time_ms=processing_time_ms,
                user_agent=user_agent,
                referrer=referrer,
                voice_id=voice_id,
                audio_duration_ms=audio_duration_ms,
                word_count=word_count,
                metadata=metadata,
                request_id=request_id,
                timestamp=timestamp,
            )
        except Exception as e:
            logger.error(f"Failed to track usage event: {str(e)}")
            self._record_failure()
            return False

    async def _track_usage_event_impl(
        self,
        endpoint: str,
        http_method: str,
        status_code: int,
        character_count: int,
        request_type: str,
        user_id: Optional[str] = None,
        subscription_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        processing_time_ms: Optional[int] = None,
        user_agent: Optional[str] = None,
        referrer: Optional[str] = None,
        voice_id: Optional[str] = None,
        audio_duration_ms: Optional[int] = None,
        word_count: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> bool:
        """Actual implementation of the track_usage_event method."""
        if not self._client:
            logger.warning("Supabase client not initialized")
            return False

        try:
            # Create event data
            event_data = {
                "request_id": request_id,
                "timestamp": timestamp or datetime.utcnow().isoformat(),
                "endpoint": endpoint,
                "http_method": http_method,
                "status_code": status_code,
                "character_count": character_count,
                "request_type": request_type,
            }

            # Add optional fields if provided
            if user_id:
                event_data["user_id"] = user_id
            if subscription_id:
                event_data["subscription_id"] = subscription_id
            if ip_address:
                event_data["ip_address"] = ip_address
            if processing_time_ms is not None:
                event_data["processing_time_ms"] = processing_time_ms
            if user_agent:
                event_data["user_agent"] = user_agent
            if referrer:
                event_data["referrer"] = referrer
            if voice_id:
                event_data["voice_id"] = voice_id
            if audio_duration_ms is not None:
                event_data["audio_duration_ms"] = audio_duration_ms
            if word_count is not None:
                event_data["word_count"] = word_count
            if metadata:
                event_data["metadata"] = json.dumps(metadata)

            # Insert event into the database
            result = (
                await self._client.from_("usage_events").insert(event_data).execute()
            )

            # Check if the insertion was successful
            if result.data:
                logger.debug(f"Successfully tracked usage event: {request_id}")
                return True
            else:
                logger.warning(f"Failed to track usage event: {result.error}")
                return False

        except Exception as e:
            logger.error(f"Error tracking usage event: {e}")
            return False

    async def update_usage_event(
        self, request_id: str, updates: Dict[str, Any]
    ) -> bool:
        """Update an existing usage event entry, primarily for streaming requests.

        Args:
            request_id: The unique request ID of the event to update
            updates: Dictionary containing the fields to update

        Returns:
            bool: True if successful, False otherwise
        """
        if not self._should_attempt_operation():
            return False

        if not self._client:
            logger.warning("Supabase client not initialized.")
            return False

        try:
            # Execute the function with the circuit breaker pattern
            return await self._execute_with_circuit_breaker(
                self._update_usage_event_impl, request_id=request_id, updates=updates
            )
        except Exception as e:
            logger.error(f"Failed to update usage event: {str(e)}")
            self._record_failure()
            return False

    async def _update_usage_event_impl(
        self, request_id: str, updates: Dict[str, Any]
    ) -> bool:
        """Implementation of usage event update.

        Args:
            request_id: The unique request ID of the event to update
            updates: Dictionary containing the fields to update

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # First try to use an RPC call for better performance
            try:
                result = await self._client.rpc(
                    "update_usage_event",
                    {"p_request_id": request_id, "p_updates": updates},
                    timeout=5,
                ).execute()

                if result.data and result.data.get("success", False):
                    return True
            except Exception as e:
                logger.warning(
                    f"RPC update_usage_event failed, falling back to direct update: {e}"
                )

            # Fallback to direct update if RPC fails
            result = (
                await self._client.table("usage_events")
                .update(updates)
                .eq("request_id", request_id)
                .execute()
            )

            if not result.data:
                logger.warning(
                    f"Failed to update usage event {request_id}: No data returned"
                )
                return False

            return True

        except Exception as e:
            logger.error(f"Error updating usage event: {e}")
            raise  # Re-raise to be caught by circuit breaker

    async def check_overage_and_report_to_stripe(
        self, subscription_id: str, metered_item_id: str, character_limit: int
    ) -> Tuple[bool, Optional[str]]:
        """Check if usage exceeds limits and report overage to Stripe if needed.

        Args:
            subscription_id: Supabase subscription ID
            metered_item_id: Stripe metered subscription item ID
            character_limit: Character limit included in the plan

        Returns:
            Tuple[bool, Optional[str]]: (Success status, Error message if any)
        """
        if not self._client:
            logger.warning("Supabase client not initialized")
            return False, "Supabase client not initialized"

        try:
            # Get current usage
            now = datetime.utcnow()
            period_start = datetime(now.year, now.month, 1)

            # First get the usage record ID for locking purposes
            usage_query = (
                self._client.table("usage")
                .select("id, characters_used, reported_to_stripe")
                .eq("subscription_id", subscription_id)
                .eq("period_start", period_start.isoformat())
                .limit(1)
            )

            usage_response = await usage_query.execute()

            if not usage_response.data or len(usage_response.data) == 0:
                logger.warning(f"No usage found for subscription {subscription_id}")
                return False, "No usage record found"

            usage_id = usage_response.data[0]["id"]
            characters_used = usage_response.data[0].get("characters_used", 0)
            reported_to_stripe = usage_response.data[0].get("reported_to_stripe", False)

            # Check if we're already over the limit and need to report
            if characters_used <= character_limit:
                # Still within included limit
                return False, "Usage within included limit"

            # Check if already reported to Stripe
            if reported_to_stripe:
                return False, "Already reported to Stripe"

            # Calculate billable usage
            billable_characters = characters_used - character_limit

            # Use a database update with a condition to ensure we only update
            # if the reported_to_stripe flag is still false (optimistic locking)
            update_query = (
                self._client.table("usage")
                .update({"reported_to_stripe": True})
                .eq("id", usage_id)
                .eq("reported_to_stripe", False)
            )

            update_result = await update_query.execute()

            # Check if the update was successful (affected a row)
            if not update_result.data or len(update_result.data) == 0:
                # Another process might have updated it already
                return False, "Race condition: Another process reported usage already"

            # Successfully marked as reported
            logger.info(
                f"Marked {billable_characters} characters as billable for subscription {subscription_id}"
            )
            return True, None

        except Exception as e:
            error_msg = f"Failed to check and report usage: {e}"
            logger.error(error_msg)
            return False, error_msg

    async def get_all_metered_subscriptions(self) -> Optional[List[Dict]]:
        """Get all active subscriptions with metered billing.

        Returns:
            Optional[List[Dict]]: List of active subscriptions with metered billing or None if error
        """
        if not self._client:
            logger.warning("Supabase client not initialized")
            return None

        try:
            # Get all active subscriptions with products that have metered billing
            query = (
                self._client.table("subscriptions")
                .select("*, products(*)")
                .eq("status", "active")
                .not_.is_("products.stripe_metered_price_id", "null")
            )

            result = await query.execute()

            if not result.data:
                return []

            return result.data

        except Exception as e:
            logger.error(f"Failed to get metered subscriptions: {e}")
            return None

    async def check_health(self) -> bool:
        """Check if the database is available and the circuit is closed."""
        # If circuit is open, we know there are issues
        if self._circuit_open:
            return False

        if not self._client:
            return False

        try:
            # Simple query to test connection
            await self._client.from_("health_check").select("count").limit(1).execute()
            return True
        except Exception:
            return False

    async def reset_circuit_breaker(self):
        """Manually reset the circuit breaker state."""
        self._circuit_open = False
        self._consecutive_failures = 0
        logger.info("Circuit breaker manually reset")

    async def track_usage_events_batch(self, events: List[Dict]) -> bool:
        """Track multiple usage events in a batch operation.

        Args:
            events: List of event dictionaries to track

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            if not events:
                return True

            # Generate a single batch request_id that will be used for all events missing request_id
            batch_request_id = str(uuid.uuid4())

            for event in events:
                if "request_id" not in event:
                    # Use the same batch_request_id for all events in this batch
                    event["request_id"] = batch_request_id

                # Add timestamp if not present
                if "timestamp" not in event:
                    event["timestamp"] = datetime.utcnow().isoformat()

            # Note: RPC function 'insert_usage_events_batch' doesn't exist in the database schema
            # Use chunked inserts directly instead of attempting RPC

            # Split into chunks of 50 records for better performance
            chunk_size = 50
            success_count = 0
            total_chunks = (
                len(events) + chunk_size - 1
            ) // chunk_size  # Ceiling division

            for i in range(0, len(events), chunk_size):
                chunk = events[i : i + chunk_size]
                try:
                    insert_result = await asyncio.wait_for(
                        self._client.table("usage_events").insert(chunk).execute(),
                        timeout=5.0,
                    )

                    if insert_result.data:
                        success_count += 1
                except Exception as chunk_error:
                    logger.error(
                        f"Error inserting chunk {i // chunk_size + 1}/{total_chunks}: {chunk_error}"
                    )

            # Consider it a success if at least half the chunks were successful
            return success_count >= total_chunks / 2

        except Exception as e:
            logger.error(f"Failed to track usage events batch: {e}")
            return False

    async def purge_old_events(self, days_to_retain: int = 90) -> bool:
        """Purge usage events older than the specified retention period.

        Args:
            days_to_retain: Number of days of data to keep

        Returns:
            bool: True if purge was successful
        """
        if not self._client:
            logger.warning("Supabase client not initialized")
            return False

        try:
            # Calculate cutoff date
            cutoff_date = (
                datetime.utcnow() - timedelta(days=days_to_retain)
            ).isoformat()

            # Try to use the more efficient RPC if available
            try:
                result = await self._client.rpc(
                    "purge_old_usage_events", {"p_cutoff_date": cutoff_date}
                ).execute()

                if result.data:
                    logger.info(
                        f"Purged {result.data.get('deleted_count', 0)} old usage events"
                    )
                    return True
            except Exception as e:
                logger.warning(f"RPC purge failed, falling back to direct delete: {e}")

            # Fallback to direct delete
            # Note: This might time out for large datasets, but the RPC is designed to avoid that
            delete_result = (
                await self._client.table("usage_events")
                .delete()
                .lt("timestamp", cutoff_date)
                .execute()
            )
            logger.info(f"Purged old usage events before {cutoff_date}")
            return True

        except Exception as e:
            logger.error(f"Failed to purge old events: {e}")
            return False
