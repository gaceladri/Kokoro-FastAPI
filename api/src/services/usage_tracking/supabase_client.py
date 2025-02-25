"""Supabase client for usage tracking."""

from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

from loguru import logger
from supabase import Client, create_client

from ...core.config import settings


class SupabaseClient:
    """Supabase client singleton for usage tracking."""

    _instance: Optional["SupabaseClient"] = None
    _client: Optional[Client] = None

    def __init__(self):
        """Initialize Supabase client."""
        if not settings.supabase_url or not settings.supabase_key:
            logger.warning("Supabase credentials not configured")
            return

        try:
            self._client = create_client(settings.supabase_url, settings.supabase_key)
            logger.info("Supabase client initialized")
        except Exception as e:
            logger.error(f"Failed to initialize Supabase client: {e}")

    @classmethod
    def get_instance(cls) -> "SupabaseClient":
        """Get Supabase client instance."""
        if cls._instance is None:
            cls._instance = cls()
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
            api_key = (
                self._client.table("api_keys")
                .select("*, users(*)")
                .eq("key_prefix", api_key_prefix)
                .eq("is_active", True)
                .single()
                .execute()
            )

            if not api_key.data:
                logger.warning(f"Invalid API key prefix: {api_key_prefix}")
                return None

            # Update last_used_at
            self._client.table("api_keys").update(
                {"last_used_at": datetime.utcnow().isoformat()}
            ).eq("id", api_key.data["id"]).execute()

            return api_key.data

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
            subscription = (
                self._client.table("subscriptions")
                .select("*, products(*)")
                .eq("user_id", user_id)
                .eq("status", "active")
                .single()
                .execute()
            )

            return subscription.data

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
            subscription = (
                self._client.table("subscriptions")
                .select("*, products(*)")
                .eq("id", subscription_id)
                .single()
                .execute()
            )

            return subscription.data

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
            usage = (
                self._client.table("usage")
                .select("*")
                .eq("subscription_id", subscription_id)
                .eq("period_start", period_start.isoformat())
                .single()
                .execute()
            )

            now = datetime.utcnow()

            if usage.data:
                # Update existing record
                self._client.table("usage").update(
                    {
                        "total_requests": usage.data["total_requests"] + request_count,
                        "total_characters": usage.data.get("total_characters", 0)
                        + character_count,
                        "last_request_at": now.isoformat(),
                    }
                ).eq("id", usage.data["id"]).execute()
            else:
                # Create new record
                self._client.table("usage").insert(
                    {
                        "subscription_id": subscription_id,
                        "period_start": period_start.isoformat(),
                        "period_end": period_end.isoformat(),
                        "total_requests": request_count,
                        "total_characters": character_count,
                        "last_request_at": now.isoformat(),
                    }
                ).execute()

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
            usage = (
                self._client.table("usage")
                .select("*")
                .eq("subscription_id", subscription_id)
                .eq("period_start", period_start.isoformat())
                .single()
                .execute()
            )

            if not usage.data:
                return {
                    "total_requests": 0,
                    "last_request_at": None,
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                }

            return usage.data

        except Exception as e:
            logger.error(f"Failed to get usage: {e}")
            return None

    async def get_free_tier_usage(
        self, user_id: str, period_start: datetime, period_end: Optional[datetime] = None
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
            # Build query
            query = (
                self._client.table("free_usage")
                .select("*")
                .eq("user_id", user_id)
                .eq("period_start", period_start.isoformat())
            )
            
            # Add period_end condition if provided
            if period_end:
                query = query.eq("period_end", period_end.isoformat())
                
            # Execute query
            usage = query.single().execute()

            if not usage.data:
                # If period_end is not provided, calculate it based on period_start
                if not period_end:
                    if period_start.month < 12:
                        period_end = datetime(period_start.year, period_start.month + 1, 1) - timedelta(seconds=1)
                    else:
                        period_end = datetime(period_start.year + 1, 1, 1) - timedelta(seconds=1)
                
                return {
                    "total_requests": 0,
                    "last_request_at": None,
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat() if period_end else None,
                }

            return usage.data

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
            
            # Try to get existing usage record
            usage = (
                self._client.table("free_usage")
                .select("*")
                .eq("user_id", user_id)
                .eq("period_start", period_start.isoformat())
                .single()
                .execute()
            )

            now = datetime.utcnow()

            if usage.data:
                # Update existing record
                self._client.table("free_usage").update(
                    {
                        "total_requests": usage.data["total_requests"] + character_count,
                        "last_request_at": now.isoformat(),
                    }
                ).eq("id", usage.data["id"]).execute()
            else:
                # For demo users, we need to create a user record first if it doesn't exist
                if is_demo_user and not await self._ensure_demo_user_exists(user_id):
                    logger.warning(f"Failed to create demo user record for {user_id}")
                    return False
                
                # Create new record
                self._client.table("free_usage").insert(
                    {
                        "user_id": user_id,
                        "period_start": period_start.isoformat(),
                        "period_end": period_end.isoformat(),
                        "total_requests": character_count,
                        "last_request_at": now.isoformat(),
                    }
                ).execute()

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
            user = (
                self._client.table("users")
                .select("id")
                .eq("id", demo_user_id)
                .single()
                .execute()
            )
            
            if user.data:
                return True
                
            # Create demo user record
            ip_address = demo_user_id.split(":", 1)[1]
            self._client.table("users").insert(
                {
                    "id": demo_user_id,
                    "email": f"demo_{ip_address}@example.com",  # Placeholder email
                    "full_name": f"Demo User ({ip_address})",
                }
            ).execute()
            
            return True
        except Exception as e:
            logger.error(f"Failed to ensure demo user exists: {e}")
            return False
