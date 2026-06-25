"""Structured logging (§30 Observability — log component).

A single configured logger tree under the ``ironjarvis`` namespace. Other
observability consumers (metrics, traces) attach to the Event Bus instead.
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def configure_logging(level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-5s %(name)s :: %(message)s")
    )
    root = logging.getLogger("ironjarvis")
    root.setLevel(level)
    root.addHandler(handler)
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(f"ironjarvis.{name}")
