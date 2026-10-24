from __future__ import annotations

import os
import tempfile

import pytest

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.platform import build_platform


@pytest.fixture(autouse=True, scope="session")
def _local_ocr_off_by_default():
    """Keep the offline suite on the VISION-path OCR it was written against (C-05).

    ``documents/local_ocr`` reads scans with Windows' built-in OCR BEFORE any
    vision model. On a dev box with an OCR language installed, every existing
    OCR test (fake vision routers, call counts, exact notes) would otherwise be
    answered by the real engine instead — and CI's Windows Server image may or
    may not have a language pack, so the same test would pass on one runner and
    fail on another. Tests for local OCR switch it back on explicitly
    (``monkeypatch.setattr(local_ocr, "_FORCED_OFF", False)``).
    """
    from iron_jarvis.documents import local_ocr

    original = local_ocr._FORCED_OFF
    local_ocr._FORCED_OFF = True
    try:
        yield
    finally:
        local_ocr._FORCED_OFF = original


@pytest.fixture(autouse=True, scope="session")
def _never_the_users_iron_proxy():
    """v1.367.0: the suite must NEVER find the Iron-Proxy on this machine.

    ``iron_proxy.service`` finds a running proxy through
    ``<IRON_PROXY_DATA_DIR or ~/.iron-proxy>/proxy.json``. On a dev box where
    the packaged app runs one, a test whose service was switched on leased
    REAL accounts: claude-native tests were answered "the Claude account
    'TSI' … needs to sign in again" by the user's own proxy, and a fake CLI's
    scripted 401 could be reported to it as that account's sign-in failure.
    Every worker gets a throwaway data dir; a test that needs its own sets
    the variable with monkeypatch on top of this one.
    """
    old = os.environ.get("IRON_PROXY_DATA_DIR")
    os.environ["IRON_PROXY_DATA_DIR"] = tempfile.mkdtemp(prefix="ij-test-iron-proxy-")
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("IRON_PROXY_DATA_DIR", None)
        else:
            os.environ["IRON_PROXY_DATA_DIR"] = old


@pytest.fixture(autouse=True)
def _iron_proxy_service_per_test():
    """v1.367.0: ``iron_proxy.service._CURRENT`` is the process's live service
    (the last platform built registers itself). A test that switched it on
    used to leave it on for every LATER test on the same worker — the
    claude-native adapter tests then leased through a service they never
    built. Each test starts with the service it had before and leaves none
    behind."""
    from iron_jarvis.iron_proxy import service as _svc

    before = _svc.current()
    try:
        yield
    finally:
        _svc._set_current(before)


@pytest.fixture(autouse=True, scope="session")
def _no_real_claude_model_discovery():
    """v1.300.0: never run the REAL ``claude`` picker handshake from the suite.

    ``GET /models`` (and anything else reading ``claude_models.catalog``) starts
    ONE background handshake when the catalog is stale. On a dev box with a
    signed-in ``claude`` on PATH that spawned the real CLI, and its answer (this
    machine's live default, ``claude-opus-5-5[1m]``) landed in the module's
    in-process cache whenever the thread finished — inside a LATER test on the
    same xdist worker, which then read a catalog it never set up (seen once at
    ``-n 8`` in ``test_claude_models_v1300``). With no CLI (CI) the same code
    answered the pinned table, so the suite was host-dependent. Tests that
    exercise discovery replace ``_run_handshake`` with their own fake via
    monkeypatch, which overrides this default for their duration.
    """
    from iron_jarvis.providers import claude_models

    def _disabled(timeout: float) -> str:
        raise FileNotFoundError("real claude model discovery is disabled in the test suite")

    original = claude_models._run_handshake
    claude_models._run_handshake = _disabled
    try:
        yield
    finally:
        claude_models._run_handshake = original


