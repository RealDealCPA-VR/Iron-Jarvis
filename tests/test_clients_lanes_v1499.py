"""v1.499.0 (vision plan leap 3, Phase 3): a CLIENT's facts reach every seam
that works in that client's folders — and only those.

The seams (builder B2 of the clients design):

* BOTH chat lanes (``POST /chat`` = ``chat_turn.run_chat_turn``, ``POST
  /chat/stream`` = ``routes/chat.chat_stream``): the hop ``client`` in the
  ONE shared ``_gather_grounding`` (so lock step by construction), the
  ``_Grounding`` slot ``client`` joined in ``stable()`` as ``project +
  folder_rules + lessons + client + index`` — the cached head, BEFORE
  ``_apply_compaction`` / ``_plan_context`` (the planner prices it). The hop
  is REQUIRED (never dropped at the soft deadline) and BOUNDED
  (``workmap.clients.CLIENT_HOP_MAX_S`` from the gather's start, measured by
  the INJECTED ``chat_turn._grounding_clock``): past the bound, or when the
  block cannot be read, it is "" and ``context.late`` names it.
* the turn's CLIENT WALL (``workmap.clients.wall``): the POST lane runs the
  whole turn inside ``wall.turn_scope`` (``chat_turn._client_turn``), the
  stream lane iterates the whole stream inside ``wall.scoped_frames`` beside
  ``egress.scoped_frames`` — so every TOOL in the turn reads the turn's wall,
  computed from the OWNER's words (``_compose_recall_query``), never the
  model's.
* Chat › Code (the stream lane, ``workspace_dir = S.folder_of(row)``: a
  session on its own branch works in ``<repo>.ij-worktrees/<name>``, which
  the resolver maps to ``<repo>`` with ``keys.home_folder``).
* agent runs (``agents/runtime``: right after ``apply_to_prompt``, inside the
  ``skip_memory`` guard; the run inside ``wall.turn_scope`` with owner words
  = ``session.task``) and terminal help (``routes/terminals``: after the
  lessons, folder = the pane's ``cwd``).
* ``client_question`` ALWAYS on the POST response and the stream's done frame
  (null, never absent), and the client door — both lanes, MIRROR NOTE.

The clients are REAL: made through builder A's store (``store.create`` +
``store.keep_preference``), told through its ``client_block`` and walled by
builder B1's ``turn_wall``; the scene only SPIES on those two (each call is
recorded, then handed to the real function). A test that stalls or breaks a
hop replaces it for that test alone. Made-up names (Hartleys, Smith) and
made-up folders under the test's own tmp_path only. No wall-clock bars: a stalled hop waits on an
Event, and the bound is crossed by the injected clock.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import iron_jarvis.daemon.chat_turn as CT
import iron_jarvis.daemon.routes.chat as chat_routes
from iron_jarvis.daemon import doors as doors_mod
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolResult
from iron_jarvis.workmap import clients as C
from iron_jarvis.workmap import keys as K
from iron_jarvis.workmap.clients import block as client_block_mod
from iron_jarvis.workmap.clients import store as store_mod
from iron_jarvis.workmap.clients import wall as wall_mod

LANES = ("post", "stream")

BLOCK_H = (
    "# Client: Hartleys\n"
    "This chat's folder belongs to Hartleys, a client the owner confirmed.\n"
    "What the owner keeps for Hartleys (follow these for Hartleys' work unless the owner says "
    "otherwise in this chat; they hold for Hartleys only):\n"
    "- Use cash basis.\n"
    "A preference the owner states for Hartleys is kept for Hartleys only, never with "
    "remember_preference."
)
BLOCK_S = (
    "# Client: Smith\n"
    "This chat's folder belongs to Smith, a client the owner confirmed.\n"
    "What the owner keeps for Smith (follow these for Smith's work unless the owner says "
    "otherwise in this chat; they hold for Smith only):\n"
    "- Use accrual basis.\n"
    "A preference the owner states for Smith is kept for Smith only, never with "
    "remember_preference."
)
LESSON = "Prefers short answers with numbered steps"
LESSONS_HEAD = "# What I've learned about working with you"
FAB = "\n\nFABRIC-FOR:"


# --------------------------------------------------------------------------- #
# spies on the real block and the real wall
# --------------------------------------------------------------------------- #
class _Spies:
    """Records each ``client_block`` and ``turn_wall`` call, then hands it to
    the real function (builder A's block, builder B1's wall)."""

    def __init__(self):
        self.block_calls: list[str] = []
        self.wall_calls: list[tuple[str, str]] = []
        self.walls: list[C.TurnWall] = []
        self._block = client_block_mod.client_block_or_raise
        self._turn_wall = wall_mod.turn_wall

    def block(self, platform, *, folder, local=None):
        self.block_calls.append(str(folder))
        return self._block(platform, folder=folder, local=local)

    def turn_wall(self, engine, folder, user_text):
        self.wall_calls.append((str(folder), str(user_text)))
        got = self._turn_wall(engine, folder, user_text)
        self.walls.append(got)
        return got

    def install(self, monkeypatch):
        monkeypatch.setattr(client_block_mod, "client_block_or_raise", self.block)
        monkeypatch.setattr(wall_mod, "turn_wall", self.turn_wall)
        return self


@pytest.fixture()
def folders(tmp_path):
    hartleys = tmp_path / "Clients" / "Hartleys"
    smith = tmp_path / "Clients" / "Smith"
    for f in (hartleys / "2026" / "payroll", smith / "2025"):
        f.mkdir(parents=True)
    return {"hartleys": hartleys, "smith": smith, "root": tmp_path / "Clients"}


@pytest.fixture()
def scene(tmp_path, folders, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    client = TestClient(app)
    platform = app.state.platform
    platform.learning.note_preference(LESSON)
    hid = store_mod.create(platform, folder=str(folders["hartleys"]), name="Hartleys")["client"]["id"]
    sid = store_mod.create(platform, folder=str(folders["smith"]), name="Smith")["client"]["id"]
    store_mod.keep_preference(platform, hid, "Use cash basis.", origin="typed")
    store_mod.keep_preference(platform, sid, "Use accrual basis.", origin="typed")
    clients = _Spies().install(monkeypatch)

    class _Fabric:
        """Query-dependent snippets: everything before them is the stable head."""

        def ground(self, query, **kw):
            return f"{FAB}{query}"

        def __getattr__(self, item):
            raise AttributeError(item)

    monkeypatch.setattr(platform, "fabric", _Fabric())
    return type("Scene", (), {"app": app, "client": client, "platform": platform,
                              "clients": clients, "tmp": tmp_path, "hid": hid, "sid": sid,
                              **folders})


def _scripted(platform, monkeypatch, rounds=(), final="Done."):
    seen: dict = {"systems": [], "n": 0}

    def _next():
        n = seen["n"]
        seen["n"] += 1
        if n < len(rounds):
            return LLMResponse(text="", tool_calls=list(rounds[n]), usage={"input_tokens": 3, "output_tokens": 1})
        return LLMResponse(text=final, usage={"input_tokens": 3, "output_tokens": 1})

    async def fake_complete(*, system, messages, tools=None, **kw):
        seen["systems"].append(system)
        return RouteResult(_next(), "mock", "mock")

    async def fake_stream(*, system, messages, tools=None, **kw):
        seen["systems"].append(system)
        resp = _next()
        if resp.text:
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.router, "stream", fake_stream)
    return seen


def _frames(raw: str) -> list[tuple[str, dict]]:
    out = []
    for block in raw.split("\n\n"):
        block = block.strip()
        if not block or block.startswith(":"):
            continue
        ev, data = None, ""
        for line in block.splitlines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if ev:
            out.append((ev, json.loads(data or "{}")))
    return out


def _turn(client, lane: str, payload: dict) -> dict:
    """One turn on ``lane``: the POST response, or the stream's done frame."""
    if lane == "post":
        r = client.post("/chat", json=payload)
        assert r.status_code == 200, r.text
        return r.json()
    r = client.post("/chat/stream", json=payload)
    assert r.status_code == 200, r.text
    done = [d for e, d in _frames(r.text) if e == "done"]
    assert done, r.text[-800:]
    return done[-1]


