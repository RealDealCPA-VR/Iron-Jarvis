"""Structured logging (§30 Observability — log component).

ONE configured logger tree, reachable under TWO names (v1.229.0, audit Wave
3, OBS1): ``ironjarvis`` (what :func:`get_logger` hands out) and
``iron_jarvis`` (the package name, which 27 modules log under directly —
``core/events``, ``core/db``, the 500 envelope in ``daemon/auth``, the chat
lanes, terminals). Before this, only ``ironjarvis`` had a handler: everything
under ``iron_jarvis.*`` fell through to a handler-less root, so its INFO
vanished and its WARNING/ERROR printed as bare text with no timestamp and no
logger name. Both names get the same timestamped handler at the same level.
NEVER ADD A THIRD NAME — alias it here.

The ROOT logger gets the same handler at WARNING, so what the libraries say
(``asyncio``, ``apscheduler``, ``uvicorn.error``) carries a timestamp and a
logger name too. That handler SKIPS the two app trees (``_NotAppTree``):
``iron_jarvis`` keeps ``propagate=True`` — pytest's ``caplog`` and any root
capture rely on it — so without the skip a WARNING under it would print twice.

Also here (OBS4): the two noise filters the daemon log needed — measured on
the live install, 48.8% of ``daemon.log`` was OPTIONS preflights, 37.3% green
polls of the same nine routes and 4.4% the Windows proactor's reset-on-close
traceback (561/day), against 0.02% app lines. ``install_noise_filters`` puts
them on the ``asyncio`` and ``uvicorn.access`` loggers; ``daemon/cli.py``
calls it where uvicorn is started. Logger-level filters survive uvicorn's
``dictConfig`` (it replaces handlers, not filters).

Also here (OBS5): :class:`RecentErrorsHandler`, a memory-only ring buffer of
the last :data:`RECENT_ERRORS_CAPACITY` WARNING+ records from the whole tree
(both app names AND the libraries), read by ``GET /diagnostics/errors`` so
"what went wrong recently" is one click away instead of a log file the user
has to find. It is installed by :func:`configure_logging` — a handler nobody
installs records nothing — and never touches the disk or the event loop.

Other observability consumers (metrics, traces) attach to the Event Bus.
"""

from __future__ import annotations

import collections
import copy
import logging
import sys
from datetime import datetime, timezone

_CONFIGURED = False

_FORMAT = "%(asctime)s %(levelname)-5s %(name)s :: %(message)s"

#: The two names ONE app tree answers to. Do not add a third.
APP_LOGGER_NAMES = ("ironjarvis", "iron_jarvis")


#: How many WARNING+ records ``GET /diagnostics/errors`` keeps (OBS5).
RECENT_ERRORS_CAPACITY = 50


class RecentErrorsHandler(logging.Handler):
    """Ring buffer of the last N WARNING+ records as plain dicts
    (``{ts, level, logger, message}``). Memory only: a bounded deque, no I/O,
    so it is safe on any thread including the event loop. Instances may share
    one ``store`` — the root and the ``ironjarvis`` tree each need their own
    handler (root's carries :class:`_NotAppTree`, and a filter is per handler)
    but the user wants ONE list."""

    def __init__(self, store: "collections.deque | None" = None) -> None:
        super().__init__(level=logging.WARNING)
        self.store: collections.deque = (
            store if store is not None else collections.deque(maxlen=RECENT_ERRORS_CAPACITY)
        )

    def emit(self, record: logging.LogRecord) -> None:
        # A handler that raises aborts the CALLER's log call — and the callers
        # are the daemon's except branches. Nothing here may escape.
        try:
            try:
                msg = record.getMessage()
            except Exception:  # noqa: BLE001 — a bad format string is still a record
                msg = str(record.msg)
            exc = record.exc_info[1] if record.exc_info else None
            if exc is not None:
                try:
                    text = str(exc)
                except Exception:  # noqa: BLE001 — an exception whose __str__ raises
                    text = "<exception str() failed>"
                msg = f"{msg} — {type(exc).__name__}: {text}"
            self.store.append(
                {
                    "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(
                        timespec="milliseconds"
                    ),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": msg[:2000],
                }
            )
        except Exception:  # noqa: BLE001
            self.handleError(record)


#: The shared store every ring handler appends to; read via :func:`recent_errors`.
_RECENT_ERRORS: collections.deque = collections.deque(maxlen=RECENT_ERRORS_CAPACITY)


