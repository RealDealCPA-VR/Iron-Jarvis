"""Agent identity (v1.171.0, P2): roster activity join + portraits.

Frozen contracts under test:

* ``GET /agents/roster`` rows gain ADDITIVE ``last_active`` / ``last_message``
  / ``avatar`` — real joined data or honest nulls, NEVER invented activity.
  The join is one query pass over the agent-thread store, and the preview is
  injection-safe plain text clipped to <=140 chars.
* ``GET|POST|DELETE /agents/{name}/avatar`` — portraits stored by name under
  ``<home>/avatars/<slug>.png`` (the file's existence IS the record; no schema
  change). Upload is content-sniffed (PNG/JPEG/WebP), <=2MB decoded, and
  normalized to <=512px PNG. Generate goes through the platform's existing
  Pixio image path; with no image model configured the answer is an HONEST
  409 naming what's missing — never a placeholder image.

Mutation-minded: nulls are asserted where the join must stay silent (a
mutation that always emits a URL or a timestamp fails here), the 409 asserts
the file was NOT created, and the slug test proves lossy sanitization cannot
merge two agents' portraits.
"""

from __future__ import annotations

import base64
from io import BytesIO
from types import SimpleNamespace

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import agents as agents_routes
from iron_jarvis.daemon.routes.agents import (
    _avatar_slug,
    _preview_text,
    _sniff_image,
    _thread_activity,
)


def _client(tmp_path, **kw):
    return TestClient(create_app(str(tmp_path)), **kw)


def _avatars_dir(tmp_path):
    return tmp_path / ".ironjarvis" / "avatars"


def _image_b64(fmt="PNG", size=(4, 4)) -> str:
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", size, (200, 40, 40)).save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode()


