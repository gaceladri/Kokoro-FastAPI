#!/usr/bin/env python3
"""
Kokoro TTS API Testing

This script tests the Kokoro TTS API with different user types:
1. Demo users (no authentication)
2. Free tier users (with user ID)
3. Paid tier users (with API key)

Make sure your API is running locally before executing this script.
"""

# %% [markdown]
# # Kokoro TTS API Testing
# 
# This notebook tests the Kokoro TTS API with different user types:
# 1. Demo users (no authentication)
# 2. Free tier users (with user ID)
# 3. Paid tier users (with API key)
# 
# Make sure your API is running locally before executing this notebook.

# %%
import requests
import json
import os
import time
import matplotlib.pyplot as plt
import numpy as np
import IPython.display as ipd
from pydub import AudioSegment
from io import BytesIO

# API configuration
API_BASE_URL = "http://localhost:8880"

# Test data
TEST_TEXT = "This is a test of the Kokoro TTS API. It's working great!"
TEST_VOICE = "en_US_001"

# %% [markdown]
# ## Helper Functions

# %%
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
    # 1. Check available voices
    response = requests.get(f"{API_BASE_URL}/v1/audio/voices")
    print("Available voices:")
    print(display_json(response.json()))
    
    # 2. Generate speech
    payload = {
        "model": "tts-1",
        "input": TEST_TEXT,
        "voice": TEST_VOICE,
        "response_format": "mp3"
    }
    
    response = requests.post(
        f"{API_BASE_URL}/v1/audio/speech",
        json=payload
    )
    
    if response.status_code == 200:
        print("Speech generated successfully!")
        audio = play_audio(response.content)
        return audio
    else:
        print(f"Error: {response.status_code}")
        print(display_json(response.json()))
        return None
    
# Test demo user
demo_audio = test_demo_user()

# %% [markdown]
# ## 2. Check Demo Usage Statistics

# %%
def check_demo_usage():
    """Check usage statistics for demo user"""
    response = requests.get(f"{API_BASE_URL}/v1/usage")
    
    if response.status_code == 200:
        print("Demo usage statistics:")
        print(display_json(response.json()))
        return response.json()
    else:
        print(f"Error: {response.status_code}")
        print(display_json(response.json()))
        return None

# Check demo usage
demo_usage = check_demo_usage()

# %% [markdown]
# ## 3. Testing Free Tier User (With User ID)

# %%
# You can use a UUID from your database or generate a new one
FREE_TIER_USER_ID = "00000000-0000-0000-0000-000000000001"  # Replace with actual user ID

def test_free_tier_user(user_id):
    """Test the API as a free tier user (with user ID)"""
    # Generate speech
    payload = {
        "model": "tts-1",
        "input": TEST_TEXT,
        "voice": TEST_VOICE,
        "response_format": "mp3"
    }
    
    headers = {
        "X-User-ID": user_id
    }
    
    response = requests.post(
        f"{API_BASE_URL}/v1/audio/speech",
        json=payload,
        headers=headers
    )
    
    if response.status_code == 200:
        print("Speech generated successfully!")
        audio = play_audio(response.content)
        return audio
    else:
        print(f"Error: {response.status_code}")
        print(display_json(response.json()))
        return None

# Test free tier user
free_tier_audio = test_free_tier_user(FREE_TIER_USER_ID)

# %% [markdown]
# ## 4. Check Free Tier Usage Statistics

# %%
def check_free_tier_usage(user_id):
    """Check usage statistics for free tier user"""
    headers = {
        "X-User-ID": user_id
    }
    
    response = requests.get(
        f"{API_BASE_URL}/v1/usage",
        headers=headers
    )
    
    if response.status_code == 200:
        print("Free tier usage statistics:")
        print(display_json(response.json()))
        return response.json()
    else:
        print(f"Error: {response.status_code}")
        print(display_json(response.json()))
        return None

# Check free tier usage
free_tier_usage = check_free_tier_usage(FREE_TIER_USER_ID)

# %% [markdown]
# ## 5. Testing Paid Tier User (With API Key)

