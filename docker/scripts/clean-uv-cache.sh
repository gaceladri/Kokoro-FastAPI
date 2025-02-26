#!/bin/bash
# Script to clean UV cache inside the Docker container

echo "Cleaning UV cache..."

# Remove UV cache directories
rm -rf /home/appuser/.cache/uv

echo "UV cache cleaned successfully." 