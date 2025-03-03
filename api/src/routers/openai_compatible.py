"""OpenAI-compatible router for text-to-speech"""

import asyncio
import io
import json
import os
import tempfile
import time
from datetime import datetime
from typing import Dict, List, Optional, Union

import aiofiles
import torch
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from loguru import logger

from api.src.core.timing import TimingTracker

from ..core.config import settings
from ..services.audio import AudioService
from ..services.tts_service import TTSService
from ..services.usage_tracking.usage_service import (
    UsageTrackingService,
)
from ..structures import OpenAISpeechRequest


def estimate_audio_duration(audio_size_bytes: int, format: str) -> int:
    """Estimate audio duration in milliseconds based on file size and format.

    Args:
        audio_size_bytes: Size of the audio file in bytes
        format: Audio format (mp3, opus, aac, flac, wav, pcm)

    Returns:
        Estimated duration in milliseconds
    """
    # Bitrates for different formats in bits per second
    # These are rough estimates and will vary based on quality settings
    bitrates = {
        "mp3": 128000,  # 128 kbps (typical MP3)
        "opus": 64000,  # 64 kbps (good quality Opus)
        "aac": 128000,  # 128 kbps (typical AAC)
        "flac": 700000,  # ~700 kbps (typical FLAC)
        "wav": 768000,  # 16-bit stereo at 24kHz
        "pcm": 384000,  # 16-bit mono at 24kHz
    }

    # Use the appropriate bitrate or default to mp3
    bitrate = bitrates.get(format.lower(), 128000)

    # Convert bytes to bits
    audio_size_bits = audio_size_bytes * 8

    # Calculate duration: size/bitrate = seconds
    duration_seconds = audio_size_bits / bitrate

    # Convert to milliseconds
    return int(duration_seconds * 1000)


