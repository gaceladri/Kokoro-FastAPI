"""Supabase client for usage tracking."""

from datetime import datetime
from typing import Dict, List, Optional, Tuple

from loguru import logger
from supabase import AsyncClient, acreate_client

from ...core.config import settings


class SupabaseClient:
    """Supabase client singleton for usage tracking."""

    _instance: Optional["SupabaseClient"] = None
    _client: Optional[AsyncClient] = None

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
            self._client = await acreate_client(settings.supabase_url, settings.supabase_key)
            logger.info("Supabase client initialized")
        except Exception as e:
            logger.error(f"Failed to initialize Supabase client: {e}")

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
            api_key_prefix: First 8 characters of the API key

        Returns:
            Optional[Dict]: User and subscription info if valid, None if invalid
        """
        if not self._client:
            logger.warning("Supabase client not initialized")
            return None

        try:
            # Get API key info
            query = (
                self._client.table("api_keys")
                .select("*, users(*)")
                .eq("key_prefix", api_key_prefix)
                .eq("is_active", True)
                .order("last_used_at", desc=True)  # Order by most recently used
                .order("created_at", desc=True)    # Then by most recently created
                .limit(1)                          # Take only the most recent one
            )
            
            response = await query.execute()

            if not response.data or len(response.data) == 0:
                logger.warning(f"Invalid API key prefix: {api_key_prefix}")
                return None
                
            api_key_data = response.data[0]

            # Update last_used_at
            update_query = (
                self._client.table("api_keys")
                .update({"last_used_at": datetime.utcnow().isoformat()})
                .eq("id", api_key_data["id"])
            )
            
            await update_query.execute()

            return api_key_data

        except Exception as e:
            logger.error(f"Failed to validate API key: {e}")
            return None

    async def get_active_subscription(self, user_id: str) -> Optional[Dict]:
        """Get active subscription for a user.

        Args:
            user_id: User identifier

        Returns:
            Optional[Dict]: Active subscription info if exists, None otherwise
        """
        if not self._client:
            logger.warning("Supabase client not initialized")
            return None

        try:
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

        except Exception as e:
            logger.error(f"Failed to get subscription: {e}")
            return None

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
                    .update({
                        "total_requests": usage_data["total_requests"] + request_count,
                        "characters_used": usage_data.get("characters_used", 0)
                        + character_count,
                        "last_request_at": now.isoformat(),
                    })
                    .eq("id", usage_data["id"])
                )
                
                await update_query.execute()
            else:
                # Create new record
                insert_query = (
                    self._client.table("usage")
                    .insert({
                        "subscription_id": subscription_id,
                        "period_start": period_start.isoformat(),
                        "period_end": period_end.isoformat(),
                        "total_requests": request_count,
                        "characters_used": character_count,
                        "last_request_at": now.isoformat(),
                        "reported_to_stripe": False,
                    })
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
                    self._client.table("users")
                    .select("id")
                    .eq("id", user_id)
                    .limit(1)
                )
                user_result = await user_query.execute()
                
                if not user_result.data or len(user_result.data) == 0:
                    logger.error(f"Cannot track free tier usage: User {user_id} does not exist")
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
                    .update({
                        "total_requests": usage_data["total_requests"] + request_count,
                        "characters_used": usage_data.get("characters_used", 0)
                        + character_count,
                        "last_request_at": now.isoformat(),
                    })
                    .eq("id", usage_data["id"])
                )
                
                await update_query.execute()
            else:
                # For demo users, we need to create a user record first if it doesn't exist
                if is_demo_user and not await self._ensure_demo_user_exists(user_id):
                    logger.warning(f"Failed to create demo user record for {user_id}")
                    return False

                # Create new record
                insert_query = (
                    self._client.table("free_usage")
                    .insert({
                        "user_id": user_id,
                        "period_start": period_start.isoformat(),
                        "period_end": period_end.isoformat(),
                        "total_requests": request_count,
                        "characters_used": character_count,
                        "last_request_at": now.isoformat(),
                    })
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
                self._client.table("users")
                .select("id")
                .eq("id", demo_user_id)
                .limit(1)
            )
            
            response = await query.execute()

            if response.data and len(response.data) > 0:
                return True

            # Create demo user record
            ip_address = demo_user_id.split(":", 1)[1]
            insert_query = (
                self._client.table("users")
                .insert({
                    "id": demo_user_id,
                    "email": f"demo_{ip_address}@example.com",  # Placeholder email
                    "full_name": f"Demo User ({ip_address})",
                })
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
        metadata: Optional[Dict] = None,
    ) -> bool:
        """Track a single API request event for time-series analytics.

        Args:
            endpoint: API endpoint path
            http_method: HTTP method (GET, POST, etc.)
            status_code: HTTP status code
            character_count: Number of characters processed
            request_type: Type of request ('paid', 'free', or 'demo')
            user_id: User ID if available
            subscription_id: Subscription ID if available
            ip_address: Client IP address
            processing_time_ms: Request processing time in milliseconds
            user_agent: User agent string
            metadata: Additional request metadata

        Returns:
            bool: True if tracking successful, False otherwise
        """
        if not self._client:
            logger.warning("Supabase client not initialized")
            return False
            
        try:
            # Create event record
            event_data = {
                "endpoint": endpoint,
                "http_method": http_method,
                "status_code": status_code,
                "character_count": character_count,
                "request_type": request_type,
                "timestamp": datetime.utcnow().isoformat(),
            }
            
            # Add optional fields if they exist
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
                
            if metadata:
                event_data["metadata"] = metadata
                
            # Insert the event
            insert_query = self._client.table("usage_events").insert(event_data)
            await insert_query.execute()
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to track usage event: {e}")
            return False

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
