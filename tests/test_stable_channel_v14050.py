"""v1.405.0 — a STABLE update channel for the daily-driver PC.

The user's words: "55 releases in 7 days (7 on Oct 28). v1.400.9 broke the
dashboard type check, and v1.401.0's installer failed until v1.401.1. The
first start after an update hung on Oct 27. Build: a Stable update channel for
the daily-driver PC that gets a release only after it has run a day on a Fresh
channel on the other PC."

What is pinned here:

1. THE RULE (``scripts/promote_stable.choose_stable``, pure): the newest
   release that is published, out for a day, not held, has its three files,
   whose commit's Tests run ended ``success``, and is newer than Stable now.
   ``--promote`` skips the wait only.
2. THE BRANCH'S FILES (``run``): latest.yml through the Release job's OWN
   rewrite (imported, never copied), holds.txt, promoted.json, README.md.
3. THE WORKFLOW (``.github/workflows/stable.yml``): hourly + dispatch with
   hold / unhold / promote, one run at a time, bounded, writes only
   updates-stable; neither existing workflow triggers on that branch.
4. THE DESKTOP (``desktop/main.js``, lifted and run under node): the channel
   read from config.toml at every check; Stable reads ITS manifest and never
   the releases feed; Fresh keeps the old order; ``update:channels``.
5. THE SETTING (``update_channel``): schema row, Config field, tier per value,
   and the file the daemon writes is the file the shell reads.
6. THE HOLD FROM THE APP (``core/update_channel``): only with a signed-in
   ``gh``, two presses, the version validated — the real ``gh`` never runs.

House idiom (``test_update_channel_v1260.py``): shipped JS is LIFTED out of
main.js verbatim and run under node against stubs.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[1]
_MAIN = _ROOT / "desktop" / "main.js"
_PRELOAD = _ROOT / "desktop" / "preload.js"
_WF = _ROOT / ".github" / "workflows"
_SCRIPTS = _ROOT / "scripts"

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")

REPO = "RealDealCPA-VR/Iron-Jarvis"
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def _txt(path: Path) -> str:
    # CRLF on the CI runner, LF here (v1.232.1): normalise at the READER.
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _mod():
    sys.path.insert(0, str(_SCRIPTS))
    try:
        import promote_stable as mod  # noqa: WPS433
    finally:
        sys.path.pop(0)
    return mod


def _rel(version, hours_ago=48.0, *, draft=False, prerelease=False, assets=None, unpublished=False):
    mod = _mod()
    names = mod.required_assets(version) if assets is None else assets
    return mod.Release(
        version=version,
        tag=f"v{version}",
        draft=draft,
        prerelease=prerelease,
        published_at=None if unpublished else NOW - timedelta(hours=hours_ago),
        assets=frozenset(names),
    )


# ==========================================================================
# 1. the rule
# ==========================================================================


def test_the_newest_release_out_for_a_day_with_green_tests_wins():
    mod = _mod()
    rels = [_rel("1.404.0", 30), _rel("1.403.0", 50), _rel("1.405.0", 5)]
    tests = {"1.404.0": "success", "1.403.0": "success", "1.405.0": "success"}
    d = mod.choose_stable(rels, [], tests, NOW, "1.402.0")
    assert d.version == "1.404.0", d
    assert "out for 30 h" in d.why and "Tests passed" in d.why
    # The younger one is named with how long it has been out, in words.
    assert any("1.405.0" in n and "out for 5 h of the 24 h" in n for n in d.notes), d.notes


_REASONS = [
    ("held", lambda: _rel("1.404.0", 30), "success", "you held it back"),
    ("too young", lambda: _rel("1.404.0", 23.9), "success", "out for 23 h of the 24 h"),
    ("tests red", lambda: _rel("1.404.0", 30), "failure", "Tests run ended “failure”"),
    ("tests missing", lambda: _rel("1.404.0", 30), "missing", "no Tests run was found"),
    ("tests running", lambda: _rel("1.404.0", 30), "running", "has not finished"),
    ("tests cancelled", lambda: _rel("1.404.0", 30), "cancelled", "Tests run ended “cancelled”"),
    ("draft", lambda: _rel("1.404.0", 30, draft=True, unpublished=True), "success", "not published yet"),
    # A release turned back into a draft keeps its old publish date.
    ("draft with a date", lambda: _rel("1.404.0", 30, draft=True), "success", "not published yet"),
    ("prerelease", lambda: _rel("1.404.0", 30, prerelease=True), "success", "prerelease"),
    ("no installer", lambda: _rel("1.404.0", 30, assets=["latest.yml", "Iron-Jarvis-Setup-1.404.0.exe.blockmap"]),
     "success", "missing Iron-Jarvis-Setup-1.404.0.exe"),
    ("no manifest", lambda: _rel("1.404.0", 30, assets=["Iron-Jarvis-Setup-1.404.0.exe",
                                                        "Iron-Jarvis-Setup-1.404.0.exe.blockmap"]),
     "success", "missing latest.yml"),
    ("no blockmap", lambda: _rel("1.404.0", 30, assets=["latest.yml", "Iron-Jarvis-Setup-1.404.0.exe"]),
     "success", "missing Iron-Jarvis-Setup-1.404.0.exe.blockmap"),
]


@pytest.mark.parametrize("case, make, tests, words", _REASONS, ids=[r[0] for r in _REASONS])
def test_each_reason_keeps_a_release_off_stable_and_says_why(case, make, tests, words):
    mod = _mod()
    rel = make()
    holds = ["1.404.0"] if case == "held" else []
    tmap = {} if tests is None else {"1.404.0": tests}
    d = mod.choose_stable([rel, _rel("1.403.0", 72)], holds, tmap, NOW, "1.403.0")
    assert d.version is None, (case, d)
    assert "Nothing qualifies, so Stable stays on v1.403.0." == d.why
    assert any("1.404.0" in n and words in n for n in d.notes), (case, d.notes)


def test_an_older_qualifying_release_is_taken_when_the_newer_one_does_not_qualify():
    mod = _mod()
    rels = [_rel("1.404.0", 30), _rel("1.403.0", 40)]
    d = mod.choose_stable(rels, [], {"1.404.0": "failure", "1.403.0": "success"}, NOW, "1.402.0")
    assert d.version == "1.403.0"


def test_a_release_replaced_by_a_newer_patch_waits_for_that_one():
    """The reviewer's probe: 1.388.0 out 24.4 h, its fix 1.388.1 out 19 h.
    The fix replaced it, so nothing moves until the fix has been out a day."""
    mod = _mod()
    rels = [_rel("1.388.1", 19), _rel("1.388.0", 24.4), _rel("1.387.0", 60)]
    green = {v: "success" for v in ("1.388.1", "1.388.0", "1.387.0")}
    d = mod.choose_stable(rels, [], green, NOW, "1.386.0")
    assert d.version != "1.388.0", d
    assert any("v1.388.0: replaced by v1.388.1 — waiting for that one." == n for n in d.notes), d.notes
    # 1.387.0 is another line (a newer MINOR does not replace it) and qualifies.
    assert d.version == "1.387.0"
    # A day later the fix itself goes on.
    later = mod.choose_stable(rels, [], green, NOW + timedelta(hours=6), "1.387.0")
    assert later.version == "1.388.1", later
    # A newer patch that is only a DRAFT replaces nothing.
    drafted = [_rel("1.388.1", 19, draft=True, unpublished=True), _rel("1.388.0", 30)]
    assert mod.choose_stable(drafted, [], green, NOW, "1.387.0").version == "1.388.0"
    # The newest replacement is the one named.
    three = [_rel("1.388.2", 1), _rel("1.388.1", 19), _rel("1.388.0", 30)]
    d = mod.choose_stable(three, [], green, NOW, "1.387.0")
    assert any("v1.388.0: replaced by v1.388.2" in n for n in d.notes), d.notes
    # And no Tests read is asked for a replaced release.
    assert mod.tests_wanted(rels, [], NOW, "1.386.0") == ["1.387.0"]


def test_promote_may_name_a_replaced_release_and_says_so():
    mod = _mod()
    rels = [_rel("1.388.1", 19), _rel("1.388.0", 30)]
    d = mod.choose_stable(rels, [], {"1.388.0": "success"}, NOW, "1.387.0", promote="1.388.0")
    assert d.version == "1.388.0" and "v1.388.1 had replaced it" in d.why, d


def test_an_unread_verdict_on_the_newest_candidate_promotes_nothing_older():
    mod = _mod()
    rels = [_rel("1.405.0", 30), _rel("1.404.0", 40)]
    d = mod.choose_stable(rels, [], {"1.404.0": "success"}, NOW, "1.403.0")
    assert d.version is None and d.unread == "1.405.0", d
    assert d.why.startswith("Nothing changed: the Tests result of v1.405.0 could not be read")
    # A verdict that WAS read and is not green still lets an older one through.
    d = mod.choose_stable(rels, [], {"1.405.0": "failure", "1.404.0": "success"}, NOW, "1.403.0")
    assert d.version == "1.404.0" and not d.unread


def test_stable_never_moves_backwards_or_sideways():
    mod = _mod()
    rels = [_rel("1.403.0", 30), _rel("1.402.0", 60)]
    tests = {"1.403.0": "success", "1.402.0": "success"}
    d = mod.choose_stable(rels, [], tests, NOW, "1.404.0")
    assert d.version is None and "stays on v1.404.0" in d.why
    d = mod.choose_stable(rels, [], tests, NOW, "1.403.0")
    assert d.version is None, "the same version is no change"
    # Numeric, never string order: 1.10.0 is newer than 1.9.0.
    d = mod.choose_stable([_rel("1.10.0", 30)], [], {"1.10.0": "success"}, NOW, "1.9.0")
    assert d.version == "1.10.0"


def test_a_held_version_already_on_stable_stays_and_the_log_says_so():
    mod = _mod()
    d = mod.choose_stable([_rel("1.404.0", 30)], ["1.404.0"], {"1.404.0": "success"}, NOW, "1.404.0")
    assert d.version is None
    assert any("held back" in n and "never moves to an older version" in n for n in d.notes)


def test_the_first_run_promotes_the_newest_qualifying_release():
    mod = _mod()
    d = mod.choose_stable([_rel("1.404.0", 30)], [], {"1.404.0": "success"}, NOW, None)
    assert d.version == "1.404.0"


def test_promote_skips_the_wait_and_nothing_else():
    mod = _mod()
    young = _rel("1.405.0", 2)
    green = {"1.405.0": "success"}
    d = mod.choose_stable([young], [], green, NOW, "1.404.0", promote="1.405.0")
    assert d.version == "1.405.0" and not d.refused and "skipping the day's wait" in d.why

    refusals = [
        ("held", [young], ["1.405.0"], green, "1.404.0", "you held it back. Unhold it first."),
        ("tests red", [young], [], {"1.405.0": "failure"}, "1.404.0", "Tests run ended “failure”"),
        ("tests running", [young], [], {"1.405.0": "running"}, "1.404.0", "has not finished"),
        ("draft", [_rel("1.405.0", 2, draft=True, unpublished=True)], [], green, "1.404.0", "not published"),
        ("unknown", [young], [], green, "1.404.0", "There is no release v1.406.0"),
        ("older", [young, _rel("1.403.0", 90)], [], {"1.403.0": "success"}, "1.404.0", "never moves to an older"),
    ]
    for case, rels, holds, tests, current, words in refusals:
        want = {"unknown": "1.406.0", "older": "1.403.0"}.get(case, "1.405.0")
        d = mod.choose_stable(rels, holds, tests, NOW, current, promote=want)
        assert d.version is None and words in d.refused, (case, d)
        assert d.why.startswith("Nothing changed: "), (case, d.why)

    same = mod.choose_stable([young], [], green, NOW, "1.405.0", promote="1.405.0")
    assert same.version is None and not same.refused and "already on Stable" in same.why


def test_tests_verdict_reads_the_newest_completed_run():
    mod = _mod()
    v = mod.tests_verdict
    assert v([]) == "missing"
    assert v([{"status": "in_progress", "created_at": "2026-10-29T01:00:00Z"}]) == "running"
    runs = [
        {"status": "completed", "conclusion": "failure", "created_at": "2026-10-29T01:00:00Z", "run_number": 1},
        {"status": "completed", "conclusion": "success", "created_at": "2026-10-29T02:00:00Z", "run_number": 2},
        {"status": "in_progress", "created_at": "2026-10-29T03:00:00Z", "run_number": 3},
    ]
    assert v(runs) == "success", "the newest COMPLETED run decides; one still going does not"
    runs.append({"status": "completed", "conclusion": "failure", "created_at": "2026-10-29T04:00:00Z", "run_number": 4})
    assert v(runs) == "failure", "a later red run overrules an earlier green one"
    # A cancelled or skipped run is no verdict: the green run before it stands.
    green_then_cancelled = [
        {"status": "completed", "conclusion": "success", "created_at": "2026-10-29T01:00:00Z"},
        {"status": "completed", "conclusion": "cancelled", "created_at": "2026-10-29T02:00:00Z"},
        {"status": "completed", "conclusion": "skipped", "created_at": "2026-10-29T03:00:00Z"},
    ]
    assert v(green_then_cancelled) == "success"
    only_cancelled = [{"status": "completed", "conclusion": "cancelled", "created_at": "2026-10-29T01:00:00Z"}]
    assert v(only_cancelled) == "cancelled", "never green"
    assert v(only_cancelled + [{"status": "queued", "created_at": "2026-10-29T02:00:00Z"}]) == "running"


def test_the_api_shape_is_read_honestly():
    mod = _mod()
    raw = {
        "tag_name": "v1.404.0", "draft": False, "prerelease": False,
        "published_at": "2026-10-29T01:18:43Z",
        "assets": [
            {"name": "Iron-Jarvis-Setup-1.404.0.exe", "state": "uploaded"},
            {"name": "Iron-Jarvis-Setup-1.404.0.exe.blockmap", "state": "starter"},
            {"name": "latest.yml", "state": "uploaded"},
        ],
    }
    r = mod.release_from_api(raw)
    assert r.version == "1.404.0" and r.published_at == datetime(2026, 10, 29, 1, 18, 43, tzinfo=timezone.utc)
    assert "Iron-Jarvis-Setup-1.404.0.exe.blockmap" not in r.assets, "an upload still going is not an asset yet"
    assert mod.release_from_api({"tag_name": "nightly"}) is None


def test_tests_are_read_only_for_releases_the_decision_needs():
    mod = _mod()
    rels = [_rel("1.406.0", 2), _rel("1.405.0", 30), _rel("1.404.0", 40, draft=True, unpublished=True),
            _rel("1.403.0", 60), _rel("1.402.0", 80)]
    want = mod.tests_wanted(rels, ["1.403.0"], NOW, "1.402.0")
    assert want == ["1.405.0"], want
    assert mod.tests_wanted(rels, [], NOW, "1.402.0", promote="1.406.0")[0] == "1.406.0"


def test_hold_edits_are_validated_and_said():
    mod = _mod()
    holds, said = mod.apply_hold_edits(["1.400.0"], hold="v1.401.0", unhold="1.400.0")
    assert holds == ["1.401.0"] and len(said) == 2
    with pytest.raises(ValueError):
        mod.apply_hold_edits([], hold="1.401")
    with pytest.raises(ValueError):
        mod.apply_hold_edits([], hold="1.401.0; rm -rf /")
    with pytest.raises(ValueError):
        mod.apply_hold_edits([], hold="1.401.0", unhold="1.401.0")
    assert mod.read_holds("1.400.0\n\nnot a version\nv1.401.0 # bad build\n1.400.0\n") == ["1.400.0", "1.401.0"]


# ==========================================================================
# 2. the branch's files, end to end with a fake GitHub
# ==========================================================================

_MANIFEST = (
    "version: {v}\n"
    "files:\n"
    "  - url: Iron-Jarvis-Setup-{v}.exe\n"
    "    sha512: 7GZ6x0mK3Qh2b7Mu0kq5T8zPq4QG8n7yj0v2p3LZ9x1A==\n"
    "    size: 279526995\n"
    "path: Iron-Jarvis-Setup-{v}.exe\n"
    "sha512: 7GZ6x0mK3Qh2b7Mu0kq5T8zPq4QG8n7yj0v2p3LZ9x1A==\n"
    "releaseDate: '2026-10-28T01:18:43.000Z'\n"
)


class FakeGitHub:
    def __init__(self, releases, tests, manifests=None):
        self._releases = releases
        self._tests = tests
        self._manifests = manifests or {}
        self.calls: list[tuple[str, str]] = []

    def releases(self):
        self.calls.append(("releases", ""))
        return self._releases

    def tag_commit(self, tag):
        self.calls.append(("commit", tag))
        return f"sha-{tag}"

    def tests_runs(self, sha):
        self.calls.append(("tests", sha))
        verdict = self._tests.get(sha.removeprefix("sha-v"))
        if verdict is None:
            return []
        return [{"status": "completed", "conclusion": verdict, "created_at": "2026-10-28T00:00:00Z"}]

    def manifest(self, tag):
        self.calls.append(("manifest", tag))
        v = tag.lstrip("v")
        return self._manifests.get(v, _MANIFEST.format(v=v))


def _api(version, hours_ago, **kw):
    published = (NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")
    return {
        "tag_name": f"v{version}", "draft": kw.get("draft", False), "prerelease": False,
        "published_at": None if kw.get("draft") else published,
        "assets": [{"name": n, "state": "uploaded"} for n in _mod().required_assets(version)],
    }


def test_a_pass_writes_the_manifest_through_the_release_jobs_own_rewrite(tmp_path):
    mod = _mod()
    gh = FakeGitHub([_api("1.405.0", 3), _api("1.404.0", 30), _api("1.403.0", 60)],
                    {"1.404.0": "success", "1.403.0": "success"})
    logs: list[str] = []
    code, message = mod.run(tmp_path, REPO, gh, now=NOW, log=logs.append)
    assert code == 0 and message == "stable: v1.404.0"
    latest = (tmp_path / "latest.yml").read_bytes()
    assert b"\r\n" not in latest
    base = f"https://github.com/{REPO}/releases/download/v1.404.0"
    assert f"  - url: {base}/Iron-Jarvis-Setup-1.404.0.exe\n".encode() in latest
    assert f"path: {base}/Iron-Jarvis-Setup-1.404.0.exe\n".encode() in latest
    assert latest.decode() == mod.rewrite(_MANIFEST.format(v="1.404.0"), REPO, "1.404.0")
    history = json.loads((tmp_path / "promoted.json").read_text(encoding="utf-8"))
    assert history[-1]["version"] == "1.404.0" and history[-1]["published_at"].endswith("Z")
    assert set(history[-1]) == {"version", "promoted_at", "published_at", "why"}
    assert (tmp_path / "holds.txt").read_text(encoding="utf-8") == ""
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "updates-stable" in readme and "time only" in readme
    # Only the newest qualifying one's Tests were read (the older needed none).
    assert ("tests", "sha-v1.403.0") not in gh.calls and ("tests", "sha-v1.405.0") not in gh.calls

    # A second pass with nothing new changes NOTHING on disk.
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    code, message = mod.run(tmp_path, REPO, gh, now=NOW + timedelta(hours=1), log=logs.append)
    assert code == 0 and message == "stable: no change"
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_the_script_reuses_the_rewrite_and_imports_only_the_stdlib():
    mod = _mod()
    sys.path.insert(0, str(_SCRIPTS))
    try:
        import publish_update_manifest as pum  # noqa: WPS433
    finally:
        sys.path.pop(0)
    assert mod.rewrite is pum.rewrite, "the Release job's rewrite, imported — never a copy"
    tree = ast.parse(_txt(_SCRIPTS / "promote_stable.py"))
    assert not any(isinstance(n, ast.FunctionDef) and n.name == "rewrite" for n in tree.body)
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert roots - set(sys.stdlib_module_names) == {"publish_update_manifest"}, roots


def test_a_manifest_naming_a_file_the_release_lacks_never_reaches_stable(tmp_path):
    mod = _mod()
    bad = _MANIFEST.format(v="1.404.0").replace("Iron-Jarvis-Setup-1.404.0.exe", "Iron-Jarvis-Setup-9.exe")
    gh = FakeGitHub([_api("1.404.0", 30)], {"1.404.0": "success"}, {"1.404.0": bad})
    logs: list[str] = []
    code, message = mod.run(tmp_path, REPO, gh, now=NOW, hold="1.300.0", log=logs.append)
    assert code == mod.EXIT_MANIFEST_REFUSED == 4, "the run ends red so the failure shows"
    assert not (tmp_path / "latest.yml").exists() and not (tmp_path / "promoted.json").exists()
    assert (tmp_path / "holds.txt").read_text(encoding="utf-8") == "1.300.0\n", "the hold still lands"
    assert any("could not be used" in line for line in logs), logs


def test_a_hold_lands_and_keeps_its_version_off(tmp_path):
    mod = _mod()
    gh = FakeGitHub([_api("1.404.0", 30), _api("1.403.0", 60)], {"1.404.0": "success", "1.403.0": "success"})
    code, message = mod.run(tmp_path, REPO, gh, now=NOW, hold="1.404.0", log=lambda *_: None)
    assert code == 0
    assert (tmp_path / "holds.txt").read_text(encoding="utf-8") == "1.404.0\n"
    assert mod.manifest_version((tmp_path / "latest.yml").read_text(encoding="utf-8")) == "1.403.0"
    assert message.startswith("stable: v1.403.0 (") and "held back" in message
    code, message = mod.run(tmp_path, REPO, gh, now=NOW, unhold="1.404.0", log=lambda *_: None)
    assert code == 0 and (tmp_path / "holds.txt").read_text(encoding="utf-8") == ""
    assert mod.manifest_version((tmp_path / "latest.yml").read_text(encoding="utf-8")) == "1.404.0"


def test_a_refused_promote_exits_3_and_still_writes_the_hold(tmp_path):
    mod = _mod()
    gh = FakeGitHub([_api("1.405.0", 2), _api("1.404.0", 30)], {"1.405.0": "failure", "1.404.0": "success"})
    logs: list[str] = []
    code, message = mod.run(tmp_path, REPO, gh, now=NOW, promote="1.405.0", hold="1.403.0", log=logs.append)
    assert code == mod.EXIT_PROMOTE_REFUSED == 3
    assert not (tmp_path / "latest.yml").exists(), "a refused promote moves nothing"
    assert (tmp_path / "holds.txt").read_text(encoding="utf-8") == "1.403.0\n"
    assert any("Tests run ended “failure”" in line for line in logs)
    # The young, green one goes through when promoted by hand.
    gh2 = FakeGitHub([_api("1.405.0", 2)], {"1.405.0": "success"})
    code, message = mod.run(tmp_path, REPO, gh2, now=NOW, promote="1.405.0", log=lambda *_: None)
    assert code == 0 and message == "stable: v1.405.0"
    assert "skipping the day's wait" in json.loads((tmp_path / "promoted.json").read_text())[-1]["why"]


def test_bad_inputs_write_nothing(tmp_path):
    mod = _mod()
    gh = FakeGitHub([_api("1.404.0", 30)], {"1.404.0": "success"})
    for kw in ({"hold": "1.4"}, {"unhold": "latest"}, {"promote": "$(whoami)"}):
        code, message = mod.run(tmp_path, REPO, gh, now=NOW, log=lambda *_: None, **kw)
        assert code == 2 and message == "", kw
    assert list(tmp_path.iterdir()) == []
    assert gh.calls == [], "nothing was even read"


def test_a_tests_read_that_fails_is_never_green(tmp_path):
    mod = _mod()

    class Broken(FakeGitHub):
        def tests_runs(self, sha):
            raise RuntimeError("gh api failed: HTTP 403: Resource not accessible by integration")

    logs: list[str] = []
    code, _ = mod.run(tmp_path, REPO, Broken([_api("1.404.0", 30)], {}), now=NOW, log=logs.append)
    assert code == mod.EXIT_TESTS_UNREAD == 5 and not (tmp_path / "latest.yml").exists()
    assert any("could not read its Tests result" in line for line in logs)


def test_an_unread_newest_candidate_stops_the_run_before_anything_older(tmp_path):
    """Else a Stable PC downloads the older one now and the newer one an hour
    later. The older release's Tests are not even read."""
    mod = _mod()

    class FlakyNewest(FakeGitHub):
        def tests_runs(self, sha):
            if sha == "sha-v1.405.0":
                self.calls.append(("tests", sha))
                raise RuntimeError("gh api failed: HTTP 502")
            return super().tests_runs(sha)

    gh = FlakyNewest([_api("1.405.0", 30), _api("1.404.0", 50)], {"1.404.0": "success"})
    logs: list[str] = []
    code, message = mod.run(tmp_path, REPO, gh, now=NOW, log=logs.append)
    assert code == 5 and not (tmp_path / "latest.yml").exists(), logs
    assert ("tests", "sha-v1.404.0") not in gh.calls
    assert any("could not be read, so nothing older is put on Stable" in line for line in logs), logs


