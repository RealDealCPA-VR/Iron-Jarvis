"""v1.410.0 — animated backgrounds, downloaded on pick and run sealed off.

Pins:
* THE SHA256 PIN: both catalogs (this package's and the dashboard's twin)
  name the same five backgrounds, and every size + sha256 is the file in the
  repository — a background edited without updating both fails here.
* The files keep their contract: small, no network words, the pause/still
  messages, reduced motion, only source-over blending.
* The download: one file, from the INSTALLED version's tag, no redirects, a
  size cap, a sha256 check in plain words; a saved file is never fetched again;
  after an update an out-of-date file reads ``outdated`` and is fetched again.
* The view: served only while it matches the catalog, under a no-network CSP
  with the ``sandbox`` directive; token-exempt for exactly that leaf.
* Delete frees the space; backups never carry the files.

The network is never touched: ``store.FETCH`` is replaced, and the real
``_fetch`` is driven against a loopback server.
"""

from __future__ import annotations

import hashlib
import http.server
import re
import tarfile
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis import __version__
from iron_jarvis.backgrounds import catalog, store
from iron_jarvis.daemon import auth
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import backgrounds as bg_routes

ROOT = Path(__file__).resolve().parents[1]
TS_CATALOG = ROOT / "dashboard" / "lib" / "backgroundCatalog.ts"


def _file(bg_id: str) -> Path:
    return ROOT / "backgrounds" / bg_id / "index.html"


def _ts_entries() -> list[dict]:
    text = TS_CATALOG.read_text(encoding="utf-8").replace("\r\n", "\n")
    out = []
    for m in re.finditer(
        r'id: "([a-z0-9-]+)",\s*name: "([^"]+)",\s*blurb: "[^"]*",\s*size: (\d+),\s*sha256: "([0-9a-f]*)"',
        text,
    ):
        out.append({"id": m[1], "name": m[2], "size": int(m[3]), "sha256": m[4]})
    return out


# --------------------------------------------------------------- the catalog


def test_five_backgrounds_each_pinned_to_its_file_in_the_repository():
    assert len(catalog.BACKGROUNDS) == 5
    for bg in catalog.BACKGROUNDS:
        body = _file(bg.id).read_bytes()
        assert bg.size == len(body), f"{bg.id}: catalog size is stale — update both catalogs"
        assert bg.sha256 == hashlib.sha256(body).hexdigest(), (
            f"{bg.id}: catalog sha256 is stale — update catalog.py and backgroundCatalog.ts"
        )


def test_the_dashboard_catalog_is_the_same_list():
    ts = _ts_entries()
    py = [{"id": b.id, "name": b.name, "size": b.size, "sha256": b.sha256} for b in catalog.BACKGROUNDS]
    assert ts == py


def test_every_folder_in_backgrounds_is_in_the_catalog():
    folders = sorted(p.name for p in (ROOT / "backgrounds").iterdir() if p.is_dir())
    assert folders == sorted(b.id for b in catalog.BACKGROUNDS)


def test_the_download_address_is_pinned_to_the_running_versions_tag():
    url = catalog.source_url("aurora")
    assert url == (
        f"https://raw.githubusercontent.com/RealDealCPA-VR/Iron-Jarvis/v{__version__}/backgrounds/aurora/index.html"
    )
    assert "/master/" not in url and "/main/" not in url


def test_the_backgrounds_folder_keeps_its_bytes_on_checkout():
    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "backgrounds/** -text" in attrs


def test_the_backgrounds_are_not_in_the_installer():
    """The desktop build and the daemon spec ship nothing from backgrounds/."""
    pkg = (ROOT / "desktop" / "package.json").read_text(encoding="utf-8")
    spec = (ROOT / "packaging" / "ironjarvis.spec").read_text(encoding="utf-8")
    assert "backgrounds" not in pkg
    assert "backgrounds/" not in spec and "backgrounds\\\\" not in spec
    # The dashboard never imports a background file (only the catalog).
    for p in (ROOT / "dashboard").rglob("*.ts*"):
        if "node_modules" in p.parts or ".next" in p.parts[0:4]:
            continue
        if p.suffix not in (".ts", ".tsx"):
            continue
        assert "index.html" not in p.read_text(encoding="utf-8", errors="ignore") or p.name in (
            "backgroundCatalog.ts",
        ), p


# --------------------------------------------------------- the file contract

FORBIDDEN = [
    "http:",
    "https:",
    "fetch",
    "XMLHttpRequest",
    "WebSocket",
    "EventSource",
    "importScripts",
    "Worker",
    "eval(",
    "new Function",
    "src=",
    "href=",
    "url(",
    "@import",
    '"lighter"',
    '"screen"',
    "<link",
    "<iframe",
]


