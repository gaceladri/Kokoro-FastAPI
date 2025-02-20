#!/bin/bash
set -e

# Function to load environment variables from file
load_env_file() {
    if [ -f "/app/config/.env" ]; then
        echo "Loading environment variables from /app/config/.env"
        set -a
        source /app/config/.env
        set +a
    fi
}

# Function to check required environment variables
check_required_vars() {
    if [ "$ENABLE_USAGE_TRACKING" = "true" ]; then
        if [ -z "$SUPABASE_URL" ]; then
            echo "Error: SUPABASE_URL is required when usage tracking is enabled"
            exit 1
        fi
        if [ -z "$SUPABASE_KEY" ]; then
            echo "Error: SUPABASE_KEY is required when usage tracking is enabled"
            exit 1
        fi
    fi
}

# Load environment variables
load_env_file

# Check required variables
check_required_vars

# Download model if enabled
if [ "$DOWNLOAD_MODEL" = "true" ]; then
    python download_model.py --output api/src/models/v1_0
fi

# Start the FastAPI server
exec uv run --extra $DEVICE python -m uvicorn api.src.main:app --host 0.0.0.0 --port 8880 --log-level debug