def test_the_cli_runs_with_a_fake_github_and_writes_the_commit_message(tmp_path):
    mod = _mod()
    gh = FakeGitHub([_api("1.404.0", 30)], {"1.404.0": "success"})
    out = tmp_path / "msg.txt"
    code = mod.main(["--dir", str(tmp_path / "wt"), "--repo", REPO, "--message-out", str(out)], gh=gh, now=NOW)
    assert code == 0 and out.read_text(encoding="utf-8") == "stable: v1.404.0\n"
    assert mod.main(["--dir", str(tmp_path / "wt"), "--repo", "not a repo"], gh=gh, now=NOW) == 2


# ==========================================================================
# 3. the workflow
# ==========================================================================


def _yaml(name: str) -> dict:
    return yaml.safe_load(_txt(_WF / name))


def _on(wf: dict) -> dict:
    return wf.get("on", wf.get(True))  # PyYAML reads the bare key `on` as True


def test_the_stable_workflow_has_its_shape():
    wf = _yaml("stable.yml")
    on = _on(wf)
    assert set(on) == {"schedule", "workflow_dispatch"}, "no push, no pull_request: it never runs on a push"
    cron = on["schedule"][0]["cron"]
    minute = cron.split()[0]
    assert re.fullmatch(r"\d+ \* \* \* \*", cron) and minute not in ("0", "30"), cron
    inputs = on["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"hold", "unhold", "promote"}
    assert all(spec["type"] == "string" and spec["default"] == "" for spec in inputs.values())
    # The ONLY write is contents (pushing updates-stable); actions: read is the
    # Tests verdict (a token's unlisted scopes are none).
    assert wf["permissions"] == {"contents": "write", "actions": "read"}, wf["permissions"]
    assert [k for k, v in wf["permissions"].items() if v == "write"] == ["contents"]
    assert wf["concurrency"]["group"] and wf["concurrency"]["cancel-in-progress"] is False
    (job,) = wf["jobs"].values()
    assert isinstance(job["timeout-minutes"], int) and 0 < job["timeout-minutes"] <= 30
    assert "permissions" not in job, "no job widens the workflow's permissions"
    run = "\n".join(s.get("run", "") for s in job["steps"])
    assert "uv run --no-project python scripts/promote_stable.py" in run
    assert "push origin HEAD:updates-stable" in run
    assert re.findall(r"push origin \S+", run) == ["push origin HEAD:updates-stable"], "it writes ONE branch"
    assert "checkout --orphan updates-stable" in run
    assert "${{" not in run, "inputs reach the script through env, never pasted into shell text"
    env = next(s["env"] for s in job["steps"] if "run" in s)
    assert env["HOLD"] == "${{ inputs.hold }}" and env["PROMOTE"] == "${{ inputs.promote }}"
    # Exits 3 (promote refused), 4 (manifest unusable), 5 (Tests unread)
    # still commit the hold edits, then end red; anything else ends at once.
    assert "0|3|4|5) ;;" in run and '*) exit "$rc" ;;' in run, run
    assert run.rstrip().endswith('exit "$rc"')


def test_neither_existing_workflow_triggers_on_the_stable_branch():
    for name in ("release.yml", "tests.yml"):
        on = _on(_yaml(name))
        push = on.get("push") or {}
        assert push.get("branches") == ["master"], (name, push)
        assert set(push.get("tags") or []) <= {"v*"}, (name, push)
        assert "updates-stable" not in json.dumps(on), name
        # pull_request fires for a PR, never for a branch push.
        assert set(on) <= {"push", "pull_request", "workflow_dispatch"}, (name, sorted(on))


# ==========================================================================
# 4. the desktop shell, lifted and run under node
# ==========================================================================


def _main_src() -> str:
    return _txt(_MAIN)


def _fn(name: str, prefix: str = "") -> str:
    src = _main_src()
    start = src.index(f"{prefix}function {name}(")
    end = src.index("\n}\n", start) + 3
    return src[start:end]


def _decl(name: str) -> str:
    m = re.search(rf"^(?:const|let) {name} = [^\n]*;", _main_src(), re.M)
    assert m, f"main.js has no top-level decl {name}"
    return m.group(0)


def _node(script: str, tmp_path: Path, name: str, *argv: str) -> dict:
    f = tmp_path / f"{name}.js"
    f.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(f), *argv], capture_output=True, text=True, encoding="utf-8",
                          timeout=120, env={**os.environ})
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


