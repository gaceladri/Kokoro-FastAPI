import asyncio
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import stripe
from fastapi import Request
from loguru import logger

from ...core.config import settings
from .supabase_client import SupabaseClient

stripe.api_key = settings.stripe_secret_key
USE_STRIPE = (
    settings.stripe_secret_key is not None and len(settings.stripe_secret_key) > 0
)


def parse_iso_datetime(date_string: str) -> datetime:
    """
    Parse ISO format datetime strings safely, handling timezone information.
    Always returns a naive UTC datetime for consistency in comparisons.

    Args:
        date_string: ISO format datetime string

    Returns:
        datetime object (naive, in UTC)
    """
    try:
        # Try the built-in method first
        dt = datetime.fromisoformat(date_string)
        # If it has timezone info, convert to UTC and remove timezone
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except ValueError:
        # For strings with timezone and microseconds that older Python versions can't handle
        if "+" in date_string or "-" in date_string[10:]:  # Has timezone info
            # Extract just the date and time part
            match = re.match(
                r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.?\d*([+-].+)?", date_string
            )
            if match:
                basic_datetime = match.group(1)
                # Parse without timezone info
                return datetime.fromisoformat(basic_datetime)

        # Fallback
        logger.warning(f"Failed to parse datetime: {date_string}, using current time")
        return datetime.utcnow()


