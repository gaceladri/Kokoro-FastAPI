#!/bin/bash

# Get project root directory
PROJECT_ROOT=$(pwd)

# Set environment variables
export USE_GPU=true
export USE_ONNX=false
export PYTHONPATH=$PROJECT_ROOT:$PROJECT_ROOT/api
export MODEL_DIR=src/models
export VOICES_DIR=src/voices/v1_0
export WEB_PLAYER_PATH=$PROJECT_ROOT/web

# Load environment variables from .env file if it exists
if [ -f .env ]; then
    source .env
fi

# Ensure required environment variables are set
if [ -z "$STRIPE_SECRET_KEY" ]; then
    echo "Warning: STRIPE_SECRET_KEY is not set. Usage tracking with Stripe will not work."
fi

# Run FastAPI with GPU extras using uv run
uv pip install -e ".[gpu]"
uv run uvicorn api.src.main:app --reload --host 0.0.0.0 --port 8880