_HARNESS = """
const fs = require("fs");
const path = require("path");
const logs = [];
const states = [];
const timers = [];
let checks = 0;
function desktopLog(level, ...a) { logs.push(level + ": " + a.join(" ")); }
function _emitUpdateState(patch) { states.push(patch); }
function checkForUpdates() { checks += 1; }
// Never the real ~/.ironjarvis/config.toml: the pins hand the file in.
function globalConfigFile() { return ""; }
const realSetTimeout = setTimeout;
const realClearTimeout = clearTimeout;
let captureTimers = true;
setTimeout = (fn, ms) => {
  if (!captureTimers) return realSetTimeout(fn, ms);
  const t = { fn, ms, cleared: false, unref() { this.unrefd = true; } }; timers.push(t); return t;
};
clearTimeout = (t) => { if (t && t.fn) { t.cleared = true; } else { realClearTimeout(t); } };
__DECLS__
__FUNCS__
const scenario = process.argv[2];
const cases = process.argv[3] ? JSON.parse(fs.readFileSync(process.argv[3], "utf8")) : null;
const emit = (o) => process.stdout.write(JSON.stringify(o) + "\\n");
const feedCalls = [];
function makeUpdater(plan) {
  return {
    setFeedURL(opts) { feedCalls.push(opts.provider === "github" ? "github" : opts.url); },
    async checkForUpdates() {
      const which = feedCalls[feedCalls.length - 1];
      const verdict = plan[which === "github" ? "feed" : which.endsWith("/updates-stable") ? "stable" : "generic"];
      if (verdict === "ok") return { from: which };
      onUpdaterError(plan.msg);
      throw new Error(plan.msg);
    },
  };
}
(async () => {
  if (scenario === "parse") {
    emit(cases.map((t) => updateChannelOf(t)));
  } else if (scenario === "home") {
    emit({ none: readUpdateChannel(cases.missing), stable: readUpdateChannel(cases.stable),
           layered: (cases.layers || []).map(([home, global]) => readUpdateChannel(home, global)) });
  } else if (scenario === "channels") {
    captureTimers = false;
    const asked = [];
    const answers = cases;
    const fakeFetch = (url, opts) => {
      asked.push(url);
      const plan = answers[url.includes("/updates-stable/") ? "stable" : "fresh"];
      if (plan === "hang") {
        return new Promise((_res, rej) => opts.signal.addEventListener("abort", () => rej(new Error("aborted"))));
      }
      if (plan === "throw") return Promise.reject(new Error("ENOTFOUND"));
      if (plan === "404") return Promise.resolve({ ok: false, text: async () => "Not Found" });
      return Promise.resolve({ ok: true, text: async () => plan });
    };
    const t0 = Date.now();
    const out = await readChannelVersions(fakeFetch, 150);
    emit({ out, asked, ms: Date.now() - t0 });
  } else if (scenario === "fits") {
    emit(cases.map(([channel, foundOn]) => downloadFitsChannel(channel, foundOn)));
  } else {
    const au = makeUpdater(cases.plan);
    let result = null, rejected = null;
    try { result = await checkForUpdatesWithFallback(au, cases.channel); } catch (e) { rejected = String(e.message).split("\\n")[0]; }
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
            "UPDATE_STABLE_MANIFEST_URL", "UPDATE_CHANNEL_KEY", "UPDATE_CHANNELS_TIMEOUT_MS",
            "_updateSource", "_updateRetryTimer", "_updateFallbackArmed",
        )
    )
    funcs = "\n".join(
        _fn(n, prefix)
        for prefix, n in (
            ("", "updateChannelSetting"), ("", "updateChannelOf"), ("", "readUpdateChannel"),
            ("", "manifestVersionOf"), ("async ", "readChannelVersions"), ("", "downloadFitsChannel"),
            ("", "updateErrorKind"), ("", "_updateErrorStatus"), ("", "friendlyUpdateError"),
            ("", "friendlyStableError"), ("", "scheduleUpdateRetry"), ("", "clearUpdateRetry"),
            ("", "onUpdaterError"), ("", "_setUpdateSource"), ("async ", "checkForUpdatesWithFallback"),
        )
    )
    return _HARNESS.replace("__DECLS__", decls).replace("__FUNCS__", funcs)


def _cases(tmp_path: Path, payload, name="cases.json") -> str:
    f = tmp_path / name
    f.write_text(json.dumps(payload), encoding="utf-8")
    return str(f)


@requires_node
def test_the_channel_is_read_from_config_toml_and_anything_unclear_is_fresh(tmp_path):
    texts = [
        "", "update_channel = \"stable\"\n", "update_channel = 'stable'\n", "update_channel = \" Stable \"\n",
        "\ufeffupdate_channel = \"stable\"  # the daily driver\n",
        "update_channel = \"fresh\"\n", "update_channel = \"stabel\"\n", "update_channel = stable\n",
        "update_channel = true\n", "# update_channel = \"stable\"\n",
        "[providers]\nupdate_channel = \"stable\"\n",  # inside a table: not the setting
        "lan_access = true\r\nupdate_channel = \"stable\"\r\n[x]\n",
        "not toml at all {{{", "update_channel_extra = \"stable\"\n",
    ]
    got = _node(_harness(), tmp_path, "parse", "parse", _cases(tmp_path, texts))
    assert got == ["fresh", "stable", "stable", "stable", "stable", "fresh", "fresh", "fresh",
                   "fresh", "fresh", "fresh", "stable", "fresh", "fresh"], got


@requires_node
def test_the_file_the_daemons_writer_saves_is_the_file_the_shell_reads(tmp_path):
    """Cross-language: the settings writer's config.toml (tomli_w) → main.js."""
    from iron_jarvis.daemon.app import create_app

    client = TestClient(create_app(str(tmp_path / "root")))
    home = Path(client.app.state.d.platform.config.home)
    r = client.put("/settings/values", json={"values": {"update_channel": "stable"}})
    assert r.status_code == 200, r.text
    assert re.search(r'^update_channel = "stable"$', (home / "config.toml").read_text(encoding="utf-8"), re.M)
    got = _node(_harness(), tmp_path, "home", "home",
                _cases(tmp_path, {"missing": str(tmp_path / "nowhere"), "stable": str(home)}))
    assert (got["none"], got["stable"]) == ("fresh", "stable")
    r = client.put("/settings/values", json={"values": {"update_channel": "fresh"}})
    assert r.status_code == 200
    got = _node(_harness(), tmp_path, "home2", "home",
                _cases(tmp_path, {"missing": str(tmp_path / "nowhere"), "stable": str(home)}, "c2.json"))
    assert got["stable"] == "fresh", "a change applies at the next read — no restart"


