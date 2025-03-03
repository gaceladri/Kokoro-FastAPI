#!/usr/bin/env python3
"""
Test script for timing logs in the Kokoro API

This script makes a text-to-speech request and shows the detailed timing logs
in the console output. It's meant to verify the timing tracker functionality.

Usage:
    python test_timing_logs.py [--url API_URL] [--key API_KEY] [--text "Text to convert"] [--voice voice_id] [--output output.mp3]
"""

import time
import argparse
import requests

def test_tts_request(api_url, api_key, text, voice, output_file):
    """
    Make a TTS request and print the timing information from the response headers.
    
    Args:
        api_url: The API URL to use
        api_key: Optional API key
        text: Text to convert to speech
        voice: Voice to use
        output_file: Path to save the audio output
    """
    print(f"Making TTS request with timing logs enabled...\n")
    print(f"API URL: {api_url}")
    print(f"Text: {text}")
    print(f"Voice: {voice}")
    print(f"Output: {output_file}")
    print()
    
    # Prepare request headers
    headers = {
        "Content-Type": "application/json",
    }
    
    # Add API key if provided
    if api_key:
        headers["X-API-Key"] = api_key
    
    # Prepare request body
    payload = {
        "model": "kokoro",
        "input": text,
        "voice": voice,
        "response_format": "pcm"
    }
    
    # Make the request with timing
    start_time = time.time()
    
    # Use streaming and collect chunks
    response = requests.post(
        f"{api_url}/v1/audio/speech",
        headers=headers,
        json=payload,
        stream=True  # Enable streaming
    )
    
    # Process streaming response
    first_chunk_time = None
    all_chunks = bytearray()
    chunk_count = 0
    
    for chunk in response.iter_content(chunk_size=512):
        if first_chunk_time is None:
            first_chunk_time = time.time() - start_time
            print(f"\n🚀 First chunk received in: {first_chunk_time:.4f}s")
        
        chunk_count += 1
        all_chunks.extend(chunk)
    
    total_time = time.time() - start_time
    
    # Print response status
    print(f"Response status: {response.status_code}")
    print(f"Received {chunk_count} chunks, total size: {len(all_chunks)} bytes")
    
    # Print all headers
    print("\nResponse headers:")
    for header, value in response.headers.items():
        print(f"  {header}: {value}")
    
    # Check if there's timing information in the headers (now removed, only for backward compatibility)
    timing_header = response.headers.get("X-Timing-Breakdown")
    if timing_header:
        print("\nTiming breakdown from response headers:")
        print(f"  {timing_header}")
        
        # Extract and highlight specific timing metrics
        timing_parts = timing_header.split(", ")
        important_metrics = ["time_to_first_chunk", "time_to_first_chunk_delivery", "total_request"]
        
        print("\n⏱️ Key Timing Metrics:")
        for part in timing_parts:
            for metric in important_metrics:
                if part.startswith(f"{metric}: "):
                    print(f"  ▶️ {part}")
    else:
        print("\nNOTE: Timing information is no longer included in headers")
        print("Timing metrics are now logged in server DEBUG logs only")
        print("Check server logs for timing information with DEBUG log level enabled")
    
    print(f"\n⏱️ Summary of timing measurements:")
    print(f"  ▶️ First chunk received: {first_chunk_time:.4f}s (measured by client)")
    print(f"  ▶️ Total request time: {total_time:.4f}s (measured by client)")
    
    # Check for individual timing headers (now removed)
    first_chunk_server = response.headers.get("X-Time-To-First-Chunk")
    first_chunk_delivery = response.headers.get("X-Time-To-First-Chunk-Delivery")
    
    if first_chunk_server or first_chunk_delivery:
        print("\n⏱️ Server-side timing metrics:")
        if first_chunk_server:
            print(f"  ▶️ Time to first chunk (server): {first_chunk_server}")
        if first_chunk_delivery:
            print(f"  ▶️ Time to first chunk delivery: {first_chunk_delivery}")
    else:
        print("\nNOTE: Individual timing headers are no longer included")
        print("View server DEBUG logs to see these metrics instead")
            
    # Calculate network overhead only if we have server metrics
    if first_chunk_server:
        server_time = float(first_chunk_server.rstrip('s'))
        network_overhead = first_chunk_time - server_time
        print(f"\n⏱️ Network overhead: {network_overhead:.4f}s")

