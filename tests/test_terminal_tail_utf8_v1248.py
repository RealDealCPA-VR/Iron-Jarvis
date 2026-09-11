"""v1.248.0 — a pane's text tail never starts or ends with half a character.

`TerminalSession.output_tail` (what the AI assist, "copy context" and the
pane-state classifier read) decoded the last 32 KB of raw output with
errors="replace". That window is cut at a BYTE offset, so it could begin in
the middle of a multi-byte character, and — now that the raw ConPTY backend
records bytes the moment they are read — end in the middle of one too. Each
edge became a U+FFFD the program never printed; the v1.248.0 browser check
found exactly one in 4.3 MB of mixed text.
"""

from __future__ import annotations

from iron_jarvis.terminals.session import TerminalSession, _utf8_window

MIXED = "é漢😀─ñ"  # 2-, 3-, 4-, 3- and 2-byte characters


def _session(tmp_path) -> TerminalSession:
    # Constructed, never started: no shell is spawned.
    return TerminalSession(cwd=str(tmp_path), shell="pwsh", argv=["pwsh"], cols=80, rows=24)


def test_the_tail_never_starts_with_half_a_character(tmp_path):
    s = _session(tmp_path)
    data = (MIXED * 20_000).encode("utf-8")
    for shift in range(8):  # every alignment of the 32 KB window
        s._tail = bytearray(b"x" * shift + data)
        out = s.output_tail()
        assert "�" not in out, shift
        assert out.endswith(MIXED)


def test_the_tail_never_ends_with_half_a_character(tmp_path):
    s = _session(tmp_path)
    for ch in ("é", "漢", "😀"):
        whole = ("done " + ch).encode("utf-8")
        for cut in range(1, len(ch.encode("utf-8"))):
            s._tail = bytearray(whole[:-cut])
            out = s.output_tail()
            assert "�" not in out, (ch, cut)
            assert out == "done "


def test_real_invalid_bytes_are_still_reported():
    # Only the EDGES are trimmed; a genuinely invalid byte in the middle still
    # shows as U+FFFD, so this is not hiding bad output.
    assert _utf8_window(b"ok \xff ok") == "ok � ok"
    assert _utf8_window("plain ascii".encode()) == "plain ascii"
    assert _utf8_window(b"") == ""
