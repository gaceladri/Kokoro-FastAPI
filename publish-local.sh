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

# Build and push images
echo "Building and pushing images..."
docker buildx bake "$TARGET" --push

echo "Images built and pushed successfully!" 