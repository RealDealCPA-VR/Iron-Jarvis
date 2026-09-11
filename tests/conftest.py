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
