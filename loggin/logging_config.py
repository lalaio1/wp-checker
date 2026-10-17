"""Logging configuration.

Log records are routed through the progress bar sink (when one is active) so
they never corrupt the single-line progress rendering, and can optionally be
mirrored to a rotating file for later analysis.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys

__all__ = ["setup_logging", "set_log_sink", "get_logger"]

_sink = None


class _SinkHandler(logging.Handler):
    """Forward formatted records to a callable (the progress bar ``log()``)."""

    def __init__(self) -> None:
        super().__init__()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            if _sink is not None:
                _sink(message)
            else:
                stream = sys.stdout
                stream.write(message + "\n")
                stream.flush()
        except Exception:  # pragma: no cover - never fail because of logging
            pass


class _StderrOnlyHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover
        try:
            sys.stderr.write(self.format(record) + "\n")
            sys.stderr.flush()
        except Exception:
            pass


def set_log_sink(sink) -> None:
    """Install a callable that receives fully formatted log lines."""
    global _sink
    _sink = sink


def get_logger(name: str = "wp-checker") -> logging.Logger:
    return logging.getLogger(name)


def setup_logging(verbose: bool = False, log_file: str | None = None) -> logging.Logger:
    logger = logging.getLogger("wp-checker")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG if verbose else logging.WARNING)
    logger.propagate = False

    console = _SinkHandler()
    console.setFormatter(logging.Formatter("%(message)s"))
    console.setLevel(logging.DEBUG if verbose else logging.WARNING)
    logger.addHandler(console)

    if log_file:
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=8 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        file_handler.setLevel(logging.DEBUG)
        logger.addHandler(file_handler)

    return logger
