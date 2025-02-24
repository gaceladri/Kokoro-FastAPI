import asyncio
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

import stripe
from loguru import logger

from ...core.config import settings
from .supabase_client import SupabaseClient

stripe.api_key = settings.stripe_secret_key


class UsageTrackingService:
    """Service for tracking TTS usage."""

    def __init__(self):
        """Initialize usage tracking service."""
        self._supabase = SupabaseClient.get_instance()
        self._demo_cache = defaultdict(list)  # IP -> List[timestamp]
        self._last_cleanup = time.time()
        self._cache_lock = asyncio.Lock()

    @classmethod
    async def create(cls) -> "UsageTrackingService":
        """Create and initialize usage tracking service."""
        return cls()

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
            period_start = datetime.fromisoformat(subscription["current_period_start"])
            period_end = datetime.fromisoformat(subscription["current_period_end"])

            if now < period_start or now > period_end:
                return False, "Subscription period expired", None

            # Get current period usage
            usage = await self._supabase.get_period_usage(
                subscription["id"], period_start, period_end
            )

            # Check usage limits
            product = subscription["products"]
            current_requests = usage["total_requests"] if usage else 0
            current_characters = usage.get("total_characters", 0) if usage else 0

            # For premium plan, remove character limit check since it's pay-as-you-go
            if product["monthly_request_limit"] is not None:
                if current_requests >= product["monthly_request_limit"]:
                    return False, "Monthly request limit exceeded", None

            # Remove character limit check for premium plan
            # Optionally, enforce a very high limit if needed
            # if text_length is not None and product.get("monthly_character_limit") is not None:
            #     if current_characters + text_length > product["monthly_character_limit"]:
            #         return False, "Monthly character limit would be exceeded", None

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

            # Get metered_item_id from subscription
            subscription = await self._supabase.get_subscription(subscription_id)
            metered_item_id = subscription.get("metered_item_id")

            if metered_item_id:
                # Report to Stripe
                stripe.SubscriptionItem.create_usage_record(
                    metered_item_id,
                    quantity=character_count,
                    timestamp=int(datetime.utcnow().timestamp()),
                    action="increment",
                )

            return True
        except Exception as e:
            logger.error(f"Failed to track request: {e}")
            return False

    async def get_subscription_usage(
        self, subscription_id: str, period_start: datetime, period_end: datetime
    ) -> Optional[Dict]:
        """Get usage statistics for a subscription period."""
        if not settings.enable_usage_tracking:
            return {"total_requests": 0, "total_characters": 0}

        try:
            return await self._supabase.get_period_usage(
                subscription_id, period_start, period_end
            )
        except Exception as e:
            logger.error(f"Failed to get subscription usage: {e}")
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
                    start_time = (datetime.utcnow() - timedelta(hours=24)).isoformat()
                    result = (
                        self._supabase._client.table("demo_requests")
                        .select("id", count="exact")
                        .eq("ip_address", ip_address)
                        .gt("request_time", start_time)
                        .execute()
                    )
                    count = result.count or 0

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
        """Track a demo request by updating cache and asynchronously updating database."""
        now = time.time()

        try:
            # Update cache immediately
            async with self._cache_lock:
                self._demo_cache[ip_address].append(now)

            # Asynchronously update database without waiting
            if self._supabase._client:
                asyncio.create_task(self._async_track_demo_request(ip_address, character_count))

            return True
        except Exception as e:
            logger.error(f"Failed to track demo request for IP {ip_address}: {e}")
            return False

    async def _async_track_demo_request(self, ip_address: str, character_count: int):
        """Asynchronously update database with demo request."""
        try:
            data = await self._supabase._client.table("demo_requests").insert(
                {
                    "ip_address": ip_address,
                    "request_time": datetime.utcnow().isoformat(),
                    "character_count": character_count,
                }
            ).execute()
        except Exception as e:
            error_type = type(e).__name__
            error_details = str(e)
            logger.error(
                f"Failed to persist demo request:\n"
                f"IP: {ip_address}\n"
                f"Character Count: {character_count}\n"
                f"Error Type: {error_type}\n"
                f"Error Details: {error_details}\n"
                f"Timestamp: {datetime.utcnow().isoformat()}"
            )
            # Don't raise exception as this is background task

    async def get_demo_usage_stats(self, ip_address: str) -> Dict:
        """Get demo usage statistics for an IP address in the last 24 hours.

        Args:
            ip_address: IP address to get stats for

        Returns:
            Dict containing usage statistics
        """
        try:
            # Get request count
            request_count = await self.get_demo_request_count(ip_address)

            # Get character count from database
            if self._supabase._client:
                start_time = (datetime.utcnow() - timedelta(hours=24)).isoformat()
                result = (
                    self._supabase._client.table("demo_requests")
                    .select("character_count")
                    .eq("ip_address", ip_address)
                    .gt("request_time", start_time)
                    .execute()
                )
                
                total_characters = sum(row.get("character_count", 0) for row in result.data)
            else:
                total_characters = 0

            return {
                "total_requests": request_count,
                "total_characters": total_characters,
                "requests_remaining": settings.demo_daily_limit - request_count,
                "characters_remaining": settings.demo_max_characters,  # Per request limit
                "period_start": (datetime.utcnow() - timedelta(hours=24)).isoformat(),
                "period_end": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"Failed to get demo usage stats for IP {ip_address}: {e}")
            return {
                "total_requests": 0,
                "total_characters": 0,
                "requests_remaining": settings.demo_daily_limit,
                "characters_remaining": settings.demo_max_characters,
                "period_start": (datetime.utcnow() - timedelta(hours=24)).isoformat(),
                "period_end": datetime.utcnow().isoformat(),
            }
