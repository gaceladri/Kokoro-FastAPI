#!/bin/bash
# Script to set up environment configuration for Kokoro TTS

# Exit on error
set -e

# Default values
CONFIG_DIR="./config"
ENV_FILE="$CONFIG_DIR/.env"
ENV_EXAMPLE_FILE="$CONFIG_DIR/.env.example"

# Function to display help message
show_help() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Set up environment configuration for Kokoro TTS"
    echo ""
    echo "Options:"
    echo "  -h, --help           Show this help message"
    echo "  -c, --config DIR     Set config directory (default: ./config)"
    echo "  --no-tracking        Disable usage tracking"
    echo ""
}

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            show_help
            exit 0
            ;;
        -c|--config)
            CONFIG_DIR="$2"
            ENV_FILE="$CONFIG_DIR/.env"
            ENV_EXAMPLE_FILE="$CONFIG_DIR/.env.example"
            shift 2
            ;;
        --no-tracking)
            DISABLE_TRACKING=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

# Create config directory if it doesn't exist
if [ ! -d "$CONFIG_DIR" ]; then
    echo "Creating config directory: $CONFIG_DIR"
    mkdir -p "$CONFIG_DIR"
fi

# Create .env.example if it doesn't exist
if [ ! -f "$ENV_EXAMPLE_FILE" ]; then
    echo "Creating example environment file: $ENV_EXAMPLE_FILE"
    cat > "$ENV_EXAMPLE_FILE" << EOL
# Kokoro TTS Environment Configuration

# Usage Tracking (optional)
ENABLE_USAGE_TRACKING=false
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_key

# Model Configuration
DOWNLOAD_MODEL=true
USE_GPU=true
USE_ONNX=false

# Server Configuration
PORT=8880
HOST=0.0.0.0
LOG_LEVEL=info

# Model Paths
MODEL_DIR=src/models
VOICES_DIR=src/voices/v1_0
WEB_PLAYER_PATH=web
EOL
fi

# If .env doesn't exist, create it from .env.example
if [ ! -f "$ENV_FILE" ]; then
    echo "Creating environment file from example: $ENV_FILE"
    cp "$ENV_EXAMPLE_FILE" "$ENV_FILE"
    
    # Set secure permissions on .env file
    chmod 600 "$ENV_FILE"
    
    echo "Please edit $ENV_FILE with your configuration values."
else
    echo "Environment file already exists: $ENV_FILE"
fi

# If tracking is disabled, update .env file
if [ "$DISABLE_TRACKING" = true ]; then
    echo "Disabling usage tracking in $ENV_FILE"
    sed -i 's/ENABLE_USAGE_TRACKING=.*/ENABLE_USAGE_TRACKING=false/' "$ENV_FILE"
fi

echo ""
echo "Environment setup complete!"
echo "Configuration files:"
echo "  - $ENV_FILE"
echo "  - $ENV_EXAMPLE_FILE"
echo ""
echo "Next steps:"
echo "1. Edit $ENV_FILE with your configuration values"
echo "2. Run ./run-local-dev.sh to start the service"
echo "" 