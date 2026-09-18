"""v1.272.0 — the microphone can be allowed: a page in a tab asks what a side panel cannot.

THE REPORT (2026-09-18): "when I try to use the mic option, it doesn't say I have
permission without the ability to give it permission."

What was true: Chromium answers ``getUserMedia`` from a side panel with
NotAllowedError and NO prompt, and the v1.269.0 panel then said "Microphone
blocked ... Allow it for this add-on in your browser's site permissions" — a place
that does not exist for a side panel. The permission belongs to the add-on's
ORIGIN, and the browser shows the prompt for a page of that origin open in a TAB;
setup.html already uses that shape for site access.

Now: ``src/mic/mic.html`` + ``mic.ts`` (one button, the call inside the click), the
worker opens it on ``request_microphone`` and broadcasts ``mic_permission`` when
the page reports, the panel asks for it on NotAllowedError and says what to do.
Source pins here; the runtime suite drives the built panel.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from iron_jarvis.onboarding import doctor as _doctor_pkg  # noqa: F401 — the package re-exports the function

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "extensions" / "chrome"
SRC = ADDON / "src"
MIC_HTML = SRC / "mic" / "mic.html"
MIC_TS = SRC / "mic" / "mic.ts"
WORKER = SRC / "background" / "index.ts"
HOSTPERMS = SRC / "background" / "hostperms.ts"
PANEL_TS = SRC / "sidepanel" / "sidepanel.ts"
ESBUILD = ADDON / "esbuild.config.mjs"


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _visible(html: str) -> str:
    text = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    text = re.sub(r"<style.*?</style>", "", text, flags=re.S)
    text = re.sub(r"<script.*?</script>", "", text, flags=re.S)
    return re.sub(r"<[^>]+>", " ", text)


# --------------------------------------------------------------------------- #
# 1. The page
# --------------------------------------------------------------------------- #


def test_the_page_has_one_button_an_outcome_line_and_its_script():
    html = _src(MIC_HTML)
    assert re.search(r'<button id="allow"[^>]*>\s*Allow the microphone\s*<', html), "no Allow button"
    assert 'id="outcome"' in html
    assert '<script src="mic.js"></script>' in html
    assert html.count("<button") == 1, "the page gained a second control; it grants or reports, nothing else"


def test_the_page_says_why_it_exists_and_where_to_unblock():
    text = " ".join(_visible(_src(MIC_HTML)).split())
    assert "never inside the sidebar" in text, "the page does not say why the sidebar could not ask"
    assert "Nothing is recorded" in text
    assert "never to a cloud speech service" in text
    assert "left of the address bar" in text and "set Microphone to Allow" in text, "no remedy for an earlier block"


def test_the_page_never_calls_the_addon_an_extension():
    text = _visible(_src(MIC_HTML))
    hits = re.findall(r"\bextension\b", text, flags=re.I)
    assert not hits, f"mic.html calls the add-on an extension {len(hits)} time(s)"


# --------------------------------------------------------------------------- #
# 2. The script: the call is inside the click, with no await before it
# --------------------------------------------------------------------------- #


def test_get_user_media_is_called_inside_the_click_with_no_await_before_it():
    ts = _src(MIC_TS)
    handler = ts[ts.index('addEventListener("click"'):]
    call = handler.index("getUserMedia({ audio: true })")
    code_before = re.sub(r"//.*", "", handler[:call])  # comments may SAY "no await"; code may not have one
    assert not re.search(r"\bawait\b", code_before), "an await before getUserMedia loses the user gesture and the prompt never appears"
    assert re.search(r"getTracks\(\)\.forEach\(\(track\) => track\.stop\(\)\)", ts), "the stream is not stopped — the page would keep the microphone open"
    for tone in ("granted", "refused", "error"):
        assert re.search(r'report\(\s*"' + tone + '"', ts), f"the outcome is not reported as {tone!r}"
    assert 'kind: "mic_permission_result"' in ts, "the page never tells the worker"
    assert "NotAllowedError" in ts and "left of the address bar" in ts, "a refusal does not name the site-info remedy"


# --------------------------------------------------------------------------- #
# 3. The bundler, the worker, the panel, the doctor
# --------------------------------------------------------------------------- #


def test_the_bundler_builds_and_copies_the_page():
    config = _src(ESBUILD)
    assert 'mic: join(ROOT, "src/mic/mic.ts")' in config, "no mic entry point: dist/mic.js is never written"
    assert '["src/mic/mic.html", "mic.html"]' in config, "mic.html is not copied into dist/"


def test_the_worker_opens_the_page_on_request_and_relays_the_grant():
    worker = _src(WORKER)
    assert '| { kind: "request_microphone" }' in worker
    assert '| { kind: "mic_permission_result"; granted: boolean }' in worker
    assert '| { kind: "mic_permission"; granted: boolean }' in worker
    assert re.search(r'case "request_microphone":[\s\S]{0,600}await openMicPage\(\);', worker), "the worker does not open the page"
    assert re.search(r'case "mic_permission_result": \{[\s\S]{0,400}broadcast\(\{ kind: "mic_permission", granted \}\)', worker), (
        "the worker does not relay the page's report to the panel"
    )
    hostperms = _src(HOSTPERMS)
    assert 'export const MIC_PAGE = "dist/mic.html";' in hostperms
    assert "export async function openMicPage()" in hostperms and "openAddonPage(MIC_PAGE)" in hostperms
    assert "openAddonPage(SETUP_PAGE)" in hostperms, "the two pages no longer share one opener"


def test_the_panel_asks_for_the_page_and_never_says_blocked_with_nowhere_to_allow():
    panel = _src(PANEL_TS)
    branch = re.search(r'if \(name === "NotAllowedError"\) \{([\s\S]*?)\n    \}', panel)
    assert branch, "the panel has no NotAllowedError branch"
    body = branch.group(1)
    assert 'sendMessage({ kind: "request_microphone" })' in body, "the panel does not ask the worker for the page"
    assert "cannot ask inside this sidebar" in body and "press the microphone here again" in body
    assert "site permissions" not in panel, "the old sentence — a place that does not exist for a side panel — is back"
    assert 'body.kind === "mic_permission"' in panel and "Microphone allowed" in panel


def test_the_doctor_names_the_page_among_the_addons_runtime_files():
    import importlib

    doctor = importlib.import_module("iron_jarvis.onboarding.doctor")
    assert "dist/mic.html" in doctor.BROWSER_ADDON_RUNTIME_FILES
    assert "dist/setup.html" in doctor.BROWSER_ADDON_RUNTIME_FILES, "the setup page fell out of the list"


def test_a_built_addon_folder_carries_the_page_and_its_script():
    dist = ADDON / "dist"
    if not (dist / "background.js").exists():
        pytest.skip("extensions/chrome/dist is not built here; the CI job builds it")
    for rel in ("mic.html", "mic.js"):
        built = dist / rel
        assert built.is_file() and built.stat().st_size > 0, f"dist/{rel} is missing or empty in a built dist/"
    assert '<script src="mic.js"></script>' in _src(dist / "mic.html")