# %%
# Replace with an actual API key from your database
API_KEY = "sk-kokoro-xxxxxxxxxxxxxxxxxxxxxxxxxxxx"  

def test_paid_tier_user(api_key):
    """Test the API as a paid tier user (with API key)"""
    # Generate speech
    payload = {
        "model": "tts-1",
        "input": TEST_TEXT,
        "voice": TEST_VOICE,
        "response_format": "mp3"
    }
    
    headers = {
        "X-API-Key": api_key
    }
    
    response = requests.post(
        f"{API_BASE_URL}/v1/audio/speech",
        json=payload,
        headers=headers
    )
    
    if response.status_code == 200:
        print("Speech generated successfully!")
        audio = play_audio(response.content)
        return audio
    else:
        print(f"Error: {response.status_code}")
        print(display_json(response.json()))
        return None

# Test paid tier user
# Uncomment the line below and replace with a valid API key to test
# paid_tier_audio = test_paid_tier_user(API_KEY)

# %% [markdown]
# ## 6. Check Paid Tier Usage Statistics

# %%
def check_paid_tier_usage(api_key):
    """Check usage statistics for paid tier user"""
    headers = {
        "X-API-Key": api_key
    }
    
    response = requests.get(
        f"{API_BASE_URL}/v1/usage",
        headers=headers
    )
    
    if response.status_code == 200:
        print("Paid tier usage statistics:")
        print(display_json(response.json()))
        return response.json()
    else:
        print(f"Error: {response.status_code}")
        print(display_json(response.json()))
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
        "response_format": "mp3"
    }
    
    for i in range(num_requests):
        print(f"Request {i+1}/{num_requests}...")
        
        response = requests.post(
            f"{API_BASE_URL}/v1/audio/speech",
            json=payload
        )
        
        results.append({
            "status_code": response.status_code,
            "success": response.status_code == 200,
            "response": response.json() if response.status_code != 200 else "Success"
        })
        
        # Check usage after each request
        usage_response = requests.get(f"{API_BASE_URL}/v1/usage")
        if usage_response.status_code == 200:
            print(f"Usage after request {i+1}:")
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
from supabase import create_client
import uuid

# Replace with your Supabase credentials
SUPABASE_URL = "https://your-project-id.supabase.co"
SUPABASE_KEY = "your-supabase-key"

