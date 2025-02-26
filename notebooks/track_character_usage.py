#!/usr/bin/env python3
"""
Utility functions for tracking character usage in the Kokoro TTS API.

This module provides functions to:
1. Track character usage for a subscription
2. Report usage to Stripe for metered billing
3. Get current usage statistics for a user/subscription

Usage:
    from track_character_usage import track_usage, get_usage_stats
"""

import os
import time
import datetime
from dotenv import load_dotenv
from supabase import create_client
import stripe
import pathlib

# Get the script directory and construct absolute path to .env file
script_dir = pathlib.Path(__file__).parent.absolute()
env_path = script_dir.parent / "config" / ".env.example"

# Load environment variables from .env file
load_dotenv(env_path)

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

# Initialize Supabase client
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def track_usage(user_id, text_content, is_paid=True):
    """
    Track character usage for a user.
    
    Args:
        user_id (str): The ID of the user
        text_content (str): The text being processed
        is_paid (bool): Whether this is a paid tier user (vs. free tier)
        
    Returns:
        dict: The usage record data
    """
    # Count characters
    character_count = len(text_content)
    
    # Get current time
    now = datetime.datetime.utcnow()
    
    # Determine current billing period
    period_start = datetime.datetime(now.year, now.month, 1).isoformat()
    next_month = now.month + 1 if now.month < 12 else 1
    next_year = now.year if now.month < 12 else now.year + 1
    period_end = datetime.datetime(next_year, next_month, 1).isoformat()
    
    if is_paid:
        # Get user's active subscription
        subscription_response = supabase.table("subscriptions").select("*").eq("user_id", user_id).eq("status", "active").execute()
        
        if not subscription_response.data:
            print(f"No active subscription found for user {user_id}")
            return None
            
        subscription_id = subscription_response.data[0]["id"]
        
        # Check if usage record exists for this period
        usage_response = supabase.table("usage").select("*").eq("subscription_id", subscription_id).eq("period_start", period_start).execute()
        
        if usage_response.data:
            # Update existing usage record
            usage_id = usage_response.data[0]["id"]
            current_total_requests = usage_response.data[0]["total_requests"]
            current_characters_used = usage_response.data[0].get("characters_used", 0)
            new_characters_used = current_characters_used + character_count
            
            update_response = supabase.table("usage").update({
                "total_requests": current_total_requests + 1,
                "characters_used": new_characters_used,
                "last_request_at": now.isoformat()
            }).eq("id", usage_id).execute()
            
            usage_record = update_response.data[0] if update_response.data else None
        else:
            # Create new usage record
            usage_data = {
                "subscription_id": subscription_id,
                "period_start": period_start,
                "period_end": period_end,
                "total_requests": 1,
                "characters_used": character_count,
                "last_request_at": now.isoformat(),
                "reported_to_stripe": False
            }
            
            insert_response = supabase.table("usage").insert(usage_data).execute()
            usage_record = insert_response.data[0] if insert_response.data else None
        
        # Check if we need to report to Stripe
        check_and_report_to_stripe(usage_record, subscription_id)
        
        return usage_record
    else:
        # For free tier users, update the free_usage table
        free_usage_response = supabase.table("free_usage").select("*").eq("user_id", user_id).eq("period_start", period_start).execute()
        
        if free_usage_response.data:
            # Update existing usage record
            usage_id = free_usage_response.data[0]["id"]
            current_total_requests = free_usage_response.data[0]["total_requests"]
            current_characters_used = free_usage_response.data[0].get("characters_used", 0)
            new_characters_used = current_characters_used + character_count
            
            update_response = supabase.table("free_usage").update({
                "total_requests": current_total_requests + 1,
                "characters_used": new_characters_used,
                "last_request_at": now.isoformat()
            }).eq("id", usage_id).execute()
            
            return update_response.data[0] if update_response.data else None
        else:
            # Create new usage record
            usage_data = {
                "user_id": user_id,
                "period_start": period_start,
                "period_end": period_end,
                "total_requests": 1,
                "characters_used": character_count,
                "last_request_at": now.isoformat()
            }
            
            insert_response = supabase.table("free_usage").insert(usage_data).execute()
            return insert_response.data[0] if insert_response.data else None

