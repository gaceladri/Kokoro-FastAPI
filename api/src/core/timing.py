"""Utilities for timing and performance tracking."""

import time
from typing import Dict

from loguru import logger


class TimingTracker:
    """Track timing information for various events in the request lifecycle."""

    def __init__(self):
        """Initialize a new timing tracker."""
        self.events: Dict[str, float] = {}
        self.durations: Dict[str, float] = {}
        self.start_time = time.time()

        # Initialize with start time
        self.events["start_time"] = self.start_time
        logger.debug("Started timing tracker")

    def mark(self, event_name: str) -> None:
        """Mark the current time for an event."""
        self.events[event_name] = time.time()
        logger.debug(f"Marked timing event: {event_name}")

    def start_event(self, event_name: str) -> None:
        """Mark the start of an event that will be measured."""
        self.events[f"start_{event_name}"] = time.time()
        logger.debug(f"Started timing event: {event_name}")

    def end_event(self, event_name: str) -> float:
        """
        Mark the end of an event and calculate its duration.

        Returns:
            The duration of the event in seconds.
        """
        end_time = time.time()
        start_key = f"start_{event_name}"

        if start_key not in self.events:
            logger.warning(
                f"Attempted to end timing for {event_name} but no start time was recorded"
            )
            self.durations[event_name] = 0.0
            return 0.0

        duration = end_time - self.events[start_key]
        self.durations[event_name] = duration

        # Log timing events - only log at INFO for important metrics
        if event_name in ["time_to_first_chunk", "time_to_first_chunk_delivery"]:
            logger.info(f"🚀 {event_name}: {duration:.4f}s")
            # No need to also log at DEBUG with the same information
        else:
            logger.debug(
                f"Ended timing event: {event_name} - Duration: {duration:.4f}s"
            )

        return duration

    def get_event_breakdown(self) -> str:
        """Generate a breakdown of all timed events for logging purposes."""
        # Calculate total time if not already calculated
        if "total_request" not in self.durations:
            current_time = time.time()
            self.durations["total_request"] = current_time - self.start_time

        # Create event breakdown string with ordered events
        important_events = [
            "time_to_first_chunk",
            "time_to_first_chunk_delivery",
            "total_request",
        ]

        parts = []

        # First add important events in order
        for event in important_events:
            if event in self.durations:
                parts.append(f"{event}: {self.durations[event]:.4f}s")

        # Then add all other events
        for key, value in sorted(self.durations.items()):
            if key not in important_events:
                parts.append(f"{key}: {value:.4f}s")

        return ", ".join(parts)

    def to_dict(self) -> Dict[str, float]:
        """Get all event durations as a dictionary."""
        # Ensure total is calculated
        if "total_request" not in self.durations:
            current_time = time.time()
            self.durations["total_request"] = current_time - self.start_time

        return dict(self.durations)

    def log_breakdown(self, log_level: str = "DEBUG") -> None:
        """Log the event breakdown at the specified log level, defaulting to DEBUG."""
        breakdown = self.get_event_breakdown()

        # Extract important metrics
        important_metrics = {
            k: v
            for k, v in self.durations.items()
            if k
            in ["time_to_first_chunk", "time_to_first_chunk_delivery", "total_request"]
        }

        # Choose how to log based on the log level
        if log_level == "DEBUG":
            # For DEBUG level, log everything in a compact format
            logger.debug(f"Full timing breakdown: {breakdown}")

            # Also log important metrics in a structured way for easier reading
            if important_metrics:
                logger.debug("⏱️ Important timing metrics (DEBUG):")
                for metric, value in important_metrics.items():
                    logger.debug(f"  ▶️ {metric}: {value:.4f}s")
        else:
            # For INFO and above, only log important metrics
            if important_metrics:
                logger.info("⏱️ Important timing metrics:")
                for metric, value in important_metrics.items():
                    logger.info(f"  ▶️ {metric}: {value:.4f}s")
