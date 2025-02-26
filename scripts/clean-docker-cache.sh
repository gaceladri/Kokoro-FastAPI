#!/bin/bash
# Script to clean Docker cache for UV package manager

echo "Cleaning Docker UV cache..."

# Create a temporary Dockerfile for cleaning
cat > clean-cache-dockerfile << EOF
FROM alpine:latest
RUN mkdir -p /tmp/clean
WORKDIR /tmp/clean
CMD ["echo", "Cache cleaned"]
EOF

# Build a temporary image to clean the cache
docker build --no-cache -t clean-cache -f clean-cache-dockerfile .

# Remove the temporary Dockerfile
rm clean-cache-dockerfile

# Remove the temporary image
docker rmi clean-cache

# Prune Docker build cache
docker builder prune -f

echo "Docker UV cache cleaned successfully."
echo "Now you can run ./run-local-dev.sh -r to rebuild the Docker image." 