# Load OpenAI mappings
def load_openai_mappings() -> Dict:
    """Load OpenAI voice and model mappings from JSON"""
    api_dir = os.path.dirname(os.path.dirname(__file__))
    mapping_path = os.path.join(api_dir, "core", "openai_mappings.json")
    try:
        with open(mapping_path, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load OpenAI mappings: {e}")
        return {"models": {}, "voices": {}}


# Global mappings
_openai_mappings = load_openai_mappings()


router = APIRouter(
    tags=["OpenAI Compatible TTS"],
    responses={404: {"description": "Not found"}},
)

# Global services
_tts_service = None
_usage_service = None
_init_lock = None


async def get_tts_service() -> TTSService:
    """Get global TTSService instance"""
    global _tts_service, _init_lock

    # Create lock if needed
    if _init_lock is None:
        import asyncio

        _init_lock = asyncio.Lock()

    # Initialize service if needed
    if _tts_service is None:
        async with _init_lock:
            # Double check pattern
            if _tts_service is None:
                _tts_service = await TTSService.create()
                logger.info("Created global TTSService instance")

    return _tts_service


async def get_usage_service() -> UsageTrackingService:
    """Get global UsageTrackingService instance"""
    global _usage_service, _init_lock

    # Create lock if needed
    if _init_lock is None:
        import asyncio

        _init_lock = asyncio.Lock()

    # Initialize service if needed
    if _usage_service is None:
        async with _init_lock:
            # Double check pattern
            if _usage_service is None:
                _usage_service = await UsageTrackingService.create()
                logger.info("Created global UsageTrackingService instance")

    return _usage_service


def get_model_name(model: str) -> str:
    """Get internal model name from OpenAI model name"""
    base_name = _openai_mappings["models"].get(model)
    if not base_name:
        raise ValueError(f"Unsupported model: {model}")
    return base_name + ".pth"


async def process_voices(
    voice_input: Union[str, List[str]], tts_service: TTSService
) -> str:
    """Process voice input, handling both string and list formats

    Returns:
        Voice name to use (with weights if specified)
    """
    # Convert input to list of voices
    if isinstance(voice_input, str):
        # Check if it's an OpenAI voice name
        mapped_voice = _openai_mappings["voices"].get(voice_input)
        if mapped_voice:
            voice_input = mapped_voice
        # Split on + but preserve any parentheses
        voices = []
        for part in voice_input.split("+"):
            part = part.strip()
            if not part:
                continue
            # Extract voice name without weight
            voice_name = part.split("(")[0].strip()
            # Check if it's a valid voice
            available_voices = await tts_service.list_voices()
            if voice_name not in available_voices:
                raise ValueError(
                    f"Voice '{voice_name}' not found. Available voices: {', '.join(sorted(available_voices))}"
                )
            voices.append(part)
    else:
        # For list input, map each voice if it's an OpenAI voice name
        voices = []
        for v in voice_input:
            mapped = _openai_mappings["voices"].get(v, v)
            voice_name = mapped.split("(")[0].strip()
            # Check if it's a valid voice
            available_voices = await tts_service.list_voices()
            if voice_name not in available_voices:
                raise ValueError(
                    f"Voice '{voice_name}' not found. Available voices: {', '.join(sorted(available_voices))}"
                )
            voices.append(mapped)

    if not voices:
        raise ValueError("No voices provided")

    # For multiple voices, combine them with +
    return "+".join(voices)


async def stream_audio_chunks(
    response: StreamingResponse,
    character_count: int,
    word_count: Optional[int] = None,
    request: Request = None,
    voice_id: Optional[str] = None,
    tracking_service: Optional[UsageTrackingService] = None,
) -> StreamingResponse:
    """Wrap a streaming response to track usage metrics."""
    # Get the original iterator
    original_iterator = response.body_iterator

    # Get current time for metrics
    start_time = time.time()
    stream_wrapper_start_time = time.time()

    # Get timing tracker from request state
    timing_tracker = getattr(request.state, "timing_tracker", None) if request else None
    if timing_tracker:
        timing_tracker.mark("stream_wrapper_start")

    # Get the request start time from request state if available
    request_start_time = getattr(request.state, "request_start_time", None) if request else None

    # Prepare usage tracking data
    if tracking_service and request:
        try:
            # Calculate audio duration estimate based on character count
            audio_duration_estimate = None
            if character_count > 0:
                # Estimate audio duration based on character count and format
                # For very short text, streaming overhead is significant
                format_name = request.query_params.get("response_format", "mp3")
                audio_duration_estimate = estimate_audio_duration(
                    character_count, format_name
                )
        except Exception as e:
            # Don't let estimation errors block streaming
            logger.warning(f"Error estimating audio duration: {e}")

    # Start timing for pre-streaming setup
    if timing_tracker:
        timing_tracker.start_event("pre_streaming_setup")

    # Skip pre-stream tracking to reduce latency - we'll track after the first chunk
    pre_tracking_start = time.time()
    initial_tracking_done = False

    pre_streaming_duration = time.time() - pre_tracking_start
    if timing_tracker:
        timing_tracker.end_event("pre_streaming_setup")
        logger.debug(
            f"⏱️ Pre-streaming setup time: {timing_tracker.durations.get('pre_streaming_setup', 0):.4f}s"
        )

    # Record total wrapper setup time before the iterator
    wrapper_setup_time = time.time() - stream_wrapper_start_time
    logger.debug(f"⏱️ Stream wrapper setup time: {wrapper_setup_time:.4f}s")

    # Get timing tracker from request state
    if timing_tracker:
        timing_tracker.start_event("time_to_first_chunk_delivery")
        logger.debug(f"⏱️ Starting time to first chunk measurement")

    # Wrap the original iterator to track actual metrics
    async def tracked_iterator():
        nonlocal start_time, initial_tracking_done
        iterator_start_time = time.time()

        total_bytes = 0
        total_chunks = 0
        last_chunk = None
        first_chunk_sent = False
        first_chunk_timing = {}
        
        # Keep a reference to the original_iterator to ensure it's not garbage collected
        _original_iterator = original_iterator

        try:
            # Stream all audio chunks
            logger.debug(
                f"⏱️ Starting audio chunk iteration at {time.time() - start_time:.4f}s"
            )
            iterator_begin = time.time()

            # Wrap iterator to track timing for entering/exiting the first yield
            async for chunk in _original_iterator:
                chunk_received_time = time.time()
                
                # Skip empty chunks
                if chunk is None or len(chunk) == 0:
                    logger.warning("Received empty chunk from original iterator, skipping")
                    continue

                # Mark the first chunk delivery time
                if not first_chunk_sent:
                    first_chunk_received = chunk_received_time - iterator_begin
                    if timing_tracker:
                        ttfc = timing_tracker.end_event("time_to_first_chunk_delivery")
                        first_chunk_timing["time_to_first_chunk_delivery"] = ttfc
                        logger.debug(f"⏱️ Time to first chunk (delivery): {ttfc:.4f}s")
                        logger.debug(
                            f"⏱️ Time from iterator start to first chunk: {first_chunk_received:.4f}s"
                        )

                        # Calculate and log the full trace time from request to first chunk
                        request_start = getattr(
                            request.state, "request_start_time", None
                        )
                        if request_start:
                            full_trace = chunk_received_time - request_start
                            logger.debug(
                                f"⏱️ Full trace from request to first chunk: {full_trace:.4f}s"
                            )
                            first_chunk_timing["full_trace_time"] = full_trace

                    first_chunk_sent = True

                    # After the first chunk is sent, track the usage in the background
                    # This moves the database operation out of the critical path
                    if tracking_service and request and not initial_tracking_done:
                        initial_tracking_done = True
                        # Create a task but don't await it
                        asyncio.create_task(
                            tracking_service.track_usage_event(
                                request=request,
                                endpoint=request.url.path,
                                http_method=request.method,
                                status_code=200,
                                character_count=character_count,
                                word_count=word_count,
                                voice_id=voice_id,
                                audio_duration_ms=audio_duration_estimate,
                                processing_time_ms=int(
                                    (time.time() - start_time) * 1000
                                ),
                                metadata={
                                    "streaming": True,
                                    "is_estimate": True,
                                    "ttfc_details": first_chunk_timing,
                                },
                            )
                        )

                # Update total bytes and chunks metrics
                chunk_size = len(chunk)
                total_bytes += chunk_size
                total_chunks += 1
                last_chunk = chunk

                # Record chunk size for first chunk
                if total_chunks == 1:
                    logger.debug(f"⏱️ First chunk size: {chunk_size} bytes")

                # Measure yield timing
                before_yield = time.time()
                yield chunk  # This is the critical line that sends audio data to the client
                after_yield = time.time()
                yield_time = after_yield - before_yield

                # Log yield time only for first chunk to avoid log spam
                if total_chunks == 1:
                    logger.debug(f"⏱️ First chunk yield time: {yield_time:.4f}s")

                # For debugging, log first few chunks
                if total_chunks <= 5:
                    logger.debug(
                        f"Yielded chunk {total_chunks}: {chunk_size} bytes after {time.time() - start_time:.4f}s"
                    )

            # All chunks processed
            processing_time = time.time() - start_time
            logger.info(
                f"Audio streaming completed: {total_chunks} chunks, {total_bytes} bytes in {processing_time:.4f}s"
            )

            # Final tracking with actual metrics after streaming completes
            if tracking_service and request:
                # Now we have exact size and can better estimate duration
                audio_format = request.query_params.get("response_format", "pcm")
                audio_duration = estimate_audio_duration(total_bytes, audio_format)
                logger.debug(
                    f"Audio duration estimate from bytes: {audio_duration}ms ({audio_format})"
                )
                # Track final statistics in background
                try:
                    await tracking_service.track_usage_event(
                        request=request,
                        endpoint=request.url.path,
                        http_method=request.method,
                        status_code=200,
                        character_count=character_count,
                        word_count=word_count,
                        voice_id=voice_id,
                        audio_duration_ms=audio_duration,
                        processing_time_ms=int(processing_time * 1000),
                        metadata={
                            "streaming": True,
                            "total_bytes": total_bytes,
                            "chunk_count": total_chunks,
                        },
                    )
                except Exception as tracking_e:
                    logger.error(f"Error during final usage tracking: {tracking_e}")

        except Exception as e:
            # Log any errors that occur during streaming
            logger.error(f"Error during audio streaming: {e}")
            # Track error in background if available
            if tracking_service and request:
                try:
                    await tracking_service.track_usage_event(
                        request=request,
                        endpoint=request.url.path,
                        http_method=request.method,
                        status_code=500,  # Internal error
                        character_count=character_count,
                        word_count=word_count,
                        voice_id=voice_id,
                        processing_time_ms=int((time.time() - start_time) * 1000),
                        metadata={"streaming": True, "error": str(e)},
                    )
                except Exception as tracking_e:
                    logger.error(f"Error during error tracking: {tracking_e}")
            raise

    # Replace the original iterator with our tracked version
    response.body_iterator = tracked_iterator()

    # Add TTFC debug headers if requested
    if request and request.headers.get("X-Debug") == "true":
        timing_data = {}
        if timing_tracker and timing_tracker.durations:
            timing_data = {k: v for k, v in timing_tracker.durations.items()}
        response.headers["X-TTFC-Debug"] = json.dumps(
            {"time_to_first_chunk_delivery": 0}
        )  # Will be updated by iterator

    # Calculate response start time - safely handle timing info
    middleware_duration = getattr(request.state, "middleware_duration", 0) if request else 0
    time_to_response_start = 0
    if request_start_time:
        time_to_response_start = time.time() - request_start_time

    # Add timing headers for client debugging
    response.headers["X-Timing-Debug"] = json.dumps(
        {
            "pre_stream_tracking_call": pre_streaming_duration,
            "wrapper_setup_time": wrapper_setup_time,
            "middleware_duration_before_handler": middleware_duration,
            "time_to_response_start": time_to_response_start,
        }
    )

    return response


def get_audio_bitrate(format_name: str) -> Optional[int]:
    """
    Get the estimated bitrate for an audio format in bits per second.

    Args:
        format_name: Audio format name (mp3, opus, aac, etc.)

    Returns:
        int: Bitrate in bits per second or None if unknown format
    """
    format_name = format_name.lower()

    # Common formats and their typical bitrates
    bitrates = {
        "mp3": 128000,  # 128 kbps
        "opus": 64000,  # 64 kbps
        "aac": 128000,  # 128 kbps
        "flac": 700000,  # ~700 kbps (variable)
        "wav": 768000,  # 16-bit stereo at 24kHz
        "pcm": 384000,  # 16-bit mono at 24kHz
    }

    return bitrates.get(format_name)


@router.post("/audio/speech")
async def create_speech(
    request: OpenAISpeechRequest,
    client_request: Request,
    tts_service: TTSService = Depends(get_tts_service),
) -> Response:
    """Generate speech from text using OpenAI compatible API."""
    handler_start_time = time.time()
    try:
        # Create timing tracker
        timing_tracker = TimingTracker()
        client_request.state.timing_tracker = timing_tracker
        timing_tracker.mark("handler_start")

        # Log that we started processing the request
        logger.info(
            f"Processing speech request for text: '{request.input[:30]}...' with voice: {request.voice}"
        )

        # Extract request data
        input_text = request.input
        voice_id = request.voice
        speed = request.speed
        response_format = request.response_format
        normalization_options = getattr(request, "normalization_options", None)

        # Early validation
        timing_tracker.start_event("input_validation")
        validate_input_length(input_text)
        timing_tracker.end_event("input_validation")
        logger.debug(
            f"⏱️ Input validation: {timing_tracker.durations.get('input_validation', 0):.4f}s"
        )

        # Get validation timing information for debugging
        timing_header = client_request.headers.get("X-Timing-Breakdown", "")

        # Check if we're in emergency mode
        is_emergency_mode = getattr(client_request.state, "is_emergency_mode", False)
        if is_emergency_mode:
            timing_tracker.mark("emergency_mode_enabled")
            logger.warning(
                "Processing speech request in emergency mode due to database unavailability"
            )

        # Set up usage tracking if enabled
        timing_tracker.start_event("usage_tracking_setup")
        usage_tracking = None
        if settings.enable_usage_tracking and not is_emergency_mode:
            try:
                usage_tracking = await UsageTrackingService.create()
            except Exception as e:
                logger.error(f"Failed to initialize usage tracking: {e}")
                # Don't let tracking errors prevent the main functionality
        timing_tracker.end_event("usage_tracking_setup")
        logger.debug(
            f"⏱️ Usage tracking setup: {timing_tracker.durations.get('usage_tracking_setup', 0):.4f}s"
        )

        # Detect if streaming is requested from the request body parameter
        streaming_requested = request.stream

        try:
            # For streaming responses (e.g. for web players)
            if streaming_requested:
                streaming_start = time.time()
                logger.info(
                    f"Streaming speech response for format: {response_format} (stream parameter: {streaming_requested})"
                )

                # Log handler pre-processing time before we start TTS
                handler_preprocesssing_time = time.time() - handler_start_time
                logger.debug(
                    f"⏱️ Handler pre-processing time: {handler_preprocesssing_time:.4f}s"
                )

                # Set up speech generator
                timing_tracker.start_event("tts_service_setup")
                speech_generator = tts_service.generate_audio_stream(
                    text=input_text,
                    voice=voice_id,
                    speed=speed,
                    output_format=response_format,
                    lang_code=request.lang if hasattr(request, "lang") else None,
                    normalization_options=normalization_options,
                    request=client_request,  # Pass the request to enable timing tracking
                )
                tts_setup_time = timing_tracker.end_event("tts_service_setup")
                logger.debug(f"⏱️ TTS service setup time: {tts_setup_time:.4f}s")

                # Validate that we actually have a generator
                if speech_generator is None:
                    logger.error("TTS service returned None for speech generator")
                    raise HTTPException(status_code=500, detail="Failed to create audio stream")

                # Log intermediate timing results (before streaming)
                logger.info("Intermediate timing results (before streaming):")
                timing_tracker.log_breakdown(log_level="DEBUG")

                # Get the appropriate content type
                timing_tracker.start_event("response_preparation")
                content_type = get_content_type(response_format)

                # Check if the speech generator will produce valid audio
                try:
                    # Create a wrapper for the speech generator to handle potential empty chunks
                    async def filtered_generator():
                        empty_chunk_count = 0
                        async for chunk in speech_generator:
                            if chunk is None or len(chunk) == 0:
                                empty_chunk_count += 1
                                if empty_chunk_count <= 5:  # Log only the first few to avoid spam
                                    logger.warning(f"Empty chunk received from TTS service (count: {empty_chunk_count})")
                                continue
                            yield chunk
                        
                        if empty_chunk_count > 0:
                            logger.warning(f"Total empty chunks filtered out: {empty_chunk_count}")

                    # Create streaming response with the filtered generator
                    streaming_response = StreamingResponse(
                        filtered_generator(),
                        media_type=content_type,
                        headers={
                            "Content-Type": content_type,
                        },
                    )
                except Exception as e:
                    logger.error(f"Error preparing streaming response: {e}")
                    raise HTTPException(status_code=500, detail="Error preparing audio stream")

                # Wrap the streaming response with tracking
                character_count = len(input_text)
                word_count = calculate_word_count(input_text)

                response_prep_time = timing_tracker.end_event("response_preparation")
                logger.debug(f"⏱️ Response preparation time: {response_prep_time:.4f}s")

                # Measure time to prepare the entire response before streaming
                stream_total_prep_time = time.time() - streaming_start
                logger.debug(
                    f"⏱️ Total time to prepare streaming response: {stream_total_prep_time:.4f}s"
                )
                logger.debug(
                    f"⏱️ Total handler time before streaming starts: {time.time() - handler_start_time:.4f}s"
                )

                # Add debug info to response headers
                streaming_response.headers["X-Handler-Prep-Time"] = str(
                    handler_preprocesssing_time
                )
                streaming_response.headers["X-TTS-Setup-Time"] = str(tts_setup_time)
                streaming_response.headers["X-Response-Prep-Time"] = str(
                    response_prep_time
                )
                streaming_response.headers["X-Stream-Total-Prep-Time"] = str(
                    stream_total_prep_time
                )

                # Now pass the streaming response to our tracker
                return await stream_audio_chunks(
                    response=streaming_response,
                    character_count=character_count,
                    word_count=word_count,
                    request=client_request,
                    voice_id=voice_id,
                    tracking_service=usage_tracking,
                )

            # For non-streaming responses (regular downloads)
            else:
                logger.info(
                    f"Processing non-streaming speech request for format: {response_format}"
                )

                # Generate audio
                timing_tracker.start_event("generate_audio")
                audio_result, duration = await tts_service.generate_audio(
                    text=input_text,
                    voice=voice_id,
                    speed=speed,
                )
                timing_tracker.end_event("generate_audio")
                logger.info(f"Audio generation completed in {duration:.4f}s")

                # Convert the numpy array to the requested format
                timing_tracker.start_event("convert_audio")
                audio_data = await AudioService.convert_audio(
                    audio_result,
                    24000,  # Sample rate is fixed at 24kHz
                    response_format,
                    speed,
                    input_text,
                )
                timing_tracker.end_event("convert_audio")

                # Log timing results at debug level (only once)
                logger.info("Timing results after audio generation:")
                timing_tracker.log_breakdown(log_level="DEBUG")

                # Calculate character count for usage tracking
                character_count = len(input_text)
                word_count = calculate_word_count(input_text)

                # Set state variables for tracking in middleware
                client_request.state.audio_duration_ms = int(
                    len(audio_result) / 24
                )  # Convert samples to ms
                client_request.state.word_count = word_count

                # Track usage asynchronously if enabled
                if usage_tracking and not is_emergency_mode:
                    try:
                        # No need to await this, can run in background
                        asyncio.create_task(
                            usage_tracking.track_usage_event(
                                request=client_request,
                                endpoint="/v1/audio/speech",
                                http_method="POST",
                                status_code=200,
                                character_count=character_count,
                                processing_time_ms=int(
                                    timing_tracker.to_dict().get("total_request", 0)
                                    * 1000
                                ),
                                voice_id=voice_id,
                                audio_duration_ms=int(len(audio_result) / 24),
                                word_count=word_count,
                            )
                        )
                    except Exception as e:
                        logger.error(f"Failed to track usage: {e}")

                # Create response with appropriate headers
                filename = f"speech_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.{response_format}"
                audio_size = len(audio_data)

                # Get the appropriate content type
                content_type = get_content_type(response_format)

                # Add OpenAI compatible response headers
                headers = {
                    "Content-Type": content_type,
                    "Content-Length": str(audio_size),
                    "Content-Disposition": f"attachment; filename={filename}",
                    "X-Character-Count": str(character_count),
                    "X-Word-Count": str(word_count),
                }

                # We already logged the timing information above, no need to log again

                # Estimate audio duration
                if audio_size > 0:
                    estimated_duration = estimate_audio_duration(
                        audio_size, response_format
                    )
                    if estimated_duration:
                        headers["X-Audio-Duration"] = str(estimated_duration)

                # Log final stats
                logger.info(
                    f"Completed speech generation: {character_count} chars, {word_count} words, {audio_size} bytes"
                )

                return Response(content=audio_data, headers=headers)

        except ValueError as e:
            logger.warning(f"Validation error in speech generation: {str(e)}")
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "validation_error",
                    "message": str(e),
                    "type": "invalid_request_error",
                },
            )

    except Exception as e:
        logger.exception(f"Error generating speech: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "message": "Failed to generate speech",
                    "type": "server_error",
                }
            },
        )