class UsageTrackingService:
    """Service for tracking TTS usage."""

    def __init__(self):
        """Initialize usage tracking service."""
        self._supabase = None  # Will be initialized in create()
        self._demo_cache = defaultdict(list)  # IP -> List[timestamp]
        self._last_cleanup = time.time()
        self._cache_lock = asyncio.Lock()
        self._stripe_reporting_lock = asyncio.Lock()  # Add lock for Stripe reporting
        self._demo_user_id = None  # Cached demo user ID

    @classmethod
    async def create(cls) -> "UsageTrackingService":
        """Create and initialize usage tracking service."""
        service = cls()
        # Initialize Supabase client
        service._supabase = await SupabaseClient.get_instance()
        # Initialize the demo user if usage tracking is enabled
        if settings.enable_usage_tracking:
            await service._ensure_demo_user_exists()
        return service

    async def _ensure_demo_user_exists(self) -> str:
        """Ensure a demo user exists for tracking demo usage.

        Returns:
            str: UUID of the demo user, or None if creation failed
        """
        if self._demo_user_id:
            # Verify that the cached user ID actually exists in the database
            user_exists = await self._verify_user_exists(self._demo_user_id)
            if user_exists:
                return self._demo_user_id
            else:
                logger.warning(
                    f"Cached demo user ID {self._demo_user_id} does not exist in database - will recreate"
                )
                self._demo_user_id = None  # Reset the cached ID since it's invalid

        # Check if the demo user already exists
        try:
            query = (
                self._supabase._client.table("users")
                .select("id")
                .eq("email", "demo@kokoro.ai")
            )

            result = await query.execute()

            if result.data and len(result.data) > 0:
                self._demo_user_id = result.data[0]["id"]
                logger.info(f"Using existing demo user with ID: {self._demo_user_id}")

                # Double-check that this user ID is actually valid
                user_exists = await self._verify_user_exists(self._demo_user_id)
                if not user_exists:
                    logger.warning(
                        f"Demo user ID {self._demo_user_id} from query doesn't actually exist - will recreate"
                    )
                    self._demo_user_id = None
                else:
                    return self._demo_user_id

            # Create the demo user if it doesn't exist or is invalid
            demo_user = {"email": "demo@kokoro.ai", "full_name": "Demo User"}

            insert_query = self._supabase._client.table("users").insert(demo_user)
            result = await insert_query.execute()

            if result.data and len(result.data) > 0:
                self._demo_user_id = result.data[0]["id"]
                logger.info(f"Created demo user with ID: {self._demo_user_id}")

                # Verify one more time
                user_exists = await self._verify_user_exists(self._demo_user_id)
                if not user_exists:
                    logger.error(
                        f"Newly created demo user {self._demo_user_id} doesn't exist immediately after creation"
                    )
                    self._demo_user_id = None

                return self._demo_user_id
            else:
                logger.error("Failed to create demo user")
                return None

        except Exception as e:
            logger.error(f"Error ensuring demo user exists: {e}")
            return None

    async def _get_demo_user_id(self) -> str:
        """Get the demo user ID, creating if necessary."""
        if not self._demo_user_id:
            return await self._ensure_demo_user_exists()
        return self._demo_user_id

    async def validate_request(
        self,
        api_key_prefix: str,
        text_length: Optional[int] = None,
    ) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """Validate a request using API key and subscription info."""
        if not settings.enable_usage_tracking:
            return True, None, None

        try:
            # Validate API key
            api_key_info = await self._supabase.validate_api_key(api_key_prefix)
            if not api_key_info:
                return False, "Invalid API key", None

            # Get user's active subscription
            user_id = api_key_info["user_id"]
            subscription = await self._supabase.get_active_subscription(user_id)

            if not subscription:
                return False, "No active subscription found", None

            # Check if subscription is active
            now = datetime.utcnow()
            period_start = parse_iso_datetime(subscription["current_period_start"])
            period_end = parse_iso_datetime(subscription["current_period_end"])

            if now < period_start or now > period_end:
                return False, "Subscription period expired", None

            # Get current period usage
            usage = await self._supabase.get_period_usage(
                subscription["id"], period_start, period_end
            )

            # Check usage limits
            product = subscription["products"]
            current_requests = usage["total_requests"] if usage else 0
            current_characters = usage.get("characters_used", 0) if usage else 0

            # Check request limit for all plans
            if product["monthly_request_limit"] is not None:
                if current_requests >= product["monthly_request_limit"]:
                    return False, "Monthly request limit exceeded", None

            # For premium plan with metered billing, don't enforce a strict character limit
            # as it will be billed as overage instead of being rejected
            is_premium = product.get("stripe_metered_price_id") is not None

            # Only enforce character limit for non-premium plans if a limit is set
            if (
                not is_premium
                and text_length is not None
                and product.get("monthly_request_limit") is not None
            ):
                character_limit = product.get("monthly_request_limit", 0)
                if current_characters + text_length > character_limit:
                    return False, "Monthly character limit would be exceeded", None

            return (
                True,
                None,
                {
                    "user": api_key_info["users"],
                    "subscription": subscription,
                    "usage": usage,
                },
            )

        except Exception as e:
            logger.error(f"Failed to validate request: {e}")
            return False, "Internal server error", None

    async def _validate_api_key(
        self, api_key: str
    ) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """Validate API key for compatibility with middleware.

        This is a wrapper around validate_request to provide compatibility
        with code that expects the middleware's _validate_api_key method.

        Args:
            api_key: Full API key

        Returns:
            Tuple containing validity, error message (if any), and info (if valid)
        """
        # Extract the prefix (first 8 characters) from the full API key
        api_key_prefix = api_key[:8] if api_key else ""

        if not api_key_prefix:
            return False, "Invalid API key format", None

        # Delegate to the validate_request method
        return await self.validate_request(api_key_prefix)

    async def validate_free_tier_request(
        self,
        user_id: str,
        text_length: Optional[int] = None,
    ) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """Validate a request for a free tier user."""
        if not settings.enable_usage_tracking:
            return True, None, None

        try:
            # Get current month's boundaries
            now = datetime.utcnow()
            period_start = datetime(now.year, now.month, 1)
            if now.month < 12:
                period_end = datetime(now.year, now.month + 1, 1) - timedelta(seconds=1)
            else:
                period_end = datetime(now.year + 1, 1, 1) - timedelta(seconds=1)

            # Get free tier usage
            usage = await self._supabase.get_free_tier_usage(
                user_id, period_start, period_end
            )

            # Check usage limits
            current_characters = usage.get("characters_used", 0) if usage else 0
            free_tier_limit = settings.free_tier_character_limit

            if text_length is not None and (
                current_characters + text_length > free_tier_limit
            ):
                return (
                    False,
                    f"Free tier character limit would be exceeded (limit: {free_tier_limit})",
                    None,
                )

            return (
                True,
                None,
                {
                    "user_id": user_id,
                    "usage": usage,
                    "period_start": period_start,
                    "period_end": period_end,
                },
            )

        except Exception as e:
            logger.error(f"Failed to validate free tier request: {e}")
            return False, "Internal server error", None

    async def track_request(
        self,
        subscription_id: str,
        period_start: datetime,
        period_end: datetime,
        character_count: int,
    ) -> bool:
        """Track a TTS request and report usage to Stripe."""
        if not settings.enable_usage_tracking:
            return True

        try:
            # Update Supabase
            await self._supabase.track_usage(
                subscription_id=subscription_id,
                period_start=period_start,
                period_end=period_end,
                character_count=character_count,
            )

            # Get subscription details to check for metered billing
            subscription = await self._supabase.get_subscription(subscription_id)
            if not subscription:
                logger.warning(
                    f"Could not find subscription {subscription_id} for usage reporting"
                )
                return False

            # Check if this is a subscription with metered billing
            product = subscription.get("products", {})
            stripe_subscription_id = subscription.get("stripe_subscription_id")
            metered_price_id = product.get("stripe_metered_price_id")

            if USE_STRIPE and stripe_subscription_id and metered_price_id:
                # Use a lock to prevent race conditions in Stripe reporting
                async with self._stripe_reporting_lock:
                    # Get the latest usage data within the lock to prevent race conditions
                    latest_usage = await self._supabase.get_period_usage(
                        subscription_id, period_start, period_end
                    )

                    # Skip if already reported to Stripe
                    if latest_usage and latest_usage.get("reported_to_stripe"):
                        return True

                    # Get Stripe subscription to find the metered item
                    try:
                        stripe_sub = stripe.Subscription.retrieve(
                            stripe_subscription_id
                        )

                        # Find the metered subscription item
                        metered_item_id = None
                        for item in stripe_sub.items.data:
                            if (
                                hasattr(item.price, "recurring")
                                and item.price.recurring.usage_type == "metered"
                            ):
                                metered_item_id = item.id
                                break

                        if metered_item_id:
                            # Get character limit from product
                            character_limit = product.get("monthly_request_limit", 0)

                            # Check if usage exceeds limit
                            if (
                                latest_usage
                                and latest_usage.get("characters_used", 0)
                                > character_limit
                            ):
                                # Calculate billable characters (beyond included limit)
                                billable_characters = (
                                    latest_usage.get("characters_used", 0)
                                    - character_limit
                                )

                                # Create a unique identifier based on subscription and period
                                usage_timestamp = int(datetime.utcnow().timestamp())

                                # Report to Stripe - use the correct parameters supported by the SDK
                                # The action parameter is specified as 'set' in the code
                                usage_record = stripe.SubscriptionItem.create_usage_record(
                                    metered_item_id,
                                    quantity=billable_characters,
                                    timestamp=usage_timestamp,
                                    action="set",  # This is a valid parameter according to Stripe docs
                                )

                                # Update the database to mark as reported
                                await (
                                    self._supabase._client.table("usage")
                                    .update(
                                        {
                                            "reported_to_stripe": True,
                                            "stripe_usage_record_id": usage_record.id,
                                        }
                                    )
                                    .eq("id", latest_usage["id"])
                                    .execute()
                                )

                                logger.info(
                                    f"Reported {billable_characters} billable characters to Stripe for subscription {subscription_id}"
                                )
                    except stripe.error.StripeError as e:
                        # Handle Stripe-specific errors
                        logger.error(f"Stripe API error when reporting usage: {e}")
                    except Exception as e:
                        logger.error(f"Error reporting usage to Stripe: {e}")
                        # Continue even if Stripe reporting fails

            return True
        except Exception as e:
            logger.error(f"Failed to track request: {e}")
            return False

    async def track_free_tier_usage(
        self,
        user_id: str,
        period_start: datetime,
        period_end: datetime,
        character_count: int,
    ) -> bool:
        """Track usage for a free tier user."""
        if not settings.enable_usage_tracking:
            return True

        try:
            # First, verify that the user exists in the database
            user_exists = await self._verify_user_exists(user_id)
            if not user_exists:
                logger.error(
                    f"Cannot track free tier usage: User {user_id} does not exist in the database"
                )
                return False

            # Update Supabase
            await self._supabase.track_free_tier_usage(
                user_id=user_id,
                period_start=period_start,
                period_end=period_end,
                character_count=character_count,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to track free tier usage: {e}")
            return False

    async def _verify_user_exists(self, user_id: str) -> bool:
        """Verify that a user exists in the database.

        Args:
            user_id: User ID to verify

        Returns:
            bool: True if the user exists, False otherwise
        """
        if not self._supabase or not self._supabase._client:
            logger.warning("Supabase client not initialized")
            return False

        try:
            # Query the users table to check if the user exists
            query = (
                self._supabase._client.table("users")
                .select("id")
                .eq("id", user_id)
                .limit(1)
            )

            result = await query.execute()

            # User exists if we got a result
            return result.data and len(result.data) > 0
        except Exception as e:
            logger.error(f"Failed to verify user existence: {e}")
            return False

    async def get_subscription_usage(
        self, subscription_id: str, period_start: datetime, period_end: datetime
    ) -> Optional[Dict]:
        """Get usage statistics for a subscription period."""
        if not settings.enable_usage_tracking:
            return {"total_requests": 0, "characters_used": 0}

        try:
            return await self._supabase.get_period_usage(
                subscription_id, period_start, period_end
            )
        except Exception as e:
            logger.error(f"Failed to get subscription usage: {e}")
            return None

    async def get_free_tier_usage(
        self, user_id: str, period_start: datetime, period_end: datetime
    ) -> Optional[Dict]:
        """Get usage statistics for a free tier user."""
        if not settings.enable_usage_tracking:
            return {"total_requests": 0, "characters_used": 0}

        try:
            return await self._supabase.get_free_tier_usage(
                user_id, period_start, period_end
            )
        except Exception as e:
            logger.error(f"Failed to get free tier usage: {e}")
            return None

    async def _cleanup_demo_cache(self):
        """Clean up old entries from demo cache."""
        now = time.time()
        cutoff = now - 24 * 3600  # 24 hours ago

        async with self._cache_lock:
            for ip in list(self._demo_cache.keys()):
                # Keep only timestamps within last 24 hours
                self._demo_cache[ip] = [
                    ts for ts in self._demo_cache[ip] if ts > cutoff
                ]
                if not self._demo_cache[ip]:
                    del self._demo_cache[ip]

    async def get_demo_request_count(self, ip_address: str) -> int:
        """Get the number of demo requests from this IP in the last 24 hours."""
        # Periodic cleanup of old cache entries
        now = time.time()
        if now - self._last_cleanup > 3600:  # Cleanup every hour
            await self._cleanup_demo_cache()
            self._last_cleanup = now

        cutoff = now - 24 * 3600  # 24 hours ago

        async with self._cache_lock:
            # Count requests in last 24 hours from cache
            count = sum(1 for ts in self._demo_cache[ip_address] if ts > cutoff)

            # If no cached requests, check database for historical data
            if count == 0 and self._supabase._client:
                try:
                    # Get demo user ID
                    demo_user_id = await self._get_demo_user_id()
                    if not demo_user_id:
                        return count

                    # Get the date range for the past 24 hours
                    start_time = datetime.utcnow() - timedelta(hours=24)

                    # Query free_usage table with metadata to find IP-specific usage
                    query = (
                        self._supabase._client.table("free_usage")
                        .select("total_requests")
                        .eq("user_id", demo_user_id)
                        .gte("last_request_at", start_time.isoformat())
                        .eq("metadata->>ip_address", ip_address)
                    )

                    result = await query.execute()

                    if result.data:
                        # Sum up the request count
                        for item in result.data:
                            count += item.get("total_requests", 0)

                        # Update cache with a single timestamp to represent historical count
                        if count > 0:
                            self._demo_cache[ip_address].extend([now - 3600] * count)
                except Exception as e:
                    logger.error(
                        f"Failed to get demo request count for IP {ip_address}: {e}"
                    )

            return count

    async def validate_demo_request(
        self, ip_address: str, text_length: int
    ) -> Tuple[bool, Optional[str]]:
        """Validate a demo request based on IP and text length limits."""
        try:
            # Check text length limit first (fastest check)
            if text_length > settings.demo_max_characters:
                return (
                    False,
                    f"Text length exceeds demo limit of {settings.demo_max_characters} characters",
                )

            # Check daily request limit using cache-first approach
            count = await self.get_demo_request_count(ip_address)
            if count >= settings.demo_daily_limit:
                return False, "Daily demo request limit exceeded"

            return True, None
        except Exception as e:
            logger.error(f"Failed to validate demo request for IP {ip_address}: {e}")
            return False, "Internal server error"

    async def track_demo_request(self, ip_address: str, character_count: int) -> bool:
        """Track a demo request by updating cache and the free_usage table with IP metadata."""
        now = time.time()

        try:
            # Update cache immediately
            async with self._cache_lock:
                self._demo_cache[ip_address].append(now)

            # Get the current month's boundaries for tracking
            now_dt = datetime.utcnow()
            period_start = datetime(now_dt.year, now_dt.month, 1)
            if now_dt.month < 12:
                period_end = datetime(now_dt.year, now_dt.month + 1, 1) - timedelta(
                    seconds=1
                )
            else:
                period_end = datetime(now_dt.year + 1, 1, 1) - timedelta(seconds=1)

            # Get the demo user ID
            demo_user_id = await self._get_demo_user_id()
            if not demo_user_id:
                logger.error("Cannot track demo request: failed to get demo user ID")
                return False

            # Verify that the demo user exists in the database
            user_exists = await self._verify_user_exists(demo_user_id)
            if not user_exists:
                logger.error(
                    f"Cannot track demo request: Demo user {demo_user_id} does not exist in the database"
                )
                # Try to create the demo user again
                demo_user_id = await self._ensure_demo_user_exists()
                if not demo_user_id or not await self._verify_user_exists(demo_user_id):
                    logger.error(
                        "Cannot track demo request: Failed to create demo user"
                    )
                    return False

            # Try to find an existing record for this IP address
            try:
                query = (
                    self._supabase._client.table("free_usage")
                    .select("id", "total_requests", "characters_used")
                    .eq("user_id", demo_user_id)
                    .eq("period_start", period_start.isoformat())
                    .eq("metadata->>ip_address", ip_address)
                )

                result = await query.execute()

                if result.data and len(result.data) > 0:
                    # Update existing record
                    record = result.data[0]
                    updated_data = {
                        "total_requests": record.get("total_requests", 0) + 1,
                        "characters_used": record.get("characters_used", 0)
                        + character_count,
                        "last_request_at": now_dt.isoformat(),
                    }

                    update_query = (
                        self._supabase._client.table("free_usage")
                        .update(updated_data)
                        .eq("id", record["id"])
                    )

                    await update_query.execute()
                else:
                    # Create new record
                    new_record = {
                        "user_id": demo_user_id,
                        "period_start": period_start.isoformat(),
                        "period_end": period_end.isoformat(),
                        "total_requests": 1,
                        "characters_used": character_count,
                        "last_request_at": now_dt.isoformat(),
                        "metadata": {"ip_address": ip_address},
                    }

                    insert_query = self._supabase._client.table("free_usage").insert(
                        new_record
                    )

                    await insert_query.execute()

                return True
            except Exception as e:
                logger.error(f"Failed to track demo request for IP {ip_address}: {e}")
                return False

        except Exception as e:
            logger.error(f"Failed to track demo request for IP {ip_address}: {e}")
            return False

    async def get_demo_usage_stats(self, ip_address: str) -> Dict:
        """Get demo usage statistics for an IP address in the last 24 hours.

        Args:
            ip_address: IP address to get stats for

        Returns:
            Dict containing usage statistics
        """
        try:
            # Get request count for the last 24 hours
            request_count = await self.get_demo_request_count(ip_address)

            # Get demo user ID
            demo_user_id = await self._get_demo_user_id()
            if not demo_user_id:
                logger.error("Cannot get demo usage stats: failed to get demo user ID")
                return self._get_default_demo_stats()

            # Get IP-specific usage from the free_usage table
            total_characters = 0
            monthly_characters = 0

            try:
                # Get usage for the last 24 hours
                start_time = (datetime.utcnow() - timedelta(hours=24)).isoformat()
                query = (
                    self._supabase._client.table("free_usage")
                    .select("characters_used")
                    .eq("user_id", demo_user_id)
                    .gte("last_request_at", start_time)
                    .eq("metadata->>ip_address", ip_address)
                )

                result = await query.execute()

                for row in result.data:
                    total_characters += row.get("characters_used", 0)

                # Get monthly usage
                now = datetime.utcnow()
                period_start = datetime(now.year, now.month, 1)

                monthly_query = (
                    self._supabase._client.table("free_usage")
                    .select("characters_used")
                    .eq("user_id", demo_user_id)
                    .gte("period_start", period_start.isoformat())
                    .eq("metadata->>ip_address", ip_address)
                )

                result = await monthly_query.execute()

                for row in result.data:
                    monthly_characters += row.get("characters_used", 0)

            except Exception as e:
                logger.error(
                    f"Failed to query demo usage stats for IP {ip_address}: {e}"
                )

            return {
                "tier": "demo",
                "total_requests": request_count,  # Last 24 hours
                "total_characters": total_characters,  # Last 24 hours
                "monthly_characters": monthly_characters,  # Current month
                "requests_remaining": settings.demo_daily_limit - request_count,
                "characters_remaining": settings.demo_max_characters,  # Per request limit
                "monthly_limit": settings.free_tier_character_limit,
                "monthly_remaining": settings.free_tier_character_limit
                - monthly_characters,
                "period_start": (datetime.utcnow() - timedelta(hours=24)).isoformat(),
                "period_end": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"Failed to get demo usage stats for IP {ip_address}: {e}")
            return self._get_default_demo_stats()

    def _get_default_demo_stats(self) -> Dict:
        """Get default demo usage statistics."""
        return {
            "tier": "demo",
            "total_requests": 0,
            "total_characters": 0,
            "monthly_characters": 0,
            "requests_remaining": settings.demo_daily_limit,
            "characters_remaining": settings.demo_max_characters,
            "monthly_limit": settings.free_tier_character_limit,
            "monthly_remaining": settings.free_tier_character_limit,
            "period_start": (datetime.utcnow() - timedelta(hours=24)).isoformat(),
            "period_end": datetime.utcnow().isoformat(),
        }

    async def get_user_statistics(self, user_id: str) -> Optional[Dict]:
        """Get usage statistics for a user, handling both free and paid tiers.

        Args:
            user_id: User identifier

        Returns:
            Dict containing usage statistics
        """
        try:
            # Check if user has an active subscription
            subscription = await self._supabase.get_active_subscription(user_id)

            if subscription:
                # Paid tier statistics
                period_start = parse_iso_datetime(subscription["current_period_start"])
                period_end = parse_iso_datetime(subscription["current_period_end"])

                usage = await self.get_subscription_usage(
                    subscription["id"], period_start, period_end
                )

                product = subscription["products"]

                # Check if this is a metered plan (premium)
                is_metered = product.get("stripe_metered_price_id") is not None
                character_limit = product.get("monthly_request_limit", 0)

                # For metered plans, show included usage and overage
                metered_usage = 0
                if is_metered and usage:
                    characters_used = usage.get("characters_used", 0)
                    if characters_used > character_limit:
                        metered_usage = characters_used - character_limit

                return {
                    "tier": "paid",
                    "subscription_id": subscription["id"],
                    "period_start": subscription["current_period_start"],
                    "period_end": subscription["current_period_end"],
                    "total_requests": usage.get("total_requests", 0) if usage else 0,
                    "total_characters": usage.get("characters_used", 0) if usage else 0,
                    "request_limit": product["monthly_request_limit"],
                    "character_limit": character_limit,
                    "requests_remaining": (
                        product["monthly_request_limit"]
                        - usage.get("total_requests", 0)
                        if product["monthly_request_limit"] is not None and usage
                        else None
                    ),
                    "characters_remaining": (
                        max(0, character_limit - usage.get("characters_used", 0))
                        if character_limit and usage
                        else None
                    ),
                    "is_metered": is_metered,
                    "metered_usage": metered_usage,
                    "reported_to_stripe": usage.get("reported_to_stripe", False)
                    if usage
                    else False,
                }
            else:
                # Free tier statistics
                now = datetime.utcnow()
                period_start = datetime(now.year, now.month, 1)
                if now.month < 12:
                    period_end = datetime(now.year, now.month + 1, 1) - timedelta(
                        seconds=1
                    )
                else:
                    period_end = datetime(now.year + 1, 1, 1) - timedelta(seconds=1)

                usage = await self.get_free_tier_usage(
                    user_id, period_start, period_end
                )

                characters_used = usage.get("characters_used", 0) if usage else 0

                return {
                    "tier": "free",
                    "user_id": user_id,
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "total_requests": usage.get("total_requests", 0) if usage else 0,
                    "total_characters": characters_used,
                    "character_limit": settings.free_tier_character_limit,
                    "characters_remaining": max(
                        0, settings.free_tier_character_limit - characters_used
                    ),
                    "is_metered": False,
                }

        except Exception as e:
            logger.error(f"Failed to get user statistics: {e}")
            return None

    async def report_all_overages_to_stripe(self) -> List[Dict]:
        """Report all unreported overages to Stripe.

        This method should be called periodically by a scheduled job to ensure
        all overages are reported to Stripe.

        Returns:
            List[Dict]: List of results with subscription_id and status
        """
        if not settings.enable_usage_tracking or not USE_STRIPE:
            logger.info(
                "Usage tracking or Stripe integration not enabled, skipping overage reporting"
            )
            return []

        logger.info("Starting periodic overage reporting to Stripe")
        results = []

        try:
            # Get all active subscriptions with metered billing
            subscriptions = await self._supabase.get_all_metered_subscriptions()

            if not subscriptions:
                logger.info("No active subscriptions with metered billing found")
                return []

            for subscription in subscriptions:
                subscription_id = subscription.get("id")
                stripe_subscription_id = subscription.get("stripe_subscription_id")
                product = subscription.get("products", {})

                if not stripe_subscription_id or not product:
                    continue

                metered_price_id = product.get("stripe_metered_price_id")
                if not metered_price_id:
                    continue

                # Get current period
                period_start = parse_iso_datetime(subscription["current_period_start"])
                period_end = parse_iso_datetime(subscription["current_period_end"])

                # Get current usage
                usage = await self._supabase.get_period_usage(
                    subscription_id, period_start, period_end
                )

                if not usage:
                    results.append(
                        {
                            "subscription_id": subscription_id,
                            "status": "no_usage_records",
                            "error": None,
                        }
                    )
                    continue

                # Skip if already reported
                if usage.get("reported_to_stripe"):
                    results.append(
                        {
                            "subscription_id": subscription_id,
                            "status": "already_reported",
                            "error": None,
                        }
                    )
                    continue

                # Get character limit
                character_limit = product.get("monthly_request_limit", 0)

                # Check if usage exceeds limit
                characters_used = usage.get("characters_used", 0)
                if characters_used <= character_limit:
                    results.append(
                        {
                            "subscription_id": subscription_id,
                            "status": "within_limits",
                            "error": None,
                        }
                    )
                    continue

                # Calculate billable characters
                billable_characters = characters_used - character_limit

                # Use lock to prevent race conditions
                async with self._stripe_reporting_lock:
                    try:
                        # Re-check that it's not already reported (within the lock)
                        latest_usage = await self._supabase.get_period_usage(
                            subscription_id, period_start, period_end
                        )

                        if latest_usage.get("reported_to_stripe"):
                            results.append(
                                {
                                    "subscription_id": subscription_id,
                                    "status": "already_reported_race_condition",
                                    "error": None,
                                }
                            )
                            continue

                        # Get Stripe subscription
                        stripe_sub = stripe.Subscription.retrieve(
                            stripe_subscription_id
                        )

                        # Find metered item
                        metered_item_id = None
                        for item in stripe_sub.items.data:
                            if (
                                hasattr(item.price, "recurring")
                                and item.price.recurring.usage_type == "metered"
                            ):
                                metered_item_id = item.id
                                break

                        if not metered_item_id:
                            results.append(
                                {
                                    "subscription_id": subscription_id,
                                    "status": "no_metered_item",
                                    "error": "No metered item found in Stripe subscription",
                                }
                            )
                            continue

                        # Report to Stripe
                        usage_timestamp = int(datetime.utcnow().timestamp())
                        usage_record = stripe.SubscriptionItem.create_usage_record(
                            metered_item_id,
                            quantity=billable_characters,
                            timestamp=usage_timestamp,
                            action="set",
                        )

                        # Update database
                        await (
                            self._supabase._client.table("usage")
                            .update(
                                {
                                    "reported_to_stripe": True,
                                    "stripe_usage_record_id": usage_record.id,
                                }
                            )
                            .eq("id", latest_usage["id"])
                            .execute()
                        )

                        logger.info(
                            f"Reported {billable_characters} billable characters to Stripe for subscription {subscription_id}"
                        )

                        results.append(
                            {
                                "subscription_id": subscription_id,
                                "status": "reported",
                                "billable_characters": billable_characters,
                                "stripe_usage_record_id": usage_record.id,
                                "error": None,
                            }
                        )

                    except stripe.error.StripeError as e:
                        error_msg = f"Stripe API error for subscription {subscription_id}: {str(e)}"
                        logger.error(error_msg)
                        results.append(
                            {
                                "subscription_id": subscription_id,
                                "status": "stripe_error",
                                "error": error_msg,
                            }
                        )
                    except Exception as e:
                        error_msg = f"Error reporting usage for subscription {subscription_id}: {str(e)}"
                        logger.error(error_msg)
                        results.append(
                            {
                                "subscription_id": subscription_id,
                                "status": "error",
                                "error": error_msg,
                            }
                        )

            logger.info(
                f"Completed periodic overage reporting for {len(subscriptions)} subscriptions"
            )
            return results

        except Exception as e:
            error_msg = f"Failed to run periodic overage reporting: {str(e)}"
            logger.error(error_msg)
            return [{"status": "global_error", "error": error_msg}]

    async def track_usage_event(
        self,
        request: Request,
        endpoint: str,
        http_method: str,
        status_code: int,
        character_count: int,
        processing_time_ms: Optional[int] = None,
    ) -> bool:
        """Track a detailed usage event for time-series analytics.

        Args:
            request: FastAPI Request object
            endpoint: API endpoint path
            http_method: HTTP method (GET, POST, etc.)
            status_code: HTTP status code
            character_count: Number of characters processed
            processing_time_ms: Request processing time in milliseconds

        Returns:
            bool: True if tracking successful, False otherwise
        """
        if not settings.enable_usage_tracking:
            return True

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

            # Collect user identification based on request type
            user_id = None
            subscription_id = None
            ip_address = None

            if request_type == "paid":
                # For paid tier
                user = getattr(request.state, "user", None)
                if user:
                    user_id = user.get("id")

                subscription = getattr(request.state, "subscription", None)
                if subscription:
                    subscription_id = subscription.get("id")

            elif request_type == "free":
                # For free tier
                user = getattr(request.state, "user", None)
                if user:
                    user_id = user.get("id")
                if not user_id:
                    user_id = request.headers.get("X-User-ID")

            else:
                # For demo tier
                ip_address = getattr(request.state, "ip_address", None)
                if not ip_address:
                    ip_address = request.client.host

            # Collect additional metadata
            metadata = {
                "headers": dict(request.headers),
                "query_params": dict(request.query_params),
                "path_params": getattr(request, "path_params", {}),
            }

            # Get user agent
            user_agent = request.headers.get("User-Agent")

            # Track the event
            return await self._supabase.track_usage_event(
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
                metadata=metadata,
            )

        except Exception as e:
            logger.error(f"Failed to track usage event: {e}")
            return False
