"""Logging utilities for nolongerevil server."""

import logging
import sys

from nolongerevil.config import settings


class ColoredFormatter(logging.Formatter):
    """Formatter that adds ANSI color codes and timestamps to log output."""

    # ANSI color codes
    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[1;31m",  # Bold Red
    }
    RESET = "\033[0m"

    def __init__(self, fmt: str | None = None, use_color: bool = True):
        super().__init__(fmt, datefmt="%H:%M:%S")
        self.use_color = use_color

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        """Format time with milliseconds as [HH:MM:SS.mmm]."""
        import time

        ct = self.converter(record.created)
        s = time.strftime(datefmt, ct) if datefmt else time.strftime("%H:%M:%S", ct)
        return f"{s}.{int(record.msecs):03d}"

    def format(self, record: logging.LogRecord) -> str:
        if self.use_color and record.levelname in self.COLORS:
            color = self.COLORS[record.levelname]
            record.levelname = f"{color}{record.levelname}{self.RESET}"
        return super().format(record)


def get_logger(name: str) -> logging.Logger:
    """Get a configured logger instance.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured logger instance
    """
    # Normalize __main__ to main for cleaner log output
    if name == "__main__":
        name = "main"

    logger = logging.getLogger(name)

    # Only configure if not already configured
    if not logger.handlers:
        # Set level based on debug setting
        level = logging.DEBUG if settings.debug_logging else logging.INFO
        logger.setLevel(level)

        # Create console handler
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)

        # Create colored formatter (disable color if not a TTY)
        use_color = sys.stdout.isatty()
        formatter = ColoredFormatter(
            "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s", use_color=use_color
        )
        handler.setFormatter(formatter)

        logger.addHandler(handler)

        # Prevent propagation to root logger
        logger.propagate = False

    return logger