def calculate_word_count(text: str) -> int:
    """
    Calculate the number of words in a string.

    Args:
        text: Input text

    Returns:
        int: Word count
    """
    if not text:
        return 0
    # Split by whitespace and count non-empty words
    return len([word for word in text.split() if word])


@router.get("/download/{filename}")
async def download_audio_file(filename: str):
    """Download a generated audio file from temp storage"""
    try:
        from ..core.paths import _find_file, get_content_type

        # Search for file in temp directory
        file_path = await _find_file(
            filename=filename, search_paths=[settings.temp_file_dir]
        )

        # Get content type from path helper
        content_type = await get_content_type(file_path)

        return FileResponse(
            file_path,
            media_type=content_type,
            filename=filename,
            headers={
                "Cache-Control": "no-cache",
                "Content-Disposition": f"attachment; filename={filename}",
            },
        )

    except Exception as e:
        logger.error(f"Error serving download file {filename}: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "server_error",
                "message": "Failed to serve audio file",
                "type": "server_error",
            },
        )


@router.get("/models")
async def list_models():
    """List all available models"""
    try:
        # Create standard model list
        models = [
            {
                "id": "tts-1",
                "object": "model",
                "created": 1686935002,
                "owned_by": "kokoro",
            },
            {
                "id": "tts-1-hd",
                "object": "model",
                "created": 1686935002,
                "owned_by": "kokoro",
            },
            {
                "id": "kokoro",
                "object": "model",
                "created": 1686935002,
                "owned_by": "kokoro",
            },
        ]

        return {"object": "list", "data": models}
    except Exception as e:
        logger.error(f"Error listing models: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "server_error",
                "message": "Failed to retrieve model list",
                "type": "server_error",
            },
        )


