"""Remote SSH/SFTP long-term-memory source — fully offline via a fake SFTP."""

from __future__ import annotations

import io

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.ltm.ssh import SSHBrainConnector


class _FakeFile(io.BytesIO):
    """A writable SFTP file whose contents land back in the fake fs on close."""

    def __init__(self, fs, path, mode):
        super().__init__(fs.get(path, b"") if "r" in mode else b"")
        self._fs, self._path, self._mode = fs, path, mode

    def write(self, data):  # type: ignore[override]
        if isinstance(data, str):
            data = data.encode()
        self._fs[self._path] = self._fs.get(self._path, b"") + data if False else data

    def __exit__(self, *a):
        return super().__exit__(*a)


class _FakeSFTP:
    """In-memory SFTP: listdir + open(r/w). Records that close() was called."""

    def __init__(self, files: dict[str, bytes]):
        self.fs = dict(files)
        self.closed = False

    def listdir(self, path):
        # flat: return basenames whose dir == path
        import posixpath

        return [posixpath.basename(p) for p in self.fs if posixpath.dirname(p) == path]

    def open(self, path, mode="r"):
        return _FakeFile(self.fs, path, mode)

    def close(self):
        self.closed = True


def _conn(files):
    sftp = _FakeSFTP(files)
    conn = SSHBrainConnector(
        "nas.local", "/notes", username="me", sftp_factory=lambda: sftp
    )
    return conn, sftp


def test_search_ranks_remote_markdown():
    conn, _ = _conn(
        {
            "/notes/roadmap.md": b"# Roadmap\nShip the SSH memory feature next quarter.",
            "/notes/groceries.md": b"# Groceries\nmilk, eggs",
            "/notes/readme.txt": b"ignore me (not markdown)",
        }
    )
    hits = conn.search("ssh memory", k=5)
    assert hits, "expected a hit"
    assert hits[0]["title"] == "roadmap"
    assert hits[0]["source"] == "ssh"
    assert hits[0]["ref"].startswith("nas.local:/notes/")
    assert "ssh" in hits[0]["snippet"].lower()


def test_append_writes_remote_file():
    conn, sftp = _conn({})
    ref = conn.append("My Idea", "a brilliant plan")
    assert ref == "nas.local:/notes/my-idea.md"
    assert "/notes/my-idea.md" in sftp.fs
    assert b"a brilliant plan" in sftp.fs["/notes/my-idea.md"]


def test_no_paramiko_needed_with_injected_factory():
    # The whole test module runs without a real paramiko connection.
    conn, sftp = _conn({"/notes/a.md": b"alpha"})
    conn.search("alpha")
    assert sftp.closed is True  # connection torn down after use


# --- endpoint: add an ssh source, password -> vault, persists ----------------


def test_add_ssh_source_stores_password_in_vault(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.post(
        "/ltm/sources",
        json={
            "name": "server-notes",
            "kind": "ssh",
            "host": "nas.local",
            "port": 22,
            "username": "me",
            "path": "/home/me/notes",
            "password": "s3cret",
        },
    )
    assert r.status_code == 200 and r.json()["kind"] == "ssh"
    # the password went to the vault (never the DB record)
    secrets = {s["name"] for s in client.get("/secrets").json()["secrets"]}
    assert any("ssh" in s for s in secrets)
    # and the source is listed + persisted with no plaintext password
    src = {s["name"]: s for s in client.get("/ltm/sources").json()["sources"]}
    assert src["server-notes"]["host"] == "nas.local"
    assert "s3cret" not in str(src["server-notes"])


def test_add_ssh_source_requires_host_and_path(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.post("/ltm/sources", json={"name": "x", "kind": "ssh", "host": "", "path": ""})
    assert r.status_code == 400