def _body(text: str, folder: Path | None = None, **extra) -> dict:
    body = {"messages": [{"role": "user", "content": text}], "auto_tools": False, **extra}
    if folder is not None:
        body["workspace_dir"] = str(folder)
    return body


# --------------------------------------------------------------------------- #
# 1. both lanes: the same block, in the stable head, right after the lessons,
#    priced by the planner
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("where", ["hartleys", "hartleys_2026", "hartleys_payroll"])
def test_both_lanes_carry_the_same_client_block_in_the_stable_head_after_lessons(
    scene, monkeypatch, where,
):
    folder = {
        "hartleys": scene.hartleys,
        "hartleys_2026": scene.hartleys / "2026",
        "hartleys_payroll": scene.hartleys / "2026" / "payroll",
    }[where]
    planned: list[str] = []
    compacted: list[str] = []
    real_plan, real_compact = CT._plan_context, CT._apply_compaction

    def plan_spy(d, body, system, *a, **k):
        planned.append(system)
        return real_plan(d, body, system, *a, **k)

    async def compact_spy(d, body, system, *a, **k):
        compacted.append(system)
        return await real_compact(d, body, system, *a, **k)

    for mod in (CT, chat_routes):
        monkeypatch.setattr(mod, "_plan_context", plan_spy)
        monkeypatch.setattr(mod, "_apply_compaction", compact_spy)
    seen = _scripted(scene.platform, monkeypatch)
    systems = {}
    for lane in LANES:
        before = len(seen["systems"])
        done = _turn(scene.client, lane, _body("what is left for the March payroll?", folder))
        system = seen["systems"][before]
        systems[lane] = system
        assert "late" not in done["context"], done["context"]
        assert BLOCK_H in system, f"{lane}: the client block is missing"
        assert BLOCK_S not in system and "# Client: Smith" not in system, f"{lane}: another client's block"
        head = system[: system.index(FAB)]
        assert BLOCK_H in head, f"{lane}: the client block must sit in the stable head"
        lessons_at = head.index(LESSONS_HEAD)
        assert head.index(LESSON) < head.index(BLOCK_H), f"{lane}: the block comes after the lessons"
        assert lessons_at < head.index("# Client: Hartleys")
        # Exactly the lessons block, then the client block (nothing between).
        assert ("\n\n" + BLOCK_H) in head and head.split("\n\n" + BLOCK_H)[0].endswith(LESSON), (
            f"{lane}: the client block must follow the lessons directly"
        )
        assert BLOCK_H in planned[-1], f"{lane}: the planner never priced the client block"
        assert BLOCK_H in compacted[-1], f"{lane}: compaction was handed a prompt without it"
    assert systems["post"] == systems["stream"], "the two lanes sent different prompts"
    assert scene.clients.block_calls == [str(folder)] * 2, scene.clients.block_calls


