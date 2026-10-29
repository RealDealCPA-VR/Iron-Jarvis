"""v1.405.0 — open a file in VS Code / Cursor at a line (harness/code_editor,
daemon/routes/editor).

Pinned here:

* THE URL IS BUILT BY THE DAEMON: ``vscode://file/<path>:<line>:<col>``, the
  path with forward slashes and every character a URL would misread
  percent-encoded (space, ``#``, ``%``, ``?``, non-ASCII), the drive's colon
  kept, always a line and a column;
* THE PATH IS CHECKED BEFORE THE DISK SEES IT: a network share or a device
  path is refused before the file policy (which resolves paths) is asked;
  then absolute, the protected roots, the folder limits, an existing file;
* DETECTION reads ``HKEY_CLASSES_ROOT\\<scheme>`` through a FAKE registry
  here, needs the open command's program to exist, is cached, never raises,
  and is "not known" (None) off Windows;
* THE LAUNCHER IS SPIED (``routes.editor._launch_url``): called with the exact
  URL the daemon built, never with anything a client sent — and no real
  editor is ever opened by this file;
* THE SETTING ``code_editor`` (auto | vscode | cursor): a schema row, a
  tolerant Config field, read live by ``GET /editor/options``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.config import Config
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import editor as editor_routes
from iron_jarvis.harness import code_editor
from iron_jarvis.settings import schema

# --------------------------------------------------------------- fakes


class FakeKey:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeReg:
    """A tiny HKEY_CLASSES_ROOT: ``{scheme: {"url": bool, "command": str|None}}``."""

    HKEY_CLASSES_ROOT = "HKCR"

    def __init__(self, entries: dict[str, dict]) -> None:
        self.entries = entries
        self.opened: list[str] = []

    def OpenKey(self, parent, sub):  # noqa: N802 — winreg's spelling
        name = sub if parent == self.HKEY_CLASSES_ROOT else f"{parent.name}\\{sub}"
        self.opened.append(name)
        scheme = name.split("\\", 1)[0]
        e = self.entries.get(scheme)
        if e is None:
            raise FileNotFoundError(name)
        if "\\" in name and e.get("command") is None:
            raise FileNotFoundError(name)
        return FakeKey(name)

    def QueryValueEx(self, key, value):  # noqa: N802
        scheme = key.name.split("\\", 1)[0]
        e = self.entries[scheme]
        if value == "URL Protocol":
            if not e.get("url"):
                raise FileNotFoundError(value)
            return ("", 1)
        return (e["command"], 1)

    def CloseKey(self, key):  # noqa: N802
        pass


@pytest.fixture(autouse=True)
def _fresh_cache():
    code_editor.forget()
    yield
    code_editor.forget()


@pytest.fixture()
def programs(tmp_path):
    vs = tmp_path / "Microsoft VS Code" / "Code.exe"
    cu = tmp_path / "cursor" / "Cursor.exe"
    vs.parent.mkdir(parents=True)
    cu.parent.mkdir(parents=True)
    vs.write_bytes(b"MZ")
    cu.write_bytes(b"MZ")
    return vs, cu


def _reg(monkeypatch, entries):
    reg = FakeReg(entries)
    monkeypatch.setattr(code_editor, "_winreg", lambda: reg)
    return reg


# ------------------------------------------------------------------ URL

URL_TABLE = [
    (r"C:\Users\VR\proj\app.py", 12, 3, "vscode://file/C:/Users/VR/proj/app.py:12:3"),
    (r"C:\Users\VR\my proj\a b.py", 1, None, "vscode://file/C:/Users/VR/my%20proj/a%20b.py:1:1"),
    (r"C:\x\issue#12.py", 5, 1, "vscode://file/C:/x/issue%2312.py:5:1"),
    (r"C:\x\100%.txt", None, None, "vscode://file/C:/x/100%25.txt:1:1"),
    (r"C:\x\why?.md", 2, 2, "vscode://file/C:/x/why%3F.md:2:2"),
    (r"C:\Users\Zoë\café\naïve.py", 7, 1, "vscode://file/C:/Users/Zo%C3%AB/caf%C3%A9/na%C3%AFve.py:7:1"),
    (r"C:\x\日本.py", 1, 1, "vscode://file/C:/x/%E6%97%A5%E6%9C%AC.py:1:1"),
    ("/home/me/proj/app.py", 40, 1, "vscode://file/home/me/proj/app.py:40:1"),
    (r"D:\a;b&c=d+e,f@g!h'(i).py", 1, 1, "vscode://file/D:/a%3Bb%26c%3Dd%2Be%2Cf%40g%21h%27%28i%29.py:1:1"),
]


@pytest.mark.parametrize(("path", "line", "col", "url"), URL_TABLE)
def test_the_url_encodes_what_a_url_would_misread(path, line, col, url):
    assert code_editor.editor_url("vscode", path, line, col) == url


def test_cursor_uses_its_own_scheme_and_a_name_ending_in_digits_is_never_a_line():
    assert code_editor.editor_url("cursor", r"C:\x\a.py", 9, 4) == "cursor://file/C:/x/a.py:9:4"
    # Always a line and a column: "a:1" can never be read as line 1 of "a".
    assert code_editor.editor_url("vscode", "/srv/a:1") == "vscode://file/srv/a:1:1:1"


def test_an_unknown_editor_is_refused_when_building_a_url():
    with pytest.raises(code_editor.EditorRefusal) as e:
        code_editor.editor_url("notepad", r"C:\x\a.py")
    assert e.value.sentence == code_editor.UNKNOWN_EDITOR


# -------------------------------------------------------------- the path


def test_a_real_file_passes_and_comes_back_normalised(tmp_path):
    f = tmp_path / "src" / "app.py"
    f.parent.mkdir()
    f.write_text("x = 1\n", encoding="utf-8")
    got = code_editor.check_path(str(tmp_path / "src" / "." / "app.py"))
    assert got == Path(os.path.normpath(str(f)))


@pytest.mark.parametrize(
    ("raw", "sentence"),
    [
        ("", code_editor.NOT_ABSOLUTE),
        ("   ", code_editor.NOT_ABSOLUTE),
        (None, code_editor.NOT_ABSOLUTE),
        (42, code_editor.NOT_ABSOLUTE),
        ("src/app.py", code_editor.NOT_ABSOLUTE),
        ("vscode://file/C:/x/a.py:3", code_editor.NOT_ABSOLUTE),
        ("https://evil.example/a.py", code_editor.NOT_ABSOLUTE),
        ("C:a.py", code_editor.NOT_ABSOLUTE),
        ("C:\\x\\a\nb.py", code_editor.NOT_ABSOLUTE),
        ("C:\\" + "a" * 5000, code_editor.NOT_ABSOLUTE),
        (r"\\attacker\share\a.py", code_editor.NETWORK_PATH),
        ("//attacker/share/a.py", code_editor.NETWORK_PATH),
        (r"\\?\C:\Windows\win.ini", code_editor.NETWORK_PATH),
        (r"\\.\PhysicalDrive0", code_editor.NETWORK_PATH),
        (r"\\?\UNC\attacker\share\a.py", code_editor.NETWORK_PATH),
    ],
)
def test_refusals_are_one_plain_sentence(raw, sentence):
    with pytest.raises(code_editor.EditorRefusal) as e:
        code_editor.check_path(raw)
    assert e.value.sentence == sentence


def test_a_network_path_is_refused_before_the_file_policy_resolves_it(monkeypatch):
    from iron_jarvis.core import fs_policy

    touched: list[str] = []
    monkeypatch.setattr(fs_policy, "fs_read_ok", lambda p: touched.append(p) or (True, ""))
    monkeypatch.setattr(fs_policy, "is_protected_path", lambda p: touched.append(p) or False)
    monkeypatch.setattr(Path, "is_file", lambda self: touched.append(str(self)) or True)
    with pytest.raises(code_editor.EditorRefusal) as e:
        code_editor.check_path(r"\\attacker\share\app.py")
    assert e.value.status == 403
    assert touched == []


def test_a_missing_file_and_a_folder_are_refused(tmp_path):
    with pytest.raises(code_editor.EditorRefusal) as e:
        code_editor.check_path(str(tmp_path / "gone.py"))
    assert (e.value.sentence, e.value.status) == (code_editor.MISSING, 404)
    with pytest.raises(code_editor.EditorRefusal) as e:
        code_editor.check_path(str(tmp_path))
    assert e.value.sentence == code_editor.A_FOLDER


def test_a_protected_file_is_refused(tmp_path):
    key = tmp_path / ".secrets.key"
    key.write_bytes(b"k")
    with pytest.raises(code_editor.EditorRefusal) as e:
        code_editor.check_path(str(key))
    assert (e.value.sentence, e.value.status) == (code_editor.NOT_ALLOWED, 403)


def test_the_folder_limit_refuses_in_its_own_words(tmp_path, monkeypatch):
    from iron_jarvis.core import fs_policy

    inside = tmp_path / "allowed"
    inside.mkdir()
    outside = tmp_path / "other.py"
    outside.write_text("x", encoding="utf-8")
    monkeypatch.setenv("IRONJARVIS_FS_ALLOWLIST", str(inside))
    with pytest.raises(code_editor.EditorRefusal) as e:
        code_editor.check_path(str(outside))
    assert e.value.sentence == fs_policy.READ_REFUSAL_INSTALL
    assert e.value.status == 403


@pytest.mark.parametrize("bad", [0, -1, 10_000_001, True, False, "12", 3.5, [1]])
def test_a_line_must_be_a_whole_number_in_range(bad):
    with pytest.raises(code_editor.EditorRefusal) as e:
        code_editor.check_position(bad)
    assert e.value.sentence == code_editor.BAD_LINE


def test_line_and_column_ranges():
    assert code_editor.check_position(None) is None
    assert code_editor.check_position(1) == 1
    assert code_editor.check_position(12.0) == 12
    assert code_editor.check_position(10_000_000) == 10_000_000
    assert code_editor.check_position(100_000, column=True) == 100_000
    with pytest.raises(code_editor.EditorRefusal) as e:
        code_editor.check_position(100_001, column=True)
    assert e.value.sentence == code_editor.BAD_COLUMN


# ------------------------------------------------------------ detection


def test_detection_reads_the_url_protocol_and_needs_the_program(monkeypatch, programs):
    vs, cu = programs
    _reg(
        monkeypatch,
        {
            "vscode": {"url": True, "command": f'"{vs}" --open-url -- "%1"'},
            "cursor": {"url": True, "command": f'"{cu}" "--open-url" "--" "%1"'},
        },
    )
    assert code_editor.detect() == {"vscode": True, "cursor": True}


def test_a_stale_key_a_missing_url_protocol_or_no_key_is_not_installed(monkeypatch, tmp_path, programs):
    vs, _ = programs
    _reg(
        monkeypatch,
        {
            # Left behind by an uninstall: the program is gone.
            "vscode": {"url": True, "command": f'"{tmp_path / "gone" / "Code.exe"}" --open-url -- "%1"'},
            # A key that is not a URL protocol.
            "cursor": {"url": False, "command": f'"{vs}" "%1"'},
        },
    )
    assert code_editor.detect() == {"vscode": False, "cursor": False}
    code_editor.forget()
    _reg(monkeypatch, {})
    assert code_editor.detect() == {"vscode": False, "cursor": False}


def test_an_unquoted_command_names_its_program(monkeypatch, tmp_path):
    exe = tmp_path / "Code.exe"
    exe.write_bytes(b"MZ")
    _reg(monkeypatch, {"vscode": {"url": True, "command": f"{exe} --open-url -- %1"}})
    assert code_editor.detect()["vscode"] is True


def test_detection_never_raises(monkeypatch):
    class Broken(FakeReg):
        def OpenKey(self, parent, sub):  # noqa: N802
            raise RuntimeError("the registry is having a day")

    monkeypatch.setattr(code_editor, "_winreg", lambda: Broken({}))
    # An unexpected error is "not known", never a crash (and never a guess).
    assert code_editor.detect() == {"vscode": None, "cursor": None}
    code_editor.forget()

    def boom():
        raise RuntimeError("no registry")

    monkeypatch.setattr(code_editor, "_winreg", boom)
    assert code_editor.detect() == {"vscode": None, "cursor": None}


def test_off_windows_it_is_not_known(monkeypatch):
    monkeypatch.setattr(code_editor, "_winreg", lambda: None)
    assert code_editor.detect() == {"vscode": None, "cursor": None}
    opts = code_editor.options("auto")
    assert opts["preferred"] is None
    assert opts["known"] is False
    assert opts["note"] == code_editor.NOT_KNOWN_NOTE
    code_editor.forget()
    # The user's own choice stands when nothing is known.
    assert code_editor.options("cursor")["preferred"] == "cursor"


def test_the_answer_is_cached_for_a_few_minutes(monkeypatch, programs):
    vs, _ = programs
    reg = _reg(monkeypatch, {"vscode": {"url": True, "command": f'"{vs}" "%1"'}})
    code_editor.detect(now=100.0)
    n = len(reg.opened)
    code_editor.detect(now=100.0 + code_editor.CACHE_S - 1)
    assert len(reg.opened) == n
    code_editor.detect(now=100.0 + code_editor.CACHE_S + 1)
    assert len(reg.opened) > n


@pytest.mark.parametrize(
    ("setting", "installed", "pick"),
    [
        ("auto", {"vscode": True, "cursor": True}, "vscode"),
        ("auto", {"vscode": False, "cursor": True}, "cursor"),
        ("auto", {"vscode": False, "cursor": False}, None),
        ("auto", {"vscode": None, "cursor": None}, None),
        ("cursor", {"vscode": True, "cursor": True}, "cursor"),
        ("cursor", {"vscode": True, "cursor": False}, None),
        ("vscode", {"vscode": None, "cursor": None}, "vscode"),
        ("nonsense", {"vscode": False, "cursor": True}, "cursor"),
    ],
)
def test_the_preferred_editor(setting, installed, pick):
    assert code_editor.preferred(setting, installed) == pick


# --------------------------------------------------------------- routes


@pytest.fixture()
def client(tmp_path, monkeypatch, programs):
    vs, cu = programs
    _reg(
        monkeypatch,
        {
            "vscode": {"url": True, "command": f'"{vs}" --open-url -- "%1"'},
            "cursor": {"url": True, "command": f'"{cu}" "--open-url" "--" "%1"'},
        },
    )
    return TestClient(create_app(str(tmp_path / "home")))


@pytest.fixture()
def launched(monkeypatch):
    urls: list[str] = []
    monkeypatch.setattr(editor_routes, "_launch_url", lambda url: urls.append(url))
    return urls


@pytest.fixture()
def source(tmp_path):
    f = tmp_path / "my proj" / "src" / "cart #2.py"
    f.parent.mkdir(parents=True)
    f.write_text("a = 1\n" * 50, encoding="utf-8")
    return f


def test_get_options_names_the_editors_and_the_pick(client):
    body = client.get("/editor/options").json()
    assert body["editors"] == [
        {"id": "vscode", "label": "VS Code", "installed": True},
        {"id": "cursor", "label": "Cursor", "installed": True},
    ]
    assert (body["preferred"], body["preferred_label"], body["setting"]) == ("vscode", "VS Code", "auto")


def test_open_hands_the_os_the_url_the_daemon_built(client, launched, source):
    r = client.post("/editor/open", json={"path": str(source), "line": 12, "column": 3})
    assert r.status_code == 200, r.text
    expected = code_editor.editor_url("vscode", os.path.normpath(str(source)), 12, 3)
    assert launched == [expected]
    assert "my%20proj" in expected and "cart%20%232.py:12:3" in expected
    assert r.json() == {"ok": True, "editor": "vscode", "label": "VS Code", "line": 12}


def test_open_with_cursor_named(client, launched, source):
    r = client.post("/editor/open", json={"path": str(source), "line": 2, "editor": "cursor"})
    assert r.status_code == 200, r.text
    assert launched[0].startswith("cursor://file/")
    assert launched[0].endswith(":2:1")


def test_a_client_url_is_never_launched(client, launched, source):
    for body in (
        {"path": "vscode://file/C:/Windows/System32/calc.exe"},
        {"path": "https://evil.example/x"},
        {"path": "javascript:alert(1)"},
        {"path": r"\\attacker\share\x.py"},
    ):
        r = client.post("/editor/open", json=body)
        assert r.status_code in (400, 403), (body, r.text)
        assert isinstance(r.json()["detail"], str)
    # An extra "url" key is ignored: the URL is built from the path.
    r = client.post("/editor/open", json={"path": str(source), "url": "vscode://file/C:/evil.py"})
    assert r.status_code == 200, r.text
    assert launched == [code_editor.editor_url("vscode", os.path.normpath(str(source)))]
    assert all("evil" not in u for u in launched)


def test_refusals_from_the_route_are_plain_sentences(client, launched, source, tmp_path):
    cases = [
        ({"path": "src/a.py"}, 400, code_editor.NOT_ABSOLUTE),
        ({"path": str(tmp_path / "nope.py")}, 404, code_editor.MISSING),
        ({"path": str(source), "line": 0}, 400, code_editor.BAD_LINE),
        ({"path": str(source), "line": "12"}, 400, code_editor.BAD_LINE),
        ({"path": str(source), "column": -2}, 400, code_editor.BAD_COLUMN),
        ({"path": str(source), "editor": "notepad"}, 400, code_editor.UNKNOWN_EDITOR),
    ]
    for body, status, sentence in cases:
        r = client.post("/editor/open", json=body)
        assert (r.status_code, r.json()["detail"]) == (status, sentence), body
    assert launched == []


def test_an_editor_not_installed_or_none_at_all(client, launched, source, monkeypatch, programs):
    vs, _ = programs
    code_editor.forget()
    _reg(monkeypatch, {"vscode": {"url": True, "command": f'"{vs}" "%1"'}})
    r = client.post("/editor/open", json={"path": str(source), "editor": "cursor"})
    assert (r.status_code, r.json()["detail"]) == (409, "Cursor is not installed on this PC.")
    code_editor.forget()
    _reg(monkeypatch, {})
    r = client.post("/editor/open", json={"path": str(source)})
    assert (r.status_code, r.json()["detail"]) == (409, code_editor.NO_EDITOR)
    assert launched == []
    assert client.get("/editor/options").json()["preferred"] is None


def test_a_launch_the_os_refuses_is_said_plainly(client, monkeypatch, source):
    def boom(url):
        raise OSError("no association")

    monkeypatch.setattr(editor_routes, "_launch_url", boom)
    r = client.post("/editor/open", json={"path": str(source)})
    assert r.status_code == 422
    assert r.json()["detail"] == "Iron Jarvis could not open VS Code. Open VS Code once by itself, then try again."


# -------------------------------------------------------------- setting


def test_the_setting_row_and_config_field():
    d = schema.get("code_editor")
    assert (d.type, d.group, d.tier) == ("enum", "automation", "allow")
    assert [v for v, _ in d.options] == ["auto", "vscode", "cursor"]
    assert "code_editor" in schema.daemon_keys()
    assert Config.model_fields["code_editor"].default == "auto"


def test_config_reads_an_unknown_value_as_auto(tmp_path):
    cfg = Config(home=tmp_path, project_root=tmp_path, code_editor="sublime")
    assert cfg.code_editor == "auto"
    cfg.code_editor = " Cursor "
    assert cfg.code_editor == "cursor"


def test_changing_the_setting_changes_the_pick_live(client, launched, source):
    r = client.put("/settings", json={"values": {"code_editor": "cursor"}})
    assert r.status_code == 200, r.text
    assert client.get("/editor/options").json()["preferred"] == "cursor"
    assert client.post("/editor/open", json={"path": str(source)}).status_code == 200
    assert launched[0].startswith("cursor://file/")
    bad = client.put("/settings", json={"values": {"code_editor": "notepad"}})
    assert bad.status_code >= 400
    assert client.get("/editor/options").json()["setting"] == "cursor"


# ------------------------------------------------------ review fixes (v1.405.0)


def _symlink_or_skip(target: str, link: Path, *, folder: bool = False) -> None:
    try:
        os.symlink(target, str(link), target_is_directory=folder)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover — no symlink right
        pytest.skip(f"this PC cannot make symlinks: {exc}")


def _spy_resolvers(monkeypatch) -> list[str]:
    """Every way the old check reached a link's TARGET: the file policy (which
    resolves), ``Path.resolve``, ``os.path.realpath``, ``Path.is_file`` /
    ``is_dir`` (which follow links)."""
    from iron_jarvis.core import fs_policy

    touched: list[str] = []
    monkeypatch.setattr(fs_policy, "fs_read_ok", lambda p: touched.append(f"fs_read_ok {p}") or (True, ""))
    monkeypatch.setattr(fs_policy, "is_protected_path", lambda p: touched.append(f"protected {p}") or False)
    monkeypatch.setattr(Path, "resolve", lambda self, strict=False: touched.append(f"resolve {self}") or self)
    monkeypatch.setattr(os.path, "realpath", lambda p, *a, **k: touched.append(f"realpath {p}") or p)
    monkeypatch.setattr(Path, "is_file", lambda self: touched.append(f"is_file {self}") or True)
    monkeypatch.setattr(Path, "is_dir", lambda self: touched.append(f"is_dir {self}") or False)
    return touched


@pytest.mark.parametrize("shape", ["file link", "folder link on the way", "a chain of links"])
def test_a_link_into_a_network_share_is_refused_before_anything_follows_it(tmp_path, monkeypatch, shape):
    # TEST-NET-1 (192.0.2.1): nothing answers there, and nothing here may try.
    share = r"\\192.0.2.1\share"
    if shape == "file link":
        path = tmp_path / "app.py"
        _symlink_or_skip(share + r"\app.py", path)
    elif shape == "folder link on the way":
        _symlink_or_skip(share, tmp_path / "src", folder=True)
        path = tmp_path / "src" / "app.py"
    else:
        _symlink_or_skip(share + r"\app.py", tmp_path / "far.py")
        _symlink_or_skip(str(tmp_path / "far.py"), tmp_path / "near.py")
        path = tmp_path / "near.py"
    touched = _spy_resolvers(monkeypatch)
    with pytest.raises(code_editor.EditorRefusal) as e:
        code_editor.check_path(str(path))
    assert (e.value.sentence, e.value.status) == (code_editor.NETWORK_PATH, 403)
    assert touched == [], touched


def test_a_long_path_unc_target_is_a_network_target_and_a_drive_is_not():
    assert code_editor._network_target(r"\\?\UNC\host\share\a.py")
    assert code_editor._network_target(r"\??\UNC\host\share")
    assert code_editor._network_target(r"\\host\share")
    assert code_editor._network_target(r"\\.\pipe\x")
    assert code_editor._network_target(r"\\?\Volume{1234}\a.py")
    assert not code_editor._network_target(r"\\?\C:\Users\me\a.py")
    assert not code_editor._network_target(r"C:\Users\me\a.py")
    assert not code_editor._network_target("sibling.py")


def test_a_local_link_still_opens(tmp_path):
    real = tmp_path / "real.py"
    real.write_text("x = 1\n", encoding="utf-8")
    _symlink_or_skip(str(real), tmp_path / "link.py")
    _symlink_or_skip("real.py", tmp_path / "rel.py")
    assert code_editor.check_path(str(tmp_path / "link.py")) == Path(os.path.normpath(str(tmp_path / "link.py")))
    assert code_editor.check_path(str(tmp_path / "rel.py")) == Path(os.path.normpath(str(tmp_path / "rel.py")))


def test_a_code_workspace_file_is_refused(tmp_path):
    ws = tmp_path / "shop.code-workspace"
    ws.write_text('{"folders": [], "tasks": {}}', encoding="utf-8")
    for raw in (str(ws), str(tmp_path / "SHOP.Code-Workspace")):
        with pytest.raises(code_editor.EditorRefusal) as e:
            code_editor.check_path(raw)
        assert (e.value.sentence, e.value.status) == (code_editor.WORKSPACE_FILE, 403)
    # A link with an innocent name that leads to a workspace file.
    _symlink_or_skip(str(ws), tmp_path / "notes.txt")
    with pytest.raises(code_editor.EditorRefusal) as e:
        code_editor.check_path(str(tmp_path / "notes.txt"))
    assert e.value.sentence == code_editor.WORKSPACE_FILE


def test_the_handbook_says_vscode_asks_the_first_time():
    text = (Path(__file__).resolve().parents[1] / "docs" / "HANDBOOK.md").read_bytes().decode("utf-8")
    part = text.split("## Reviewing a coding session's changes, and opening them in your editor", 1)[1]
    part = part.split("\n## ", 1)[0].replace("\r\n", "\n")
    assert "VS Code shows its own confirmation" in " ".join(part.split())


# -------------------------- a settled step keeps its file (the saved detail)

#: The fixture the dashboard reads (review-pane-v1405.test.tsx): what the REAL
#: ``chat_turn.step_detail`` saves for each file step, and the file a press
#: must open. Regenerate after an intended change with
#: ``IJ_REGEN_EDITOR_STEPS=1 uv run pytest tests/test_code_editor_v1405.py``.
STEP_FIXTURE = (
    Path(__file__).resolve().parents[1] / "dashboard" / "__tests__" / "fixtures" / "editor-step-inputs-v1405.json"
)

_LONG = "\n".join(f"    total = total + item_{i}.price * item_{i}.qty" for i in range(60))
STEP_CASES = [
    ("write_file", {"path": "src/shop/cart.py", "content": _LONG}, "src/shop/cart.py", None),
    (
        "edit_file",
        {"path": "src/shop/cart.py", "old": _LONG[:900], "new": _LONG[:900].replace("total", "sum"), "replace_all": False},
        "src/shop/cart.py",
        None,
    ),
    (
        "multi_edit",
        {"path": "src/shop/tax.py", "edits": [{"old": _LONG[:500], "new": "x"}, {"new": _LONG[:400], "old": "y"}]},
        "src/shop/tax.py",
        None,
    ),
    (
        "notebook_edit",
        {"path": "notebooks/report.ipynb", "cell_index": 3, "edit_mode": "replace", "new_source": _LONG, "cell_type": "code"},
        "notebooks/report.ipynb",
        None,
    ),
    ("read_file", {"path": "src/shop/cart.py", "offset": 40, "limit": 20}, "src/shop/cart.py", 40),
]


def _step_inputs(tmp_path) -> list[dict]:
    from iron_jarvis.daemon.chat_turn import STEP_DETAIL_INPUT_CHARS, redacted_args, step_detail

    platform = create_app(str(tmp_path / "home")).state.platform
    rows = []
    for name, args, path, line in STEP_CASES:
        assert platform.registry.get(name) is not None, f"anti-vacuity: {name} is a real tool"
        detail = step_detail(name, redacted_args(platform.registry, name, args), "ok")
        assert len(detail["input"]) <= STEP_DETAIL_INPUT_CHARS
        rows.append({"name": name, "input": detail["input"], "path": path, "line": line})
    return rows


def test_a_settled_file_step_keeps_its_path_inside_the_saved_detail(tmp_path):
    import json

    from iron_jarvis.daemon.chat_turn import PATH_FIRST_TOOLS, STEP_DETAIL_INPUT_CHARS

    rows = _step_inputs(tmp_path)
    for row, (name, args, path, _line) in zip(rows, STEP_CASES, strict=True):
        assert f'"path": {json.dumps(path)}' in row["input"], row
        if name in PATH_FIRST_TOOLS:
            # Anti-vacuity: sorted keys WOULD have cut the path off.
            sorted_line = json.dumps(args, ensure_ascii=False, sort_keys=True)
            assert '"path"' not in sorted_line[:STEP_DETAIL_INPUT_CHARS], name
    if os.environ.get("IJ_REGEN_EDITOR_STEPS") == "1":
        STEP_FIXTURE.write_bytes((json.dumps(rows, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    assert json.loads(STEP_FIXTURE.read_bytes().decode("utf-8")) == rows, (
        "the fixture is stale: IJ_REGEN_EDITOR_STEPS=1 uv run pytest tests/test_code_editor_v1405.py"
    )


def test_every_other_tools_saved_detail_is_byte_identical():
    import json

    from iron_jarvis.daemon.chat_turn import STEP_DETAIL_INPUT_CHARS, prior_tool_result_entry, step_detail

    for name, args in [
        ("shell", {"command": "pytest -q", "cwd": "src", "timeout_s": 60}),
        ("web_fetch", {"url": "https://example.com/a", "max_chars": 4000}),
        ("apply_patch", {"patch": "*** Begin Patch\n*** Update File: a.py\n" + _LONG}),
        ("glob", {"pattern": "**/*.py", "path": "src"}),
        ("t", {"z": 1, "a": {"y": 2, "b": 3}, "path": "p"}),
    ]:
        want = json.dumps(args, default=str, ensure_ascii=False, sort_keys=True)
        assert step_detail(name, args, "")["input"] == want[:STEP_DETAIL_INPUT_CHARS], name
        assert prior_tool_result_entry(name, args, True, "")["args_summary"] == want[: len(
            prior_tool_result_entry(name, args, True, "")["args_summary"]
        )]
    # A file tool's nested values keep their sorted keys; the escalation row
    # reads the same line as the step (they never disagree).
    args = {"path": "a.py", "edits": [{"old": "o", "new": "n", "replace_all": True}]}
    line = step_detail("multi_edit", args, "")["input"]
    assert line == '{"path": "a.py", "edits": [{"new": "n", "old": "o", "replace_all": true}]}'
    assert prior_tool_result_entry("multi_edit", args, True, "")["args_summary"] == line


# ---------------- a binary file says its size (the Reviewed mark hashes it)


def test_a_binary_change_carries_its_size_in_both_changes_views(tmp_path):
    import subprocess

    from iron_jarvis.harness import branches
    from iron_jarvis.platform import build_platform
    from tests.test_chat_changes_v1328 import _client, _journal, _post, _since

    # This session (POST /chat/changes): the size of the file NOW.
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "work"
    ws.mkdir()
    (ws / "pic.bin").write_bytes(b"\x89PNG\x00\x01after!!")
    since = _since()
    _journal(platform, workspace=ws, path="pic.bin", prior=b"\x89PNG\x00\x01before", mode="raw")
    (c,) = _post(_client(platform), since=since)["changes"]
    assert (c["binary"], c["size"]) == (True, len(b"\x89PNG\x00\x01after!!"))

    # Branch (a new binary file, and a committed binary change).
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-c", "core.autocrlf=false", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], cwd=repo, check=True)
    (repo / "logo.png").write_bytes(b"\x89PNG\x00" + b"a" * 10)
    subprocess.run([*git, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "one"], cwd=repo, check=True)
    base = subprocess.run([*git, "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    (repo / "logo.png").write_bytes(b"\x89PNG\x00" + b"b" * 30)
    subprocess.run([*git, "commit", "-q", "-am", "two"], cwd=repo, check=True)
    (repo / "new.bin").write_bytes(b"\x00\x01\x02" * 4)
    got = branches.branch_changes({"worktree": str(repo), "base_commit": base})
    rows = {r["rel"]: r for r in got["changes"]}
    assert (rows["logo.png"]["binary"], rows["logo.png"]["size"]) == (True, 35)
    assert (rows["new.bin"]["binary"], rows["new.bin"]["size"]) == (True, 12)
