"""v1.260.0 — update checks survive a slow GitHub.

THE INCIDENT (2026-09-14). electron-updater's GitHub provider begins every check
by reading ``github.com/<repo>/releases.atom``. GitHub renders that page in 2–10 s
and cuts off at ~10 s, so about every other check on both of the user's PCs ended
in a 504 — and the updater's raw ``HttpError`` text (the whole "Unicorn" error
page, every response header, the ``_gh_sess`` cookie) was printed on the Updates
page, because ``friendlyUpdateError`` only knew the 404 publishing window.

THREE CHANGES, each pinned here:
  1. ``friendlyUpdateError`` renders a transient failure (5xx / 429 / timeout /
     network / a feed that is not a feed) as ONE sentence with nothing of the
     response in it; unknown errors are cut to their first line.
  2. ``scheduleUpdateRetry`` arms one earlier re-check (``UPDATE_RETRY_MS``) after
     a transient failure — one pending at a time, cleared by the next check.
  3. ``checkForUpdatesWithFallback`` reads the manifest on the ``updates`` branch
     (raw.githubusercontent.com, no feed involved) FIRST and the GitHub feed only
     if that fails, restoring the manifest for the next check either way; the
     release job writes that manifest through ``scripts/publish_update_manifest.py``.

House idiom (``test_desktop_reliability_v1249.py``): the shipped source is LIFTED
out of main.js verbatim and run under node against stubs, so reverting a change
turns these red. The stub updater EMITS "error" before it rejects, exactly as
electron-updater's ``checkForUpdates`` does — that ordering is what the fallback's
suppression flag exists for.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_MAIN = _ROOT / "desktop" / "main.js"
_RELEASE = _ROOT / ".github" / "workflows" / "release.yml"
_TESTS_WF = _ROOT / ".github" / "workflows" / "tests.yml"
_SCRIPT = _ROOT / "scripts" / "publish_update_manifest.py"

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def _src(path: Path = _MAIN) -> str:
    # CRLF on the CI runner, LF here (v1.232.1): normalise at the READER.
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _lift(start_marker: str, end_after: str) -> str:
    src = _src()
    start = src.index(start_marker)
    tail = src.index(end_after, start)
    end = src.index("\n}\n", tail) + 3
    return src[start:end]


def _decl(name: str) -> str:
    m = re.search(rf"^(?:const|let) {name} = [^\n]*;", _src(), re.M)
    assert m, f"main.js has no top-level decl {name}"
    return m.group(0)


def _run(script: str, tmp_path: Path, name: str, *argv: str) -> dict:
    f = tmp_path / f"{name}.js"
    f.write_text(script, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(f), *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        env={**os.environ},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


# The two shapes that reached the user's screen on 2026-09-14, cookie value
# replaced. The first is GitHub's short gateway page, the second its "Unicorn"
# page — 54 KB of inline-styled HTML that arrives with the SAME 504.
_REAL_504_SHORT = (
    '504 "method: GET url: https://github.com/RealDealCPA-VR/Iron-Jarvis/releases.atom\n\n'
    " Data:\n <html><body><h1>504 Gateway Time-out</h1>\nThe server didn't respond in time.\n"
    '</body></html>\n\n " Headers: { "cache-control": "no-cache", "content-length": "92", '
    '"content-type": "text/html", "x-github-request-id": "9492:2C50A:5E068A:7D2B16:6AA805FB", '
    '"set-cookie": [ "_gh_sess=REDACTED0123456789; path=/; HttpOnly; secure; SameSite=Lax" ] }'
)
_REAL_504_UNICORN = (
    '504 "method: GET url: https://github.com/RealDealCPA-VR/Iron-Jarvis/releases.atom\n\n'
    " Data:\n <!DOCTYPE html>\r\n<html>\r\n <head>\r\n <title>Unicorn! &middot; GitHub</title>\r\n"
    ' <style type="text/css" media="screen">\r\n body { background-color: #f1f1f1; }\r\n </style>\r\n'
    " </head>\r\n <body>\r\n <p><strong>We couldn't respond to your request in time.</strong></p>\r\n"
    ' </body>\r\n</html>\r\n\n " Headers: { "content-length": "54881", "content-type": "text/html; charset=utf-8", '
    '"set-cookie": [ "_gh_sess=REDACTEDabcdef; path=/; HttpOnly; secure; SameSite=Lax", '
    '"_octo=GH1.1.2128403571.1789396507; domain=.github.com; path=/; secure; SameSite=Lax" ] }'
)
_PUBLISHING_404 = (
    "Cannot find latest.yml in the latest release artifacts "
    "(https://github.com/RealDealCPA-VR/Iron-Jarvis/releases/download/v1.260.0/latest.yml): "
    "HttpError: 404 Not Found"
)
_LEAK_MARKERS = ("<html", "<!DOCTYPE", "set-cookie", "_gh_sess", "Headers:", "Data:", "x-github-request-id")


_HARNESS = """
const logs = [];
const states = [];
const timers = [];
let checks = 0;
function desktopLog(level, ...a) { logs.push(level + ": " + a.join(" ")); }
function _emitUpdateState(patch) { states.push(patch); }
function checkForUpdates() { checks += 1; }
// Timers are CAPTURED, never waited on: the retry is five real minutes.
const realSetTimeout = setTimeout;
setTimeout = (fn, ms) => { const t = { fn, ms, cleared: false, unref() { this.unrefd = true; } }; timers.push(t); return t; };
clearTimeout = (t) => { if (t) t.cleared = true; };
__DECLS__
__FUNCS__
const scenario = process.argv[2];
const cases = process.argv[3] ? JSON.parse(require("fs").readFileSync(process.argv[3], "utf8")) : null;
const emit = (o) => process.stdout.write(JSON.stringify(o) + "\\n");
const feedCalls = [];
function makeUpdater(plan) {
  // plan: { generic: "ok" | "fail", feed: "ok" | "fail", msg }
  return {
    setFeedURL(opts) { feedCalls.push(opts.provider); },
    async checkForUpdates() {
      const which = feedCalls[feedCalls.length - 1];
      const verdict = plan[which === "github" ? "feed" : "generic"];
      if (verdict === "ok") return { from: which };
      // electron-updater emits "error" and THEN rejects (AppUpdater.checkForUpdates).
      onUpdaterError(plan.msg);
      throw new Error(plan.msg);
    },
  };
}
(async () => {
  if (scenario === "friendly") {
    emit(cases.map((m) => ({ kind: updateErrorKind(m), text: friendlyUpdateError(m) })));
  } else if (scenario === "retry") {
    const armedFirst = scheduleUpdateRetry(cases[0]);
    const armedSecond = scheduleUpdateRetry(cases[0]);      // one pending at a time
    const armedOther = scheduleUpdateRetry("something else entirely");
    const pendingBeforeFire = timers.filter((t) => !t.cleared).length;
    timers[0].fn();                                          // the retry fires
    const checksAfterFire = checks;
    const armedAgain = scheduleUpdateRetry(cases[0]);        // the slot is free again
    clearUpdateRetry();
    emit({ armedFirst, armedSecond, armedOther, pendingBeforeFire, checksAfterFire, armedAgain,
           ms: timers[0].ms, unrefd: timers[0].unrefd === true, clearedAfterClear: timers[1].cleared,
           retryMs: UPDATE_RETRY_MS, recheckMs: UPDATE_RECHECK_MS });
  } else {
    const plan = cases;
    const au = makeUpdater(plan);
    let result = null, rejected = null;
    try { result = await checkForUpdatesWithFallback(au); } catch (e) { rejected = String(e.message).split("\\n")[0]; }
    emit({ feedCalls, result, rejected, states, armed: _updateFallbackArmed, source: _updateSource,
           timers: timers.filter((t) => !t.cleared).map((t) => t.ms), logs });
  }
})();
"""


def _harness() -> str:
    decls = "\n".join(
        _decl(n)
        for n in (
            "UPDATE_RECHECK_MS", "UPDATE_RETRY_MS", "UPDATE_MANIFEST_URL", "UPDATE_GITHUB_FEED",
            "_updateSource", "_updateRetryTimer", "_updateFallbackArmed",
        )
    )
    funcs = "\n".join(
        _lift(f"{prefix}function {n}", f"{prefix}function {n}")
        for prefix, n in (
            ("", "updateErrorKind"), ("", "_updateErrorStatus"), ("", "friendlyUpdateError"),
            ("", "scheduleUpdateRetry"), ("", "clearUpdateRetry"), ("", "onUpdaterError"),
            ("", "_setUpdateSource"), ("async ", "checkForUpdatesWithFallback"),
        )
    )
    assert "UPDATE_RETRY_MS" in funcs and "_updateFallbackArmed" in funcs
    return _HARNESS.replace("__DECLS__", decls).replace("__FUNCS__", funcs)


def _cases_file(tmp_path: Path, payload) -> str:
    f = tmp_path / "cases.json"
    f.write_text(json.dumps(payload), encoding="utf-8")
    return str(f)


# ==========================================================================
# 1. one plain sentence, nothing of the response
# ==========================================================================


@requires_node
def test_a_504_from_github_becomes_one_sentence_with_nothing_of_the_response(tmp_path):
    out = _run(_harness(), tmp_path, "friendly", "friendly",
               _cases_file(tmp_path, [_REAL_504_SHORT, _REAL_504_UNICORN]))
    for row in out:
        assert row["kind"] == "transient"
        text = row["text"]
        assert "GitHub" in text and "504" in text and "try again in 5 minutes" in text
        for marker in _LEAK_MARKERS:
            assert marker.lower() not in text.lower(), f"leaked {marker!r}: {text!r}"
        assert "\n" not in text and len(text) < 220, text


@requires_node
def test_network_and_not_a_feed_failures_are_transient_too(tmp_path):
    msgs = [
        "Error: connect ETIMEDOUT 140.82.112.3:443",
        "Error: read ECONNRESET",
        "Error: getaddrinfo ENOTFOUND github.com",
        "net::ERR_INTERNET_DISCONNECTED",
        "Cannot parse releases feed: Error: Unexpected token <,\nXML:\n<!DOCTYPE html>...",
        "HTTP error: 503 Service Unavailable",
        "429 Too Many Requests",
    ]
    out = _run(_harness(), tmp_path, "friendly2", "friendly", _cases_file(tmp_path, msgs))
    for msg, row in zip(msgs, out):
        assert row["kind"] == "transient", msg
        assert "try again" in row["text"] and "<" not in row["text"], row["text"]
    assert "(HTTP 503)" in out[5]["text"] and "(HTTP 429)" in out[6]["text"]
    assert "(HTTP" not in out[0]["text"], "a socket error carries no status to name"


@requires_node
def test_the_publishing_window_and_unknown_errors_keep_their_own_words(tmp_path):
    long_unknown = "Something odd happened while checking\n Data:\n <html>lots</html> Headers: {x}"
    very_long = "E" * 400
    out = _run(_harness(), tmp_path, "friendly3", "friendly",
               _cases_file(tmp_path, [_PUBLISHING_404, long_unknown, very_long, ""]))
    assert out[0]["kind"] == "publishing" and "still uploading" in out[0]["text"]
    assert out[1]["kind"] == "other"
    assert out[1]["text"] == "Something odd happened while checking", out[1]["text"]
    assert out[2]["kind"] == "other" and len(out[2]["text"]) <= 200 and out[2]["text"].endswith("…")
    assert out[3]["text"] == "update failed"


# ==========================================================================
# 2. one earlier retry after a transient failure
# ==========================================================================


@requires_node
def test_a_transient_failure_arms_one_earlier_retry_never_a_storm(tmp_path):
    out = _run(_harness(), tmp_path, "retry", "retry", _cases_file(tmp_path, [_REAL_504_SHORT]))
    assert out["armedFirst"] is True
    assert out["armedSecond"] is False, "a second transient error must not stack a second timer"
    assert out["armedOther"] is False, "a non-transient error earns no early retry"
    assert out["pendingBeforeFire"] == 1
    assert out["checksAfterFire"] == 1, "the retry runs exactly one check"
    assert out["armedAgain"] is True, "after it fires the slot is free again"
    assert out["clearedAfterClear"] is True, "clearUpdateRetry cancels the pending timer"
    assert out["ms"] == out["retryMs"] and out["unrefd"] is True
    assert 60_000 <= out["retryMs"] < out["recheckMs"], "sooner than the half-hour cadence, never a tight loop"


# ==========================================================================
# 3. manifest first, feed as fallback, manifest restored
# ==========================================================================


@requires_node
def test_a_healthy_manifest_never_touches_the_feed(tmp_path):
    out = _run(_harness(), tmp_path, "fb1", "fallback",
               _cases_file(tmp_path, {"generic": "ok", "feed": "ok", "msg": _REAL_504_SHORT}))
    assert out["feedCalls"] == ["generic"]
    assert out["result"] == {"from": "generic"} and out["rejected"] is None
    assert out["states"] == [] and out["armed"] is False and out["source"] == "manifest"


@requires_node
def test_a_failed_manifest_falls_back_to_the_feed_silently_and_restores_the_manifest(tmp_path):
    out = _run(_harness(), tmp_path, "fb2", "fallback",
               _cases_file(tmp_path, {"generic": "fail", "feed": "ok", "msg": "Error: getaddrinfo ENOTFOUND raw.githubusercontent.com"}))
    assert out["feedCalls"] == ["generic", "github", "generic"], out["feedCalls"]
    assert out["result"] == {"from": "github"} and out["rejected"] is None
    # The manifest's "error" event was emitted BEFORE the fallback ran — and the
    # user never saw it: the feed attempt is the outcome that counts.
    assert out["states"] == [], out["states"]
    assert out["timers"] == [], "no retry for a failure the fallback absorbed"
    assert out["armed"] is False and out["source"] == "manifest"
    assert any("trying the GitHub releases feed" in line for line in out["logs"])


@requires_node
def test_when_both_sources_fail_the_user_sees_one_sentence_and_one_retry(tmp_path):
    out = _run(_harness(), tmp_path, "fb3", "fallback",
               _cases_file(tmp_path, {"generic": "fail", "feed": "fail", "msg": _REAL_504_UNICORN}))
    assert out["feedCalls"] == ["generic", "github", "generic"]
    assert out["rejected"] is not None, "the caller still learns the check failed"
    errors = [s for s in out["states"] if s.get("status") == "error"]
    assert len(errors) == 1, out["states"]
    assert "504" in errors[0]["error"] and "_gh_sess" not in errors[0]["error"]
    assert len(out["timers"]) == 1, "exactly one early retry armed"
    assert out["armed"] is False and out["source"] == "manifest"


# ==========================================================================
# 4. the wiring is the shipped one (source pins)
# ==========================================================================


def test_every_check_goes_through_the_fallback_and_the_manifest_is_the_first_source():
    src = _src()
    init = src[src.index("function initUpdater()"):src.index("function checkForUpdates()")]
    assert 'autoUpdater.on("error", (err) => onUpdaterError(' in init
    assert '_setUpdateSource(autoUpdater, "manifest")' in init
    timer_site = src[src.index("function checkForUpdates()"):src.index("function checkForUpdates()") + 600]
    assert "checkForUpdatesWithFallback(autoUpdater)" in timer_site
    button_site = src[src.index('ipcMain.handle("update:check"'):src.index('ipcMain.handle("update:apply"')]
    assert "await checkForUpdatesWithFallback(au)" in button_site
    # No call site may reach the updater directly any more.
    direct = re.findall(r"\b(?:au|autoUpdater)\.checkForUpdates\(\)", src)
    inside = _lift("async function checkForUpdatesWithFallback", "async function checkForUpdatesWithFallback")
    assert len(direct) == len(re.findall(r"\bau\.checkForUpdates\(\)", inside)) == 2, direct


def test_the_manifest_url_and_the_feed_name_the_same_repo_the_installer_is_published_to():
    pkg = json.loads(_src(_ROOT / "desktop" / "package.json"))
    publish = pkg["build"]["publish"][0]
    assert publish["provider"] == "github"
    owner, repo = publish["owner"], publish["repo"]
    assert _decl("UPDATE_MANIFEST_URL") == (
        f'const UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/{owner}/{repo}/updates";'
    )
    feed = _decl("UPDATE_GITHUB_FEED")
    assert f'owner: "{owner}"' in feed and f'repo: "{repo}"' in feed and 'provider: "github"' in feed


def test_the_release_job_publishes_the_manifest_after_the_release_is_public_and_nothing_triggers_on_it():
    wf = _src(_RELEASE)
    public = wf.index('--draft=false --latest')
    step = wf.index("Publish the update manifest to the updates branch")
    assert step > public, "the manifest must point at a PUBLIC release's assets"
    body = wf[step:]
    assert "gh release download" in body and "--pattern latest.yml" in body
    assert "scripts/publish_update_manifest.py" in body and '--repo "$GITHUB_REPOSITORY" --version "$ver"' in body
    assert "push origin HEAD:updates" in body
    assert "checkout --orphan updates" in body, "the branch is an orphan: one file, no history"
    for path in (_RELEASE, _TESTS_WF):
        on = re.search(r"^on:\n(?:  .*\n)+", _src(path), re.M).group(0)
        assert re.search(r"branches: \[master\]", on), f"{path.name} must run on master only, or the updates push loops"


# ==========================================================================
# 5. the manifest rewriter
# ==========================================================================

_SAMPLE = (
    "version: 1.260.0\n"
    "files:\n"
    "  - url: Iron-Jarvis-Setup-1.260.0.exe\n"
    "    sha512: 7GZ6x0mK3Qh2b7Mu0kq5T8zPq4QG8n7yj0v2p3LZ9x1A==\n"
    "    size: 269477572\n"
    "path: Iron-Jarvis-Setup-1.260.0.exe\n"
    "sha512: 7GZ6x0mK3Qh2b7Mu0kq5T8zPq4QG8n7yj0v2p3LZ9x1A==\n"
    "releaseDate: '2026-09-14T16:00:00.000Z'\n"
)


def _rewriter():
    sys.path.insert(0, str(_ROOT / "scripts"))
    try:
        import publish_update_manifest as mod  # noqa: WPS433
    finally:
        sys.path.pop(0)
    return mod


def test_the_rewriter_makes_every_installer_url_absolute_and_touches_nothing_else():
    mod = _rewriter()
    out = mod.rewrite(_SAMPLE, "RealDealCPA-VR/Iron-Jarvis", "1.260.0")
    base = "https://github.com/RealDealCPA-VR/Iron-Jarvis/releases/download/v1.260.0"
    assert f"  - url: {base}/Iron-Jarvis-Setup-1.260.0.exe\n" in out
    assert f"path: {base}/Iron-Jarvis-Setup-1.260.0.exe\n" in out
    kept = [l for l in _SAMPLE.splitlines() if not l.lstrip().startswith(("- url:", "path:"))]
    for line in kept:
        assert line + "\n" in out, f"changed a line it must not: {line!r}"
    assert mod.rewrite(out, "RealDealCPA-VR/Iron-Jarvis", "1.260.0") == out, "idempotent"


def test_the_rewriter_refuses_what_must_never_reach_the_branch():
    mod = _rewriter()
    with pytest.raises(ValueError, match="not the 1.261.0"):
        mod.rewrite(_SAMPLE, "RealDealCPA-VR/Iron-Jarvis", "1.261.0")
    with pytest.raises(ValueError, match="no sha512"):
        mod.rewrite(_SAMPLE.replace("sha512", "sha1"), "RealDealCPA-VR/Iron-Jarvis", "1.260.0")
    with pytest.raises(ValueError, match="no version"):
        mod.rewrite(_SAMPLE.replace("version: 1.260.0\n", ""), "RealDealCPA-VR/Iron-Jarvis", "1.260.0")
    with pytest.raises(ValueError, match="OWNER/NAME"):
        mod.rewrite(_SAMPLE, "not a repo", "1.260.0")


_UPDATER_PKG = _ROOT / "desktop" / "node_modules" / "electron-updater"
requires_updater = pytest.mark.skipif(
    not (_UPDATER_PKG / "out" / "providers" / "GenericProvider.js").exists(),
    reason="desktop/node_modules/electron-updater not installed",
)

_PROVIDER_PROBE = """
const { GenericProvider } = require(process.argv[2] + "/out/providers/GenericProvider");
const manifest = require("fs").readFileSync(process.argv[3], "utf8");
const asked = [];
const executor = { request: async (opts) => { asked.push(`${opts.protocol}//${opts.hostname}${opts.path}`); return manifest; } };
const updater = { channel: null, isAddNoCacheQuery: false, allowPrerelease: false, requestHeaders: null, httpExecutor: executor };
const p = new GenericProvider({ provider: "generic", url: process.argv[4] }, updater, { executor, isUseMultipleRangeRequest: false, platform: "win32" });
p.getLatestVersion().then((info) => {
  const files = p.resolveFiles(info);
  process.stdout.write(JSON.stringify({ asked, version: info.version, files: files.map((f) => f.url.href), sha512: files[0].info.sha512 }) + "\\n");
});
"""


@requires_node
@requires_updater
def test_the_real_generic_provider_reads_the_rewritten_manifest_from_the_updates_branch(tmp_path):
    """The JS consumer and the Python producer, joined: electron-updater's OWN
    GenericProvider (from desktop/node_modules, no Electron needed) is pointed at
    UPDATE_MANIFEST_URL exactly as main.js sets it and handed the rewriter's
    output. It must ask for `<url>/latest.yml` — a base without a trailing slash
    would otherwise resolve to `.../Iron-Jarvis/latest.yml`, one segment short —
    and hand back the ABSOLUTE installer URL untouched, checksum intact."""
    mod = _rewriter()
    manifest = tmp_path / "latest.yml"
    manifest.write_text(mod.rewrite(_SAMPLE, "RealDealCPA-VR/Iron-Jarvis", "1.260.0"), encoding="utf-8")
    url = re.search(r'"(https://[^"]+)"', _decl("UPDATE_MANIFEST_URL")).group(1)
    out = _run(_PROVIDER_PROBE, tmp_path, "provider", str(_UPDATER_PKG).replace("\\", "/"), str(manifest), url)
    assert out["asked"] == [f"{url}/latest.yml"], out["asked"]
    assert out["version"] == "1.260.0"
    assert out["files"] == ["https://github.com/RealDealCPA-VR/Iron-Jarvis/releases/download/v1.260.0/Iron-Jarvis-Setup-1.260.0.exe"]
    assert out["sha512"] == "7GZ6x0mK3Qh2b7Mu0kq5T8zPq4QG8n7yj0v2p3LZ9x1A=="


def test_the_rewriter_cli_writes_the_file_and_fails_loudly_on_a_wrong_version(tmp_path):
    src = tmp_path / "in" / "latest.yml"
    src.parent.mkdir()
    src.write_text(_SAMPLE, encoding="utf-8")
    dst = tmp_path / "out" / "latest.yml"
    ok = subprocess.run(
        [sys.executable, str(_SCRIPT), str(src), str(dst), "--repo", "RealDealCPA-VR/Iron-Jarvis", "--version", "1.260.0"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert ok.returncode == 0, ok.stderr
    assert "releases/download/v1.260.0/Iron-Jarvis-Setup-1.260.0.exe" in dst.read_text(encoding="utf-8")
    assert b"\r\n" not in dst.read_bytes(), "LF on every machine, like the file electron-builder writes"
    bad = subprocess.run(
        [sys.executable, str(_SCRIPT), str(src), str(tmp_path / "bad.yml"), "--repo", "RealDealCPA-VR/Iron-Jarvis", "--version", "9.9.9"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert bad.returncode == 1 and "not the 9.9.9" in bad.stderr
    assert not (tmp_path / "bad.yml").exists()
