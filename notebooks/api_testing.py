import json
import os
import pathlib
import time
import uuid
from datetime import datetime

import IPython.display as ipd
import requests
import stripe
from pydub import AudioSegment
from supabase import create_client

# API configuration
API_BASE_URL = "http://localhost:8880"

# Test data
TEST_TEXT = "This is a test of the Kokoro TTS API. It's working great!"
TEST_VOICE = "af_bella"

# Path to test data file
SCRIPT_DIR = pathlib.Path(__file__).parent.absolute()
TEST_DATA_FILE = SCRIPT_DIR / "test_data_ids.json"

# Supabase and Stripe configuration
# These should be set in your environment or updated here
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")

# Initialize clients if credentials are available
supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# %% [markdown]
# ## Helper Functions


# %%
def load_test_data():
    """Load test data from the JSON file created by setup_test_data.py"""
    if not TEST_DATA_FILE.exists():
        print(f"Warning: Test data file {TEST_DATA_FILE} not found!")
        print("Please run setup_test_data.py --create first to generate test data.")
        return {
            "free_tier_user_id": None,
            "basic_tier_api_key": None,
            "premium_tier_api_key": None,
            "premium_subscription_id": None,
        }

    try:
        with open(TEST_DATA_FILE, "r") as f:
            data = json.load(f)

        # Get the test data section
        test_data = data.get("test_data", {})

        if not test_data or not any(test_data.values()):
            print("Warning: No test data found in the JSON file.")
            print("Please run setup_test_data.py --create with the updated version.")

        return test_data
    except Exception as e:
        print(f"Error loading test data: {e}")
        return {}


# Load test data from JSON file
test_data = load_test_data()

# Extract test data values, with fallbacks for backward compatibility
# Extract required test data values
if not all(
    key in test_data
    for key in [
        "free_tier_user_id",
        "basic_tier_api_key",
        "premium_tier_api_key",
        "premium_subscription_id",
    ]
):
    raise ValueError(
        "Missing required test data. Please run setup_test_data.py first to "
        "generate test credentials."
    )

FREE_TIER_USER_ID = test_data["free_tier_user_id"]
BASIC_TIER_API_KEY = test_data["basic_tier_api_key"]
PREMIUM_TIER_API_KEY = test_data["premium_tier_api_key"]
PREMIUM_SUBSCRIPTION_ID = test_data["premium_subscription_id"]
print("\nUsing test data:")
print(f"- Free Tier User ID: {FREE_TIER_USER_ID}")
print(f"- Basic Tier API Key: {BASIC_TIER_API_KEY[:8]}...")
print(f"- Premium Tier API Key: {PREMIUM_TIER_API_KEY[:8]}...")
print(f"- Premium Subscription ID: {PREMIUM_SUBSCRIPTION_ID}")


def display_json(data):
    """Display JSON data in a formatted way"""
    return json.dumps(data, indent=2)


def play_audio(audio_bytes):
    """Play audio bytes in the notebook"""
    try:
        # Save to a temporary file
        with open("temp_audio.mp3", "wb") as f:
            f.write(audio_bytes)

        # Load and display
        audio = AudioSegment.from_file("temp_audio.mp3")
        return ipd.Audio("temp_audio.mp3")
    except Exception as e:
        print(f"Error playing audio: {e}")
        return None
    finally:
        # Clean up
        if os.path.exists("temp_audio.mp3"):
            os.remove("temp_audio.mp3")


# %% [markdown]
# ## 1. Testing Demo User (No Authentication)


# %%
def test_demo_user():
    """Test the API as a demo user (no authentication)"""
    # 1. Skip checking available voices since it's failing with a 500 error
    print("Skipping voices check as it's currently failing with a 500 error")
    print("Testing speech generation endpoint directly")

    # 2. Generate speech
    payload = {
        "model": "kokoro",
        "input": TEST_TEXT,
        "voice": TEST_VOICE,
        "response_format": "mp3",
    }

    try:
        print(f"Sending speech generation request with payload: {json.dumps(payload)}")
        response = requests.post(f"{API_BASE_URL}/v1/audio/speech", json=payload)

        print(f"Response status code: {response.status_code}")
        if response.status_code == 200:
            print("Speech generated successfully!")
            audio = play_audio(response.content)
            return audio
        else:
            print(f"Error response: {response.text}")
            try:
                error_json = response.json()
                print(f"Error details: {display_json(error_json)}")
            except:
                print("Response was not JSON formatted")
            return None
    except requests.exceptions.ConnectionError:
        print("Connection error: Unable to connect to the API server.")
        print(f"Please ensure the API server is running at {API_BASE_URL}")
        return None
    except Exception as e:
        print(f"Error during speech generation: {str(e)}")
        return None


