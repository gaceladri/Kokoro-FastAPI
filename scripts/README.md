# Scripts Directory

This directory contains various utility scripts for the Kokoro TTS project.

## Main Scripts

- `setup-env.sh` - Sets up environment variables and configuration files
- `security-check.sh` - Runs security checks on the codebase
- `run-jupyter.sh` - Starts a Jupyter notebook server for development

## Docker Scripts

The `docker` subdirectory contains scripts related to Docker operations:

- `run-local-dev.sh` - Runs the application locally using Docker
- `check-cuda.sh` - Checks CUDA version and compatibility
- `start-cpu.sh` - Starts the application with CPU configuration
- `start-gpu.sh` - Starts the application with GPU configuration
- `publish-local.sh` - Publishes Docker images locally

## CUDA Version Selection

The `run-local-dev.sh` script supports dynamic CUDA version selection for GPU mode:

```bash
# Run with default CUDA version (12.8.0)
./docker/run-local-dev.sh --gpu

# Run with a specific CUDA version
./docker/run-local-dev.sh --cuda 11.8.0

# Check CUDA compatibility before running
./docker/run-local-dev.sh --check-cuda
```

Common CUDA versions:
- 11.8.0 - Best compatibility with older drivers
- 12.2.0 - Requires drivers >= 525.0
- 12.8.0 - Requires drivers >= 535.0 (latest)

## Usage

Most scripts can be run directly from the scripts directory:

```bash
# Setup environment
./setup-env.sh

# Run with Docker (GPU mode)
./docker/run-local-dev.sh --gpu

# Run with Docker (CPU mode)
./docker/run-local-dev.sh --cpu

# Check CUDA compatibility
./docker/check-cuda.sh
```

For convenience, symbolic links to the most commonly used scripts are available in the root directory. 