@router.get("/models/{model}")
async def retrieve_model(model: str):
    """Retrieve a specific model"""
    try:
        # Define available models
        models = {
            "tts-1": {
                "id": "tts-1",
                "object": "model",
                "created": 1686935002,
                "owned_by": "kokoro",
            },
            "tts-1-hd": {
                "id": "tts-1-hd",
                "object": "model",
                "created": 1686935002,
                "owned_by": "kokoro",
            },
            "kokoro": {
                "id": "kokoro",
                "object": "model",
                "created": 1686935002,
                "owned_by": "kokoro",
            },
        }

        # Check if requested model exists
        if model not in models:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "model_not_found",
                    "message": f"Model '{model}' not found",
                    "type": "invalid_request_error",
                },
            )

        # Return the specific model
        return models[model]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving model {model}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "server_error",
                "message": "Failed to retrieve model information",
                "type": "server_error",
            },
        )


@router.get("/audio/voices")
async def list_voices():
    """List all available voices for text-to-speech"""
    try:
        tts_service = await get_tts_service()
        voices = await tts_service.list_voices()
        return {"voices": voices}
    except Exception as e:
        logger.error(f"Error listing voices: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "server_error",
                "message": "Failed to retrieve voice list",
                "type": "server_error",
            },
        )


@router.post("/audio/voices/combine")
async def combine_voices(request: Union[str, List[str]]):
    """Combine multiple voices into a new voice and return the .pt file.

    Args:
        request: Either a string with voices separated by + (e.g. "voice1+voice2")
                or a list of voice names to combine

    Returns:
        FileResponse with the combined voice .pt file

    Raises:
        HTTPException:
            - 400: Invalid request (wrong number of voices, voice not found)
            - 500: Server error (file system issues, combination failed)
    """
    # Check if local voice saving is allowed
    if not settings.allow_local_voice_saving:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "permission_denied",
                "message": "Local voice saving is disabled",
                "type": "permission_error",
            },
        )

    try:
        # Convert input to list of voices
        if isinstance(request, str):
            # Check if it's an OpenAI voice name
            mapped_voice = _openai_mappings["voices"].get(request)
            if mapped_voice:
                request = mapped_voice
            voices = [v.strip() for v in request.split("+") if v.strip()]
        else:
            # For list input, map each voice if it's an OpenAI voice name
            voices = [_openai_mappings["voices"].get(v, v) for v in request]
            voices = [v.strip() for v in voices if v.strip()]

        if not voices:
            raise ValueError("No voices provided")

        # For multiple voices, validate base voices exist
        tts_service = await get_tts_service()
        available_voices = await tts_service.list_voices()
        for voice in voices:
            if voice not in available_voices:
                raise ValueError(
                    f"Base voice '{voice}' not found. Available voices: {', '.join(sorted(available_voices))}"
                )

        # Combine voices
        combined_tensor = await tts_service.combine_voices(voices=voices)
        combined_name = "+".join(voices)

        # Save to temp file
        temp_dir = tempfile.gettempdir()
        voice_path = os.path.join(temp_dir, f"{combined_name}.pt")
        buffer = io.BytesIO()
        torch.save(combined_tensor, buffer)
        async with aiofiles.open(voice_path, "wb") as f:
            await f.write(buffer.getvalue())

        return FileResponse(
            voice_path,
            media_type="application/octet-stream",
            filename=f"{combined_name}.pt",
            headers={
                "Content-Disposition": f"attachment; filename={combined_name}.pt",
                "Cache-Control": "no-cache",
            },
        )

    except ValueError as e:
        logger.warning(f"Invalid voice combination request: {str(e)}")
        raise HTTPException(
            status_code=400,
            detail={
                "error": "validation_error",
                "message": str(e),
                "type": "invalid_request_error",
            },
        )
    except RuntimeError as e:
        logger.error(f"Voice combination processing error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "processing_error",
                "message": "Failed to process voice combination request",
                "type": "server_error",
            },
        )
    except Exception as e:
        logger.error(f"Unexpected error in voice combination: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "server_error",
                "message": "An unexpected error occurred",
                "type": "server_error",
            },
        )