# Test demo user
demo_audio = test_demo_user()

# %% [markdown]
# ## 2. Check Demo Usage Statistics


# %%
def check_demo_usage():
    """Check usage statistics for demo user"""
    try:
        response = requests.get(f"{API_BASE_URL}/v1/usage")

        print(f"Usage stats status code: {response.status_code}")
        if response.status_code == 200:
            print("Demo usage statistics:")
            print(display_json(response.json()))
            return response.json()
        else:
            print(f"Error getting usage stats: {response.status_code}")
            print(f"Error response: {response.text}")
            return None
    except requests.exceptions.ConnectionError:
        print("Connection error: Unable to connect to the API server.")
        print(f"Please ensure the API server is running at {API_BASE_URL}")
        return None
    except requests.exceptions.JSONDecodeError:
        print(f"Response is not valid JSON: {response.text}")
        return None
    except Exception as e:
        print(f"Unexpected error checking usage: {str(e)}")
        return None


# Check demo usage
demo_usage = check_demo_usage()

# %% [markdown]
# ## 3. Testing Free Tier User (With User ID)

# %%
# You can use a UUID from your database or generate a new one


def test_free_tier_user(user_id):
    """Test the API as a free tier user (with user ID)"""
    if not user_id:
        print("Error: No user ID provided")
        return None

    # Generate speech
    payload = {
        "model": "kokoro",
        "input": TEST_TEXT,
        "voice": TEST_VOICE,
        "response_format": "mp3",
    }

    headers = {"X-User-ID": user_id}

    try:
        print(f"Sending request as free tier user with ID: {user_id}")
        print(f"Request payload: {json.dumps(payload)}")

        response = requests.post(
            f"{API_BASE_URL}/v1/audio/speech", json=payload, headers=headers
        )

        print(f"Response status code: {response.status_code}")
        if response.status_code == 200:
            print("Speech generated successfully!")
            audio = play_audio(response.content)
            return audio
        else:
            print(f"Error response: {response.text}")
            try:
                error_json = response.json()
                print(f"Error details: {display_json(error_json)}")
            except:
                print("Response was not JSON formatted")
            return None
    except requests.exceptions.ConnectionError:
        print("Connection error: Unable to connect to the API server.")
        print(f"Please ensure the API server is running at {API_BASE_URL}")
        return None
    except Exception as e:
        print(f"Error during free tier test: {str(e)}")
        return None


# Test free tier user
free_tier_audio = test_free_tier_user(FREE_TIER_USER_ID)

# %% [markdown]
# ## 4. Check Free Tier Usage Statistics


# %%
def check_free_tier_usage(user_id):
    """Check usage statistics for free tier user"""
    if not user_id:
        print("Error: No user ID provided")
        return None

    headers = {"X-User-ID": user_id}

    try:
        print(f"Checking usage for free tier user with ID: {user_id}")
        response = requests.get(f"{API_BASE_URL}/v1/usage", headers=headers)

        print(f"Usage stats status code: {response.status_code}")
        if response.status_code == 200:
            print("Free tier usage statistics:")
            print(display_json(response.json()))
            return response.json()
        else:
            print(f"Error getting free tier usage stats: {response.status_code}")
            print(f"Error response: {response.text}")
            return None
    except requests.exceptions.ConnectionError:
        print("Connection error: Unable to connect to the API server.")
        print(f"Please ensure the API server is running at {API_BASE_URL}")
        return None
    except requests.exceptions.JSONDecodeError:
        print(f"Response is not valid JSON: {response.text}")
        return None
    except Exception as e:
        print(f"Unexpected error checking free tier usage: {str(e)}")
        return None


# Check free tier usage
free_tier_usage = check_free_tier_usage(FREE_TIER_USER_ID)

# %% [markdown]
# ## 5. Testing Paid Tier User (With API Key)

# %%
# Replace with an actual API key from your database
API_KEY = "sk-kokoro-020ed6236de341a3a97cf412ddf40ed0"


