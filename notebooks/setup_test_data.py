#!/usr/bin/env python3
"""
Setup test data in Supabase and Stripe for Kokoro TTS API testing.

This script creates:
1. Test users (free tier and paid tier) in both Supabase and Stripe
2. Products in both Supabase and Stripe
3. Subscriptions in both Supabase and Stripe
4. API keys in Supabase
5. Usage records in Supabase

It also provides a way to clean up all created test data.

Usage:
    python setup_test_data.py [--create] [--cleanup]
"""

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import time
import uuid
import base64

import stripe
from dotenv import load_dotenv
from supabase import create_client

# Get the script directory and construct absolute path to .env file
script_dir = pathlib.Path(__file__).parent.absolute()
env_path = script_dir.parent / "config" / ".env.example"

# Parse command line arguments
parser = argparse.ArgumentParser(
    description="Setup or cleanup test data for Kokoro TTS API"
)
parser.add_argument("--create", action="store_true", help="Create test data")
parser.add_argument("--cleanup", action="store_true", help="Cleanup test data")
args = parser.parse_args()

# Load environment variables from .env file
load_dotenv(env_path)
print(f"Loading environment variables from: {env_path}")

# Supabase configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Stripe configuration
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("Error: SUPABASE_URL and SUPABASE_KEY must be set in ../config/.env")
    exit(1)

if not STRIPE_SECRET_KEY:
    print("Warning: STRIPE_SECRET_KEY not found. Stripe integration will be mocked.")
    USE_STRIPE = False
else:
    USE_STRIPE = True
    stripe.api_key = STRIPE_SECRET_KEY
    print(f"Stripe API key loaded: {STRIPE_SECRET_KEY[:4]}...{STRIPE_SECRET_KEY[-4:]}")
    
    # Test the Stripe connection
    try:
        account = stripe.Account.retrieve()
        print(f"Stripe connection successful! Connected to account: {account.id}")
    except Exception as e:
        print(f"ERROR: Failed to connect to Stripe API: {e}")
        print("Falling back to mock mode")
        USE_STRIPE = False

# Initialize Supabase client
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# File to store test data IDs for cleanup
TEST_DATA_FILE = script_dir / "test_data_ids.json"


def load_test_data_ids():
    """Load test data IDs from file"""
    # Define the default structure with empty lists and the test_data section
    default_structure = {
        # Original ID tracking for cleanup
        "users": [],
        "products": [],
        "subscriptions": [],
        "api_keys": [],
        "usage": [],
        "free_usage": [],
        "stripe_customers": [],
        "stripe_products": [],
        "stripe_subscriptions": [],
        # New section with complete test data for API testing
        "test_data": {
            "free_tier_user_id": None,
            "basic_tier_user_id": None,
            "premium_tier_user_id": None,
            "basic_tier_api_key": None,
            "premium_tier_api_key": None,
            "premium_subscription_id": None,
            "free_product_id": None,
            "basic_product_id": None,
            "premium_product_id": None,
            "demo_user_id": None,  # Add demo user ID to the default structure
        }
    }
    
    if TEST_DATA_FILE.exists():
        try:
            with open(TEST_DATA_FILE, "r") as f:
                data = json.load(f)
                
            # Ensure all required keys exist in the loaded data
            for key in default_structure:
                if key not in data:
                    data[key] = default_structure[key]
                    
            # Make sure test_data section is complete
            if "test_data" in data:
                for subkey in default_structure["test_data"]:
                    if subkey not in data["test_data"]:
                        data["test_data"][subkey] = None
                        
            return data
        except Exception as e:
            print(f"Error loading test data file: {e}")
            return default_structure
    
    return default_structure


