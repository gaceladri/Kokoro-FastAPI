#!/usr/bin/env python3
"""
Setup test data in Supabase for Kokoro TTS API testing.

This script creates:
1. Test users (free tier and paid tier)
2. Products
3. Subscriptions
4. API keys

Usage:
    python setup_test_data.py
"""

import os
import uuid
import hashlib
import datetime
from dotenv import load_dotenv
from supabase import create_client

# Load environment variables from .env file
load_dotenv("../config/.env")

# Supabase configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("Error: SUPABASE_URL and SUPABASE_KEY must be set in ../config/.env")
    exit(1)

# Initialize Supabase client
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def hash_api_key(api_key):
    """Hash an API key for storage"""
    return hashlib.sha256(api_key.encode()).hexdigest()

def create_test_user(email, full_name):
    """Create a test user in Supabase"""
    try:
        # Check if user already exists
        response = supabase.table("users").select("*").eq("email", email).execute()
        if response.data:
            print(f"User with email {email} already exists, skipping creation")
            return response.data[0]["id"]
        
        # Create user
        user_data = {
            "email": email,
            "full_name": full_name
        }
        
        response = supabase.table("users").insert(user_data).execute()
        
        if response.data:
            user_id = response.data[0]["id"]
            print(f"Created user: {full_name} ({email}) with ID: {user_id}")
            return user_id
        else:
            print(f"Failed to create user: {email}")
            return None
    except Exception as e:
        print(f"Error creating user {email}: {e}")
        return None

def create_product(name, description, price, request_limit=None):
    """Create a product in Supabase"""
    try:
        # Check if product already exists
        response = supabase.table("products").select("*").eq("name", name).execute()
        if response.data:
            print(f"Product {name} already exists, skipping creation")
            return response.data[0]["id"]
        
        # Create product
        product_data = {
            "name": name,
            "description": description,
            "price": price,
            "stripe_product_id": f"prod_{uuid.uuid4().hex[:8]}",
            "stripe_price_id": f"price_{uuid.uuid4().hex[:8]}",
            "active": True,
            "monthly_request_limit": request_limit
        }
        
        response = supabase.table("products").insert(product_data).execute()
        
        if response.data:
            product_id = response.data[0]["id"]
            print(f"Created product: {name} (${price}) with ID: {product_id}")
            return product_id
        else:
            print(f"Failed to create product: {name}")
            return None
    except Exception as e:
        print(f"Error creating product {name}: {e}")
        return None

def create_subscription(user_id, product_id, status="active"):
    """Create a subscription in Supabase"""
    try:
        # Check if subscription already exists
        response = supabase.table("subscriptions").select("*").eq("user_id", user_id).eq("product_id", product_id).execute()
        if response.data:
            print(f"Subscription for user {user_id} and product {product_id} already exists, skipping creation")
            return response.data[0]["id"]
        
        # Set subscription period
        now = datetime.datetime.utcnow()
        period_start = now.isoformat()
        period_end = (now + datetime.timedelta(days=30)).isoformat()
        
        # Create subscription
        subscription_data = {
            "user_id": user_id,
            "product_id": product_id,
            "stripe_subscription_id": f"sub_{uuid.uuid4().hex[:8]}",
            "stripe_price_id": f"price_{uuid.uuid4().hex[:8]}",
            "status": status,
            "current_period_start": period_start,
            "current_period_end": period_end,
            "cancel_at_period_end": False
        }
        
        response = supabase.table("subscriptions").insert(subscription_data).execute()
        
        if response.data:
            subscription_id = response.data[0]["id"]
            print(f"Created subscription for user {user_id} with ID: {subscription_id}")
            return subscription_id
        else:
            print(f"Failed to create subscription for user {user_id}")
            return None
    except Exception as e:
        print(f"Error creating subscription for user {user_id}: {e}")
        return None

def create_api_key(user_id, name="Test API Key"):
    """Create an API key for a user"""
    try:
        # Generate API key
        api_key = f"sk-kokoro-{uuid.uuid4().hex}"
        key_prefix = api_key[:8]
        
        # Check if API key already exists
        response = supabase.table("api_keys").select("*").eq("user_id", user_id).eq("name", name).execute()
        if response.data:
            print(f"API key {name} for user {user_id} already exists, skipping creation")
            return response.data[0]["id"], api_key
        
        # Store in database
        api_key_data = {
            "user_id": user_id,
            "name": name,
            "key_prefix": key_prefix,
            "key_hash": hash_api_key(api_key),
            "is_active": True
        }
        
        response = supabase.table("api_keys").insert(api_key_data).execute()
        
        if response.data:
            api_key_id = response.data[0]["id"]
            print(f"Created API key: {api_key} for user {user_id}")
            return api_key_id, api_key
        else:
            print(f"Failed to create API key for user {user_id}")
            return None, None
    except Exception as e:
        print(f"Error creating API key for user {user_id}: {e}")
        return None, None

def main():
    """Main function to set up test data"""
    print("Setting up test data in Supabase...")
    
    # Create test users
    free_user_id = create_test_user("free-user@example.com", "Free Tier User")
    basic_user_id = create_test_user("basic-user@example.com", "Basic Tier User")
    premium_user_id = create_test_user("premium-user@example.com", "Premium Tier User")
    
    # Create products
    free_product_id = create_product("Free Tier", "Limited usage, no cost", 0.00, 100)
    basic_product_id = create_product("Basic Tier", "Standard usage, monthly fee", 9.99, 1000)
    premium_product_id = create_product("Premium Tier", "Unlimited usage, monthly fee", 29.99, None)
    
    # Create subscriptions
    if basic_user_id and basic_product_id:
        basic_subscription_id = create_subscription(basic_user_id, basic_product_id)
    
    if premium_user_id and premium_product_id:
        premium_subscription_id = create_subscription(premium_user_id, premium_product_id)
    
    # Create API keys
    if basic_user_id:
        _, basic_api_key = create_api_key(basic_user_id, "Basic Tier API Key")
    
    if premium_user_id:
        _, premium_api_key = create_api_key(premium_user_id, "Premium Tier API Key")
    
    # Print test data for use in the notebook
    print("\n=== Test Data for Notebook ===")
    print(f"Free Tier User ID: {free_user_id}")
    print(f"Basic Tier API Key: {basic_api_key}")
    print(f"Premium Tier API Key: {premium_api_key}")
    print("==============================\n")
    
    print("Test data setup complete!")

if __name__ == "__main__":
    main() 