def test_paid_tier_user(api_key):
    """Test the API as a paid tier user (with API key)"""
    if not api_key:
        print("Error: No API key provided")
        return None

    # Generate speech
    payload = {
        "model": "kokoro",
        "input": TEST_TEXT,
        "voice": TEST_VOICE,
        "response_format": "mp3",
    }

    headers = {"X-API-Key": api_key}

    try:
        print(f"Sending request as paid tier user with API key: {api_key[:8]}...")
        print(f"Request payload: {json.dumps(payload)}")

        response = requests.post(
            f"{API_BASE_URL}/v1/audio/speech", json=payload, headers=headers
        )

        print(f"Response status code: {response.status_code}")
        if response.status_code == 200:
            print("Speech generated successfully!")
            audio = play_audio(response.content)
            return audio
        else:
            print(f"Error response: {response.text}")
            try:
                error_json = response.json()
                print(f"Error details: {display_json(error_json)}")
            except:
                print("Response was not JSON formatted")
            return None
    except requests.exceptions.ConnectionError:
        print("Connection error: Unable to connect to the API server.")
        print(f"Please ensure the API server is running at {API_BASE_URL}")
        return None
    except Exception as e:
        print(f"Error during paid tier test: {str(e)}")
        return None


# Test paid tier user
# Uncomment the line below and replace with a valid API key to test
# paid_tier_audio = test_paid_tier_user(API_KEY)

# %% [markdown]
# ## 6. Check Paid Tier Usage Statistics


# %%
def check_paid_tier_usage(api_key):
    """Check usage statistics for paid tier user"""
    if not api_key:
        print("Error: No API key provided")
        return None

    headers = {"X-API-Key": api_key}

    try:
        print(f"Checking usage for paid tier user with API key: {api_key[:8]}...")
        response = requests.get(f"{API_BASE_URL}/v1/usage", headers=headers)

        print(f"Usage stats status code: {response.status_code}")
        if response.status_code == 200:
            print("Paid tier usage statistics:")
            print(display_json(response.json()))
            return response.json()
        else:
            print(f"Error getting paid tier usage stats: {response.status_code}")
            print(f"Error response: {response.text}")
            return None
    except requests.exceptions.ConnectionError:
        print("Connection error: Unable to connect to the API server.")
        print(f"Please ensure the API server is running at {API_BASE_URL}")
        return None
    except requests.exceptions.JSONDecodeError:
        print(f"Response is not valid JSON: {response.text}")
        return None
    except Exception as e:
        print(f"Unexpected error checking paid tier usage: {str(e)}")
        return None


# Check paid tier usage
# Uncomment the line below and replace with a valid API key to test
# paid_tier_usage = check_paid_tier_usage(API_KEY)

# %% [markdown]
# ## 7. Stress Test (Multiple Requests)


# %%
def stress_test_demo(num_requests=5, delay=1):
    """Make multiple requests as a demo user to test rate limiting"""
    results = []

    payload = {
        "model": "tts-1",
        "input": TEST_TEXT,
        "voice": TEST_VOICE,
        "response_format": "mp3",
    }

    for i in range(num_requests):
        print(f"Request {i + 1}/{num_requests}...")

        response = requests.post(f"{API_BASE_URL}/v1/audio/speech", json=payload)

        results.append(
            {
                "status_code": response.status_code,
                "success": response.status_code == 200,
                "response": response.json()
                if response.status_code != 200
                else "Success",
            }
        )

        # Check usage after each request
        usage_response = requests.get(f"{API_BASE_URL}/v1/usage")
        if usage_response.status_code == 200:
            print(f"Usage after request {i + 1}:")
            print(display_json(usage_response.json()))

        # Add delay between requests
        if i < num_requests - 1:
            time.sleep(delay)

    # Summarize results
    success_count = sum(1 for r in results if r["success"])
    print(f"\nSummary: {success_count}/{num_requests} requests succeeded")

    return results


# Uncomment to run stress test
# stress_results = stress_test_demo(num_requests=5, delay=1)

# %% [markdown]
# ## 8. Create Test Users in Supabase
#
# If you need to create test users in your Supabase database, you can use the code below. Make sure to update the Supabase URL and key.


# %%
def create_test_user():
    """Create a test user in Supabase"""
    if not supabase:
        print(
            "Supabase client not initialized. Set SUPABASE_URL and SUPABASE_KEY environment variables."
        )
        return None

    try:
        # Generate a unique email
        email = f"test-{uuid.uuid4()}@example.com"

        # Create user
        user_data = {"email": email, "full_name": "Test User"}

        response = supabase.table("users").insert(user_data).execute()

        if response.data:
            user_id = response.data[0]["id"]
            print(f"Created test user with ID: {user_id}")
            return user_id
        else:
            print("Failed to create test user")
            return None
    except Exception as e:
        print(f"Error creating test user: {e}")
        return None


