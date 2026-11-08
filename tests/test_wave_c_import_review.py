"""Wave C (item 30) REVIEW pins — the import door's read-only and
names-only guarantees, driven adversarially against the four new sources.

What the review found and fixed:

* The PREVIEW row masked each argument on its own (``mask``), so the value
  after a secret-named flag (``--api-key VALUE``, ``--token VALUE``,
  ``--password VALUE``) showed in the clear — the shape Grok's, OpenCode's
  and Pi's configs carry for ``mcp-remote``-style servers. The row now goes
  through the SAME ``split_args`` the add stores.
* A ``?key=`` query parameter on a pack's address showed in the preview AND
  was stored in the clear in ``config.toml`` (``mask`` knows ``api_key`` but
  not a bare ``key``); ``split_url`` now masks a credential-named parameter.
* The OpenCode PROJECT reader read ``<project>/config.json`` (and
  ``.opencode/config.json``), a name OpenCode reads only in its GLOBAL
  folder — a project's unrelated ``config.json`` was listed under "OpenCode
  (this project)".

And what it checked holds: a scan writes nothing and makes no folder; a
``{file:PATH}`` placeholder naming a share is never looked at.
"""

from __future__ import annotations

import builtins
import hashlib
import io
import json
import os
import pathlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Project
from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import antigravity as hosted_agy
from iron_jarvis.hosted import grok as hosted_grok
from iron_jarvis.mcp import import_sources
from iron_jarvis.mcp.import_config import PackSpec, parse_any
from iron_jarvis.mcp.secrets import split_url
from iron_jarvis.providers import opencode as providers_opencode

NO_SUCH = "ij-no-such-mcp-launcher-wave-c-review"
ARG = "argsecret-wave-c-BBBB2222"
QUERY_KEY = "qksecret-wave-c-DDDD4444"
ENV = "envsecret-wave-c-AAAA1111"


@pytest.fixture
def homes(tmp_path, monkeypatch) -> dict[str, Path]:
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("USERPROFILE", str(user))
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    for var in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_CONFIG_HOME", "CLAUDE_CODE_PLUGIN_CACHE_DIR",
                "GROK_HOME", "PI_CODING_AGENT_DIR", "OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR"):
        monkeypatch.delenv(var, raising=False)
    opencode_dir = user / ".config" / "opencode"
    grok_home = user / ".grok"
    agy_home = user / ".gemini" / "antigravity-cli"
    pi_dir = user / ".pi" / "agent"
    monkeypatch.setenv(providers_opencode.CONFIG_DIR_ENV, str(opencode_dir))
    monkeypatch.setenv(hosted_grok.HOME_ENV, str(grok_home))
    monkeypatch.setenv(hosted_agy.HOME_ENV, str(agy_home))
    monkeypatch.setenv(import_sources.PI_IMPORT_DIR_ENV, str(pi_dir))
    return {"user": user, "opencode": opencode_dir, "grok": grok_home, "agy": agy_home, "pi": pi_dir,
            "agy_config": user / ".gemini" / "config"}


@pytest.fixture
def client(tmp_path, homes):
    return TestClient(create_app(str(tmp_path / "ij")))


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _rows(client) -> dict[str, dict]:
    res = client.get("/mcp/import/sources")
    assert res.status_code == 200, res.text
    return {r["source"]: r for r in res.json()["sources"]}


def _activate_project(platform, root: Path) -> None:
    with session_scope(platform.engine) as db:
        db.add(Project(id="project-wave-c-review", name="Import review", root=str(root), status="active"))
        db.commit()
    platform.config.active_project_id = "project-wave-c-review"


