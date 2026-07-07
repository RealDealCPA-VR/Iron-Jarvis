"""Creative Studio: CLI launch flow, chat relay, tail, mkdir."""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


def test_studio_start_rejects_unknown_and_missing_cli(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.post(
        "/creative/studio/start", json={"cli": "not-a-cli", "cwd": str(tmp_path)}
    )
    assert r.status_code == 404
    r = client.post(
        "/creative/studio/start", json={"cli": "claude", "cwd": "relative/dir"}
    )
    assert r.status_code == 400


def test_studio_say_and_tail_relay_into_a_real_terminal(tmp_path):
    """Drive a plain shell terminal through the studio relay endpoints — the
    same write/tail path the CLI session uses, without launching any AI CLI."""
    client = TestClient(create_app(str(tmp_path)))
    term = client.post("/terminals", json={"cwd": str(tmp_path)})
    assert term.status_code == 200
    tid = term.json()["id"]
    try:
        r = client.post(
            f"/creative/studio/{tid}/say",
            json={
                "text": "echo studio-brief-check",
                "first": True,
                "skill": "pixio-story",
                "save_dir": str(tmp_path),
            },
        )
        assert r.status_code == 200 and r.json()["typed"] is True
        # The composed FIRST message embeds the brief around the user text.
        assert r.json()["chars"] > len("echo studio-brief-check")

        t = client.get(f"/creative/studio/{tid}/tail")
        assert t.status_code == 200
        body = t.json()
        assert set(body) == {"tail", "alive", "exit_code", "mode", "automode", "ready"}
        assert isinstance(body["tail"], str)

        # Newlines are flattened so a multi-line brief can't submit early.
        r2 = client.post(
            f"/creative/studio/{tid}/say", json={"text": "line one\nline two"}
        )
        assert r2.status_code == 200

        empty = client.post(f"/creative/studio/{tid}/say", json={"text": "   "})
        assert empty.status_code == 400
    finally:
        client.delete(f"/terminals/{tid}")

    gone = client.post(f"/creative/studio/{tid}/say", json={"text": "hi"})
    assert gone.status_code in (404, 409)


def test_latest_claude_mode_detection():
    from iron_jarvis.daemon.routes.creative import _AUTO_MODES, latest_claude_mode

    assert latest_claude_mode("booting…\n? for shortcuts") is None
    # Current Claude banners.
    assert latest_claude_mode("x accept edits on y") == "accept edits on"
    assert latest_claude_mode("auto mode on") == "auto mode on"
    assert latest_claude_mode("manual mode on") == "manual mode on"
    # The TUI repaints the banner each Shift+Tab — the LATEST occurrence wins.
    cycled = "accept edits on ... plan mode on"
    assert latest_claude_mode(cycled) == "plan mode on"
    back = "plan mode on ......... auto mode on"
    assert latest_claude_mode(back) == "auto mode on"
    # Overlap: "accept edits on" is a substring of the older "auto-accept edits
    # on" alias — the longer banner must win, not the prefix inside it.
    assert latest_claude_mode("auto-accept edits on") == "auto-accept edits on"
    # Only hands-off modes count as auto; plan/manual must NOT.
    assert "accept edits on" in _AUTO_MODES and "auto mode on" in _AUTO_MODES
    assert "plan mode on" not in _AUTO_MODES and "manual mode on" not in _AUTO_MODES


def test_tail_reports_mode_fields(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    term = client.post("/terminals", json={"cwd": str(tmp_path)})
    tid = term.json()["id"]
    try:
        body = client.get(f"/creative/studio/{tid}/tail").json()
        assert set(body) == {"tail", "alive", "exit_code", "mode", "automode", "ready"}
        assert body["automode"] is False  # a plain shell paints no mode banner
    finally:
        client.delete(f"/terminals/{tid}")


def test_automode_stops_once_user_has_spoken():
    """Regression: the Shift+Tab automode thread must bail the moment the user
    types a brief (studio_say sets _studio_said) — a late cycle would flip
    Claude into plan mode mid-run. Must return immediately: no keystrokes, no
    boot-wait sleeping."""
    import time

    from iron_jarvis.daemon.routes.creative import _engage_claude_automode

    class _FakeSession:
        _studio_said = True  # the user already spoke
        alive = True

        def __init__(self):
            self.writes: list[str] = []

        def output_tail(self) -> str:
            return "? for shortcuts"  # booted TUI — cycling WOULD start otherwise

        def write(self, data) -> None:
            self.writes.append(data)

    session = _FakeSession()
    start = time.monotonic()
    _engage_claude_automode(session)
    assert session.writes == []  # not a single Shift+Tab
    assert time.monotonic() - start < 1.0  # returned immediately, no waiting


def test_fs_mkdir_refuses_protected_secrets_dir(tmp_path):
    """A mkdir is a WRITE — it must be refused inside the protected roots the
    platform registers at boot (the secrets/key dirs), not just reads."""
    client = TestClient(create_app(str(tmp_path)))
    target = tmp_path / ".ironjarvis" / "secrets" / "sneaky"
    r = client.post("/fs/mkdir", json={"path": str(target)})
    assert r.status_code == 403
    assert not target.exists()


def test_fs_mkdir_creates_subfolder_with_guards(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    target = tmp_path / "renders"
    r = client.post("/fs/mkdir", json={"path": str(target)})
    assert r.status_code == 200 and r.json()["created"] is True
    assert target.is_dir()
    # Idempotent: existing folder is fine, reported honestly.
    again = client.post("/fs/mkdir", json={"path": str(target)})
    assert again.status_code == 200 and again.json()["created"] is False
    # Relative and deep-missing-parent paths are refused.
    assert client.post("/fs/mkdir", json={"path": "renders2"}).status_code == 400
    deep = tmp_path / "no" / "such" / "parent" / "x"
    assert client.post("/fs/mkdir", json={"path": str(deep)}).status_code == 400