@requires_node
def test_the_shell_layers_the_files_the_way_the_daemon_does(tmp_path, monkeypatch):
    """core/config._load_config layers ~/.ironjarvis/config.toml UNDER the
    state home's config.toml; the shell reads the same two files, home first."""
    import iron_jarvis.core.config as C

    homes = {}
    for name, text in (("empty", ""), ("fresh", 'update_channel = "fresh"\n'), ("none", None)):
        d = tmp_path / name
        d.mkdir()
        if text is not None:
            (d / "config.toml").write_text(text, encoding="utf-8")
        homes[name] = str(d)
    global_stable = tmp_path / "global.toml"
    global_stable.write_text('update_channel = "stable"\n', encoding="utf-8")
    layers = [
        [homes["none"], str(global_stable)],   # no home file: the global layer decides
        [homes["empty"], str(global_stable)],  # home file without the key: the global layer
        [homes["fresh"], str(global_stable)],  # the home sets it: the home wins
        [homes["none"], str(tmp_path / "nowhere.toml")],
    ]
    got = _node(_harness(), tmp_path, "layers", "home",
                _cases(tmp_path, {"missing": homes["none"], "stable": homes["none"], "layers": layers}))
    assert got["layered"] == ["stable", "stable", "fresh", "fresh"], got
    # The daemon's own merge agrees on the same three files.
    monkeypatch.setattr(C, "global_config_path", lambda: global_stable)
    for (home, _g), want in zip(layers[:3], ["stable", "stable", "fresh"]):
        root = Path(home) / "root"
        root.mkdir(exist_ok=True)
        monkeypatch.setenv("IRONJARVIS_HOME", home)
        assert C._load_config(root).update_channel == want, home


