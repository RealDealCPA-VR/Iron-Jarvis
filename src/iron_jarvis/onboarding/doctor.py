"""Self-diagnostic ("doctor") for Iron Jarvis.

A SAFE, read-only health check anyone can run from zero to confirm their machine
can get value out of Iron Jarvis. No check ever raises; each returns a human
``detail`` and an actionable ``fix`` hint. *Required* checks gate the overall
``ok`` (these are what the install itself needs); *recommended* checks — the web
dashboard and voice/browser tooling — only warn so a minimal Python+uv install
still reports healthy.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

#: Minimum Python the platform supports (matches pyproject's requires-python).
MIN_PYTHON: tuple[int, int] = (3, 12)

REQUIRED = "required"
RECOMMENDED = "recommended"


def _result(
    name: str, ok: bool, detail: str, fix: str = "", level: str = REQUIRED
) -> dict:
    """One normalized check row: ``{name, ok, detail, fix, level}``."""
    return {"name": name, "ok": bool(ok), "detail": detail, "fix": fix, "level": level}


def _which(*names: str) -> str | None:
    """First executable among ``names`` found on PATH, else None (read-only)."""
    for n in names:
        try:
            path = shutil.which(n)
        except Exception:  # noqa: BLE001 — never let a probe crash the doctor
            path = None
        if path:
            return path
    return None


def _find_browser() -> str | None:
    """Locate a Chromium-based browser (Chrome/Edge) without launching it.

    Checks PATH first (cross-platform), then well-known Windows/macOS install
    locations. Pure ``os.path.exists`` lookups — nothing is executed.
    """
    path = _which(
        "chrome",
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "msedge",
    )
    if path:
        return path

    candidates: list[str] = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
        base = os.environ.get(env)
        if not base:
            continue
        candidates += [
            os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
        ]
    candidates.append(
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )
    candidates.append(
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
    )
    for c in candidates:
        try:
            if c and os.path.exists(c):
                return c
        except OSError:
            continue
    return None


def check_python() -> dict:
    v = sys.version_info
    ok = (v.major, v.minor) >= MIN_PYTHON
    cur = f"{v.major}.{v.minor}.{v.micro}"
    need = f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}"
    detail = (
        f"Python {cur} (>= {need} required)."
        if ok
        else f"Python {cur} is too old; Iron Jarvis needs >= {need}."
    )
    return _result(
        "python",
        ok,
        detail,
        fix=""
        if ok
        else f"Install Python {need}+ from https://python.org and recreate the venv.",
    )


def check_uv() -> dict:
    path = _which("uv")
    ok = path is not None
    return _result(
        "uv",
        ok,
        f"uv found at {path}."
        if ok
        else "uv (the Python package/runtime manager) is not on PATH.",
        fix=""
        if ok
        else "Install uv: https://docs.astral.sh/uv/ "
        "(PowerShell: `irm https://astral.sh/uv/install.ps1 | iex`).",
        # RECOMMENDED, not REQUIRED: uv is a source/dev tool (self-update, repair).
        # A packaged/frozen install ships its own runtime and runs fine without it,
        # so a missing uv must NOT make the app's self-diagnosis report "broken".
        level=RECOMMENDED,
    )


def check_git() -> dict:
    path = _which("git")
    ok = path is not None
    return _result(
        "git",
        ok,
        f"git found at {path}."
        if ok
        else "git is not on PATH — needed for git-native sessions and review/approve.",
        fix=""
        if ok
        else "Install Git: https://git-scm.com/downloads (optional unless you "
        "want git-native review).",
        level=RECOMMENDED,
    )


def check_node() -> dict:
    path = _which("node")
    ok = path is not None
    return _result(
        "node",
        ok,
        f"node found at {path}."
        if ok
        else "Node.js not found — only needed to run the web dashboard.",
        fix=""
        if ok
        else "Install Node.js LTS: https://nodejs.org "
        "(the dashboard is optional; the CLI and daemon work without it).",
        level=RECOMMENDED,
    )


def check_pnpm() -> dict:
    path = _which("pnpm")
    ok = path is not None
    return _result(
        "pnpm",
        ok,
        f"pnpm found at {path}."
        if ok
        else "pnpm not found — only needed to install/run the web dashboard.",
        fix=""
        if ok
        else "Install pnpm: https://pnpm.io/installation (or run `corepack enable`). "
        "Dashboard-only.",
        level=RECOMMENDED,
    )


def check_browser() -> dict:
    path = _find_browser()
    ok = path is not None
    return _result(
        "browser",
        ok,
        f"Chromium-based browser found ({path})."
        if ok
        else "No Chrome/Edge found — needed for the voice UI and browser automation.",
        fix=""
        if ok
        else "Install Google Chrome (https://google.com/chrome) or Microsoft Edge.",
        level=RECOMMENDED,
    )


def check_pdf_classifier() -> dict:
    """The per-page scan router (v1.176.0) is a NATIVE extension.

    RECOMMENDED, not required: without it the document pipeline falls back to
    the v1.174.0 whole-document heuristic and still reads wholly-scanned files.
    What is lost is silent and specific — a native-text return with a SCANNED
    page stapled in reads as if that page were blank. A packaged build that
    dropped the extension would degrade exactly that way forever without a word,
    which is the pikepdf lesson; this check is how it says so instead.
    """
    from ..documents.pdf_classify import available

    ok = available()
    return _result(
        "pdf scan routing",
        ok,
        "Per-page PDF scan routing is available (mixed documents are detected)."
        if ok
        else "pdf-inspector is missing — scanned pages inside an otherwise "
        "text-based PDF will not be found (whole-file scans still are).",
        fix="" if ok else "Reinstall dependencies (`uv sync`); if this is the "
        "packaged app, the native extension is missing from the build.",
        level=RECOMMENDED,
    )


def check_guide_docs() -> dict:
    """The Guide's bundled reference docs are all present (v1.223.0).

    RECOMMENDED, not required: the app runs without them, but the built-in
    Iron Jarvis Guide then answers from the live catalogs alone and says so.
    In a packaged build a missing file means the .spec's ``ijdocs`` bundle
    drifted from ``guide.BUNDLED_DOCS`` — the silent-degradation shape this
    project keeps meeting (pikepdf, the pdf classifier), caught here instead.
    """
    from ..guide import BUNDLED_DOCS, doc_path

    missing = [Path(rel).name for _slug, rel, _title in BUNDLED_DOCS if not doc_path(rel).is_file()]
    ok = not missing
    return _result(
        "guide_docs",
        ok,
        f"all {len(BUNDLED_DOCS)} Guide reference docs present."
        if ok
        else f"Guide reference docs missing: {', '.join(missing)} — the Guide cannot answer from them.",
        fix=""
        if ok
        else "Reinstall the current release; if running from source, restore the files in the repo.",
        level=RECOMMENDED,
    )


# --------------------------------------------------------------------------- #
# The browser add-on (D25, D27) - where it is, and whether it was BUILT.
# --------------------------------------------------------------------------- #

#: Points the add-on lookup at an explicit folder, read PER CALL (the shape
#: ``browser.identity.EXTENSION_ID_ENV`` has). The desktop supervisor can name the
#: folder it installed, and a test can build a layout without freezing anything.
BROWSER_ADDON_ENV = "IRONJARVIS_BROWSER_ADDON_DIR"

#: The add-on folder inside a PACKAGED install, relative to the resources root.
#: electron-builder copies the built add-on there as an extraResource; the frozen
#: daemon sits beside it in ``<resources>/daemon``.
BROWSER_ADDON_RESOURCE_DIR = "browser-addon"

#: Other names that folder has been given, tried after the one above. A renamed
#: extraResource must not make the doctor say "no add-on shipped" while the folder
#: is sitting right there: the add-on is IDENTIFIED by its manifest
#: (:func:`_is_addon_dir`), not by the name chosen in ``desktop/package.json``.
_BROWSER_ADDON_ALIASES = ("browser_addon", "chrome-addon", "extension", "addon")

#: The bundles the add-on loads AT RUN TIME, which ``manifest.json`` does not name.
#:
#: There is no ``content_scripts`` block by design (plan section 6): the content
#: script is injected on demand by ``chrome.scripting.executeScript`` from
#: ``src/background/tabs.ts`` (``CONTENT_SCRIPT_FILE``), and the host-permission
#: setup page is opened by ``src/background/hostperms.ts`` (``SETUP_PAGE``). So a
#: check that read ONLY the manifest -- which is what this one did until v1.239.0 --
#: called a ``dist/`` holding ``background.js`` and the side panel "built and ready
#: to load" while every ``read_page`` failed with a bare injection error and the
#: site-access setup page could not open at all: the exact silent-blame case this
#: row exists to end.
#:
#: The SIDE PANEL is not in this tuple and must not be added: ``manifest.json``
#: names it at ``side_panel.default_path``, so :func:`_addon_build_missing` reads it
#: from the manifest the way Chrome does, and a second hard-coded copy here could
#: disagree with the shipped manifest in silence.
#:
#: Named HERE rather than read from those sources, because a PACKAGED install ships
#: ``manifest.json``, ``README.md`` and ``dist/**`` and no ``src/`` at all
#: (``desktop/package.json`` extraResources filter), so there is nothing on disk to
#: read. The build's own verifier
#: (``extensions/chrome/scripts/build.mjs::requiredFiles``) reads the two constants
#: with a regex, and ``test_browser_doctor_v1239`` reads them with the SAME regex and
#: asserts this tuple equals what it found -- so a rename on either side fails a test
#: here as well as the build, and the two lists cannot drift in silence.
BROWSER_ADDON_RUNTIME_FILES: tuple[str, ...] = ("dist/content.js", "dist/setup.html")


def _is_addon_dir(path: Path) -> bool:
    """Whether *path* looks like the add-on: a folder holding a ``manifest.json``."""
    try:
        return path.is_dir() and (path / "manifest.json").is_file()
    except OSError:
        return False


def browser_addon_dir() -> Path | None:
    """The folder a user points Chrome's *Load unpacked* at, or ``None``.

    ONE resolver for both layouts, because the doctor is what a user runs when the
    add-on will not load and "which folder?" is half the question:

    * the env override, when it names a real add-on folder;
    * **packaged**: beside the frozen daemon under Electron's ``resources`` --
      ``<resources>/browser-addon``, then the aliases, then any immediate child of
      ``resources`` carrying a ``manifest.json``;
    * **dev**: ``<repo>/extensions/chrome``.

    Returns the folder even when its build output is missing. "Present but
    unbuilt" is the failure this ship exists to catch -- ``extensions/chrome/dist``
    is gitignored, so a release that skipped the extension build ships a folder
    Chrome refuses -- and only a check that FOUND the folder can report it.
    """
    override = (os.environ.get(BROWSER_ADDON_ENV) or "").strip()
    if override:
        candidate = Path(override)
        if _is_addon_dir(candidate):
            return candidate

    if getattr(sys, "frozen", False):
        try:
            resources = Path(sys.executable).resolve().parent.parent
        except OSError:  # pragma: no cover - defensive
            return None
        for name in (BROWSER_ADDON_RESOURCE_DIR, *_BROWSER_ADDON_ALIASES):
            candidate = resources / name
            if _is_addon_dir(candidate):
                return candidate
        try:
            children = sorted(resources.iterdir())
        except OSError:
            children = []
        for child in children:
            if _is_addon_dir(child):
                return child
        return None

    candidate = Path(__file__).resolve().parents[3] / "extensions" / "chrome"
    return candidate if _is_addon_dir(candidate) else None


def _addon_manifest(folder: Path) -> dict:
    """The add-on's ``manifest.json`` as a dict, or ``{}``. Never raises."""
    import json

    try:
        loaded = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _addon_build_missing(folder: Path) -> list[str]:
    """The files ``manifest.json`` points at that are NOT on disk.

    Read OUT of the manifest rather than listed here: the manifest is what Chrome
    loads, so a build that renamed ``background.js`` is caught by the same read
    Chrome performs, and a second hard-coded list could not disagree with it
    silently. An unreadable manifest reports itself as the missing file.

    The manifest is not the WHOLE list, though, and believing it was is what made
    this check green on an add-on that could not read a page:
    :data:`BROWSER_ADDON_RUNTIME_FILES` carries the bundles the background script
    loads at run time, which no manifest field names.
    """
    manifest = _addon_manifest(folder)
    if not manifest:
        return ["manifest.json"]
    wanted: list[str] = []
    background = manifest.get("background")
    if isinstance(background, dict) and background.get("service_worker"):
        wanted.append(str(background["service_worker"]))
    # ``side_panel.default_path``, NOT ``action.default_popup``: the popup was retired
    # in v1.242.0 (D32 -- Chrome ignores ``openPanelOnActionClick`` while a popup is
    # declared), and a check still reading the old field would find nothing, check
    # nothing, and report a panel-less add-on as ready to load. ``action`` is still in
    # the manifest, carrying only ``default_title``, so reading it would find no file
    # at all and this row would quietly stop covering the surface the user clicks.
    panel = manifest.get("side_panel")
    if isinstance(panel, dict) and panel.get("default_path"):
        wanted.append(str(panel["default_path"]))
    for entry in manifest.get("content_scripts") or []:
        if isinstance(entry, dict):
            wanted += [str(js) for js in (entry.get("js") or [])]
    wanted += [name for name in BROWSER_ADDON_RUNTIME_FILES if name not in wanted]
    missing: list[str] = []
    for rel in wanted:
        try:
            if not (folder / rel).is_file():
                missing.append(rel)
        except OSError:  # pragma: no cover - defensive
            missing.append(rel)
    return missing


