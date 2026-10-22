"""v1.370.0 review: write_file's prior copy is EXACT on new journal rows, and
every reader of the journal still judges OLD rows the old way.

v1.370.0 changed an existing behaviour: ``write_file`` used to keep its prior
copy as ``read_text()`` re-encoded (universal newlines, so a CRLF or LF file
came back as LF text) and the revert wrote it with ``write_text`` (the OS's
newlines — CRLF here). Now a NEW row carries ``exact: true`` and the revert
writes the saved bytes back as they were. Rows written before keep the old
write. Pinned for both, through the real write tool and the real Undo (the
body of ``POST /undo/{id}``), and through the readers that judge "changed
since" (``POST /chat/changes``, the rewind's simulation):

* a NEW row gives an LF file back as LF and a CRLF file back as CRLF, byte
  for byte; ``/chat/changes`` reads it as undone, nothing changed since;
* an OLD row (no ``exact``) still undoes — no "changed since" refusal — and
  gives the text back with this OS's newlines, as it always did;
  ``/chat/changes`` reads it as undone, nothing changed since;
* the rewind's simulation (``rewind._written``) predicts exactly the bytes
  each kind of row's revert writes.
"""

from __future__ import annotations

import iron_jarvis.workflows.models  # noqa: F401  (register tables before init_db)

import base64
import json
import os

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import UndoJournal
from iron_jarvis.harness import rewind as R
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.undo import read_envelope, resolve_prior_bytes
from tests.test_chat_changes_undone_v1369 import _undo, _write
from tests.test_chat_changes_v1328 import _client, _ctx, _post, _since


def _envelope(platform, action_id: str) -> dict:
    with session_scope(platform.engine) as db:
        j = db.get(UndoJournal, action_id)
        return {"kind": j.kind, "pre_ref": j.pre_ref, "pre_inline": j.pre_inline}


def _make_old(platform, action_id: str, original: bytes) -> None:
    """Rewrite a NEW row's envelope into the shape v1.369.0 wrote: no
    ``exact``, the prior copy read back as text (universal newlines)."""
    with session_scope(platform.engine) as db:
        j = db.get(UndoJournal, action_id)
        meta = json.loads(j.pre_inline)
        assert meta.pop("exact") is True
        text = original.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        meta["data"] = base64.b64encode(text.encode("utf-8")).decode("ascii")
        j.pre_inline = json.dumps(meta)
        db.add(j)
        db.commit()


@pytest.mark.parametrize("original", [b"alpha\nbeta\n", b"alpha\r\nbeta\r\n", b"no newline at end"])
def test_a_new_row_gives_the_file_back_byte_for_byte(tmp_path, original):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "work"
    ctx = _ctx(platform, ws)
    (ws / "f.txt").write_bytes(original)
    since = _since()
    action = _write(platform, ctx, "f.txt", "replaced\n")
    env = _envelope(platform, action)
    meta = read_envelope(env)
    assert meta["mode"] == "text" and meta["exact"] is True
    assert resolve_prior_bytes(platform.config.home, env, meta) == original
    _undo(platform, action)
    assert (ws / "f.txt").read_bytes() == original
    (c,) = _post(_client(platform), since=since)["changes"]
    assert c["undone"] is True and c["changed_since"] is False and c["note"] == "", c


@pytest.mark.parametrize("original", [b"alpha\nbeta\n", b"alpha\r\nbeta\r\n"])
def test_an_old_row_still_undoes_with_the_os_newlines_as_before(tmp_path, original):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "work"
    ctx = _ctx(platform, ws)
    (ws / "f.txt").write_bytes(original)
    since = _since()
    action = _write(platform, ctx, "f.txt", "replaced\n")
    _make_old(platform, action, original)
    _undo(platform, action)  # no "changed since" refusal
    text = original.decode("utf-8").replace("\r\n", "\n")
    assert (ws / "f.txt").read_bytes() == text.replace("\n", os.linesep).encode("utf-8")
    (c,) = _post(_client(platform), since=since)["changes"]
    assert c["undone"] is True and c["changed_since"] is False and c["note"] == "", c


def test_the_rewind_simulation_writes_what_each_rows_revert_writes(tmp_path):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "work"
    ctx = _ctx(platform, ws)
    for original, old in ((b"a\nb\n", False), (b"a\r\nb\r\n", False), (b"a\nb\n", True), (b"a\r\nb\r\n", True)):
        (ws / "f.txt").write_bytes(original)
        action = _write(platform, ctx, "f.txt", "x\n")
        if old:
            _make_old(platform, action, original)
        env = _envelope(platform, action)
        meta = read_envelope(env)
        predicted = R._written(resolve_prior_bytes(platform.config.home, env, meta), meta["mode"],
                               bool(meta.get("exact")))
        _undo(platform, action)
        assert (ws / "f.txt").read_bytes() == predicted, (original, old)


def test_only_write_files_text_copy_carries_the_flag(tmp_path):
    """The flag is on write_file's TEXT copy only: a binary prior (raw mode)
    and a newly created file (nothing to put back) carry none."""
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "work"
    ctx = _ctx(platform, ws)
    (ws / "bin.dat").write_bytes(b"\xff\xfe\x00raw")
    raw = _envelope(platform, _write(platform, ctx, "bin.dat", "text now\n"))
    created = _envelope(platform, _write(platform, ctx, "new.txt", "hello\n"))
    assert "exact" not in read_envelope(raw) and read_envelope(raw)["mode"] == "raw"
    assert "exact" not in read_envelope(created)
    with session_scope(platform.engine) as db:
        kinds = sorted(j.kind for j in db.exec(select(UndoJournal)).all())
    assert kinds == ["file_delete", "file_restore"]