@requires_node
def test_stable_reads_its_own_manifest_and_never_falls_back_to_the_feed(tmp_path):
    stable_url = re.search(r'"(https://[^"]+)"', _decl("UPDATE_STABLE_MANIFEST_URL")).group(1)
    ok = _node(_harness(), tmp_path, "s1", "check",
               _cases(tmp_path, {"channel": "stable", "plan": {"stable": "ok", "generic": "ok", "feed": "ok"}}))
    assert ok["feedCalls"] == [stable_url] and ok["result"] == {"from": stable_url}
    assert ok["source"] == "stable" and ok["armed"] is False and ok["states"] == []

    msg = "Error: getaddrinfo ENOTFOUND raw.githubusercontent.com"
    bad = _node(_harness(), tmp_path, "s2", "check",
                _cases(tmp_path, {"channel": "stable", "plan": {"stable": "fail", "generic": "ok", "feed": "ok", "msg": msg}},
                       "c2.json"))
    assert bad["feedCalls"] == [stable_url], "NEVER the feed (it names the newest release) nor the Fresh manifest"
    assert bad["rejected"] is not None
    errors = [s for s in bad["states"] if s.get("status") == "error"]
    assert len(errors) == 1 and "try again in 5 minutes" in errors[0]["error"], bad["states"]
    assert len(bad["timers"]) == 1, "the existing one early retry"
    assert bad["armed"] is False

    missing = ("Cannot find latest.yml in the latest release artifacts "
               "(https://raw.githubusercontent.com/RealDealCPA-VR/Iron-Jarvis/updates-stable/latest.yml): "
               "HttpError: 404 Not Found")
    none = _node(_harness(), tmp_path, "s3", "check",
                 _cases(tmp_path, {"channel": "stable", "plan": {"stable": "fail", "msg": missing}}, "c3.json"))
    errors = [s for s in none["states"] if s.get("status") == "error"]
    assert errors and errors[0]["error"].startswith("The Stable channel has no version to offer yet."), errors
    assert "still uploading" not in errors[0]["error"]


@requires_node
def test_fresh_keeps_the_old_order_exactly(tmp_path):
    fresh_url = re.search(r'"(https://[^"]+)"', _decl("UPDATE_MANIFEST_URL")).group(1)
    for i, channel in enumerate(("fresh", None, "", "garbage")):
        out = _node(_harness(), tmp_path, f"f{i}", "check",
                    _cases(tmp_path, {"channel": channel,
                                      "plan": {"generic": "fail", "feed": "ok", "stable": "ok", "msg": "Error: read ECONNRESET"}},
                           f"f{i}.json"))
        assert out["feedCalls"] == [fresh_url, "github", fresh_url], (channel, out["feedCalls"])
        assert out["result"] == {"from": "github"} and out["states"] == [] and out["timers"] == []