@router.get("/usage")
async def get_usage_stats(
    request: Request,
    usage_service: UsageTrackingService = Depends(get_usage_service),
):
    """Get usage statistics for the current user."""
    try:
        if not settings.enable_usage_tracking:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "usage_tracking_disabled",
                    "message": "Usage tracking is not enabled",
                    "type": "invalid_request_error",
                },
            )

        # Get request type from request state (set by middleware)
        request_type = getattr(request.state, "request_type", None)

        # If request type is not set, determine it based on headers
        if not request_type:
            api_key = request.headers.get("X-API-Key")
            user_id = request.headers.get("X-User-ID")

            if api_key:
                request_type = "paid"
            elif user_id:
                request_type = "free"
            else:
                request_type = "demo"

        # Get usage statistics based on request type
        if request_type == "paid":
            # Paid tier statistics
            user_info = getattr(request.state, "user", None)
            if not user_info:
                # Try to get user info from API key
                api_key = request.headers.get("X-API-Key", "")
                api_key_prefix = api_key[:8] if api_key else None

                if not api_key_prefix:
                    raise HTTPException(
                        status_code=401,
                        detail={
                            "error": "missing_api_key",
                            "message": "API key is required",
                            "type": "authentication_error",
                        },
                    )

                # Validate API key to get user info
                is_valid, _, request_info = await usage_service._validate_api_key(
                    api_key, skip_limit_check=True
                )
                if not is_valid or not request_info:
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": "invalid_api_key",
                            "message": "Invalid API key",
                            "type": "authentication_error",
                        },
                    )

                user_info = request_info["user"]

            # Get user statistics
            user_id = user_info["id"]
            stats = await usage_service.get_user_statistics(user_id)
        elif request_type == "free":
            # Free tier statistics
            user_id = getattr(request.state, "user", {}).get("id")
            if not user_id:
                user_id = request.headers.get("X-User-ID")

            if not user_id:
                raise HTTPException(
                    status_code=401,
                    detail={
                        "error": "missing_user_id",
                        "message": "User ID is required for free tier",
                        "type": "authentication_error",
                    },
                )

            # Get user statistics
            stats = await usage_service.get_user_statistics(user_id)
        else:
            # Demo statistics (IP-based)
            client_ip = getattr(request.state, "ip_address", None)
            if not client_ip:
                client_ip = request.client.host

            stats = await usage_service.get_demo_usage_stats(client_ip)

        if stats is None:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "usage_stats_error",
                    "message": "Failed to retrieve usage statistics",
                    "type": "server_error",
                },
            )

        return stats

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting usage stats: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "server_error",
                "message": "Failed to retrieve usage statistics",
                "type": "server_error",
            },
        )