def _addon_manifest_id(folder: Path) -> tuple[str, str]:
    """``(derived id, problem)`` for the manifest's ``key``. Never raises.

    The id Chrome will actually compute for this folder (D27A). The check compares
    it with the id the daemon's origin allowlist admits, because those two parting
    is a total and silent failure: the real add-on loads, connects, and is refused
    at ``/browser/ws`` with 1008 while the card waits to pair forever.
    """
    from ..browser.identity import extension_id_from_spki_b64

    manifest = _addon_manifest(folder)
    if not manifest:
        return "", "its manifest could not be read"
    key = str(manifest.get("key") or "")
    if not key:
        return "", "its manifest carries no 'key', so Chrome would give it a random id"
    try:
        return extension_id_from_spki_b64(key), ""
    except Exception as exc:  # noqa: BLE001 - a doctor check never raises
        return "", f"its manifest key is not a usable public key ({exc})"


def check_browser_addon() -> dict:
    """The browser add-on ships, is BUILT, and carries the pinned id (D25, D27A).

    RECOMMENDED, never REQUIRED: Iron Jarvis runs fine with no browser attached,
    and a machine that has never loaded the add-on is not broken. Named
    ``browser_addon`` because ``check_browser`` already answers a different
    question -- is a Chromium browser installed at all.

    What is silently LOST when this fails is specific. ``extensions/chrome/dist``
    is gitignored and produced by the release, so a build that skipped that stage
    ships a folder whose ``manifest.json`` points at a service worker that is not
    there: Chrome refuses to register it, the user sees an add-on error rather
    than an Iron Jarvis one, and nothing in this app says a word. A manifest
    ``key`` that has drifted from ``browser.identity`` is worse than an absent
    one -- the add-on loads, connects, and is refused at ``/browser/ws`` with 1008
    by the origin allowlist, leaving the card on "Waiting to pair" with no
    explanation anywhere.
    """
    from ..browser.identity import (
        EXTENSION_ID_ENV,
        PINNED_EXTENSION_ID,
        pinned_extension_id,
    )

    expected = pinned_extension_id()
    # The pinned id is CONFIGURABLE (``EXTENSION_ID_ENV``), and an override left in
    # the environment by an earlier experiment narrows the origin allowlist to an
    # add-on the user is no longer running -- every connection refused with 1008
    # and nothing anywhere naming the reason. So when the id in force is not the
    # built-in one, every sentence below says where it came from.
    source = f" (from {EXTENSION_ID_ENV})" if expected != PINNED_EXTENSION_ID else ""
    folder = browser_addon_dir()
    if folder is None:
        return _result(
            "browser_addon",
            False,
            "the browser add-on folder is not in this install - Load unpacked has "
            "nothing to point at, so no browser can be paired.",
            fix="Reinstall the current release; from source the folder is extensions/chrome.",
            level=RECOMMENDED,
        )

    missing = _addon_build_missing(folder)
    if missing:
        return _result(
            "browser_addon",
            False,
            f"the browser add-on at {folder} is not built: {', '.join(missing)} "
            "missing - Chrome refuses to load it, so pairing can never start.",
            fix="Reinstall the current release; from source, run `pnpm install && pnpm build` "
            "in extensions/chrome.",
            level=RECOMMENDED,
        )

    derived, problem = _addon_manifest_id(folder)
    if problem:
        return _result(
            "browser_addon",
            False,
            f"the browser add-on at {folder} is built, but {problem} - the daemon "
            f"admits only {expected}{source} at /browser/ws, so it would be refused.",
            fix="Reinstall the current release; the add-on's manifest key must be the one in "
            "iron_jarvis/browser/identity.py.",
            level=RECOMMENDED,
        )
    if derived != expected:
        return _result(
            "browser_addon",
            False,
            f"the browser add-on at {folder} loads as {derived}, but the daemon admits "
            f"only {expected}{source} at /browser/ws - it would be refused with no message, "
            "and the card would wait to pair forever.",
            fix="Reinstall the current release; if you loaded your own build, set "
            "IRONJARVIS_BROWSER_EXTENSION_ID to its id.",
            level=RECOMMENDED,
        )
    return _result(
        "browser_addon",
        True,
        f"browser add-on built and ready to load from {folder} (id {expected}{source}).",
        level=RECOMMENDED,
    )

