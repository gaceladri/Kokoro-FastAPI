# Kokoro TTS API Test Data Setup

This document explains how to use the test data setup script for the Kokoro TTS API. The script creates test users, products, subscriptions, and API keys in Supabase and Stripe for testing purposes.

## Prerequisites

- Python 3.7+
- Access to Supabase project
- (Optional) Stripe API credentials for subscription testing

## Configuration

The script requires environment variables to be set. Create a `.env` file in the `config` directory with the following variables:

```
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_key
STRIPE_SECRET_KEY=your_stripe_secret_key (optional)
```

If `STRIPE_SECRET_KEY` is not provided, the script will mock Stripe interactions.

## Usage

To create test data:

```python
python setup_test_data.py
```

To remove test data:

```python
python setup_test_data.py cleanup
```

## Test Data Structure

The script creates the following test data:

### Users

- Free Tier User: A user with access to the free tier
- Basic Tier User: A user with a subscription to the basic tier
- Premium Tier User: A user with a subscription to the premium tier

### Products

- Free Tier: Limited to 5 requests per month, no credit card required
- Basic Tier: $5/month for 100 requests per month (1 million characters)
- Premium Tier: $50/month for 1 million characters per month with metered billing for overages

### Subscriptions

Subscriptions are created for the Basic and Premium tier users. The Premium tier subscription includes metered billing for characters beyond the included limit.

### API Keys

Each user is assigned an API key for authentication.

## Metered Billing with Stripe

The Premium tier includes metered billing functionality for character usage beyond the included limit. The billing structure is:

- Base subscription price: $50/month for 1 million characters
- Metered usage: $10 per additional million characters (prorated)

To report usage for a premium subscription, you can use the `report_usage_example` function:

```python
from setup_test_data import report_usage_example

# Report 50,000 characters usage for a premium subscription
report_usage_example(premium_subscription_id, 50000)
```

The system will only report billable usage to Stripe when the user exceeds their included character limit.

## Schema Modifications

The script has updated the database schema to support character usage tracking and metered billing:

1. Added a `stripe_metered_price_id` column to the `products` table.
2. Added columns to the `usage` table to track character usage:
   - `characters_used`: The number of characters processed
   - `reported_to_stripe`: Whether this usage has been reported to Stripe
   - `stripe_usage_record_id`: The ID of the usage record in Stripe

3. Added a `characters_used` column to the `free_usage` table for free tier users.

## Usage Tracking

The `track_character_usage.py` utility provides functions to track character usage:

```python
from track_character_usage import track_usage, get_usage_stats

# Track 5000 characters of usage for a paid user
track_usage(user_id, "Some text that is 5000 characters long...")

# Get current usage statistics
stats = get_usage_stats(user_id)
print(stats)
```

The tracking system will:
1. Count the characters in each request
2. Accumulate usage in the `usage` table (or `free_usage` for free tier)
3. Report billable usage to Stripe when necessary
4. Provide usage statistics including remaining character limits

## Implementation Notes

- The system assumes monthly billing periods starting on the 1st of each month
- For premium tier, only usage beyond the included limit is reported to Stripe
- Usage is reported to Stripe using the `set` action, which sets the absolute value rather than incrementing

## Cleanup

Running the script with the `cleanup` argument will remove all test data from Supabase and Stripe, including:

- Test users
- Products created by the script
- Subscriptions for test users
- API keys for test users
- Usage records for test subscriptions

This ensures that your testing environment remains clean. 