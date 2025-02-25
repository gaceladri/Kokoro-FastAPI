#!/bin/bash

# Exit on error
set -e

# Allow specifying which target to build (default: both CPU and GPU)
TARGET=${1:-"default"}

# Read version from VERSION file
VERSION=$(cat VERSION)
echo "Building version: $VERSION"
echo "Building target: $TARGET"

# Export version for docker buildx bake
export VERSION

# Check if user is logged in to Docker Hub
if ! docker info | grep -q "Username"; then
    echo "You are not logged in to Docker Hub. Please login first with:"
    echo "docker login"
    exit 1
fi

# Enable BuildKit
export DOCKER_BUILDKIT=1
export BUILDKIT_STEP_LOG_MAX_SIZE=10485760

# Create a new builder instance if it doesn't exist
if ! docker buildx inspect multiplatform-builder >/dev/null 2>&1; then
    echo "Creating new builder instance..."
    docker buildx create --name multiplatform-builder --driver docker-container --bootstrap
fi

# Use the new builder
echo "Switching to multi-platform builder..."
docker buildx use multiplatform-builder

# Security warning for sensitive environment variables
echo "⚠️  SECURITY WARNING ⚠️"
echo "This build process will create Docker images without sensitive environment variables."
echo "For production use, you should mount a config directory with your .env file:"
echo "  - Use './setup-env.sh' to create your secure environment configuration"
echo "  - Mount the config directory when running containers"
echo "  - See README.md for detailed instructions on secure environment setup"
echo ""

# Build and push images
echo "Building and pushing images..."
docker buildx bake "$TARGET" --push

# Check for warnings about sensitive environment variables
if docker buildx bake "$TARGET" --print | grep -q "SUPABASE_KEY\|STRIPE_SECRET_KEY"; then
    echo "⚠️  WARNING: Sensitive environment variables detected in Dockerfile!"
    echo "Please remove any sensitive variables from the Dockerfile and use mounted config instead."
fi

echo "Images built and pushed successfully!"
echo ""
echo "To run the container with secure environment variables:"
echo "  1. Set up your environment: ./setup-env.sh"
echo "  2. Run with config mount: docker run -p 8880:8880 -v ./config:/app/config gaceladri/kokoro-fastapi-gpu:$VERSION" 