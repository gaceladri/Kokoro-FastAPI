import json
import statistics
import time
from typing import Dict, Tuple, Optional, List
import os
import sys
from contextlib import contextmanager
import binascii

import requests
import pyaudio
import wave
import io
import struct


# Create a context manager to suppress ALSA and JACK error messages
@contextmanager
def suppress_audio_errors():
    """Context manager to suppress ALSA and JACK error messages."""
    # Save original stderr
    stderr_fd = os.dup(sys.stderr.fileno())
    # Open /dev/null for writing
    devnull = os.open(os.devnull, os.O_WRONLY)
    # Redirect stderr to /dev/null
    os.dup2(devnull, sys.stderr.fileno())
    try:
        yield
    finally:
        # Restore original stderr
        os.dup2(stderr_fd, sys.stderr.fileno())
        # Close file descriptors
        os.close(devnull)
        os.close(stderr_fd)


def is_valid_audio_chunk(chunk: bytes, format_type: str = "pcm") -> bool:
    """
    Basic validation to check if a chunk looks like valid audio data
    
    Args:
        chunk: Audio chunk to validate
        format_type: Expected audio format (pcm or mp3)
        
    Returns:
        True if the chunk appears to be valid audio data
    """
    if len(chunk) == 0:
        return False
        
    if format_type == "mp3":
        # Check for MP3 header (most MP3s start with ID3 or with sync word 0xFF)
        return chunk.startswith(b'ID3') or chunk.startswith(b'\xff\xfb') or chunk.startswith(b'\xff\xfa')
    else:  # PCM
        # For PCM, just check that the chunk has a reasonable size
        # (at least 32 bytes, as valid PCM data should have multiple samples)
        return len(chunk) >= 32


