from datetime import datetime
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
