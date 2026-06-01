"""
logger.py --  file logging for the K8s Agentic AI Monitor.

Creates a new log file per day: logs/agent_YYYY-MM-DD.log
Multiple runs on the same day append to the same file.

Usage:
    from agent.logger import write
    write("INFO", "Monitor started")
    write("ERROR", "API error: connection refused")

Log format:
    2024-01-15 14:32:13 | INFO    | message here
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from agent.config import cfg

# ---------------------------------------------------------------------------
# Internal setup -- runs once when the module is first imported
# ---------------------------------------------------------------------------

def _setup_logger() -> logging.Logger:
    """
    Build and return a Logger that writes to today's log file.
    Called once at module import -- result stored in _logger below.
    """
    # Ensure logs/ directory exists
    log_dir = Path(cfg.log_file).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    # Build today's filename: logs/agent_2024-01-15.log
    today     = datetime.now().strftime("%Y-%m-%d")
    log_path  = log_dir / f"agent_{today}.log"

    # Map config string to logging level constant
    level_map = {
        "DEBUG":   logging.DEBUG,
        "INFO":    logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR":   logging.ERROR,
    }
    level = level_map.get(cfg.log_level.upper(), logging.INFO)

    # Create logger
    logger = logging.getLogger("k8s_agent")
    logger.setLevel(logging.DEBUG)  # capture everything, handler filters by level

    # Avoid duplicate handlers if module is reloaded
    if logger.handlers:
        return logger

    # File handler -- writes to today's log file
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s",
                          datefmt="%Y-%m-%d %H:%M:%S")
    )

    logger.addHandler(file_handler)
    return logger


# Module-level logger instance -- created once on import
_logger = _setup_logger()


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def write(level: str, message: str) -> None:
    """
    Write a message to today's log file.

    Args:
        level:   "DEBUG", "INFO", "WARNING", or "ERROR"
        message: the message to log

    Examples:
        write("INFO", "Monitor started -- watching namespace: default")
        write("ERROR", "Ollama API error: connection refused")
        write("WARNING", "Agent reached max iterations without deciding")
    """
    level = level.upper()

    if level == "DEBUG":
        _logger.debug(message)
    elif level == "INFO":
        _logger.info(message)
    elif level == "WARNING":
        _logger.warning(message)
    elif level == "ERROR":
        _logger.error(message)
    else:
        _logger.info(message)


# ---------------------------------------------------------------------------
# Convenience mapping -- translates display event_type → log level
# ---------------------------------------------------------------------------

# Maps the event_type strings used in display.py to log levels.
# Errors and escalations are WARNING/ERROR, everything else is INFO.
EVENT_LEVEL_MAP = {
    "investigating":   "INFO",
    "thinking":        "DEBUG",   # verbose -- only written if log_level=DEBUG
    "tool_call":       "DEBUG",
    "tool_result":     "DEBUG",
    "decision":        "INFO",
    "reasoning":       "INFO",
    "recommendation":  "INFO",
    "cooldown":        "INFO",
    "escalated":       "WARNING",
    "error":           "ERROR",
}


def write_event(event_type: str, message: str) -> None:
    """
    Write a display event to the log file using the appropriate level.
    Called from display.log_event() so every terminal event is also persisted.

    Args:
        event_type: one of the event_type strings from display.py
        message:    the message text
    """
    level = EVENT_LEVEL_MAP.get(event_type, "INFO")
    write(level, f"[{event_type}] {message}")