# --------------------------------------------------------------------------- #
# Names only: a value after a secret flag, a ?key= on the address
# --------------------------------------------------------------------------- #
def test_the_preview_and_the_stored_config_hide_a_value_after_a_secret_flag_and_a_key_parameter(client, homes):
    _write(homes["opencode"] / "opencode.json", json.dumps({"mcp": {
        "oc-flag": {"type": "local", "command": [NO_SUCH, "--api-key", ARG], "environment": {"GITHUB_TOKEN": ENV}},
        "oc-url": {"type": "remote", "url": f"https://h.example/mcp?key={QUERY_KEY}"},
    }}))
    _write(homes["grok"] / "config.toml", f'[mcp_servers.gk]\ncommand = "{NO_SUCH}"\nargs = ["--token", "{ARG}"]\n')
    _write(homes["pi"] / "mcp.json", json.dumps({"mcpServers": {"pi1": {"command": NO_SUCH, "args": ["--password", ARG]}}}))
    res = client.get("/mcp/import/sources")
    assert res.status_code == 200
    for value in (ARG, QUERY_KEY, ENV):
        assert value not in res.text, f"a value reached the preview: {value}"
    rows = _rows(client)
    servers = {s["name"]: s for key in ("opencode", "grok", "pi") for s in rows[key]["servers"]}
    assert servers["oc-flag"]["args"] == ["--api-key", "***"]
    assert servers["gk"]["args"] == ["--token", "***"]
    assert servers["pi1"]["args"] == ["--password", "***"]
    assert servers["oc-url"]["url"] == "https://h.example/mcp?key=***"
    platform = client.app.state.platform
    for source, names in (("opencode", ["oc-flag", "oc-url"]), ("grok", ["gk"]), ("pi", ["pi1"])):
        applied = client.post("/mcp/import/apply", json={"source": source, "names": names})
        assert applied.status_code == 200, applied.text
        for value in (ARG, QUERY_KEY, ENV):
            assert value not in applied.text
    stored = json.dumps(platform.config.mcp_servers)
    for value in (ARG, QUERY_KEY, ENV):
        assert value not in stored, f"a value reached the stored config: {value}"
    by_name = {s["name"]: s for s in platform.config.mcp_servers}
    assert by_name["oc-url"]["url"] == "https://h.example/mcp?key=***" and by_name["oc-url"]["url_secret"]
    assert platform.secrets.get(by_name["oc-url"]["url_secret"]) == f"https://h.example/mcp?key={QUERY_KEY}"
    for name in ("oc-flag", "gk", "pi1"):
        assert by_name[name]["args"][1] == "***" and by_name[name]["arg_secrets"] == {"1": f"mcp.{name}.arg.1"}
        assert platform.secrets.get(f"mcp.{name}.arg.1") == ARG
    assert platform.secrets.get("mcp.oc-flag.GITHUB_TOKEN") == ENV
    config_file = Path(platform.config.home) / "config.toml" if getattr(platform.config, "home", None) else None
    if config_file is not None and config_file.exists():
        text = config_file.read_text(encoding="utf-8", errors="replace")
        for value in (ARG, QUERY_KEY, ENV):
            assert value not in text


def test_split_url_masks_a_credential_named_query_parameter_and_keeps_the_rest():
    shown, real = split_url(f"https://h.example/mcp?key={QUERY_KEY}&v=2")
    assert shown == "https://h.example/mcp?key=***&v=2" and real == f"https://h.example/mcp?key={QUERY_KEY}&v=2"
    # Case-blind, a later parameter, and a fragment left in place.
    shown, real = split_url("https://h.example/mcp?v=2&KEY=abc#frag")
    assert shown == "https://h.example/mcp?v=2&KEY=***#frag" and real
    # Not a credential: a plain parameter, and a key-word inside the path.
    shown, real = split_url("https://h.example/key/mcp?keyboard=qwerty&monkey=1")
    assert shown == "https://h.example/key/mcp?keyboard=qwerty&monkey=1" and real == ""
    # Idempotent: a masked address is left as it is (no second vault copy).
    shown, real = split_url("https://h.example/mcp?key=***")
    assert shown == "https://h.example/mcp?key=***" and real == ""


def test_the_preview_row_is_the_adds_own_split():
    spec = PackSpec(name="x", command="npx", args=["-y", "mcp-remote", "--api-key", ARG],
                    url="", env={})
    row = spec.preview_row()
    assert row["args"] == ["-y", "mcp-remote", "--api-key", "***"]
    http = PackSpec(name="y", transport="http", url=f"https://me:pw@h.example/mcp?key={QUERY_KEY}")
    assert http.preview_row()["url"] == "https://me:***@h.example/mcp?key=***"


