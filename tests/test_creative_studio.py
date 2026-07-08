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
        assert set(body) == {
            "tail",
            "alive",
            "exit_code",
            "mode",
            "automode",
            "ready",
            "phase",
            "status_line",
        }
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
    # Only FULL-auto (runs commands too) counts as automode. "accept edits on"
    # still prompts on commands, so it is NOT hands-off; plan/manual never are.
    assert "auto mode on" in _AUTO_MODES and "bypass permissions on" in _AUTO_MODES
    assert "accept edits on" not in _AUTO_MODES
    assert "plan mode on" not in _AUTO_MODES and "manual mode on" not in _AUTO_MODES


def test_tail_reports_mode_fields(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    term = client.post("/terminals", json={"cwd": str(tmp_path)})
    tid = term.json()["id"]
    try:
        body = client.get(f"/creative/studio/{tid}/tail").json()
        assert set(body) == {
            "tail",
            "alive",
            "exit_code",
            "mode",
            "automode",
            "ready",
            "phase",
            "status_line",
        }
        assert body["automode"] is False  # a plain shell paints no mode banner
    finally:
        client.delete(f"/terminals/{tid}")


class _FakeAutomodeSession:
    """Minimal session double for the automode thread: a mutable tail + a
    recorded write log."""

    alive = True

    def __init__(self, tail: str = "? for shortcuts") -> None:
        self.writes: list[str] = []
        self.tail = tail

    def output_tail(self) -> str:
        return self.tail

    def write(self, data) -> None:
        self.writes.append(data)


def test_automode_never_types_inside_the_say_quiet_window():
    """The automode thread must not send a Shift+Tab within the quiet window
    after a studio_say — a keystroke there could land inside a typed brief.
    Once the target mode is painted it stops without ever pressing."""
    import threading
    import time

    from iron_jarvis.daemon.routes.creative import _engage_claude_automode

    session = _FakeAutomodeSession()
    session._last_say_ts = time.time()  # a brief was JUST typed
    t = threading.Thread(target=_engage_claude_automode, args=(session,), daemon=True)
    t.start()
    time.sleep(1.2)  # well inside the 2.5s quiet window
    assert session.writes == []  # it waited instead of typing over the brief
    session.tail = "? for shortcuts ... auto mode on"  # full-auto engaged elsewhere
    t.join(timeout=5)
    assert not t.is_alive()
    assert session.writes == []  # engaged without a single press


def test_automode_cycles_past_mild_modes_to_full_auto():
    """The loop must press PAST accept-edits and plan (which still prompt on
    commands) and only stop on full 'auto mode on' — stopping at accept-edits
    would stall every generation at its first shell-command prompt."""
    from iron_jarvis.daemon.routes.creative import _SHIFT_TAB, _engage_claude_automode

    # The real Shift+Tab ladder: manual → accept-edits → plan → auto → manual.
    ladder = ["accept edits on", "plan mode on", "auto mode on"]
    session = _FakeAutomodeSession("? for shortcuts ... manual mode on")
    real_write = session.write

    def write_and_advance(data) -> None:
        real_write(data)
        step = len(session.writes) - 1
        if step < len(ladder):
            session.tail = f"? for shortcuts ... {ladder[step]}"

    session.write = write_and_advance
    _engage_claude_automode(session)
    # Three presses: manual→accept-edits→plan→auto, then verified stop.
    assert session.writes == [_SHIFT_TAB, _SHIFT_TAB, _SHIFT_TAB]


def test_derive_phase_lifecycle():
    """booting → thinking (fresh esc-to-interrupt) → idle → exited, with the
    freshness guard keeping a STALE marker from reading as a live turn."""
    from iron_jarvis.daemon.routes.creative import derive_phase

    # CLI hasn't painted yet.
    assert derive_phase("PS C:\\work> claude", ready=False) == ("booting", None)
    # A running turn paints its status bar (fresh output).
    tail = "booted\n* Cerebrating... (14s - esc to interrupt)"
    phase, status = derive_phase(tail, ready=True, output_age=1.0)
    assert phase == "thinking"
    assert status is not None and "esc to interrupt" in status.lower()
    # The SAME marker with stale output is a leftover, not a live turn.
    assert derive_phase(tail, ready=True, output_age=30.0)[0] == "idle"
    # The CLI quit back to the shell: prompt is the LAST thing in the tail.
    exited = "old tui content\nDone.\nPS C:\\Users\\VR\\work> "
    assert derive_phase(exited, ready=True, output_age=30.0)[0] == "exited"
    # Booted, quiet, no prompt at the end = waiting for input.
    assert derive_phase("booted\n? for shortcuts", ready=True, output_age=30.0)[0] == "idle"


def test_studio_say_refuses_a_bare_shell_prompt(tmp_path):
    """SAFETY: once the engine has exited (or never started), a typed brief
    would run as a SHELL COMMAND — the say endpoint must refuse, honestly."""
    client = TestClient(create_app(str(tmp_path)))
    term = client.post("/terminals", json={"cwd": str(tmp_path)})
    tid = term.json()["id"]
    try:
        # Simulate the engine-exited tail: ready was seen, prompt is back.
        from iron_jarvis.daemon.routes.creative import derive_phase  # noqa: F401

        app = client.app
        session = app.state.platform.terminals.get(tid)
        session._studio_ready = True
        session._tail = bytearray(b"old claude output\nDone.\nPS C:\\work> ")
        r = client.post(f"/creative/studio/{tid}/say", json={"text": "make a video"})
        assert r.status_code == 409
        assert "exited" in r.json()["detail"]
    finally:
        client.delete(f"/terminals/{tid}")


def test_studio_media_walks_subfolders(tmp_path):
    """The recursive scan finds media in SUBFOLDERS (pixio-story's layout),
    reports folder-relative names, and skips non-media + hidden dirs."""
    client = TestClient(create_app(str(tmp_path)))
    dest = tmp_path / "renders"
    (dest / "shots").mkdir(parents=True)
    (dest / ".cache").mkdir()
    (dest / "cover.png").write_bytes(b"\x89PNG fake")
    (dest / "shots" / "shot-01.mp4").write_bytes(b"fake video")
    (dest / "notes.txt").write_text("not media")
    (dest / ".cache" / "sneaky.png").write_bytes(b"hidden")

    r = client.get(f"/creative/studio-media?path={dest}")
    assert r.status_code == 200
    body = r.json()
    names = {f["name"] for f in body["files"]}
    assert names == {"cover.png", "shots/shot-01.mp4"}
    assert body["truncated"] is False
    kinds = {f["name"]: f["media"] for f in body["files"]}
    assert kinds["shots/shot-01.mp4"] == "video"

    assert client.get("/creative/studio-media?path=relative/dir").status_code == 400
    missing = tmp_path / "nope"
    assert client.get(f"/creative/studio-media?path={missing}").status_code == 404


def test_creative_ingest_brings_studio_output_into_the_gallery(tmp_path):
    """A studio generation on disk becomes a durable gallery artifact —
    idempotently (re-ingesting the same bytes returns the same artifact)."""
    client = TestClient(create_app(str(tmp_path)))
    src = tmp_path / "generated.png"
    src.write_bytes(b"\x89PNG synthetic bytes")

    r = client.post("/creative/ingest", json={"path": str(src)})
    assert r.status_code == 200
    body = r.json()
    assert body["ingested"] is True and body["media"] == "image"
    name = body["name"]
    assert name.startswith("studio-generated-")

    again = client.post("/creative/ingest", json={"path": str(src)})
    assert again.status_code == 200
    assert again.json()["ingested"] is False  # same bytes → same artifact
    assert again.json()["name"] == name

    # It now shows in the gallery and serves bytes.
    items = client.get("/creative/items").json()["items"]
    assert any(i["name"] == name for i in items)
    served = client.get(f"/creative/file/{name}")
    assert served.status_code == 200 and served.content == src.read_bytes()

    # Guards: non-media, relative path, missing file.
    txt = tmp_path / "notes.txt"
    txt.write_text("nope")
    assert client.post("/creative/ingest", json={"path": str(txt)}).status_code == 415
    assert client.post("/creative/ingest", json={"path": "rel.png"}).status_code == 400
    assert (
        client.post("/creative/ingest", json={"path": str(tmp_path / "ghost.png")}).status_code
        == 404
    )


def test_gallery_serves_artifact_names_containing_slashes(tmp_path):
    """Artifact names with '/' (computer-use screenshots) must serve and
    delete through the path-converter routes instead of 404ing."""
    client = TestClient(create_app(str(tmp_path)))
    store = client.app.state.platform.artifacts
    store.save("shots/frame-01.png", b"\x89PNG slashed", kind="image", filename="frame-01.png")

    r = client.get("/creative/file/shots/frame-01.png")
    assert r.status_code == 200 and r.content == b"\x89PNG slashed"

    d = client.delete("/creative/items/shots/frame-01.png")
    assert d.status_code == 200 and d.json()["deleted"] == "shots/frame-01.png"
    assert client.get("/creative/file/shots/frame-01.png").status_code == 404


def test_fs_list_reports_truncation_flag(tmp_path):
    """The listing must SAY it's partial past the entry cap."""
    from iron_jarvis.fsbrowser.browser import list_dir

    d = tmp_path / "many"
    d.mkdir()
    for i in range(5):
        (d / f"f{i}.txt").write_text("x")
    out = list_dir(d)
    assert out["truncated"] is False
    import iron_jarvis.fsbrowser.browser as br

    old = br.MAX_ENTRIES
    br.MAX_ENTRIES = 3
    try:
        out = list_dir(d)
        assert out["truncated"] is True and len(out["entries"]) == 3
    finally:
        br.MAX_ENTRIES = old


def test_pixio_connection_honors_env_fallback(tmp_path, monkeypatch):
    """The Connections card must not call a WORKING Pixio setup 'disconnected'
    when the key comes from the PIXIO_API_KEY env fallback."""
    monkeypatch.setenv("PIXIO_API_KEY", "pxio_live_test123")
    client = TestClient(create_app(str(tmp_path)))
    rows = client.get("/connections").json()["connections"]
    pixio = next(r for r in rows if r["provider"] == "pixio")
    assert pixio["connected"] is True
    assert "environment" in pixio["account"]

    monkeypatch.delenv("PIXIO_API_KEY")
    client2 = TestClient(create_app(str(tmp_path / "b")))
    rows2 = client2.get("/connections").json()["connections"]
    pixio2 = next(r for r in rows2 if r["provider"] == "pixio")
    assert pixio2["connected"] is False


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
