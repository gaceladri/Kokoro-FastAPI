#!/bin/bash
# Script to run Kokoro TTS locally in development mode using Docker

# Function to display help message
show_help() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Run Kokoro TTS locally in development mode using Docker"
    echo ""
    echo "Options:"
    echo "  -h, --help           Show this help message"
    echo "  -c, --config DIR     Set config directory (default: ./config)"
    echo "  -p, --port PORT      Set port number (default: 8880)"
    echo "  -g, --gpu            Use GPU (default)"
    echo "  --cpu                Use CPU instead of GPU"
    echo "  --cuda VERSION       Specify CUDA version (default: 12.8.0)"
    echo "                       Common versions: 11.8.0, 12.2.0, 12.8.0"
    echo "  -r, --rebuild        Rebuild Docker image"
    echo "  -d, --detached       Run in detached mode"
    echo "  --check-cuda         Check CUDA compatibility before running"
    echo "  --no-tracking        Disable usage tracking"
    echo ""
    echo "Examples:"
    echo "  $0 --cpu             Run with CPU configuration"
    echo "  $0 --gpu --rebuild   Run with GPU and rebuild the image"
    echo "  $0 -d -p 8881        Run in detached mode on port 8881"
    echo "  $0 --cuda 11.8.0     Run with CUDA 11.8.0"
    echo "  $0 --no-tracking     Run with usage tracking disabled"
    echo ""
}

# Default values
CONFIG_DIR="./config"
PORT="8880"
USE_GPU="true"
REBUILD="false"
DETACHED="false"
CUDA_VERSION="12.8.0"
CHECK_CUDA="false"
DISABLE_TRACKING="false"

# Get the script's directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$SCRIPT_DIR"
SETUP_ENV_SCRIPT="$ROOT_DIR/scripts/setup-env.sh"
CHECK_CUDA_SCRIPT="$SCRIPT_DIR/check-cuda.sh"

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            show_help
            exit 0
            ;;
        -c|--config)
            CONFIG_DIR="$2"
            shift 2
            ;;
        -p|--port)
            PORT="$2"
            shift 2
            ;;
        -g|--gpu)
            USE_GPU="true"
            shift
            ;;
        --cpu)
            USE_GPU="false"
            shift
            ;;
        --cuda)
            CUDA_VERSION="$2"
            shift 2
            ;;
        --check-cuda)
            CHECK_CUDA="true"
            shift
            ;;
        --no-tracking)
            DISABLE_TRACKING="true"
            shift
            ;;
        -r|--rebuild)
            REBUILD="true"
            shift
            ;;
        -d|--detached)
            DETACHED="true"
            shift
            ;;
        *)
            echo "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

# Check CUDA compatibility if requested
if [ "$CHECK_CUDA" = "true" ] && [ "$USE_GPU" = "true" ]; then
    echo "Checking CUDA compatibility..."
    "$CHECK_CUDA_SCRIPT"
    echo ""
    read -p "Continue with CUDA version $CUDA_VERSION? (y/n): " continue_with_cuda
    if [ "$continue_with_cuda" != "y" ]; then
        echo "Aborting. Please specify a different CUDA version with --cuda."
        exit 0
    fi
fi

# Check if config directory exists
if [ ! -d "$CONFIG_DIR" ]; then
    echo "Config directory $CONFIG_DIR does not exist."
    # echo "Running setup-env.sh to create it..."
    # "$SETUP_ENV_SCRIPT"
fi

# Check for environment files
ENV_FILE="$CONFIG_DIR/.env"
ENV_EXAMPLE_FILE="$CONFIG_DIR/.env.example"

# Make sure PWD is set correctly for Docker Compose
export PWD="$(pwd)"

# Check if either .env or .env.example exists
if [ ! -f "$ENV_FILE" ] && [ ! -f "$ENV_EXAMPLE_FILE" ]; then
    echo "No .env or .env.example file found in $CONFIG_DIR."
    echo "Please run $SETUP_ENV_SCRIPT first to create your environment configuration."
    exit 1
fi

# If .env doesn't exist but .env.example does, copy it
if [ ! -f "$ENV_FILE" ] && [ -f "$ENV_EXAMPLE_FILE" ]; then
    echo "No .env file found in $CONFIG_DIR, but .env.example exists."
    echo "Copying .env.example to .env..."
    cp "$ENV_EXAMPLE_FILE" "$ENV_FILE"
    echo "Please edit $ENV_FILE with your configuration if needed."
fi

# Verify that we now have a .env file
if [ ! -f "$ENV_FILE" ]; then
    echo "Failed to create .env file. Please check permissions and try again."
    exit 1