@pytest.fixture(autouse=True, scope="session")
def _isolate_cli_provider_home():
    """Point locally-installed-CLI-provider detection at an empty home for the
    whole test session.

    CLI-provider detection (``providers/cli_detect``) reads ``GROK_HOME`` /
    ``~/.grok`` off the real disk, so on a dev box where the ``grok`` CLI is
    installed and logged in, a *bare* test would otherwise see a live provider —
    making availability, onboarding, and first-run assertions depend on host
    state. Overriding ``GROK_HOME`` to an empty temp dir keeps every test
    hermetic; the real app still uses the user's real ``GROK_HOME``. Tests that
    exercise detection itself set their own ``GROK_HOME`` via monkeypatch, which
    transparently overrides this default for their duration.
    """
    prev = os.environ.get("GROK_HOME")
    tmp = tempfile.mkdtemp(prefix="ij-test-grokhome-")
    os.environ["GROK_HOME"] = tmp
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("GROK_HOME", None)
        else:
            os.environ["GROK_HOME"] = prev


@pytest.fixture(autouse=True, scope="session")
def _isolate_local_ollama_probe():
    """No test asks the REAL Ollama on this machine's 127.0.0.1:11434 (v1.332.0).

    ``cli_detect.detect_ollama`` runs on every catalog build (``GET /models``,
    the templates list, the browser sidebar) and probes ``/api/tags`` whenever
    an ``ollama`` binary is found. On a dev box with Ollama installed and
    running, a bare test would list that machine's models (and wait on its
    answer); with Ollama installed and stopped, every build waited out the
    probe. The probe now reads "not answering" for the whole session, which is
    exactly what CI sees. Tests that exercise the probe itself set
    ``cli_detect._OLLAMA_PROBE_FORCED_OFF`` back to False via monkeypatch."""
    from iron_jarvis.providers import cli_detect

    original = cli_detect._OLLAMA_PROBE_FORCED_OFF
    cli_detect._OLLAMA_PROBE_FORCED_OFF = True
    try:
        yield
    finally:
        cli_detect._OLLAMA_PROBE_FORCED_OFF = original


@pytest.fixture(autouse=True, scope="session")
def _isolate_local_model_servers_probe():
    """No test asks a REAL LM Studio (localhost:1234) or llama.cpp
    (localhost:8080) on this machine (v1.358.0, lane 8D).

    ``providers/local_servers_seen`` is read by ``GET /health``, ``GET
    /onboarding``, ``/connections/local-servers`` and the use-model door; its
    answer is process-wide. On a dev box with LM Studio running, a bare test
    would see that machine's models and exact ``usable == []`` pins would go
    red by test order. The probe now reads "not answering" for the whole
    session, which is exactly what CI sees. Tests that exercise the probe set
    ``local_servers_seen.PROBE_FORCED_OFF`` back to False via monkeypatch."""
    from iron_jarvis.providers import local_servers_seen

    original = local_servers_seen.PROBE_FORCED_OFF
    local_servers_seen.PROBE_FORCED_OFF = True
    try:
        yield
    finally:
        local_servers_seen.PROBE_FORCED_OFF = original


@pytest.fixture(autouse=True, scope="session")
def _isolate_build_library_reflection():
    """No bare test starts the Build EXPERIENCE LIBRARY's background
    reflection (v1.370.0, IJ harness Phase 7).

    In production, every Build turn that did real work schedules ONE extra
    model call after its reply is written. In a test that is a background
    call on the scripted model that lands at an arbitrary moment — one more
    ``script.calls`` entry, one more usage row on the thread — so exact
    counts in older build-turn pins would go red by timing. The automatic
    scheduling is off for the whole session; the library's own pins
    (``tests/test_harness_library_v1370.py``) set
    ``library.AUTO_REFLECT`` back to True via monkeypatch."""
    from iron_jarvis.harness import library

    original = library.AUTO_REFLECT
    library.AUTO_REFLECT = False
    try:
        yield
    finally:
        library.AUTO_REFLECT = original