@pytest.mark.parametrize("folder_of", ["smith", "root", "none", "outside"])
def test_no_turn_outside_hartleys_folders_hears_its_block(scene, monkeypatch, folder_of):
    """A Smith folder hears Smith's block, never Hartleys'; ``C:\\Clients``, a
    turn with no folder and a folder no client claims hear none."""
    folder = {
        "smith": scene.smith / "2025",
        "root": scene.root,
        "none": None,
        "outside": scene.tmp,
    }[folder_of]
    seen = _scripted(scene.platform, monkeypatch)
    for lane in LANES:
        before = len(seen["systems"])
        _turn(scene.client, lane, _body("what does Hartleys want for the payroll?", folder))
        system = seen["systems"][before]
        assert "# Client: Hartleys" not in system and "Use cash basis" not in system, (
            f"{lane}: Hartleys' block reached a turn in {folder_of}"
        )
        assert (BLOCK_S in system) == (folder_of == "smith"), lane
    if folder is None:
        assert scene.clients.block_calls == [], "no folder, no client hop"


def test_both_lanes_are_byte_identical_without_a_client(scene, monkeypatch):
    """A turn in a folder no client claims runs the hop (it really ran, in
    both lanes) and adds NOT ONE BYTE: the prompt equals the prompt of a turn
    whose client hop could not answer at all (raised: "" + named late), and
    the two lanes agree. The receipt carries no ``late`` key."""
    folder = scene.tmp / "Misc"
    folder.mkdir()
    seen = _scripted(scene.platform, monkeypatch)
    with_hop: dict[str, str] = {}
    for lane in LANES:
        before = len(seen["systems"])
        done = _turn(scene.client, lane, _body("tidy these notes", folder))
        with_hop[lane] = seen["systems"][before]
        assert "late" not in done["context"], done["context"]
    assert scene.clients.block_calls == [str(folder)] * 2  # the hop really ran

    # The REAL layer fails (the independent review): the record's own reads
    # raise, never a patched block, so a block that swallowed the error and
    # answered "" would go red here.
    def locked(*a, **k):
        raise C.ClientsUnreadable()

    for broken in ("load_map",):
        with monkeypatch.context() as m:
            m.setattr(store_mod, broken, locked)
            for lane in LANES:
                before = len(seen["systems"])
                done = _turn(scene.client, lane, _body("tidy these notes", folder))
                assert seen["systems"][before] == with_hop[lane], f"{lane}: an empty client hop added bytes"
                assert "client" in (done["context"].get("late") or []), (
                    f"{broken}/{lane}: a client block that could not be read must be named: {done['context']}"
                )
    assert with_hop["post"] == with_hop["stream"]


@pytest.mark.parametrize("lane", LANES)
def test_a_clients_preferences_that_cannot_be_read_are_named_on_the_receipt(scene, monkeypatch, lane):
    """In Hartleys' folder the map reads but the preferences do not (the
    record's own reader raises): the turn goes on without the block, and
    ``context.late`` names ``client`` (the independent review)."""
    seen = _scripted(scene.platform, monkeypatch)
    done = _turn(scene.client, lane, _body("payroll status?", scene.hartleys))
    assert "Use cash basis." in seen["systems"][0] and "late" not in done["context"]

    def locked(*a, **k):
        raise C.ClientsUnreadable()

    monkeypatch.setattr(store_mod, "preferences", locked)
    done = _turn(scene.client, lane, _body("payroll status?", scene.hartleys))
    assert "Use cash basis." not in seen["systems"][1]
    assert "client" in (done["context"].get("late") or []), done["context"]


# --------------------------------------------------------------------------- #
# 2. the client hop is REQUIRED and BOUNDED (injected clock, no sleep)
# --------------------------------------------------------------------------- #
def test_the_client_hop_is_one_of_the_required_hops():
    assert "client" in CT.GROUNDING_REQUIRED_HOPS
    assert C.CLIENT_HOP_MAX_S == 5