fi

echo "Using environment file: $ENV_FILE"

# Determine which Docker Compose file to use based on USE_GPU
if [ "$USE_GPU" = "true" ]; then
    COMPOSE_FILE="$ROOT_DIR/docker/gpu/docker-compose.yml"
    echo "Using GPU configuration with CUDA $CUDA_VERSION"
else
    COMPOSE_FILE="$ROOT_DIR/docker/cpu/docker-compose.yml"
    echo "Using CPU configuration"
fi

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "Docker is not installed. Please install Docker first."
    exit 1
fi

# Check if Docker Compose is installed
if ! command -v docker compose &> /dev/null; then
    echo "Docker Compose is not installed. Please install Docker Compose first."
    exit 1
fi

# Build and start the Docker container
echo "Starting Kokoro TTS on port $PORT using Docker..."

# Create a temporary .env file for docker-compose
cat > "$ROOT_DIR/.docker-compose-env" << EOL
CONFIG_DIR=$CONFIG_DIR
PORT=$PORT
CUDA_VERSION=$CUDA_VERSION
EOL

# Check if SUPABASE credentials exist in the .env file
SUPABASE_URL=$(grep -oP 'SUPABASE_URL=\K.*' "$ENV_FILE" 2>/dev/null || echo "")
SUPABASE_KEY=$(grep -oP 'SUPABASE_KEY=\K.*' "$ENV_FILE" 2>/dev/null || echo "")

# If --no-tracking flag is set or SUPABASE credentials are missing, disable usage tracking
if [ "$DISABLE_TRACKING" = "true" ] || [ -z "$SUPABASE_URL" ] || [ -z "$SUPABASE_KEY" ]; then
    if [ "$DISABLE_TRACKING" = "true" ]; then
        echo "Usage tracking disabled by user request."
    else
        echo "Warning: SUPABASE_URL or SUPABASE_KEY not found in $ENV_FILE"
        echo "Disabling usage tracking to avoid errors."
    fi
    echo "ENABLE_USAGE_TRACKING=false" >> "$ROOT_DIR/.docker-compose-env"
else
    # If credentials exist and tracking is not disabled, enable usage tracking
    echo "ENABLE_USAGE_TRACKING=true" >> "$ROOT_DIR/.docker-compose-env"
    echo "SUPABASE_URL=$SUPABASE_URL" >> "$ROOT_DIR/.docker-compose-env"
    echo "SUPABASE_KEY=$SUPABASE_KEY" >> "$ROOT_DIR/.docker-compose-env"
    
    # Export these variables so Docker Compose can use them with ${PWD}
    export SUPABASE_URL="$SUPABASE_URL"
    export SUPABASE_KEY="$SUPABASE_KEY"
    export CONFIG_DIR="$CONFIG_DIR"
    export PWD="$(pwd)"
fi

# Add other environment variables from .env file
echo "DOWNLOAD_MODEL=$(grep -oP 'DOWNLOAD_MODEL=\K.*' "$ENV_FILE" 2>/dev/null || echo "true")" >> "$ROOT_DIR/.docker-compose-env"

# Display environment variables being used (without sensitive info)
echo "Environment variables being used:"
grep -v "SUPABASE_" "$ROOT_DIR/.docker-compose-env" || true

# Build the image if requested
if [ "$REBUILD" = "true" ]; then
    echo "Rebuilding Docker image with CUDA $CUDA_VERSION..."
    cd "$ROOT_DIR" && docker compose --env-file "$ROOT_DIR/.docker-compose-env" -f "$COMPOSE_FILE" build
fi

# Run the Docker container
echo "Starting Docker container..."
if [ "$DETACHED" = "true" ]; then
    echo "Running in detached mode. Use 'docker compose -f $COMPOSE_FILE down' to stop."
    cd "$ROOT_DIR" && docker compose --env-file "$ROOT_DIR/.docker-compose-env" -f "$COMPOSE_FILE" up -d
else
    echo "Running in interactive mode. Use Ctrl+C to stop."
    cd "$ROOT_DIR" && docker compose --env-file "$ROOT_DIR/.docker-compose-env" -f "$COMPOSE_FILE" up
fi

# Clean up temporary env file
rm -f "$ROOT_DIR/.docker-compose-env"

# Display helpful information if running in detached mode
if [ "$DETACHED" = "true" ]; then
    echo ""
    echo "Container is running in the background."
    echo "To view logs: docker compose -f $COMPOSE_FILE logs -f"
    echo "To stop: docker compose -f $COMPOSE_FILE down"
    echo "API is available at: http://localhost:$PORT"
fi

# Note: Use Ctrl+C to stop the container 