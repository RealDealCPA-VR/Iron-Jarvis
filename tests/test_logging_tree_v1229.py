"""v1.229.0 (audit Wave 3, OBS1/OBS3/OBS4): one logger tree, filtered noise,
an error id on every 500. Lifted from tests/_audit_20260904/test_obs_lane.py.

OBS1 is probed in a SUBPROCESS: pytest installs its own root handler during a
test, which would hide exactly the behaviour under test (a handler-less root).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import logging.config
import re
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from iron_jarvis.core.logging import (
    APP_LOGGER_NAMES,
    QUIET_PATHS,
    PolledRouteAccessFilter,
    ProactorResetFilter,
    install_noise_filters,
    uvicorn_log_config,
)
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.auth import unhandled_error_response

ORIGIN = {"Origin": "http://127.0.0.1:8788"}
ERR_ID = re.compile(r"^internal error \[(err_[0-9a-f]{8})\]: RuntimeError: kaboom$")
STAMPED = re.compile(r"^20\d\d-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} ")


# ---------------------------------------------------------------------------
# OBS1 — both namespaces reach the same timestamped handler; the root carries
# library WARNING+ with a timestamp and a name; nothing prints twice.
# ---------------------------------------------------------------------------

_PROBE = r"""
import logging
from iron_jarvis.core.logging import configure_logging
configure_logging()
logging.getLogger("ironjarvis.daemon").info("CONFIGURED-INFO")
logging.getLogger("ironjarvis.daemon").warning("CONFIGURED-WARN")
logging.getLogger("iron_jarvis.daemon").info("UNDERSCORE-INFO")
logging.getLogger("iron_jarvis.daemon").warning("UNDERSCORE-WARN")
logging.getLogger("iron_jarvis.events").debug("UNDERSCORE-DEBUG")
logging.getLogger("asyncio").error("ASYNCIO-ERR")
logging.getLogger("asyncio").info("ASYNCIO-INFO")
logging.getLogger("apscheduler.executors.default").warning("APS-WARN")
logging.getLogger("uvicorn.error").warning("UV-WARN")
try:
    raise RuntimeError("boom-500")
except RuntimeError:
    logging.getLogger("ironjarvis.daemon").exception("unhandled error on GET /x [err_00000000]")