# --------------------------------------------------------------------------- #
# Read-only: a scan changes nothing on disk and makes no folder
# --------------------------------------------------------------------------- #
def _tree(root: Path) -> dict[str, tuple[int, int, str]]:
    out: dict[str, tuple[int, int, str]] = {}
    for p in root.rglob("*"):
        try:
            st = p.lstat()
        except OSError:
            continue
        digest = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else ""
        out[str(p)] = (st.st_size, st.st_mtime_ns, digest)
    return out


def test_a_scan_changes_nothing_on_disk_makes_no_folder_and_opens_no_file_for_writing(client, homes, monkeypatch):
    _write(homes["opencode"] / "opencode.jsonc", json.dumps({"mcp": {"a": {"type": "local", "command": [NO_SUCH]}}}))
    _write(homes["grok"] / "config.toml", f'[mcp_servers.g]\ncommand = "{NO_SUCH}"\n')
    # Pi's folder and Antigravity's config folder do NOT exist: a scan must not make them.
    assert not homes["pi"].exists() and not homes["agy_config"].exists()
    before = _tree(homes["user"])
    writes: list[tuple[str, str]] = []
    made: list[str] = []
    real_open, real_io_open = builtins.open, io.open

    def spy_open(file, mode="r", *a, **k):
        if any(c in str(mode) for c in "wax+"):
            writes.append((str(file), str(mode)))
        return real_open(file, mode, *a, **k)

    def spy_io_open(file, mode="r", *a, **k):
        if any(c in str(mode) for c in "wax+"):
            writes.append((str(file), str(mode)))
        return real_io_open(file, mode, *a, **k)

    real_mkdir, real_makedirs, real_path_mkdir = os.mkdir, os.makedirs, pathlib.Path.mkdir
    # A context of its own: the fixture's env patches must outlive the spies.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(builtins, "open", spy_open)
        mp.setattr(io, "open", spy_io_open)
        mp.setattr(os, "mkdir", lambda p, *a, **k: (made.append(str(p)), real_mkdir(p, *a, **k))[1])
        mp.setattr(os, "makedirs", lambda p, *a, **k: (made.append(str(p)), real_makedirs(p, *a, **k))[1])
        mp.setattr(pathlib.Path, "mkdir",
                   lambda self, *a, **k: (made.append(str(self)), real_path_mkdir(self, *a, **k))[1])
        out = import_sources.scan(client.app.state.platform)
    assert {r.source for r in out} >= {"opencode", "grok", "antigravity", "pi"}
    assert writes == [], "a scan opened a file for writing"
    assert made == [], "a scan made a folder"
    assert _tree(homes["user"]) == before, "a scan changed a file"
    assert not homes["pi"].exists() and not homes["agy_config"].exists()


def test_a_file_placeholder_naming_a_share_is_a_fill_and_is_never_looked_at(client, homes, monkeypatch):
    share = r"{file:\\evil-host\share\token}"
    _write(homes["pi"] / "mcp.json", json.dumps({"mcpServers": {"p": {"command": NO_SUCH, "env": {"PI_SECRET": share}}}}))
    looked: list[str] = []
    real_stat, real_lstat = os.stat, os.lstat

    def spy_stat(p, *a, **k):
        if str(p).startswith("\\\\"):
            looked.append(str(p))
        return real_stat(p, *a, **k)

    def spy_lstat(p, *a, **k):
        if str(p).startswith("\\\\"):
            looked.append(str(p))
        return real_lstat(p, *a, **k)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "stat", spy_stat)
        mp.setattr(os, "lstat", spy_lstat)
        rows = _rows(client)
        [spec] = parse_any(json.dumps({"mcp": {"q": {"type": "remote", "url": "https://x.example/mcp",
                                                       "headers": {"Authorization": f"Bearer {share}"}}}}))
    assert looked == [], "a {file:...} path was looked at on disk"
    [row] = rows["pi"]["servers"]
    assert row["asks"] == ["PI_SECRET"] and row["secret_keys"] == ["PI_SECRET"]
    assert spec.headers == {"Authorization": ""} and share in spec.fills[0]["template"]