def get_content_type(format_name: str) -> str:
    """
    Get the content type for an audio format.

    Args:
        format_name: Audio format name (mp3, opus, aac, etc.)

    Returns:
        str: The content type for the format
    """
    format_name = format_name.lower()

    content_types = {
        "mp3": "audio/mpeg",
        "opus": "audio/opus",
        "aac": "audio/aac",
        "flac": "audio/flac",
        "wav": "audio/wav",
        "pcm": "audio/pcm",
    }

    return content_types.get(format_name, f"audio/{format_name}")


def validate_input_length(input_text: str) -> None:
    """
    Validate the input text length.

    Args:
        input_text: The input text to validate

    Raises:
        ValueError: If the input text is too long or empty
    """
    # Check for empty input
    if not input_text or input_text.strip() == "":
        raise ValueError("Input text cannot be empty")

    # Check for maximum length (adjust limit as needed)
    max_length = 4096  # Example limit
    if len(input_text) > max_length:
        raise ValueError(
            f"Input text exceeds maximum length of {max_length} characters"
        )

    return None


def estimate_audio_duration(size_bytes: int, format_name: str) -> Optional[int]:
    """
    Estimate audio duration based on file size and format.

    Args:
        size_bytes: Size of the audio file in bytes
        format_name: Audio format (mp3, opus, aac, etc.)

    Returns:
        int: Estimated duration in milliseconds or None if unknown format
    """
    # Get bitrate for the format
    bitrate = get_audio_bitrate(format_name.lower())

    if not bitrate or not size_bytes:
        return None

    # Calculate duration: size_bytes * 8 (bits) / bitrate (bits/second) * 1000 (milliseconds)
    duration_ms = int((size_bytes * 8 / bitrate) * 1000)

    return duration_ms