@pytest.mark.parametrize("lane", LANES)
def test_the_client_hop_is_required(scene, monkeypatch, lane):
    """With a soft deadline of ZERO every optional hop that has not answered
    is late — the client block (a stalled read) is still awaited, is in the
    prompt and is never named late. The clock is FROZEN, so its bound never
    runs out in this test however slow the machine is."""
    monkeypatch.setattr(CT, "GROUNDING_SOFT_S", 0.0)
    monkeypatch.setattr(CT, "_grounding_clock", lambda: 0.0)
    hold = threading.Event()

    def slow(platform, *, folder, local=None):
        hold.wait(1.0)  # a slow read (a locked table); awaited anyway
        return BLOCK_H

    monkeypatch.setattr(client_block_mod, "client_block_or_raise", slow)
    seen = _scripted(scene.platform, monkeypatch)
    done = _turn(scene.client, lane, _body("payroll status?", scene.hartleys))
    assert BLOCK_H in seen["systems"][0], f"{lane}: a required hop was dropped at the soft deadline"
    assert "client" not in (done["context"].get("late") or []), done["context"]


@pytest.mark.parametrize("lane", LANES)
def test_a_client_hop_past_its_bound_is_left_out_and_named(scene, monkeypatch, lane):
    """The INJECTED clock jumps past ``CLIENT_HOP_MAX_S`` between the gather's
    start and the bounded wait: the turn goes on (the model is asked while the
    read is still blocked — it is released only by the model call), the block
    is not in the prompt and ``context.late`` names ``client``."""
    monkeypatch.setattr(CT, "GROUNDING_SOFT_S", 0.0)
    ticks = iter([0.0])
    monkeypatch.setattr(CT, "_grounding_clock", lambda: next(ticks, 100.0))
    asked = threading.Event()
    entered = threading.Event()

    def stalled(platform, *, folder, local=None):
        entered.set()
        asked.wait(10)
        return BLOCK_H

    monkeypatch.setattr(client_block_mod, "client_block_or_raise", stalled)
    seen = _scripted(scene.platform, monkeypatch)
    real_complete, real_stream = scene.platform.router.complete, scene.platform.router.stream

    async def complete(**kw):
        asked.set()
        return await real_complete(**kw)

    async def stream(**kw):
        asked.set()
        async for f in real_stream(**kw):
            yield f

    monkeypatch.setattr(scene.platform.router, "complete", complete)
    monkeypatch.setattr(scene.platform.router, "stream", stream)
    try:
        done = _turn(scene.client, lane, _body("payroll status?", scene.hartleys))
    finally:
        asked.set()
    assert entered.is_set(), "the client hop never ran"
    assert BLOCK_H not in seen["systems"][0], f"{lane}: the turn waited past the client hop's bound"
    assert "client" in (done["context"].get("late") or []), done["context"]


# --------------------------------------------------------------------------- #
# 3. tools in a turn see the turn's wall, built from the OWNER's words
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", LANES)
def test_tools_in_a_turn_see_the_turns_wall_on_both_lanes(scene, monkeypatch, lane):
    from iron_jarvis.settings import records_clients as RC

    seen_by_tool: list[C.TurnWall] = []
    words_by_tool: list[str] = []

    async def invoke(name, args, ctx, permissions, overrides=None, **kw):
        seen_by_tool.append(wall_mod.current())
        words_by_tool.append(RC.owner_words())
        return ToolResult(ok=True, output="OUT")

    monkeypatch.setattr(scene.platform.registry, "invoke", invoke)
    _scripted(scene.platform, monkeypatch, rounds=[[
        ToolCall(id="c1", name="read_file", arguments={"path": "a.txt"}),
    ]], final="Smith's figures say otherwise.")
    owner_words = "what did we decide for Hartleys and the payroll last month?"
    body = _body(owner_words, scene.hartleys / "2026", tools=["read_file"], approval_mode="yolo")
    _turn(scene.client, lane, body)
    assert seen_by_tool, f"{lane}: the tool never ran"
    wall = scene.clients.walls[-1]
    assert all(w is wall for w in seen_by_tool), f"{lane}: a tool saw another wall: {seen_by_tool}"
    assert wall.client is not None and wall.client.client_id == scene.hid
    assert wall.named == frozenset({scene.hid})
    # Built from the OWNER's words and the turn's folder — never the model's.
    assert scene.clients.wall_calls[-1] == (str(scene.hartleys / "2026"), owner_words)
    assert "Smith" not in scene.clients.wall_calls[-1][1]
    # The client chat tools read the OWNER's words (records_clients.
    # OWNER_WORDS), the same text the wall was built from.
    assert words_by_tool and all(w == owner_words for w in words_by_tool), (lane, words_by_tool)
    # Reset when the turn ends: nothing outside a turn reads a client's wall.
    assert wall_mod.current() is C.NO_CLIENT
    assert RC.owner_words() == ""


@pytest.mark.parametrize("lane", LANES)
def test_a_wall_that_cannot_be_read_fails_closed(scene, monkeypatch, lane):
    seen_by_tool: list[C.TurnWall] = []

    async def invoke(name, args, ctx, permissions, overrides=None, **kw):
        seen_by_tool.append(wall_mod.current())
        return ToolResult(ok=True, output="OUT")

    def broken(engine, folder, user_text):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(wall_mod, "turn_wall", broken)
    monkeypatch.setattr(scene.platform.registry, "invoke", invoke)
    _scripted(scene.platform, monkeypatch, rounds=[[
        ToolCall(id="c1", name="read_file", arguments={"path": "a.txt"}),
    ]])
    _turn(scene.client, lane, _body("payroll?", scene.hartleys, tools=["read_file"], approval_mode="yolo"))
    assert seen_by_tool and all(w.ok is False for w in seen_by_tool), seen_by_tool
    assert seen_by_tool[0].folder_key == K.folder_key(scene.hartleys)


