"""v1.473.0: the new script features THROUGH the real chat lanes and a real
agent run. Only the model is scripted; the app, the registry, the cards and
the isolated worker are the real ones.

* a value a script kept reaches a workbook through `_from_script`, and the
  card shows a short summary of it (never the whole value, never "Always");
* a planned batch shows ONE card that lists every change, and a declined
  card changes nothing;
* a saved skill that fits the message is named to the model beside
  `skill_load` in BOTH lanes; a message that fits none adds nothing;
* "change my … skill" offers `skill_update` on a card (stream lane only);
* after four reads of one tool the script tool joins the turn, in both lanes
  and in an agent run; never with Auto tools off or over the user's deny.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlmodel import select

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation, UndoJournal
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.skills.offer import OFFER_HEAD
from iron_jarvis.tools import script_values as SV
from iron_jarvis.tools.base import ToolContext
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream

pytestmark = pytest.mark.timeout(300)

NOTE_MARK = "`tool_script` is now one of your tools"


def _names(tools) -> list[str]:
    return [
        str(getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else "") or "")
        for t in tools or []
    ]


def _text_of(message) -> tuple[str, str]:
    role = getattr(message, "role", None) or (message.get("role") if isinstance(message, dict) else "")
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    return str(role or ""), str(content or "")


class Model:
    """A scripted model for either lane. Each step is a list of ToolCalls, a
    reply, or a function of the model (so a step can look at what it was
    offered this round)."""

    def __init__(self, steps) -> None:
        self.steps = list(steps)
        self.offered: list[list[str]] = []
        self.systems: list[str] = []
        self.tool_texts: list[list[str]] = []
        self.specs: list[dict] = []

    def _see(self, system, messages, tools) -> None:
        self.offered.append(_names(tools))
        self.systems.append(str(system or ""))
        self.tool_texts.append([c for r, c in map(_text_of, messages or []) if r == "tool"])
        self.specs.append({str(t.get("name")): t for t in (tools or []) if isinstance(t, dict)})

    def _next(self):
        move = self.steps.pop(0) if self.steps else "Done."
        return move(self) if callable(move) else move

    async def stream(self, *, provider=None, model=None, system="", messages=None, tools=None, **kw):
        self._see(system, messages, tools)
        move = self._next()
        if isinstance(move, list):
            yield {"type": "final", "response": LLMResponse(text="", tool_calls=move),
                   "provider": "mock", "model": "mock"}
            return
        yield {"type": "text", "text": move}
        yield {"type": "final", "response": LLMResponse(text=move), "provider": "mock", "model": "mock"}

    async def complete(self, *, provider=None, model=None, system="", messages=None, tools=None, **kw):
        self._see(system, messages, tools)
        move = self._next()
        if isinstance(move, list):
            return RouteResult(LLMResponse(text="", tool_calls=move), "mock", "mock")
        return RouteResult(LLMResponse(text=move), "mock", "mock")


def _call(i, tool, /, **args) -> list[ToolCall]:
    return [ToolCall(id=f"c{i}", name=tool, arguments=args)]


@pytest.fixture()
def desk(tmp_path):
    app = create_app(str(tmp_path / "home"))
    ws = tmp_path / "work"
    ws.mkdir()
    for i in range(6):
        (ws / f"f{i}.txt").write_text(f"file {i} total {i * 10}\n", encoding="utf-8")
    SV.SHELF.clear()
    yield app, ws
    p = app.state.platform
    if p.repl is not None:
        asyncio.run(p.repl.dispose_all())
    SV.SHELF.clear()


_TURN = iter(range(1, 10_000))


def _body(ws: Path, text: str, **extra) -> dict:
    """A body as the chat page sends it: every turn is named."""
    return {"messages": [{"role": "user", "content": text}], "tools": [], "auto_tools": True,
            "approval_mode": "approve_for_me", "workspace_dir": str(ws),
            "turn_id": f"turn-lanes-{next(_TURN)}", **extra}


def _answer(app, seen: list, decision: str = "once"):
    async def answer(data):
        seen.append(data)
        status, raw = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": decision})
        assert status == 200, raw

    return answer


async def _stream(app, body, model: Model, decision: str = "once"):
    app.state.platform.router.stream = model.stream
    cards: list = []
    frames = await _drive_stream(app, body, _answer(app, cards, decision))
    done = [d for ev, d in frames if ev == "done"]
    assert done, [ev for ev, _ in frames]
    return frames, cards, done[-1]


async def _post(app, body, model: Model) -> dict:
    app.state.platform.router.complete = model.complete
    status, raw = await _asgi_post(app, "/chat", body)
    assert status == 200, raw
    return json.loads(raw)


def _rows(app, tool: str) -> list[ToolInvocation]:
    with session_scope(app.state.platform.engine) as db:
        return [r for r in db.exec(select(ToolInvocation)).all() if r.tool == tool]


async def _make_skill(app, ws: Path, **args):
    p = app.state.platform
    ctx = ToolContext(workspace=ws, session_id="chat", agent_run_id="chat",
                      config=p.config, event_bus=p.event_bus, engine=p.engine)
    r = await p.registry.invoke("skill_create", args, ctx, p.permissions,
                                allowed_names={"skill_create"}, session_allow={"skill_create"})
    assert r.ok, r.error
    return r


# --------------------------------------------------------------------------- #
# A kept value reaches a file through the model's own call
# --------------------------------------------------------------------------- #

KEEP = (
    'rows = [["Name", "Amount"], ["alpha", 1234.56], ["beta", 98765.4321], ["LASTROW", 7]]\n'
    'tools.keep("totals", rows)\n'
    'print("kept", len(rows))\n'
)


@pytest.mark.asyncio
async def test_a_kept_value_reaches_the_workbook_and_the_card_shows_its_summary(desk):
    app, ws = desk
    model = Model([
        _call(1, "tool_script", code=KEEP),
        _call(2, "write_document", path="totals.xlsx", _from_script={"content": "totals"}),
        "Saved.",
    ])
    body = _body(ws, "put the totals in a workbook", tools=["tool_script", "write_document"],
                 auto_tools=False, approval_mode="always_ask")
    frames, cards, done = await _stream(app, body, model)

    # The file tool says it takes a kept value only beside the script tool.
    spec = model.specs[0]["write_document"]
    assert "_from_script" in spec["input_schema"]["properties"]
    assert "content" not in spec["input_schema"].get("required", [])

    card = [c for c in cards if c.get("tool") == "write_document"]
    assert len(card) == 1, cards
    shown = card[0]["args"]
    assert "from the script's `totals`" in str(shown.get("content")), shown
    assert "LASTROW" not in json.dumps(shown), "the card carried the whole value"
    assert "_from_script" not in shown
    assert card[0].get("can_always") is False, "a kept value must never be always-allowed by name"

    import openpyxl

    wb = openpyxl.load_workbook(ws / "totals.xlsx")
    got = [[c.value for c in row] for row in wb.active.iter_rows()]
    assert got[0] == ["Name", "Amount"]
    assert got[1] == ["alpha", 1234.56]
    assert got[2] == ["beta", 98765.4321], "the number is the script's own, not a retyped one"
    assert got[3] == ["LASTROW", 7]

    row = _rows(app, "write_document")[-1]
    noted = json.loads(row.args_json)["_from_script"]["content"]
    assert noted["name"] == "totals" and len(noted["sha"]) >= 16
    assert "LASTROW" not in row.args_json
    assert "write_document" in done.get("tools_used", [])


@pytest.mark.asyncio
async def test_a_caller_with_no_chat_and_no_turn_is_told_why_it_cannot_keep(desk):
    """A headless caller's script runs in a worker used once: there is no
    later call to hand a kept value to, so `tools.keep` says so."""
    app, ws = desk
    model = Model([_call(1, "tool_script", code=KEEP), "ok"])
    body = _body(ws, "go", tools=["tool_script", "write_document"], auto_tools=False)
    body.pop("turn_id")
    await _stream(app, body, model)
    said = " ".join(model.tool_texts[-1])
    assert "inside a chat or a run" in said, said


@pytest.mark.asyncio
async def test_a_value_nobody_kept_is_a_refusal_in_words_and_no_file(desk):
    app, ws = desk
    model = Model([
        _call(1, "write_document", path="nope.xlsx", _from_script={"content": "ghost"}),
        "I could not.",
    ])
    body = _body(ws, "go", tools=["tool_script", "write_document"], auto_tools=False)
    await _stream(app, body, model)
    assert not (ws / "nope.xlsx").exists()
    assert any("no script here kept a value named `ghost`" in t for t in model.tool_texts[-1])


# --------------------------------------------------------------------------- #
# A batch: one card, every change listed
# --------------------------------------------------------------------------- #

PLAN = (
    'for i in range(4):\n'
    '    tools.plan("rename_file", path=f"f{i}.txt", new_path=f"statement-{i}.txt")\n'
    'print("planned")\n'
)


def _batch_model() -> Model:
    return Model([
        _call(1, "tool_script", code=PLAN),
        _call(2, "apply_changes", _from_script={"changes": "changes"}),
        "Renamed.",
    ])


@pytest.mark.asyncio
async def test_a_planned_batch_is_one_card_that_lists_every_change(desk):
    app, ws = desk
    model = _batch_model()
    body = _body(ws, "go", tools=["tool_script", "rename_file"])
    frames, cards, done = await _stream(app, body, model)

    assert "apply_changes" in model.offered[0], model.offered[0]
    # The script tool's own words about planning ride only beside the batch tool.
    assert "tools.plan" in model.specs[0]["tool_script"]["description"]
    assert [c.get("tool") for c in cards] == ["apply_changes"], "one card, for the batch"
    shown = cards[0]["args"]
    assert shown.get("total") == 4 and len(shown.get("changes") or []) == 4, shown
    assert all(isinstance(line, str) for line in shown["changes"])
    assert "f0.txt" in shown["changes"][0] and "statement-0.txt" in shown["changes"][0]
    assert cards[0].get("can_always") is False

    for i in range(4):
        assert (ws / f"statement-{i}.txt").is_file() and not (ws / f"f{i}.txt").exists()
    assert (ws / "f4.txt").is_file()
    # Each change is its own ledger row with its own Undo.
    renames = [r for r in _rows(app, "rename_file") if r.ok]
    assert len(renames) == 4
    with session_scope(app.state.platform.engine) as db:
        undo = [u for u in db.exec(select(UndoJournal)).all()]
    assert len(undo) >= 4


@pytest.mark.asyncio
async def test_a_declined_batch_changes_nothing(desk):
    app, ws = desk
    model = _batch_model()
    body = _body(ws, "go", tools=["tool_script", "rename_file"])
    frames, cards, done = await _stream(app, body, model, decision="deny")
    assert [c.get("tool") for c in cards] == ["apply_changes"]
    assert all((ws / f"f{i}.txt").is_file() for i in range(6))
    assert not list(ws.glob("statement-*"))
    assert not [r for r in _rows(app, "rename_file") if r.ok]


RENAME_ALL = "Rename the statement files here so each name starts with its month, like 2026-01-statement.csv."


@pytest.mark.asyncio
async def test_a_rename_request_is_offered_the_rename_tool_and_the_batch(desk):
    """Chat never offered `rename_file`, so a request like this one left the
    model the shell (no Undo). Now the words alone bring the rename tool, the
    script tool (many files) and, in the lane that can show a card, the
    batch."""
    app, ws = desk
    model = Model([
        _call(1, "tool_script", code=PLAN),
        _call(2, "apply_changes", _from_script={"changes": "changes"}),
        "Renamed.",
    ])
    frames, cards, done = await _stream(app, _body(ws, RENAME_ALL), model)
    for tool in ("rename_file", "list_files", "tool_script", "apply_changes"):
        assert tool in model.offered[0], (tool, model.offered[0])
    assert [c.get("tool") for c in cards] == ["apply_changes"]
    assert all((ws / f"statement-{i}.txt").is_file() for i in range(4))

    post = Model(["ok"])
    await _post(app, _body(ws, RENAME_ALL), post)
    assert "rename_file" in post.offered[0]
    assert "apply_changes" not in post.offered[0]


@pytest.mark.asyncio
async def test_one_rename_runs_with_its_undo_and_no_card(desk):
    app, ws = desk
    model = Model([_call(1, "rename_file", path="f0.txt", new_path="first.txt"), "Done."])
    frames, cards, done = await _stream(app, _body(ws, "rename these files: f0.txt becomes first.txt"), model)
    assert not cards
    assert (ws / "first.txt").is_file() and not (ws / "f0.txt").exists()
    with session_scope(app.state.platform.engine) as db:
        assert db.exec(select(UndoJournal)).all(), "a rename can be taken back"


@pytest.mark.asyncio
async def test_no_rename_tool_for_a_question_or_with_auto_tools_off(desk):
    app, ws = desk
    q = Model(["ok"])
    await _stream(app, _body(ws, "how do I rename a file?"), q)
    assert "rename_file" not in q.offered[0]
    off = Model(["ok"])
    await _stream(app, _body(ws, RENAME_ALL, auto_tools=False), off)
    assert "rename_file" not in off.offered[0]
    app.state.platform.permissions._base["rename_file"] = "deny"
    denied = Model(["ok"])
    await _stream(app, _body(ws, RENAME_ALL), denied)
    assert "rename_file" not in denied.offered[0]


@pytest.mark.asyncio
async def test_the_batch_tool_is_not_offered_without_the_script_tool_or_in_the_post_lane(desk):
    app, ws = desk
    model = Model(["ok"])
    await _stream(app, _body(ws, "go", tools=["rename_file"]), model)
    assert "apply_changes" not in model.offered[0]

    model = Model(["ok"])
    await _post(app, _body(ws, "go", tools=["tool_script", "rename_file"]), model)
    assert "apply_changes" not in model.offered[0], "a lane that cannot ask must not offer it"
    assert "tools.plan" not in model.specs[0]["tool_script"]["description"]


FIXTURE = Path(__file__).resolve().parents[1] / "dashboard" / "__tests__" / "fixtures" / "script-cards-v1473.json"


@pytest.mark.asyncio
async def test_the_cards_the_dashboard_draws_are_what_the_daemon_sends(desk):
    """ONE fixture both suites read: the two cards exactly as the stream lane
    sends them (regenerate with IJ_REGEN_SCRIPT_CARDS=1 after an intended
    change; `dashboard/__tests__/script-cards-v1473.test.tsx` draws them)."""
    import os

    app, ws = desk
    _, batch_cards, _ = await _stream(app, _body(ws, "go", tools=["tool_script", "rename_file"]), _batch_model())
    kept_model = Model([
        _call(1, "tool_script", code=KEEP),
        _call(2, "write_document", path="totals.xlsx", _from_script={"content": "totals"}),
        "Saved.",
    ])
    _, kept_cards, _ = await _stream(
        app,
        _body(ws, "go", tools=["tool_script", "write_document"], auto_tools=False, approval_mode="always_ask"),
        kept_model,
    )
    kept = [c for c in kept_cards if c.get("tool") == "write_document"]
    assert len(batch_cards) == 1 and len(kept) == 1

    def card(c: dict) -> dict:
        return {"tool": c["tool"], "args": c["args"], "can_always": c.get("can_always")}

    want = {"batch": card(batch_cards[0]), "kept": card(kept[0])}
    text = json.dumps(want, indent=2, sort_keys=True) + chr(10)
    if os.environ.get("IJ_REGEN_SCRIPT_CARDS") == "1":
        FIXTURE.write_bytes(text.encode("utf-8"))
    stored = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert stored == want, "regenerate with IJ_REGEN_SCRIPT_CARDS=1 after an intended change"


# --------------------------------------------------------------------------- #
# A skill that fits the message is named to the model
# --------------------------------------------------------------------------- #

FITS = "what can a freelancer deduct for a home office this year?"
FITS_NOTHING = "tell me a short joke about cats"


async def _tax_skill(app, ws):
    await _make_skill(
        app, ws, name="tax-advice",
        description="Tax advisement for freelancers: deductions, home office, estimated payments.",
        instructions="Always ask which tax year first. PINEAPPLE-RULE.",
    )


@pytest.mark.asyncio
async def test_the_stream_lane_names_a_fitting_skill_beside_skill_load(desk):
    app, ws = desk
    await _tax_skill(app, ws)
    model = Model([
        lambda m: _call(1, "skill_load", name="tax-advice"),
        "Which tax year?",
    ])
    await _stream(app, _body(ws, FITS), model)
    assert "skill_load" in model.offered[0]
    assert OFFER_HEAD in model.systems[0]
    assert "- tax-advice:" in model.systems[0]
    # The skill's own text is not injected: the model reads it by loading it.
    assert "PINEAPPLE-RULE" not in model.systems[0]
    assert any("PINEAPPLE-RULE" in t for t in model.tool_texts[-1])


@pytest.mark.asyncio
async def test_the_post_lane_names_it_too(desk):
    app, ws = desk
    await _tax_skill(app, ws)
    model = Model(["Which tax year?"])
    await _post(app, _body(ws, FITS), model)
    assert "skill_load" in model.offered[0]
    assert OFFER_HEAD in model.systems[0] and "- tax-advice:" in model.systems[0]


@pytest.mark.asyncio
async def test_a_message_that_fits_no_skill_adds_nothing(desk):
    app, ws = desk
    before = Model(["ha"])
    await _stream(app, _body(ws, FITS_NOTHING), before)
    await _tax_skill(app, ws)
    after = Model(["ha"])
    await _stream(app, _body(ws, FITS_NOTHING), after)
    assert OFFER_HEAD not in after.systems[0]
    assert "skill_load" not in after.offered[0]
    assert after.offered[0] == before.offered[0]

    post = Model(["ha"])
    await _post(app, _body(ws, FITS_NOTHING), post)
    assert OFFER_HEAD not in post.systems[0] and "skill_load" not in post.offered[0]


@pytest.mark.asyncio
async def test_no_skill_is_offered_with_auto_tools_off_or_over_a_deny(desk):
    app, ws = desk
    await _tax_skill(app, ws)
    off = Model(["ok"])
    await _stream(app, _body(ws, FITS, auto_tools=False), off)
    assert OFFER_HEAD not in off.systems[0] and "skill_load" not in off.offered[0]

    app.state.platform.permissions._base["skill_load"] = "deny"
    denied = Model(["ok"])
    await _stream(app, _body(ws, FITS), denied)
    assert OFFER_HEAD not in denied.systems[0] and "skill_load" not in denied.offered[0]


# --------------------------------------------------------------------------- #
# A skill with a saved script brings what the script needs
# --------------------------------------------------------------------------- #

SAVED = (
    'names = [f"f{i}.txt" for i in range(params.get("count", 3))]\n'
    'rs = tools.map("read_file", [{"path": n} for n in names])\n'
    'print("READ", sum(1 for r in rs if r.ok))\n'
)
USE_SAVED = "run my folder census for this folder"


async def _census_skill(app, ws):
    await _make_skill(
        app, ws, name="folder-census",
        description="Folder census: counts and reads the text files in a folder.",
        instructions="Count the files and say how many were read.",
        script=SAVED,
    )


@pytest.mark.asyncio
async def test_a_skill_with_a_saved_script_brings_the_script_tool_and_its_readers(desk):
    """The skill's note says "run it with tool_script(saved=…)". A later chat
    whose words do not ask for a script must still be able to run it."""
    app, ws = desk
    await _census_skill(app, ws)
    model = Model([
        _call(1, "skill_load", name="folder-census"),
        _call(2, "tool_script", saved="folder-census", params={"count": 4}),
        "Four files read.",
    ])
    frames, cards, done = await _stream(app, _body(ws, USE_SAVED), model)
    for tool in ("skill_load", "tool_script", "read_file"):
        assert tool in model.offered[0], (tool, model.offered[0])
    assert not cards
    assert any("READ 4" in t for t in model.tool_texts[-1]), model.tool_texts[-1]

    post = Model(["ok"])
    await _post(app, _body(ws, USE_SAVED), post)
    assert "tool_script" in post.offered[0] and "read_file" in post.offered[0]


@pytest.mark.asyncio
async def test_a_slash_pick_of_such_a_skill_brings_them_too(desk):
    app, ws = desk
    await _census_skill(app, ws)
    model = Model(["ok"])
    await _stream(app, _body(ws, "go", skill="folder-census"), model)
    assert "tool_script" in model.offered[0] and "read_file" in model.offered[0]


@pytest.mark.asyncio
async def test_a_saved_script_never_widens_what_a_turn_may_do(desk):
    """A saved script that names a tool which changes something, or one that
    asks first, brings neither: only readers the chat arms by itself."""
    app, ws = desk
    before = Model(["ok"])
    await _stream(app, _body(ws, "run my grabby census of invoices"), before)
    await _make_skill(
        app, ws, name="grabby",
        description="Grabby census of invoices in a folder.",
        instructions="x",
        script='tools.call("write_file", path="x.txt", content="a")\ntools.web_fetch(url="https://example.com")\ntools.call("read_file", path="f0.txt")\ntools.call("skill_create", name="x")\n',
    )
    model = Model(["ok"])
    await _stream(app, _body(ws, "run my grabby census of invoices"), model)
    gained = set(model.offered[0]) - set(before.offered[0])
    assert {"tool_script", "read_file", "skill_load"} <= gained | set(before.offered[0])
    # Only the script tool, the skill reader and readers the chat arms by
    # itself: never the writer or the skill maker the script names.
    assert gained <= {"tool_script", "read_file", "skill_load", "web_fetch"}, gained
    assert "write_file" not in model.offered[0] and "skill_create" not in model.offered[0]

    off = Model(["ok"])
    await _stream(app, _body(ws, "run my grabby census of invoices", auto_tools=False), off)
    assert "tool_script" not in off.offered[0]
    app.state.platform.permissions._base["tool_script"] = "deny"
    denied = Model(["ok"])
    await _stream(app, _body(ws, "run my grabby census of invoices"), denied)
    assert "tool_script" not in denied.offered[0]


# --------------------------------------------------------------------------- #
# "Change my tax skill" from chat
# --------------------------------------------------------------------------- #

EDIT = "update my tax-advice skill to also cover state returns"


@pytest.mark.asyncio
async def test_changing_a_skill_from_chat_shows_a_card_and_keeps_the_version_before(desk):
    app, ws = desk
    await _tax_skill(app, ws)
    model = Model([
        _call(1, "skill_update", name="tax-advice", append="Also cover state returns."),
        "Updated.",
    ])
    frames, cards, done = await _stream(app, _body(ws, EDIT), model)
    assert "skill_update" in model.offered[0]
    assert CT.SKILL_EDIT_BLOCK in model.systems[0]
    assert [c.get("tool") for c in cards] == ["skill_update"]

    home = Path(app.state.platform.config.home)
    skill_dir = home / "skills" / "tax-advice"
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "Also cover state returns." in text and "PINEAPPLE-RULE" in text
    kept = list((skill_dir / ".history").glob("*-SKILL.md"))
    assert len(kept) == 1 and "Also cover state returns." not in kept[0].read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_a_declined_skill_change_changes_nothing_and_the_post_lane_never_offers_it(desk):
    app, ws = desk
    await _tax_skill(app, ws)
    home = Path(app.state.platform.config.home)
    path = home / "skills" / "tax-advice" / "SKILL.md"
    before = path.read_bytes()
    model = Model([
        _call(1, "skill_update", name="tax-advice", instructions="Replaced."),
        "No.",
    ])
    await _stream(app, _body(ws, EDIT), model, decision="deny")
    assert path.read_bytes() == before

    post = Model(["ok"])
    await _post(app, _body(ws, EDIT), post)
    assert "skill_update" not in post.offered[0]
    assert CT.SKILL_EDIT_BLOCK not in post.systems[0]


# --------------------------------------------------------------------------- #
# The script tool joins a turn that keeps making the same read
# --------------------------------------------------------------------------- #

READ_ALL = (
    'rs = tools.map("read_file", [{"path": f"f{i}.txt"} for i in range(6)])\n'
    'print(sum(1 for r in rs if r.ok))\n'
)


def _reader_steps(n: int = 6):
    """Read one file a round until the script tool is offered, then use it."""

    def step(model: Model):
        i = len(model.offered)
        if "tool_script" in model.offered[-1]:
            return _call(100 + i, "tool_script", code=READ_ALL)
        return _call(i, "read_file", path=f"f{(i - 1) % 6}.txt")

    return [step] * n + ["All read."]


def _first_offer(model: Model) -> int:
    return next((i for i, names in enumerate(model.offered) if "tool_script" in names), -1)


@pytest.mark.asyncio
async def test_the_stream_lane_offers_the_script_tool_after_the_fourth_read(desk):
    app, ws = desk
    model = Model(_reader_steps())
    frames, cards, done = await _stream(app, _body(ws, "look", tools=["read_file"]), model)
    at = _first_offer(model)
    assert at == CT.SCRIPT_OFFER_AFTER, model.offered
    assert all("tool_script" not in names for names in model.offered[:at])
    # The round's last result says so, once.
    assert NOTE_MARK in model.tool_texts[at][-1]
    assert sum(t.count(NOTE_MARK) for t in model.tool_texts[-1]) == 1
    # And it runs with no card: it reaches only what the turn already holds.
    assert not cards
    script = [r for r in _rows(app, "tool_script") if r.ok]
    assert len(script) == 1
    assert "tool_script" in done.get("tools_used", [])


@pytest.mark.asyncio
async def test_the_post_lane_offers_it_the_same_way(desk):
    app, ws = desk
    model = Model(_reader_steps())
    out = await _post(app, _body(ws, "look", tools=["read_file"]), model)
    at = _first_offer(model)
    assert at == CT.SCRIPT_OFFER_AFTER, model.offered
    assert NOTE_MARK in model.tool_texts[at][-1]
    assert [r for r in _rows(app, "tool_script") if r.ok]
    assert "tool_script" in out.get("tools_used", [])


@pytest.mark.asyncio
async def test_four_reads_in_one_round_count_too(desk):
    app, ws = desk
    four = [ToolCall(id=f"r{i}", name="read_file", arguments={"path": f"f{i}.txt"}) for i in range(4)]
    model = Model([four, "ok"])
    await _stream(app, _body(ws, "look", tools=["read_file"]), model)
    assert "tool_script" not in model.offered[0] and "tool_script" in model.offered[1]


@pytest.mark.asyncio
async def test_failed_reads_and_different_tools_do_not_add_up(desk):
    app, ws = desk
    missing = [[ToolCall(id=f"m{i}", name="read_file", arguments={"path": f"nope{i}.txt"})] for i in range(5)]
    model = Model(missing + ["ok"])
    await _stream(app, _body(ws, "look", tools=["read_file"]), model)
    assert _first_offer(model) == -1, "reads that failed are not a pattern worth a script"

    mixed = [
        _call(1, "read_file", path="f0.txt"), _call(2, "list_files", path="."),
        _call(3, "read_file", path="f1.txt"), _call(4, "list_files", path="."),
        _call(5, "read_file", path="f2.txt"), "ok",
    ]
    model = Model(mixed)
    await _stream(app, _body(ws, "look", tools=["read_file", "list_files"]), model)
    assert _first_offer(model) == -1


@pytest.mark.asyncio
async def test_it_is_never_offered_with_auto_tools_off_or_over_the_users_deny(desk):
    app, ws = desk
    model = Model(_reader_steps())
    await _stream(app, _body(ws, "look", tools=["read_file"], auto_tools=False), model)
    assert _first_offer(model) == -1

    model = Model(_reader_steps())
    await _post(app, _body(ws, "look", tools=["read_file"], auto_tools=False), model)
    assert _first_offer(model) == -1

    app.state.platform.permissions._base["tool_script"] = "deny"
    model = Model(_reader_steps())
    await _stream(app, _body(ws, "look", tools=["read_file"]), model)
    assert _first_offer(model) == -1
    assert not _rows(app, "tool_script")


# --------------------------------------------------------------------------- #
# An agent run
# --------------------------------------------------------------------------- #


def _agent(tmp_path, task: str, steps, **session_kw):
    p = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "work"
    ws.mkdir(exist_ok=True)
    for i in range(6):
        (ws / f"f{i}.txt").write_text(f"file {i}\n", encoding="utf-8")
    offered: list[list[str]] = []
    tool_texts: list[list[str]] = []
    script = list(steps)

    class Scripted(MockLLMAdapter):
        async def complete(self, *, system, messages, tools, **kw):
            offered.append(_names(tools))
            tool_texts.append([c for r, c in map(_text_of, messages or []) if r == "tool"])
            move = script.pop(0) if script else "done"
            if callable(move):
                move = move(offered)
            if isinstance(move, list):
                return LLMResponse(tool_calls=move, finish_reason="tool_use")
            return LLMResponse(text=move, finish_reason="stop")

    p.providers.register("mock", lambda model=None: Scripted())
    orch = Orchestrator(p)

    async def go():
        s = await orch.create_session(task, provider="mock", model="mock-1",
                                      workspace_root=str(ws), **session_kw)
        done = await orch.run_session(s.id)
        if p.repl is not None:
            await p.repl.dispose_all()
        return done

    s = asyncio.run(go())
    with session_scope(p.engine) as db:
        rows = [(r.tool, r.ok) for r in db.exec(
            select(ToolInvocation).where(ToolInvocation.session_id == s.id)).all()]
    return s, offered, tool_texts, rows


def test_a_run_whose_task_is_the_same_read_many_times_has_the_script_tool(tmp_path):
    SV.SHELF.clear()
    s, offered, texts, rows = _agent(
        tmp_path, "read every text file in this folder and list the totals",
        [_call(1, "tool_script", code=READ_ALL), "Six files read."],
    )
    assert "tool_script" in offered[0], offered[0]
    assert ("tool_script", True) in rows, rows
    # Every read the script made is its own ledger row of THIS run.
    assert sum(1 for tool, ok in rows if tool == "read_file" and ok) == 6


def test_an_ordinary_run_gets_it_only_after_the_fourth_read(tmp_path):
    def step(offered):
        i = len(offered)
        if "tool_script" in offered[-1]:
            return _call(100 + i, "tool_script", code=READ_ALL)
        return _call(i, "read_file", path=f"f{(i - 1) % 6}.txt")

    s, offered, texts, rows = _agent(
        tmp_path, "tell me what f0.txt says", [step] * 6 + ["Read."],
    )
    at = next((i for i, names in enumerate(offered) if "tool_script" in names), -1)
    assert at == CT.SCRIPT_OFFER_AFTER, offered
    assert NOTE_MARK in texts[at][-1]
    assert ("tool_script", True) in rows


def test_a_run_that_carries_a_skill_with_a_saved_script_has_the_script_tool(tmp_path):
    """A schedule's skill (or an agent's own) with a saved script: the run
    must be able to run it, whatever the task's words are."""
    from iron_jarvis.skills import saved_scripts as SS

    p0 = build_platform(str(tmp_path / "home"))
    home = Path(p0.config.home)
    skill_dir = home / "skills" / "folder-census"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: folder-census\ndescription: census\n---\nCount the files.\n", encoding="utf-8"
    )
    SS.write_script(home / "skills", "folder-census", SAVED)
    assert SS.any_saved_script(home, ["folder-census"]) and not SS.any_saved_script(home, ["nope"])
    assert SS.script_tools(home, "folder-census") == ["read_file"]
    assert SS.script_tools(home, "nope") is None
    p0.config.default_skills = ["folder-census"]

    seen: list[list[str]] = []

    class Scripted(MockLLMAdapter):
        async def complete(self, *, system, messages, tools, **kw):
            seen.append(_names(tools))
            return LLMResponse(text="done", finish_reason="stop")

    p0.providers.register("mock", lambda model=None: Scripted())
    orch = Orchestrator(p0)

    async def go():
        s = await orch.create_session("say hello", provider="mock", model="mock-1")
        return await orch.run_session(s.id)

    asyncio.run(go())
    assert seen and "tool_script" in seen[0], seen


def test_a_run_that_reads_once_is_exactly_as_before(tmp_path):
    s, offered, texts, rows = _agent(
        tmp_path, "tell me what f0.txt says",
        [_call(1, "read_file", path="f0.txt"), "It says file 0."],
    )
    assert all("tool_script" not in names for names in offered)
    assert not any(NOTE_MARK in t for round_texts in texts for t in round_texts)