def test_streaming_metrics(api_url, api_key, text, voice, output_file=None):
    """
    Test streaming metrics with a focus on client-side measurements.
    This function is based on the openai_streaming_audio.py example.
    
    Args:
        api_url: The API URL to use
        api_key: Optional API key
        text: Text to convert to speech
        voice: Voice to use
        output_file: Optional path to save the audio output
    """
    print(f"\n=== Testing Streaming Metrics ===")
    print(f"API URL: {api_url}")
    print(f"Text: {text}")
    print(f"Voice: {voice}")
    if output_file:
        print(f"Output: {output_file}")
    print()
    
    # Prepare request headers
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/octet-stream",
        "X-Debug": "true",  # Request debug information if available
    }
    
    # Add API key if provided
    if api_key:
        headers["X-API-Key"] = api_key
    
    # Prepare request body
    payload = {
        "model": "kokoro",
        "input": text,
        "voice": voice,
        "response_format": "pcm"  # similar to WAV, but without a header chunk at the start
    }
    
    try:
        # Optional: Setup audio playback if pyaudio is available
        play_audio = False
        player_stream = None
        try:
            import pyaudio
            player = pyaudio.PyAudio()
            player_stream = player.open(format=pyaudio.paInt16, channels=1, rate=24000, output=True)
            play_audio = True
            print("Audio playback enabled - will play through speakers")
        except (ImportError, ModuleNotFoundError):
            print("PyAudio not available - skipping audio playback")
        
        # Measure streaming performance
        print("\n⏱️ Starting streaming metrics measurement...")
        
        # Start timing
        start_time = time.time()
        
        # Make the streaming request
        response = requests.post(
            f"{api_url}/v1/audio/speech",
            headers=headers,
            json=payload,
            stream=True
        )
        
        # Check if request was successful
        if response.status_code != 200:
            print(f"Error: Request failed with status code {response.status_code}")
            print(f"Response: {response.text}")
            return
        
        # Track streaming metrics
        first_byte_time = None
        total_bytes = 0
        chunk_count = 0
        all_chunks = bytearray()
        chunk_times = []
        
        # Process the streaming response
        last_chunk_time = start_time
        for chunk in response.iter_content(chunk_size=1024):
            current_time = time.time()
            
            # Measure time to first byte (TTFB)
            if chunk_count == 0:
                first_byte_time = current_time - start_time
                print(f"🚀 Time to first byte: {first_byte_time*1000:.2f}ms")
            else:
                # Calculate time since last chunk
                chunk_times.append(current_time - last_chunk_time)
            
            last_chunk_time = current_time
            chunk_count += 1
            bytes_received = len(chunk)
            total_bytes += bytes_received
            
            # Store chunk for saving to file later
            all_chunks.extend(chunk)
            
            # If PyAudio is available, play the audio chunk
            if play_audio and player_stream:
                player_stream.write(chunk)
        
        # Calculate total time
        total_time = time.time() - start_time
        
        # Print results
        print(f"\n=== Streaming Metrics Results ===")
        print(f"✅ Request completed in {total_time*1000:.2f}ms")
        print(f"✅ Time to first byte: {first_byte_time*1000:.2f}ms")
        print(f"✅ Total bytes received: {total_bytes} bytes")
        print(f"✅ Number of chunks: {chunk_count}")
        print(f"✅ Average chunk size: {total_bytes/chunk_count:.2f} bytes")
        
        if chunk_count > 1:
            avg_chunk_interval = sum(chunk_times) / len(chunk_times) * 1000
            print(f"✅ Average time between chunks: {avg_chunk_interval:.2f}ms")
            
            if chunk_times:
                min_interval = min(chunk_times) * 1000
                max_interval = max(chunk_times) * 1000
                print(f"✅ Min/Max chunk interval: {min_interval:.2f}ms / {max_interval:.2f}ms")
        
        print(f"✅ Average chunk receive time: {total_time/chunk_count*1000:.2f}ms")
        
        if total_time > 0:
            print(f"✅ Throughput: {total_bytes/total_time/1024:.2f} KB/s")
        
        # Check for any timing information in response headers
        print("\n=== Response Headers ===")
        for header, value in response.headers.items():
            print(f"  {header}: {value}")
            
        # Check for specific timing or debug headers that might be present
        debug_headers = [
            ('X-Process-Time', 'Server processing time'),
            ('X-Timing-Breakdown', 'Timing breakdown'),
            ('X-Debug-Info', 'Debug information'),
            ('X-Time-To-First-Chunk', 'Time to first chunk'),
            ('X-Time-To-First-Chunk-Delivery', 'Time to first chunk delivery')
        ]
        
        print("\n=== Important Headers ===")
        found_important_headers = False
        for header_name, header_desc in debug_headers:
            if header_name.lower() in [h.lower() for h in response.headers]:
                found_important_headers = True
                for h in response.headers:
                    if h.lower() == header_name.lower():
                        print(f"  ▶️ {header_desc}: {response.headers[h]}")
        
        if not found_important_headers:
            print("  No timing/debug headers found in response.")
            print("  Note: Timing metrics are now logged in server DEBUG logs only.")
            print("  To see timing metrics, check the server logs with DEBUG level enabled.")
        
        # Clean up audio if needed
        if play_audio and player_stream:
            player_stream.stop_stream()
            player_stream.close()
            player.terminate()
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()


def main():
    """Main function to parse arguments and run the test"""
    parser = argparse.ArgumentParser(description="Test timing logs in the Kokoro API")
    parser.add_argument("--url", default="http://localhost:8880", help="API URL")
    parser.add_argument("--key", help="API key")
    parser.add_argument("--text", default="Hello world, this is a test of the timing logs functionality.", help="Text to convert to speech")
    parser.add_argument("--voice", default="af_bella", help="Voice to use")
    parser.add_argument("--output", default="timing_test_output.mp3", help="Output file path")

    args = parser.parse_args()
    
    print("=== Kokoro API Timing Test ===")
    print("Use --instructions for detailed usage information\n")
    

    test_streaming_metrics(
        api_url=args.url, 
        api_key=args.key, 
        text=args.text, 
        voice=args.voice,
        output_file=args.output
    )

    test_tts_request(
        api_url=args.url, 
        api_key=args.key, 
        text=args.text, 
        voice=args.voice, 
        output_file=args.output
    )

if __name__ == "__main__":
    main() 