"""Per-agent FACE overrides (v1.180.0).

The user asked for the one thing the v1.171.0 face system could not do: "the
faces for each agent should be customizable with either a different shape, eyes
or color". The face was derived from the agent's NAME and nothing could change
it. These are the frozen contracts of the store and the routes that now can.

Under test, and each one is a way this feature could silently lie:

* ``PUT|GET|DELETE /agents/{name}/face`` round-trips a PARTIAL override. Set
  only ``shape`` and the colour and eyes must still DERIVE — an implementation
  that filled the other two in with today's seed would look identical right up
  until the seeding changed, and would have pinned two fields the user never
  chose.
* AN INVALID VALUE IS AN HONEST 400, per field, and writes NOTHING. A silent
  default would tell the user they picked something they did not.
* THE SLUG IS THE PORTRAIT'S SLUG. Both are asserted through the API on names
  the sanitizer must touch (``A/B``, ``nul``): two slug functions would drift
  the first time one learned about a new hostile shape, and an agent's face
  would then belong to a different agent than its portrait.
* READS ARE LENIENT: a record holding a value this build cannot draw degrades
  THAT FIELD to derived instead of erroring or rendering something the palette
  does not contain.
* ``face`` is served ADDITIVELY on ``GET /agents`` and ``GET /agents/roster``,
  null when there is no override — the same contract ``avatar`` froze.
* NOTHING BLOCKING ON THE EVENT LOOP (v1.153.1): the face file IO runs on a
  worker thread. Asserted structurally — the store function is called with NO
  running loop, which is only true off the loop thread.
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
from io import BytesIO
from urllib.parse import quote

from fastapi.testclient import TestClient

from iron_jarvis.agents import faces
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes.agents import _avatar_slug


def _client(tmp_path, **kw):
    return TestClient(create_app(str(tmp_path)), **kw)


def _home(tmp_path):
    return tmp_path / ".ironjarvis"


def _image_b64(fmt="PNG", size=(4, 4)) -> str:
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", size, (200, 40, 40)).save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode()


def _make_agent(client, name="analyst"):
    r = client.post(
        "/agents",
        json={
            "name": name,
            "system_prompt": "You are a careful analyst.",
            "tools": [],
            "description": "tax workpapers",
            "provider": "",
            "model": "",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# The store: pure helpers
# --------------------------------------------------------------------------- #


def test_allowed_sets_are_non_empty_and_lowercase_hex():
    """The picker offers these and the daemon validates against them, so a
    stray uppercase colour here would 400 a value the UI shows."""
    assert faces.FACE_SHAPES and faces.FACE_COLORS and faces.FACE_EYES
    assert all(c == c.lower() and c.startswith("#") for c in faces.FACE_COLORS)
    assert all(e == e.lower() for e in faces.FACE_EYES)
    # Eyes are a REAL NAMED SET, not free-form — the component draws geometry
    # per name, so every value must be a name it knows.
    assert "round" in faces.FACE_EYES and "visor" in faces.FACE_EYES


def test_normalize_override_is_partial_strict_and_case_insensitive():
    # Partial: one field in, one field out — the rest DERIVE.
    assert faces.normalize_override({"shape": "hexagon"}) == {"shape": "hexagon"}
    # Case and whitespace are forgiven; the stored value is canonical.
    assert faces.normalize_override({"color": "  #3FB1C9 "}) == {"color": "#3fb1c9"}
    # None and "" are UNSET ("I did not choose one"), never a stored blank.
    assert faces.normalize_override({"shape": None, "eyes": ""}) == {}
    assert faces.normalize_override(None) == {}
    # Strict on anything else.
    for bad in ({"shape": "octagon"}, {"eyes": "lasers"}, {"color": "#123456"}):
        try:
            faces.normalize_override(bad)
        except faces.FaceValueError as exc:
            assert exc.field in ("shape", "eyes", "color")
        else:  # pragma: no cover - the assertion below reports it
            raise AssertionError(f"{bad} was accepted")
    # A non-string is rejected, not coerced — {"shape": 3} must not index a list.
    try:
        faces.normalize_override({"shape": 3})
    except faces.FaceValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("a non-string shape was accepted")


def test_read_is_lenient_where_write_is_strict(tmp_path):
    """A record written by a NEWER build (or edited by hand) must degrade the
    unknown field to derived and keep the rest — never raise, never render a
    value this build cannot draw."""
    p = faces.face_path(tmp_path, "analyst")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"name": "analyst", "shape": "hexagon", "color": "#ff00ff"}),
        "utf-8",
    )
    assert faces.read_face(tmp_path, "analyst") == {"shape": "hexagon"}
    # Corrupt / absent / non-object records all mean "no override".
    p.write_text("{not json", "utf-8")
    assert faces.read_face(tmp_path, "analyst") == {}
    p.write_text("[1, 2, 3]", "utf-8")
    assert faces.read_face(tmp_path, "analyst") == {}
    assert faces.read_face(tmp_path, "nobody") == {}


def test_write_is_atomic_and_leaves_no_temp_files(tmp_path):
    faces.write_face(tmp_path, "analyst", {"shape": "drop"}, name="analyst")
    files = sorted(p.name for p in faces.faces_dir(tmp_path).iterdir())
    assert files == ["analyst.json"]  # no .tmp survivor
    assert faces.delete_face(tmp_path, "analyst") is True
    # Idempotent: resetting an already-derived face is not an error.
    assert faces.delete_face(tmp_path, "analyst") is False


def test_list_faces_keys_by_name_and_skips_nameless_records(tmp_path):
    faces.write_face(tmp_path, "a", {"shape": "pill"}, name="Analyst")
    faces.write_face(tmp_path, "b", {"eyes": "visor"}, name="")
    listed = faces.list_faces(tmp_path)
    # The ORIGINAL name is the key — a slug is lossy (lowercased + digested)
    # and cannot be reversed, so a nameless record is skipped rather than
    # guessed at and attached to the wrong agent.
    assert listed == {"Analyst": {"shape": "pill"}}


# --------------------------------------------------------------------------- #
# The routes
# --------------------------------------------------------------------------- #


def test_set_get_reset_round_trip_keeps_unset_fields_deriving(tmp_path):
    with _client(tmp_path) as client:
        _make_agent(client, "analyst")
        # No override yet — 200 with an honest null, not a 404 and not a
        # fabricated "current" face.
        r = client.get("/agents/analyst/face")
        assert r.status_code == 200, r.text
        assert r.json()["face"] is None
        assert r.json()["options"]["eyes"] == list(faces.FACE_EYES)

        # PARTIAL: only the shape is pinned.
        r = client.put("/agents/analyst/face", json={"shape": "hexagon"})
        assert r.status_code == 200, r.text
        assert r.json()["face"] == {"shape": "hexagon"}
        # ...and the other two are ABSENT, not filled in with today's seed.
        stored = client.get("/agents/analyst/face").json()["face"]
        assert stored == {"shape": "hexagon"}
        assert "color" not in stored and "eyes" not in stored

        # All three, and a later PUT REPLACES rather than merges.
        r = client.put(
            "/agents/analyst/face",
            json={"shape": "drop", "color": "#3fb1c9", "eyes": "visor"},
        )
        assert r.json()["face"] == {
            "shape": "drop",
            "color": "#3fb1c9",
            "eyes": "visor",
        }
        r = client.put("/agents/analyst/face", json={"eyes": "sleepy"})
        assert r.json()["face"] == {"eyes": "sleepy"}

        # Reset — and it is idempotent, because a Reset button that errors on
        # an already-derived face reports a failure where nothing failed.
        r = client.delete("/agents/analyst/face")
        assert r.status_code == 200 and r.json()["removed"] is True
        r = client.delete("/agents/analyst/face")
        assert r.status_code == 200 and r.json()["removed"] is False
        assert client.get("/agents/analyst/face").json()["face"] is None


def test_invalid_values_are_honest_400s_and_write_nothing(tmp_path):
    with _client(tmp_path) as client:
        _make_agent(client, "analyst")
        for body, field in (
            ({"shape": "octagon"}, "shape"),
            ({"eyes": "lasers"}, "eyes"),
            ({"color": "#ff00ff"}, "color"),
            ({"shape": 7}, "shape"),
        ):
            r = client.put("/agents/analyst/face", json=body)
            assert r.status_code == 400, (body, r.text)
            # The message NAMES the field and what it accepts — a bare
            # "invalid" would leave the user guessing which control was wrong.
            assert field in r.json()["detail"]
            assert "must be one of" in r.json()["detail"]
        # An empty override is refused too, and points at the reset verb
        # instead of storing a record with nothing in it.
        r = client.put("/agents/analyst/face", json={})
        assert r.status_code == 400
        assert "DELETE" in r.json()["detail"]
        # NOTHING was written by any of the above.
        assert not faces.faces_dir(_home(tmp_path)).exists()
        assert client.get("/agents/analyst/face").json()["face"] is None


def test_face_slug_is_the_portrait_slug_for_hostile_names(tmp_path):
    """An agent's portrait and its face key can never disagree — proven
    end-to-end on names the sanitizer MUST touch, not by re-calling the same
    helper the route calls."""
    with _client(tmp_path) as client:
        for name in ("A B", "nul", "Analyst", "名前"):
            enc = quote(name, safe="")
            r = client.put(f"/agents/{enc}/face", json={"shape": "pill"})
            assert r.status_code == 200, (name, r.text)
            r = client.post(f"/agents/{enc}/avatar", json={"image_b64": _image_b64()})
            assert r.status_code == 200, (name, r.text)
            face_files = {p.stem for p in faces.faces_dir(_home(tmp_path)).iterdir()}
            avatar_files = {p.stem for p in (_home(tmp_path) / "avatars").iterdir()}
            assert face_files == avatar_files, name
            # ...and it really is the one sanitizer, digest and all.
            assert _avatar_slug(name) in face_files
            for p in faces.faces_dir(_home(tmp_path)).iterdir():
                p.unlink()
            for p in (_home(tmp_path) / "avatars").iterdir():
                p.unlink()


def test_faces_listing_serves_every_override_and_the_allowed_sets(tmp_path):
    with _client(tmp_path) as client:
        _make_agent(client, "analyst")
        client.put("/agents/analyst/face", json={"color": "#c65949"})
        client.put("/agents/builder/face", json={"eyes": "square"})  # a built-in
        r = client.get("/agents/faces")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["faces"] == {
            "analyst": {"color": "#c65949"},
            "builder": {"eyes": "square"},
        }
        # The picker renders the DAEMON's sets, so it can never offer a value
        # the daemon would 400.
        assert body["options"]["shapes"] == list(faces.FACE_SHAPES)
        assert body["options"]["colors"] == list(faces.FACE_COLORS)


def test_agents_list_and_roster_carry_the_override_additively(tmp_path):
    with _client(tmp_path) as client:
        _make_agent(client, "analyst")
        # Before: the field is present and NULL — "derive from the name".
        row = next(a for a in client.get("/agents").json()["dynamic"] if a["name"] == "analyst")
        assert row["face"] is None
        entry = next(
            e for e in client.get("/agents/roster").json()["roster"] if "analyst" in e["name"]
        )
        assert entry["face"] is None

        client.put("/agents/analyst/face", json={"shape": "cloud", "eyes": "wide"})
        row = next(a for a in client.get("/agents").json()["dynamic"] if a["name"] == "analyst")
        assert row["face"] == {"shape": "cloud", "eyes": "wide"}
        # The roster names it "custom:analyst" but the face is keyed on the
        # BARE name, exactly like the portrait — so every surface agrees.
        entry = next(
            e for e in client.get("/agents/roster").json()["roster"] if "analyst" in e["name"]
        )
        assert entry["face"] == {"shape": "cloud", "eyes": "wide"}
        # ...and the rest of both payloads is untouched (additive).
        assert "avatar" in row and "effective_tools" in row
        assert entry["line"] and "last_active" in entry


def test_face_file_io_never_runs_on_the_event_loop(tmp_path, monkeypatch):
    """v1.153.1: the daemon is ONE asyncio loop and a blocking read on it does
    not look like a freeze — it looks like "Daemon offline".

    STRUCTURAL assertion: the store function is entered with NO running loop,
    which is only true on a worker thread. Delete the ``asyncio.to_thread`` in
    the route and ``get_running_loop()`` succeeds, flipping this red.
    """
    seen: dict[str, object] = {}
    real = faces.read_face

    def spy(home, slug):
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        seen["thread"] = threading.current_thread().name
        return real(home, slug)

    monkeypatch.setattr(faces, "read_face", spy)
    with _client(tmp_path) as client:
        assert client.get("/agents/analyst/face").status_code == 200
    assert seen["on_loop"] is False, "the face read ran ON the event loop"

    # The write path too — a PUT is the same blocking IO.
    seen.clear()
    real_write = faces.write_face

    def spy_write(home, slug, override, *, name=""):
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        return real_write(home, slug, override, name=name)

    monkeypatch.setattr(faces, "write_face", spy_write)
    with _client(tmp_path) as client:
        assert client.put("/agents/analyst/face", json={"shape": "pill"}).status_code == 200
    assert seen["on_loop"] is False, "the face write ran ON the event loop"


def test_a_broken_face_store_degrades_the_list_instead_of_500ing(tmp_path, monkeypatch):
    """A display field must never be able to break the agents list."""
    with _client(tmp_path) as client:
        _make_agent(client, "analyst")

        def boom(*_a, **_k):
            raise OSError("disk gone")

        monkeypatch.setattr(faces, "read_face", boom)
        r = client.get("/agents")
        assert r.status_code == 200
        assert next(a for a in r.json()["dynamic"] if a["name"] == "analyst")["face"] is None
        monkeypatch.setattr(faces, "list_faces", boom)
        assert client.get("/agents/faces").json()["faces"] == {}
