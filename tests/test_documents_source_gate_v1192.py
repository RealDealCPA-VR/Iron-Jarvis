"""Document SOURCE paths are RESOLVED before they are GATED (findings 03/04).

Two check-then-resolve holes, one shape. ``POST /documents/save-copy`` and
``_redact_source`` (behind ``/documents/redact/scan`` + ``/apply``) both ran
``fs_read_ok`` on the RAW request string and then opened a DIFFERENT file:
``Path(raw).expanduser()``, and for redaction ``(home/'documents'/raw).resolve()``
when the string was relative. ``Path.resolve()`` does not expand ``~`` and cannot
anticipate a later join, so:

* ``~/.ironjarvis/secrets/vault.enc`` was gated as a literal ``~`` folder under
  the cwd — outside every protected root — and then read from the real one.
* ``../undo/preimage.txt`` was gated against the cwd and then resolved to
  ``home/undo/...``: the undo journal, which holds pre-images of the user's own
  files. ``/redact/apply`` writes the near-complete content to a destination the
  caller picks, so the read is a full exfiltration.

The registered protected roots (``home/secrets``, ``home/browser``,
``home/undo``, ``config.db_path`` + WAL/SHM) are exactly the plaintext-sensitive
stores the gate exists for. Resolve first, gate second.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app

SAMPLE = "Taxpayer: Robert J. Alvarez\nSSN: 412-88-7391\n"


@dataclass
class Env:
    client: TestClient
    home: Path
    secret: Path
    preimage: Path
    dest: Path


@pytest.fixture
def env(tmp_path, monkeypatch) -> Env:
    """An app whose state home is ``~/.ironjarvis`` for a FAKE ``~`` — the only
    way the tilde attack can name this test's protected roots."""
    monkeypatch.delenv("IRONJARVIS_FS_ALLOWLIST", raising=False)
    monkeypatch.delenv("IRONJARVIS_HOME", raising=False)  # keep home per-project
    monkeypatch.setenv("HOME", str(tmp_path))  # posix expanduser
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # nt expanduser
    assert Path("~").expanduser() == tmp_path, "fixture cannot fake ~ on this platform"

    # config.home is <project_root>/.ironjarvis, and ~ is tmp_path — so
    # "~/.ironjarvis/..." names THIS app's protected roots.
    home = tmp_path / ".ironjarvis"
    client = TestClient(create_app(str(tmp_path)))

    secret = home / "secrets" / "vault.enc"  # NOT a .secrets.key name — the
    secret.parent.mkdir(parents=True, exist_ok=True)  # by-name layer must not
    secret.write_text("KEY MATERIAL", encoding="utf-8")  # be what saves us

    preimage = home / "undo" / "preimage.txt"
    preimage.parent.mkdir(parents=True, exist_ok=True)
    preimage.write_text(SAMPLE, encoding="utf-8")

    dest = tmp_path / "Desktop"
    dest.mkdir()
    return Env(client=client, home=home, secret=secret, preimage=preimage, dest=dest)


def test_the_fixture_roots_really_are_protected(env):
    """Guard the guard: if registration ever moved, every test below would pass
    vacuously."""
    from iron_jarvis.core.fs_policy import is_protected_path

    assert is_protected_path(env.secret)
    assert is_protected_path(env.preimage)
    assert is_protected_path(env.home / "ironjarvis.db")


# --- finding 03: save-copy -------------------------------------------------


def test_save_copy_refuses_a_tilde_path_to_a_protected_store(env):
    r = env.client.post(
        "/documents/save-copy",
        json={
            "source": "~/.ironjarvis/secrets/vault.enc",
            "dest_dir": str(env.dest),
            "name": "copy.enc",
        },
    )
    assert r.status_code == 403, r.text
    assert not (env.dest / "copy.enc").exists()


def test_save_copy_refuses_the_tilde_form_of_the_app_database(env):
    """The reported scenario verbatim: the SQLite DB carries conversations,
    memory, and inline undo pre-images of the user's real files."""
    assert (env.home / "ironjarvis.db").is_file()
    r = env.client.post(
        "/documents/save-copy",
        json={
            "source": "~/.ironjarvis/ironjarvis.db",
            "dest_dir": str(env.dest),
            "name": "copy.db",
        },
    )
    assert r.status_code == 403, r.text
    assert not (env.dest / "copy.db").exists()


