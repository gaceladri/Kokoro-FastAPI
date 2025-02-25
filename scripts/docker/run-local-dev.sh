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
    echo ""
    echo "Examples:"
    echo "  $0 --cpu             Run with CPU configuration"
    echo "  $0 --gpu --rebuild   Run with GPU and rebuild the image"
    echo "  $0 -d -p 8881        Run in detached mode on port 8881"
    echo "  $0 --cuda 11.8.0     Run with CUDA 11.8.0"
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

# Get the script's directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
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
    echo "Running setup-env.sh to create it..."
    "$SETUP_ENV_SCRIPT"
fi

# Check if .env file exists in config directory
if [ ! -f "$CONFIG_DIR/.env.example" ]; then
    echo "No .env.example file found in $CONFIG_DIR."
    echo "Please run $SETUP_ENV_SCRIPT first to create your environment configuration."
    exit 1
fi

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

# Build the image if requested
if [ "$REBUILD" = "true" ]; then
    echo "Rebuilding Docker image with CUDA $CUDA_VERSION..."
    cd "$ROOT_DIR" && docker compose --env-file .docker-compose-env -f $COMPOSE_FILE build
fi

# Run the Docker container
echo "Starting Docker container..."
if [ "$DETACHED" = "true" ]; then
    echo "Running in detached mode. Use 'docker compose -f $COMPOSE_FILE down' to stop."
    cd "$ROOT_DIR" && docker compose --env-file .docker-compose-env -f $COMPOSE_FILE up -d
else
    echo "Running in interactive mode. Use Ctrl+C to stop."
    cd "$ROOT_DIR" && docker compose --env-file .docker-compose-env -f $COMPOSE_FILE up
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