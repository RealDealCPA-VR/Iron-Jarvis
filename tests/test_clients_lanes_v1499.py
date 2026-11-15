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

``client_block`` and ``turn_wall`` belong to other builders; here they are
FAKES with the contract's shapes (the fake block resolves a folder the way
the contract says the resolver does: ``keys.home_folder`` + ``keys.folder_key``
+ ``keys.inside``). Made-up names (Hartleys, Smith) and made-up folders under
the test's own tmp_path only. No wall-clock bars: a stalled hop waits on an
Event, and the bound is crossed by the injected clock.
"""

from __future__ import annotations

import json
import threading
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
HARTLEYS_ID = "client_0a1b2c3d4e5f"
SMITH_ID = "client_5f4e3d2c1b0a"

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
# fakes with the contract's shapes
# --------------------------------------------------------------------------- #
class _Clients:
    """``client_block`` and ``turn_wall`` for a set of CONFIRMED clients'
    folders. Text only, like the contract's resolver."""

    def __init__(self, roots: dict[str, tuple[str, str, str]]):
        #: folder -> (client id, name, block)
        self.roots = {K.folder_key(f): (f, *v) for f, v in roots.items()}
        self.block_calls: list[str] = []
        self.wall_calls: list[tuple[str, str]] = []
        self.walls: list[C.TurnWall] = []

    def _root_of(self, folder):
        key = K.folder_key(K.home_folder(str(folder or "")))
        for root, row in self.roots.items():
            if K.inside(key, root):
                return root, row
        return None, None

    def block(self, platform, *, folder, local=None):
        self.block_calls.append(str(folder))
        _root, row = self._root_of(folder)
        return row[3] if row else ""

    def turn_wall(self, engine, folder, user_text):
        self.wall_calls.append((str(folder), str(user_text)))
        root, row = self._root_of(folder)
        named = frozenset(
            cid for _f, cid, name, _b in self.roots.values() if name in str(user_text or "")
        )
        if row is None:
            wall = C.TurnWall(named=named, folder_key=K.folder_key(folder))
        else:
            _f, cid, name, _b = row
            wall = C.TurnWall(
                owner=f"client:{cid}", client=C.ClientRoot(cid, name, root, _f),
                named=named, folder_key=K.folder_key(folder),
            )
        self.walls.append(wall)
        return wall

    def cmap(self):
        return store_mod.ClientMap(
            names={cid: (name, name.lower().rstrip("s"), ()) for _f, cid, name, _b in self.roots.values()},
        )

    def install(self, monkeypatch):
        monkeypatch.setattr(client_block_mod, "client_block", self.block)
        monkeypatch.setattr(wall_mod, "turn_wall", self.turn_wall)
        monkeypatch.setattr(store_mod, "load_map", lambda engine: self.cmap())
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
    clients = _Clients({
        str(folders["hartleys"]): (HARTLEYS_ID, "Hartleys", BLOCK_H),
        str(folders["smith"]): (SMITH_ID, "Smith", BLOCK_S),
    }).install(monkeypatch)

    class _Fabric:
        """Query-dependent snippets: everything before them is the stable head."""

        def ground(self, query, **kw):
            return f"{FAB}{query}"

        def __getattr__(self, item):
            raise AttributeError(item)

    monkeypatch.setattr(platform, "fabric", _Fabric())
    return type("Scene", (), {"app": app, "client": client, "platform": platform,
                              "clients": clients, "tmp": tmp_path, **folders})


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

    def boom(platform, *, folder, local=None):
        raise RuntimeError("the client tables are locked")

    monkeypatch.setattr(client_block_mod, "client_block", boom)
    for lane in LANES:
        before = len(seen["systems"])
        done = _turn(scene.client, lane, _body("tidy these notes", folder))
        assert seen["systems"][before] == with_hop[lane], f"{lane}: an empty client hop added bytes"
        assert "client" in (done["context"].get("late") or []), (
            f"{lane}: a client block that could not be read must be named: {done['context']}"
        )
    assert with_hop["post"] == with_hop["stream"]


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

    monkeypatch.setattr(client_block_mod, "client_block", slow)
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

    monkeypatch.setattr(client_block_mod, "client_block", stalled)
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
    seen_by_tool: list[C.TurnWall] = []

    async def invoke(name, args, ctx, permissions, overrides=None, **kw):
        seen_by_tool.append(wall_mod.current())
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
    assert wall.client is not None and wall.client.client_id == HARTLEYS_ID
    assert wall.named == frozenset({HARTLEYS_ID})
    # Built from the OWNER's words and the turn's folder — never the model's.
    assert scene.clients.wall_calls[-1] == (str(scene.hartleys / "2026"), owner_words)
    assert "Smith" not in scene.clients.wall_calls[-1][1]
    # Reset when the turn ends: nothing outside a turn reads a client's wall.
    assert wall_mod.current() is C.NO_CLIENT


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

    def question_for_turn(platform, *, folder, thread_id):
        asked.append({"folder": folder, "thread_id": thread_id})
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
    assert handed == [[{"id": HARTLEYS_ID, "name": "Hartleys"}]], handed
    assert {"href": f"/clients/{HARTLEYS_ID}", "label": "Open Hartleys"} in done["doors"]
    # A message that names no client hands nothing to the door.
    _turn(scene.client, lane, _body("what is a closure?"))
    assert len(handed) == 1


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
    assert set(got[-1]["client_names"]) == {"Hartleys", "Smith"}
    # No clients on record: the call is exactly what it was.
    monkeypatch.setattr(store_mod, "load_map", lambda engine: store_mod.ClientMap())
    _turn(scene.client, "stream", {
        "messages": [{"role": "user", "content": "hello there"}], "auto_tools": True,
    })
    assert got[-1] == {}


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
    assert scene.clients.walls[-1].client.client_id == HARTLEYS_ID


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
    assert wall.client is not None and wall.client.client_id == HARTLEYS_ID
    assert scene.clients.wall_calls[-1] == (str(scene.hartleys / "2026"), "prepare the Hartleys payroll summary")
    assert wall_mod.current() is C.NO_CLIENT

    # A run in Smith's folder: Smith's block, never Hartleys'.
    system, wall = run("prepare the payroll summary", scene.smith / "2025")
    assert BLOCK_S in system and "# Client: Hartleys" not in system and "cash basis" not in system
    assert wall.client is not None and wall.client.client_id == SMITH_ID

    # A run in its own scratch folder belongs to no client: no block, and the
    # scratch folder is never handed to the resolver.
    calls_before = list(scene.clients.block_calls)
    system, wall = run("summarise Hartleys and Smith", None)
    assert "# Client:" not in system
    assert scene.clients.block_calls == calls_before, scene.clients.block_calls
    assert wall.client is None and wall.named == frozenset({HARTLEYS_ID, SMITH_ID})

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
