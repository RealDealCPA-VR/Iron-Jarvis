"""An append must never MISPLACE or DESTROY a note (deep review 2026-08-20).

Two data-destroying defects in ``MarkdownDirConnector.append``:

10. The unconditional ``mkdir(parents=True, exist_ok=True)`` ignored
    ``self.create``, so v1.172.0's REFUSE-DON'T-RECREATE invariant held in
    ``__init__`` and nowhere else. A user vault that had moved / unmounted was
    re-created EMPTY at the old path, the note landed in the wrong folder, the
    vault split in two — and ``missing`` then went False, so ``health()``
    flipped back to ``available: True`` and hid the breakage forever.

11. The existing note was read through ``_read``, which swallows OSError AND
    UnicodeDecodeError to ``""`` — after which the whole file was rewritten as
    ``existing + new``. Appending to a cp1252/ANSI note (or one briefly locked
    by antivirus) therefore replaced years of notes with one new paragraph, and
    ``LTMAppendTool.capture_undo`` fails on the same decode (``reversible=False``)
    so there is no pre-image to restore.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iron_jarvis.ltm.base import LTMWriteRefused
from iron_jarvis.ltm.brain import MarkdownBrainConnector
from iron_jarvis.ltm.obsidian import ObsidianConnector


# --- 10. a vanished create=False base is refused, never conjured -------------


def test_append_to_a_vanished_vault_refuses_and_creates_nothing(tmp_path):
    vault = tmp_path / "MyVault"
    vault.mkdir()
    (vault / "clients.md").write_text("# Clients\n\nold notes\n", encoding="utf-8")
    conn = ObsidianConnector(vault)
    assert conn.health()["available"] is True

    # The user renames the folder / the cloud drive unmounts.
    (vault / "clients.md").unlink()
    vault.rmdir()
    assert conn.missing is True

    with pytest.raises(LTMWriteRefused) as err:
        conn.append("Clients", "a note the steward wanted to file")

    # Says the same thing health() says, naming the path it could not find.
    assert str(vault) in str(err.value)
    assert "nothing was created in its place" in str(err.value)
    # THE POINT: the vault was not conjured back, and health stays honest.
    assert not vault.exists(), "the vault was RECREATED by append — the split-vault bug"
    assert conn.missing is True
    assert conn.health()["available"] is False


def test_append_to_a_vanished_user_markdown_source_refuses(tmp_path):
    """The other create=False constructor: ltm/sources.py's registered folder."""
    base = tmp_path / "team-wiki"
    conn = MarkdownBrainConnector(base, create=False)
    with pytest.raises(LTMWriteRefused):
        conn.append("Process", "how we close the books")
    assert not base.exists()


def test_the_builtin_brain_still_self_creates_on_append(tmp_path):
    """create=True is the app's OWN store — it must keep creating itself."""
    brain = tmp_path / "state" / "brain"
    conn = MarkdownBrainConnector(brain)  # create=True
    brain.rmdir()  # e.g. a cleaner wiped the state home between runs
    ref = conn.append("Ideas", "a brilliant idea")
    assert brain.is_dir()
    assert Path(ref).read_text(encoding="utf-8").startswith("# Ideas")


# --- 11. an unreadable existing note is refused, never truncated -------------


def test_append_to_a_cp1252_note_refuses_and_leaves_the_bytes_intact(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "clients.md"
    # Written years ago by Notepad in ANSI: curly quotes, em dash, é.
    original = "# Clients\n\nAcme “big fish” — Ren\xe9e, S-corp\n".encode(
        "cp1252"
    )
    note.write_bytes(original)
    with pytest.raises(UnicodeDecodeError):  # the trigger, pinned
        note.read_text(encoding="utf-8")

    conn = ObsidianConnector(vault)
    with pytest.raises(LTMWriteRefused) as err:
        conn.append("Clients", "new paragraph from the unattended steward")

    assert str(note) in str(err.value)
    assert "nothing was written" in str(err.value).lower()
    # THE POINT: byte-for-byte untouched. Before the fix the body became
    # "\n\n<new paragraph>\n" and no undo pre-image existed.
    assert note.read_bytes() == original
    # And no atomic-write temp file was left behind.
    assert sorted(p.name for p in vault.iterdir()) == ["clients.md"]


def test_append_to_an_unreadable_existing_note_refuses(tmp_path, monkeypatch):
    """The wider OSError arm: a file briefly locked by antivirus/permissions."""
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "journal.md"
    original = b"# Journal\n\nfirst entry\n"
    note.write_bytes(original)
    conn = ObsidianConnector(vault)

    real_read_text = Path.read_text

    def locked(self, *a, **kw):
        if self == note:
            raise PermissionError(13, "The process cannot access the file")
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", locked)
    with pytest.raises(LTMWriteRefused):
        conn.append("Journal", "second entry")
    monkeypatch.undo()
    assert note.read_bytes() == original


def test_a_readable_note_still_appends_normally(tmp_path):
    """The refusals must not cost the feature: UTF-8 append still works."""
    vault = tmp_path / "vault"
    vault.mkdir()
    conn = ObsidianConnector(vault)
    p1 = conn.append("Journal", "first entry")
    p2 = conn.append("Journal", "second entry")
    assert p1 == p2
    body = Path(p1).read_text(encoding="utf-8")
    assert "first entry" in body and "second entry" in body
