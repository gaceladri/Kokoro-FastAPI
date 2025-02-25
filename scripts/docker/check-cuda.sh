#!/bin/bash
# Script to check CUDA version and compatibility

# Get the script's directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_LOCAL_DEV_SCRIPT="$ROOT_DIR/scripts/docker/run-local-dev.sh"

echo "Checking NVIDIA driver and CUDA information..."
echo "----------------------------------------------"

# Check if bc is installed
if ! command -v bc &> /dev/null; then
    echo "Warning: 'bc' command not found. Detailed version comparison will be limited."
    echo "Consider installing bc: sudo apt-get install -y bc"
    echo ""
    HAS_BC=false
else
    HAS_BC=true
fi

# Check if nvidia-smi is available
if command -v nvidia-smi &> /dev/null; then
    echo "NVIDIA Driver Information:"
    DRIVER_INFO=$(nvidia-smi | grep "Driver Version")
    echo "$DRIVER_INFO"
    
    # Extract driver version
    DRIVER_VERSION=$(echo "$DRIVER_INFO" | grep -oP "(?<=Driver Version: )\d+\.\d+")
    echo ""
    
    echo "CUDA Version (from nvidia-smi):"
    CUDA_INFO=$(nvidia-smi | grep "CUDA Version")
    echo "$CUDA_INFO"
    
    # Extract CUDA version
    CUDA_VERSION=$(echo "$CUDA_INFO" | grep -oP "(?<=CUDA Version: )\d+\.\d+")
    echo ""
    
    # Provide compatibility recommendations based on driver version
    echo "CUDA Compatibility Recommendations:"
    echo "-----------------------------------"
    
    if [ "$HAS_BC" = true ]; then
        if (( $(echo "$DRIVER_VERSION >= 535.0" | bc -l) )); then
            echo "✅ Your driver supports CUDA 12.x"
            echo "   Recommended: CUDA 12.8.0 (latest)"
            RECOMMENDED_VERSION="12.8.0"
        elif (( $(echo "$DRIVER_VERSION >= 525.0" | bc -l) )); then
            echo "✅ Your driver supports CUDA 12.0"
            echo "   Recommended: CUDA 12.2.0"
            RECOMMENDED_VERSION="12.2.0"
        elif (( $(echo "$DRIVER_VERSION >= 450.0" | bc -l) )); then
            echo "✅ Your driver supports CUDA 11.x"
            echo "   Recommended: CUDA 11.8.0"
            RECOMMENDED_VERSION="11.8.0"
        else
            echo "⚠️ Your driver is quite old and may have limited CUDA support"
            echo "   Recommended: CUDA 11.8.0 (for best compatibility)"
            RECOMMENDED_VERSION="11.8.0"
        fi
    else
        # Simple version check without bc
        DRIVER_VERSION_MAJOR=$(echo "$DRIVER_VERSION" | cut -d. -f1)
        if [ "$DRIVER_VERSION_MAJOR" -ge 535 ]; then
            echo "✅ Your driver supports CUDA 12.x"
            echo "   Recommended: CUDA 12.8.0 (latest)"
            RECOMMENDED_VERSION="12.8.0"
        elif [ "$DRIVER_VERSION_MAJOR" -ge 525 ]; then
            echo "✅ Your driver supports CUDA 12.0"
            echo "   Recommended: CUDA 12.2.0"
            RECOMMENDED_VERSION="12.2.0"
        elif [ "$DRIVER_VERSION_MAJOR" -ge 450 ]; then
            echo "✅ Your driver supports CUDA 11.x"
            echo "   Recommended: CUDA 11.8.0"
            RECOMMENDED_VERSION="11.8.0"
        else
            echo "⚠️ Your driver is quite old and may have limited CUDA support"
            echo "   Recommended: CUDA 11.8.0 (for best compatibility)"
            RECOMMENDED_VERSION="11.8.0"
        fi
    fi
    echo ""
else
    echo "nvidia-smi not found. NVIDIA drivers may not be installed."
    echo "If you have an NVIDIA GPU, please install the appropriate drivers."
    echo "Otherwise, use the --cpu option to run without GPU acceleration."
    echo ""
    RECOMMENDED_VERSION="11.8.0"
fi

# Check if nvcc is available
if command -v nvcc &> /dev/null; then
    echo "CUDA Toolkit Version (from nvcc):"
    nvcc --version | grep "release"
    echo ""
else
    echo "nvcc not found. CUDA Toolkit may not be installed."
    echo "This is normal if you're using Docker for CUDA support."
    echo ""
fi

# List compatible CUDA versions
echo "Available CUDA versions for Docker:"
echo "- CUDA 11.8.0 (widely compatible with older drivers)"
echo "- CUDA 12.2.0 (requires drivers >= 525.0)"
echo "- CUDA 12.8.0 (requires drivers >= 535.0, latest)"
echo ""

echo "To run with a specific CUDA version:"
echo "$RUN_LOCAL_DEV_SCRIPT --cuda $RECOMMENDED_VERSION"
echo ""

echo "If you encounter issues, try using an earlier CUDA version for better compatibility." 