# --------------------------------------------------------------------------- #
# OpenCode reads each place the way OpenCode itself does
# --------------------------------------------------------------------------- #
def test_a_projects_own_config_json_is_not_opencodes_but_the_global_folders_is(client, homes, tmp_path):
    # GLOBAL: OpenCode merges config.json beside opencode.json (control).
    _write(homes["opencode"] / "opencode.json", json.dumps({"mcp": {"global-one": {"type": "local", "command": [NO_SUCH]}}}))
    _write(homes["opencode"] / "config.json", json.dumps({"mcp": {"global-two": {"type": "local", "command": [NO_SUCH]}}}))
    # ...but never a ``.opencode`` sub-folder of its config folder.
    _write(homes["opencode"] / ".opencode" / "opencode.json",
           json.dumps({"mcp": {"not-a-place": {"type": "local", "command": [NO_SUCH]}}}))
    # PROJECT: a repository's unrelated config.json (another program's) that
    # happens to hold a server map.
    root = tmp_path / "repo"
    _write(root / "opencode.json", json.dumps({"mcp": {"proj-real": {"type": "local", "command": [NO_SUCH]}}}))
    _write(root / ".opencode" / "opencode.jsonc", json.dumps({"mcp": {"proj-dot": {"type": "local", "command": [NO_SUCH]}}}))
    _write(root / "config.json", json.dumps({"mcpServers": {"not-opencodes": {"command": NO_SUCH}}}))
    _write(root / ".opencode" / "config.json", json.dumps({"mcpServers": {"not-opencodes-either": {"command": NO_SUCH}}}))
    _activate_project(client.app.state.platform, root)
    rows = _rows(client)
    assert sorted(s["name"] for s in rows["opencode"]["servers"]) == ["global-one", "global-two"]
    assert sorted(s["name"] for s in rows["opencode-project"]["servers"]) == ["proj-dot", "proj-real"]
    # The same when the only project file is the .opencode one (the row's
    # path is then inside .opencode, and the reader still looks up to the root).
    (root / "opencode.json").unlink()
    rows = _rows(client)
    assert rows["opencode-project"]["origin"] == str(root / ".opencode" / "opencode.jsonc")
    assert [s["name"] for s in rows["opencode-project"]["servers"]] == ["proj-dot"]


# --------------------------------------------------------------------------- #
# Item 28: the read-back is bounded, and a probe that raises is worded
# --------------------------------------------------------------------------- #
def test_read_back_hands_a_network_base_the_budget_and_words_a_probe_that_raises(client, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from iron_jarvis.daemon.routes import knowledge as K

    vault = tmp_path / "vault"
    vault.mkdir()
    res = client.post("/ltm/sources", json={"name": "netty", "kind": "markdown", "path": str(vault)})
    assert res.status_code == 200, res.text
    platform = client.app.state.platform
    seen: list[float] = []

    class Network:
        def cached_health(self):
            return None

        def invalidate_health(self):
            pass

        def health(self, timeout=None):
            seen.append(float(timeout))
            return {"available": True, "detail": ""}

    class Raising:
        def health(self):
            raise ConnectionError("the box is off")

    real_get = platform.ltm.get
    monkeypatch.setattr(platform.ltm, "get", lambda name: Network() if name == "netty" else real_get(name))
    back = K.read_back(SimpleNamespace(platform=platform), "netty")
    assert back["answering"] is True and seen and 0 < seen[0] <= K._LTM_PROBE_BUDGET == 6.0
    monkeypatch.setattr(platform.ltm, "get", lambda name: Raising() if name == "netty" else real_get(name))
    back = K.read_back(SimpleNamespace(platform=platform), "netty")
    assert back["answering"] is False
    assert back["words"] == "Added, but it is not answering: ConnectionError: the box is off."
    # The row the add made is still there: the probe never costs the record.
    assert "netty" in client.get("/ltm/sources").json()["active"]