def _make_agent(client, name="analyst", description="tax workpapers"):
    r = client.post(
        "/agents",
        json={
            "name": name,
            "system_prompt": "You are a careful analyst.",
            "tools": [],
            "description": description,
            "provider": "",
            "model": "",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _roster(client) -> dict:
    r = client.get("/agents/roster")
    assert r.status_code == 200
    return {e["name"]: e for e in r.json()["roster"]}


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def test_avatar_slug_preserves_identity_and_never_collides():
    # Clean names pass through verbatim — the identity is never eaten.
    assert _avatar_slug("builder") == "builder"
    assert _avatar_slug("my-agent_2.0") == "my-agent_2.0"
    # Sanitized names carry a digest of the ORIGINAL, so distinct hostile
    # names can never merge onto one portrait file.
    slugs = {_avatar_slug(n) for n in ("a/b", "a_b", "a b", "a:b", "a?b")}
    assert len(slugs) == 5
    # Every slug is one path-safe segment: no separators, never empty.
    import re

    for n in ("../../evil", "..", "///", "", "  ", "名前", "a\x00b"):
        slug = _avatar_slug(n)
        assert slug and re.fullmatch(r"[A-Za-z0-9._-]+", slug), (n, slug)
        assert "/" not in slug and "\\" not in slug and ".." != slug


def test_avatar_slug_case_fold_prevents_merges_on_case_insensitive_fs():
    """NTFS/APFS (the SHIPPING filesystems) are case-insensitive: two slugs
    differing only by case resolve to ONE file, so before the fix 'Analyst'
    uploading a portrait overwrote 'analyst', DELETE removed both, and
    _avatar_url reported the wrong agent as having one (proven live on this
    machine). Case-folding is lossy sanitization like any other."""
    # Clean lowercase names keep byte-identical filenames — no migration.
    assert _avatar_slug("analyst") == "analyst"
    slugs = [_avatar_slug(n) for n in ("analyst", "Analyst", "ANALYST", "AnAlYsT")]
    # Every slug is fully lowercase, so case can never be the only difference
    # between two stored paths — a case-insensitive filesystem cannot merge.
    assert all(s == s.lower() for s in slugs)
    assert len(set(slugs)) == 4  # four names, four files


def test_avatar_slug_windows_reserved_device_names_are_defused():
    """avatars/nul.png IS the NUL device on Windows: the write 'succeeds'
    into the void, POST claims success, GET 404s. The device match keys on
    the segment before the FIRST dot (nul.txt is still NUL), so the digest
    is PREFIXED — appending would leave 'nul.txt-<digest>' matching."""
    import re

    reserved = {"con", "prn", "aux", "nul"} | {
        f"{base}{i}" for base in ("com", "lpt") for i in "123456789"
    }
    for name in ("NUL", "nul", "CON", "prn", "AUX", "com1", "COM9", "lpt1", "nul.txt"):
        slug = _avatar_slug(name)
        assert slug.split(".", 1)[0].lower() not in reserved, (name, slug)
        assert re.fullmatch(r"[A-Za-z0-9._-]+", slug)
    # Distinct casings of a reserved name still map to distinct files.
    assert _avatar_slug("NUL") != _avatar_slug("nul")


def test_preview_text_collapses_whitespace_and_clips_to_140():
    assert _preview_text("hello") == "hello"
    assert _preview_text(None) == ""
    # Newlines/tabs (layout escape + mild injection surface) collapse.
    assert _preview_text("line one\nEVIL: two\n\tthree") == "line one EVIL: two three"
    long = "word " * 60  # 300 chars
    clipped = _preview_text(long)
    assert len(clipped) <= 140
    assert clipped.endswith("…")
    # Exactly-140 input is NOT clipped (boundary).
    exact = "x" * 140
    assert _preview_text(exact) == exact


def test_preview_text_strips_control_and_bidi_characters():
    """'Injection-safe plain text' (frozen contract 1): the whitespace collapse
    alone let ESC (terminal escape), BEL, NUL and U+202E RLO — which visually
    REVERSES the preview, spoofing what the agent appears to have said —
    through verbatim (proven pre-fix). They are stripped, never rendered."""
    hostile = "hi\x1b]0;evil\x07 there \u202egnp.exe"
    out = _preview_text(hostile)
    assert out == "hi]0;evil there gnp.exe"
    for ch in ("\x1b", "\x07", "\x00", "\u202e"):
        assert ch not in out
    # NUL vanishes without splitting the word; C1, bidi isolates + marks too.
    assert _preview_text("a\x00b") == "ab"
    assert _preview_text("x\x9by \u2066z\u2069 \u200f!") == "xy z !"
    # Stripping never leaves doubled spaces behind.
    assert "  " not in _preview_text("a \x1b b")


def test_thread_activity_preview_is_control_free():
    """An RLO/ESC-bearing thread message yields a CLEAN roster preview — the
    join applies the same injection-safe rule as the pure helper."""
    import json

    records = [
        SimpleNamespace(
            messages_json=json.dumps(
                [
                    {
                        "who": "builtin:builder",
                        "content": "ok\x1b[31m \u202e done",
                        "at": "2026-08-01T10:00:00+00:00",
                    }
                ]
            )
        )
    ]
    _at, preview = _thread_activity(records)["builtin:builder"]
    assert preview == "ok[31m done"
    assert "\x1b" not in preview and "\u202e" not in preview


def test_thread_activity_newest_wins_and_users_and_garbage_are_skipped():
    def rec(msgs_json):
        return SimpleNamespace(messages_json=msgs_json)

    import json

    records = [
        rec(
            json.dumps(
                [
                    {"who": "user", "content": "hi", "at": "2026-08-01T10:00:00+00:00"},
                    {
                        "who": "builtin:builder",
                        "content": "older reply",
                        "at": "2026-08-01T10:00:01+00:00",
                    },
                    {"who": "builtin:builder", "content": "ignored", "at": "not-a-date"},
                ]
            )
        ),
        rec("{corrupt json"),
        rec(
            json.dumps(
                [
                    {
                        "who": "builtin:builder",
                        "content": "newest reply",
                        "at": "2026-08-02T09:00:00+00:00",
                    },
                    # Honest error entries count as activity (content empty).
                    {
                        "who": "remote:mini",
                        "content": "",
                        "error": "mini is offline (disabled) — skipped.",
                        "at": "2026-08-02T09:00:01+00:00",
                    },
                ]
            )
        ),
    ]
    activity = _thread_activity(records)
    assert activity["builtin:builder"] == (
        "2026-08-02T09:00:00+00:00",
        "newest reply",
    )
    assert activity["remote:mini"][1] == "mini is offline (disabled) — skipped."
    assert "user" not in activity
    # Empty/None input never raises.
    assert _thread_activity([]) == {}
    assert _thread_activity(None) == {}


def test_sniff_image_by_content_never_by_name():
    assert _sniff_image(base64.b64decode(_image_b64("PNG"))) == "png"
    assert _sniff_image(base64.b64decode(_image_b64("JPEG"))) == "jpeg"
    assert _sniff_image(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"
    assert _sniff_image(b"#!/usr/bin/env python\nprint('hi')") is None
    assert _sniff_image(b"") is None


# --------------------------------------------------------------------------- #
# Roster join (contract 1)
# --------------------------------------------------------------------------- #


def test_roster_rows_carry_honest_nulls_when_nothing_happened(tmp_path):
    with _client(tmp_path) as client:
        rows = _roster(client)
        assert "builder" in rows
        for row in rows.values():
            # Additive fields PRESENT on every row, and all null — a mutation
            # inventing activity or a portrait URL fails here.
            assert row["last_active"] is None
            assert row["last_message"] is None
            assert row["avatar"] is None
            # The pre-v1.171.0 shape is intact.
            for key in ("name", "kind", "description", "delegable", "healthy", "stats", "line"):
                assert key in row


def test_roster_joins_the_newest_thread_entry_per_participant(tmp_path):
    with _client(tmp_path) as client:
        _make_agent(client, "analyst")
        r = client.post(
            "/agents/threads",
            json={
                "title": "panel",
                "participants": [
                    {"source": "builtin", "name": "builder", "role": "lead"},
                    {"source": "dynamic", "name": "analyst", "role": "critic"},
                ],
            },
        )
        assert r.status_code == 200, r.text
        tid = r.json()["id"]
        r = client.post(f"/agents/threads/{tid}/say", json={"message": "hello team"})
        assert r.status_code == 200, r.text

        msgs = client.get(f"/agents/threads/{tid}").json()["messages"]
        rows = _roster(client)
        for key, roster_name in (
            ("builtin:builder", "builder"),
            ("dynamic:analyst", "custom:analyst"),
        ):
            newest = [m for m in msgs if m["who"] == key][-1]
            row = rows[roster_name]
            assert row["last_active"] == newest["at"]
            expected = " ".join(str(newest.get("content") or newest.get("error") or "").split())
            if len(expected) > 140:
                expected = expected[:139].rstrip() + "…"
            assert row["last_message"] == expected
            assert row["last_message"]  # the round really produced text
        # A participant that never spoke stays honestly null.
        assert rows["researcher"]["last_active"] is None
        assert rows["researcher"]["last_message"] is None


def test_roster_newest_round_wins(tmp_path):
    with _client(tmp_path) as client:
        r = client.post(
            "/agents/threads",
            json={
                "title": "panel",
                "participants": [
                    {"source": "builtin", "name": "builder", "role": "lead"}
                ],
            },
        )
        tid = r.json()["id"]
        client.post(f"/agents/threads/{tid}/say", json={"message": "round one"})
        first = _roster(client)["builder"]["last_active"]
        client.post(f"/agents/threads/{tid}/say", json={"message": "round two"})
        second = _roster(client)["builder"]["last_active"]
        assert first is not None and second is not None
        assert second > first  # strictly newer — the join tracks the max


# --------------------------------------------------------------------------- #
# Portraits (contract 2)
# --------------------------------------------------------------------------- #


def test_avatar_upload_serve_roster_delete_roundtrip_builtin(tmp_path):
    with _client(tmp_path) as client:
        r = client.post("/agents/builder/avatar", json={"image_b64": _image_b64("PNG")})
        assert r.status_code == 200, r.text
        assert r.json() == {
            "name": "builder",
            "avatar": "/agents/builder/avatar",
            "source": "upload",
            "bytes": r.json()["bytes"],
        }
        # Serve: PNG bytes, INLINE disposition (an <img> must render it).
        r = client.get("/agents/builder/avatar")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.headers["content-disposition"].startswith("inline")
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
        # The roster row now points at it; everyone else stays null.
        rows = _roster(client)
        assert rows["builder"]["avatar"] == "/agents/builder/avatar"
        assert rows["researcher"]["avatar"] is None
        # Delete means deleted.
        assert client.delete("/agents/builder/avatar").json() == {"removed": "builder"}
        assert client.get("/agents/builder/avatar").status_code == 404
        assert _roster(client)["builder"]["avatar"] is None
        # Deleting again is an honest 404, not a silent success.
        assert client.delete("/agents/builder/avatar").status_code == 404


def test_avatar_jpeg_upload_is_normalized_to_small_png(tmp_path):
    from PIL import Image

    with _client(tmp_path) as client:
        r = client.post(
            "/agents/builder/avatar",
            json={"image_b64": _image_b64("JPEG", size=(800, 600))},
        )
        assert r.status_code == 200, r.text
        served = client.get("/agents/builder/avatar").content
        assert served[:8] == b"\x89PNG\r\n\x1a\n"  # stored as PNG, whatever came in
        with Image.open(BytesIO(served)) as im:
            assert max(im.size) <= 512  # normalized down


def test_avatar_for_dynamic_slug_reaches_roster_and_agents_list(tmp_path):
    with _client(tmp_path) as client:
        _make_agent(client, "analyst")
        r = client.post("/agents/analyst/avatar", json={"image_b64": _image_b64()})
        assert r.status_code == 200, r.text
        assert _roster(client)["custom:analyst"]["avatar"] == "/agents/analyst/avatar"
        # GET /agents (the Setup card's feed) carries it too — additive field.
        dyn = client.get("/agents").json()["dynamic"]
        row = next(a for a in dyn if a["name"] == "analyst")
        assert row["avatar"] == "/agents/analyst/avatar"
        # An agent without a portrait reports null there as well.
        _make_agent(client, "plain")
        dyn = client.get("/agents").json()["dynamic"]
        assert next(a for a in dyn if a["name"] == "plain")["avatar"] is None


def test_avatar_rejects_non_images_bad_b64_and_bad_bodies(tmp_path):
    with _client(tmp_path) as client:
        # Content sniff: not an image → 415, nothing stored.
        b64 = base64.b64encode(b"#!/bin/sh\necho hi").decode()
        assert client.post("/agents/builder/avatar", json={"image_b64": b64}).status_code == 415
        assert client.get("/agents/builder/avatar").status_code == 404
        # Garbage "base64" decodes to nothing image-shaped (b64decode with
        # validate=False strips foreign chars) → the content sniff's 415.
        r = client.post("/agents/builder/avatar", json={"image_b64": "!!!"})
        assert r.status_code == 415
        # Exactly one of image_b64 / generate.
        assert client.post("/agents/builder/avatar", json={}).status_code == 400
        assert (
            client.post(
                "/agents/builder/avatar",
                json={"image_b64": _image_b64(), "generate": True},
            ).status_code
            == 400
        )
        # Sniffable header but undecodable body → 415, not a stored corrupt file.
        junk = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32).decode()
        assert client.post("/agents/builder/avatar", json={"image_b64": junk}).status_code == 415
        assert client.get("/agents/builder/avatar").status_code == 404


def test_avatar_oversize_is_413_before_decoding(tmp_path):
    with _client(tmp_path) as client:
        # ~3MB decoded (4MB of base64 chars) — rejected on length, pre-decode.
        r = client.post("/agents/builder/avatar", json={"image_b64": "A" * 4_000_000})
        assert r.status_code == 413
        assert "2 MB" in r.json()["detail"]


def test_generate_without_image_model_is_an_honest_409(tmp_path, monkeypatch):
    monkeypatch.delenv("PIXIO_API_KEY", raising=False)
    with _client(tmp_path) as client:
        r = client.post("/agents/builder/avatar", json={"generate": True})
        assert r.status_code == 409
        detail = r.json()["detail"]
        # Names exactly what is missing and how to fix it — no placeholder.
        assert "pixio" in detail and "PIXIO_API_KEY" in detail
        assert client.get("/agents/builder/avatar").status_code == 404
        # And nothing landed on disk.
        avatars = _avatars_dir(tmp_path)
        assert not avatars.exists() or not list(avatars.iterdir())


def test_generate_with_key_stores_the_real_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("PIXIO_API_KEY", "pxio_live_test")
    calls: list[tuple[str, str]] = []
    png = base64.b64decode(_image_b64("PNG", size=(64, 64)))

    def fake_generate(key: str, prompt: str, **kw) -> bytes:
        calls.append((key, prompt))
        return png

    monkeypatch.setattr(agents_routes, "_generate_avatar_bytes", fake_generate)
    with _client(tmp_path) as client:
        _make_agent(client, "analyst", description="chases  unpaid\ninvoices")
        r = client.post("/agents/analyst/avatar", json={"generate": True})
        assert r.status_code == 200, r.text
        assert r.json()["source"] == "generated"
        assert calls and calls[0][0] == "pxio_live_test"
        prompt = calls[0][1]
        # The prompt names the agent and folds in its (collapsed) purpose.
        assert "analyst" in prompt
        assert "chases unpaid invoices" in prompt
        assert client.get("/agents/analyst/avatar").status_code == 200


def test_generate_failure_is_a_424_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("PIXIO_API_KEY", "pxio_live_test")

    def boom(key: str, prompt: str, **kw) -> bytes:
        raise RuntimeError("Pixio: insufficient credits (402) — top up the account")

    monkeypatch.setattr(agents_routes, "_generate_avatar_bytes", boom)
    with _client(tmp_path) as client:
        r = client.post("/agents/builder/avatar", json={"generate": True})
        assert r.status_code == 424
        assert "insufficient credits" in r.json()["detail"]
        assert client.get("/agents/builder/avatar").status_code == 404


def test_generated_non_image_bytes_are_refused_never_stored(tmp_path, monkeypatch):
    monkeypatch.setenv("PIXIO_API_KEY", "pxio_live_test")
    monkeypatch.setattr(
        agents_routes, "_generate_avatar_bytes", lambda key, prompt, **kw: b"<html>error page</html>"
    )
    with _client(tmp_path) as client:
        r = client.post("/agents/builder/avatar", json={"generate": True})
        assert r.status_code == 415
        assert "image model returned" in r.json()["detail"]
        assert client.get("/agents/builder/avatar").status_code == 404


def test_hostile_names_store_inside_avatars_dir_without_colliding(tmp_path):
    with _client(tmp_path) as client:
        for name in ("a:b", "a_b"):
            from urllib.parse import quote

            r = client.post(
                f"/agents/{quote(name, safe='')}/avatar",
                json={"image_b64": _image_b64()},
            )
            assert r.status_code == 200, r.text
        avatars = _avatars_dir(tmp_path)
        files = sorted(p.name for p in avatars.iterdir())
        assert len(files) == 2  # two names, two files — never merged
        for f in avatars.iterdir():
            # Every stored file sits DIRECTLY inside the avatars dir.
            assert f.parent == avatars
            assert f.suffix == ".png"
        # And each serves back under its own (encoded) name.
        from urllib.parse import quote

        for name in ("a:b", "a_b"):
            assert client.get(f"/agents/{quote(name, safe='')}/avatar").status_code == 200


def test_case_variant_names_keep_separate_portraits_live(tmp_path):
    """Proven live on this (case-insensitive NTFS) machine before the fix:
    'analyst' and 'Analyst' produced distinct slugs that resolved to ONE
    path — upload for one overwrote the other and DELETE removed both."""
    from PIL import Image

    with _client(tmp_path) as client:
        r = client.post(
            "/agents/analyst/avatar", json={"image_b64": _image_b64(size=(4, 4))}
        )
        assert r.status_code == 200, r.text
        r = client.post(
            "/agents/Analyst/avatar", json={"image_b64": _image_b64(size=(8, 8))}
        )
        assert r.status_code == 200, r.text
        assert len(list(_avatars_dir(tmp_path).iterdir())) == 2  # never merged
        # Each name serves ITS OWN bytes back (sizes differ on purpose).
        with Image.open(BytesIO(client.get("/agents/analyst/avatar").content)) as im:
            assert im.size == (4, 4)
        with Image.open(BytesIO(client.get("/agents/Analyst/avatar").content)) as im:
            assert im.size == (8, 8)
        # Deleting one leaves the other untouched.
        assert client.delete("/agents/Analyst/avatar").status_code == 200
        assert client.get("/agents/Analyst/avatar").status_code == 404
        assert client.get("/agents/analyst/avatar").status_code == 200


def test_reserved_device_name_stores_a_real_file_that_serves_back(tmp_path):
    """A 'NUL' agent's PNG must land in the avatars dir, not the null device
    (where POST claims success while GET 404s — proven live pre-fix)."""
    with _client(tmp_path) as client:
        r = client.post("/agents/NUL/avatar", json={"image_b64": _image_b64()})
        assert r.status_code == 200, r.text
        files = list(_avatars_dir(tmp_path).iterdir())
        assert len(files) == 1 and files[0].stat().st_size > 0  # a REAL file
        assert client.get("/agents/NUL/avatar").status_code == 200
        assert client.delete("/agents/NUL/avatar").status_code == 200
        assert client.get("/agents/NUL/avatar").status_code == 404


def test_avatar_store_is_atomic_and_a_failed_publish_leaves_nothing(tmp_path, monkeypatch):
    """The store is temp-file + os.replace (the creative-thumbs convention):
    a concurrent GET can never be served a half-written PNG, and a failed
    publish leaves neither a torn target nor a .tmp orphan. Failing
    os.replace proves the write PATH — a mutation back to a direct
    p.write_bytes() stores the file anyway and fails here."""
    import os as _os

    real_replace = _os.replace

    def boom(src, dst):
        raise OSError("disk detached mid-publish")

    with _client(tmp_path, raise_server_exceptions=False) as client:
        # Patch AFTER boot so only the avatar publish sees the failure.
        monkeypatch.setattr(agents_routes.os, "replace", boom)
        r = client.post("/agents/builder/avatar", json={"image_b64": _image_b64()})
        assert r.status_code == 500
        avatars = _avatars_dir(tmp_path)
        leftovers = list(avatars.iterdir()) if avatars.exists() else []
        assert leftovers == []  # no torn target, no orphaned temp
        assert client.get("/agents/builder/avatar").status_code == 404
        monkeypatch.setattr(agents_routes.os, "replace", real_replace)
        r = client.post("/agents/builder/avatar", json={"image_b64": _image_b64()})
        assert r.status_code == 200, r.text
        files = list(_avatars_dir(tmp_path).iterdir())
        # Exactly the published PNG — the temp never lingers after success.
        assert [f.suffix for f in files] == [".png"]


def test_empty_avatar_name_is_not_served(tmp_path):
    with _client(tmp_path) as client:
        # "%20" → a blank name after strip; must 404, never touch the disk.
        assert client.get("/agents/%20/avatar").status_code == 404
        assert client.delete("/agents/%20/avatar").status_code == 404