@requires_node
def test_update_channels_reads_both_versions_bounded_and_never_throws(tmp_path):
    got = _node(_harness(), tmp_path, "c1", "channels",
                _cases(tmp_path, {"fresh": "version: 1.405.2\nfiles: []\n", "stable": "version: '1.404.0'\n"}))
    assert got["out"] == {"fresh": "1.405.2", "stable": "1.404.0"}
    assert sorted(got["asked"]) == sorted([
        "https://raw.githubusercontent.com/RealDealCPA-VR/Iron-Jarvis/updates/latest.yml",
        "https://raw.githubusercontent.com/RealDealCPA-VR/Iron-Jarvis/updates-stable/latest.yml",
    ])
    got = _node(_harness(), tmp_path, "c2", "channels", _cases(tmp_path, {"fresh": "throw", "stable": "hang"}, "c2.json"))
    assert got["out"] == {"fresh": None, "stable": None}
    assert got["ms"] < 5000, "a hanging branch is cut at the bound"
    got = _node(_harness(), tmp_path, "c3", "channels", _cases(tmp_path, {"fresh": "404", "stable": "nonsense"}, "c3.json"))
    assert got["out"] == {"fresh": None, "stable": None}


@requires_node
def test_a_download_fits_the_channel_only_when_that_channel_offered_it(tmp_path):
    got = _node(_harness(), tmp_path, "fits", "fits",
                _cases(tmp_path, [["stable", "stable"], ["stable", "manifest"], ["stable", "feed"],
                                  ["stable", None], ["fresh", "manifest"], ["fresh", "stable"], ["fresh", None]]))
    assert got == [True, False, False, False, True, True, True]


# --- the SEQUENCE: the real initUpdater handlers on a stand-in updater ------

_SEQ_HARNESS = """
const fs = require("fs");
const path = require("path");
const EventEmitter = require("events");
const realRequire = require;
const HOME = process.argv[3];
const scenario = process.argv[2];
const out = { logs: [], states: [], shown: [], rechecks: [], cancelled: 0, retries: 0 };
const fakeAU = new EventEmitter();
fakeAU.plan = [];
fakeAU.feed = null;
fakeAU.setFeedURL = (o) => { fakeAU.feed = o.provider === "github" ? "github" : o.url; };
fakeAU.checkForUpdates = async () => fakeAU.plan.shift()(fakeAU);
function setChannel(ch) {
  fs.writeFileSync(path.join(HOME, "config.toml"), ch ? `update_channel = "${ch}"\\n` : "");
}
function harness(require) {
  const IS_PACKAGED = true;
  let _updateState = { status: "idle", channel: null };
  function _emitUpdateState(patch) { _updateState = { ..._updateState, ...patch }; out.states.push(patch); }
  function desktopLog(level, ...a) { out.logs.push(level + ": " + a.join(" ")); }
  function safeAppVersion() { return "1.404.0"; }
  function updateChannelHome() { return HOME; }
  function globalConfigFile() { return ""; }
  function refreshTrayMenu() {}
  const tray = null;
  class Notification { constructor(o) { this.o = o; } on() {} show() { out.shown.push(this.o.title); } }
  function requestUpdateInstall() {}
  function checkForUpdates() { out.rechecks.push(1); }
  const setTimeout = (fn, ms) => { out.timerMs = (out.timerMs || []).concat(ms); return { unref() {} }; };
  const clearTimeout = () => {};
  __DECLS__
  __FUNCS__
  const X = "1.405.2", Y = "1.405.0";
  const pendingDownload = () => { let r; const p = new Promise((res) => { r = res; }); return { p, r }; };
  const freshOffersX = (au) => {
    au.emit("checking-for-update");
    au.emit("update-available", { version: X });
    const d = pendingDownload();
    fakeAU.resolveX = d.r;
    return { isUpdateAvailable: true, updateInfo: { version: X },
             cancellationToken: { cancel() { out.cancelled += 1; } }, downloadPromise: d.p };
  };
  const stableHasNothing = (au) => {
    au.emit("checking-for-update");
    au.emit("update-not-available", { version: "1.404.0" });
    return { isUpdateAvailable: false, updateInfo: { version: "1.404.0" } };
  };
  const stableOffersYButHandsBackX = (au) => {
    // electron-updater 6.8.9 AppUpdater.downloadUpdate: a download already in
    // progress is returned as it is — the Stable check's result names Y, the
    // file that lands is X.
    au.emit("checking-for-update");
    au.emit("update-available", { version: Y });
    return { isUpdateAvailable: true, updateInfo: { version: Y },
             cancellationToken: { cancel() {} }, downloadPromise: new Promise(() => {}) };
  };
  const stableFails = (au) => {
    au.emit("checking-for-update");
    au.emit("error", new Error("Error: read ECONNRESET"));
    throw new Error("Error: read ECONNRESET");
  };
  const report = () => ({ ...out, pending: pendingUpdateInfo, autoInstall: fakeAU.autoInstallOnAppQuit,
                          lastStatus: _updateState.status, feed: fakeAU.feed });
  return {
    async run() {
      initUpdater();
      if (scenario === "A") {
        // Fresh finds X and starts downloading it ...
        setChannel("fresh");
        fakeAU.plan.push(freshOffersX);
        await runUpdateCheck(fakeAU);
        fakeAU.emit("download-progress", { percent: 40 });
        // ... the user picks Stable at 40% (the page saves, then checks) ...
        setChannel("stable");
        fakeAU.plan.push(stableHasNothing);
        await runUpdateCheck(fakeAU);
        // ... and X lands anyway.
        fakeAU.emit("update-downloaded", { version: X });
        fakeAU.resolveX();
        return report();
      }
      if (scenario === "B") {
        setChannel("fresh");
        fakeAU.plan.push(freshOffersX);
        await runUpdateCheck(fakeAU);
        setChannel("stable");
        fakeAU.plan.push(stableOffersYButHandsBackX);
        await runUpdateCheck(fakeAU);
        fakeAU.emit("update-downloaded", { version: X });   // not offered
        const afterX = { pending: pendingUpdateInfo, autoInstall: fakeAU.autoInstallOnAppQuit, shown: out.shown.slice() };
        fakeAU.emit("update-downloaded", { version: Y });   // Y was offered by Stable
        return { ...report(), afterX };
      }
      if (scenario === "C") {
        // X downloaded and waiting on Fresh; then Stable, and its check FAILS.
        setChannel("fresh");
        fakeAU.plan.push(freshOffersX);
        await runUpdateCheck(fakeAU);
        fakeAU.emit("update-downloaded", { version: X });
        fakeAU.resolveX();
        const before = { pending: pendingUpdateInfo, autoInstall: fakeAU.autoInstallOnAppQuit };
        setChannel("stable");
        fakeAU.plan.push(stableFails);
        let threw = false;
        try { await runUpdateCheck(fakeAU); } catch { threw = true; }
        return { ...report(), before, threw };
      }
      if (scenario === "D") {
        // Waiting on Fresh, then Stable with NO check before the install press
        // or the quit: the install paths drop it themselves.
        setChannel("fresh");
        fakeAU.plan.push(freshOffersX);
        await runUpdateCheck(fakeAU);
        fakeAU.emit("update-downloaded", { version: X });
        setChannel("stable");
        const dropped = dropPendingIfOffChannel();
        return { ...report(), dropped, again: dropPendingIfOffChannel() };
      }
      if (scenario === "fresh") {
        // Control: on Fresh a download is offered as before.
        setChannel("fresh");
        fakeAU.plan.push(freshOffersX);
        await runUpdateCheck(fakeAU);
        fakeAU.emit("update-downloaded", { version: X });
        return report();
      }
      if (scenario === "sticky") {
        // Found on Stable once, it stays Stable's even if Fresh finds it later.
        setChannel("stable");
        noteFoundOn(X, "stable");
        noteFoundOn(X, "manifest");
        fakeAU.emit("update-downloaded", { version: X });
        return report();
      }
    },
  };
}
harness((m) => (m === "electron-updater" ? { autoUpdater: fakeAU } : realRequire(m)))
  .run()
  .then((r) => process.stdout.write(JSON.stringify(r) + "\\n"));
"""


