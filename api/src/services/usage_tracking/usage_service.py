import asyncio
import re
import ssl
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

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


class ApiKeyCacheManager:
    """Manager for API key validation caching to reduce database load."""

    def __init__(self, refresh_interval: int = 300):
        """Initialize the API key cache manager.

        Args:
            refresh_interval: Interval in seconds for cache refresh (default 5 minutes)
        """
        self._api_keys = {}  # prefix -> key info
        self._free_users = {}  # user_id -> user info
        self._refresh_interval = refresh_interval
        self._supabase = None
        self._refresh_lock = asyncio.Lock()
        self._last_refresh = 0
        self._demo_user_cache = {}  # Cache for demo users - lasts 1 hour
        self._demo_user_cache_ttl = 3600
        self._initialized = False  # Flag to track initialization state

        # Performance metrics
        self._cache_hits = 0
        self._cache_misses = 0
        self._last_db_operation_time = 0
        self._db_operation_count = 0
        self._validation_cache = {}  # Additional validation result cache

        # For scheduled refresh
        self._refresh_task = None

    def get_metrics(self) -> Dict[str, Any]:
        """Get cache performance metrics."""
        now = time.time()
        time_window = now - self._last_metrics_reset

        return {
            "cache_size": len(self._api_keys),
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "hit_rate": (self._cache_hits / (self._cache_hits + self._cache_misses))
            if (self._cache_hits + self._cache_misses) > 0
            else 0,
            "time_since_last_reset": f"{time_window:.1f}s",
            "time_since_last_refresh": f"{(now - self._last_refresh):.1f}s",
            "last_refresh_success": self._last_refresh_success,
        }

    def reset_metrics(self):
        """Reset cache metrics."""
        self._cache_hits = 0
        self._cache_misses = 0
        self._last_metrics_reset = time.time()

    async def initialize(self):
        """Initialize the cache and start the refresh task."""
        if self._initialized:
            return

        self._supabase = await SupabaseClient.get_instance()

        # Perform initial cache refresh
        await self.refresh_cache()

        # Start the background refresh task
        self._refresh_task = asyncio.create_task(self._refresh_loop())
        self._initialized = True

    async def _refresh_loop(self):
        """Background task to periodically refresh the cache."""
        while True:
            try:
                await asyncio.sleep(self._refresh_interval)
                await self.refresh_cache()
            except asyncio.CancelledError:
                logger.info("API key cache refresh task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in API key cache refresh loop: {e}")
                await asyncio.sleep(5)  # Wait a bit before retrying

    async def refresh_cache(self):
        """Refresh the API key and user data cache from Supabase."""
        try:
            async with self._refresh_lock:
                if not self._supabase:
                    logger.warning(
                        "Cannot refresh API key cache: Supabase client not initialized"
                    )
                    self._last_refresh_success = False
                    return

                start_time = time.time()
                logger.debug("Refreshing API key cache...")

                # Load all active API keys in a single query
                query = (
                    self._supabase._client.table("api_keys")
                    .select("*, users(*)")
                    .eq("is_active", True)
                )

                response = await query.execute()

                if not response.data:
                    logger.warning("No active API keys found in database")
                    self._api_keys = {}  # Clear cache
                    return

                # Process API keys and build cache
                new_cache = {}
                subscription_cache = {}  # Temporary cache to avoid duplicate subscription queries
                self._active_user_ids = set()  # Reset the active user IDs set

                for key_data in response.data:
                    try:
                        key_prefix = key_data.get("key_prefix")
                        user_id = key_data.get("user_id")

                        if not key_prefix or not user_id:
                            continue

                        # Add to active user IDs for free tier tracking
                        self._active_user_ids.add(user_id)

                        # Get subscription (from cache if possible)
                        subscription = subscription_cache.get(user_id)
                        if not subscription:
                            subscription = await self._get_active_subscription(user_id)
                            if subscription:
                                subscription_cache[user_id] = subscription
                            else:
                                continue  # Skip keys without active subscription

                        # Get current period usage
                        period_start = parse_iso_datetime(
                            subscription["current_period_start"]
                        )
                        period_end = parse_iso_datetime(
                            subscription["current_period_end"]
                        )

                        usage = await self._get_subscription_usage(
                            subscription["id"], period_start, period_end
                        )

                        # Store in cache
                        new_cache[key_prefix] = {
                            "timestamp": time.time(),
                            "info": {
                                "user": key_data["users"],
                                "subscription": subscription,
                                "usage": usage
                                or {
                                    "total_requests": 0,
                                    "characters_used": 0,
                                    "last_request_at": None,
                                },
                            },
                        }

                    except Exception as e:
                        logger.error(
                            f"Error processing API key {key_data.get('key_prefix')}: {e}"
                        )

                # Replace the cache atomically
                self._api_keys = new_cache

                # Now load free users data
                await self._load_free_users()

                self._last_refresh = time.time()
                self._last_refresh_success = True
                refresh_time = time.time() - start_time
                logger.info(
                    f"API key cache refreshed in {refresh_time:.2f}s - {len(self._api_keys)} keys cached, {len(self._free_users)} free users cached"
                )
        except Exception as e:
            self._last_refresh_success = False
            logger.error(f"Failed to refresh API key cache: {e}")

    async def _load_api_keys(self):
        """Load all active API keys from Supabase."""
        try:
            if not self._supabase or not self._supabase._client:
                return

            # Query all active API keys with user info
            query = (
                self._supabase._client.table("api_keys")
                .select("*, users(*)")
                .eq("is_active", True)
            )

            response = await query.execute()

            if not response.data:
                logger.warning("No active API keys found in database")
                return

            # Process API keys
            new_keys = {}
            for key_data in response.data:
                try:
                    # Extract key prefix and user ID
                    key_prefix = key_data.get("key_prefix")
                    user_id = key_data.get("user_id")

                    if not key_prefix or not user_id:
                        continue

                    # For effective caching, we need a more unique identifier than just 8 chars
                    # Instead, use a combination of user_id and key_prefix to ensure uniqueness
                    # This is a temporary solution until the database schema is updated
                    cache_key = f"{key_prefix}_{user_id[:8]}"

                    # Track user ID as active
                    self._active_user_ids.add(user_id)

                    # Get user's active subscription
                    subscription = await self._get_active_subscription(user_id)
                    if not subscription:
                        # Skip keys without active subscription
                        continue

                    # Get current period usage
                    period_start = parse_iso_datetime(
                        subscription["current_period_start"]
                    )
                    period_end = parse_iso_datetime(subscription["current_period_end"])

                    usage = await self._get_subscription_usage(
                        subscription["id"], period_start, period_end
                    )

                    # Store key with all relevant data
                    new_keys[cache_key] = {
                        "timestamp": time.time(),
                        "info": {
                            "user": key_data["users"],
                            "subscription": subscription,
                            "usage": usage,
                        },
                    }

                except Exception as e:
                    logger.error(
                        f"Error processing API key {key_data.get('key_prefix')}: {e}"
                    )

            # Replace the cache with new data
            self._api_keys = new_keys

        except Exception as e:
            logger.error(f"Error loading API keys: {e}")

    async def _load_free_users(self):
        """Load usage data for active free tier users."""
        try:
            if not self._supabase or not self._supabase._client:
                return

            if not self._active_user_ids:
                return

            # Get current month boundaries
            now = datetime.utcnow()
            period_start = datetime(now.year, now.month, 1)
            if now.month < 12:
                period_end = datetime(now.year, now.month + 1, 1) - timedelta(seconds=1)
            else:
                period_end = datetime(now.year + 1, 1, 1) - timedelta(seconds=1)

            new_free_users = {}

            # Query free_usage table for active users
            for user_id in self._active_user_ids:
                try:
                    query = (
                        self._supabase._client.table("free_usage")
                        .select("*")
                        .eq("user_id", user_id)
                        .eq("period_start", period_start.isoformat())
                        .limit(1)
                    )

                    result = await query.execute()

                    usage = None
                    if result.data and len(result.data) > 0:
                        usage = result.data[0]
                    else:
                        # No usage record yet, create empty one
                        usage = {
                            "total_requests": 0,
                            "characters_used": 0,
                            "last_request_at": None,
                            "period_start": period_start.isoformat(),
                            "period_end": period_end.isoformat(),
                        }

                    new_free_users[user_id] = {
                        "timestamp": time.time(),
                        "usage": usage,
                        "period_start": period_start,
                        "period_end": period_end,
                    }

                except Exception as e:
                    logger.error(f"Error loading free user data for {user_id}: {e}")

            # Update the free users cache
            self._free_users = new_free_users

        except Exception as e:
            logger.error(f"Error loading free users data: {e}")

    async def _get_active_subscription(self, user_id: str) -> Optional[Dict]:
        """Get active subscription for a user directly from Supabase."""
        try:
            if not self._supabase or not self._supabase._client:
                return None

            # Query active subscription with product info
            query = (
                self._supabase._client.table("subscriptions")
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
            logger.error(f"Error getting active subscription for user {user_id}: {e}")
            return None

    async def _get_subscription_usage(
        self, subscription_id: str, period_start: datetime, period_end: datetime
    ) -> Dict:
        """Get usage statistics for a subscription period directly from Supabase."""
        try:
            if not self._supabase or not self._supabase._client:
                return {
                    "total_requests": 0,
                    "characters_used": 0,
                    "last_request_at": None,
                }

            # Query usage for period
            query = (
                self._supabase._client.table("usage")
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
            logger.error(f"Error getting usage for subscription {subscription_id}: {e}")
            return {
                "total_requests": 0,
                "characters_used": 0,
                "last_request_at": None,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "reported_to_stripe": False,
            }

    async def validate_api_key(
        self, api_key_prefix: str, text_length: Optional[int] = None
    ) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """Validate an API key using the cache, falling back to database if needed."""
        validation_start = time.time()

        # Check if we have a cached entry
        async with self._refresh_lock:
            cached_info = self._api_keys.get(api_key_prefix)
            if cached_info:
                self._cache_hits += 1
                cache_time = time.time() - validation_start
                logger.info(
                    f"API key validation from cache took {cache_time:.4f}s (Hit rate: {self.get_metrics()['hit_rate']:.1%})"
                )
                return self._validate_cached_key_info(cached_info, text_length)

            self._cache_misses += 1

        # Cache miss - fall back to database validation
        logger.warning(
            f"API key cache miss for {api_key_prefix}, falling back to database (Hit rate: {self.get_metrics()['hit_rate']:.1%})"
        )
        try:
            if not self._supabase:
                return False, "Database unavailable", None

            # Validate using database
            db_start = time.time()
            api_key_info = await self._supabase.validate_api_key(api_key_prefix)
            db_key_time = time.time() - db_start
            logger.info(f"API key database lookup took {db_key_time:.4f}s")

            if not api_key_info:
                return False, "Invalid API key", None

            # Get user's active subscription
            sub_start = time.time()
            user_id = api_key_info["user_id"]
            subscription = await self._get_active_subscription(user_id)
            sub_time = time.time() - sub_start
            logger.info(f"Subscription lookup took {sub_time:.4f}s")

            if not subscription:
                return False, "No active subscription found", None

            # Check if subscription is active
            now = datetime.utcnow()
            period_start = parse_iso_datetime(subscription["current_period_start"])
            period_end = parse_iso_datetime(subscription["current_period_end"])

            if now < period_start or now > period_end:
                return False, "Subscription period expired", None

            # Get current period usage
            usage_start = time.time()
            usage = await self._get_subscription_usage(
                subscription["id"], period_start, period_end
            )
            usage_time = time.time() - usage_start
            logger.info(f"Usage lookup took {usage_time:.4f}s")

            # Create request info
            request_info = {
                "user": api_key_info["users"],
                "subscription": subscription,
                "usage": usage
                or {
                    "total_requests": 0,
                    "characters_used": 0,
                    "last_request_at": None,
                },
            }

            # Cache the result
            async with self._refresh_lock:
                self._api_keys[api_key_prefix] = {
                    "timestamp": time.time(),
                    "info": request_info,
                }

            total_time = time.time() - validation_start
            logger.info(f"Total API key validation took {total_time:.4f}s")

            return True, None, request_info

        except Exception as e:
            logger.error(f"Error validating API key: {e}")
            return False, str(e), None

    async def validate_free_tier_request(
        self, user_id: str, text_length: Optional[int] = None
    ) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """Validate a request for a free tier user using cache when possible.

        Args:
            user_id: User ID for free tier validation
            text_length: Length of text to validate against limits (optional)

        Returns:
            Tuple of (is_valid, error_message, context)
        """
        try:
            if not self._initialized:
                await self.initialize()

            # Check cache
            async with self._refresh_lock:
                # Ensure _free_users is initialized
                if not hasattr(self, "_free_users"):
                    self._free_users = {}

                cached_info = self._free_users.get(user_id)

            if cached_info:
                # We have cached info for this user
                usage = cached_info["usage"]
                period_start = cached_info["period_start"]
                period_end = cached_info["period_end"]

                # Check usage limits
                if text_length is not None:
                    current_characters = usage.get("characters_used", 0)
                    free_tier_limit = settings.free_tier_character_limit

                    if current_characters + text_length > free_tier_limit:
                        return (
                            False,
                            f"Free tier character limit would be exceeded (limit: {free_tier_limit})",
                            None,
                        )

                # Return cached info
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

            # Cache miss - fall back to database validation
            logger.warning(
                f"Free tier cache miss for {user_id}, falling back to database"
            )

            # Get current month's boundaries
            now = datetime.utcnow()
            period_start = datetime(now.year, now.month, 1)
            if now.month < 12:
                period_end = datetime(now.year, now.month + 1, 1) - timedelta(seconds=1)
            else:
                period_end = datetime(now.year + 1, 1, 1) - timedelta(seconds=1)

            # Get free tier usage from database
            if not self._supabase:
                return False, "Database unavailable", None

            usage = await self._supabase.get_free_tier_usage(
                user_id, period_start, period_end
            )

            # Check usage limits
            if text_length is not None:
                current_characters = usage.get("characters_used", 0) if usage else 0
                free_tier_limit = settings.free_tier_character_limit

                if current_characters + text_length > free_tier_limit:
                    return (
                        False,
                        f"Free tier character limit would be exceeded (limit: {free_tier_limit})",
                        None,
                    )

            # Create context
            context = {
                "user_id": user_id,
                "usage": usage,
                "period_start": period_start,
                "period_end": period_end,
            }

            # Ensure _active_user_ids is initialized
            if not hasattr(self, "_active_user_ids"):
                self._active_user_ids = set()

            # Update cache for future requests
            async with self._refresh_lock:
                self._free_users[user_id] = {
                    "timestamp": time.time(),
                    "usage": usage,
                    "period_start": period_start,
                    "period_end": period_end,
                }
                self._active_user_ids.add(user_id)

            return True, None, context

        except Exception as e:
            logger.error(f"Failed to validate free tier request: {e}")
            return False, "Internal server error", None

    def get_status(self) -> Dict:
        """Get the current status of the cache manager.

        Returns:
            Dict with status information
        """
        return {
            "initialized": self._initialized,
            "api_keys_cached": len(self._api_keys),
            "free_users_cached": len(self._free_users),
            "active_users_tracked": len(self._active_user_ids),
            "last_refresh": self._last_refresh,
            "last_refresh_success": self._last_refresh_success,
            "refresh_interval": self._refresh_interval,
        }

    async def shutdown(self):
        """Clean up resources when shutting down."""
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass

        logger.info("API key cache manager shut down")

    def _validate_cached_key_info(
        self, cached_info: Dict, text_length: Optional[int] = None
    ) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """Validate cached key information against request limitations.

        Args:
            cached_info: The cached key information
            text_length: Length of text to validate against usage limits (optional)

        Returns:
            Tuple of (is_valid, error_message, request_info)
        """
        request_info = cached_info["info"]

        # Check if subscription is active
        subscription = request_info["subscription"]
        now = datetime.utcnow()

        # Ensure subscription has required fields
        if (
            "current_period_start" not in subscription
            or "current_period_end" not in subscription
        ):
            logger.warning("Cached subscription missing period start/end fields")
            return False, "Invalid subscription data", None

        period_start = parse_iso_datetime(subscription["current_period_start"])
        period_end = parse_iso_datetime(subscription["current_period_end"])

        if now < period_start or now > period_end:
            return False, "Subscription period expired", None

        # Check usage limits if text length is provided
        if text_length is not None:
            usage = request_info["usage"]

            # Ensure products exists in subscription
            if "products" not in subscription:
                logger.warning("Cached subscription missing products field")
                return False, "Invalid subscription data", None

            product = subscription["products"]
            current_requests = usage["total_requests"] if usage else 0
            current_characters = usage.get("characters_used", 0) if usage else 0

            # Check request limit for all plans
            if product.get("monthly_request_limit") is not None:
                if current_requests >= product["monthly_request_limit"]:
                    return False, "Monthly request limit exceeded", None

            # For premium plan with metered billing, don't enforce a strict character limit
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

        # Validation passed
        return True, None, request_info


class UsageTrackingService:
    """Service for tracking TTS usage."""

    # Class-level variables for caching and shared state
    _key_cache_manager = None
    _key_cache_init_lock = asyncio.Lock()
    _demo_user_id = None  # Cached demo user ID at class level
    _demo_user_lock = asyncio.Lock()
    _event_queue = asyncio.Queue(maxsize=1000)
    _validation_cache = {}  # Cache for validation results
    _is_queue_processor_running = False
    _queue_processor_lock = asyncio.Lock()

    def __init__(self):
        """Initialize usage tracking service."""
        self._supabase = None  # Will be initialized in create()
        self._demo_cache = defaultdict(list)  # IP -> List[timestamp]
        self._last_cleanup = time.time()
        self._cache_lock = asyncio.Lock()
        self._stripe_reporting_lock = asyncio.Lock()  # Add lock for Stripe reporting
        self._last_tracking_error_time = 0
        self._tracking_error_count = 0
        self._last_batch_flush = time.time()
        self._ssl_context = None  # Will be initialized in create()

    @classmethod
    async def create(cls) -> "UsageTrackingService":
        """Create and initialize usage tracking service."""
        service = cls()

        # Initialize Supabase client with proper SSL context
        service._ssl_context = ssl.create_default_context()
        service._ssl_context.check_hostname = True
        service._ssl_context.verify_mode = ssl.CERT_REQUIRED

        # Initialize Supabase client
        service._supabase = await SupabaseClient.get_instance()

        # Initialize the demo user if usage tracking is enabled
        if settings.enable_usage_tracking:
            # Start the background event processor
            asyncio.create_task(service._start_event_processor())

            # Asynchronously initialize demo user without blocking
            asyncio.create_task(service._ensure_demo_user_exists())

        # Initialize API key cache manager with longer refresh interval
        await cls._get_key_cache_manager()

        return service

    @classmethod
    async def _get_key_cache_manager(cls) -> ApiKeyCacheManager:
        """Get or create the API key cache manager with extended cache TTL."""
        async with cls._key_cache_init_lock:
            if cls._key_cache_manager is None:
                # Create and initialize the cache manager with longer refresh interval
                cls._key_cache_manager = ApiKeyCacheManager(
                    refresh_interval=300  # Refresh every 5 minutes instead of 30 seconds
                )
                await cls._key_cache_manager.initialize()

        return cls._key_cache_manager

    async def _start_event_processor(self):
        """Start the background event processor if not already running."""
        async with self._queue_processor_lock:
            if self.__class__._is_queue_processor_running:
                return

            self.__class__._is_queue_processor_running = True

        try:
            # Process events in batches
            while True:
                try:
                    # Wait for events to accumulate or timeout
                    await asyncio.sleep(1.0)  # Check queue every second

                    # Process events if we have enough or it's been too long
                    current_time = time.time()
                    queue_size = self.__class__._event_queue.qsize()

                    if queue_size >= 10 or (
                        queue_size > 0 and current_time - self._last_batch_flush > 5
                    ):
                        # Get events from queue (up to 50)
                        events = []
                        for _ in range(min(50, queue_size)):
                            try:
                                events.append(self.__class__._event_queue.get_nowait())
                            except asyncio.QueueEmpty:
                                break

                        if events:
                            # Process the batch
                            try:
                                await self.track_usage_events_batch(events)
                            except Exception as e:
                                logger.error(f"Failed to process event batch: {e}")
                            finally:
                                # Mark tasks as done regardless of success
                                for _ in range(len(events)):
                                    self.__class__._event_queue.task_done()

                        self._last_batch_flush = current_time

                except Exception as e:
                    logger.error(f"Error in event processor: {e}")
                    await asyncio.sleep(5)  # Backoff on errors

        except asyncio.CancelledError:
            logger.info("Event processor task cancelled")
        finally:
            async with self._queue_processor_lock:
                self.__class__._is_queue_processor_running = False

    async def _ensure_demo_user_exists(self) -> str:
        """Ensure a demo user exists for tracking demo usage with class-level caching."""
        # First check if we already have the ID cached at class level
        if self.__class__._demo_user_id:
            return self.__class__._demo_user_id

        # Use a lock to prevent multiple simultaneous DB operations
        async with self.__class__._demo_user_lock:
            # Double-check after acquiring lock
            if self.__class__._demo_user_id:
                return self.__class__._demo_user_id

            # Check if the demo user already exists
            try:
                query = (
                    self._supabase._client.table("users")
                    .select("id")
                    .eq("email", "demo@kokoro.ai")
                )

                result = await query.execute()

                if result.data and len(result.data) > 0:
                    self.__class__._demo_user_id = result.data[0]["id"]
                    logger.info(
                        f"Using existing demo user with ID: {self.__class__._demo_user_id}"
                    )
                    return self.__class__._demo_user_id

                # Create the demo user if it doesn't exist
                demo_user = {"email": "demo@kokoro.ai", "full_name": "Demo User"}

                insert_query = self._supabase._client.table("users").insert(demo_user)
                result = await insert_query.execute()

                if result.data and len(result.data) > 0:
                    self.__class__._demo_user_id = result.data[0]["id"]
                    logger.info(
                        f"Created demo user with ID: {self.__class__._demo_user_id}"
                    )
                    return self.__class__._demo_user_id
                else:
                    logger.error("Failed to create demo user")
                    return None

            except Exception as e:
                logger.error(f"Error ensuring demo user exists: {e}")
                return None

    async def _get_demo_user_id(self) -> str:
        """Get the demo user ID with efficient caching."""
        # Use the class-level cached value if available
        if self.__class__._demo_user_id:
            return self.__class__._demo_user_id

        # Try to ensure it exists (this will update the class-level cache)
        return await self._ensure_demo_user_exists()

    async def validate_request(
        self,
        api_key_prefix: str,
        text_length: Optional[int] = None,
    ) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """Validate a request using API key with caching."""
        if not settings.enable_usage_tracking:
            return True, None, None

        try:
            # Check cache first for this exact validation request
            cache_key = f"{api_key_prefix}:{text_length or 0}"

            # Return cached result if available and not expired
            current_time = time.time()
            if cache_key in self.__class__._validation_cache:
                result, expiry = self.__class__._validation_cache[cache_key]
                if current_time < expiry:
                    return result

            # Use the API key cache manager for validation
            key_cache = await self._get_key_cache_manager()
            result = await key_cache.validate_api_key(api_key_prefix, text_length)

            # Cache the result for 5 minutes
            self.__class__._validation_cache[cache_key] = (result, current_time + 300)

            return result
        except Exception as e:
            logger.error(f"Error validating request: {e}")
            # Cache the error result for a shorter time (30 seconds)
            self.__class__._validation_cache[cache_key] = (
                (True, None, None),
                current_time + 30,
            )
            return True, None, None  # Allow the request on validation errors

    async def track_usage_event(
        self,
        request: Request,
        endpoint: str,
        http_method: str,
        status_code: int,
        character_count: int,
        processing_time_ms: Optional[int] = None,
        voice_id: Optional[str] = None,
        audio_duration_ms: Optional[int] = None,
        word_count: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Track an API usage event.

        Args:
            request: Request object
            endpoint: API endpoint
            http_method: HTTP method
            status_code: Response status code
            character_count: Number of characters in the request
            processing_time_ms: Processing time in milliseconds
            voice_id: Voice ID used
            audio_duration_ms: Audio duration in milliseconds
            word_count: Number of words in the request
            metadata: Additional metadata

        Returns:
            bool: True if successfully tracked, False otherwise
        """
        if not self._should_track():
            return True

        try:
            # Create event data
            event_data = {
                "request": request,
                "endpoint": endpoint,
                "http_method": http_method,
                "status_code": status_code,
                "character_count": character_count,
            }

            # Add optional fields
            if processing_time_ms is not None:
                event_data["processing_time_ms"] = processing_time_ms
            if voice_id:
                event_data["voice_id"] = voice_id
            if audio_duration_ms is not None:
                event_data["audio_duration_ms"] = audio_duration_ms
            if word_count is not None:
                event_data["word_count"] = word_count
            if metadata:
                event_data["metadata"] = metadata

            # Prepare data for tracking
            prepared_data = self._prepare_event_data(event_data)
            if not prepared_data:
                logger.warning("Invalid event data, skipping tracking")
                return False

            # Get the request type
            request_type = prepared_data.get("request_type")

            # For demo requests, also track in the free_usage table
            if request_type == "demo" and "ip_address" in prepared_data:
                ip_address = prepared_data["ip_address"]
                # Track the demo request in free_usage table
                await self.track_demo_request(ip_address, character_count)

            # Add event to queue for async processing
            try:
                self._event_queue.put_nowait(prepared_data)

                # Start event processor if not already running
                await self._start_event_processor()

                # Flush queue periodically
                now = time.time()
                if now - self._last_batch_flush > 30:  # Flush every 30 seconds
                    self._last_batch_flush = now

                    # Process pending events in batch
                    await self._process_event_queue(force=True)

                self._record_tracking_success()
                return True
            except asyncio.QueueFull:
                logger.warning("Event queue is full, dropping event")
                self._record_tracking_error()
                return False

        except Exception as e:
            logger.error(f"Failed to track usage event: {e}")
            self._record_tracking_error()
            return False

    async def _validate_api_key(
        self, api_key: str, skip_limit_check: bool = False
    ) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """Validate API key for compatibility with middleware.

        This is a wrapper around validate_request to provide compatibility
        with code that expects the middleware's _validate_api_key method.

        Args:
            api_key: Full API key
            skip_limit_check: If True, skip validation against character limits

        Returns:
            Tuple containing validity, error message (if any), and info (if valid)
        """
        # Extract a longer prefix from the API key - use 16 chars instead of 8
        # This makes it much more likely to be unique, even with common prefixes
        api_key_prefix = api_key[:16] if api_key else ""

        if not api_key_prefix:
            return False, "Invalid API key format", None

        # Delegate to the validate_request method
        # If skip_limit_check is True, we pass None for text_length to avoid limit checks
        return await self.validate_request(
            api_key_prefix, text_length=None if skip_limit_check else None
        )

    async def validate_free_tier_request(
        self,
        user_id: str,
        text_length: Optional[int] = None,
    ) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """Validate a request for a free tier user."""
        if not settings.enable_usage_tracking:
            return True, None, None

        try:
            # Use the API key cache manager for validation
            key_cache = await self._get_key_cache_manager()
            return await key_cache.validate_free_tier_request(user_id, text_length)
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

    def _should_track(self) -> bool:
        """Determine if we should attempt tracking based on recent failures.

        Implements backoff if there are repeated failures to prevent excessive logging
        and improve performance when the tracking system is down.
        """
        if not settings.enable_usage_tracking or not self._supabase:
            return False

        # If no recent errors, always track
        if self._tracking_error_count == 0:
            return True

        # Implement exponential backoff
        current_time = time.time()
        backoff_time = min(
            30, 2 ** (self._tracking_error_count - 1)
        )  # Max 30 seconds backoff

        if current_time - self._last_tracking_error_time > backoff_time:
            # It's been long enough since the last error, try again
            return True

        return False

    def _record_tracking_error(self):
        """Record a tracking error for backoff calculation."""
        self._tracking_error_count = min(
            10, self._tracking_error_count + 1
        )  # Cap at 10 to avoid overflow
        self._last_tracking_error_time = time.time()

    def _record_tracking_success(self):
        """Record a successful tracking operation."""
        if self._tracking_error_count > 0:
            self._tracking_error_count = 0

    async def track_usage_events_batch(self, events: List[Dict[str, Any]]) -> bool:
        """Track multiple usage events in a batch operation.

        Args:
            events: List of event data dictionaries

        Returns:
            bool: True if at least some events were tracked successfully
        """
        if not self._should_track():
            return False

        if not events:
            return True  # No events to track is a success

        try:
            # Extract common fields from events for batch processing
            batch_events = []

            for event_data in events:
                # Create a clean copy of the event data
                processed_event = self._prepare_event_data(event_data)
                if processed_event:
                    batch_events.append(processed_event)

            if not batch_events:
                return True  # No valid events after processing

            # Call the batch tracking method if the Supabase client supports it
            if hasattr(self._supabase, "track_usage_events_batch"):
                success = await self._supabase.track_usage_events_batch(batch_events)
            else:
                # Fall back to individual tracking if batch not supported
                results = await asyncio.gather(
                    *[
                        self._supabase.track_usage_event(**event)
                        for event in batch_events
                    ],
                    return_exceptions=True,
                )
                # If at least 50% were successful, consider it a success
                success_count = sum(1 for r in results if r is True)
                success = success_count >= len(batch_events) / 2

            # Update tracking status based on result
            if success:
                self._record_tracking_success()
                return True
            else:
                self._record_tracking_error()
                return False

        except Exception as e:
            logger.error(f"Failed to track usage events batch: {e}")
            self._record_tracking_error()
            return False

    def _prepare_event_data(
        self, event_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Prepare event data for tracking, extracting needed info from the request.

        Args:
            event_data: Dictionary containing event data with a request object

        Returns:
            Optional[Dict]: Prepared event data for tracking or None if invalid
        """
        if not event_data:
            return None

        # Create a copy to avoid modifying the original
        prepared_data = {}

        # Extract request object
        request = event_data.get("request")
        if not request:
            return None

        # Copy simple fields directly
        for key, value in event_data.items():
            if key != "request" and value is not None:
                prepared_data[key] = value

        # Get request type from request state
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

        prepared_data["request_type"] = request_type

        # Collect user identification based on request type
        if request_type == "paid":
            # Extract user and subscription from request state
            user_info = getattr(request.state, "user", None)
            subscription = getattr(request.state, "subscription", None)

            if user_info:
                prepared_data["user_id"] = user_info.get("id")

            if subscription:
                prepared_data["subscription_id"] = subscription.get("id")
        elif request_type == "free":
            # Extract user ID for free tier users
            user_info = getattr(request.state, "user", None)
            if user_info:
                prepared_data["user_id"] = user_info.get("id")
            else:
                prepared_data["user_id"] = request.headers.get("X-User-ID")

        # Always capture IP address for all request types
        ip_address = getattr(request.state, "ip_address", None)
        if not ip_address:
            # Use X-Forwarded-For header if available
            forwarded_for = request.headers.get("X-Forwarded-For")
            if forwarded_for:
                ip_address = forwarded_for.split(",")[0].strip()
            else:
                # Fallback to direct client address
                ip_address = request.client.host

        prepared_data["ip_address"] = ip_address

        # Collect additional request metadata
        prepared_data["user_agent"] = request.headers.get("User-Agent")
        prepared_data["referrer"] = request.headers.get(
            "Referer"
        )  # Note the HTTP header spelling

        # Build additional metadata for analytics
        metadata = {}

        # Add request headers that might be useful for analytics
        tracking_headers = ["Accept", "Accept-Language", "Origin"]
        for header in tracking_headers:
            if header in request.headers:
                metadata[header.lower()] = request.headers[header]

        # Add any custom headers for tracking (X-* headers except sensitive ones)
        for header, value in request.headers.items():
            if header.startswith("X-") and header not in ["X-API-Key", "X-User-ID"]:
                metadata[header.lower()] = value

        if metadata:
            prepared_data["metadata"] = metadata

        return prepared_data

    # Add a method to check the health of the tracking service
    async def check_health(self) -> Dict[str, Any]:
        """Check the health of the usage tracking service.

        Returns:
            Dict with health status information
        """
        health_data = {
            "enabled": settings.enable_usage_tracking,
            "tracking_client_available": self._supabase is not None,
            "error_count": self._tracking_error_count,
            "healthy": self._tracking_error_count == 0,
        }

        # Check database connection if client available
        if self._supabase and hasattr(self._supabase, "check_health"):
            health_data["database_connection"] = await self._supabase.check_health()

        return health_data

    async def get_voice_usage_statistics(self, user_id: str) -> Optional[Dict]:
        """Get voice usage statistics for a specific user.

        Args:
            user_id: User ID to get statistics for

        Returns:
            Dictionary containing voice usage statistics or None if error
        """
        if not self._supabase:
            logger.warning("Supabase client not initialized")
            return None

        try:
            # Calculate the time range for the current month
            now = datetime.utcnow()
            period_start = datetime(now.year, now.month, 1)
            if now.month < 12:
                period_end = datetime(now.year, now.month + 1, 1) - timedelta(seconds=1)
            else:
                period_end = datetime(now.year + 1, 1, 1) - timedelta(seconds=1)

            # Convert to ISO format for the database query
            period_start_iso = period_start.isoformat()
            period_end_iso = period_end.isoformat()

            # Execute an RPC call to the database function (more efficient)
            # This function aggregates voice usage from the time-series data
            result = await self._supabase.rpc(
                "get_voice_usage_statistics",
                {
                    "p_user_id": user_id,
                    "p_period_start": period_start_iso,
                    "p_period_end": period_end_iso,
                },
            ).execute()

            if result.data:
                return result.data

            # Fallback to direct query if RPC fails
            # Query the usage_events table to get voice usage statistics
            query = (
                self._supabase.table("usage_events")
                .select(
                    "voice_id",
                    "count(*) as request_count",
                    "sum(character_count) as total_characters",
                    "sum(audio_duration_ms) as total_audio_ms",
                    "avg(processing_time_ms) as avg_processing_time_ms",
                )
                .eq("user_id", user_id)
                .gte("timestamp", period_start_iso)
                .lte("timestamp", period_end_iso)
                .not_is("voice_id", "null")
                .group_by("voice_id")
                .order("total_characters", desc=True)
            )

            result = await query.execute()

            if not result.data:
                return {}

            # Format the result as a dictionary with voice_id as the key
            voice_stats = {}
            for item in result.data:
                voice_id = item.pop("voice_id")
                voice_stats[voice_id] = item

            return voice_stats

        except Exception as e:
            logger.error(f"Error getting voice usage statistics: {e}")
            return None

    async def get_performance_statistics(self, user_id: str) -> Optional[Dict]:
        """Get API performance statistics for a specific user.

        Args:
            user_id: User ID to get statistics for

        Returns:
            Dictionary containing performance statistics or None if error
        """
        if not self._supabase:
            logger.warning("Supabase client not initialized")
            return None

        try:
            # Calculate the time range for the current month
            now = datetime.utcnow()
            period_start = datetime(now.year, now.month, 1)
            if now.month < 12:
                period_end = datetime(now.year, now.month + 1, 1) - timedelta(seconds=1)
            else:
                period_end = datetime(now.year + 1, 1, 1) - timedelta(seconds=1)

            # Convert to ISO format for the database query
            period_start_iso = period_start.isoformat()
            period_end_iso = period_end.isoformat()

            # Execute an RPC call to the database function (more efficient)
            result = await self._supabase.rpc(
                "get_performance_statistics",
                {
                    "p_user_id": user_id,
                    "p_period_start": period_start_iso,
                    "p_period_end": period_end_iso,
                },
            ).execute()

            if result.data:
                return result.data

            # Fallback to direct query if RPC fails
            # Query the usage_events table to get performance statistics
            query = (
                self._supabase.table("usage_events")
                .select(
                    "endpoint",
                    "count(*) as request_count",
                    "avg(processing_time_ms) as avg_processing_time_ms",
                    "min(processing_time_ms) as min_processing_time_ms",
                    "max(processing_time_ms) as max_processing_time_ms",
                    "count(case when status_code >= 200 and status_code < 300 then 1 end) as successful_requests",
                    "count(case when status_code >= 400 then 1 end) as error_requests",
                )
                .eq("user_id", user_id)
                .gte("timestamp", period_start_iso)
                .lte("timestamp", period_end_iso)
                .group_by("endpoint")
                .order("request_count", desc=True)
            )

            result = await query.execute()

            if not result.data:
                return {
                    "endpoints": {},
                    "summary": {
                        "total_requests": 0,
                        "avg_processing_time_ms": 0,
                        "success_rate": 100,  # Default to 100% if no data
                    },
                }

            # Format the result as a nested dictionary
            endpoints = {}
            total_requests = 0
            total_successful = 0
            total_processing_time = 0

            for item in result.data:
                endpoint = item.pop("endpoint")
                endpoints[endpoint] = item
                total_requests += item["request_count"]
                total_successful += item["successful_requests"]
                total_processing_time += (
                    item["avg_processing_time_ms"] * item["request_count"]
                )

            # Calculate summary statistics
            avg_processing_time = 0
            if total_requests > 0:
                avg_processing_time = total_processing_time / total_requests

            success_rate = 100
            if total_requests > 0:
                success_rate = (total_successful / total_requests) * 100

            return {
                "endpoints": endpoints,
                "summary": {
                    "total_requests": total_requests,
                    "avg_processing_time_ms": round(avg_processing_time, 2),
                    "success_rate": round(success_rate, 2),
                },
            }

        except Exception as e:
            logger.error(f"Error getting performance statistics: {e}")
            return None

    async def _process_event_queue(self, force=False):
        """Process events in the queue.
        
        Args:
            force: Whether to force processing even if there are few events
            
        Returns:
            bool: True if events were processed, False otherwise
        """
        queue_size = self.__class__._event_queue.qsize()
        
        # Only process if we have enough events or force is True
        if queue_size == 0 or (queue_size < 10 and not force):
            return False
            
        try:
            # Get events from queue (up to 50)
            events = []
            for _ in range(min(50, queue_size)):
                try:
                    events.append(self.__class__._event_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
                    
            if not events:
                return False
                
            # Process the batch
            success = await self.track_usage_events_batch(events)
            
            # Mark tasks as done
            for _ in range(len(events)):
                self.__class__._event_queue.task_done()
                
            return success
        except Exception as e:
            logger.error(f"Failed to process event queue: {e}")
            return False