# --------------------------------------------------------------------------- #
# 4. the question under a reply and the door: both lanes, always a key
# --------------------------------------------------------------------------- #
QUESTION = {"id": "client_abcdefabcdef", "name": "Hartleys", "folders": 2, "work": 9}


@pytest.mark.parametrize("lane", LANES)
def test_the_client_question_is_always_on_both_lanes(scene, monkeypatch, lane):
    asked: list[dict] = []

    def question_for_turn(platform, *, folder, thread_id, budget_s=None, deadline=None):
        asked.append({"folder": folder, "thread_id": thread_id})
        # The lane hands over its own wait as a DEADLINE taken before the
        # thread started (v1.499.1): a thread that starts late has less left.
        assert budget_s is None and deadline is not None
        assert deadline <= time.monotonic() + CT.CLIENT_QUESTION_WAIT_S
        return dict(QUESTION, extra="never on the wire")

    monkeypatch.setattr(store_mod, "question_for_turn", question_for_turn)
    _scripted(scene.platform, monkeypatch)
    done = _turn(scene.client, lane, _body("hello", scene.hartleys, thread_id="t-1"))
    assert done["client_question"] == QUESTION, done.get("client_question")
    assert asked == [{"folder": str(scene.hartleys), "thread_id": "t-1"}]
    # No folder: the key is still there (null), and nothing was asked.
    done = _turn(scene.client, lane, _body("hello"))
    assert "client_question" in done and done["client_question"] is None
    assert len(asked) == 1


@pytest.mark.parametrize("lane", LANES)
def test_a_preference_suggestion_goes_first_and_no_question_rides_with_it(scene, monkeypatch, lane):
    from iron_jarvis.learning import preferences as prefs

    async def suggest(platform, body, **kw):
        return {"id": "lesson_1", "text": "Prefers tables", "count": 2, "quotes": [], "since": ""}

    monkeypatch.setattr(prefs, "suggest_for_turn", suggest)
    monkeypatch.setattr(store_mod, "question_for_turn", lambda platform, **kw: dict(QUESTION))
    _scripted(scene.platform, monkeypatch)
    done = _turn(scene.client, lane, _body("hello", scene.hartleys))
    assert done["suggestion"]["id"] == "lesson_1"
    assert done["client_question"] is None


@pytest.mark.parametrize("lane", LANES)
def test_a_question_that_is_not_a_client_id_is_never_sent(scene, monkeypatch, lane):
    monkeypatch.setattr(
        store_mod, "question_for_turn", lambda platform, **kw: dict(QUESTION, id="Hartley's"),
    )
    _scripted(scene.platform, monkeypatch)
    done = _turn(scene.client, lane, _body("hello", scene.hartleys))
    assert done["client_question"] is None


@pytest.mark.parametrize("lane", LANES)
def test_a_named_client_gets_its_door_on_both_lanes(scene, monkeypatch, lane):
    handed: list = []

    def with_client_door(entries, named):
        handed.append(list(named))
        return [*entries, {"href": f"/clients/{named[0]['id']}", "label": f"Open {named[0]['name']}"}]

    monkeypatch.setattr(doors_mod, "with_client_door", with_client_door, raising=False)
    _scripted(scene.platform, monkeypatch)
    done = _turn(scene.client, lane, _body("what did we last do for Hartleys?"))
    assert handed == [[{"id": scene.hid, "name": "Hartleys"}]], handed
    assert {"href": f"/clients/{scene.hid}", "label": "Open Hartleys"} in done["doors"]
    # A message that names no client hands nothing to the door.
    _turn(scene.client, lane, _body("what is a closure?"))
    assert len(handed) == 1


@pytest.mark.parametrize("lane", LANES)
def test_the_real_client_door_opens_the_named_clients_page(scene, monkeypatch, lane):
    """Builder C's ``doors.with_client_door`` itself (not a double): the
    owner names Hartleys in a chat with no client folder and the reply's
    doors hold Hartleys' page, built from its id."""
    _scripted(scene.platform, monkeypatch)
    done = _turn(scene.client, lane, _body("what did we last do for Hartleys?"))
    assert {"href": f"/clients/{scene.hid}", "label": "Open Hartleys' page"} in done["doors"], done["doors"]
    done = _turn(scene.client, lane, _body("what is a closure?"))
    assert not any(str(d.get("href", "")).startswith("/clients") for d in done["doors"]), done["doors"]


