"""Creative-module fixes: autopilot launch flag, WMP-safe first-brief guidance,
the clarifying-questions intake endpoint, and the playable/transcode plumbing.

Fully offline. Anything that must actually encode/probe a real video is gated on
``ffmpeg_exe()`` / ``ffprobe_exe()`` so CI without ffmpeg skips cleanly; every
other check runs everywhere (monkeypatch over ffmpeg where possible).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.creative.service import ffmpeg_exe, ffprobe_exe
from iron_jarvis.daemon.app import create_app


# --------------------------------------------------------------------------- #
# A. Auto-mode launch flag + bypass-acceptance regexes (issue 2)
# --------------------------------------------------------------------------- #


def test_autopilot_flags_and_bypass_regexes():
    """Claude launches genuinely hands-off via --dangerously-skip-permissions
    (Codex via --dangerously-bypass-approvals-and-sandbox — its --full-auto was
    REMOVED in codex ≥0.4x and launching with it exited instantly), and the
    one-time acceptance screen is recognised by BOTH a warning regex and an
    accept regex before any key is sent."""
    from iron_jarvis.daemon.routes.creative import (
        _AUTOPILOT_FLAGS,
        _BYPASS_ACCEPT_RE,
        _BYPASS_WARNING_RE,
    )

    assert _AUTOPILOT_FLAGS["claude"] == "--dangerously-skip-permissions"
    assert _AUTOPILOT_FLAGS["codex"] == "--dangerously-bypass-approvals-and-sandbox"

    # The warning regex fires on the phrasings Claude Code paints.
    assert _BYPASS_WARNING_RE.search("Bypass Permissions mode")
    assert _BYPASS_WARNING_RE.search("please skip permissions to continue")
    assert not _BYPASS_WARNING_RE.search("nothing to see here")

    # The accept regex fires only on the affirmative option.
    assert _BYPASS_ACCEPT_RE.search("2. Yes, I accept")
    assert _BYPASS_ACCEPT_RE.search("I accept the risks and responsibility")
    assert not _BYPASS_ACCEPT_RE.search("No, cancel")


def test_studio_start_autopilot_flag_http(tmp_path):
    """When the claude CLI is present, autopilot launches it with the flag and
    reports automode_method=='flag'. If claude isn't installed, the flag map is
    still asserted and the CLI-driving part is skipped (not failed)."""
    from iron_jarvis.daemon.routes.creative import _AUTOPILOT_FLAGS
    from iron_jarvis.terminals.ai_clis import detect_ai_clis

    claude = next((c for c in detect_ai_clis() if c["id"] == "claude"), None)
    if claude is None or not claude.get("installed"):
        assert _AUTOPILOT_FLAGS["claude"] == "--dangerously-skip-permissions"
        pytest.skip("claude CLI not installed — HTTP launch path not exercisable here")

    with TestClient(create_app(str(tmp_path))) as client:
        r = client.post(
            "/creative/studio/start",
            json={"cli": "claude", "cwd": str(tmp_path), "autopilot": True},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        tid = body["terminal_id"]
        try:
            assert "--dangerously-skip-permissions" in body["command"]
            assert body["automode_method"] == "flag"
            assert body["autopilot"] is True
        finally:
            # Tear down the launched CLI terminal immediately (no brief is ever
            # sent, so nothing runs before it's killed).
            client.delete(f"/terminals/{tid}")


# --------------------------------------------------------------------------- #
# B. First-brief WMP-compatible encoding guidance (issue 4 assist)
# --------------------------------------------------------------------------- #


def test_first_brief_includes_wmp_encoding_guidance():
    """The composed first brief instructs the CLI to encode final video as
    H.264 / yuv420p / +faststart so it plays everywhere (incl. Windows Media
    Player). Assert the guidance lives in the first-brief construction."""
    import iron_jarvis.daemon.routes.creative as creative_mod

    src = Path(creative_mod.__file__).read_text(encoding="utf-8")
    assert "yuv420p" in src
    assert "+faststart" in src


# --------------------------------------------------------------------------- #
# C. Clarifying-questions intake endpoint (issue 3)
# --------------------------------------------------------------------------- #


def test_intake_requires_brief(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        r = client.post("/creative/intake", json={"brief": ""})
        assert r.status_code == 400


def test_intake_returns_default_questions_offline(tmp_path):
    """On the default mock provider the endpoint returns a sensible default
    question set so the sharpen-the-brief step works offline."""
    with TestClient(create_app(str(tmp_path))) as client:
        r = client.post("/creative/intake", json={"brief": "a dog surfing at sunset"})
        assert r.status_code == 200
        body = r.json()
        assert body["source"] == "default"
        questions = body["questions"]
        assert isinstance(questions, list) and questions
        keys = {q["key"] for q in questions}
        assert {"duration", "style", "aspect", "mood"} <= keys
        for q in questions:
            assert q["key"] and q["label"]
            assert isinstance(q["options"], list)
            assert isinstance(q["allow_custom"], bool)


# --------------------------------------------------------------------------- #
# D. Playability probe + "make playable" transcode (issue 4)
# --------------------------------------------------------------------------- #


def test_ffmpeg_resolver_never_crashes():
    """The resolver returns None or an existing file path — never raises, and
    never points at a non-existent file."""
    got = ffmpeg_exe()
    assert got is None or Path(got).is_file()


def test_video_playability_verdicts_via_monkeypatch(monkeypatch):
    """The verdict logic, isolated from ffprobe: non-web-safe codec/pixfmt is not
    playable, H.264/yuv420p is, and an un-probeable file is never nagged."""
    from iron_jarvis.creative import service
    from iron_jarvis.creative.service import video_playability

    dummy = Path("dummy.mp4")

    monkeypatch.setattr(
        service, "probe_video",
        lambda p: {"codec": "hevc", "pix_fmt": "yuv444p", "has_ffprobe": True},
    )
    assert video_playability(dummy)["playable"] is False

    monkeypatch.setattr(
        service, "probe_video",
        lambda p: {"codec": "h264", "pix_fmt": "yuv420p", "has_ffprobe": True},
    )
    assert video_playability(dummy)["playable"] is True

    monkeypatch.setattr(
        service, "probe_video",
        lambda p: {"codec": "", "pix_fmt": "", "has_ffprobe": False},
    )
    assert video_playability(dummy)["playable"] is True


def test_transcode_honest_424_without_ffmpeg(tmp_path, monkeypatch):
    """When ffmpeg isn't available the endpoint refuses honestly (424) — checked
    BEFORE the source is resolved, so even a placeholder .mp4 hits the 424."""
    from iron_jarvis.creative import service

    monkeypatch.setattr(service, "ffmpeg_exe", lambda: None)
    dummy = tmp_path / "clip.mp4"
    dummy.write_bytes(b"\x00" * 64)
    with TestClient(create_app(str(tmp_path))) as client:
        r = client.post("/creative/transcode", json={"path": str(dummy)})
        assert r.status_code == 424
        assert "ffmpeg" in r.json()["detail"].lower()


def test_transcode_and_playable_validation(tmp_path):
    """Validation that doesn't require a real encode: /creative/playable rejects a
    non-video (415), and /creative/transcode with no source is ALWAYS a 400 —
    request validation runs BEFORE the ffmpeg-availability check, so a malformed
    request reads 400 whether or not ffmpeg is installed."""
    with TestClient(create_app(str(tmp_path))) as client:
        txt = tmp_path / "notes.txt"
        txt.write_text("not a video")
        assert client.get("/creative/playable", params={"path": str(txt)}).status_code == 415

        # Neither name nor path → 400, regardless of ffmpeg presence.
        assert client.post("/creative/transcode", json={}).status_code == 400


@pytest.mark.skipif(
    ffmpeg_exe() is None or ffprobe_exe() is None,
    reason="ffmpeg/ffprobe not installed",
)
def test_real_transcode_makes_hevc_playable(tmp_path):
    """End-to-end: a real HEVC/yuv444p clip is reported not-playable, and
    'make playable' produces an H.264/yuv420p sibling that IS playable."""
    ff = ffmpeg_exe()
    src = tmp_path / "src.mp4"
    proc = subprocess.run(
        [
            ff, "-y", "-f", "lavfi",
            "-i", "testsrc=size=320x240:rate=10:duration=1",
            "-c:v", "libx265", "-pix_fmt", "yuv444p", str(src),
        ],
        capture_output=True, timeout=120,
    )
    if proc.returncode != 0 or not src.is_file():
        pytest.skip("this ffmpeg build can't encode HEVC/yuv444p")

    with TestClient(create_app(str(tmp_path))) as client:
        r = client.get("/creative/playable", params={"path": str(src)})
        assert r.status_code == 200, r.text
        verdict = r.json()
        assert verdict["playable"] is False
        assert verdict["codec"] == "hevc"

        t = client.post("/creative/transcode", json={"path": str(src)})
        assert t.status_code == 200, t.text
        dst = Path(t.json()["path"])
        assert dst.is_file()

        r2 = client.get("/creative/playable", params={"path": str(dst)})
        assert r2.status_code == 200, r2.text
        good = r2.json()
        assert good["playable"] is True
        assert good["codec"] == "h264"
        assert good["pix_fmt"] == "yuv420p"