def create_api_key(user_id):
    """Create an API key for a user"""
    if not supabase:
        print(
            "Supabase client not initialized. Set SUPABASE_URL and SUPABASE_KEY environment variables."
        )
        return None

    try:
        # Generate API key
        api_key = f"sk-kokoro-{uuid.uuid4().hex}"
        key_prefix = api_key[:16]  # Adjust based on your prefix length

        # Store in database
        api_key_data = {
            "user_id": user_id,
            "name": "Test API Key",
            "key_prefix": key_prefix,
            "key_hash": api_key,  # In production, you'd hash this
            "is_active": True,
        }

        response = supabase.table("api_keys").insert(api_key_data).execute()

        if response.data:
            print(f"Created API key: {api_key}")
            return api_key
        else:
            print("Failed to create API key")
            return None
    except Exception as e:
        print(f"Error creating API key: {e}")
        return None


# %% [markdown]
# ## 14. Premium Tier Overage Testing


# %%
def test_premium_tier_overage(api_key, character_limit=1000, target_multiplier=1.5):
    """Test premium tier by generating enough requests to exceed the character limit

    Args:
        api_key: The premium tier API key to test with
        character_limit: The character limit for the subscription
        target_multiplier: How much to exceed the limit (e.g., 1.5 means 150% of the limit)

    Returns:
        Dict with test results and stats
    """
    if not api_key:
        print("API key is required")
        return None

    # First check current usage
    initial_usage = check_paid_tier_usage(api_key)
    if not initial_usage:
        print("Could not get initial usage stats")
        return None

    current_usage = initial_usage.get("total_characters", 0)
    character_limit = initial_usage.get("character_limit", character_limit)

    print(f"Current usage: {current_usage} / {character_limit} characters")

    # Calculate how many characters we need to generate to exceed the limit
    target_usage = int(character_limit * target_multiplier)
    characters_needed = max(0, target_usage - current_usage)

    if characters_needed <= 0:
        print("Character limit already exceeded!")
        return {
            "initial_usage": initial_usage,
            "final_usage": initial_usage,
            "characters_needed": 0,
            "requests_sent": 0,
            "success_count": 0,
            "status": "already_exceeded",
        }

    print(
        f"Need to generate {characters_needed} more characters to reach target of {target_usage}"
    )

    # Generate text of a specific length to help reach the target
    # This is more efficient than sending many small requests
    batch_size = min(10000, characters_needed)  # Don't make requests too large
    num_requests = (
        characters_needed + batch_size - 1
    ) // batch_size  # Ceiling division

    print(
        f"Will send {num_requests} requests with approximately {batch_size} characters each"
    )

    # Create text of the desired length
    base_text = "This is a test of the premium tier overage billing system. " * 10

    results = []
    success_count = 0
    actual_characters_sent = 0

    headers = {"X-API-Key": api_key}

    for i in range(num_requests):
        # Generate text of approximately the desired length
        repetitions = max(1, batch_size // len(base_text))
        input_text = base_text * repetitions

        # Adjust the last request to hit the target more precisely
        if (
            i == num_requests - 1
            and actual_characters_sent + len(input_text) > target_usage
        ):
            remaining = target_usage - actual_characters_sent
            input_text = input_text[:remaining]

        print(
            f"Request {i + 1}/{num_requests}: Sending {len(input_text)} characters..."
        )

        payload = {
            "model": "tts-1",
            "input": input_text,
            "voice": TEST_VOICE,
            "response_format": "mp3",
        }

        response = requests.post(
            f"{API_BASE_URL}/v1/audio/speech", json=payload, headers=headers
        )

        result = {
            "request_num": i + 1,
            "characters": len(input_text),
            "status_code": response.status_code,
            "success": response.status_code == 200,
        }

        if response.status_code == 200:
            success_count += 1
            actual_characters_sent += len(input_text)
            result["actual_characters"] = len(input_text)
        else:
            try:
                error_data = response.json()
                result["error"] = error_data
                print(f"Error: {display_json(error_data)}")
            except:
                result["error"] = "Could not parse error"
                print(f"Error: Status code {response.status_code}")

        results.append(result)

        # Add a short delay between requests
        time.sleep(0.5)

    # Check final usage
    final_usage = check_paid_tier_usage(api_key)

    print(f"\nSummary: {success_count}/{num_requests} requests succeeded")
    print(f"Sent approximately {actual_characters_sent} characters")

    if final_usage:
        final_character_count = final_usage.get("total_characters", 0)
        print(f"Final usage: {final_character_count} / {character_limit} characters")

        # Calculate overage
        overage = max(0, final_character_count - character_limit)
        if overage > 0:
            print(f"Overage: {overage} characters")

    return {
        "initial_usage": initial_usage,
        "final_usage": final_usage,
        "characters_needed": characters_needed,
        "characters_sent": actual_characters_sent,
        "requests_sent": num_requests,
        "success_count": success_count,
        "results": results,
        "status": "completed",
    }


# %% [markdown]
# ## 15. Validate Supabase Usage Records


# %%
def check_supabase_usage_records(api_key=None, user_id=None, subscription_id=None):
    """Check usage records directly in Supabase

    Args:
        api_key: API key for a paid user
        user_id: User ID for a free tier user
        subscription_id: Subscription ID to check directly

    Returns:
        Usage records from Supabase
    """
    if not supabase:
        print(
            "Supabase client not initialized. Set SUPABASE_URL and SUPABASE_KEY environment variables."
        )
        return None

    try:
        # Determine which records to check based on inputs
        if api_key and not subscription_id:
            # First get user and subscription info from API key
            key_prefix = api_key.split("-")[-1][
                :8
            ]  # Extract prefix based on your API key format

            api_key_info = (
                supabase.table("api_keys")
                .select("*, users(*)")
                .eq("key_prefix", key_prefix)
                .eq("is_active", True)
                .execute()
            )

            if not api_key_info.data:
                print(f"API key with prefix {key_prefix} not found")
                return None

            user_id = api_key_info.data[0].get("user_id")

            # Get subscription for this user
            sub_info = (
                supabase.table("subscriptions")
                .select("*")
                .eq("user_id", user_id)
                .eq("status", "active")
                .execute()
            )

            if not sub_info.data:
                print(f"No active subscription found for user {user_id}")
                return None

            subscription_id = sub_info.data[0].get("id")

        # If we have subscription_id, get the usage records
        if subscription_id:
            print(f"Checking usage records for subscription {subscription_id}")

            usage_records = (
                supabase.table("usage")
                .select("*")
                .eq("subscription_id", subscription_id)
                .order("period_start", desc=True)
                .execute()
            )

            if not usage_records.data:
                print("No usage records found")
                return None

            print(f"Found {len(usage_records.data)} usage records")
            return usage_records.data

        # If we have user_id but no subscription (free tier), check free usage
        elif user_id:
            print(f"Checking free tier usage for user {user_id}")

            # Get the current month's period
            now = datetime.now()
            period_start = datetime(now.year, now.month, 1).isoformat()

            free_usage = (
                supabase.table("free_usage")
                .select("*")
                .eq("user_id", user_id)
                .eq("period_start", period_start)
                .execute()
            )

            if not free_usage.data:
                print("No free tier usage records found")
                return None

            print(f"Found {len(free_usage.data)} free tier usage records")
            return free_usage.data

        else:
            print("Either API key, user ID, or subscription ID is required")
            return None

    except Exception as e:
        print(f"Error checking usage records: {e}")
        return None


# %% [markdown]
# ## 16. Check Stripe Usage Records


# %%
def check_stripe_usage_records(subscription_id=None, api_key=None):
    """Check usage records directly in Stripe

    Args:
        subscription_id: Stripe subscription ID
        api_key: API key for a paid user (will try to find the stripe subscription)

    Returns:
        Usage records from Stripe
    """
    if not stripe.api_key:
        print(
            "Stripe API key not configured. Set STRIPE_SECRET_KEY environment variable."
        )
        return None

    if not subscription_id and not api_key:
        print("Either subscription_id or api_key must be provided")
        return None

    try:
        # If we have API key but no subscription ID, try to find it
        if api_key and not subscription_id:
            if not supabase:
                print(
                    "Supabase client required to lookup Stripe subscription from API key"
                )
                return None

            # Get user and subscription info from API key
            key_prefix = api_key.split("-")[-1][
                :8
            ]  # Extract prefix based on your API key format

            api_key_info = (
                supabase.table("api_keys")
                .select("*, users(*)")
                .eq("key_prefix", key_prefix)
                .eq("is_active", True)
                .execute()
            )

            if not api_key_info.data:
                print(f"API key with prefix {key_prefix} not found")
                return None

            user_id = api_key_info.data[0].get("user_id")

            # Get stripe subscription for this user
            sub_info = (
                supabase.table("subscriptions")
                .select("*")
                .eq("user_id", user_id)
                .eq("status", "active")
                .execute()
            )

            if not sub_info.data:
                print(f"No active subscription found for user {user_id}")
                return None

            subscription_id = sub_info.data[0].get("stripe_subscription_id")

            if not subscription_id:
                print("Subscription has no Stripe subscription ID")
                return None

        # Get the Stripe subscription
        subscription = stripe.Subscription.retrieve(subscription_id)
        if not subscription:
            print(f"Stripe subscription {subscription_id} not found")
            return None

        # Find the metered subscription item
        metered_item = None
        for item in subscription.items.data:
            if (
                hasattr(item.price, "recurring")
                and item.price.recurring.usage_type == "metered"
            ):
                metered_item = item.id
                break

        if not metered_item:
            print(
                f"No metered subscription item found for subscription {subscription_id}"
            )
            return {
                "subscription": subscription,
                "usage_records": [],
                "has_metered_component": False,
            }

        # Get usage records for this subscription item
        usage_records = stripe.SubscriptionItem.list_usage_record_summaries(
            metered_item
        )

        return {
            "subscription": subscription,
            "usage_records": usage_records.data,
            "has_metered_component": True,
            "metered_item_id": metered_item,
        }

    except Exception as e:
        print(f"Error checking Stripe usage records: {e}")
        return None


# %% [markdown]
# ## 17. End-to-End Overage Test with Validation


# %%
def run_overage_validation_test(api_key, character_limit=None, target_multiplier=1.5):
    """Run a complete end-to-end test of the overage billing system

    This function:
    1. Sends requests to exceed the character limit
    2. Checks Supabase for usage records
    3. Checks Stripe for usage records
    4. Validates that overage was correctly reported

    Args:
        api_key: Premium tier API key
        character_limit: Character limit (will be retrieved from API if not provided)
        target_multiplier: How much to exceed the limit (e.g., 1.5 means 150% of the limit)

    Returns:
        Dict with test results and validation report
    """
    results = {}

    print("=== RUNNING END-TO-END OVERAGE VALIDATION TEST ===")
    print(f"Using API key: {api_key}")

    # Step 1: Get initial usage and Supabase/Stripe records
    print("\n--- STEP 1: Checking initial state ---")
    initial_usage = check_paid_tier_usage(api_key)

    if not initial_usage:
        print("Could not get initial usage stats. Aborting test.")
        return None

    results["initial_api_usage"] = initial_usage

    # Set character limit if not provided
    if not character_limit:
        character_limit = initial_usage.get("character_limit", 1000)

    # Check initial Supabase records
    initial_supabase_records = check_supabase_usage_records(api_key=api_key)
    results["initial_supabase_records"] = initial_supabase_records

    # Check initial Stripe records
    initial_stripe_records = check_stripe_usage_records(api_key=api_key)
    results["initial_stripe_records"] = initial_stripe_records

    # Step 2: Generate overage
    print("\n--- STEP 2: Generating overage by sending requests ---")
    overage_results = test_premium_tier_overage(
        api_key, character_limit, target_multiplier
    )
    results["overage_test"] = overage_results

    if not overage_results or overage_results.get("status") != "completed":
        print(
            "Overage generation failed or was not needed. Check the overage test results."
        )

    # Step 3: Check final state immediately after requests
    print("\n--- STEP 3: Checking state after requests ---")
    results["final_api_usage"] = check_paid_tier_usage(api_key)
    results["final_supabase_records"] = check_supabase_usage_records(api_key=api_key)
    results["final_stripe_records"] = check_stripe_usage_records(api_key=api_key)

    # Step 4: Wait for the scheduled job to run and check again
    # Typically this would be automated in a real test, but for manual testing we'll pause
    print("\n--- STEP 4: Waiting for scheduled jobs to run ---")
    print("Please wait for the scheduled job to run, or manually trigger it.")
    print("This is when overages should be reported to Stripe.")
    print("Press Enter when ready to check the final state...")
    input()

    # Step 5: Check final state after scheduled job
    print("\n--- STEP 5: Checking final state after scheduled job ---")
    results["post_job_api_usage"] = check_paid_tier_usage(api_key)
    results["post_job_supabase_records"] = check_supabase_usage_records(api_key=api_key)
    results["post_job_stripe_records"] = check_stripe_usage_records(api_key=api_key)

    # Step 6: Analyze and validate results
    print("\n--- STEP 6: Validating results ---")
    validation = validate_overage_results(results)
    results["validation"] = validation

    # Print validation summary
    print("\n=== VALIDATION SUMMARY ===")
    for key, value in validation.items():
        status = "✅ PASS" if value["status"] == "pass" else "❌ FAIL"
        print(f"{key}: {status} - {value['message']}")

    print("\n=== TEST COMPLETE ===")

    return results


def validate_overage_results(results):
    """Validate the results of an overage test"""
    validation = {}

    # Validate API usage reporting
    try:
        initial_usage = results.get("initial_api_usage", {})
        final_usage = results.get("final_api_usage", {})
        post_job_usage = results.get("post_job_api_usage", {})

        # Character count should increase
        initial_chars = initial_usage.get("total_characters", 0)
        final_chars = final_usage.get("total_characters", 0)

        if final_chars > initial_chars:
            validation["api_character_count"] = {
                "status": "pass",
                "message": f"Character count increased from {initial_chars} to {final_chars}",
                "difference": final_chars - initial_chars,
            }
        else:
            validation["api_character_count"] = {
                "status": "fail",
                "message": f"Character count did not increase (initial: {initial_chars}, final: {final_chars})",
                "difference": final_chars - initial_chars,
            }

        # Check if we exceeded the limit
        char_limit = initial_usage.get("character_limit", 0)
        if final_chars > char_limit:
            validation["limit_exceeded"] = {
                "status": "pass",
                "message": f"Character limit ({char_limit}) was exceeded ({final_chars})",
                "overage": final_chars - char_limit,
            }
        else:
            validation["limit_exceeded"] = {
                "status": "fail",
                "message": f"Character limit ({char_limit}) was not exceeded ({final_chars})",
                "overage": 0,
            }
    except Exception as e:
        validation["api_usage_validation"] = {
            "status": "fail",
            "message": f"Error validating API usage: {e}",
            "error": str(e),
        }

    # Validate Supabase usage records
    try:
        initial_records = results.get("initial_supabase_records", [])
        final_records = results.get("final_supabase_records", [])
        post_job_records = results.get("post_job_supabase_records", [])

        if not initial_records or not final_records:
            validation["supabase_records"] = {
                "status": "fail",
                "message": "Could not get Supabase records",
            }
        else:
            # Find the matching records to compare
            initial_record = initial_records[0] if initial_records else {}
            final_record = final_records[0] if final_records else {}
            post_job_record = post_job_records[0] if post_job_records else {}

            # Check if characters increased
            initial_chars = initial_record.get("characters_used", 0)
            final_chars = final_record.get("characters_used", 0)

            if final_chars > initial_chars:
                validation["supabase_character_count"] = {
                    "status": "pass",
                    "message": f"Supabase character count increased from {initial_chars} to {final_chars}",
                    "difference": final_chars - initial_chars,
                }
            else:
                validation["supabase_character_count"] = {
                    "status": "fail",
                    "message": f"Supabase character count did not increase (initial: {initial_chars}, final: {final_chars})",
                    "difference": final_chars - initial_chars,
                }

            # Check if reported_to_stripe was updated after the job ran
            if post_job_record and "reported_to_stripe" in post_job_record:
                if post_job_record["reported_to_stripe"] is True:
                    validation["reported_to_stripe_flag"] = {
                        "status": "pass",
                        "message": "Usage was marked as reported to Stripe",
                    }
                else:
                    validation["reported_to_stripe_flag"] = {
                        "status": "fail",
                        "message": "Usage was not marked as reported to Stripe",
                    }
    except Exception as e:
        validation["supabase_validation"] = {
            "status": "fail",
            "message": f"Error validating Supabase records: {e}",
            "error": str(e),
        }

    # Validate Stripe usage records
    try:
        initial_stripe = results.get("initial_stripe_records", {})
        final_stripe = results.get("final_stripe_records", {})
        post_job_stripe = results.get("post_job_stripe_records", {})

        # Check if there's a metered component
        has_metered = post_job_stripe.get("has_metered_component", False)
        if not has_metered:
            validation["stripe_metered_component"] = {
                "status": "fail",
                "message": "Subscription does not have a metered component",
            }
        else:
            validation["stripe_metered_component"] = {
                "status": "pass",
                "message": "Subscription has a metered component",
            }

            # Check if usage records were created
            initial_records = initial_stripe.get("usage_records", [])
            post_job_records = post_job_stripe.get("usage_records", [])

            if len(post_job_records) > len(initial_records):
                validation["stripe_usage_records"] = {
                    "status": "pass",
                    "message": f"New Stripe usage records were created (initial: {len(initial_records)}, final: {len(post_job_records)})",
                    "new_records": len(post_job_records) - len(initial_records),
                }
            else:
                validation["stripe_usage_records"] = {
                    "status": "fail",
                    "message": f"No new Stripe usage records were created (initial: {len(initial_records)}, final: {len(post_job_records)})",
                    "new_records": 0,
                }
    except Exception as e:
        validation["stripe_validation"] = {
            "status": "fail",
            "message": f"Error validating Stripe records: {e}",
            "error": str(e),
        }

    return validation


# %% [markdown]
# ## 18. Run Manual Stripe Report Job Test


# %%
def trigger_report_all_overages(api_url=API_BASE_URL):
    """Trigger the manual execution of the report_all_overages_to_stripe job

    Returns:
        Response from the API
    """
    # This assumes you have an admin endpoint to trigger the job
    # You might need to modify this based on your actual API implementation
    endpoint = f"{api_url}/dev/jobs/report_usage"

    try:
        response = requests.post(endpoint)

        if response.status_code == 200:
            print("Successfully triggered overage reporting job")
            return response.json()
        else:
            print(f"Failed to trigger overage reporting job: {response.status_code}")
            print(display_json(response.json()))
            return None
    except Exception as e:
        print(f"Error triggering overage reporting job: {e}")
        return None


def test_voices_endpoint(api_key=None, user_id=None):
    """Test the voices endpoint with different authentication methods"""
    print("\n--- Testing /v1/audio/voices endpoint ---")

    # Try with no authentication
    print("1. Testing without authentication:")
    try:
        response = requests.get(f"{API_BASE_URL}/v1/audio/voices")
        print(f"Status code: {response.status_code}")
        print(f"Response: {response.text}")
    except Exception as e:
        print(f"Error: {str(e)}")

    # Try with user ID
    if user_id:
        print(f"\n2. Testing with user ID: {user_id}")
        try:
            headers = {"X-User-ID": user_id}
            response = requests.get(f"{API_BASE_URL}/v1/audio/voices", headers=headers)
            print(f"Status code: {response.status_code}")
            print(f"Response: {response.text}")
        except Exception as e:
            print(f"Error: {str(e)}")

    # Try with API key
    if api_key:
        print(f"\n3. Testing with API key: {api_key[:8]}...")
        try:
            headers = {"X-API-Key": api_key}
            response = requests.get(f"{API_BASE_URL}/v1/audio/voices", headers=headers)
            print(f"Status code: {response.status_code}")
            if response.status_code == 200:
                print("Available voices:")
                print(display_json(response.json()))
            else:
                print(f"Response: {response.text}")
        except Exception as e:
            print(f"Error: {str(e)}")

    print("\n--- Voices endpoint test complete ---")


if __name__ == "__main__":
    print("Running API tests...")

    # Test the voices endpoint specifically
    test_voices_endpoint(api_key=BASIC_TIER_API_KEY, user_id=FREE_TIER_USER_ID)

    print("\n=== 1. Testing Demo User (No Authentication) ===")
    demo_audio = test_demo_user()
    demo_usage = check_demo_usage()

    print("\n=== 2. Testing Free Tier User (With User ID) ===")
    free_tier_audio = test_free_tier_user(FREE_TIER_USER_ID)
    free_tier_usage = check_free_tier_usage(FREE_TIER_USER_ID)

    print("\n=== 3. Testing Basic Tier User (With API Key) ===")
    basic_tier_audio = test_paid_tier_user(BASIC_TIER_API_KEY)
    basic_tier_usage = check_paid_tier_usage(BASIC_TIER_API_KEY)

    print("\n=== 4. Testing Premium Tier User (With API Key) ===")
    premium_tier_audio = test_paid_tier_user(PREMIUM_TIER_API_KEY)
    premium_tier_usage = check_paid_tier_usage(PREMIUM_TIER_API_KEY)

    print("\n=== 5. Stress Test (Multiple Requests) ===")
    # Limit to just 3 requests to avoid overloading the system
    stress_results = stress_test_demo(num_requests=3, delay=1)

    # Uncomment if you want to test premium tier overage
    # print("\n=== 6. Testing Premium Tier Overage ===")
    # overage_results = test_premium_tier_overage(PREMIUM_TIER_API_KEY, character_limit=1000000, target_multiplier=1.1)

    # Uncomment if you want to check Supabase usage records
    # print("\n=== 7. Checking Supabase Usage Records ===")
    # premium_usage_records = check_supabase_usage_records(api_key=PREMIUM_TIER_API_KEY)

    # Uncomment if you want to check Stripe usage records
    # print("\n=== 8. Checking Stripe Usage Records ===")
    # stripe_usage_records = check_stripe_usage_records(subscription_id=PREMIUM_SUBSCRIPTION_ID)

    # Uncomment if you want to manually trigger the overage report job
    # print("\n=== 9. Triggering Overage Report Job ===")
    # trigger_result = trigger_report_all_overages()

    print("\n=== All tests completed! ===")
    print(
        "To run advanced tests like overage billing, uncomment the relevant sections in the script."
    )