def get_chunk_info(chunk: bytes) -> Dict:
    """
    Get detailed information about an audio chunk
    
    Args:
        chunk: Audio chunk to analyze
        
    Returns:
        Dictionary with chunk information
    """
    info = {
        "size": len(chunk),
        "first_bytes": binascii.hexlify(chunk[:16]).decode() if chunk else None,
    }
    
    # Try to detect format from header
    if chunk:
        if chunk.startswith(b'ID3') or chunk.startswith(b'\xff\xfb') or chunk.startswith(b'\xff\xfa'):
            info["detected_format"] = "mp3"
        elif len(chunk) >= 32:
            # Check if it looks like PCM data by analyzing value distribution
            try:
                # Check sample distribution for 16-bit PCM
                if len(chunk) % 2 == 0:  # Must be even for 16-bit samples
                    samples = struct.unpack(f"<{len(chunk)//2}h", chunk[:len(chunk)//2*2])
                    # Calculate some basic stats
                    sample_min = min(samples)
                    sample_max = max(samples)
                    sample_range = sample_max - sample_min
                    
                    info["sample_min"] = sample_min
                    info["sample_max"] = sample_max
                    info["sample_range"] = sample_range
                    
                    # If we have a reasonable range of values, it's likely PCM
                    if sample_range > 100:
                        info["detected_format"] = "pcm"
                    else:
                        info["detected_format"] = "unknown (small range)"
                else:
                    info["detected_format"] = "unknown (odd length)"
            except Exception as e:
                info["detected_format"] = f"error: {str(e)}"
        else:
            info["detected_format"] = "unknown (too small)"
            
    return info


def measure_time_to_first_chunk(
    api_url: str = "http://localhost:8880/v1/audio/speech",
    api_key: str = None,
    text: str = "This is a test of the time to first audio chunk.",
    voice: str = "af_bella",
    format: str = "pcm",
    debug: bool = True,
    play_audio: bool = False,
    save_chunks: bool = False,
    verbose: bool = False,
) -> Tuple[float, Dict]:
    """
    Measure time to first audio chunk from a streaming TTS request.

    Args:
        api_url: The API URL endpoint
        api_key: Optional API key
        text: Text to convert to speech
        voice: Voice ID to use
        format: Audio format (pcm or mp3)
        debug: Whether to request debug headers
        play_audio: Whether to play the audio as it arrives
        save_chunks: Whether to save received chunks for inspection
        verbose: Whether to print detailed chunk information

    Returns:
        Tuple of (time_to_first_chunk, debug_info)
    """
    # Prepare request headers
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/octet-stream",
    }

    if debug:
        headers["X-Debug"] = "true"

    if api_key:
        headers["X-API-Key"] = api_key

    # Prepare request payload
    payload = {
        "model": "kokoro",
        "input": text,
        "voice": voice,
        "response_format": format,
    }

    # Start timing
    start_time = time.time()
    
    # For collecting chunks if save_chunks is True
    all_chunks = []

    # Make the streaming request
    response = requests.post(api_url, headers=headers, json=payload, stream=True)

    # Check if request was successful
    if response.status_code != 200:
        print(f"Error: Request failed with status code {response.status_code}")
        print(f"Response: {response.text}")
        return None, {}

    # Get the first chunk
    first_chunk = next(response.iter_content(chunk_size=1024))
    first_chunk_time = time.time() - start_time
    
    # Validate and print info about the first chunk
    first_chunk_info = get_chunk_info(first_chunk)
    if verbose:
        print(f"First chunk info: {json.dumps(first_chunk_info, indent=2)}")
    
    is_valid = is_valid_audio_chunk(first_chunk, format)
    if not is_valid:
        print(f"Warning: First chunk may not be valid audio data! Size: {len(first_chunk)} bytes")
    
    if save_chunks:
        all_chunks.append(first_chunk)

    # Get timing information from headers if available
    debug_info = {}
    debug_info["first_chunk_size"] = len(first_chunk)
    debug_info["first_chunk_appears_valid"] = is_valid
    debug_info["first_chunk_details"] = first_chunk_info
    
    if debug and "X-Debug-Info" in response.headers:
        try:
            debug_info["headers"] = json.loads(response.headers["X-Debug-Info"])
        except json.JSONDecodeError:
            debug_info["headers"] = {"raw": response.headers["X-Debug-Info"]}

    # Check for additional timing headers
    if "X-TTFC-Debug" in response.headers:
        try:
            ttfc_debug = json.loads(response.headers["X-TTFC-Debug"])
            debug_info["ttfc_details"] = ttfc_debug
        except json.JSONDecodeError:
            debug_info["ttfc_raw"] = response.headers["X-TTFC-Debug"]
    
    if "X-Timing-Debug" in response.headers:
        try:
            timing_debug = json.loads(response.headers["X-Timing-Debug"])
            debug_info["server_timing"] = timing_debug
        except json.JSONDecodeError:
            debug_info["timing_raw"] = response.headers["X-Timing-Debug"]
    
    # Play audio if requested
    if play_audio:
        # Initialize PyAudio with error suppression
        with suppress_audio_errors():
            p = pyaudio.PyAudio()
            
            # PCM 16-bit, mono, 24kHz - adjust these settings if needed based on your API's output
            sample_width = 2  # 16-bit audio
            channels = 1      # mono
            sample_rate = 24000  # 24 kHz
            
            try:
                # Open a stream
                stream = p.open(
                    format=p.get_format_from_width(sample_width),
                    channels=channels,
                    rate=sample_rate,
                    output=True
                )
                
                # Only play the first chunk if it appears valid
                if is_valid:
                    stream.write(first_chunk)
                else:
                    print("Skipping playback of first chunk (appears invalid)")
                
                # Continue playing the rest of the chunks as they arrive
                print("Playing audio stream as it arrives...")
                chunk_num = 1
                try:
                    for chunk in response.iter_content(chunk_size=1024):
                        if chunk:
                            chunk_num += 1
                            
                            if save_chunks:
                                all_chunks.append(chunk)
                                
                            if verbose:
                                chunk_valid = is_valid_audio_chunk(chunk, format)
                                print(f"Chunk {chunk_num}: {len(chunk)} bytes, valid: {chunk_valid}")
                                
                            stream.write(chunk)
                except KeyboardInterrupt:
                    print("\nAudio playback interrupted.")
                finally:
                    # Clean up
                    stream.stop_stream()
                    stream.close()
            except Exception as e:
                print(f"Error during audio playback: {str(e)}")
            finally:
                p.terminate()
    else:
        # Read a few more chunks to debug and then close
        if verbose:
            try:
                chunk_num = 1
                for i, chunk in enumerate(response.iter_content(chunk_size=1024)):
                    if i >= 5:  # Only collect first 5 additional chunks
                        break
                    chunk_num += 1
                    chunk_valid = is_valid_audio_chunk(chunk, format)
                    if save_chunks:
                        all_chunks.append(chunk)
                    print(f"Chunk {chunk_num}: {len(chunk)} bytes, valid: {chunk_valid}")
            finally:
                # Close the remaining response
                response.close()
        else:
            # Close the remaining response
            response.close()
    
    # Save chunks to file if requested
    if save_chunks:
        total_size = sum(len(chunk) for chunk in all_chunks)
        debug_info["total_chunks"] = len(all_chunks)
        debug_info["total_bytes"] = total_size
        
        # Save to a file for detailed analysis
        filename = f"audio_chunks_{int(time.time())}.{format}"
        with open(filename, "wb") as f:
            for chunk in all_chunks:
                f.write(chunk)
        print(f"Saved {len(all_chunks)} chunks (total {total_size} bytes) to {filename}")

    return first_chunk_time, debug_info


def run_multiple_tests(
    iterations: int = 5,
    api_url: str = "http://localhost:8880/v1/audio/speech",
    api_key: str = None,
    text: str = "This is a test of the time to first audio chunk.",
    voice: str = "af_bella",
    play_audio: bool = False,
    save_chunks: bool = False,
    verbose: bool = False,
) -> None:
    """
    Run multiple tests and report statistics

    Args:
        iterations: Number of tests to run
        api_url: API URL
        api_key: Optional API key
        text: Text to convert
        voice: Voice ID
        play_audio: Whether to play audio as it arrives
        save_chunks: Whether to save received chunks for inspection
        verbose: Whether to print detailed chunk information
    """
    print(f"Running {iterations} tests to measure time to first audio chunk")
    print(f"API URL: {api_url}")
    print(f"Text: {text}")
    print(f"Voice: {voice}")
    print(f"Play audio: {play_audio}")
    print(f"Save chunks: {save_chunks}")
    print(f"Verbose: {verbose}")
    print()

    times = []
    server_processing_times = []
    middleware_times = []
    handler_times = []
    tracking_times = []
    
    # For tracking chunk info across runs
    chunk_sizes = []
    valid_chunks = 0
    
    # Create a table of all timing metrics for detailed analysis
    timing_details = []

    for i in range(iterations):
        print(f"Test {i + 1}/{iterations}... ", end="", flush=True)
        time_to_first_chunk, debug_info = measure_time_to_first_chunk(
            api_url=api_url, api_key=api_key, text=text, voice=voice, 
            play_audio=play_audio, save_chunks=save_chunks, verbose=verbose
        )

        if time_to_first_chunk is None:
            print("Failed!")
            continue

        times.append(time_to_first_chunk)
        
        # Track chunk statistics
        if "first_chunk_size" in debug_info:
            chunk_sizes.append(debug_info["first_chunk_size"])
            if debug_info.get("first_chunk_appears_valid", False):
                valid_chunks += 1
        
        # Extract detailed timing information
        server_time = None
        middleware_time = None
        handler_time = None
        tracking_time = None
        
        # Process server timing information if available
        if "server_timing" in debug_info:
            timing = debug_info["server_timing"]
            
            # Extract middleware time
            if "middleware_duration_before_handler" in timing:
                middleware_time = timing["middleware_duration_before_handler"]
                middleware_times.append(middleware_time)
                
            # Check for handler execution time
            if "time_to_response_start" in timing and "middleware_duration_before_handler" in timing:
                # Handler time is the difference between total and middleware time
                handler_time = timing["time_to_response_start"] - timing["middleware_duration_before_handler"]
                handler_times.append(handler_time)
                
            # Check for usage tracking time
            if "pre_stream_tracking_call" in timing:
                tracking_time = timing["pre_stream_tracking_call"]
                tracking_times.append(tracking_time)
        
        # Process TTFC debug info if available
        server_ttfc = None
        if "ttfc_details" in debug_info:
            ttfc_details = debug_info["ttfc_details"]
            if "time_to_first_chunk_delivery" in ttfc_details:
                server_ttfc = ttfc_details["time_to_first_chunk_delivery"]
                server_processing_times.append(server_ttfc)
        
        # Add all timing details to a list for later analysis
        timing_details.append({
            "client_ttfc": time_to_first_chunk,
            "server_ttfc": server_ttfc,
            "middleware_time": middleware_time,
            "handler_time": handler_time,
            "tracking_time": tracking_time,
            "first_chunk_size": debug_info.get("first_chunk_size", 0),
            "first_chunk_valid": debug_info.get("first_chunk_appears_valid", False)
        })

        # Print basic timing info for this test
        print(f"TTFC: {time_to_first_chunk * 1000:.2f}ms (Chunk size: {debug_info.get('first_chunk_size', 0)} bytes, Valid: {debug_info.get('first_chunk_appears_valid', False)})")
        
        # Print detailed timing if available
        if server_ttfc:
            print(f"  Server TTFC: {server_ttfc * 1000:.2f}ms, Network Overhead: {(time_to_first_chunk - server_ttfc) * 1000:.2f}ms")
        
        if middleware_time:
            print(f"  Middleware: {middleware_time * 1000:.2f}ms")
            
        if handler_time:
            print(f"  Handler: {handler_time * 1000:.2f}ms")
            
        if tracking_time:
            print(f"  Usage Tracking: {tracking_time * 1000:.2f}ms")

        # Wait a bit between tests if not playing the last test
        if i < iterations - 1 or not play_audio:
            time.sleep(0.5)

    # Calculate statistics
    if times:
        avg_time = statistics.mean(times)
        median_time = statistics.median(times)
        min_time = min(times)
        max_time = max(times)
        stdev_time = statistics.stdev(times) if len(times) > 1 else 0

        print("\n=== Results ===")
        print(f"Time to First Chunk (TTFC) statistics:")
        print(f"  Average: {avg_time * 1000:.2f}ms")
        print(f"  Median:  {median_time * 1000:.2f}ms")
        print(f"  Min:     {min_time * 1000:.2f}ms")
        print(f"  Max:     {max_time * 1000:.2f}ms")
        print(f"  StdDev:  {stdev_time * 1000:.2f}ms")
        
        # Print chunk statistics
        if chunk_sizes:
            avg_chunk_size = int(statistics.mean(chunk_sizes))
            min_chunk_size = min(chunk_sizes)
            max_chunk_size = max(chunk_sizes)
            print(f"\nFirst chunk statistics:")
            print(f"  Average size: {avg_chunk_size} bytes")
            print(f"  Min size: {min_chunk_size} bytes")
            print(f"  Max size: {max_chunk_size} bytes")
            print(f"  Valid chunks: {valid_chunks}/{len(chunk_sizes)} ({(valid_chunks/len(chunk_sizes))*100:.1f}%)")

        # Print detailed timing component statistics
        print("\n=== Timing Components ===")
        
        if server_processing_times:
            avg_server = statistics.mean(server_processing_times)
            avg_network = avg_time - avg_server
            print(f"Server Processing Time:")
            print(f"  Average: {avg_server * 1000:.2f}ms ({(avg_server/avg_time)*100:.1f}% of total)")
            print(f"Network/Client Overhead:")
            print(f"  Average: {avg_network * 1000:.2f}ms ({(avg_network/avg_time)*100:.1f}% of total)")
            
        if middleware_times:
            avg_middleware = statistics.mean(middleware_times)
            print(f"Middleware Processing Time:")
            print(f"  Average: {avg_middleware * 1000:.2f}ms ({(avg_middleware/avg_time)*100:.1f}% of total)")
            
        if handler_times:
            avg_handler = statistics.mean(handler_times)
            print(f"Handler Processing Time:")
            print(f"  Average: {avg_handler * 1000:.2f}ms ({(avg_handler/avg_time)*100:.1f}% of total)")
            
        if tracking_times:
            avg_tracking = statistics.mean(tracking_times)
            print(f"Usage Tracking Time:")
            print(f"  Average: {avg_tracking * 1000:.2f}ms ({(avg_tracking/avg_time)*100:.1f}% of total)")
                
    else:
        print("\nNo successful tests to analyze.")


def play_single_audio(
    api_url: str = "http://localhost:8880/v1/audio/speech",
    api_key: str = None,
    text: str = "This is a test of the audio streaming functionality.",
    voice: str = "af_bella",
    format: str = "pcm",
    save_chunks: bool = False,
    verbose: bool = False,
) -> None:
    """
    Play a single audio stream without measuring performance

    Args:
        api_url: API URL
        api_key: Optional API key
        text: Text to convert
        voice: Voice ID
        format: Audio format
        save_chunks: Whether to save received chunks for inspection
        verbose: Whether to print detailed chunk information
    """
    print(f"Playing audio for text: \"{text}\"")
    print(f"Voice: {voice}")
    print(f"Save chunks: {save_chunks}")
    print(f"Verbose: {verbose}")
    
    # Just play the audio with measurement as a side effect
    measure_time_to_first_chunk(
        api_url=api_url, 
        api_key=api_key, 
        text=text, 
        voice=voice, 
        format=format,
        play_audio=True,
        save_chunks=save_chunks,
        verbose=verbose
    )


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Test TTS API performance and play audio")
    parser.add_argument("--url", type=str, default="http://localhost:8880/v1/audio/speech", 
                        help="API URL endpoint")
    parser.add_argument("--key", type=str, default=None, help="API key")
    parser.add_argument("--text", type=str, default="This is a test of the time to first audio chunk.",
                        help="Text to convert to speech")
    parser.add_argument("--voice", type=str, default="af_bella", help="Voice ID to use")
    parser.add_argument("--iterations", type=int, default=5, help="Number of test iterations")
    parser.add_argument("--play", action="store_true", help="Play audio as it arrives")
    parser.add_argument("--play-only", action="store_true", help="Just play audio without performance testing")
    parser.add_argument("--quiet", action="store_true", help="Suppress ALSA/JACK error messages")
    parser.add_argument("--save-chunks", action="store_true", help="Save audio chunks to file for inspection")
    parser.add_argument("--verbose", action="store_true", help="Print detailed information about chunks")
    parser.add_argument("--format", type=str, choices=["pcm", "mp3"], default="pcm", 
                        help="Audio format (pcm or mp3)")
    
    args = parser.parse_args()
    
    # If quiet flag is set, run the entire program with suppressed audio errors
    if args.quiet:
        with suppress_audio_errors():
            if args.play_only:
                play_single_audio(
                    api_url=args.url,
                    api_key=args.key,
                    text=args.text,
                    voice=args.voice,
                    format=args.format,
                    save_chunks=args.save_chunks,
                    verbose=args.verbose
                )
            else:
                run_multiple_tests(
                    iterations=args.iterations,
                    api_url=args.url,
                    api_key=args.key,
                    text=args.text,
                    voice=args.voice,
                    play_audio=args.play,
                    save_chunks=args.save_chunks,
                    verbose=args.verbose
                )
    else:
        if args.play_only:
            play_single_audio(
                api_url=args.url,
                api_key=args.key,
                text=args.text,
                voice=args.voice,
                format=args.format,
                save_chunks=args.save_chunks,
                verbose=args.verbose
            )
        else:
            run_multiple_tests(
                iterations=args.iterations,
                api_url=args.url,
                api_key=args.key,
                text=args.text,
                voice=args.voice,
                play_audio=args.play,
                save_chunks=args.save_chunks,
                verbose=args.verbose
            )