def _seq_harness() -> str:
    decls = "\n".join(
        _decl(n)
        for n in (
            "UPDATE_RECHECK_MS", "UPDATE_RETRY_MS", "UPDATE_MANIFEST_URL", "UPDATE_GITHUB_FEED",
            "UPDATE_STABLE_MANIFEST_URL", "UPDATE_DROP_RECHECK_MS", "_updateSource", "_updateRetryTimer",
            "_updateFallbackArmed", "_autoUpdater", "pendingUpdateInfo", "_foundOn", "_inFlight",
        )
    )
    funcs = "\n".join(
        _fn(n, prefix)
        for prefix, n in (
            ("", "updateChannelSetting"), ("", "readUpdateChannel"), ("", "downloadFitsChannel"),
            ("", "noteFoundOn"), ("", "noteCheckResult"), ("", "enforceUpdateChannel"),
            ("", "dropPendingIfOffChannel"), ("", "currentUpdateChannel"), ("async ", "runUpdateCheck"),
            ("", "forgetPendingUpdate"), ("", "initUpdater"), ("", "updateErrorKind"),
            ("", "_updateErrorStatus"), ("", "friendlyUpdateError"), ("", "friendlyStableError"),
            ("", "scheduleUpdateRetry"), ("", "clearUpdateRetry"), ("", "onUpdaterError"),
            ("", "_setUpdateSource"), ("async ", "checkForUpdatesWithFallback"),
        )
    )
    return _SEQ_HARNESS.replace("__DECLS__", decls).replace("__FUNCS__", funcs)


def _seq(tmp_path: Path, scenario: str) -> dict:
    home = tmp_path / f"home-{scenario}"
    home.mkdir()
    return _node(_seq_harness(), tmp_path, f"seq-{scenario}", scenario, str(home))


@requires_node
def test_scenario_a_a_fresh_download_in_flight_when_the_pc_moves_to_stable_never_installs(tmp_path):
    """The reviewer's trace: "Downloading 40%" of Fresh X → pick Stable → the
    page's check says nothing is newer while nothing is pending yet → X
    finishes. Before the fix: "vX is ready", installed on Quit."""
    got = _seq(tmp_path, "A")
    assert got["cancelled"] == 1, "the switch stops the Fresh download it can reach"
    assert got["pending"] is None and got["autoInstall"] is False, got
    assert got["shown"] == [], "no 'is ready' notification"
    assert got["lastStatus"] != "downloaded", got["states"]
    assert got["timerMs"] == [15000], "one check later fetches what Stable offers"


@requires_node
def test_scenario_b_a_stable_check_handed_the_fresh_download_does_not_offer_it(tmp_path):
    got = _seq(tmp_path, "B")
    assert got["afterX"]["pending"] is None and got["afterX"]["autoInstall"] is False
    assert got["afterX"]["shown"] == []
    # What Stable offered is offered and installs on Quit.
    assert got["pending"] == {"version": "1.405.0"} and got["autoInstall"] is True
    assert got["shown"] == ["Iron Jarvis v1.405.0 is ready"]


@requires_node
def test_scenario_c_a_failed_stable_check_still_drops_a_waiting_fresh_download(tmp_path):
    got = _seq(tmp_path, "C")
    assert got["before"]["pending"] == {"version": "1.405.2"} and got["before"]["autoInstall"] is True
    assert got["threw"] is True
    assert got["pending"] is None and got["autoInstall"] is False, got
    assert got["feed"].endswith("/updates-stable"), "and the failed check never reached the feed"


@requires_node
def test_the_install_paths_drop_an_off_channel_download_without_a_check(tmp_path):
    got = _seq(tmp_path, "D")
    assert got["dropped"] is True and got["again"] is False
    assert got["pending"] is None and got["autoInstall"] is False