def recent_errors(limit: int = RECENT_ERRORS_CAPACITY) -> list[dict]:
    """The newest ``limit`` WARNING+ records, oldest first (a snapshot copy)."""
    items = list(_RECENT_ERRORS)
    limit = max(0, min(int(limit), RECENT_ERRORS_CAPACITY))
    return items[-limit:] if limit else []


class _NotAppTree(logging.Filter):
    """Root-handler filter: the app trees have their own handler; skip them
    here so a propagating ``iron_jarvis.*`` WARNING prints once, not twice."""

    def filter(self, record: logging.LogRecord) -> bool:
        name = record.name
        for root in APP_LOGGER_NAMES:
            if name == root or name.startswith(root + "."):
                return False
        return True


def configure_logging(level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    formatter = logging.Formatter(_FORMAT)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    ring = RecentErrorsHandler(_RECENT_ERRORS)
    for name in APP_LOGGER_NAMES:
        tree = logging.getLogger(name)
        tree.setLevel(level)
        tree.addHandler(handler)
        tree.addHandler(ring)
    # ``ironjarvis`` never propagated (it is the tree get_logger hands out);
    # ``iron_jarvis`` MUST keep propagating — caplog/root captures read it.
    logging.getLogger("ironjarvis").propagate = False
    lib_handler = logging.StreamHandler(sys.stderr)
    lib_handler.setFormatter(formatter)
    lib_handler.setLevel(logging.WARNING)
    lib_handler.addFilter(_NotAppTree())
    logging.getLogger().addHandler(lib_handler)
    # The libraries' WARNING+ land in the same ring (OBS5); the app trees are
    # skipped here for the same reason as above — they already have theirs.
    lib_ring = RecentErrorsHandler(_RECENT_ERRORS)
    lib_ring.addFilter(_NotAppTree())
    logging.getLogger().addHandler(lib_ring)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(f"ironjarvis.{name}")


# --- noise filters (OBS4) ----------------------------------------------------


class ProactorResetFilter(logging.Filter):
    """Drop the 'Exception in callback _ProactorBasePipeTransport
    ._call_connection_lost ... ConnectionResetError' record the Windows
    proactor loop emits when a client (Chromium) resets a keep-alive socket
    before the server's shutdown(SHUT_RDWR) (CPython/uvicorn #2105). ONLY that
    record: a ConnectionResetError raised anywhere else, and every other
    asyncio error, is kept."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "asyncio" or record.exc_info is None:
            return True
        exc = record.exc_info[1]
        if not isinstance(exc, ConnectionResetError):
            return True
        return "_call_connection_lost" not in record.getMessage()


#: The dashboard's polled routes: a 200 GET on one of these is not information.
QUIET_PATHS = frozenset(
    {
        "/health",
        "/metrics",
        "/sessions",
        "/diagnostics",
        "/diagnostics/reliability",
        "/workflows/runs",
        "/computeruse",
        "/chat/approvals/pending",
        "/reflex/rules",
    }
)


class PolledRouteAccessFilter(logging.Filter):
    """Quiet ``uvicorn.access`` for OPTIONS preflights and status-200 GETs to
    :data:`QUIET_PATHS`. Every non-200 stays (a failing poll must be visible),
    every write stays (POST /sessions is a real event), every other GET stays.
    uvicorn's access record carries args
    ``(client_addr, method, path_with_query, http_version, status_code)``."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) != 5:
            return True
        _, method, path, _, status = args
        if status != 200:
            return True
        if method == "OPTIONS":
            return False
        if method != "GET":
            return True
        return str(path).split("?", 1)[0] not in QUIET_PATHS


def install_noise_filters() -> None:
    """Attach the two filters to their loggers (idempotent). Called where
    uvicorn is started; logger-level filters survive uvicorn's dictConfig."""
    for name, cls in (
        ("asyncio", ProactorResetFilter),
        ("uvicorn.access", PolledRouteAccessFilter),
    ):
        lg = logging.getLogger(name)
        if not any(isinstance(f, cls) for f in lg.filters):
            lg.addFilter(cls())


def uvicorn_log_config() -> dict:
    """uvicorn's own LOGGING_CONFIG with a timestamp and the logger name on
    both formatters, so ``uvicorn.error``/access lines in ``daemon.log`` can
    be placed in time next to the app's. Everything else (handlers, levels,
    ``disable_existing_loggers=False``) is uvicorn's."""
    import uvicorn.config as uc

    cfg = copy.deepcopy(uc.LOGGING_CONFIG)
    for fmt in cfg.get("formatters", {}).values():
        fmt["fmt"] = "%(asctime)s %(name)s :: " + fmt["fmt"]
    return cfg