def create_test_user():
    """Create a test user in Supabase"""
    try:
        # Initialize Supabase client
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        
        # Generate a unique email
        email = f"test-{uuid.uuid4()}@example.com"
        
        # Create user
        user_data = {
            "email": email,
            "full_name": "Test User"
        }
        
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
    try:
        # Initialize Supabase client
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        
        # Generate API key
        api_key = f"sk-kokoro-{uuid.uuid4()}"
        key_prefix = api_key[:8]
        
        # Store in database
        api_key_data = {
            "user_id": user_id,
            "name": "Test API Key",
            "key_prefix": key_prefix,
            "key_hash": api_key,  # In production, you'd hash this
            "is_active": True
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

# Uncomment to create a test user and API key
# test_user_id = create_test_user()
# if test_user_id:
#     test_api_key = create_api_key(test_user_id)

# %% [markdown]
# ## 9. Visualize Usage Data

# %%
def visualize_usage_comparison():
    """Visualize usage comparison between different user tiers"""
    # Get usage data for each tier
    demo_data = check_demo_usage()
    free_data = check_free_tier_usage(FREE_TIER_USER_ID)
    
    # Prepare data for visualization
    tiers = ['Demo', 'Free Tier']
    usage_values = [
        demo_data.get('monthly_characters', 0) if demo_data else 0,
        free_data.get('total_requests', 0) if free_data else 0
    ]
    
    limits = [
        demo_data.get('monthly_limit', 0) if demo_data else 0,
        free_data.get('request_limit', 0) if free_data else 0
    ]
    
    # Create bar chart
    fig, ax = plt.subplots(figsize=(10, 6))
    
    x = np.arange(len(tiers))
    width = 0.35
    
    ax.bar(x - width/2, usage_values, width, label='Current Usage')
    ax.bar(x + width/2, limits, width, label='Limit')
    
    ax.set_ylabel('Characters')
    ax.set_title('Usage Comparison by Tier')
    ax.set_xticks(x)
    ax.set_xticklabels(tiers)
    ax.legend()
    
    # Add usage percentage labels
    for i, v in enumerate(usage_values):
        if limits[i] > 0:
            percentage = (v / limits[i]) * 100
            ax.text(i - width/2, v + 0.1, f'{percentage:.1f}%', ha='center')
    
    plt.tight_layout()
    plt.show()

# Uncomment to visualize usage comparison
# visualize_usage_comparison()

# %% [markdown]
# ## 10. Test with Different Voice Options

# %%
def test_different_voices(user_id=None, api_key=None):
    """Test the API with different voice options"""
    # Get available voices
    response = requests.get(f"{API_BASE_URL}/v1/audio/voices")
    voices = response.json().get("voices", [])
    
    if not voices:
        print("No voices available")
        return
    
    # Select a few voices to test
    test_voices = voices[:3] if len(voices) > 3 else voices
    
    print(f"Testing {len(test_voices)} voices: {', '.join(test_voices)}")
    
    results = {}
    
    # Prepare headers based on authentication type
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
    elif user_id:
        headers["X-User-ID"] = user_id
    
    # Test each voice
    for voice in test_voices:
        print(f"\nTesting voice: {voice}")
        
        payload = {
            "model": "tts-1",
            "input": TEST_TEXT,
            "voice": voice,
            "response_format": "mp3"
        }
        
        response = requests.post(
            f"{API_BASE_URL}/v1/audio/speech",
            json=payload,
            headers=headers
        )
        
        if response.status_code == 200:
            print(f"Voice {voice} generated successfully!")
            audio = play_audio(response.content)
            results[voice] = audio
        else:
            print(f"Error with voice {voice}: {response.status_code}")
            try:
                print(display_json(response.json()))
            except:
                print("Could not parse response as JSON")
    
    return results

# Uncomment one of these lines to test different voices
# demo_voices = test_different_voices()
# free_tier_voices = test_different_voices(user_id=FREE_TIER_USER_ID)
# paid_tier_voices = test_different_voices(api_key=API_KEY)

# %% [markdown]
# ## 11. Test with Different Text Lengths

# %%
def test_text_lengths(user_id=None, api_key=None):
    """Test the API with different text lengths"""
    # Define test texts of different lengths
    test_texts = [
        "Short text.",
        "This is a medium length text that should work fine with the API.",
        "This is a longer text that will test the character limits. " * 5,
        "This is a very long text that might exceed the demo limit. " * 10
    ]
    
    print(f"Testing {len(test_texts)} different text lengths")
    
    results = []
    
    # Prepare headers based on authentication type
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
    elif user_id:
        headers["X-User-ID"] = user_id
    
    # Test each text length
    for i, text in enumerate(test_texts):
        print(f"\nTesting text {i+1} (length: {len(text)} characters)")
        
        payload = {
            "model": "tts-1",
            "input": text,
            "voice": TEST_VOICE,
            "response_format": "mp3"
        }
        
        response = requests.post(
            f"{API_BASE_URL}/v1/audio/speech",
            json=payload,
            headers=headers
        )
        
        result = {
            "text_length": len(text),
            "status_code": response.status_code,
            "success": response.status_code == 200
        }
        
        if response.status_code == 200:
            print(f"Text length {len(text)} generated successfully!")
            audio = play_audio(response.content)
            result["audio"] = "Success"
        else:
            print(f"Error with text length {len(text)}: {response.status_code}")
            try:
                error_data = response.json()
                print(display_json(error_data))
                result["error"] = error_data
            except:
                print("Could not parse response as JSON")
                result["error"] = "Unknown error"
        
        results.append(result)
        
        # Check usage after each request
        usage_headers = headers.copy()
        usage_response = requests.get(f"{API_BASE_URL}/v1/usage", headers=usage_headers)
        if usage_response.status_code == 200:
            print(f"Usage after text length {len(text)}:")
            print(display_json(usage_response.json()))
        
        # Add delay between requests
        if i < len(test_texts) - 1:
            time.sleep(1)
    
    # Summarize results
    print("\nSummary of text length tests:")
    for result in results:
        status = "✅ Success" if result["success"] else "❌ Failed"
        print(f"Length {result['text_length']}: {status} (Status code: {result['status_code']})")
    
    return results

# Uncomment one of these lines to test different text lengths
# demo_text_lengths = test_text_lengths()
# free_tier_text_lengths = test_text_lengths(user_id=FREE_TIER_USER_ID)
# paid_tier_text_lengths = test_text_lengths(api_key=API_KEY)

# %% [markdown]
# ## 12. Test API Key Validation

# %%
def test_api_key_validation():
    """Test API key validation with valid and invalid keys"""
    # Test cases
    test_cases = [
        {"name": "No API Key", "key": None},
        {"name": "Invalid Format", "key": "invalid-key"},
        {"name": "Wrong Prefix", "key": "wrong-prefix-12345678"},
        {"name": "Valid Format but Invalid Key", "key": "sk-kokoro-12345678901234567890"},
        # Add your valid key here
        # {"name": "Valid Key", "key": API_KEY}
    ]
    
    results = []
    
    for case in test_cases:
        print(f"\nTesting: {case['name']}")
        
        headers = {}
        if case["key"]:
            headers["X-API-Key"] = case["key"]
        
        # Try to get usage statistics (simple endpoint that requires authentication)
        response = requests.get(
            f"{API_BASE_URL}/v1/usage",
            headers=headers
        )
        
        result = {
            "name": case["name"],
            "status_code": response.status_code,
            "success": response.status_code == 200
        }
        
        print(f"Status code: {response.status_code}")
        try:
            response_data = response.json()
            print(display_json(response_data))
            result["response"] = response_data
        except:
            print("Could not parse response as JSON")
            result["response"] = "Not JSON"
        
        results.append(result)
    
    # Summarize results
    print("\nSummary of API key validation tests:")
    for result in results:
        status = "✅ Success" if result["success"] else "❌ Failed"
        print(f"{result['name']}: {status} (Status code: {result['status_code']})")
    
    return results

# Uncomment to test API key validation
# api_key_validation_results = test_api_key_validation()

# %% [markdown]
# ## 13. Test User ID Validation

# %%
def test_user_id_validation():
    """Test user ID validation with valid and invalid IDs"""
    # Test cases
    test_cases = [
        {"name": "No User ID", "id": None},
        {"name": "Invalid Format", "id": "not-a-uuid"},
        {"name": "Valid Format but Non-existent", "id": "00000000-0000-0000-0000-000000000000"},
        # Add your valid user ID here
        # {"name": "Valid User ID", "id": FREE_TIER_USER_ID}
    ]
    
    results = []
    
    for case in test_cases:
        print(f"\nTesting: {case['name']}")
        
        headers = {}
        if case["id"]:
            headers["X-User-ID"] = case["id"]
        
        # Try to get usage statistics (simple endpoint that requires authentication)
        response = requests.get(
            f"{API_BASE_URL}/v1/usage",
            headers=headers
        )
        
        result = {
            "name": case["name"],
            "status_code": response.status_code,
            "success": response.status_code == 200
        }
        
        print(f"Status code: {response.status_code}")
        try:
            response_data = response.json()
            print(display_json(response_data))
            result["response"] = response_data
        except:
            print("Could not parse response as JSON")
            result["response"] = "Not JSON"
        
        results.append(result)
    
    # Summarize results
    print("\nSummary of user ID validation tests:")
    for result in results:
        status = "✅ Success" if result["success"] else "❌ Failed"
        print(f"{result['name']}: {status} (Status code: {result['status_code']})")
    
    return results

# Uncomment to test user ID validation
# user_id_validation_results = test_user_id_validation()

if __name__ == "__main__":
    print("Running API tests...")
    # You can call specific test functions here
    test_demo_user()
    check_demo_usage()
    # Add more tests as needed 