#: Ordered list of every check callable — callers may render this directly.
CHECKS = [
    check_python,
    check_uv,
    check_git,
    check_node,
    check_pnpm,
    check_browser,
    check_pdf_classifier,
    check_guide_docs,
    check_browser_addon,
]


def runtime_checks(platform) -> list[dict]:
    """Live health of a RUNNING install — the failure modes a daily driver hits
    (no model connected, lost secrets key, a corrupt DB) that the machine-prereq
    checks above can't see. Each is read-only and never raises. Only meaningful
    with a built platform, so the offline CLI ``doctor`` (no platform) skips these.
    """
    checks: list[dict] = []

    # A usable model is connected (else every session silently runs on mock).
    try:
        health = platform.providers.health()
        live = [p["provider"] for p in health if p.get("available") and p.get("class") != "mock"]
        ok = bool(live)
        checks.append(
            _result(
                "provider",
                ok,
                f"connected: {', '.join(live)}" if ok else "no real model connected — sessions fall back to mock",
                fix="" if ok else "Connect a provider (API key or account login) on the Connections page.",
                # Recommended, not required: mock works offline (demo/first-run), so a
                # missing paid model is a warning, not an "install is broken".
                level=RECOMMENDED,
            )
        )
        # The mock-trap: a real provider exists but the default still points at mock.
        default_provider = getattr(platform.config, "default_provider", "mock")
        if ok and default_provider == "mock":
            checks.append(
                _result(
                    "default_model",
                    False,
                    "default provider is still 'mock' while a real provider is connected",
                    fix="Set your connected provider as the default on the Connections page.",
                    level=RECOMMENDED,
                )
            )
    except Exception as exc:  # noqa: BLE001
        checks.append(_result("provider", False, f"provider health failed: {exc}", level=RECOMMENDED))

    # Subscription CLIs (v1.234.0): installed is not signed in. A logged-out
    # `claude` used to read "connected" everywhere and refuse the first turn.
    try:
        from ..providers.cli_auth import CLI_BINARIES, SIGN_IN_FIX

        status_fn = getattr(platform.providers, "cli_login_status", None)
        if callable(status_fn):
            for prov, binary in CLI_BINARIES.items():
                st = status_fn(prov) or {}
                if not st.get("installed"):
                    continue
                signed = st.get("signed_in")
                ok = signed is not False
                if signed:
                    detail = f"{binary} CLI installed and signed in"
                elif ok:
                    detail = f"{binary} CLI installed; sign-in status not confirmed yet"
                else:
                    detail = f"{binary} CLI installed but NOT signed in — it refuses every request"
                checks.append(
                    _result(
                        f"{prov}_login",
                        ok,
                        detail,
                        fix="" if ok else SIGN_IN_FIX[binary],
                        level=RECOMMENDED,
                    )
                )
    except Exception as exc:  # noqa: BLE001
        checks.append(_result("cli_login", False, f"CLI sign-in check failed: {exc}", level=RECOMMENDED))

    # The secrets key actually decrypts stored credentials (catches a key-less restore).
    try:
        valid = platform.secrets.key_valid()
        checks.append(
            _result(
                "secrets_key",
                valid,
                "secrets key decrypts stored credentials" if valid else "secrets key cannot decrypt stored credentials (lost/mismatched key)",
                fix="" if valid else "Restore <home>/secrets/.secrets.key from a backup, or reconnect your providers to re-store credentials.",
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(_result("secrets_key", False, f"secrets check failed: {exc}", level=RECOMMENDED))

    # Database integrity (a corrupt SQLite is unrecoverable from inside the UI).
    try:
        from sqlalchemy import text

        with platform.engine.connect() as conn:
            integ = conn.execute(text("PRAGMA integrity_check")).scalar()
        ok = integ == "ok"
        checks.append(
            _result(
                "database",
                ok,
                "database integrity ok" if ok else f"database integrity check failed: {integ}",
                fix="" if ok else "Run POST /diagnostics/repair {action:'prune_events'} or restore from a backup.",
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(_result("database", False, f"integrity check failed: {exc}"))

    # Free disk on the state home (a full disk silently breaks the DB, backups,
    # and every artifact/session write — often with no obvious in-app error).
    try:
        home = platform.config.home
        free_gb = shutil.disk_usage(str(home)).free / (1024**3)
        if free_gb < 1.0:
            ok, level = False, REQUIRED
        elif free_gb < 5.0:
            ok, level = False, RECOMMENDED
        else:
            ok, level = True, RECOMMENDED
        checks.append(
            _result(
                "disk_space",
                ok,
                f"{free_gb:.1f} GB free on the state home"
                if ok
                else f"only {free_gb:.1f} GB free on the state home — writes may start failing",
                fix="" if ok else "Free up disk space (clear old backups/sessions) or move the state home to a larger drive.",
                level=level,
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(_result("disk_space", False, f"disk-space check failed: {exc}", level=RECOMMENDED))

    # Every configured MCP server actually STARTED (v1.229.0, audit U4). A
    # server skipped at boot left one WARNING in daemon.log; the Tools page
    # said "0 tools" and the Overview said nominal. The load record now keeps
    # the reason, and `npx` (what most catalog servers launch with) is resolved
    # the way the Build page resolves CLIs — PATH first, then the per-user bin
    # dirs a GUI-launched daemon never sees (`%LOCALAPPDATA%\pi-node\current`,
    # beside the node that check found).
    try:
        checks.append(check_mcp(platform))
    except Exception as exc:  # noqa: BLE001
        checks.append(_result("mcp", False, f"mcp check failed: {exc}", level=RECOMMENDED))

    # The scheduler runs on the LOCAL zone (v1.231.0, audit AE12). When tzlocal
    # cannot name the Windows zone the daemon used to abort at build_platform;
    # now the scheduler falls back to UTC and says so here — a "0 3 * * *"
    # nightly on that box fires at 03:00 UTC, which the user must know.
    try:
        note = getattr(getattr(platform, "scheduler", None), "timezone_note", "") or ""
        checks.append(
            _result(
                "scheduler_timezone",
                not note,
                "schedules run on the local time zone."
                if not note
                else f"local time zone could not be resolved ({note}) — schedules run on UTC.",
                fix=""
                if not note
                else "Pick a standard zone in Windows Settings > Time & language, or set the TZ environment variable (e.g. America/New_York), then restart.",
                level=RECOMMENDED,
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(
            _result("scheduler_timezone", False, f"time-zone check failed: {exc}", level=RECOMMENDED)
        )

    # The browser bridge (v1.239.0, D25): the service the platform built, the
    # routes actually being served, the access setting, and the pairing/connection
    # state. Not paired is not broken, so those are reported with ok true.
    try:
        checks.append(check_browser_bridge(platform))
    except Exception as exc:  # noqa: BLE001
        checks.append(
            _result("browser_bridge", False, f"browser check failed: {exc}", level=RECOMMENDED)
        )

    # The custom endpoint's configured model actually EXISTS on that gateway.
    # A renamed gateway alias otherwise 400s every request routed there with a
    # cryptic provider error (live-hit 2026-07-31: model 'brain' after the
    # gateway's list became fleet/vision/frontier).
    try:
        base = (getattr(platform.config, "custom_base_url", "") or "").strip()
        model = (getattr(platform.config, "custom_model", "") or "").strip()
        if base and model:
            import httpx

            key = None
            try:
                key = platform.secrets.get("custom_api_key")
            except Exception:  # noqa: BLE001
                key = None
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            r = httpx.get(base.rstrip("/") + "/models", headers=headers, timeout=5)
            if r.status_code == 200:
                ids = [
                    str(m.get("id"))
                    for m in (r.json().get("data") or [])
                    if isinstance(m, dict) and m.get("id")
                ]
                ok = model in ids
                checks.append(
                    _result(
                        "custom_endpoint_model",
                        ok,
                        f"endpoint model '{model}' is served by the gateway"
                        if ok
                        else (
                            f"endpoint model '{model}' is NOT on the gateway — "
                            f"it serves: {', '.join(ids[:6]) or 'nothing'}"
                        ),
                        fix=""
                        if ok
                        else "Pick one of the served models for the custom endpoint on the Connections page.",
                        level=RECOMMENDED,
                    )
                )
            # non-200/unreachable: the provider-connected check owns that story.
    except Exception:  # noqa: BLE001 — a network hiccup must not fail the doctor
        pass

    return checks


def _find_npx() -> str | None:
    """``npx`` on PATH, else beside a known node / in the per-user bin dirs."""
    from ..terminals.ai_clis import _find

    return _find("npx")


def check_mcp(platform) -> dict:
    """Each configured MCP server's last load result, plus the ``npx`` launcher
    when any server needs it. RECOMMENDED: a pack that did not start costs the
    agents its tools, not the install."""
    from ..mcp.tools import load_status

    servers = [
        s for s in (getattr(platform.config, "mcp_servers", None) or []) if isinstance(s, dict)
    ]
    if not servers:
        return _result("mcp", True, "no MCP servers configured.", level=RECOMMENDED)
    problems: list[str] = []
    needs_npx = [str(s.get("name") or "mcp") for s in servers if str(s.get("command") or "").lower() in ("npx", "npx.cmd")]
    if needs_npx and _find_npx() is None:
        problems.append(f"npx not found (needed by {', '.join(needs_npx)})")
    # The registry is the ground truth of what agents can USE; the load record
    # is the reason. A server that was attempted and holds no live tools is
    # not started even when its record is clean (a probe, or a server that
    # advertised nothing) — the record alone was fooled once.
    registry = getattr(platform, "registry", None)
    live_names = getattr(registry, "mcp_names", None) if registry is not None else None
    for s in servers:
        name = str(s.get("name") or "mcp")
        status = load_status(name)
        if status and status.get("last_error"):
            problems.append(f"{name} didn't start: {status['last_error']}")
        elif status and callable(live_names):
            try:
                live = len(live_names(name))
            except Exception:  # noqa: BLE001 — a doctor check never raises
                live = None
            if live == 0:
                problems.append(f"{name} didn't start: no tools loaded (attempted, none registered)")
    ok = not problems
    return _result(
        "mcp",
        ok,
        f"all {len(servers)} MCP server{'s' if len(servers) != 1 else ''} started."
        if ok
        else "; ".join(problems),
        fix=""
        if ok
        else "Open Tools → Connected packs and press Retry on the pack; if npx is missing, install Node.js LTS (https://nodejs.org) and restart Iron Jarvis.",
        level=RECOMMENDED,
    )



def check_browser_bridge(platform) -> dict:
    """The live half of the browser diagnostics (D25). RECOMMENDED, never raises.

    Covers what only a running install can answer -- the service the platform
    built, the routes that are actually being served, the ``browser_access``
    setting, the pairing record and the live socket -- and leaves the on-disk
    add-on and the pinned id to :func:`check_browser_addon`, which needs no
    platform and so also runs from the offline CLI doctor.

    **Not connected is not broken.** ``browser_access`` ships ``off`` and most
    installs never pair a browser, so those states are REPORTED in the detail with
    ``ok`` true. A doctor that flagged them would be crying wolf on every fresh
    install, and a user who learns to ignore a row learns to ignore the row that
    matters. What DOES fail here is a bridge that cannot work at all: no runtime on
    the platform (every ``browser_*`` tool is missing and the card is dead), routes
    that were never registered (the add-on's socket has nowhere to connect), or a
    paired browser whose transport is holding an unretired fault -- the one case
    where the user believes they have a working browser and does not.

    **Unknown is not a negative.** A fact this check cannot READ (an unreadable
    config store, a transport that raises) is reported as unknown and NOT ok, never
    as the falsy default of the variable it failed to fill. Saying "no browser is
    connected" on the strength of an exception is a claim about the user's browser
    that nothing in this process supports, and a green row over it hides the only
    failure the user could still act on.
    """
    from ..daemon.routes.browser import SERVED_PATHS, WS_PATH, served_paths

    # getattr, but for a platform that is still BUILDING: a half-constructed
    # object can raise out of a property, and `getattr(..., None)` only swallows
    # AttributeError. The doctor is what a user runs when something is already
    # wrong, so it is the last place allowed to raise.
    try:
        runtime = getattr(platform, "browser", None)
    except Exception:  # noqa: BLE001 - a doctor check never raises
        runtime = None
    if runtime is None:
        return _result(
            "browser_bridge",
            False,
            "the browser service did not initialise - every browser tool is missing "
            "and the Your browser card cannot pair anything.",
            fix="Restart Iron Jarvis; if it persists, reinstall the current release "
            "(check daemon.log for the boot error).",
            level=RECOMMENDED,
        )

    facts: list[str] = []

    # Asked ABOUT THIS PLATFORM, not about the process: ``served_paths`` records
    # what it registered on the platform it registered for, so a second app built
    # anywhere in this process cannot make this row describe the wrong route table.
    unserved = sorted(set(SERVED_PATHS) - served_paths(platform))
    if unserved:
        return _result(
            "browser_bridge",
            False,
            "the browser routes are not being served in this process "
            f"({', '.join(unserved)}) - the add-on has nowhere to connect.",
            fix="Restart Iron Jarvis; if it persists, reinstall the current release.",
            level=RECOMMENDED,
        )
    facts.append(f"the pairing socket is served at {WS_PATH}")

    # What could not be READ AT ALL, as opposed to what read as "no". The two are
    # not the same row and this check used to conflate them: an unreadable fact left
    # its variable at its falsy default, so a runtime whose every question raised
    # rendered GREEN and asserted "no browser is connected" -- a claim about the
    # user's browser derived from an exception. A fact nobody could read is not a
    # negative fact; it is a bridge whose state is unknown, which is the one thing
    # this row must never report with confidence.
    unreadable: list[str] = []

    try:
        access = str(runtime.access() or "off").strip()
    except Exception as exc:  # noqa: BLE001 - a doctor check never raises
        access = ""
        unreadable.append("the browser_access setting")
        facts.append(f"the browser_access setting could not be read ({exc})")
    if access == "off":
        facts.append("browser access is off, so Jarvis will not speak to a browser")
    elif access:
        facts.append(f"browser access is {access}")

    paired = None
    try:
        store = getattr(runtime, "pairing", None)
        paired = bool(store is not None and store.paired())
    except Exception as exc:  # noqa: BLE001
        unreadable.append("the pairing record")
        facts.append(f"the pairing record could not be read ({exc})")
    if paired is True:
        facts.append("a browser is paired")
    elif paired is False:
        facts.append("no browser is paired yet")

    # ``None``, not ``{}``: the pairing branch above already distinguishes "could
    # not read" from False by leaving its variable None, and the connection branch
    # has to do the same or it says something it does not know.
    view: dict | None = None
    try:
        view = dict(runtime.backend.status())
    except Exception as exc:  # noqa: BLE001
        unreadable.append("the transport's state")
        facts.append(f"the transport could not report its state ({exc})")
    connected = bool(view.get("connected")) if view is not None else None
    if connected is True:
        facts.append("a browser is connected")
    elif connected is False:
        facts.append("no browser is connected")
    last_error = str((view or {}).get("last_error") or "").strip()

    if unreadable:
        # No remedy would be worse than a wrong one: the user is looking at a row
        # that cannot say whether their browser works, and the honest instruction is
        # the same one the missing-runtime row gives. Naming WHAT could not be read
        # keeps the fix pointed at this failure rather than at browsers in general.
        return _result(
            "browser_bridge",
            False,
            "; ".join(facts) + " - so this row cannot say whether your browser works.",
            fix=f"Restart Iron Jarvis; if it persists, reinstall the current release "
            f"({', '.join(unreadable)} could not be read - daemon.log has the error).",
            level=RECOMMENDED,
        )

    if paired and not connected and last_error:
        return _result(
            "browser_bridge",
            False,
            "; ".join(facts) + f" - last problem: {last_error}",
            fix="Open Chrome with the add-on loaded; if it does not reconnect, press "
            "Forget on the Your browser card and pair again.",
            level=RECOMMENDED,
        )
    if last_error:
        facts.append(f"last problem: {last_error}")
    return _result("browser_bridge", True, "; ".join(facts) + ".", level=RECOMMENDED)


def doctor(platform=None) -> dict:
    """Run every check and summarize readiness.

    Returns ``{"ok": bool, "checks": [{name, ok, detail, fix, level}, ...]}``.
    ``ok`` is True iff every *required* check passes; recommended checks only
    warn. When a built ``platform`` is passed, live runtime checks (provider
    connected, secrets key valid, DB integrity) are appended. Never raises.
    """
    checks: list[dict] = []
    for fn in CHECKS:
        try:
            checks.append(fn())
        except Exception as exc:  # noqa: BLE001 — diagnostics must never crash
            name = getattr(fn, "__name__", "check").replace("check_", "")
            checks.append(
                _result(
                    name,
                    False,
                    f"check '{name}' failed to run: {exc}",
                    fix="This is a bug in the doctor check; please report it.",
                    level=RECOMMENDED,
                )
            )
    if platform is not None:
        try:
            checks.extend(runtime_checks(platform))
        except Exception:  # noqa: BLE001 — never let runtime checks crash the doctor
            pass
    ok = all(c["ok"] for c in checks if c.get("level") == REQUIRED)
    return {"ok": ok, "checks": checks}
