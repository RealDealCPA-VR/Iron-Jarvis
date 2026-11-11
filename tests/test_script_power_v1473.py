"""v1.473.0: what a tool script gained, against the REAL registry and a REAL
isolated worker.

* `tools.map`: one tool many times, several at once, in order, a failed item
  never stopping the rest;
* WHAT A SCRIPT READ STAYS WHERE IT WAS READ: once a worker's scripts hold
  this PC's data (a tool read, or the script's own file read), a tool that
  reaches outside is refused, across scripts, until the worker is replaced;
* `tools.keep` + `_from_script`: a value reaches a file tool exactly as the
  script computed it, without passing through the model;
* `tools.plan` + `apply_changes`: many file changes after one approval, each
  with its own ledger row and Undo, and nothing changed when one is refused;
* a script saved with a skill runs again by name;
* `skill_update` changes the user's own skill and keeps the version before.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation, UndoJournal
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.platform import build_platform
from iron_jarvis.repl import session as S
from iron_jarvis.repl import worker as W
from iron_jarvis.skills import saved_scripts as SS
from iron_jarvis.tools import apply_changes as AC
from iron_jarvis.tools import script_values as SV
from iron_jarvis.tools import tool_script as TS
from iron_jarvis.tools.base import Reversibility, SpendCheck, Tool, ToolContext, ToolResult
from iron_jarvis.tools.tool_script import NAME

pytestmark = pytest.mark.timeout(240)


class _Echo(Tool):
    """A read-only stand-in that can be slow, and can fail for chosen inputs."""

    name = "echo_read"
    description = "read-only test tool"
    input_schema = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}
    reversibility = Reversibility.READONLY

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.delay = 0.0
        #: A delay of its own for chosen inputs (else ``delay``).
        self.delay_for: dict[str, float] = {}
        self.live = 0
        self.most_live = 0
        #: How many calls were running when each call began (itself included).
        self.live_at_start: list[int] = []
        self.fail: set[str] = set()

    async def execute(self, args, ctx):
        q = str(args.get("q"))
        self.calls.append(q)
        self.live += 1
        self.most_live = max(self.most_live, self.live)
        self.live_at_start.append(self.live)
        try:
            delay = self.delay_for.get(q, self.delay)
            if delay:
                await asyncio.sleep(delay)
            if q in self.fail:
                return ToolResult(ok=False, error=f"cannot read {q}")
            return ToolResult(ok=True, output=f"read:{q}", data={"q": q})
        finally:
            self.live -= 1


class _Slow(_Echo):
    """The same reader under a name that is NOT on the run-together list."""

    name = "slow_read"


class _Web(Tool):
    """Stands in for `web_fetch`: registered under its real name, no network."""

    name = "web_fetch"
    description = "test stand-in"
    input_schema = {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}
    reversibility = Reversibility.READONLY

    def __init__(self) -> None:
        self.urls: list[str] = []

    async def execute(self, args, ctx):
        self.urls.append(str(args.get("url")))
        return ToolResult(ok=True, output="<page>", data={})


@pytest.fixture()
def world(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "work"
    ws.mkdir()
    for i in range(6):
        (ws / f"f{i}.txt").write_text(f"file {i}\n", encoding="utf-8")
    echo, slow, web = _Echo(), _Slow(), _Web()
    p.registry.register(echo)
    p.registry.register(slow)
    p.registry._tools["web_fetch"] = web  # the stand-in takes the real name
    for name in ("echo_read", "slow_read", "web_fetch"):
        p.permissions._base[name] = "allow"
    ctx = ToolContext(
        workspace=ws, session_id="chat", agent_run_id="chat", thread_id="chat_one",
        config=p.config, event_bus=p.event_bus, engine=p.engine,
    )
    SV.SHELF.clear()
    yield SimpleNamespace(p=p, ws=ws, ctx=ctx, echo=echo, slow=slow, web=web, home=Path(p.config.home))
    asyncio.run(p.repl.dispose_all())
    SV.SHELF.clear()


READERS = ("tool_script", "echo_read", "slow_read", "web_fetch", "read_file", "list_files")


def _invoke(w, name, args, *, allowed=READERS, session_allow=None, overrides=None):
    async def go():
        return await w.p.registry.invoke(
            name, args, w.ctx, w.p.permissions,
            agent_overrides=overrides, allowed_names=set(allowed),
            session_allow=session_allow,
        )

    return asyncio.run(go())


def _run(w, code, **kw):
    extra = {k: kw.pop(k) for k in ("saved", "params") if k in kw}
    args = {"code": code, **extra} if code is not None else dict(extra)
    return _invoke(w, NAME, args, **kw)


def _rows(w, tool):
    with session_scope(w.p.engine) as db:
        return [r for r in db.query(ToolInvocation).all() if r.tool == tool]


# --------------------------------------------------------------------------- #
# tools.map
# --------------------------------------------------------------------------- #


def test_map_runs_one_tool_over_many_items_in_order(world):
    r = _run(world, (
        "rs = tools.map('echo_read', [{'q': str(i)} for i in range(10)])\n"
        "print([x.output for x in rs])\n"
        "print(all(x.ok for x in rs), rs[3].data['q'], rs[3].args)"
    ))
    assert r.ok, r.error
    assert "['read:0', 'read:1', 'read:2', 'read:3', 'read:4', 'read:5', 'read:6', 'read:7', 'read:8', 'read:9']" in r.output
    assert "True 3 {'q': '3'}" in r.output
    assert sorted(world.echo.calls) == sorted(str(i) for i in range(10))
    assert r.data["tool_calls"] == {"echo_read": 10}
    # Every item is a ledger row of its own, like a call made directly.
    assert len(_rows(world, "echo_read")) == 10


def test_map_takes_plain_values_with_arg_and_common_arguments(world):
    r = _run(world, (
        "rs = tools.map('echo_read', ['a', 'b'], arg='q')\n"
        "print([x.output for x in rs])"
    ))
    assert r.ok, r.error
    assert "['read:a', 'read:b']" in r.output


def test_map_runs_safe_readers_side_by_side_and_others_one_at_a_time(world, monkeypatch):
    # `echo_read` joins the run-together list for this test; `slow_read`
    # (the same code under another name) does not.
    monkeypatch.setattr(TS, "MAP_TOGETHER", TS.MAP_TOGETHER | {"echo_read"})
    monkeypatch.setattr(TS, "_cpu_s", lambda: 0.0)  # these calls WAIT
    world.echo.delay = world.slow.delay = 0.15
    r = _run(world, (
        "a = tools.map('echo_read', [{'q': str(i)} for i in range(8)])\n"
        "b = tools.map('slow_read', [{'q': str(i)} for i in range(3)])\n"
        "print(len(a), len(b))"
    ))
    assert r.ok, r.error
    assert world.echo.most_live == TS.MAP_PARALLEL  # never more, and it got there
    assert world.slow.most_live == 1
    # The first call runs alone: it is how the script learns the calls wait.
    assert world.echo.live_at_start[0] == 1


def test_quick_calls_run_in_order_never_side_by_side(world, monkeypatch):
    """Measured: four quick reads at once were slower than four in a row."""
    monkeypatch.setattr(TS, "MAP_TOGETHER", TS.MAP_TOGETHER | {"echo_read"})
    monkeypatch.setattr(TS, "_cpu_s", lambda: 0.0)
    r = _run(world, (
        "rs = tools.map('echo_read', [{'q': str(i)} for i in range(40)])\n"
        "print(len(rs))"
    ))
    assert r.ok, r.error
    assert world.echo.most_live == 1
    assert world.echo.calls == [str(i) for i in range(40)], "in the script's order"


def test_calls_that_compute_are_never_run_side_by_side(world, monkeypatch):
    """Measured: twelve cold workbooks were 0.9x four at a time (parsing is
    one core's work). A slow call counts as waiting only when this process
    was NOT the one working: here every second that passes is counted as
    this process's own work."""
    monkeypatch.setattr(TS, "MAP_TOGETHER", TS.MAP_TOGETHER | {"echo_read"})
    monkeypatch.setattr(TS, "_cpu_s", time.perf_counter)
    world.echo.delay = 0.08
    r = _run(world, (
        "rs = tools.map('echo_read', [{'q': str(i)} for i in range(6)])\n"
        "print(len(rs))"
    ))
    assert r.ok, r.error
    assert world.echo.most_live == 1


def test_one_waiting_call_is_enough_and_the_rest_of_the_script_keeps_it(world, monkeypatch):
    monkeypatch.setattr(TS, "MAP_TOGETHER", TS.MAP_TOGETHER | {"echo_read"})
    monkeypatch.setattr(TS, "_cpu_s", lambda: 0.0)
    world.echo.delay = 0.12
    world.echo.delay_for = {"0": 0.0, "1": 0.0}  # two quick ones first
    r = _run(world, (
        "a = tools.map('echo_read', [{'q': str(i)} for i in range(8)])\n"
        "b = tools.map('echo_read', [{'q': 'b%d' % i} for i in range(5)])\n"
        "print(len(a), len(b))"
    ))
    assert r.ok, r.error
    # 0 and 1 were quick, 2 waited (alone), 3..7 ran together.
    assert world.echo.live_at_start[:3] == [1, 1, 1]
    assert world.echo.most_live == TS.MAP_PARALLEL
    # The second map did not learn again: its first calls began together.
    assert max(world.echo.live_at_start[8:12]) >= 2


def test_a_failed_item_does_not_stop_the_rest_and_does_not_raise(world):
    world.echo.fail = {"2", "5"}
    r = _run(world, (
        "rs = tools.map('echo_read', [{'q': str(i)} for i in range(7)])\n"
        "print([x.ok for x in rs])\n"
        "print([x.error for x in rs if not x.ok])\n"
        "print([x.output for x in rs if x.ok])"
    ))
    assert r.ok, r.error
    assert "[True, True, False, True, True, False, True]" in r.output
    assert "cannot read 2" in r.output and "cannot read 5" in r.output
    assert "'read:6'" in r.output


def test_a_map_item_the_gate_refuses_is_a_failed_result_with_the_sentence(world):
    r = _run(world, (
        "rs = tools.map('write_file', [{'path': 'x.txt', 'content': 'a'}])\n"
        "print(rs[0].ok, rs[0].error)"
    ), allowed=READERS + ("write_file",))
    assert r.ok, r.error
    assert "False" in r.output and "cannot be called from a script" in r.output
    assert not (world.ws / "x.txt").exists()


def test_map_counts_toward_the_scripts_call_limit(world, monkeypatch):
    monkeypatch.setattr(S, "MAX_TOOL_CALLS", 5)
    r = _run(world, (
        "rs = tools.map('echo_read', [{'q': str(i)} for i in range(8)])\n"
        "print(sum(1 for x in rs if x.ok), [x.error for x in rs if not x.ok][:1])"
    ))
    assert r.ok, r.error
    assert r.output.startswith("5 ")
    assert len(world.echo.calls) == 5


def test_map_refuses_what_is_not_a_list_of_calls(world):
    r = _run(world, "tools.map('echo_read', 'abc')")
    assert not r.ok and "LIST" in r.error
    r = _run(world, "tools.map('echo_read', [1, 2])")
    assert not r.ok and "arg='path'" in r.error


def test_a_stop_during_a_map_cancels_every_call_of_it(world, monkeypatch):
    monkeypatch.setattr(TS, "MAP_TOGETHER", TS.MAP_TOGETHER | {"echo_read"})
    monkeypatch.setattr(TS, "_cpu_s", lambda: 0.0)
    world.echo.delay = 30.0
    world.echo.delay_for = {"0": 0.1}  # the first shows the calls wait

    async def go():
        task = asyncio.ensure_future(world.p.registry.invoke(
            NAME,
            {"code": "tools.map('echo_read', [{'q': str(i)} for i in range(5)])"},
            world.ctx, world.p.permissions, allowed_names=set(READERS),
        ))
        for _ in range(200):
            await asyncio.sleep(0.05)
            if world.echo.live >= 2:
                break
        assert world.echo.live >= 2
        started = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        for _ in range(100):
            if world.echo.live == 0:
                break
            await asyncio.sleep(0.05)
        return time.monotonic() - started

    took = asyncio.run(go())
    assert world.echo.live == 0
    assert took < 10


# --------------------------------------------------------------------------- #
# What a script read stays where it was read
# --------------------------------------------------------------------------- #


def test_the_web_can_be_reached_before_anything_local_was_read(world):
    r = _run(world, "print(tools.call('web_fetch', url='https://example.com/a').output)")
    assert r.ok, r.error
    assert world.web.urls == ["https://example.com/a"]


def test_after_a_local_tool_read_an_internet_tool_is_refused_in_the_same_script(world):
    r = _run(world, (
        "tools.call('echo_read', q='client')\n"
        "try:\n"
        "    tools.call('web_fetch', url='https://example.com/?q=client')\n"
        "except ToolError as e:\n"
        "    print('REFUSED', e)"
    ))
    assert r.ok, r.error
    assert "REFUSED" in r.output and "reaches outside this PC" in r.output
    assert "files or data on this PC" in r.output
    assert world.web.urls == []
    assert not _rows(world, "web_fetch")  # refused before the registry: no row


def test_the_rule_holds_across_scripts_of_the_same_worker(world):
    assert _run(world, "x = tools.call('echo_read', q='client').output").ok
    r = _run(world, (
        "try:\n"
        "    tools.call('web_fetch', url='https://example.com/' + x)\n"
        "except ToolError as e:\n"
        "    print('REFUSED')"
    ))
    assert r.ok, r.error
    assert "REFUSED" in r.output and world.web.urls == []


def test_the_scripts_own_file_read_counts_as_local_data(world):
    r = _run(world, (
        "text = open('f1.txt').read()\n"
        "try:\n"
        "    tools.call('web_fetch', url='https://example.com/' + text)\n"
        "except ToolError as e:\n"
        "    print('REFUSED')"
    ))
    assert r.ok, r.error
    assert "REFUSED" in r.output and world.web.urls == []


def test_listing_the_working_folder_counts_too(world):
    r = _run(world, (
        "import os\n"
        "names = os.listdir('.')\n"
        "try:\n"
        "    tools.call('web_fetch', url='https://example.com/' + names[0])\n"
        "except ToolError:\n"
        "    print('REFUSED')"
    ))
    assert r.ok, r.error
    assert "REFUSED" in r.output and world.web.urls == []


def test_web_first_then_local_is_fine_and_then_the_web_is_closed(world):
    r = _run(world, (
        "tools.call('web_fetch', url='https://example.com/rates')\n"
        "tools.call('echo_read', q='client')\n"
        "try:\n"
        "    tools.call('web_fetch', url='https://example.com/again')\n"
        "except ToolError:\n"
        "    print('REFUSED')"
    ))
    assert r.ok, r.error
    assert world.web.urls == ["https://example.com/rates"]
    assert "REFUSED" in r.output


def test_a_new_worker_starts_clean(world):
    assert _run(world, "tools.call('echo_read', q='client')").ok
    asyncio.run(world.p.repl.dispose_all())  # the variables are gone with it
    r = _run(world, "print(tools.call('web_fetch', url='https://example.com/a').output)")
    assert r.ok, r.error
    assert world.web.urls == ["https://example.com/a"]


def test_a_failed_local_read_counts_as_a_read(world):
    """Moved on purpose by the review: this pinned "a failed read holds
    nothing". A tool's failure often QUOTES what it found (a workbook's
    sheet names, "did you mean Jane Client (SSN …)"), and the script holds
    those words, so the call counts like one that worked."""
    world.echo.fail = {"missing"}
    r = _run(world, (
        "try:\n"
        "    tools.call('echo_read', q='missing')\n"
        "except ToolError:\n"
        "    pass\n"
        "try:\n"
        "    tools.call('web_fetch', url='https://example.com/a')\n"
        "    print('SENT')\n"
        "except ToolError as e:\n"
        "    print('REFUSED', e)\n"
    ))
    assert "REFUSED" in r.output and "SENT" not in r.output, r.output
    assert world.web.urls == []


def test_a_map_of_internet_calls_is_refused_item_by_item_after_a_local_read(world):
    r = _run(world, (
        "tools.call('echo_read', q='client')\n"
        "rs = tools.map('web_fetch', [{'url': 'https://example.com/%d' % i} for i in range(3)])\n"
        "print([x.ok for x in rs])"
    ))
    assert r.ok, r.error
    assert "[False, False, False]" in r.output and world.web.urls == []


def test_one_apps_data_may_go_back_to_that_app_but_not_to_another(world):
    tool = world.p.registry.get(NAME)
    # (the origin rule itself, without a pack to start)
    assert tool._carry_refusal("web_fetch", set()) == ""
    assert "the app books" in tool._carry_refusal("web_fetch", {"pack:books"})
    assert "reaches outside" in tool._carry_refusal("web_fetch", {"local"})
    # a local reader is never refused, whatever is held
    assert tool._carry_refusal("echo_read", {"local", "pack:books"}) == ""


# --------------------------------------------------------------------------- #
# tools.keep + _from_script
# --------------------------------------------------------------------------- #

WRITERS = READERS + ("write_file", "write_document", "apply_changes", "rename_file", "edit_file")


def test_a_kept_value_reaches_the_file_exactly_as_computed(world):
    r = _run(world, (
        "import decimal, datetime\n"
        "rows = [['client', 'amount', 'when']]\n"
        "for i in range(400):\n"
        "    rows.append(['c%d' % i, decimal.Decimal('1234.56') + i, datetime.date(2026, 1, 1)])\n"
        "print(tools.keep('totals', rows))"
    ), allowed=WRITERS)
    assert r.ok, r.error
    assert "kept `totals`" in r.output and "401 rows" in r.output
    assert "[kept for a file tool]" in r.output and "_from_script" in r.output
    assert r.data["kept"] == ["totals"]

    w = _invoke(world, "write_file", {"path": "rows.json", "_from_script": {"content": "totals"}},
                allowed=WRITERS)
    # write_file needs TEXT: a list is refused by its own shape check, in words
    assert not w.ok and "content" in (w.error or "")

    # A workbook takes the rows as they are: every number arrives as the
    # number the script worked out, none of them typed by a model.
    w = _invoke(world, "write_document", {"path": "rows.xlsx", "_from_script": {"content": "totals"}},
                allowed=WRITERS)
    assert w.ok, w.error
    import openpyxl

    sheet = openpyxl.load_workbook(world.ws / "rows.xlsx").active
    values = [[c.value for c in row] for row in sheet.iter_rows()]
    assert len(values) == 401
    assert values[0][:2] == ["client", "amount"]
    assert [v[1] for v in values[1:]] == [float(f"{1234.56 + i:.2f}") for i in range(400)]
    assert values[400][0] == "c399"


def test_the_ledger_row_holds_the_reference_and_a_fingerprint_never_the_value(world):
    big = "x" * 50_000
    assert _run(world, "tools.keep('blob', 'first row ' + 'x' * 50000 + ' LASTROW')",
                allowed=WRITERS).ok
    w = _invoke(world, "write_file", {"path": "big.txt", "_from_script": {"content": "blob"}},
                allowed=WRITERS)
    assert w.ok, w.error
    assert (world.ws / "big.txt").read_text(encoding="utf-8") == "first row " + big + " LASTROW"
    row = _rows(world, "write_file")[-1]
    assert "LASTROW" not in (row.args_json or "")
    noted = json.loads(row.args_json)["_from_script"]["content"]
    assert noted["name"] == "blob" and len(noted["sha"]) == 16 and "characters" in noted["what"]
    assert len(row.args_json) < 2_000


def test_a_summary_never_carries_a_key_from_the_first_row(world):
    r = _run(world, "print(tools.keep('rows', [['api_key=sk-ant-REALLYSECRETVALUE1234567890', 1]]))",
             allowed=WRITERS)
    assert r.ok, r.error
    assert "REALLYSECRETVALUE" not in r.output


def test_a_write_fed_by_a_script_can_be_undone_like_any_other(world):
    (world.ws / "keep.txt").write_text("before", encoding="utf-8")
    assert _run(world, "tools.keep('new', 'after')", allowed=WRITERS).ok
    w = _invoke(world, "write_file", {"path": "keep.txt", "_from_script": {"content": "new"}},
                allowed=WRITERS)
    assert w.ok, w.error
    assert (world.ws / "keep.txt").read_text(encoding="utf-8") == "after"
    with session_scope(world.p.engine) as db:
        assert db.query(UndoJournal).count() == 1


def test_only_content_arguments_may_be_filled_never_a_path(world):
    assert _run(world, "tools.keep('where', '../../outside.txt')", allowed=WRITERS).ok
    w = _invoke(world, "write_file", {"content": "x", "_from_script": {"path": "where"}},
                allowed=WRITERS)
    assert not w.ok and "only for: content" in w.error
    w = _invoke(world, "rename_file", {"path": "f1.txt", "_from_script": {"new_path": "where"}},
                allowed=WRITERS)
    assert not w.ok and "does not take a script's kept value" in w.error
    assert (world.ws / "f1.txt").exists()


def test_a_name_nobody_kept_is_refused_with_what_is_kept(world):
    assert _run(world, "tools.keep('totals', 'x')", allowed=WRITERS).ok
    w = _invoke(world, "write_file", {"path": "a.txt", "_from_script": {"content": "nope"}},
                allowed=WRITERS)
    assert not w.ok and "no script here kept a value named `nope`" in w.error
    assert "Kept now: totals" in w.error
    assert not (world.ws / "a.txt").exists()
    assert _rows(world, "write_file")[-1].ok is False  # on the ledger as a failure


def test_typed_twice_is_refused_and_an_empty_typed_value_is_the_reference(world):
    assert _run(world, "tools.keep('t', 'kept text')", allowed=WRITERS).ok
    w = _invoke(world, "write_file",
                {"path": "a.txt", "content": "typed", "_from_script": {"content": "t"}},
                allowed=WRITERS)
    assert not w.ok and "given twice" in w.error
    w = _invoke(world, "write_file",
                {"path": "a.txt", "content": "", "_from_script": {"content": "t"}},
                allowed=WRITERS)
    assert w.ok, w.error
    assert (world.ws / "a.txt").read_text(encoding="utf-8") == "kept text"


@pytest.mark.parametrize("typed", ["t", " t ", "`t`", "{t}", '"t"', "$t", '_from_script={"content": "t"}'])
def test_typing_only_a_pointer_at_the_kept_value_is_the_reference(world, typed):
    """Seen with a real model, twice: the kept value's NAME typed in the
    argument beside the reference, and the reference itself typed there.
    Neither is content; the kept value is what is written."""
    assert _run(world, "tools.keep('t', 'kept text')", allowed=WRITERS).ok
    w = _invoke(world, "write_file",
                {"path": "n.txt", "content": typed, "_from_script": {"content": "t"}},
                allowed=WRITERS)
    assert w.ok, w.error
    assert (world.ws / "n.txt").read_text(encoding="utf-8") == "kept text"


def test_real_content_typed_beside_a_reference_is_still_refused(world):
    assert _run(world, "tools.keep('t', 'kept text')", allowed=WRITERS).ok
    for typed in ["the totals are below", "t and more", "x" * 500 + "_from_script"]:
        w = _invoke(world, "write_file",
                    {"path": "r.txt", "content": typed, "_from_script": {"content": "t"}},
                    allowed=WRITERS)
        assert not w.ok and "given twice" in w.error, typed
    assert not (world.ws / "r.txt").exists()


def test_a_kept_value_belongs_to_its_own_chat(world):
    assert _run(world, "tools.keep('mine', 'private rows')", allowed=WRITERS).ok
    other = ToolContext(
        workspace=world.ws, session_id="chat", agent_run_id="chat", thread_id="chat_two",
        config=world.p.config, event_bus=world.p.event_bus, engine=world.p.engine,
    )

    async def go():
        return await world.p.registry.invoke(
            "write_file", {"path": "leak.txt", "_from_script": {"content": "mine"}},
            other, world.p.permissions, allowed_names=set(WRITERS),
        )

    w = asyncio.run(go())
    assert not w.ok and "no script here kept" in w.error
    assert not (world.ws / "leak.txt").exists()


def test_keep_refuses_what_a_file_cannot_hold(world):
    r = _run(world, "tools.keep('bad name!', 1)", allowed=WRITERS)
    assert not r.ok and "plain name" in r.error
    r = _run(world, "tools.keep('n', float('nan'))", allowed=WRITERS)
    assert not r.ok and "plain data" in r.error
    r = _run(world, "tools.keep('n', object())", allowed=WRITERS)
    assert not r.ok and "plain data" in r.error


def test_the_shelf_is_bounded(monkeypatch):
    shelf = SV.Shelf()
    monkeypatch.setattr(SV, "MAX_VALUES_PER_KEY", 3)
    for i in range(5):
        shelf.put("k", f"n{i}", json.dumps("v" * 10))
    assert shelf.names("k") == ["n2", "n3", "n4"]
    monkeypatch.setattr(SV, "MAX_TOTAL_CHARS", 60)
    shelf.put("other", "big", json.dumps("v" * 40))
    assert shelf.names("k") == []  # the oldest OTHER key made room
    assert shelf.names("other") == ["big"]
    assert shelf.chars == len(json.dumps("v" * 40))
    with pytest.raises(ValueError):
        shelf.put("k", "huge", "x" * (SV.MAX_VALUE_CHARS + 1))


def test_what_a_new_chats_first_message_kept_follows_the_chat(world):
    first = ToolContext(
        workspace=world.ws, session_id="chat", agent_run_id="chat", turn_id="turn_1",
        config=world.p.config, event_bus=world.p.event_bus, engine=world.p.engine,
    )

    async def go():
        r = await world.p.registry.invoke(
            NAME, {"code": "tools.keep('t', 'from the first message')"}, first,
            world.p.permissions, allowed_names=set(WRITERS),
        )
        assert r.ok, r.error
        await TS.adopt_turn_worker(world.p, "turn_1", "chat_new")
        later = ToolContext(
            workspace=world.ws, session_id="chat", agent_run_id="chat", thread_id="chat_new",
            config=world.p.config, event_bus=world.p.event_bus, engine=world.p.engine,
        )
        return await world.p.registry.invoke(
            "write_file", {"path": "later.txt", "_from_script": {"content": "t"}},
            later, world.p.permissions, allowed_names=set(WRITERS),
        )

    w = asyncio.run(go())
    assert w.ok, w.error
    assert (world.ws / "later.txt").read_text(encoding="utf-8") == "from the first message"


# --------------------------------------------------------------------------- #
# tools.plan + apply_changes
# --------------------------------------------------------------------------- #

GRANTED = {"apply_changes"}


def test_a_script_plans_and_one_call_makes_every_change(world):
    r = _run(world, (
        "import os\n"
        "for name in sorted(os.listdir('.')):\n"
        "    tools.plan('rename_file', path=name, new_path='2026-' + name)\n"
        "print('planned')"
    ), allowed=WRITERS)
    assert r.ok, r.error
    assert "planned 6 changes" in r.output and "rename_file x6" in r.output
    assert "NOTHING HAS RUN" in r.output
    assert r.data["planned"] == 6
    assert sorted(p.name for p in world.ws.iterdir()) == [f"f{i}.txt" for i in range(6)]

    a = _invoke(world, "apply_changes", {"_from_script": {"changes": "changes"}},
                allowed=WRITERS, session_allow=GRANTED)
    assert a.ok, a.error
    assert "Made all 6 changes." in a.output and "Rename f0.txt to 2026-f0.txt" in a.output
    assert sorted(p.name for p in world.ws.iterdir()) == [f"2026-f{i}.txt" for i in range(6)]
    assert a.data == {"applied": 6, "failed": 0, "not_attempted": 0, "total": 6}
    # each change is a ledger row and an Undo entry of its own
    assert len([r for r in _rows(world, "rename_file") if r.ok]) == 6
    with session_scope(world.p.engine) as db:
        assert db.query(UndoJournal).count() == 6
    assert sorted(Path(p).name for p in a.created_paths) == [f"2026-f{i}.txt" for i in range(6)]


def test_the_batchs_own_ledger_row_is_one_line_per_change(world):
    changes = [{"tool": "write_file", "args": {"path": f"n{i}.txt", "content": "PAYLOAD" * 500}}
               for i in range(3)]
    a = _invoke(world, "apply_changes", {"changes": changes}, allowed=WRITERS, session_allow=GRANTED)
    assert a.ok, a.error
    row = _rows(world, "apply_changes")[-1]
    shown = json.loads(row.args_json)
    assert shown["total"] == 3 and shown["changes"][0] == "Write n0.txt"
    assert "PAYLOAD" not in row.args_json


def test_a_batch_holds_only_file_changes_that_can_be_undone(world):
    for tool in ("shell", "web_fetch", "skill_create", "notify", "delete_everything"):
        a = _invoke(world, "apply_changes",
                    {"changes": [{"tool": "write_file", "args": {"path": "a.txt", "content": "x"}},
                                 {"tool": tool, "args": {}}]},
                    allowed=WRITERS + (tool,), session_allow=GRANTED)
        assert not a.ok and a.error.startswith("NOTHING WAS CHANGED."), a.error
        assert f"change 2 uses `{tool}`" in a.error
    assert not (world.ws / "a.txt").exists()


def test_one_change_that_is_not_allowed_refuses_the_whole_batch(world):
    changes = [
        {"tool": "write_file", "args": {"path": "ok.txt", "content": "x"}},
        {"tool": "rename_file", "args": {"path": "f1.txt", "new_path": "g1.txt"}},
    ]
    # rename_file is set to never run: nothing at all is changed
    a = _invoke(world, "apply_changes", {"changes": changes}, allowed=WRITERS,
                session_allow=GRANTED, overrides={"rename_file": "deny"})
    assert not a.ok and "NOTHING WAS CHANGED." in a.error and "never run" in a.error
    assert not (world.ws / "ok.txt").exists() and (world.ws / "f1.txt").exists()
    # a tool that is not one of the turn's tools
    a = _invoke(world, "apply_changes", {"changes": changes},
                allowed=tuple(t for t in WRITERS if t != "rename_file"), session_allow=GRANTED)
    assert not a.ok and "not one of this turn's tools" in a.error
    assert not (world.ws / "ok.txt").exists()


def test_a_change_that_needs_its_own_approval_refuses_the_batch(world, monkeypatch):
    tool = world.p.registry.get("write_file")

    async def asks(args, ctx):
        return SpendCheck(ask=True, words="guarded") if "guard" in str(args.get("path")) else None

    monkeypatch.setattr(tool, "spend_check", asks)
    a = _invoke(world, "apply_changes", {"changes": [
        {"tool": "write_file", "args": {"path": "plain.txt", "content": "x"}},
        {"tool": "write_file", "args": {"path": "guarded.txt", "content": "x"}},
    ]}, allowed=WRITERS, session_allow=GRANTED)
    assert not a.ok and "needs an approval of its own" in a.error and "change 2" in a.error
    assert not (world.ws / "plain.txt").exists()


def test_a_change_that_fails_is_reported_and_the_rest_are_made(world):
    a = _invoke(world, "apply_changes", {"changes": [
        {"tool": "rename_file", "args": {"path": "f0.txt", "new_path": "a0.txt"}},
        {"tool": "rename_file", "args": {"path": "missing.txt", "new_path": "b.txt"}},
        {"tool": "rename_file", "args": {"path": "f2.txt", "new_path": "a2.txt"}},
    ]}, allowed=WRITERS, session_allow=GRANTED)
    assert a.ok, a.error
    assert "Made 2 of 3 changes." in a.output and "NOT DONE (1)" in a.output
    assert (world.ws / "a0.txt").exists() and (world.ws / "a2.txt").exists()
    assert a.data["failed"] == 1


def test_a_run_of_failures_stops_the_batch(world):
    changes = [{"tool": "rename_file", "args": {"path": f"none{i}.txt", "new_path": f"x{i}.txt"}}
               for i in range(9)]
    a = _invoke(world, "apply_changes", {"changes": changes}, allowed=WRITERS, session_allow=GRANTED)
    assert not a.ok and a.error.startswith("NOTHING WAS CHANGED.")
    assert "4 changes not attempted" in a.error
    assert len(_rows(world, "rename_file")) == AC.STOP_AFTER_FAILURES


def test_without_its_approval_the_batch_does_not_run(world):
    a = _invoke(world, "apply_changes", {"changes": [
        {"tool": "write_file", "args": {"path": "a.txt", "content": "x"}},
    ]}, allowed=WRITERS)  # no grant: the ask tier refuses when nothing can ask
    assert not a.ok and "permission denied" in a.error
    assert not (world.ws / "a.txt").exists()


def test_a_plan_is_checked_when_the_script_hands_it_over(world):
    r = _run(world, "tools.plan('shell', command='del *')", allowed=WRITERS + ("shell",))
    assert not r.ok and "a batch cannot hold" in r.error
    # and when `apply_changes` is not one of the turn's tools, the script hears it
    r = _run(world, "tools.plan('rename_file', path='f0.txt', new_path='x.txt')",
             allowed=tuple(t for t in WRITERS if t != "apply_changes"))
    assert not r.ok and "is not one of its tools" in r.error
    assert (world.ws / "f0.txt").exists()


def test_a_script_that_fails_hands_over_no_plan(world):
    r = _run(world, (
        "tools.plan('rename_file', path='f0.txt', new_path='x.txt')\n"
        "raise ValueError('stopped half way')"
    ), allowed=WRITERS)
    assert not r.ok
    a = _invoke(world, "apply_changes", {"_from_script": {"changes": "changes"}},
                allowed=WRITERS, session_allow=GRANTED)
    assert not a.ok and "no script here kept a value named `changes`" in a.error
    assert (world.ws / "f0.txt").exists()


def test_too_many_changes_and_bad_shapes_are_refused(world):
    many = [{"tool": "write_file", "args": {"path": f"{i}.txt", "content": "x"}}
            for i in range(AC.MAX_CHANGES + 1)]
    a = _invoke(world, "apply_changes", {"changes": many}, allowed=WRITERS, session_allow=GRANTED)
    assert not a.ok and "at most" in a.error
    a = _invoke(world, "apply_changes", {"changes": [["rename_file", "a", "b"]]},
                allowed=WRITERS, session_allow=GRANTED)
    assert not a.ok and "change 1 is not" in a.error
    assert AC.plan_problem([], set(WRITERS), world.p.registry)


def test_the_tools_a_batch_may_hold_are_all_undoable_file_tools(world):
    for name in AC.BATCH_TOOLS:
        tool = world.p.registry.get(name)
        assert tool is not None, name
        assert tool.reversibility is Reversibility.REVERSIBLE, name


# --------------------------------------------------------------------------- #
# The specs a turn with the script tool is shown
# --------------------------------------------------------------------------- #


def _spec(specs, name):
    return next(s for s in specs if s["name"] == name)


def test_file_tools_say_they_take_a_kept_value_only_beside_the_script_tool(world):
    reg = world.p.registry
    plain = _spec(reg.specs(["write_file", "read_file"]), "write_file")
    assert "_from_script" not in plain["input_schema"]["properties"]
    assert plain["input_schema"]["required"] == ["path", "content"]

    shown = _spec(reg.specs(["write_file", "read_file", "tool_script"]), "write_file")
    assert "_from_script" in shown["input_schema"]["properties"]
    assert shown["input_schema"]["required"] == ["path"]  # content may come from the script
    # the tool's own declared schema is untouched
    assert reg.get("write_file").input_schema["required"] == ["path", "content"]
    assert "_from_script" not in reg.get("write_file").input_schema["properties"]
    # a tool that takes no kept value is shown exactly as before
    assert _spec(reg.specs(["read_file", "tool_script"]), "read_file") == _spec(reg.specs(["read_file"]), "read_file")


def test_the_script_tool_names_apply_changes_only_when_the_turn_has_it(world):
    reg = world.p.registry
    without = _spec(reg.specs(["tool_script", "write_file"]), "tool_script")
    assert "apply_changes" not in without["description"]
    with_it = _spec(reg.specs(["tool_script", "write_file", "apply_changes"]), "tool_script")
    assert "tools.plan(" in with_it["description"] and "apply_changes" in with_it["description"]
    assert "apply_changes" not in reg.get("tool_script").description


def test_every_tool_that_takes_a_kept_value_exists_and_names_real_arguments(world):
    for name, args in SV.FROM_SCRIPT_ARGS.items():
        tool = world.p.registry.get(name)
        assert tool is not None, name
        props = tool.input_schema.get("properties", {})
        for arg in args:
            assert arg in props, (name, arg)


def test_the_run_together_list_is_the_chat_lanes_own(world):
    assert TS.MAP_TOGETHER <= CT.CONCURRENT_READ_TOOLS | {"web_search", "web_fetch"}


# --------------------------------------------------------------------------- #
# A saved script
# --------------------------------------------------------------------------- #


def _create_skill(w, **args):
    return _invoke(w, "skill_create", args, allowed=("skill_create",),
                   session_allow={"skill_create"})


def test_a_skill_saved_with_a_script_runs_it_again_by_name(world):
    r = _create_skill(world, name="Monthly totals", description="adds up statements",
                      instructions="Add up the month.",
                      script="rs = tools.map('echo_read', [{'q': m} for m in params['months']])\n"
                             "print(len(rs), params['label'])")
    assert r.ok, r.error
    assert "script was saved too" in r.output
    path = SS.script_path(world.home / "skills", "Monthly totals")
    assert path.is_file()
    body = (path.parent.parent / "SKILL.md").read_text(encoding="utf-8")
    assert SS.NOTE_HEAD in body and 'tool_script(saved="Monthly totals"' in body

    run = _run(world, None, saved="Monthly totals", params={"months": ["jan", "feb"], "label": "Q1"})
    assert run.ok, run.error
    assert "2 Q1" in run.output
    assert world.echo.calls == ["jan", "feb"]


def test_a_saved_script_has_no_more_power_than_a_fresh_one(world):
    r = _create_skill(world, name="sneaky", description="d", instructions="i",
                      script="tools.call('write_file', path='x.txt', content='a')")
    assert r.ok, r.error
    run = _run(world, None, saved="sneaky", allowed=READERS + ("write_file",))
    assert not run.ok and "cannot be called from a script" in run.error
    assert not (world.ws / "x.txt").exists()


def test_a_script_that_is_not_python_is_not_saved_and_neither_is_the_skill(world):
    r = _create_skill(world, name="broken", description="d", instructions="i", script="def (:")
    assert not r.ok and "not valid Python" in r.error
    assert not (world.home / "skills" / "broken").exists()


def test_saved_runs_only_from_the_users_own_skills(world):
    run = _run(world, None, saved="no-such-skill")
    assert not run.ok and "no skill named" in run.error
    assert _create_skill(world, name="plain", description="d", instructions="i").ok
    run = _run(world, None, saved="plain")
    assert not run.ok and "has no saved script" in run.error
    run = _run(world, "print(1)", saved="plain")
    assert not run.ok and "not both" in run.error
    for bad in ("../outside", "..\\..\\x", "C:/Windows"):
        run = _run(world, None, saved=bad)
        assert not run.ok


def test_code_is_still_needed_when_nothing_is_saved(world):
    r = _invoke(world, NAME, {})
    assert not r.ok and "code is required" in r.error


# --------------------------------------------------------------------------- #
# skill_update
# --------------------------------------------------------------------------- #


def _update(w, **args):
    return _invoke(w, "skill_update", args, allowed=("skill_update",),
                   session_allow={"skill_update"})


def test_a_skill_is_changed_and_the_version_before_is_kept(world):
    assert _create_skill(world, name="Tax advice", description="old words",
                         instructions="Step one.").ok
    r = _update(world, name="Tax advice", append="Step two: check the state rules.",
                description="new words")
    assert r.ok, r.error
    assert "its description" in r.output and "text added at the end" in r.output
    skill_dir = world.home / "skills" / "tax-advice"
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "Step one." in text and "Step two: check the state rules." in text
    assert "new words" in text and "old words" not in text
    kept = list((skill_dir / ".history").iterdir())
    assert len(kept) == 1 and "old words" in kept[0].read_text(encoding="utf-8")
    # the registry serves the new text at once
    assert "Step two" in world.p.skills.get("Tax advice").instructions


def test_replacing_the_instructions_keeps_provenance_and_the_pin(world):
    assert _create_skill(world, name="Close", description="d", instructions="Old.").ok
    r = _update(world, name="Close", instructions="All new.")
    assert r.ok, r.error
    text = (world.home / "skills" / "close" / "SKILL.md").read_text(encoding="utf-8")
    assert "All new." in text and "Old." not in text
    assert "created_by: agent" in text and "pinned: true" in text


def test_a_script_can_be_added_or_replaced_by_an_update(world):
    assert _create_skill(world, name="Totals", description="d", instructions="Add up.").ok
    r = _update(world, name="Totals", script="print('v1', params)")
    assert r.ok, r.error
    assert "its saved script" in r.output
    text = (world.home / "skills" / "totals" / "SKILL.md").read_text(encoding="utf-8")
    assert text.count(SS.NOTE_HEAD) == 1
    assert "v1 {}" in _run(world, None, saved="Totals").output
    r = _update(world, name="Totals", script="print('v2')")
    assert r.ok, r.error
    assert "v2" in _run(world, None, saved="Totals").output
    text = (world.home / "skills" / "totals" / "SKILL.md").read_text(encoding="utf-8")
    assert text.count(SS.NOTE_HEAD) == 1  # one note, not two
    history = sorted(p.name for p in (world.home / "skills" / "totals" / ".history").iterdir())
    assert any(name.endswith("main.py") for name in history)


def test_only_the_users_own_skills_can_be_changed(world):
    r = _update(world, name="does-not-exist", append="x")
    assert not r.ok and "no skill named" in r.error
    builtin = next(iter(s.name for s in world.p.skills.list() if s.source == "builtin"), None)
    if builtin:
        r = _update(world, name=builtin, append="x")
        assert not r.ok and "not one of your own skills" in r.error


def test_an_update_needs_something_to_change_and_never_both_texts(world):
    assert _create_skill(world, name="S", description="d", instructions="i").ok
    assert "nothing to change" in _update(world, name="S").error
    assert "not both" in _update(world, name="S", instructions="a", append="b").error
    assert "not valid Python" in _update(world, name="S", script="def (:").error
    assert not (world.home / "skills" / "s" / ".history").exists()  # nothing was touched


def test_skill_update_asks_and_is_kept_from_a_low_trust_turn(world):
    from iron_jarvis.core.trust import LOW_TRUST_DENY

    assert world.p.config.permissions.get("skill_update") == "ask"
    assert world.p.config.permissions.get("apply_changes") == "ask"
    assert "skill_update" in LOW_TRUST_DENY