@pytest.fixture(autouse=True, scope="session")
def _isolate_subscription_cli_detection():
    """Keep Claude/OpenAI subscription INHERITANCE out of bare tests.

    A logged-in ``claude``/``codex`` CLI now makes ``anthropic``/``openai``
    "available" without an API key (the sanctioned inherited-login path,
    ``ProviderManager._INHERIT_ALIAS``). On a dev box where those CLIs are
    installed, a bare test would otherwise see a live provider — flipping
    availability, onboarding, first-run, and simulated-mode assertions that
    assume nothing is connected (CI has no CLI, so it would pass there and fail
    locally). Force binary detection off for the session so every test is
    hermetic; the real app keeps real detection, and the inheritance behavior is
    covered explicitly in ``test_inherit_cli_compliance.py`` (which overrides the
    check on its own manager instance, so this default doesn't interfere)."""
    from iron_jarvis.providers.manager import ProviderManager

    original = ProviderManager._cli_binary_present
    original_signed = ProviderManager._cli_signed_in
    ProviderManager._cli_binary_present = staticmethod(lambda binary: False)
    # v1.234.0: the sign-in probe shells out to the real `claude`/`codex` on
    # a dev box — stub it to "unknown" so no test ever runs the user's CLI.
    ProviderManager._cli_signed_in = staticmethod(lambda binary: None)
    try:
        yield
    finally:
        ProviderManager._cli_binary_present = original
        ProviderManager._cli_signed_in = original_signed


@pytest.fixture(autouse=True, scope="session")
def _isolate_launch_recipe_probes():
    """No test ever runs the developer's real ``claude``/``codex``/``pi`` (v1.238.0).

    Launch recipes (``terminals/recipes.py``) detect a CLI's version and
    feature-detect its options by running that CLI's own ``--version`` and
    ``--help``. ``detect_ai_clis()`` calls into them, and several existing tests
    call ``detect_ai_clis()`` — so without this a run on THIS machine would spawn
    three real binaries and read a real help text, while CI (which has none of
    them) would read nothing, and the two would disagree about every recipe
    assertion. The same trap ``GROK_HOME``, subscription-CLI detection and the
    OpenCode store are already isolated for above.

    THREE things leak the host, not one, and stubbing only the first left this
    machine behaving differently from a CI runner (found in the v1.238.0 review):

    * ``recipes._run_probe`` is the module's subprocess chokepoint. It answers
      ``""`` — the "unknown" state every recipe is written to degrade into — and
      the probe cache is cleared on the way in so a real answer from an earlier
      import can never be reused.
    * ``recipes._find`` decides whether a CLI is probed AT ALL. Left real, a box
      with ``claude`` installed resolves a path, runs the (stubbed) probe and
      lands on a different recipe row than a runner where nothing resolves.
    * ``pi_adapter._find`` resolves Pi's bundled Node, and ``PiRecipe`` puts that
      path into its ``detail`` — so the developer's own filesystem path was being
      read into an assertion's reach, and Pi answered ``ok=True`` here and
      ``ok=False`` on CI.

    All three are stubbed to the CI-shaped answer. Tests that exercise detection
    set their own via monkeypatch, which transparently overrides these for their
    duration. ``ai_clis._find`` is deliberately NOT stubbed: it decides the
    ``installed`` column for the whole catalog, which many older tests read, and
    it is not part of the recipe machinery this ship added.
    """
    from iron_jarvis.terminals import pi_adapter as _pi_adapter
    from iron_jarvis.terminals import recipes as _recipes

    original = _recipes._run_probe
    original_find = _recipes._find
    original_pi_find = _pi_adapter._find
    _recipes._run_probe = lambda argv, **kw: ""
    _recipes._find = lambda command: None
    _pi_adapter._find = lambda command: None
    _recipes.clear_probe_cache()
    try:
        yield
    finally:
        _recipes._run_probe = original
        _recipes._find = original_find
        _pi_adapter._find = original_pi_find
        _recipes.clear_probe_cache()


@pytest.fixture
def project_root(tmp_path):
    return str(tmp_path)


@pytest.fixture
def platform(project_root):
    return build_platform(project_root)


@pytest.fixture
def orchestrator(platform):
    return Orchestrator(platform)


