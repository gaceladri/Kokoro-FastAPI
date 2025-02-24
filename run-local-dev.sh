#!/bin/bash
# Script to run Kokoro TTS locally in development mode without Docker

# Default values
CONFIG_DIR=${CONFIG_DIR:-"./config"}
USE_GPU=${USE_GPU:-"true"}
PORT=${PORT:-"8880"}

# Check if config directory exists
if [ ! -d "$CONFIG_DIR" ]; then
    echo "Config directory $CONFIG_DIR does not exist."
    echo "Running setup-env.sh to create it..."
    ./setup-env.sh
fi

# Check if .env file exists in config directory
if [ ! -f "$CONFIG_DIR/.env" ]; then
    echo "No .env file found in $CONFIG_DIR."
    echo "Please run ./setup-env.sh first to create your environment configuration."
    exit 1
fi

# Load environment variables from .env file
echo "Loading environment variables from $CONFIG_DIR/.env"
set -a
source "$CONFIG_DIR/.env"
set +a

# Check if UV is installed
if ! command -v uv &> /dev/null; then
    echo "UV is not installed. Please install it first:"
    echo "curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# Check if Python virtual environment exists
if [ ! -d ".venv" ]; then
    echo "Creating Python virtual environment..."
    uv venv
fi

# Activate virtual environment
echo "Activating virtual environment..."
source .venv/bin/activate

# Install dependencies if needed
if [ ! -f ".venv/.initialized" ]; then
    echo "Installing dependencies..."
    if [ "$USE_GPU" = "true" ]; then
        uv sync --extra gpu
    else
        uv sync --extra cpu
    fi
    touch .venv/.initialized
fi

# Set Python path
export PYTHONPATH=$PYTHONPATH:$(pwd):$(pwd)/api

# Set device based on USE_GPU
if [ "$USE_GPU" = "true" ]; then
    DEVICE="gpu"
    echo "Running with GPU support"
else
    DEVICE="cpu"
    echo "Running with CPU support"
fi

# Start the FastAPI server
echo "Starting Kokoro TTS on port $PORT..."
uv run --extra $DEVICE python -m uvicorn api.src.main:app --host 0.0.0.0 --port $PORT --reload 