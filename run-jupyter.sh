#!/bin/bash
# Script to run Jupyter notebook server for testing Kokoro TTS API

# Default values
CONFIG_DIR=${CONFIG_DIR:-"./config"}
VENV_DIR=${VENV_DIR:-"./.venv"}

# Check if config directory exists
if [ ! -d "$CONFIG_DIR" ]; then
    echo "Config directory $CONFIG_DIR does not exist."
    echo "Please make sure your configuration is set up correctly."
    exit 1
fi

# Check if .env file exists in config directory
if [ ! -f "$CONFIG_DIR/.env" ]; then
    echo "No .env file found in $CONFIG_DIR."
    echo "Please make sure your environment configuration is set up correctly."
    exit 1
fi

# Load environment variables from .env file
echo "Loading environment variables from $CONFIG_DIR/.env"
set -a
source "$CONFIG_DIR/.env"
set +a

# Check if Python virtual environment exists
if [ ! -d "$VENV_DIR" ]; then
    echo "Python virtual environment not found at $VENV_DIR."
    echo "Please run ./run-local-dev.sh first to set up the environment."
    exit 1
fi

# Activate virtual environment
echo "Activating virtual environment..."
source "$VENV_DIR/bin/activate"

# Install Jupyter and other testing dependencies if needed
echo "Installing Jupyter and testing dependencies..."
pip install jupyter notebook matplotlib pydub python-dotenv supabase

# Set Python path
export PYTHONPATH=$PYTHONPATH:$(pwd):$(pwd)/api

# Start Jupyter notebook server
echo "Starting Jupyter notebook server..."
jupyter notebook --notebook-dir=notebooks 