def test_the_arming_pass_is_handed_the_client_names_only_when_there_are_any(scene, monkeypatch):
    """The stream lane's arming (``chat_turn.settings_ask_tools``) passes
    ``client_names`` and ``in_client`` to ``record_tools_for`` (builder C's
    keywords) — read in the arming thread, which carries the turn's wall."""
    from iron_jarvis.settings import records

    got: list[dict] = []
    real = records.record_tools_for

    def spy(text, *, workflow_names=(), **kw):
        got.append(dict(kw))
        return real(text, workflow_names=workflow_names)

    monkeypatch.setattr(records, "record_tools_for", spy)
    _scripted(scene.platform, monkeypatch)
    _turn(scene.client, "stream", {
        "messages": [{"role": "user", "content": "Hartleys wants cash basis"}],
        "workspace_dir": str(scene.hartleys), "auto_tools": True,
    })
    assert got, "the arming pass never ran"
    assert got[-1]["in_client"] == "Hartleys"
    # The live table ``{id: (name, signature, aliases)}`` (records_clients.
    # live_names), so another spelling on record arms too.
    table = got[-1]["client_names"]
    assert set(table) == {scene.hid, scene.sid}
    assert {row[0] for row in table.values()} == {"Hartleys", "Smith"}
    assert all(len(row) == 3 for row in table.values())
    # No clients on record: the call is exactly what it was.
    monkeypatch.setattr(store_mod, "load_map", lambda engine: store_mod.ClientMap())
    _turn(scene.client, "stream", {
        "messages": [{"role": "user", "content": "hello there"}], "auto_tools": True,
    })
    assert got[-1] == {}


def test_in_a_clients_folder_a_preference_turn_may_keep_it_for_the_client_only(scene, monkeypatch):
    """The design's guard 5: in a CONFIRMED client's folder,
    ``client_preference_add`` is armed (ask: visible, never granted) whenever
    ``remember_preference`` is; in a folder no client claims it is not."""
    offered: list[set[str]] = []

    async def fake_stream(*, system, messages, tools=None, **kw):
        offered.append({str(t.get("name") or "") for t in (tools or []) if isinstance(t, dict)})
        resp = LLMResponse(text="Noted.", usage={"input_tokens": 3, "output_tokens": 1})
        yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    monkeypatch.setattr(scene.platform.router, "stream", fake_stream)
    words = "from now on, always use numbered steps"
    misc = scene.tmp / "Misc"
    misc.mkdir()
    for folder, client_only in ((scene.hartleys / "2026", True), (misc, False)):
        _turn(scene.client, "stream", {"messages": [{"role": "user", "content": words}],
                                       "workspace_dir": str(folder), "auto_tools": True})
        assert "remember_preference" in offered[-1], offered[-1]
        assert ("client_preference_add" in offered[-1]) is client_only, (folder, offered[-1])
    # The rule itself, outside a lane: nothing without the client, nothing
    # twice, nothing without remember_preference.
    reg = scene.platform.registry
    assert CT.client_preference_arm(["remember_preference"], [], reg) == []  # no wall: no client
    hart = wall_mod.turn_wall(scene.platform.engine, str(scene.hartleys), "")
    with wall_mod.turn_scope(hart):
        assert CT.client_preference_arm(["remember_preference"], [], reg) == ["client_preference_add"]
        assert CT.client_preference_arm(["read_file"], [], reg) == []
        assert CT.client_preference_arm(["remember_preference"], ["client_preference_add"], reg) == []


# --------------------------------------------------------------------------- #
# 5. Chat › Code in its own copy of the folder
# --------------------------------------------------------------------------- #
async def test_a_chat_code_turn_in_its_own_copy_gets_its_client(scene, monkeypatch):
    """A coding session on its own branch works in
    ``<Hartleys>.ij-worktrees/<name>`` (``S.folder_of(row)``): the stream
    lane hands THAT folder to the client hop and the wall, and the resolver
    (``keys.home_folder``) places it in Hartleys — the block reaches the
    session's model, and the session's tools see Hartleys' wall."""
    from tests.test_build_sessions_v1367 import Script, frames_of, new_session, open_turn, wire

    copy = Path(str(scene.hartleys) + K.WORKTREES_SUFFIX) / "fix-totals"
    copy.mkdir(parents=True)
    (copy / "totals.py").write_text("X = 1\n", encoding="utf-8")
    sid = new_session(scene.app, copy)["id"]
    script = wire(scene.app, Script([]))
    status, raw = await open_turn(scene.app, sid, {"text": "fix the totals", "turn_id": "cc-1"})
    assert status == 200, raw
    assert frames_of(raw)[-1][0] == "done", frames_of(raw)[-3:]
    assert script.calls, "the session's model was never asked"
    assert BLOCK_H in script.calls[0]["system"], "a Chat › Code turn in its own copy lost its client"
    assert str(copy) in scene.clients.block_calls, scene.clients.block_calls
    assert scene.clients.wall_calls[-1][0] == str(copy)
    assert scene.clients.walls[-1].client.client_id == scene.hid