@pytest.mark.parametrize("bg_id", [b.id for b in catalog.BACKGROUNDS])
def test_each_file_keeps_the_contract(bg_id):
    text = _file(bg_id).read_text(encoding="utf-8")
    assert len(text.encode()) < 100_000
    for word in FORBIDDEN:
        assert word not in text, f"{bg_id} contains {word!r}"
    assert "requestAnimationFrame" in text and "cancelAnimationFrame" in text
    assert "e.source !== window.parent" in text
    assert '"ij-bg"' in text and '"ij-bg-ready"' in text
    assert "prefers-reduced-motion" in text
    assert "visibilitychange" in text
    assert "location.hash" in text
    assert f'var ID = "{bg_id}"' in text


# ----------------------------------------------------------------- the store


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def fetched(monkeypatch):
    """Serve the repository's files to the store; record each fetch."""
    calls: list[str] = []

    def fake(url: str, cap: int) -> bytes:
        calls.append(url)
        bg_id = url.rstrip("/").split("/")[-2]
        return _file(bg_id).read_bytes()

    monkeypatch.setattr(store, "FETCH", fake)
    monkeypatch.delenv(store.BASE_ENV, raising=False)
    return calls


def test_a_pick_downloads_that_one_file_from_the_tag_and_never_again(home, fetched):
    row = store.download(home, "waves")
    assert row["state"] == "ready" and row["fetched"] is True
    assert fetched == [catalog.source_url("waves")]
    assert (home / "backgrounds" / "waves.html").read_bytes() == _file("waves").read_bytes()
    again = store.download(home, "waves")
    assert again["fetched"] is False
    assert len(fetched) == 1, "a saved background was fetched again"
    others = {r["id"]: r["state"] for r in store.list_state(home)["backgrounds"]}
    assert others == {"aurora": "absent", "particles": "absent", "waves": "ready", "grid-beams": "absent", "reactor": "absent"}


def test_a_mismatched_download_is_refused_in_words_and_nothing_is_saved(home, monkeypatch):
    monkeypatch.setattr(store, "FETCH", lambda url, cap: b"<html>not ours</html>")
    with pytest.raises(store.BackgroundError) as e:
        store.download(home, "aurora")
    assert e.value.words == store.MISMATCH
    assert not (home / "backgrounds" / "aurora.html").exists()


def test_an_unknown_background_is_a_404(home):
    with pytest.raises(store.BackgroundError) as e:
        store.download(home, "../../etc")
    assert e.value.status == 404


def test_after_an_update_a_changed_file_is_outdated_and_fetched_again(home, fetched):
    store.download(home, "reactor")
    saved = home / "backgrounds" / "reactor.html"
    saved.write_bytes(b"an older release's file")  # the catalog moved on
    assert store.state_of(home, catalog.get("reactor"))["state"] == "outdated"
    assert store.read_verified(home, "reactor") is None
    row = store.download(home, "reactor")
    assert row["state"] == "ready" and row["fetched"] is True
    assert len(fetched) == 2


def test_delete_frees_the_space(home, fetched):
    store.download(home, "particles")
    size = catalog.get("particles").size
    assert store.list_state(home)["total_bytes"] == size
    assert store.delete(home, "particles") == {"id": "particles", "freed_bytes": size}
    assert store.list_state(home)["total_bytes"] == 0
    assert store.delete(home, "particles")["freed_bytes"] == 0


def test_the_files_live_beside_the_state_home_on_the_desktop(tmp_path):
    user_data = tmp_path / "Iron Jarvis"
    (user_data / "logs").mkdir(parents=True)
    h = user_data / ".ironjarvis"
    h.mkdir()
    assert store.folder_for(h) == user_data / "backgrounds"
    assert store.folder_for(tmp_path / "devhome") == tmp_path / "devhome" / "backgrounds"


def test_a_backup_never_carries_downloaded_backgrounds(home, fetched, tmp_path):
    from iron_jarvis.maintenance import create_backup

    store.download(home, "waves")
    (home / "config.toml").write_text("x = 1\n", encoding="utf-8")
    out, _n = create_backup(home, tmp_path / "b.tar.gz")
    with tarfile.open(out) as tar:
        names = tar.getnames()
    assert any(n.endswith("config.toml") for n in names)
    assert not any("backgrounds" in n for n in names)