@requires_node
def test_on_fresh_a_download_is_offered_as_before_and_stable_stays_stable(tmp_path):
    got = _seq(tmp_path, "fresh")
    assert got["pending"] == {"version": "1.405.2"} and got["autoInstall"] is True
    assert got["shown"] == ["Iron Jarvis v1.405.2 is ready"] and got["cancelled"] == 0
    sticky = _seq(tmp_path, "sticky")
    assert sticky["pending"] == {"version": "1.405.2"} and sticky["autoInstall"] is True


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
  process.stdout.write(JSON.stringify({ asked, version: info.version, files: files.map((f) => f.url.href) }) + "\\n");
});
"""


@requires_node
@requires_updater
def test_the_real_generic_provider_reads_the_stable_branchs_manifest(tmp_path):
    """electron-updater's OWN GenericProvider pointed at the Stable URL exactly
    as main.js sets it, handed what promote_stable writes: it must ask for
    ``<url>/latest.yml`` and hand back the absolute installer URL."""
    mod = _mod()
    manifest = tmp_path / "latest.yml"
    manifest.write_text(mod.rewrite(_MANIFEST.format(v="1.404.0"), REPO, "1.404.0"), encoding="utf-8")
    url = re.search(r'"(https://[^"]+)"', _decl("UPDATE_STABLE_MANIFEST_URL")).group(1)
    out = _node(_PROVIDER_PROBE, tmp_path, "provider", str(_UPDATER_PKG).replace("\\", "/"), str(manifest), url)
    assert out["asked"] == [f"{url}/latest.yml"], out["asked"]
    assert out["version"] == "1.404.0"
    assert out["files"] == [f"https://github.com/{REPO}/releases/download/v1.404.0/Iron-Jarvis-Setup-1.404.0.exe"]


def test_the_shell_wiring_reads_the_channel_at_every_check():
    src = _main_src()
    timer_site = src[src.index("function checkForUpdates()"):src.index("function checkForUpdates()") + 900]
    assert "runUpdateCheck(autoUpdater)" in timer_site
    button = src[src.index('ipcMain.handle("update:check"'):src.index('ipcMain.handle("update:apply"')]
    assert "await runUpdateCheck(au)" in button
    run_check = _fn("runUpdateCheck", "async ")
    assert "const channel = currentUpdateChannel();" in run_check
    assert "await checkForUpdatesWithFallback(au, channel)" in run_check and "noteCheckResult(result)" in run_check
    cur = _fn("currentUpdateChannel")
    assert "readUpdateChannel(updateChannelHome())" in cur and "_emitUpdateState({ channel })" in cur
    assert "enforceUpdateChannel(channel)" in cur
    # Every install path re-judges the waiting download by the channel NOW.
    for name, prefix in (("requestUpdateInstall", ""), ("applyPendingUpdate", "async ")):
        head = "\n".join(_fn(name, prefix).split("\n")[:4])
        assert "if (dropPendingIfOffChannel()) return;" in head, (name, head)
    quit_path = src[src.index("sweepOwnDaemonOnQuit().finally("):]
    quit_path = quit_path[:quit_path.index("app.quit();")]
    assert quit_path.index("dropPendingIfOffChannel();") < quit_path.index("markUpdatePending(")
    # update:channels is sender-checked like every privileged handler.
    channels = src[src.index('ipcMain.handle("update:channels"'):]
    channels = channels[:channels.index("});") + 3]
    assert "if (!isTrustedDashboardSender(event)) return null;" in channels
    assert "readChannelVersions(fetch, UPDATE_CHANNELS_TIMEOUT_MS)" in channels
    # Where each version was found is recorded BEFORE its download starts; a
    # landed download is judged by the channel now; a fitting one re-arms
    # install-on-quit.
    init = src[src.index("function initUpdater()"):src.index("function checkForUpdates()")]
    available = init[init.index('"update-available"'):init.index('"update-downloaded"')]
    assert "noteFoundOn(info && info.version, _updateSource);" in available
    downloaded = init.split('"update-downloaded"', 1)[1]
    assert downloaded.index("downloadFitsChannel(") < downloaded.index("autoUpdater.autoInstallOnAppQuit = true;")
    assert "forgetPendingUpdate(autoUpdater)" in downloaded
    forget = _fn("forgetPendingUpdate")
    assert "pendingUpdateInfo = null;" in forget and "au.autoInstallOnAppQuit = false;" in forget
    assert "channels: () => ipcRenderer.invoke(\"update:channels\")" in _txt(_PRELOAD)


def test_the_stable_url_the_key_and_the_repo_agree_everywhere():
    from iron_jarvis.core import update_channel as U
    from iron_jarvis.settings import schema

    pkg = json.loads(_txt(_ROOT / "desktop" / "package.json"))
    publish = pkg["build"]["publish"][0]
    repo = f"{publish['owner']}/{publish['repo']}"
    assert _decl("UPDATE_STABLE_MANIFEST_URL") == (
        f'const UPDATE_STABLE_MANIFEST_URL = "https://raw.githubusercontent.com/{repo}/updates-stable";'
    )
    assert U.STABLE_REPO == repo and _mod().BRANCH == "updates-stable"
    assert _decl("UPDATE_CHANNEL_KEY") == 'const UPDATE_CHANNEL_KEY = "update_channel";'
    assert schema.get("update_channel").key == "update_channel"
    dash = _txt(_ROOT / "dashboard" / "lib" / "updateChannel.ts")
    assert 'export const UPDATE_CHANNEL_KEY = "update_channel";' in dash


# ==========================================================================
# 5. the setting
# ==========================================================================


def test_the_setting_row_and_its_tier_per_value(tmp_path):
    from iron_jarvis.core.config import Config
    from iron_jarvis.settings import schema

    row = schema.get("update_channel")
    assert row.group == "system" and row.section == "Updates" and row.type == "enum"
    assert [v for v, _ in row.options] == ["fresh", "stable"]
    assert row.restart is False, "read at the next check, never at a restart"
    assert row.tier_for("stable") == "allow", "moving to Stable only narrows what reaches the PC"
    assert row.tier_for("fresh") == "ask-floor", "moving to Fresh widens it: asks every time from chat"
    assert "update_channel" in schema.daemon_keys()
    assert "out for a day" in row.help and "tested on your other PC" not in row.help
    cfg = Config(home=tmp_path, project_root=tmp_path)
    assert cfg.update_channel == "fresh"
    for raw, want in ((" Stable ", "stable"), ("STABLE", "stable"), ("stabel", "fresh"), (None, "fresh"), ("", "fresh")):
        trial = cfg.model_copy(deep=True)
        trial.update_channel = raw
        assert trial.update_channel == want, raw


def test_the_writer_refuses_a_value_that_is_not_a_channel(tmp_path):
    from iron_jarvis.daemon.app import create_app

    client = TestClient(create_app(str(tmp_path / "root")))
    r = client.put("/settings/values", json={"values": {"update_channel": "nightly"}})
    assert r.status_code == 400 and "Update channel must be one of: fresh, stable" in r.json()["detail"]
    assert client.get("/settings/values").json()["values"]["update_channel"] == "fresh"


# ==========================================================================
# 6. the hold from the app — the real gh never runs
# ==========================================================================


class _Run:
    def __init__(self, code=0, out="", err="", timed_out=False):
        self.code, self.out, self.err, self.timed_out = code, out, err, timed_out

    @property
    def ok(self):
        return self.code == 0 and not self.timed_out


@pytest.fixture()
def gh_spy(monkeypatch):
    """A stand-in GitHub CLI. The real one (and any child at all) must never
    run: core.procs.run fails the test if a gh child reaches it."""
    from iron_jarvis.core import procs
    from iron_jarvis.harness import branches as B

    state = {"installed": True, "signed_in": True, "workflow": _Run(0, "✓ Created workflow_dispatch event"),
             "calls": []}

    def fake_gh(args, cwd, *, timeout=B.GH_S):
        state["calls"].append(list(args))
        if args[:2] == ["auth", "status"]:
            return _Run(0 if state["signed_in"] else 1)
        if args[:2] == ["workflow", "run"]:
            return state["workflow"]
        raise AssertionError(f"unexpected gh call {args}")

    real_run = procs.run

    def no_real_gh(argv, *a, **k):
        if argv and "gh" in Path(str(argv[0])).stem.lower():
            raise AssertionError(f"the real GitHub CLI was started: {argv}")
        return real_run(argv, *a, **k)

    monkeypatch.setattr(B, "gh_path", lambda: "C:/fake/gh.exe" if state["installed"] else None)
    monkeypatch.setattr(B, "_gh", fake_gh)
    monkeypatch.setattr(procs, "run", no_real_gh)
    B.forget_gh_state()
    yield state
    B.forget_gh_state()


def _client(tmp_path):
    from iron_jarvis.daemon.app import create_app

    return TestClient(create_app(str(tmp_path / "root")))


def test_the_hold_runs_the_stable_workflow_through_a_signed_in_gh_after_two_presses(tmp_path, gh_spy):
    c = _client(tmp_path)
    view = c.get("/update/stable-hold", params={"version": "v1.404.0"}).json()
    assert view["can_run"] is True and view["reason"] == ""
    assert view["command"] == f"gh workflow run stable.yml --repo {REPO} -f hold=1.404.0"
    assert view["actions_url"] == f"https://github.com/{REPO}/actions/workflows/stable.yml"

    first = c.post("/update/stable-hold", json={"version": "1.404.0"})
    assert first.status_code == 400 and "twice" in first.json()["detail"]
    assert not any(call[:2] == ["workflow", "run"] for call in gh_spy["calls"]), "one press runs nothing"

    ok = c.post("/update/stable-hold", json={"version": "1.404.0", "confirm": True})
    assert ok.status_code == 200, ok.text
    msg = ok.json()["message"]
    assert ok.json()["ok"] is True and msg.startswith("Asked GitHub to hold v1.404.0.")
    # It claims only that the run was ASKED for: the hold lands when that run
    # happens, and holds.txt is where it shows.
    assert "holds.txt" in msg and "until you let it through" not in msg and "skips" not in msg
    runs = [call for call in gh_spy["calls"] if call[:2] == ["workflow", "run"]]
    assert runs == [["workflow", "run", "stable.yml", "--repo", REPO, "-f", "hold=1.404.0"]]


@pytest.mark.parametrize("bad", ["1.404", "1.404.0 && del *", "../1.404.0", "", "latest", "1.404.0\n"])
def test_a_version_that_is_not_x_y_z_runs_nothing(tmp_path, gh_spy, bad):
    c = _client(tmp_path)
    r = c.post("/update/stable-hold", json={"version": bad, "confirm": True})
    if bad == "1.404.0\n":
        # A trailing newline is stripped before the check: the version is fine.
        assert r.status_code == 200
        return
    assert r.status_code == 400 and r.json()["detail"] == "A version looks like 1.404.0."
    assert gh_spy["calls"] == []
    assert c.get("/update/stable-hold", params={"version": bad}).status_code == 400


def test_without_a_signed_in_gh_the_page_gets_the_command_and_nothing_runs(tmp_path, gh_spy):
    c = _client(tmp_path)
    gh_spy["installed"] = False
    view = c.get("/update/stable-hold", params={"version": "1.404.0"}).json()
    assert view["can_run"] is False and "not installed" in view["reason"]
    assert view["command"].startswith("gh workflow run stable.yml")
    r = c.post("/update/stable-hold", json={"version": "1.404.0", "confirm": True})
    assert r.status_code == 409 and "Run this instead: gh workflow run stable.yml" in r.json()["detail"]

    from iron_jarvis.harness import branches as B

    B.forget_gh_state()
    gh_spy["installed"] = True
    gh_spy["signed_in"] = False
    view = c.get("/update/stable-hold", params={"version": "1.404.0"}).json()
    assert view["can_run"] is False and "not signed in" in view["reason"]
    r = c.post("/update/stable-hold", json={"version": "1.404.0", "confirm": True})
    assert r.status_code == 409
    assert not any(call[:2] == ["workflow", "run"] for call in gh_spy["calls"])


def test_a_refused_or_slow_gh_is_said_in_words_with_its_output_masked(tmp_path, gh_spy):
    c = _client(tmp_path)
    gh_spy["workflow"] = _Run(1, "", "HTTP 403: Must have admin rights (token ghp_abcdefghijklmnopqrstuvwxyz0123456789)")
    r = c.post("/update/stable-hold", json={"version": "1.404.0", "confirm": True})
    body = r.json()
    assert r.status_code == 502 and body["detail"].startswith("GitHub did not take the request: HTTP 403")
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in json.dumps(body), "a token never reaches the page"
    gh_spy["workflow"] = _Run(-1, "", "", timed_out=True)
    r = c.post("/update/stable-hold", json={"version": "1.404.0", "confirm": True})
    assert r.status_code == 504 and "may not have been asked for" in r.json()["detail"]


def test_the_words_never_claim_the_other_pc_tested_anything():
    files = [
        _SCRIPTS / "promote_stable.py", _WF / "stable.yml", _ROOT / "src" / "iron_jarvis" / "core" / "update_channel.py",
        _ROOT / "dashboard" / "lib" / "updateChannel.ts",
        _ROOT / "dashboard" / "components" / "settings" / "UpdateChannelChoice.tsx",
    ]
    for f in files:
        low = _txt(f).lower()
        for claim in ("tested on your other pc", "ran on your other pc", "verified on your other pc"):
            assert claim not in low, (f.name, claim)