def check_and_report_to_stripe(usage_record, subscription_id):
    """
    Check if usage should be reported to Stripe and report if necessary.
    
    Args:
        usage_record (dict): The usage record from the database
        subscription_id (str): The subscription ID
        
    Returns:
        bool: True if usage was reported, False otherwise
    """
    if not USE_STRIPE or not usage_record:
        return False
        
    # Check if we've already reported this period
    if usage_record.get("reported_to_stripe"):
        return False
        
    # Get subscription details from Supabase
    subscription_response = supabase.table("subscriptions").select("*").eq("id", subscription_id).execute()
    
    if not subscription_response.data:
        print(f"Subscription {subscription_id} not found")
        return False
        
    subscription = subscription_response.data[0]
    stripe_subscription_id = subscription.get("stripe_subscription_id")
    
    if not stripe_subscription_id:
        print(f"No Stripe subscription ID found for subscription {subscription_id}")
        return False
    
    # Get product details to check character limit
    product_id = subscription.get("product_id")
    product_response = supabase.table("products").select("*").eq("id", product_id).execute()
    
    if not product_response.data:
        print(f"Product not found for subscription {subscription_id}")
        return False
        
    product = product_response.data[0]
    character_limit = product.get("monthly_request_limit", 0)
    
    # Only report usage if we're exceeding the included limit
    if usage_record.get("characters_used", 0) <= character_limit:
        print(f"Usage ({usage_record.get('characters_used', 0)}) is within the character limit ({character_limit})")
        return False
    
    # Calculate billable usage (beyond the included limit)
    billable_characters = usage_record.get("characters_used", 0) - character_limit
    
    try:
        # Get the Stripe subscription
        stripe_sub = stripe.Subscription.retrieve(stripe_subscription_id)
        
        # Find the metered subscription item
        metered_item_id = None
        for item in stripe_sub.items.data:
            if hasattr(item.price, 'recurring') and hasattr(item.price.recurring, 'usage_type') and item.price.recurring.usage_type == 'metered':
                metered_item_id = item.id
                break
                
        if not metered_item_id:
            print(f"No metered subscription item found for subscription {stripe_subscription_id}")
            return False
            
        # Report usage to Stripe
        usage_record_response = stripe.SubscriptionItem.create_usage_record(
            metered_item_id,
            quantity=billable_characters,
            timestamp=int(time.time()),
            action='set'  # Use 'set' to set the absolute value, not increment
        )
        
        # Update the database record
        supabase.table("usage").update({
            "reported_to_stripe": True,
            "stripe_usage_record_id": usage_record_response.id
        }).eq("id", usage_record["id"]).execute()
        
        print(f"Reported {billable_characters} billable characters to Stripe for subscription {stripe_subscription_id}")
        return True
        
    except Exception as e:
        print(f"Error reporting usage to Stripe: {e}")
        return False

def get_usage_stats(user_id):
    """
    Get usage statistics for a user.
    
    Args:
        user_id (str): The ID of the user
        
    Returns:
        dict: Usage statistics
    """
    # Get current time
    now = datetime.datetime.utcnow()
    
    # Determine current billing period
    period_start = datetime.datetime(now.year, now.month, 1).isoformat()
    
    # Check if user has a paid subscription
    subscription_response = supabase.table("subscriptions").select("*").eq("user_id", user_id).eq("status", "active").execute()
    
    if subscription_response.data:
        # Paid user
        subscription_id = subscription_response.data[0]["id"]
        product_id = subscription_response.data[0]["product_id"]
        
        # Get product details
        product_response = supabase.table("products").select("*").eq("id", product_id).execute()
        product = product_response.data[0] if product_response.data else None
        
        # Get usage
        usage_response = supabase.table("usage").select("*").eq("subscription_id", subscription_id).eq("period_start", period_start).execute()
        usage = usage_response.data[0] if usage_response.data else {"total_requests": 0, "characters_used": 0}
        
        character_limit = product.get("monthly_request_limit", 0) if product else 0
        remaining_characters = max(0, character_limit - usage.get("characters_used", 0))
        
        return {
            "is_paid": True,
            "tier": product.get("name") if product else "Unknown",
            "total_requests": usage.get("total_requests", 0),
            "total_characters": usage.get("characters_used", 0),
            "character_limit": character_limit,
            "remaining_characters": remaining_characters,
            "metered_usage": max(0, usage.get("characters_used", 0) - character_limit),
            "period_start": period_start
        }
    else:
        # Free user
        free_usage_response = supabase.table("free_usage").select("*").eq("user_id", user_id).eq("period_start", period_start).execute()
        free_usage = free_usage_response.data[0] if free_usage_response.data else {"total_requests": 0, "characters_used": 0}
        
        # Get free tier details
        free_product_response = supabase.table("products").select("*").eq("name", "Free Tier").execute()
        free_product = free_product_response.data[0] if free_product_response.data else None
        
        request_limit = free_product.get("monthly_request_limit", 0) if free_product else 0
        remaining_requests = max(0, request_limit - free_usage.get("total_requests", 0))
        
        return {
            "is_paid": False,
            "tier": "Free",
            "total_requests": free_usage.get("total_requests", 0),
            "total_characters": free_usage.get("characters_used", 0),
            "request_limit": request_limit,
            "remaining_requests": remaining_requests,
            "period_start": period_start
        }

# Example usage
if __name__ == "__main__":
    # Get user ID from command line
    import sys
    if len(sys.argv) < 2:
        print("Usage: python track_character_usage.py <user_id> [text]")
        exit(1)
        
    user_id = sys.argv[1]
    
    if len(sys.argv) > 2:
        # Track usage for provided text
        text = sys.argv[2]
        print(f"Tracking usage for text: {text[:30]}... ({len(text)} characters)")
        track_usage(user_id, text)
    
    # Display usage stats
    stats = get_usage_stats(user_id)
    print("\nUsage Statistics:")
    for key, value in stats.items():
        print(f"{key}: {value}") 