def test_save_copy_requires_an_absolute_source(env):
    """Like the sibling creative.py file routes and ``_preview_path``: a
    relative source is resolved against whatever the daemon's cwd happens to
    be, which is not a destination any caller chose."""
    r = env.client.post(
        "/documents/save-copy",
        json={"source": "organizer.txt", "dest_dir": str(env.dest)},
    )
    assert r.status_code == 400, r.text


def test_save_copy_still_copies_an_ordinary_document(env, tmp_path):
    doc = tmp_path / "client_docs" / "organizer.txt"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(SAMPLE, encoding="utf-8")
    r = env.client.post(
        "/documents/save-copy", json={"source": str(doc), "dest_dir": str(env.dest)}
    )
    assert r.status_code == 200, r.text
    assert (env.dest / "organizer.txt").read_text(encoding="utf-8") == SAMPLE


# --- finding 04: the redaction source --------------------------------------


def test_redact_scan_refuses_a_tilde_path_to_a_protected_store(env):
    r = env.client.post(
        "/documents/redact/scan", json={"path": "~/.ironjarvis/undo/preimage.txt"}
    )
    assert r.status_code == 403, r.text


def test_redact_scan_refuses_a_relative_escape_out_of_the_documents_dir(env):
    """``home/documents/../undo`` is ``home/undo``. The gate saw the string
    resolved against the cwd and never knew."""
    r = env.client.post(
        "/documents/redact/scan", json={"path": "../undo/preimage.txt"}
    )
    assert r.status_code == 403, r.text


def test_redact_apply_cannot_exfiltrate_a_protected_file(env, tmp_path):
    """The full attack: apply reads the source and writes it, minus a couple of
    matched terms, wherever the caller points."""
    out = tmp_path / "stolen.txt"
    r = env.client.post(
        "/documents/redact/apply",
        json={"path": "../undo/preimage.txt", "terms": ["412-88-7391"],
              "output_path": str(out)},
    )
    assert r.status_code == 403, r.text
    assert not out.exists()


def test_redact_apply_cannot_exfiltrate_via_the_tilde_form(env, tmp_path):
    out = tmp_path / "stolen2.txt"
    r = env.client.post(
        "/documents/redact/apply",
        json={"path": "~/.ironjarvis/secrets/vault.enc", "terms": ["KEY"],
              "output_path": str(out)},
    )
    assert r.status_code == 403, r.text
    assert not out.exists()


def test_a_relative_source_still_reaches_the_documents_dir(env):
    """The join is a FEATURE and stays — only the ordering changed."""
    doc = env.home / "documents" / "notes.txt"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(SAMPLE, encoding="utf-8")
    r = env.client.post("/documents/redact/scan", json={"path": "notes.txt"})
    assert r.status_code == 200, r.text
    assert "412-88-7391" in {f["value"] for f in r.json()["findings"]}


def test_an_ordinary_absolute_document_still_scans_and_redacts(env, tmp_path):
    doc = tmp_path / "client_docs" / "organizer.txt"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(SAMPLE, encoding="utf-8")
    scan = env.client.post("/documents/redact/scan", json={"path": str(doc)})
    assert scan.status_code == 200, scan.text
    assert "412-88-7391" in {f["value"] for f in scan.json()["findings"]}

    out = tmp_path / "clean.txt"
    r = env.client.post(
        "/documents/redact/apply",
        json={"path": str(doc), "terms": ["412-88-7391"], "output_path": str(out)},
    )
    assert r.status_code == 200, r.text
    assert "412-88-7391" not in out.read_text(encoding="utf-8")


def test_the_output_gate_is_not_regressed(env, tmp_path):
    """The apply route already gated its DESTINATION correctly (expanduser'd
    before ``is_protected_path``). Pin it so the source fix cannot loosen it."""
    doc = tmp_path / "organizer.txt"
    doc.write_text(SAMPLE, encoding="utf-8")
    r = env.client.post(
        "/documents/redact/apply",
        json={"path": str(doc), "terms": ["412-88-7391"],
              "output_path": "~/.ironjarvis/secrets/leak.txt"},
    )
    assert r.status_code == 403, r.text
    assert not (env.home / "secrets" / "leak.txt").exists()