def test_the_dev_source_override_is_loopback_only(monkeypatch):
    monkeypatch.setenv(store.BASE_ENV, "http://127.0.0.1:9/repo")
    assert store._source("aurora") == "http://127.0.0.1:9/repo/backgrounds/aurora/index.html"
    for bad in ("https://127.0.0.1:9", "http://example.com", "http://10.0.0.5:8000", "file:///C:/x"):
        monkeypatch.setenv(store.BASE_ENV, bad)
        assert store._source("aurora") == catalog.source_url("aurora")


# ------------------------------------------------- the real fetch, on loopback


class _Handler(http.server.BaseHTTPRequestHandler):
    routes: dict[str, tuple[int, dict, bytes]] = {}

    def do_GET(self):  # noqa: N802
        status, headers, body = self.routes.get(self.path, (404, {}, b""))
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", _Handler.routes
    srv.shutdown()
    _Handler.routes = {}


def test_the_real_fetch_follows_no_redirect(server):
    base, routes = server
    routes["/moved"] = (302, {"Location": base + "/real"}, b"")
    routes["/real"] = (200, {}, b"should never be read")
    with pytest.raises(store.BackgroundError) as e:
        store._fetch(base + "/moved", 1000)
    assert e.value.words == store.REDIRECTED


def test_the_real_fetch_stops_at_the_size_cap(server):
    base, routes = server
    routes["/big"] = (200, {}, b"x" * 5000)
    with pytest.raises(store.BackgroundError) as e:
        store._fetch(base + "/big", 1000)
    assert e.value.words == store.TOO_BIG
    assert store._fetch(base + "/big", 5000) == b"x" * 5000


def test_the_real_fetch_says_a_missing_release_file_in_words(server):
    base, _routes = server
    with pytest.raises(store.BackgroundError) as e:
        store._fetch(base + "/nothing", 1000)
    assert "GitHub does not have this background" in e.value.words


def test_the_real_fetch_has_a_timeout():
    assert store.TOTAL_TIMEOUT_S <= 30 and store.CONNECT_TIMEOUT_S <= 10


def test_download_end_to_end_through_the_loopback_source(home, server, monkeypatch):
    base, routes = server
    routes["/backgrounds/grid-beams/index.html"] = (200, {}, _file("grid-beams").read_bytes())
    monkeypatch.setenv(store.BASE_ENV, base)
    monkeypatch.setattr(store, "FETCH", store._fetch)
    assert store.download(home, "grid-beams")["state"] == "ready"


# ------------------------------------------------------------- the routes


@pytest.fixture
def client(tmp_path, monkeypatch, fetched):
    return TestClient(create_app(str(tmp_path / "home")))


def test_routes_list_download_view_delete(client):
    rows = client.get("/backgrounds").json()["backgrounds"]
    assert [r["id"] for r in rows] == [b.id for b in catalog.BACKGROUNDS]
    assert client.get("/backgrounds/aurora/view").status_code == 404
    r = client.post("/backgrounds/aurora/download")
    assert r.status_code == 200 and r.json()["state"] == "ready"
    v = client.get("/backgrounds/aurora/view")
    assert v.status_code == 200
    assert v.content == _file("aurora").read_bytes()
    assert v.headers["content-security-policy"] == bg_routes.VIEW_CSP
    assert v.headers["content-type"].startswith("text/html")
    d = client.delete("/backgrounds/aurora")
    assert d.json()["freed_bytes"] == catalog.get("aurora").size
    assert client.get("/backgrounds/aurora/view").status_code == 404
    assert client.post("/backgrounds/nope/download").status_code == 404


def test_a_refused_download_answers_one_sentence(client, monkeypatch):
    monkeypatch.setattr(store, "FETCH", lambda url, cap: b"tampered")
    r = client.post("/backgrounds/waves/download")
    assert r.status_code == 502
    assert r.json()["detail"] == store.MISMATCH


def test_the_view_csp_blocks_every_network_door():
    csp = bg_routes.VIEW_CSP
    assert csp.startswith("default-src 'none';")
    for directive in ("connect-src 'none'", "frame-src 'none'", "worker-src 'none'", "form-action 'none'", "base-uri 'none'"):
        assert directive in csp
    assert "sandbox allow-scripts" in csp
    assert "allow-same-origin" not in csp
    assert "http" not in csp and "*" not in csp


def test_only_the_view_leaf_is_token_exempt():
    assert auth._is_exempt("/backgrounds/aurora/view")
    assert auth._is_exempt("/backgrounds/grid-beams/view")
    assert not auth._is_exempt("/backgrounds")
    assert not auth._is_exempt("/backgrounds/aurora/download")
    assert not auth._is_exempt("/backgrounds/aurora")
    assert not auth._is_exempt("/backgrounds/evil/view")
    assert not auth._is_exempt("/backgrounds/../health/view")