@pytest.fixture(autouse=True, scope="session")
def _isolate_opencode_store():
    """Keep the developer's REAL OpenCode store out of every test (v1.102.0).

    ``/usage`` and ``/fleet/usage`` both merge OpenCode's own SQLite store, which
    ``opencode_db_path`` resolves from the real ``Path.home()``. On this machine
    that store holds ~54M tokens, so a bare test asserting "no runs yet" saw a
    fleet full of OpenCode work — while CI, which has no store, saw none. Local
    and CI would diverge silently, the same trap ``GROK_HOME`` and CLI detection
    are isolated for above.

    Points the resolver at a path that does not exist, so the merge degrades to
    ``available: False`` exactly as it does on a machine without OpenCode. Tests
    that WANT the merge monkeypatch ``opencode_usage`` directly and are
    unaffected.
    """
    import tempfile
    from pathlib import Path

    from iron_jarvis.eval import opencode_usage as _mod

    original = _mod.opencode_db_path
    nowhere = Path(tempfile.mkdtemp(prefix="ij-test-no-opencode-")) / "opencode.db"

    def _isolated(config=None):
        # Honour an EXPLICIT opencode_data_dir — a test that builds its own store
        # and points config at it is being deliberate, and must still work. Only
        # the implicit fall-through to the real Path.home() is redirected.
        if str(getattr(config, "opencode_data_dir", "") or "").strip():
            return original(config)
        return nowhere

    _mod.opencode_db_path = _isolated
    try:
        yield
    finally:
        _mod.opencode_db_path = original


@pytest.fixture(autouse=True, scope="session")
def _isolate_opencode_config():
    """No test reads the developer's REAL OpenCode config (the gateway-key fix).

    Every app a test starts runs ``warm_opencode`` in its lifespan, which
    resolves OpenCode's local models from ``~/.config/opencode/opencode.json(c)``.
    On the user's PC that config points at their LiteLLM gateway, so EVERY
    ``with TestClient(create_app(...))`` sent ``GET <gateway>/model/info`` with
    no key (a fresh test home knows none) and ran the real ``opencode`` binary
    — measured: one bare lifespan, one unkeyed request to the gateway. CI has
    no such config, so it never saw this. The config path list is empty for
    the session; tests that exercise the parser pass explicit paths."""
    from iron_jarvis.providers import opencode as _oc

    original = _oc._config_paths
    _oc._config_paths = lambda: []
    try:
        yield
    finally:
        _oc._config_paths = original


@pytest.fixture(autouse=True, scope="session")
def _isolate_pi_store():
    """Keep the developer's REAL Pi session store out of every test (v1.213.0).

    Same trap as ``_isolate_opencode_store`` above: ``pi_sessions_root``
    resolves from the real ``Path.home()``, so on a machine where the Pi CLI
    has run, every usage assertion would see real tokens while CI sees none.
    Tests that WANT the merge set ``config.pi_sessions_dir`` (honoured) or
    monkeypatch ``pi_usage`` directly.
    """
    import tempfile
    from pathlib import Path

    from iron_jarvis.eval import pi_usage as _mod

    original = _mod.pi_sessions_root
    nowhere = Path(tempfile.mkdtemp(prefix="ij-test-no-pi-")) / "sessions"

    def _isolated(config=None):
        if str(getattr(config, "pi_sessions_dir", "") or "").strip():
            return original(config)
        return nowhere

    _mod.pi_sessions_root = _isolated
    try:
        yield
    finally:
        _mod.pi_sessions_root = original


@pytest.fixture(autouse=True)
def _join_detection_scans():
    """v1.290.0: a finished session or chat turn starts a detection scan in a
    daemon thread that reads THAT test's temporary database. Join it before the
    test's tmp dir and engine are torn down, so no scan thread outlives the
    database it is reading (a Windows xdist worker crashed mid-suite with
    several such threads alive). Cheap when nothing was scheduled."""
    yield
    try:
        from iron_jarvis.detections.bell import wait_for_scans

        wait_for_scans(timeout=15.0)
    except Exception:  # noqa: BLE001 — cleanup must never fail a test
        pass


@pytest.fixture
def normal_priority():
    """Run the test from a Normal-priority process (v1.333.1).

    A child inherits a below-normal parent's class, and the CI runner starts
    pytest below normal, so "a foreground child stays Normal" cannot be read
    off a child of the worker as it was started. The worker raises ITSELF to
    Normal for the test and goes back after. A worker trapped in a
    below-normal Job (the bug those tests guard) cannot raise itself, so the
    assertion still fails there."""
    if os.name != "nt":
        yield
        return
    import psutil

    me = psutil.Process()
    was = me.nice()
    try:
        me.nice(psutil.NORMAL_PRIORITY_CLASS)
    except Exception:  # noqa: BLE001 — the test's own assertion reports it
        pass
    try:
        yield
    finally:
        try:
            me.nice(was)
        except Exception:  # noqa: BLE001
            pass
