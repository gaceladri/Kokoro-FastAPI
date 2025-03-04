<p align="center">
  <img src="githubbanner.png" alt="Kokoro TTS Banner">
</p>

# <sub><sub>_`FastKoko`_ </sub></sub>
[![Tests](https://img.shields.io/badge/tests-69%20passed-darkgreen)]()
[![Coverage](https://img.shields.io/badge/coverage-52%25-tan)]()
[![Try on Spaces](https://img.shields.io/badge/%F0%9F%A4%97%20Try%20on-Spaces-blue)](https://huggingface.co/spaces/Remsky/Kokoro-TTS-Zero)

[![Kokoro](https://img.shields.io/badge/kokoro-v0.7.9::31a2b63-BB5420)](https://github.com/hexgrad/kokoro)
[![Misaki](https://img.shields.io/badge/misaki-v0.7.9::ebc76c2-B8860B)](https://github.com/hexgrad/misaki)

[![Tested at Model Commit](https://img.shields.io/badge/last--tested--model--commit-1.0::9901c2b-blue)](https://huggingface.co/hexgrad/Kokoro-82M/commit/9901c2b79161b6e898b7ea857ae5298f47b8b0d6)

Dockerized FastAPI wrapper for [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) text-to-speech model
- Multi-language support (English, Japanese, Korean, Chinese, _Vietnamese soon_)
- OpenAI-compatible Speech endpoint, NVIDIA GPU accelerated or CPU inference with PyTorch 
- ONNX support coming soon, see v0.1.5 and earlier for legacy ONNX support in the interim
- Debug endpoints for monitoring system stats, integrated web UI on localhost:8880/web
- Phoneme-based audio generation, phoneme generation
- Per-word timestamped caption generation
- Voice mixing with weighted combinations
- Usage tracking with Supabase and Stripe integration

### Integration Guides
 [![Helm Chart](https://img.shields.io/badge/Helm%20Chart-black?style=flat&logo=helm&logoColor=white)](https://github.com/remsky/Kokoro-FastAPI/wiki/Setup-Kubernetes) [![DigitalOcean](https://img.shields.io/badge/DigitalOcean-black?style=flat&logo=digitalocean&logoColor=white)](https://github.com/remsky/Kokoro-FastAPI/wiki/Integrations-DigitalOcean) [![SillyTavern](https://img.shields.io/badge/SillyTavern-black?style=flat&color=red)](https://github.com/remsky/Kokoro-FastAPI/wiki/Integrations-SillyTavern)
[![OpenWebUI](https://img.shields.io/badge/OpenWebUI-black?style=flat&color=white)](https://github.com/remsky/Kokoro-FastAPI/wiki/Integrations-OpenWebUi)




## Get Started

<details>
<summary>Quickest Start (docker run)</summary>


Pre built images are available to run, with arm/multi-arch support, and baked in models
Refer to the core/config.py file for a full list of variables which can be managed via the environment

```bash
# the `latest` tag can be used, but should not be considered stable as it may include `nightly` branch builds
# it may have some bonus features however, and feedback/testing is welcome

docker run -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-cpu:v0.2.2 # CPU, or:
docker run --gpus all -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-gpu:v0.2.2  #NVIDIA GPU
```


</details>

<details>

<summary>Quick Start (docker compose) </summary>

1. Install prerequisites, and start the service using Docker Compose (Full setup including UI):
   - Install [Docker](https://www.docker.com/products/docker-desktop/)
   - Clone the repository:
        ```bash
        git clone https://github.com/remsky/Kokoro-FastAPI.git
        cd Kokoro-FastAPI

        # Set up environment variables securely
        ./setup-env.sh
        
        # Run with the new script (automatically uses the right docker-compose file)
        ./run-local.sh gpu  # For GPU support
        # or
        ./run-local.sh cpu  # For CPU support
        
        # Alternatively, use docker-compose directly
        cd docker/gpu  # For GPU support
        # or cd docker/cpu  # For CPU support
        CONFIG_DIR=../../config docker compose up --build

        # Models will auto-download, but if needed you can manually download:
        python docker/scripts/download_model.py --output api/src/models/v1_0

        # Or run directly via UV:
        ./start-gpu.sh  # For GPU support
        ./start-cpu.sh  # For CPU support
        ```
</details>
<details>
<summary>Direct Run (via uv) </summary>

1. Install prerequisites ():
   - Install [astral-uv](https://docs.astral.sh/uv/)
   - Install [espeak-ng](https://github.com/espeak-ng/espeak-ng) in your system if you want it available as a fallback for unknown words/sounds. The upstream libraries may attempt to handle this, but results have varied.
   - Clone the repository:
        ```bash
        git clone https://github.com/remsky/Kokoro-FastAPI.git
        cd Kokoro-FastAPI
        ```
        
        Set up your environment variables securely:
        ```bash
        # Set up secure environment configuration
        ./setup-env.sh
        
        # Run locally with secure environment variables
        ./run-local-dev.sh  # Uses GPU by default
        
        # Or run with CPU
        USE_GPU=false ./run-local-dev.sh
        ```
        
        Alternatively, you can run the model download script and start directly:
        ```bash
        # Download the model if you haven't already
        python docker/scripts/download_model.py --output api/src/models/v1_0
     
        # Start directly via UV (with hot-reload)
        ./start-cpu.sh  # For CPU support
        ./start-gpu.sh  # For GPU support
        ```

</details>

<details>
<summary>Manual Docker Build and Run</summary>

Build and run the Docker container manually:

```bash
# Build the Docker image
docker build -t kokoro-fastapi-gpu -f Dockerfile ../..

# Run the container with GPU support
docker run -it --rm -p 8880:8880 \
  -v "$(pwd)/config:/app/config" \
  --env PYTHONPATH=/app:/app/api \
  --env USE_GPU=true \
  --env PYTHONUNBUFFERED=1 \
  --env ENABLE_USAGE_TRACKING=true \
  --env SUPABASE_URL=<your-supabase-url> \
  --env SUPABASE_KEY=<your-supabase-key> \
  --env DOWNLOAD_MODEL=true \
  --gpus all \
  kokoro-fastapi-gpu
```

Replace `<your-supabase-url>` and `<your-supabase-key>` with your actual Supabase credentials.
</details>

<details open>
<summary> Up and Running? </summary>


Run locally as an OpenAI-Compatible Speech Endpoint
    
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8880/v1", api_key="not-needed"
)

with client.audio.speech.with_streaming_response.create(
    model="kokoro",
    voice="af_sky+af_bella", #single or multiple voicepack combo
    input="Hello world!"
  ) as response:
      response.stream_to_file("output.mp3")
```
  
- The API will be available at http://localhost:8880
- API Documentation: http://localhost:8880/docs

- Web Interface: http://localhost:8880/web

<div align="center" style="display: flex; justify-content: center; gap: 10px;">
  <img src="assets/docs-screenshot.png" width="42%" alt="API Documentation" style="border: 2px solid #333; padding: 10px;">
  <img src="assets/webui-screenshot.png" width="42%" alt="Web UI Screenshot" style="border: 2px solid #333; padding: 10px;">
</div>

</details>


## Features 
<details>
<summary>OpenAI-Compatible Speech Endpoint</summary>

```python
# Using OpenAI's Python library
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8880/v1", api_key="not-needed")
response = client.audio.speech.create(
    model="kokoro",  
    voice="af_bella+af_sky", # see /api/src/core/openai_mappings.json to customize
    input="Hello world!",
    response_format="mp3"
)

response.stream_to_file("output.mp3")
```
Or Via Requests:
```python
import requests


response = requests.get("http://localhost:8880/v1/audio/voices")
voices = response.json()["voices"]

# Generate audio
response = requests.post(
    "http://localhost:8880/v1/audio/speech",
    json={
        "model": "kokoro",  
        "input": "Hello world!",
        "voice": "af_bella",
        "response_format": "mp3",  # Supported: mp3, wav, opus, flac
        "speed": 1.0
    }
)

# Save audio
with open("output.mp3", "wb") as f:
    f.write(response.content)
```

Quick tests (run from another terminal):
```bash
python examples/assorted_checks/test_openai/test_openai_tts.py # Test OpenAI Compatibility
python examples/assorted_checks/test_voices/test_all_voices.py # Test all available voices
```
</details>

<details>
<summary>Voice Combination</summary>

- Weighted voice combinations using ratios (e.g., "af_bella(2)+af_heart(1)" for 67%/33% mix)
- Ratios are automatically normalized to sum to 100%
- Available through any endpoint by adding weights in parentheses
- Saves generated voicepacks for future use

Combine voices and generate audio:
```python
import requests
response = requests.get("http://localhost:8880/v1/audio/voices")
voices = response.json()["voices"]

# Example 1: Simple voice combination (50%/50% mix)
response = requests.post(
    "http://localhost:8880/v1/audio/speech",
    json={
        "input": "Hello world!",
        "voice": "af_bella+af_sky",  # Equal weights
        "response_format": "mp3"
    }
)

# Example 2: Weighted voice combination (67%/33% mix)
response = requests.post(
    "http://localhost:8880/v1/audio/speech",
    json={
        "input": "Hello world!",
        "voice": "af_bella(2)+af_sky(1)",  # 2:1 ratio = 67%/33%
        "response_format": "mp3"
    }
)

# Example 3: Download combined voice as .pt file
response = requests.post(
    "http://localhost:8880/v1/audio/voices/combine",
    json="af_bella(2)+af_sky(1)"  # 2:1 ratio = 67%/33%
)

# Save the .pt file
with open("combined_voice.pt", "wb") as f:
    f.write(response.content)

# Use the downloaded voice file
response = requests.post(
    "http://localhost:8880/v1/audio/speech",
    json={
        "input": "Hello world!",
        "voice": "combined_voice",  # Use the saved voice file
        "response_format": "mp3"
    }
)

```
<p align="center">
  <img src="assets/voice_analysis.png" width="80%" alt="Voice Analysis Comparison" style="border: 2px solid #333; padding: 10px;">
</p>
</details>

<details>
<summary>Multiple Output Audio Formats</summary>

- mp3
- wav
- opus 
- flac
- aac
- pcm

<p align="center">
<img src="assets/format_comparison.png" width="80%" alt="Audio Format Comparison" style="border: 2px solid #333; padding: 10px;">
</p>

</details>

<details>
<summary>Streaming Support</summary>

```python
# OpenAI-compatible streaming
from openai import OpenAI
client = OpenAI(
    base_url="http://localhost:8880/v1", api_key="not-needed")

# Stream to file
with client.audio.speech.with_streaming_response.create(
    model="kokoro",
    voice="af_bella",
    input="Hello world!"
) as response:
    response.stream_to_file("output.mp3")

# Stream to speakers (requires PyAudio)
import pyaudio
player = pyaudio.PyAudio().open(
    format=pyaudio.paInt16, 
    channels=1, 
    rate=24000, 
    output=True
)

with client.audio.speech.with_streaming_response.create(
    model="kokoro",
    voice="af_bella",
    response_format="pcm",
    input="Hello world!"
) as response:
    for chunk in response.iter_bytes(chunk_size=1024):
        player.write(chunk)
```

Or via requests:
```python
import requests

response = requests.post(
    "http://localhost:8880/v1/audio/speech",
    json={
        "input": "Hello world!",
        "voice": "af_bella",
        "response_format": "pcm"
    },
    stream=True
)

for chunk in response.iter_content(chunk_size=1024):
    if chunk:
        # Process streaming chunks
        pass
```

<p align="center">
  <img src="assets/gpu_first_token_timeline_openai.png" width="45%" alt="GPU First Token Timeline" style="border: 2px solid #333; padding: 10px; margin-right: 1%;">
  <img src="assets/cpu_first_token_timeline_stream_openai.png" width="45%" alt="CPU First Token Timeline" style="border: 2px solid #333; padding: 10px;">
</p>

Key Streaming Metrics:
- First token latency @ chunksize
    - ~300ms  (GPU) @ 400 
    - ~3500ms (CPU) @ 200 (older i7)
    - ~<1s    (CPU) @ 200 (M3 Pro)
- Adjustable chunking settings for real-time playback 

*Note: Artifacts in intonation can increase with smaller chunks*
</details>

## Processing Details
<details>
<summary>Performance Benchmarks</summary>

Benchmarking was performed on generation via the local API using text lengths up to feature-length books (~1.5 hours output), measuring processing time and realtime factor. Tests were run on: 
- Windows 11 Home w/ WSL2 
- NVIDIA 4060Ti 16gb GPU @ CUDA 12.1
- 11th Gen i7-11700 @ 2.5GHz
- 64gb RAM
- WAV native output
- H.G. Wells - The Time Machine (full text)

<p align="center">
  <img src="assets/gpu_processing_time.png" width="45%" alt="Processing Time" style="border: 2px solid #333; padding: 10px; margin-right: 1%;">
  <img src="assets/gpu_realtime_factor.png" width="45%" alt="Realtime Factor" style="border: 2px solid #333; padding: 10px;">
</p>

Key Performance Metrics:
- Realtime Speed: Ranges between 35x-100x (generation time to output audio length)
- Average Processing Rate: 137.67 tokens/second (cl100k_base)
</details>
<details>
<summary>GPU Vs. CPU</summary>

```bash
# GPU: Requires NVIDIA GPU with CUDA 12.1 support (~35x-100x realtime speed)
cd docker/gpu
docker compose up --build

# CPU: PyTorch CPU inference
cd docker/cpu
docker compose up --build

```
*Note: Overall speed may have reduced somewhat with the structural changes to accommodate streaming. Looking into it* 
</details>

<details>
<summary>Natural Boundary Detection</summary>

- Automatically splits and stitches at sentence boundaries 
- Helps to reduce artifacts and allow long form processing as the base model is only currently configured for approximately 30s output

The model is capable of processing up to a 510 phonemized token chunk at a time, however, this can often lead to 'rushed' speech or other artifacts. An additional layer of chunking is applied in the server, that creates flexible chunks with a `TARGET_MIN_TOKENS` , `TARGET_MAX_TOKENS`, and `ABSOLUTE_MAX_TOKENS` which are configurable via environment variables, and set to 175, 250, 450 by default

</details>

<details>
<summary>Timestamped Captions & Phonemes</summary>

Generate audio with word-level timestamps:
```python
import requests
import json

response = requests.post(
    "http://localhost:8880/dev/captioned_speech",
    json={
        "model": "kokoro",
        "input": "Hello world!",
        "voice": "af_bella",
        "speed": 1.0,
        "response_format": "wav"
    }
)

# Get timestamps from header
timestamps = json.loads(response.headers['X-Word-Timestamps'])
print("Word-level timestamps:")
for ts in timestamps:
    print(f"{ts['word']}: {ts['start_time']:.3f}s - {ts['end_time']:.3f}s")

# Save audio
with open("output.wav", "wb") as f:
    f.write(response.content)
```
</details>

<details>
<summary>Phoneme & Token Routes</summary>

Convert text to phonemes and/or generate audio directly from phonemes:
```python
import requests

def get_phonemes(text: str, language: str = "a"):
    """Get phonemes and tokens for input text"""
    response = requests.post(
        "http://localhost:8880/dev/phonemize",
        json={"text": text, "language": language}  # "a" for American English
    )
    response.raise_for_status()
    result = response.json()
    return result["phonemes"], result["tokens"]

def generate_audio_from_phonemes(phonemes: str, voice: str = "af_bella"):
    """Generate audio from phonemes"""
    response = requests.post(
        "http://localhost:8880/dev/generate_from_phonemes",
        json={"phonemes": phonemes, "voice": voice},
        headers={"Accept": "audio/wav"}
    )
    if response.status_code != 200:
        print(f"Error: {response.text}")
        return None
    return response.content

# Example usage
text = "Hello world!"
try:
    # Convert text to phonemes
    phonemes, tokens = get_phonemes(text)
    print(f"Phonemes: {phonemes}")  # e.g. ðɪs ɪz ˈoʊnli ɐ tˈɛst
    print(f"Tokens: {tokens}")      # Token IDs including start/end tokens

    # Generate and save audio
    if audio_bytes := generate_audio_from_phonemes(phonemes):
        with open("speech.wav", "wb") as f:
            f.write(audio_bytes)
        print(f"Generated {len(audio_bytes)} bytes of audio")
except Exception as e:
    print(f"Error: {e}")
```

See `examples/phoneme_examples/generate_phonemes.py` for a sample script.
</details>

<details>
<summary>Debug Endpoints</summary>

Monitor system state and resource usage with these endpoints:

- `/debug/threads` - Get thread information and stack traces
- `/debug/storage` - Monitor temp file and output directory usage
- `/debug/system` - Get system information (CPU, memory, GPU)
- `/debug/session_pools` - View ONNX session and CUDA stream status

Useful for debugging resource exhaustion or performance issues.
</details>

## API Key Cache Optimization

To improve performance for the streaming API, Kokoro now implements an optimized API key validation mechanism. The system proactively caches all API keys and user information in memory and refreshes this cache every 30 seconds. This significantly reduces the time spent validating API keys and checking usage limits:

- Original validation flow: ~0.7s for API key validation, ~1.1s for request validation
- Optimized validation flow: <0.01s for most API key validations

The key cache maintains all active API keys with their associated user, subscription, and usage data. When a request arrives, validation happens entirely in memory rather than making database calls.

### Cache Refresh Mechanism

- The system loads all active API keys from the database during initialization
- Every 30 seconds, it refreshes this cache in the background
- If a requested key is not found in cache, it falls back to direct database validation

### Monitoring Cache Status

You can monitor the API key cache status using the debug endpoint:

```
GET /debug/api-key-cache
```

Response:
```json
{
  "enabled": true,
  "status": "healthy",
  "stats": {
    "initialized": true,
    "api_keys_cached": 25,
    "free_users_cached": 10,
    "active_users_tracked": 35,
    "last_refresh": 1621234567.89,
    "last_refresh_success": true,
    "refresh_interval": 30,
    "middleware_cache_keys": 15
  },
  "message": "API key cache is operational."
}
```

### Benefits

1. **Faster Response Time**: Eliminating database calls for API key validation reduces the time to first audio chunk
2. **Reduced Database Load**: Fewer validation queries to the database improves overall system performance
3. **Improved Streaming Experience**: Lower latency for streaming audio responses
4. **Resilience**: Can continue functioning even during brief database outages

### API Key Format Best Practices

For security and performance reasons, consider the following best practices for API key formats:

1. **Use Unique Prefixes**: Each API key should have a unique prefix (first 16 characters) to ensure proper caching and validation
2. **Recommended Format**: `sk-kokoro-XXXXXXXXXXXX` where X is a unique identifier (e.g., UUID or random string)
3. **Avoid Common Prefixes**: Don't use the same prefix (e.g., `sk-kokor`) for all API keys as this reduces security and efficiency
4. **Key Rotation**: Implement a key rotation policy and use the `rotated_from` field to track key history

Example API key formats:
```
sk-kokoro-f8a91bc64e    # Good: Unique prefix
sk-kokoro-user123456    # Good: User ID embedded in key
sk-kokor-0a2b3c4d5e6f   # Not ideal: Common prefix "sk-kokor"
sk-kokor                # Bad: Too short, not unique
```

Using proper API key formats helps the cache work effectively and prevents validation issues that could lead to performance problems.

## Known Issues

<details>
<summary>Versioning & Development</summary>

I'm doing what I can to keep things stable, but we are on an early and rapid set of build cycles here.
If you run into trouble, you may have to roll back a version on the release tags if something comes up, or build up from source and/or troubleshoot + submit a PR. Will leave the branch up here for the last known stable points:

`v0.1.4`
`v0.0.5post1`

Free and open source is a community effort, and I love working on this project, though there's only really so many hours in a day. If you'd like to support the work, feel free to open a PR, buy me a coffee, or report any bugs/features/etc you find during use.

  <a href="https://www.buymeacoffee.com/remsky" target="_blank">
    <img 
      src="https://cdn.buymeacoffee.com/buttons/v2/default-violet.png" 
      alt="Buy Me A Coffee" 
      style="height: 30px !important;width: 110px !important;"
    >
  </a>

  
</details>

<details>
<summary>Linux GPU Permissions</summary>

Some Linux users may encounter GPU permission issues when running as non-root. 
Can't guarantee anything, but here are some common solutions, consider your security requirements carefully

### Option 1: Container Groups (Likely the best option)
```yaml
services:
  kokoro-tts:
    # ... existing config ...
    group_add:
      - "video"
      - "render"
```

### Option 2: Host System Groups
```yaml
services:
  kokoro-tts:
    # ... existing config ...
    user: "${UID}:${GID}"
    group_add:
      - "video"
```
Note: May require adding host user to groups: `sudo usermod -aG docker,video $USER` and system restart.

### Option 3: Device Permissions (Use with caution)
```yaml
services:
  kokoro-tts:
    # ... existing config ...
    devices:
      - /dev/nvidia0:/dev/nvidia0
      - /dev/nvidiactl:/dev/nvidiactl
      - /dev/nvidia-uvm:/dev/nvidia-uvm
```
⚠️ Warning: Reduces system security. Use only in development environments.

Prerequisites: NVIDIA GPU, drivers, and container toolkit must be properly configured.

Visit [NVIDIA Container Toolkit installation](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) for more detailed information

</details>

## Model and License

<details open>
<summary>Model</summary>

This API uses the [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) model from HuggingFace. 

Visit the model page for more details about training, architecture, and capabilities. I have no affiliation with any of their work, and produced this wrapper for ease of use and personal projects.
</details>
<details>
<summary>License</summary>
This project is licensed under the Apache License 2.0 - see below for details:

- The Kokoro model weights are licensed under Apache 2.0 (see [model page](https://huggingface.co/hexgrad/Kokoro-82M))
- The FastAPI wrapper code in this repository is licensed under Apache 2.0 to match
- The inference code adapted from StyleTTS2 is MIT licensed

The full Apache 2.0 license text can be found at: https://www.apache.org/licenses/LICENSE-2.0
</details>

## Environment Configuration

<details>
<summary>Secure Environment Variables Setup</summary>

For features like usage tracking with Supabase and Stripe integration, you need to securely configure environment variables:

1. **Create your environment file**:
   ```bash
   # Copy the example file
   cp .env.example /path/to/your/config/.env
   
   # Edit with your secure values
   nano /path/to/your/config/.env
   ```

2. **Mount the config directory** when running with Docker:
   ```bash
   docker run -p 8880:8880 \
     -v /path/to/your/config:/app/config \
     ghcr.io/remsky/kokoro-fastapi-cpu:v0.2.2
   ```

3. **With docker-compose**:
   ```yaml
   services:
     kokoro-api:
       image: ghcr.io/remsky/kokoro-fastapi-gpu:v0.2.2
       volumes:
         - /path/to/your/config:/app/config
       ports:
         - "8880:8880"
       deploy:
         resources:
           reservations:
             devices:
               - driver: nvidia
                 count: 1
                 capabilities: [gpu]
   ```

4. **Required variables for usage tracking**:
   - `SUPABASE_URL`: Your Supabase project URL
   - `SUPABASE_KEY`: Your Supabase service key
   - `STRIPE_SECRET_KEY`: Your Stripe secret key
   - `ENABLE_USAGE_TRACKING`: Set to "true" to enable tracking

5. **Security best practices**:
   - Never commit .env files to version control
   - Use Docker secrets in production environments
   - Set appropriate file permissions (chmod 600) on your .env file
   - Consider using a secrets manager for production deployments

For more detailed information on secure deployment in production environments, see the [Secure Deployment Guide](docs/secure-deployment.md).
</details>


Event breakdown: voice_processing: 0.0007s, api_key_validation: 0.7212s, request_validation: 1.1284s, get_voice_path: 0.0010s, get_voice_and_backend: 0.0010s, text_chunking: 0.0707s, kokoro_generate_from_tokens: 0.0000s, chunk_inference_0: 0.0622s, chunk_0_processing: 0.0623s, generate_audio: 0.1348s, total_request: 1.2654s