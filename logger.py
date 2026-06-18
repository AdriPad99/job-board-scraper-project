import logging
import os
import sys


def setup_logging(level: int | str | None = None) -> None:
    """Configure application-wide logging once.

    Level can be overridden via the LOG_LEVEL env var (e.g. DEBUG, INFO).
    Defaults to INFO.
    """
    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO")

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger."""
    return logging.getLogger(name)
