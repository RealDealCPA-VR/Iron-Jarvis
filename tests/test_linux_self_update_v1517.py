"""The Linux app updates itself, from its OWN branches, and Windows reads what it read.

The owner's condition for everything Linux (2026-11-11, repeated 2026-11-16):
no effect on the Windows app at all. So the updater in ``desktop/main.js`` has
ONE name for the system it serves (``UPDATE_PLATFORM``), every Linux path sits
behind it, and these pins run BOTH paths on whatever machine runs the suite:

* Windows asks for ``latest.yml`` on ``updates`` / ``updates-stable``, exactly
  as before; Linux asks for ``latest-linux.yml`` on ``updates-linux`` /
  ``updates-stable-linux`` (branches only the Linux release workflow writes).
* A missing ``latest-linux.yml`` is "still being prepared", never a raw error
  (every Linux preview showed "Cannot find latest-linux.yml" on each check).
* A Linux copy that cannot replace itself (an unpacked folder) has no updater:
  the Updates page says so instead of a check that never ends.
* The update REHEARSAL source is plain http on 127.0.0.1 only, Linux only.
* The new app file leaves at once when the updater starts it only to finish a
  Quit, and waits for the old app's lock after "Restart to update".
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

import pytest

from tests.test_update_channel_v1260 import _decl, _lift, _run, _src, requires_node

_HARNESS = """
const fs = require("fs"), path = require("path"), crypto = require("crypto");
const UPDATE_PLATFORM = __PLATFORM__;
__DECLS__
let _updateSource = "manifest";
__FUNCS__
const emit = (o) => process.stdout.write(JSON.stringify(o) + "\\n");
(async () => {
  const feeds = [];
  const au = { setFeedURL(o) { feeds.push(o); } };
  for (const which of ["manifest", "stable", "feed"]) _setUpdateSource(au, which);
  const asked = [];
  const fakeFetch = async (url) => { asked.push(url); return { ok: true, text: async () => "version: 1.2.3\\n" }; };
  const versions = await readChannelVersions(fakeFetch, 500);
  const env = (v) => ({ IRONJARVIS_UPDATE_REHEARSAL_URL: v });
  emit({
    feeds, asked, versions,
    file: updateManifestFileFor(),
    files: ["win32", "linux", "darwin"].map((p) => updateManifestFileFor(p)),
    bases: ["win32", "linux", "darwin"].map((p) => updateBaseFor("https://x/updates", p)),
    rehearsal: __REHEARSALS__.map((v) => updateRehearsalUrl(env(v))),
    rehearsalElsewhere: ["win32", "darwin"].map((p) => updateRehearsalUrl(env("http://127.0.0.1:9123"), p)),
    kinds: __MESSAGES__.map((m) => updateErrorKind(m)),
    stableWords: friendlyStableError(__MESSAGES__[0]),
    freshWords: friendlyUpdateError(__MESSAGES__[0]),
    selfUpdate: [
      linuxCanSelfUpdate({ APPIMAGE: "/home/pat/Iron-Jarvis-x86_64.AppImage" }, "/nowhere", () => false),
      linuxCanSelfUpdate({}, "/opt/Iron Jarvis/resources", (p) => p.replace(/\\\\/g, "/").endsWith("/resources/package-type")),
      linuxCanSelfUpdate({}, "/opt/Iron Jarvis/resources", () => false),
      linuxCanSelfUpdate({}, "", () => true),
    ],
    cached: cachedInstallerState(),
  });
})();
"""

_REHEARSALS = [
    "http://127.0.0.1:9123",            # the one shape accepted
    "https://127.0.0.1:9123",           # not plain http
    "http://localhost:9123",            # a name can be pointed elsewhere
    "http://127.0.0.1.example.com:80",  # looks like it, is another host
    "http://127.0.0.1:9123/x",          # nothing after the port
    "http://user@127.0.0.1:9123",
    "http://127.0.0.1",                 # no port
    "http://10.0.0.5:9123",
    "",
]

_MESSAGES = [
    'Cannot find channel "latest-linux.yml" update info: HttpError: 404 ',
    "Cannot find latest-linux.yml in the latest release artifacts "
    "(https://github.com/RealDealCPA-VR/Iron-Jarvis/releases/download/v1.515.0/latest-linux.yml): HttpError: 404 ",
    "Cannot find latest.yml in the latest release artifacts (https://github.com/x/y/releases/download/v1/latest.yml): "
    "HttpError: 404 ",
    "504 Gateway Time-out",
    "something else entirely",
]


def _script(platform: str) -> str:
    decls = "\n".join(
        _decl(n)
        for n in ("UPDATE_RETRY_MS", "UPDATE_MANIFEST_URL", "UPDATE_GITHUB_FEED", "UPDATE_STABLE_MANIFEST_URL")
    )
    funcs = "\n".join(
        _lift(f"{prefix}function {n}", f"{prefix}function {n}")
        for prefix, n in (
            ("", "updateBaseFor"), ("", "updateManifestFileFor"), ("", "updateRehearsalUrl"),
            ("", "linuxCanSelfUpdate"), ("", "manifestVersionOf"), ("async ", "readChannelVersions"),
            ("", "updateErrorKind"), ("", "_updateErrorStatus"), ("", "friendlyUpdateError"),
            ("", "friendlyStableError"), ("", "_setUpdateSource"), ("", "cachedInstallerState"),
        )
    )
    return (
        _HARNESS.replace("__PLATFORM__", json.dumps(platform))
        .replace("__DECLS__", decls)
        .replace("__FUNCS__", funcs)
        .replace("__REHEARSALS__", json.dumps(_REHEARSALS))
        .replace("__MESSAGES__", json.dumps(_MESSAGES))
    )


def _out(tmp_path: Path, platform: str, monkeypatch, **env: str) -> dict:
    monkeypatch.delenv("IRONJARVIS_UPDATE_REHEARSAL_URL", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return _run(_script(platform), tmp_path, f"linux-update-{platform}")


_BASE = "https://raw.githubusercontent.com/RealDealCPA-VR/Iron-Jarvis"


@requires_node
def test_windows_reads_exactly_what_it_read(tmp_path, monkeypatch):
    out = _out(tmp_path, "win32", monkeypatch)
    assert out["feeds"] == [
        {"provider": "generic", "url": f"{_BASE}/updates"},
        {"provider": "generic", "url": f"{_BASE}/updates-stable"},
        {"provider": "github", "owner": "RealDealCPA-VR", "repo": "Iron-Jarvis"},
    ]
    assert out["asked"] == [f"{_BASE}/updates/latest.yml", f"{_BASE}/updates-stable/latest.yml"]
    assert out["file"] == "latest.yml"
    # A rehearsal address in the environment changes nothing on Windows.
    out = _out(tmp_path, "win32", monkeypatch, IRONJARVIS_UPDATE_REHEARSAL_URL="http://127.0.0.1:9123")
    assert [f.get("url") for f in out["feeds"][:2]] == [f"{_BASE}/updates", f"{_BASE}/updates-stable"]


@requires_node
def test_linux_reads_its_own_branches_and_its_own_file(tmp_path, monkeypatch):
    out = _out(tmp_path, "linux", monkeypatch)
    assert out["feeds"] == [
        {"provider": "generic", "url": f"{_BASE}/updates-linux"},
        {"provider": "generic", "url": f"{_BASE}/updates-stable-linux"},
        {"provider": "github", "owner": "RealDealCPA-VR", "repo": "Iron-Jarvis"},
    ]
    assert out["asked"] == [
        f"{_BASE}/updates-linux/latest-linux.yml",
        f"{_BASE}/updates-stable-linux/latest-linux.yml",
    ]
    assert out["versions"] == {"fresh": "1.2.3", "stable": "1.2.3"}
    assert out["files"] == ["latest.yml", "latest-linux.yml", "latest.yml"]
    assert out["bases"] == ["https://x/updates", "https://x/updates-linux", "https://x/updates"]


@requires_node
def test_a_missing_linux_file_is_still_being_prepared_never_a_raw_error(tmp_path, monkeypatch):
    out = _out(tmp_path, "linux", monkeypatch)
    assert out["kinds"] == ["publishing", "publishing", "publishing", "transient", "other"]
    assert "still uploading" in out["freshWords"] and "latest-linux" not in out["freshWords"]
    assert out["stableWords"].startswith("The Stable channel has no version to offer yet.")
    # The same answers on Windows: one rule, both files.
    assert _out(tmp_path, "win32", monkeypatch)["kinds"] == out["kinds"]


@requires_node
def test_the_rehearsal_source_is_this_machine_only_and_linux_only(tmp_path, monkeypatch):
    out = _out(tmp_path, "linux", monkeypatch)
    assert out["rehearsal"] == ["http://127.0.0.1:9123"] + [""] * (len(_REHEARSALS) - 1)
    assert out["rehearsalElsewhere"] == ["", ""]
    out = _out(tmp_path, "linux", monkeypatch, IRONJARVIS_UPDATE_REHEARSAL_URL="http://127.0.0.1:9123")
    assert [f["url"] for f in out["feeds"]] == ["http://127.0.0.1:9123"] * 3
    # Anything else in that variable is ignored: the real branches are read.
    out = _out(tmp_path, "linux", monkeypatch, IRONJARVIS_UPDATE_REHEARSAL_URL="https://updates.example.com")
    assert out["feeds"][0] == {"provider": "generic", "url": f"{_BASE}/updates-linux"}


@requires_node
def test_which_linux_copies_can_replace_themselves(tmp_path, monkeypatch):
    out = _out(tmp_path, "linux", monkeypatch)
    # The single file; an installed package; an unpacked folder; nothing known.
    assert out["selfUpdate"] == [True, True, False, False]


@requires_node
def test_the_kept_download_is_found_in_each_systems_own_folder(tmp_path, monkeypatch):
    body = b"the new app file"
    info = {"fileName": "Iron-Jarvis-x86_64.AppImage",
            "sha512": base64.b64encode(hashlib.sha512(body).digest()).decode("ascii")}
    for name in ("cache", "local"):
        pending = tmp_path / name / "iron-jarvis-desktop-updater" / "pending"
        pending.mkdir(parents=True)
        (pending / "update-info.json").write_text(json.dumps(info), encoding="utf-8")
    (tmp_path / "cache" / "iron-jarvis-desktop-updater" / "pending" / info["fileName"]).write_bytes(body)
    # Linux looks in the cache folder (whole file: ok); Windows in LOCALAPPDATA
    # (the marker with no file beside it: missing).
    env = {"XDG_CACHE_HOME": str(tmp_path / "cache"), "LOCALAPPDATA": str(tmp_path / "local")}
    assert _out(tmp_path, "linux", monkeypatch, **env)["cached"] == "ok"
    assert _out(tmp_path, "win32", monkeypatch, **env)["cached"] == "missing"


def test_every_linux_path_in_the_shell_sits_behind_the_system_check():
    src = _src()
    assert _decl("UPDATE_PLATFORM") == "const UPDATE_PLATFORM = process.platform;"
    # The new file leaves at once when started only to finish a Quit: before
    # anything is created, and on Linux only.
    guard = 'if (process.platform === "linux" && process.env.APPIMAGE_EXIT_AFTER_INSTALL === "true") {\n  process.exit(0);\n}'
    assert guard in src
    assert src.index(guard) < src.index('const windowState = require("./windowState");')
    # After "Restart to update": wait for the old app's lock, on Linux only.
    lock = src[src.index("const gotLock ="):src.index("if (!gotLock) {")]
    assert "app.requestSingleInstanceLock() ||" in lock
    assert 'process.platform === "linux" && process.env.APPIMAGE_SILENT_INSTALL === "true" && lockAfterUpdate(' in lock
    assert src.count("lockAfterUpdate(") == 2  # its definition and that one call
    # A copy that cannot replace itself has no updater (the page says "unsupported").
    init = src[src.index("function initUpdater()"):src.index("function checkForUpdates()")]
    assert 'if (UPDATE_PLATFORM === "linux" && !updateRehearsalUrl() && !linuxCanSelfUpdate()) return null;' in init
    # The rehearsal presses the button itself only when a rehearsal is on.
    assert re.search(
        r'if \(updateRehearsalUrl\(\) && process\.env\.IRONJARVIS_UPDATE_REHEARSAL_INSTALL === "restart"\) \{',
        init,
    )
    # No other place in the shell reads the rehearsal variables.
    assert src.count("IRONJARVIS_UPDATE_REHEARSAL_URL") == 1
    assert src.count("IRONJARVIS_UPDATE_REHEARSAL_INSTALL") == 1
