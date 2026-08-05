from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

_CONFIGURED = False


def setup_logging(
    log_file: str | Path = "logs/pbandai_hk.log",
    *,
    level: str = "INFO",
    also_console: bool = True,
) -> logging.Logger:
    """Configure root app logger once: file + optional console."""
    global _CONFIGURED
    logger = logging.getLogger("pbandai_hk")
    if _CONFIGURED:
        return logger

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    path = Path(log_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    if also_console:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        console.setLevel(getattr(logging, level.upper(), logging.INFO))
        logger.addHandler(console)

    _CONFIGURED = True
    logger.debug("logging initialized -> %s", path.resolve())
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    if name:
        return logging.getLogger(f"pbandai_hk.{name}")
    return logging.getLogger("pbandai_hk")


def log_exception(logger: logging.Logger, message: str, exc: BaseException) -> None:
    """Record an error with traceback to the log file."""
    logger.error("%s: %s", message, exc, exc_info=True)