def save_test_data_ids(data):
    """Save test data IDs to file"""
    with open(TEST_DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


def hash_api_key(api_key):
    """Hash an API key for storage"""
    return hashlib.sha256(api_key.encode()).hexdigest()


def create_stripe_customer(email, name):
    """Create a customer in Stripe"""
    if not USE_STRIPE:
        mock_id = f"cus_mock_{uuid.uuid4().hex[:8]}"
        print(f"MOCK MODE: Created fake Stripe customer: {name} ({email}) with ID: {mock_id}")
        return mock_id

    try:
        print(f"Creating Stripe customer for {name} ({email})...")
        customer = stripe.Customer.create(
            email=email, name=name, metadata={"is_test": "true"}
        )
        print(f"Created Stripe customer: {name} ({email}) with ID: {customer.id}")
        
        # Attach a test payment method to the customer
        attach_test_payment_method(customer.id)
        
        return customer.id
    except Exception as e:
        print(f"ERROR creating Stripe customer {email}: {e}")
        print(f"Stripe API key in use: {STRIPE_SECRET_KEY[:4]}...{STRIPE_SECRET_KEY[-4:]}")
        return None


def attach_test_payment_method(customer_id):
    """Attach a test payment method to a Stripe customer"""
    if not USE_STRIPE:
        return

    try:
        # Use a predefined test token instead of creating one with raw card data
        # This is a test token for a Visa card that always succeeds
        test_token = "tok_visa"  # Predefined Stripe test token
        
        # Create a source using the token
        source = stripe.Customer.create_source(
            customer_id,
            source=test_token
        )
        
        # Set as the default source
        stripe.Customer.modify(
            customer_id,
            default_source=source.id
        )
        
        print(f"Attached test payment source {source.id} to customer {customer_id}")
        return source.id
    except Exception as e:
        print(f"ERROR attaching payment method to customer {customer_id}: {e}")
        return None


def create_stripe_product(
    name, description, price, request_limit=None, is_premium=False
):
    """Create a product in Stripe"""
    if not USE_STRIPE:
        return f"prod_mock_{uuid.uuid4().hex[:8]}", f"price_mock_{uuid.uuid4().hex[:8]}"

    try:
        # Add tier and character limit to metadata
        metadata = {
            "is_test": "true",
            "tier": name.lower().replace(" tier", ""),
            "character_limit": str(request_limit if request_limit else "unlimited"),
        }

        # Add overage rate for premium tier
        if is_premium:
            metadata["overage_rate"] = "10.00"  # $10 per million characters

        # Create the product
        product = stripe.Product.create(
            name=name, description=description, metadata=metadata
        )

        # Base price configuration
        price_params = {
            "product": product.id,
            "unit_amount": int(price * 100),  # Convert to cents
            "currency": "usd",
            "recurring": {"interval": "month"},
            "metadata": metadata,
        }

        # Create the base subscription price
        stripe_price = stripe.Price.create(**price_params)

        # If this is a premium tier, also create a metered price for character overages
        metered_price_id = None
        if is_premium:
            metered_price = stripe.Price.create(
                product=product.id,
                currency="usd",
                unit_amount_decimal="0.00001",  # $0.00001 per character ($10 per million)
                recurring={"interval": "month", "usage_type": "metered"},
                metadata={
                    "is_test": "true",
                    "tier": name.lower().replace(" tier", ""),
                    "type": "overage",
                    "unit": "character",
                },
                nickname="Character Overage",
            )
            metered_price_id = metered_price.id
            print(f"Created metered price for {name} overages: {metered_price_id}")

        print(f"Created Stripe product: {name} (${price}) with ID: {product.id}")
        return product.id, stripe_price.id, metered_price_id
    except Exception as e:
        print(f"Error creating Stripe product {name}: {e}")
        return None, None, None


def create_stripe_subscription(customer_id, price_id, metered_price_id=None):
    """Create a subscription in Stripe"""
    if not USE_STRIPE:
        mock_id = f"sub_mock_{uuid.uuid4().hex[:8]}"
        print(f"MOCK MODE: Created fake Stripe subscription for customer {customer_id} with ID: {mock_id}")
        return mock_id

    try:
        print(f"Creating Stripe subscription for customer {customer_id} with price {price_id}...")
        
        # Create subscription items array
        items = [{"price": price_id}]

        # Add metered price if provided
        if metered_price_id:
            print(f"Including metered price component: {metered_price_id}")
            items.append(
                {
                    "price": metered_price_id,
                    # Don't charge immediately for usage, will be reported and billed later
                    "billing_thresholds": {"usage_gte": 1},
                }
            )

        subscription = stripe.Subscription.create(
            customer=customer_id, items=items, metadata={"is_test": "true"}
        )
        print(
            f"Created Stripe subscription for customer {customer_id} with ID: {subscription.id}"
        )
        return subscription.id
    except Exception as e:
        print(f"ERROR creating Stripe subscription for customer {customer_id}: {e}")
        print(f"Price ID: {price_id}, Metered Price ID: {metered_price_id}")
        return None


def create_test_user(email, full_name):
    """Create a test user in Supabase and Stripe"""
    test_data = load_test_data_ids()

    try:
        # Check if user already exists
        response = supabase.table("users").select("*").eq("email", email).execute()
        if response.data:
            print(f"User with email {email} already exists, skipping creation")
            user_id = response.data[0]["id"]
            if user_id not in test_data["users"]:
                test_data["users"].append(user_id)
                save_test_data_ids(test_data)
            return user_id, response.data[0].get("stripe_customer_id")

        # Create Stripe customer
        stripe_customer_id = create_stripe_customer(email, full_name)

        # Create user in Supabase
        user_data = {
            "email": email,
            "full_name": full_name,
            "stripe_customer_id": stripe_customer_id,
        }

        response = supabase.table("users").insert(user_data).execute()

        if response.data:
            user_id = response.data[0]["id"]
            print(f"Created user: {full_name} ({email}) with ID: {user_id}")

            # Save for cleanup
            test_data["users"].append(user_id)
            if stripe_customer_id:
                test_data["stripe_customers"].append(stripe_customer_id)
            save_test_data_ids(test_data)

            return user_id, stripe_customer_id
        else:
            print(f"Failed to create user: {email}")
            return None, None
    except Exception as e:
        print(f"Error creating user {email}: {e}")
        return None, None


def create_product(name, description, price, request_limit=None):
    """Create a product in Supabase and Stripe"""
    test_data = load_test_data_ids()

    try:
        # Check if product already exists
        response = supabase.table("products").select("*").eq("name", name).execute()
        if response.data:
            print(f"Product {name} already exists, skipping creation")
            product_id = response.data[0]["id"]
            if product_id not in test_data["products"]:
                test_data["products"].append(product_id)
                save_test_data_ids(test_data)
            return (
                product_id,
                response.data[0].get("stripe_product_id"),
                response.data[0].get("stripe_price_id"),
                response.data[0].get("stripe_metered_price_id"),
            )

        # Determine if this is the premium tier (for metered billing)
        is_premium = "Premium" in name

        # Create product in Stripe
        stripe_product_id, stripe_price_id, stripe_metered_price_id = (
            create_stripe_product(name, description, price, request_limit, is_premium)
        )

        # Create product in Supabase
        product_data = {
            "name": name,
            "description": description,
            "price": price,
            "stripe_product_id": stripe_product_id,
            "stripe_price_id": stripe_price_id,
            "stripe_metered_price_id": stripe_metered_price_id,  # Store the metered price ID
            "active": True,
            "monthly_request_limit": request_limit,
        }

        response = supabase.table("products").insert(product_data).execute()

        if response.data:
            product_id = response.data[0]["id"]
            print(f"Created product: {name} (${price}) with ID: {product_id}")

            # Save for cleanup
            test_data["products"].append(product_id)
            if stripe_product_id:
                test_data["stripe_products"].append(stripe_product_id)
            save_test_data_ids(test_data)

            return (
                product_id,
                stripe_product_id,
                stripe_price_id,
                stripe_metered_price_id,
            )
        else:
            print(f"Failed to create product: {name}")
            return None, None, None, None
    except Exception as e:
        print(f"Error creating product {name}: {e}")
        return None, None, None, None


def create_subscription(
    user_id,
    product_id,
    stripe_customer_id=None,
    stripe_price_id=None,
    stripe_metered_price_id=None,
    status="active",
):
    """Create a subscription in Supabase and Stripe"""
    test_data = load_test_data_ids()

    try:
        # Check if subscription already exists
        response = (
            supabase.table("subscriptions")
            .select("*")
            .eq("user_id", user_id)
            .eq("product_id", product_id)
            .execute()
        )
        if response.data:
            print(
                f"Subscription for user {user_id} and product {product_id} already exists, skipping creation"
            )
            sub_id = response.data[0]["id"]
            if sub_id not in test_data["subscriptions"]:
                test_data["subscriptions"].append(sub_id)
                save_test_data_ids(test_data)
            return sub_id, response.data[0].get("stripe_subscription_id")

        # Create subscription in Stripe
        stripe_sub_id = None
        if stripe_customer_id and stripe_price_id:
            stripe_sub_id = create_stripe_subscription(
                stripe_customer_id, stripe_price_id, stripe_metered_price_id
            )

        # Set subscription period
        now = datetime.datetime.utcnow()
        period_start = now.isoformat()
        period_end = (now + datetime.timedelta(days=30)).isoformat()

        # Create subscription in Supabase
        subscription_data = {
            "user_id": user_id,
            "product_id": product_id,
            "stripe_subscription_id": stripe_sub_id,
            "stripe_price_id": stripe_price_id,
            "status": status,
            "current_period_start": period_start,
            "current_period_end": period_end,
            "cancel_at_period_end": False,
        }

        response = supabase.table("subscriptions").insert(subscription_data).execute()

        if response.data:
            subscription_id = response.data[0]["id"]
            print(f"Created subscription for user {user_id} with ID: {subscription_id}")

            # Create initial usage record
            create_usage_record(subscription_id, period_start, period_end)

            # Save for cleanup
            test_data["subscriptions"].append(subscription_id)
            if stripe_sub_id:
                test_data["stripe_subscriptions"].append(stripe_sub_id)
            save_test_data_ids(test_data)

            return subscription_id, stripe_sub_id
        else:
            print(f"Failed to create subscription for user {user_id}")
            return None, None
    except Exception as e:
        print(f"Error creating subscription for user {user_id}: {e}")
        return None, None


def create_free_usage_record(user_id, period_start, period_end):
    """Create a free usage record for a user"""
    test_data = load_test_data_ids()

    try:
        # Check if usage record already exists
        response = (
            supabase.table("free_usage")
            .select("*")
            .eq("user_id", user_id)
            .eq("period_start", period_start)
            .execute()
        )
        if response.data:
            print(
                f"Free usage record for user {user_id} already exists, skipping creation"
            )
            usage_id = response.data[0]["id"]
            if usage_id not in test_data["free_usage"]:
                test_data["free_usage"].append(usage_id)
                save_test_data_ids(test_data)
            return usage_id

        # Create usage record
        usage_data = {
            "user_id": user_id,
            "period_start": period_start,
            "period_end": period_end,
            "total_requests": 0,
            "last_request_at": None,
        }

        response = supabase.table("free_usage").insert(usage_data).execute()

        if response.data:
            usage_id = response.data[0]["id"]
            print(f"Created free usage record for user {user_id} with ID: {usage_id}")

            # Save for cleanup
            test_data["free_usage"].append(usage_id)
            save_test_data_ids(test_data)

            return usage_id
        else:
            print(f"Failed to create free usage record for user {user_id}")
            return None
    except Exception as e:
        print(f"Error creating free usage record for user {user_id}: {e}")
        return None


def create_usage_record(subscription_id, period_start, period_end):
    """Create a usage record for a subscription"""
    test_data = load_test_data_ids()

    try:
        # Check if usage record already exists
        response = (
            supabase.table("usage")
            .select("*")
            .eq("subscription_id", subscription_id)
            .eq("period_start", period_start)
            .execute()
        )
        if response.data:
            print(
                f"Usage record for subscription {subscription_id} already exists, skipping creation"
            )
            usage_id = response.data[0]["id"]
            if usage_id not in test_data["usage"]:
                test_data["usage"].append(usage_id)
                save_test_data_ids(test_data)
            return usage_id

        # Create usage record
        usage_data = {
            "subscription_id": subscription_id,
            "period_start": period_start,
            "period_end": period_end,
            "total_requests": 0,
            "last_request_at": None,
        }

        response = supabase.table("usage").insert(usage_data).execute()

        if response.data:
            usage_id = response.data[0]["id"]
            print(
                f"Created usage record for subscription {subscription_id} with ID: {usage_id}"
            )

            # Save for cleanup
            test_data["usage"].append(usage_id)
            save_test_data_ids(test_data)

            return usage_id
        else:
            print(f"Failed to create usage record for subscription {subscription_id}")
            return None
    except Exception as e:
        print(f"Error creating usage record for subscription {subscription_id}: {e}")
        return None


def create_api_key(user_id, name="Test API Key"):
    """Create an API key for a user"""
    test_data = load_test_data_ids()
    
    try:
        # Check if API key already exists
        response = (
            supabase.table("api_keys")
            .select("*")
            .eq("user_id", user_id)
            .eq("name", name)
            .execute()
        )
        if response.data:
            print(
                f"API key {name} for user {user_id} already exists, skipping creation"
            )
            api_key_id = response.data[0]["id"]
            
            # For existing keys, we need to create a new one for testing
            # since we can't retrieve the original unhashed key
            api_key = f"sk-kokoro-{uuid.uuid4().hex}"
            key_prefix = api_key[:8]
            
            # Update the existing API key with the new value
            update_data = {
                "key_prefix": key_prefix,
                "key_hash": hash_api_key(api_key),
            }
            
            supabase.table("api_keys").update(update_data).eq("id", api_key_id).execute()
            print(f"Updated API key: {key_prefix}*** for user {user_id}")
            
            # Save for cleanup
            if api_key_id not in test_data["api_keys"]:
                test_data["api_keys"].append(api_key_id)
            
            # Store the actual API key in test_data
            if "basic" in name.lower():
                test_data["test_data"]["basic_tier_api_key"] = api_key
            elif "premium" in name.lower():
                test_data["test_data"]["premium_tier_api_key"] = api_key
                
            save_test_data_ids(test_data)
            return api_key_id, api_key

        # Generate API key
        api_key = f"sk-kokoro-{uuid.uuid4().hex}"
        key_prefix = api_key[:8]

        # Store in database
        api_key_data = {
            "user_id": user_id,
            "name": name,
            "key_prefix": key_prefix,
            "key_hash": hash_api_key(api_key),
            "is_active": True,
        }

        response = supabase.table("api_keys").insert(api_key_data).execute()

        if response.data:
            api_key_id = response.data[0]["id"]
            print(f"Created API key: {key_prefix}*** for user {user_id}")

            # Save for cleanup
            test_data["api_keys"].append(api_key_id)
            
            # Store the actual API key in test_data
            if "basic" in name.lower():
                test_data["test_data"]["basic_tier_api_key"] = api_key
            elif "premium" in name.lower():
                test_data["test_data"]["premium_tier_api_key"] = api_key
                
            save_test_data_ids(test_data)

            return api_key_id, api_key
        else:
            print(f"Failed to create API key for user {user_id}")
            return None, None
    except Exception as e:
        print(f"Error creating API key for user {user_id}: {e}")
        return None, None


def create_demo_user():
    """Create a demo user if it doesn't already exist"""
    test_data = load_test_data_ids()
    
    try:
        # Check if demo user already exists
        existing_user = (
            supabase.table("users")
            .select("id")
            .eq("email", "demo@kokoro.ai")
            .execute()
        )

        if existing_user.data:
            demo_user_id = existing_user.data[0]["id"]
            print(f"Demo user already exists with ID: {demo_user_id}")
            
            # Store the demo user ID in the test data under a special key
            test_data["test_data"]["demo_user_id"] = demo_user_id
            save_test_data_ids(test_data)
            
            return demo_user_id

        # Create the demo user
        demo_user = {
            "email": "demo@kokoro.ai",
            "full_name": "Demo User"
        }

        response = supabase.table("users").insert(demo_user).execute()

        if response.data:
            demo_user_id = response.data[0]["id"]
            print(f"Created demo user with ID: {demo_user_id}")
            
            # Store the demo user ID in the test data under a special key
            # This will prevent it from being deleted during cleanup
            test_data["test_data"]["demo_user_id"] = demo_user_id
            save_test_data_ids(test_data)
            
            return demo_user_id
        else:
            print("Failed to create demo user")
            return None
    except Exception as e:
        print(f"Error creating demo user: {e}")
        return None


def cleanup_test_data():
    """Clean up all test data"""
    test_data = load_test_data_ids()

    print("Cleaning up test data...")

    # Delete in the correct order to respect foreign key constraints
    print("\n--- Deleting API Keys ---")
    for api_key_id in test_data["api_keys"]:
        try:
            supabase.table("api_keys").delete().eq("id", api_key_id).execute()
            print(f"Deleted API key: {api_key_id}")
        except Exception as e:
            print(f"Error deleting API key {api_key_id}: {e}")

    print("\n--- Deleting Usage Records ---")
    # Delete usage records
    for usage_id in test_data["usage"]:
        try:
            supabase.table("usage").delete().eq("id", usage_id).execute()
            print(f"Deleted usage record: {usage_id}")
        except Exception as e:
            print(f"Error deleting usage record {usage_id}: {e}")

    # Delete free usage records
    for usage_id in test_data["free_usage"]:
        try:
            supabase.table("free_usage").delete().eq("id", usage_id).execute()
            print(f"Deleted free usage record: {usage_id}")
        except Exception as e:
            print(f"Error deleting free usage record {usage_id}: {e}")
    
    print("\n--- Deleting Subscriptions ---")
    # Get all subscriptions in the database
    print("Fetching all subscriptions...")
    try:
        response = supabase.table("subscriptions").select("id").execute()
        if response.data:
            for sub in response.data:
                try:
                    supabase.table("subscriptions").delete().eq("id", sub["id"]).execute()
                    print(f"Deleted subscription: {sub['id']}")
                except Exception as e:
                    print(f"Error deleting subscription {sub['id']}: {e}")
    except Exception as e:
        print(f"Error fetching subscriptions: {e}")
    
    # Also delete tracked subscriptions
    for sub_id in test_data["subscriptions"]:
        try:
            supabase.table("subscriptions").delete().eq("id", sub_id).execute()
            print(f"Deleted tracked subscription: {sub_id}")
        except Exception as e:
            print(f"Error deleting tracked subscription {sub_id}: {e}")

    print("\n--- Deleting Products ---")
    # Get all products in the database
    print("Fetching all products...")
    try:
        response = supabase.table("products").select("id").execute()
        if response.data:
            for product in response.data:
                try:
                    supabase.table("products").delete().eq("id", product["id"]).execute()
                    print(f"Deleted product: {product['id']}")
                except Exception as e:
                    print(f"Error deleting product {product['id']}: {e}")
    except Exception as e:
        print(f"Error fetching products: {e}")
    
    # Also delete tracked products
    for product_id in test_data["products"]:
        try:
            supabase.table("products").delete().eq("id", product_id).execute()
            print(f"Deleted tracked product: {product_id}")
        except Exception as e:
            print(f"Error deleting tracked product {product_id}: {e}")

    print("\n--- Deleting Users ---")
    # Get demo user ID to protect it from deletion
    demo_user_id = test_data["test_data"].get("demo_user_id")
    if demo_user_id:
        print(f"Protecting demo user {demo_user_id} from deletion")
    
    # Delete users (should be last, as they might be referenced)
    for user_id in test_data["users"]:
        # Skip the demo user
        if user_id == demo_user_id:
            print(f"Skipping demo user: {user_id}")
            continue
            
        try:
            supabase.table("users").delete().eq("id", user_id).execute()
            print(f"Deleted user: {user_id}")
        except Exception as e:
            print(f"Error deleting user {user_id}: {e}")

    # Delete Stripe resources
    if USE_STRIPE:
        print("\n--- Cleaning up Stripe Resources ---")
        # Delete Stripe subscriptions
        for sub_id in test_data["stripe_subscriptions"]:
            try:
                stripe.Subscription.delete(sub_id)
                print(f"Deleted Stripe subscription: {sub_id}")
            except Exception as e:
                print(f"Error deleting Stripe subscription {sub_id}: {e}")

        # Archive Stripe products instead of deleting them
        for product_id in test_data["stripe_products"]:
            try:
                stripe.Product.modify(product_id, active=False)
                print(f"Archived Stripe product: {product_id}")
            except Exception as e:
                print(f"Error archiving Stripe product {product_id}: {e}")
                
        # Clean up payment methods for customers
        for customer_id in test_data["stripe_customers"]:
            try:
                # Get all payment methods for the customer
                payment_methods = stripe.PaymentMethod.list(
                    customer=customer_id,
                    type="card",
                )
                
                # Detach each payment method
                for pm in payment_methods.data:
                    stripe.PaymentMethod.detach(pm.id)
                    print(f"Detached payment method {pm.id} from customer {customer_id}")
                    
                # Now delete the customer
                stripe.Customer.delete(customer_id)
                print(f"Deleted Stripe customer: {customer_id}")
            except Exception as e:
                print(f"Error cleaning up Stripe customer {customer_id}: {e}")

    # Reset test data using the default structure
    # We'll use the load_test_data_ids function to get a fresh default structure
    fresh_data = load_test_data_ids()
    # Clear all lists but keep the test_data structure with None values
    for key in fresh_data:
        if isinstance(fresh_data[key], list):
            fresh_data[key] = []
    
    save_test_data_ids(fresh_data)

    print("Test data cleanup complete!")


def report_usage_example(subscription_id, quantity):
    """Utility function to report metered usage to Stripe"""
    if not USE_STRIPE:
        print("Stripe integration is not enabled, usage reporting is mocked")
        return

    try:
        # Get the subscription to find the subscription item for the metered component
        subscription = stripe.Subscription.retrieve(subscription_id)

        # Find the subscription item for the metered component
        metered_item = None
        for item in subscription.items.data:
            # The metered item is the one with usage_type='metered'
            if item.price.recurring.usage_type == "metered":
                metered_item = item.id
                break

        if not metered_item:
            print(
                f"No metered subscription item found for subscription {subscription_id}"
            )
            return

        # Report usage
        usage_record = stripe.SubscriptionItem.create_usage_record(
            metered_item,
            quantity=quantity,
            timestamp=int(time.time()),
            action="increment",
        )

        print(
            f"Reported usage of {quantity} characters for subscription {subscription_id}"
        )
        print(f"Usage record ID: {usage_record.id}")
        return usage_record.id

    except Exception as e:
        print(f"Error reporting usage for subscription {subscription_id}: {e}")
        return None


def create_test_data():
    """Create test data in Supabase and Stripe"""
    print("Setting up test data in Supabase and Stripe...")
    print(f"Stripe integration is {'ENABLED' if USE_STRIPE else 'DISABLED (MOCK MODE)'}")

    # Create a demo user (this user won't be cleaned up as it's used by the system)
    demo_user_id = create_demo_user()
    if demo_user_id:
        print(f"Demo user created/verified with ID: {demo_user_id}")

    # Load existing test data
    test_data = load_test_data_ids()

    # Create test users
    print("\n--- Creating Users ---")
    free_user_id, free_customer_id = create_test_user("free-user@example.com", "Free Tier User")
    print(f"Free user ID: {free_user_id}, Stripe Customer ID: {free_customer_id}")
    
    basic_user_id, basic_customer_id = create_test_user(
        "basic-user@example.com", "Basic Tier User"
    )
    print(f"Basic user ID: {basic_user_id}, Stripe Customer ID: {basic_customer_id}")
    
    premium_user_id, premium_customer_id = create_test_user(
        "premium-user@example.com", "Premium Tier User"
    )
    print(f"Premium user ID: {premium_user_id}, Stripe Customer ID: {premium_customer_id}")

    # Save user IDs to test data
    test_data["test_data"]["free_tier_user_id"] = free_user_id
    test_data["test_data"]["basic_tier_user_id"] = basic_user_id
    test_data["test_data"]["premium_tier_user_id"] = premium_user_id

    # Create products
    print("\n--- Creating Products ---")
    free_product_id, free_stripe_product_id, free_stripe_price_id, _ = create_product(
        "Free Tier", "Limited usage, no cost", 0.00, 100
    )
    print(f"Free product ID: {free_product_id}, Stripe Product ID: {free_stripe_product_id}, Stripe Price ID: {free_stripe_price_id}")
    
    basic_product_id, basic_stripe_product_id, basic_price_id, _ = create_product(
        "Basic Tier", "Standard usage, monthly fee", 9.99, 1000
    )
    print(f"Basic product ID: {basic_product_id}, Stripe Product ID: {basic_stripe_product_id}, Stripe Price ID: {basic_price_id}")
    
    premium_product_id, premium_stripe_product_id, premium_price_id, premium_metered_price_id = create_product(
        "Premium Tier",
        "Premium tier with 1M characters included and pay-as-you-go overage",
        29.99,
        1000000,  # 1 million characters included
    )
    print(f"Premium product ID: {premium_product_id}, Stripe Product ID: {premium_stripe_product_id}, Stripe Price ID: {premium_price_id}, Metered Price ID: {premium_metered_price_id}")

    # Save product IDs to test data
    test_data["test_data"]["free_product_id"] = free_product_id
    test_data["test_data"]["basic_product_id"] = basic_product_id
    test_data["test_data"]["premium_product_id"] = premium_product_id

    # Initialize subscription variables
    free_subscription_id = None
    basic_subscription_id = None
    premium_subscription_id = None
    premium_stripe_sub_id = None

    # Create subscriptions
    print("\n--- Creating Subscriptions ---")
    if free_user_id and free_product_id:
        # Free users don't have Stripe subscriptions
        print(f"Creating free subscription for user {free_user_id}...")
        free_subscription_id, free_stripe_sub_id = create_subscription(free_user_id, free_product_id)
        print(f"Free subscription ID: {free_subscription_id}, Stripe Sub ID: {free_stripe_sub_id}")

    if basic_user_id and basic_product_id and basic_customer_id and basic_price_id:
        print(f"Creating basic subscription for user {basic_user_id} with Stripe customer {basic_customer_id}...")
        basic_subscription_id, basic_stripe_sub_id = create_subscription(
            basic_user_id, basic_product_id, basic_customer_id, basic_price_id
        )
        print(f"Basic subscription ID: {basic_subscription_id}, Stripe Sub ID: {basic_stripe_sub_id}")

    if (
        premium_user_id
        and premium_product_id
        and premium_customer_id
        and premium_price_id
    ):
        print(f"Creating premium subscription for user {premium_user_id} with Stripe customer {premium_customer_id}...")
        premium_subscription_id, premium_stripe_sub_id = create_subscription(
            premium_user_id,
            premium_product_id,
            premium_customer_id,
            premium_price_id,
            premium_metered_price_id,
        )
        print(f"Premium subscription ID: {premium_subscription_id}, Stripe Sub ID: {premium_stripe_sub_id}")
        
        # Save premium subscription ID to test data
        test_data["test_data"]["premium_subscription_id"] = premium_stripe_sub_id

    # Create API keys
    print("\n--- Creating API Keys ---")
    basic_api_key = None
    premium_api_key = None

    if basic_user_id:
        _, basic_api_key = create_api_key(basic_user_id, "Basic Tier API Key")
        print(f"Basic tier API key created: {basic_api_key}")
        # Save API key to test data
        test_data["test_data"]["basic_tier_api_key"] = basic_api_key

    if premium_user_id:
        _, premium_api_key = create_api_key(premium_user_id, "Premium Tier API Key")
        print(f"Premium tier API key created: {premium_api_key}")
        # Save API key to test data
        test_data["test_data"]["premium_tier_api_key"] = premium_api_key

    # Save the updated test data
    save_test_data_ids(test_data)

    # Create free usage record for free user
    print("\n--- Creating Usage Records ---")
    if free_user_id:
        now = datetime.datetime.utcnow()
        period_start = now.isoformat()
        period_end = (now + datetime.timedelta(days=30)).isoformat()
        free_usage_id = create_free_usage_record(free_user_id, period_start, period_end)
        print(f"Free usage record created: {free_usage_id}")

    # Print test data for use in testing
    print("\n=== Test Data for API Testing ===")
    print(f"Free Tier User ID: {free_user_id}")
    
    if basic_api_key:
        # Show masked version in console output
        basic_masked = f"{basic_api_key[:10]}...{basic_api_key[-4:]}"
        print(f"Basic Tier API Key: {basic_masked}")
    
    if premium_api_key:
        # Show masked version in console output
        premium_masked = f"{premium_api_key[:10]}...{premium_api_key[-4:]}"
        print(f"Premium Tier API Key: {premium_masked}")
        
    if USE_STRIPE and premium_stripe_sub_id:
        print(f"\nPremium Tier Stripe Subscription ID: {premium_stripe_sub_id}")
        print(f"\nTo report metered usage for Premium tier, use:")
        print(
            f"report_usage_example('{premium_stripe_sub_id}', 50000)  # Report 50,000 characters"
        )
    print("=================================\n")
    
    print("Test data saved to:", TEST_DATA_FILE)
    print("Test data setup complete!")


def main():
    """Main function"""
    if not args.create and not args.cleanup:
        print(
            "No action specified. Use --create to create test data or --cleanup to clean up test data."
        )
        return

    if args.cleanup:
        cleanup_test_data()

    if args.create:
        create_test_data()


if __name__ == "__main__":
    main()