async def test_a_chat_code_reply_keeps_its_client_question_and_reads_its_answer(scene, monkeypatch):
    """A Chat › Code turn in a waiting name's folder carries the question on
    its done frame AND on the reply line the daemon writes
    (``clientQuestion``, a line key on both sides); the session's view folds
    in the CURRENT answer from the record, so a reopened session never asks
    again.

    v1.515.0: this is the one test that takes the REAL question through a
    lane, so it raced the lane's real one-second wait: v1.514.0's release run
    failed it twice on a busy runner (the first run where the OCR tests shared
    its shard) and no installer was published. What it pins is what rides the
    reply, not the wait, so it gets a wait a busy runner cannot beat. The wait
    itself is pinned with explicit deadlines in test_clients_notice_v1499."""
    monkeypatch.setattr(CT, "CLIENT_QUESTION_WAIT_S", 60.0)
    monkeypatch.setattr(store_mod, "QUESTION_LOCK_S", 60.0)
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.ids import new_id
    from iron_jarvis.harness import sessions as S
    from iron_jarvis.workmap.clients import names as N
    from iron_jarvis.workmap.models import WorkClient
    from tests.test_build_sessions_v1367 import Script, frames_of, messages_of, new_session, open_turn, wire

    jones = scene.root / "Jones"
    jones.mkdir()
    (jones / "ledger.py").write_text("X = 1", encoding="utf-8")
    jid = new_id("client")
    with session_scope(scene.platform.engine) as db:
        db.add(WorkClient(
            id=jid, name="Jones", name_key=N.name_key("Jones"), signature=N.signature("Jones"),
            status="proposed", how="noticed", strength=9, evidence_json=json.dumps({
                "rule": "under_clients", "work": 4, "folders": 1,
                "homes": [{"folder": str(jones), "key": K.folder_key(str(jones)), "work": 4,
                           "last_at": None, "local": False}],
            }),
        ))
        db.commit()
    store_mod.bump_version()
    sid = new_session(scene.app, jones)["id"]
    wire(scene.app, Script([]))
    status, raw = await open_turn(scene.app, sid, {"text": "tidy the ledger", "turn_id": "cq-1"})
    assert status == 200, raw
    done = [d for e, d in frames_of(raw) if e == "done"][-1]
    assert done["client_question"] == {"id": jid, "name": "Jones", "folders": 1, "work": 4}, done
    reply = [m for m in messages_of(scene.app, sid) if m["role"] == "assistant"][-1]
    assert reply["clientQuestion"] == {"id": jid, "name": "Jones", "folders": 1, "work": 4}
    assert "clientQuestion" in S.LINE_KEYS

    def shown():
        msgs = scene.client.get(f"/build/sessions/{sid}").json()["messages"]
        return [m for m in msgs if m["role"] == "assistant"][-1]["clientQuestion"]

    assert "state" not in shown()  # still asking
    r = scene.client.post(f"/clients/{jid}/confirm")
    assert r.status_code == 200, r.text
    assert shown()["state"] == "yes"
    with session_scope(scene.platform.engine) as db:
        db.delete(db.get(WorkClient, jid))
        db.commit()
    store_mod.bump_version()
    assert shown()["state"] == "gone"


# --------------------------------------------------------------------------- #
# 6. agent runs and terminal help: only their own client's block
# --------------------------------------------------------------------------- #
def _spy_adapters(platform, seen: dict):
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)
        real_complete = adapter.complete

        async def spy(*, system, messages, tools, **kw):
            seen.setdefault("systems", []).append(system)
            seen.setdefault("walls", []).append(wall_mod.current())
            return await real_complete(system=system, messages=messages, tools=tools, **kw)

        adapter.complete = spy
        return adapter

    platform.providers.get = spy_get


def test_agent_run_and_terminal_assist_get_only_their_own_clients_block(scene, monkeypatch):
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", _fake_backend)
    seen: dict = {}
    _spy_adapters(scene.platform, seen)

    def run(task: str, folder: Path | None) -> tuple[str, C.TurnWall]:
        seen.clear()
        body = {"task": task, "wait": True}
        if folder is not None:
            body["workspace_root"] = str(folder)
        r = scene.client.post("/sessions", json=body)
        assert r.status_code == 200, r.text
        assert seen.get("systems"), "the run never asked its model"
        return seen["systems"][0], seen["walls"][0]

    # A run in Hartleys' folder: Hartleys' block, after the owner's lessons;
    # its wall is Hartleys', built from the TASK.
    system, wall = run("prepare the Hartleys payroll summary", scene.hartleys / "2026")
    assert BLOCK_H in system and BLOCK_S not in system
    assert system.index(LESSON) < system.index(BLOCK_H), "the run's block comes after the lessons"
    assert wall.client is not None and wall.client.client_id == scene.hid
    assert scene.clients.wall_calls[-1] == (str(scene.hartleys / "2026"), "prepare the Hartleys payroll summary")
    assert wall_mod.current() is C.NO_CLIENT

    # A run in Smith's folder: Smith's block, never Hartleys'.
    system, wall = run("prepare the payroll summary", scene.smith / "2025")
    assert BLOCK_S in system and "# Client: Hartleys" not in system and "cash basis" not in system
    assert wall.client is not None and wall.client.client_id == scene.sid

    # A run in its own scratch folder belongs to no client: no block, and the
    # scratch folder is never handed to the resolver.
    calls_before = list(scene.clients.block_calls)
    system, wall = run("summarise Hartleys and Smith", None)
    assert "# Client:" not in system
    assert scene.clients.block_calls == calls_before, scene.clients.block_calls
    assert wall.client is None and wall.named == frozenset({scene.hid, scene.sid})

    # A run told to skip memory (a schedule's knob) skips the client block
    # with the lessons — it is a memory injection.
    import iron_jarvis.scheduling.knobs as knobs

    real_options = knobs.session_options
    monkeypatch.setattr(knobs, "session_options", lambda session: {"skip_memory": True})
    system, _wall = run("prepare the Hartleys payroll summary", scene.hartleys)
    assert "# Client:" not in system and LESSON not in system
    monkeypatch.setattr(knobs, "session_options", real_options)

    # Terminal help: a pane in Hartleys' folder hears Hartleys' block after
    # the lessons; a pane in Smith's folder never does.
    def assist(cwd: Path) -> str:
        seen.clear()
        term = scene.client.post("/terminals", json={"cwd": str(cwd)}).json()
        r = scene.client.post(f"/terminals/{term['id']}/ai", json={"prompt": "what happened?"})
        assert r.status_code == 200, r.text
        return seen["systems"][-1]

    system = assist(scene.hartleys / "2026" / "payroll")
    assert BLOCK_H in system and BLOCK_S not in system
    assert system.index(LESSON) < system.index(BLOCK_H)
    system = assist(scene.smith / "2025")
    assert "# Client: Hartleys" not in system and BLOCK_S in system
    system = assist(scene.tmp)
    assert "# Client:" not in system