"""


@pytest.fixture(scope="module")
def probe_stderr() -> str:
    out = subprocess.run(
        [sys.executable, "-c", _PROBE], capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, out.stderr
    return out.stderr


def _lines_with(err: str, marker: str) -> list[str]:
    return [ln for ln in err.splitlines() if marker in ln]


@pytest.mark.parametrize(
    "marker,logger",
    [
        ("CONFIGURED-INFO", "ironjarvis.daemon"),
        ("CONFIGURED-WARN", "ironjarvis.daemon"),
        ("UNDERSCORE-INFO", "iron_jarvis.daemon"),  # was dropped (lastResort is WARNING+)
        ("UNDERSCORE-WARN", "iron_jarvis.daemon"),  # was printed bare, no time/name
        ("ASYNCIO-ERR", "asyncio"),
        ("APS-WARN", "apscheduler.executors.default"),
        ("UV-WARN", "uvicorn.error"),
        ("unhandled error on GET /x [err_00000000]", "ironjarvis.daemon"),
    ],
)
def test_every_namespace_prints_once_with_timestamp_and_name(probe_stderr, marker, logger):
    hits = _lines_with(probe_stderr, marker)
    assert len(hits) == 1, f"{marker!r} printed {len(hits)} times:\n{probe_stderr}"
    assert STAMPED.match(hits[0]), f"no timestamp: {hits[0]!r}"
    assert f" {logger} :: " in hits[0], hits[0]


def test_library_info_and_app_debug_stay_quiet(probe_stderr):
    assert not _lines_with(probe_stderr, "ASYNCIO-INFO")  # root handler is WARNING
    assert not _lines_with(probe_stderr, "UNDERSCORE-DEBUG")  # app trees are INFO


def test_500_traceback_follows_its_timestamped_header(probe_stderr):
    assert "RuntimeError: boom-500" in probe_stderr
    head = _lines_with(probe_stderr, "unhandled error on GET /x")
    assert len(head) == 1 and STAMPED.match(head[0]), head


def test_underscore_tree_still_propagates_for_caplog(caplog):
    """The alias must not cut ``iron_jarvis.*`` off from root captures — four
    existing tests read it through caplog."""
    with caplog.at_level(logging.INFO, logger="iron_jarvis.daemon"):
        logging.getLogger("iron_jarvis.daemon").info("seen-by-caplog")
    assert [r.getMessage() for r in caplog.records] == ["seen-by-caplog"]
    assert APP_LOGGER_NAMES == ("ironjarvis", "iron_jarvis")


# ---------------------------------------------------------------------------
# OBS3 — every 500 carries err_<8 hex> in detail AND on the logged header.
# ---------------------------------------------------------------------------


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_500_envelope_mints_an_error_id_and_logs_it_on_the_app_tree(tmp_path):
    app = create_app(str(tmp_path))

    @app.get("/_boom")
    def _boom():
        raise RuntimeError("kaboom")

    cap = _Capture()
    logging.getLogger("ironjarvis").addHandler(cap)
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.get("/_boom", headers=ORIGIN)
    finally:
        logging.getLogger("ironjarvis").removeHandler(cap)

    assert r.status_code == 500
    assert r.headers.get("access-control-allow-origin") == ORIGIN["Origin"]  # v1.151.3 kept
    body = r.json()
    assert set(body) == {"detail"}
    m = ERR_ID.match(body["detail"])
    assert m, body
    err_id = m.group(1)
    recs = [x for x in cap.records if "unhandled error" in x.getMessage()]
    assert len(recs) == 1, [x.getMessage() for x in cap.records]
    assert recs[0].name == "ironjarvis.daemon"
    assert recs[0].levelno == logging.ERROR and recs[0].exc_info is not None
    assert recs[0].getMessage() == f"unhandled error on GET /_boom [{err_id}]"


def test_two_500s_get_two_ids():
    req = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})
    a = json.loads(unhandled_error_response(req, RuntimeError("kaboom")).body)
    b = json.loads(unhandled_error_response(req, RuntimeError("kaboom")).body)
    assert ERR_ID.match(a["detail"]) and ERR_ID.match(b["detail"])
    assert a["detail"] != b["detail"]


def test_app_py_backstop_handler_uses_the_same_envelope(tmp_path):
    """ServerErrorMiddleware's handler (outside the middleware chain) must
    speak the same contract, or a user sees two error formats."""
    app = create_app(str(tmp_path))
    handler = app.exception_handlers[Exception]
    req = Request({"type": "http", "method": "POST", "path": "/y", "headers": []})
    resp = asyncio.run(handler(req, RuntimeError("kaboom")))
    assert resp.status_code == 500
    assert ERR_ID.match(json.loads(resp.body)["detail"])


# ---------------------------------------------------------------------------
# OBS4 — the two noise filters, unit-tested on records shaped like the ones
# CPython / uvicorn produce.
# ---------------------------------------------------------------------------


def _proactor_record() -> logging.LogRecord:
    try:
        raise ConnectionResetError(
            10054, "An existing connection was forcibly closed by the remote host"
        )
    except ConnectionResetError:
        exc_info = sys.exc_info()
    return logging.LogRecord(
        "asyncio",
        logging.ERROR,
        "asyncio\\base_events.py",
        1891,
        "Exception in callback _ProactorBasePipeTransport._call_connection_lost(None)\n"
        "handle: <Handle _ProactorBasePipeTransport._call_connection_lost(None)>",
        (),
        exc_info,
    )


def test_proactor_filter_drops_only_the_reset_on_close_noise():
    f = ProactorResetFilter()
    assert f.filter(_proactor_record()) is False
    try:
        raise ValueError("real bug")
    except ValueError:
        real = logging.LogRecord(
            "asyncio", logging.ERROR, "x", 1, "Exception in callback something_else()", (), sys.exc_info()
        )
    assert f.filter(real) is True
    try:
        raise ConnectionResetError(10054, "reset")
    except ConnectionResetError:
        elsewhere = logging.LogRecord(
            "asyncio", logging.ERROR, "x", 1, "Task exception was never retrieved", (), sys.exc_info()
        )
    assert f.filter(elsewhere) is True
    other = _proactor_record()
    other.name = "ironjarvis.daemon"
    assert f.filter(other) is True


def _access(method: str, path: str, status: int) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "h11_impl.py",
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1", method, path, "1.1", status),
        None,
    )


@pytest.mark.parametrize(
    "method,path,status,kept",
    [
        ("GET", "/health", 200, False),
        ("GET", "/metrics", 200, False),
        ("GET", "/sessions", 200, False),
        ("GET", "/diagnostics", 200, False),
        ("GET", "/diagnostics/reliability", 200, False),
        ("GET", "/workflows/runs?status=waiting&slim=true&limit=200", 200, False),
        ("GET", "/computeruse", 200, False),
        ("GET", "/chat/approvals/pending", 200, False),
        ("GET", "/reflex/rules", 200, False),
        ("OPTIONS", "/health", 200, False),
        ("OPTIONS", "/documents/upload", 200, False),  # every green preflight is noise
        ("GET", "/health", 500, True),  # a failing poll must stay visible
        ("GET", "/health", 401, True),
        ("POST", "/sessions", 200, True),  # writes always stay
        ("DELETE", "/reflex/rules", 200, True),
        ("GET", "/sessions/abc", 200, True),  # a real fetch, not the list poll
        ("GET", "/chat/threads", 200, True),
    ],
)
def test_access_filter_keeps_everything_but_green_polls(method, path, status, kept):
    assert PolledRouteAccessFilter().filter(_access(method, path, status)) is kept


def test_access_filter_ignores_records_of_another_shape():
    odd = logging.LogRecord("uvicorn.access", logging.INFO, "x", 1, "free text", (), None)
    assert PolledRouteAccessFilter().filter(odd) is True
    assert QUIET_PATHS == frozenset(
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
            # v1.245.0: the Build page's 2.5 s pane-state poll.
            "/terminals/activity",
        }
    )


def test_install_is_idempotent_and_survives_uvicorn_dictconfig():
    asy, acc = logging.getLogger("asyncio"), logging.getLogger("uvicorn.access")
    before = (list(asy.filters), list(acc.filters))
    try:
        install_noise_filters()
        install_noise_filters()
        assert sum(isinstance(f, ProactorResetFilter) for f in asy.filters) == 1
        assert sum(isinstance(f, PolledRouteAccessFilter) for f in acc.filters) == 1
        # uvicorn.run applies its dictConfig AFTER we install: handlers are
        # replaced, logger filters are kept, and no logger is disabled.
        logging.config.dictConfig(uvicorn_log_config())
        assert any(isinstance(f, PolledRouteAccessFilter) for f in acc.filters)
        assert any(isinstance(f, ProactorResetFilter) for f in asy.filters)
        assert acc.disabled is False and logging.getLogger("ironjarvis").disabled is False
        # the filter really gates the logger's handlers
        cap = _Capture()
        acc.addHandler(cap)
        try:
            acc.handle(_access("GET", "/health", 200))
            acc.handle(_access("POST", "/sessions", 200))
        finally:
            acc.removeHandler(cap)
        assert [r.args[1] for r in cap.records] == ["POST"]
    finally:
        asy.filters[:] = before[0]
        acc.filters[:] = before[1]


def test_uvicorn_log_config_stamps_its_own_lines():
    import uvicorn.config as uc

    cfg = uvicorn_log_config()
    for name in ("default", "access"):
        fmt = cfg["formatters"][name]["fmt"]
        assert fmt.startswith("%(asctime)s %(name)s :: ")
        assert fmt.endswith(uc.LOGGING_CONFIG["formatters"][name]["fmt"])
    assert cfg["disable_existing_loggers"] is False
    # uvicorn's own table is untouched (deep copy)
    assert uc.LOGGING_CONFIG["formatters"]["default"]["fmt"].startswith("%(levelprefix)s")


def test_cli_installs_the_filters_at_every_uvicorn_seam():
    """The CALL SITE is the feature: a filter nobody installs filters nothing."""
    from iron_jarvis.daemon import cli

    src = inspect.getsource(cli)
    assert src.count("uvicorn.run(") == 2
    assert src.count("install_noise_filters()") == 2
    assert src.count("log_config=uvicorn_log_config()") == 2
