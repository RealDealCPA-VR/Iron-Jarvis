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
        assert set(body) == {"tail", "alive", "exit_code", "mode", "automode"}
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
    from iron_jarvis.daemon.routes.creative import latest_claude_mode

    assert latest_claude_mode("booting…\n? for shortcuts") is None
    assert latest_claude_mode("x auto-accept edits on y") == "auto-accept edits on"
    # The TUI repaints the banner each Shift+Tab — the LATEST occurrence wins.
    cycled = "auto-accept edits on ... plan mode on"
    assert latest_claude_mode(cycled) == "plan mode on"
    back = "plan mode on ......... auto-accept edits on"
    assert latest_claude_mode(back) == "auto-accept edits on"
    assert latest_claude_mode("bypass permissions on") == "bypass permissions on"


def test_tail_reports_mode_fields(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    term = client.post("/terminals", json={"cwd": str(tmp_path)})
    tid = term.json()["id"]
    try:
        body = client.get(f"/creative/studio/{tid}/tail").json()
        assert set(body) == {"tail", "alive", "exit_code", "mode", "automode"}
        assert body["automode"] is False  # a plain shell paints no mode banner
    finally:
        client.delete(f"/terminals/{tid}")


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