def _fake_backend():
    from iron_jarvis.terminals.backend import FakeBackend

    return FakeBackend()


def test_terminal_help_follows_the_folder_the_pane_is_in_now(scene, monkeypatch):
    """The independent review: ``session.cwd`` is only the folder the pane
    STARTED in. A pane that moved into Smith's folder (its prompt says so),
    or that ran a ``cd`` nobody can follow, is told no client; a pane whose
    prompt is still in Hartleys' folders is told Hartleys'."""
    from iron_jarvis.daemon.routes import terminals as T

    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", _fake_backend)
    seen: dict = {}
    _spy_adapters(scene.platform, seen)
    term = scene.client.post("/terminals", json={"cwd": str(scene.hartleys)}).json()
    session = scene.platform.terminals.get(term["id"])

    def assist(output: str) -> str:
        session._tail += output.encode("utf-8")
        seen.clear()
        r = scene.client.post(f"/terminals/{term['id']}/ai", json={"prompt": "how do I total this CSV?"})
        assert r.status_code == 200, r.text
        return seen["systems"][-1]

    assert BLOCK_H in assist("")  # just opened: where it started
    if os.name == "nt":
        sep = "\\"

        def at(folder: object, typed: str = "") -> str:  # PowerShell's prompt
            return f"PS {folder}> {typed}"
    else:
        sep = "/"

        def at(folder: object, typed: str = "") -> str:  # bash's prompt
            return f"pat@box:{folder}$ {typed}"

    assert BLOCK_H in assist(at(f"{scene.hartleys}{sep}2026", "dir") + "\r\n")
    assert "# Client:" not in assist(
        at(scene.hartleys, f"cd ..{sep}Smith{sep}2025") + "\r\n" + at(f"{scene.smith}{sep}2025"))
    assert "cash basis" not in assist("")
    assert BLOCK_H in assist("\r\n" + at(f"{scene.hartleys}{sep}2026{sep}payroll"))
    # A cd with no prompt to follow it: not known, so no client.
    session._tail.clear()
    assert "# Client:" not in assist(f"cd ..{sep}Smith\r\n")
    # The prompt shapes read.
    assert T._live_folder(r"C:\a", "") == r"C:\a"
    assert T._live_folder(r"C:\a", r"C:\b\c>dir" + "\n") == r"C:\b\c"
    assert T._live_folder("/a", "me@box:/srv/x$ ls\n") == "/srv/x"
    assert T._live_folder(r"C:\a", "some output\nmore\n") == r"C:\a"
    assert T._live_folder(r"C:\a", r"Set-Location D:\x" + "\n") is None
    # A cd AFTER the last prompt wins over that prompt (v1.499.1 review): on
    # the prompt line itself (a server still running, no new prompt yet)...
    assert T._live_folder(r"C:\a", "PS C:\\a> cd D:\\Smith; npm run dev\r\nlistening on 3000\r\n") is None
    # ...or on a later line; a prompt printed after the cd is the answer.
    assert T._live_folder(r"C:\a", "PS C:\\a> dir\r\nx.csv\r\ncd ..\\Smith\r\n") is None
    assert T._live_folder(r"C:\a", "PS C:\\a> cd D:\\x\r\nPS D:\\x> ") == r"D:\x"
    # No prompt in output that was CUT (the cd may have scrolled out): not known.
    assert T._live_folder(r"C:\a", "some output\n", whole=False) is None
    # The pane end to end: more output than the window keeps, no prompt in it.
    session._tail.clear()
    assert BLOCK_H in assist("x\r\n")
    assert session.output_tail_is_whole()
    assert "# Client:" not in assist("y" * 100 + "\r\n" * 400 + "z" * (40 * 1024) + "\r\n")
    assert not session.output_tail_is_whole()
