"""``browser_access`` — the switch the whole Browser capability hangs off (v1.235.0).

Three surfaces have to agree about one string, and each of them fails silently on
its own, which is why they are pinned together here:

* **``Config.browser_access``** exists, defaults to ``off``, and refuses anything
  but ``off | read_only | interactive``. Default off is not a preference: this
  capability drives the browser the user is actually logged into, so a default of
  anything else would hand a fresh install their real sessions. The validator is
  the second half — a typo like ``"readonly"`` persisted to ``config.toml`` would
  read as "not interactive" at one check and "not off" at another, leaving a
  capability half on with nothing anywhere saying so.
* **``_SETTINGS_KEYS``** lists it. A key absent from that whitelist is invisible
  to ``GET /settings`` AND ``PUT /settings``, so the only way to turn the
  capability on would be hand-editing ``config.toml`` — which is not a feature,
  it is a feature nobody can reach (the v1.148.0 local-first lesson, which that
  file records against itself).
* **The live re-arm** fires for a ``browser_*`` change. Without it, a user who
  moves Browser access to ``off`` keeps a live paired socket driving their real
  Chrome until the next daemon restart — the single failure mode the switch
  exists to prevent — and the settings page would still show ``off``, so nothing
  would look wrong.

Also pinned here: the ``browser`` object on ``GET /health`` (plan section 3.2).
``/health`` is polled by the desktop shell and by every dashboard window, and
``lib/api.ts`` maps a dead fetch to "Daemon offline", so a browser probe that
raised would not look like a failed probe — it would look like the whole app
going down. The tests therefore drive it BEFORE the platform wires
``platform.browser`` (the honest degraded shape), with a runtime attached, and
with a runtime whose flags raise.

No wall-clock assertion appears here. The re-arm test asserts that the hook was
CALLED, on the loop, in the same request — ordering, not duration.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from iron_jarvis.core.config import Config, persist_config_values
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import settings as settings_routes
from iron_jarvis.daemon.schemas import _SETTINGS_KEYS
from iron_jarvis.platform import build_platform

_SETTINGS_SOURCE = Path(settings_routes.__file__)


def _read_source(path: Path) -> str:
    """Read a source file with CRLF normalised at the READER (v1.232.1).

    GitHub's Windows runners check out with ``core.autocrlf=true``, so every file
    the suite reads as text has ``\\r\\n`` line ends there and ``\\n`` here. A pin
    that does not normalise here matches every local run and never matches on CI,
    which is exactly how a green local suite took an installer down.
    """
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


class _ImmediateLoop:
    """A stand-in for the daemon loop: ``call_soon_threadsafe`` runs it now.

    ``PUT /settings`` runs in FastAPI's threadpool and hops onto the daemon loop
    to re-arm, so the hop is the thing under test. Running the callback inline
    keeps the assertion about WHICH hook was scheduled rather than about timing.
    """

    def __init__(self) -> None:
        self.hops = 0

    def call_soon_threadsafe(self, fn, *args, **kw):
        self.hops += 1
        return fn(*args, **kw)


def _settings_app(tmp_path: Path):
    """A bare app with only the settings routes, plus a recorded re-arm table."""
    platform = build_platform(str(tmp_path))
    app = FastAPI()
    loop = _ImmediateLoop()
    rearmed: list[str] = []
    live = {
        "loop": loop,
        "browser": lambda: rearmed.append("browser"),
        "autonomy": lambda: rearmed.append("autonomy"),
        "sentinels": lambda: rearmed.append("sentinels"),
        "calendar": lambda: rearmed.append("calendar"),
        "fleet": lambda: rearmed.append("fleet"),
    }
    d = SimpleNamespace(
        platform=platform,
        _live_rearm=live,
        _persist_config=lambda keys: persist_config_values(
            platform.config.home, {k: getattr(platform.config, k, None) for k in keys}
        ),
    )
    settings_routes.register(app, d)
    return TestClient(app), platform, rearmed, loop


# --------------------------------------------------------------------------- #
# 1. The field.
# --------------------------------------------------------------------------- #


def test_browser_access_defaults_to_off(tmp_path):
    cfg = Config(project_root=tmp_path, home=tmp_path / "home")
    assert cfg.browser_access == "off"


def test_browser_access_accepts_only_the_three_levels(tmp_path):
    cfg = Config(project_root=tmp_path, home=tmp_path / "home")
    for value in ("off", "read_only", "interactive"):
        cfg.browser_access = value
        assert cfg.browser_access == value
    for bad in ("readonly", "Interactive", "on", "", "full"):
        try:
            cfg.browser_access = bad
        except Exception as exc:  # pydantic ValidationError
            assert "browser_access must be off | read_only | interactive" in str(exc)
        else:  # pragma: no cover - the assertion below is the failure message
            raise AssertionError(f"{bad!r} was accepted as a browser_access value")
        # The rejected value must not have landed: validate_assignment rolls back,
        # and a half-applied assignment would leave the capability in a state no
        # check recognises.
        assert cfg.browser_access == "interactive"


# --------------------------------------------------------------------------- #
# 2. The settings routes.
# --------------------------------------------------------------------------- #


def test_browser_access_is_in_settings_keys():
    assert "browser_access" in _SETTINGS_KEYS


def test_settings_round_trip_and_persist(tmp_path):
    client, platform, _rearmed, _loop = _settings_app(tmp_path)
    assert client.get("/settings").json()["settings"]["browser_access"] == "off"
    resp = client.put("/settings", json={"values": {"browser_access": "interactive"}})
    assert resp.status_code == 200, resp.text
    assert "browser_access" in resp.json()["updated"]
    assert platform.config.browser_access == "interactive"
    assert client.get("/settings").json()["settings"]["browser_access"] == "interactive"
    # A fresh platform on the SAME root reads it back: the value reached
    # config.toml, not just the live object.
    assert build_platform(str(tmp_path)).config.browser_access == "interactive"


def test_a_bad_browser_access_value_is_refused_with_400(tmp_path):
    client, platform, _rearmed, _loop = _settings_app(tmp_path)
    client.put("/settings", json={"values": {"browser_access": "read_only"}})
    resp = client.put("/settings", json={"values": {"browser_access": "readonly"}})
    assert resp.status_code == 400
    assert "browser_access" in resp.json()["detail"]
    # The live config is untouched — the route validates on a throwaway copy
    # first, so one bad value cannot partially mutate (and then persist) it.
    assert platform.config.browser_access == "read_only"


# --------------------------------------------------------------------------- #
# 3. The live re-arm.
# --------------------------------------------------------------------------- #


def test_moving_browser_access_to_off_rearms_live(tmp_path):
    """Turning the capability off must DROP the live socket in this request."""
    client, platform, rearmed, loop = _settings_app(tmp_path)
    client.put("/settings", json={"values": {"browser_access": "interactive"}})
    assert rearmed == ["browser"], rearmed
    rearmed.clear()
    client.put("/settings", json={"values": {"browser_access": "off"}})
    assert rearmed == ["browser"], rearmed
    assert platform.config.browser_access == "off"
    assert loop.hops >= 2, "the re-arm must hop onto the daemon loop, not run in the threadpool"


def test_an_unrelated_settings_change_does_not_rearm_browser(tmp_path):
    """The group match is prefix-based, so a neighbouring key must not fire it.

    Dropping the paired socket on every unrelated settings save would look like a
    browser that keeps disconnecting itself for no reason the user can see.
    """
    client, _platform, rearmed, _loop = _settings_app(tmp_path)
    client.put("/settings", json={"values": {"max_agent_steps": 9}})
    assert rearmed == []


def test_browser_is_named_in_the_live_rearm_groups():
    """A source pin, because the functional test above supplies its own hook
    table: the group name must be in the SHIPPED tuple, or the coordinator's
    ``_live_rearm["browser"]`` is registered and never consulted."""
    source = _read_source(_SETTINGS_SOURCE)
    marker = 'for group in ("autonomy", "sentinels", "calendar", "fleet", "browser"):'
    assert marker in source, "the live re-arm groups no longer name browser"


# --------------------------------------------------------------------------- #
# 4. The /health browser object.
# --------------------------------------------------------------------------- #


class _Runtime:
    """A ``BrowserRuntime`` stand-in exposing only the flags ``/health`` reads."""

    def __init__(self, *, connected=False, paired=False, host_permission=False):
        self.connected = connected
        self.paired = paired
        self.host_permission = host_permission


class _AngryRuntime:
    """A runtime whose flags raise — the shape that must not take /health down."""

    @property
    def connected(self) -> bool:
        raise RuntimeError("backend is wedged")

    @property
    def paired(self) -> bool:
        raise RuntimeError("backend is wedged")

    @property
    def host_permission(self) -> bool:
        raise RuntimeError("backend is wedged")


def test_health_reports_the_browser_object_before_the_runtime_exists(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    browser = client.get("/health").json()["browser"]
    assert browser == {
        "connected": False,
        "access": "off",
        "paired": False,
        "host_permission": False,
        "extension_installed": False,
    }


def test_health_browser_access_follows_the_setting(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    app.state.platform.config.browser_access = "read_only"
    assert client.get("/health").json()["browser"]["access"] == "read_only"


def test_health_reports_a_connected_paired_browser(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    app.state.platform.browser = _Runtime(
        connected=True, paired=True, host_permission=True
    )
    browser = client.get("/health").json()["browser"]
    assert browser["connected"] is True
    assert browser["paired"] is True
    assert browser["host_permission"] is True
    # Derived, not observed: the daemon cannot see Chrome's add-on list, so
    # "installed" means the add-on has spoken to us.
    assert browser["extension_installed"] is True


def test_health_reports_a_paired_but_offline_browser_as_installed(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    app.state.platform.browser = _Runtime(connected=False, paired=True)
    browser = client.get("/health").json()["browser"]
    assert browser["connected"] is False
    assert browser["extension_installed"] is True
    assert browser["host_permission"] is False


def test_health_never_fails_on_a_wedged_browser_runtime(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    app.state.platform.browser = _AngryRuntime()
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["browser"]["connected"] is False
    assert body["browser"]["extension_installed"] is False
    # The rest of /health is intact — a browser probe must not cost the version
    # or the provider rows the desktop shell reads from the same response.
    assert body["version"] and "providers" in body
