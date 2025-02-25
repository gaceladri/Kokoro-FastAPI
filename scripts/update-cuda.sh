#!/bin/bash

# CUDA Update Script for Ubuntu 24.04
# Based on official NVIDIA documentation

# Stop on any error
set -e

echo "===== CUDA Update Script for Ubuntu 24.04 ====="
echo "This script will update your CUDA installation to version 12.8."

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run this script as root or with sudo."
    exit 1
fi

# Detect current CUDA version if installed
if command -v nvcc &> /dev/null; then
    CURRENT_VERSION=$(nvcc --version | grep "release" | awk '{print $6}' | cut -c2-)
    echo "Current CUDA version: $CURRENT_VERSION"
else
    echo "CUDA not found on your system."
fi

# Add NVIDIA repository and set pin priority
echo "Adding NVIDIA repository..."
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-ubuntu2404.pin
mv cuda-ubuntu2404.pin /etc/apt/preferences.d/cuda-repository-pin-600

# Download and install local repo package
wget https://developer.download.nvidia.com/compute/cuda/12.8.0/local_installers/cuda-repo-ubuntu2404-12-8-local_12.8.0-570.86.10-1_amd64.deb
dpkg -i cuda-repo-ubuntu2404-12-8-local_12.8.0-570.86.10-1_amd64.deb

# Copy keyring
cp /var/cuda-repo-ubuntu2404-12-8-local/cuda-*-keyring.gpg /usr/share/keyrings/

# Update package lists
echo "Updating package lists..."
apt-get update

# Install CUDA toolkit
echo "Installing CUDA toolkit 12.8..."
apt-get -y install cuda-toolkit-12-8

# Ask user about driver installation
echo "Do you want to install NVIDIA drivers? (y/n)"
read -r install_drivers

if [[ $install_drivers =~ ^[Yy]$ ]]; then
    echo "Do you want to install the open kernel module (1) or legacy kernel module (2)?"
    echo "1 - Open kernel module (nvidia-open)"
    echo "2 - Legacy kernel module (cuda-drivers)"
    read -r driver_choice
    
    if [[ $driver_choice == "1" ]]; then
        echo "Installing open kernel module drivers..."
        apt-get install -y nvidia-open
    elif [[ $driver_choice == "2" ]]; then
        echo "Installing legacy kernel module drivers..."
        apt-get install -y cuda-drivers
    else
        echo "Invalid choice. Skipping driver installation."
    fi
fi

# Update environment variables
echo "Updating environment variables..."
if ! grep -q 'export PATH=/usr/local/cuda/bin${PATH:+:${PATH}}' /etc/profile.d/cuda.sh 2>/dev/null; then
    echo 'export PATH=/usr/local/cuda/bin${PATH:+:${PATH}}' > /etc/profile.d/cuda.sh
    echo 'export LD_LIBRARY_PATH=/usr/local/cuda/lib64${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}' >> /etc/profile.d/cuda.sh
    chmod +x /etc/profile.d/cuda.sh
fi

# Cleanup downloaded files
echo "Cleaning up..."
rm -f cuda-repo-ubuntu2404-12-8-local_12.8.0-570.86.10-1_amd64.deb

# Check the installed version
echo "Verifying installation..."
source /etc/profile.d/cuda.sh
if command -v nvcc &> /dev/null; then
    NEW_VERSION=$(nvcc --version | grep "release" | awk '{print $6}' | cut -c2-)
    echo "CUDA updated successfully. New version: $NEW_VERSION"
else
    echo "Warning: CUDA installation verification failed."
    echo "You may need to reboot your system first."
fi

echo "===== CUDA Update Complete ====="
echo "Please reboot your system to complete the installation."
echo "After reboot, you can verify the installation with: nvcc --version"