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
