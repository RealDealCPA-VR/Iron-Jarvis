"""v1.185.0: the defects v1.178.0/v1.179.0 reviewers found and nobody fixed.

Four of them, each diagnosed in ``docs/TODO.md`` and each carried across six
releases. They share a shape worth naming: every one is a place where the app
says something CONFIDENT about a fact it does not have.

* ``_effective_tools`` answered ``[]`` when the registry would not answer, so
  "I don't know this agent's roster" and "this agent holds nothing" arrived as
  the same wire value — and they are opposite instructions to the card.
* ``list_agents`` (the AGENT-facing tool) reported no roster at all, so a
  supervisor deciding whom to delegate to saw less than the human looking at
  the same agent in the dashboard.
* ``approve()`` split its pending guard, its apply and its write across three
  transactions, so two clicks a second apart both passed the guard.
* The proposals listing returned every row ever filed, unbounded.

Each is mutation-proven: revert the fix, watch the named assertion go red.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iron_jarvis.agents.agent_tools import ListAgentsTool
from iron_jarvis.capability import store as _cap_store
from iron_jarvis.capability.routes import register as register_capability
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _cap_client(platform) -> TestClient:
    app = FastAPI()
    register_capability(app, SimpleNamespace(platform=platform))
    return TestClient(app)


# --------------------------------------------------------------------------- #
# 1. `_effective_tools`: None means unknown, [] means genuinely none.
# --------------------------------------------------------------------------- #


def _make_agent(client, name="scribe", tools=None):
    r = client.post(
        "/agents",
        json={
            "name": name,
            "system_prompt": "take notes",
            "tools": [] if tools is None else tools,
            "description": "notes",
        },
    )
    assert r.status_code in (200, 201), r.text
    return name


def _row(client, name):
    rows = client.get("/agents").json()["dynamic"]
    return next(r for r in rows if r["name"] == name)


def test_an_inheriting_agent_reports_the_roster_it_actually_holds(tmp_path):
    """The v1.178.0 baseline, restated so the fix below cannot regress it: an
    empty STORED list means inherit, and the resolved roster is what ships."""
    client = _client(tmp_path)
    _make_agent(client, "scribe", tools=[])

    row = _row(client, "scribe")
    assert row["tools"] == []  # stored, exactly as saved
    assert isinstance(row["effective_tools"], list)
    assert row["effective_tools"], "an inheriting agent holds the base roster"


def test_an_unresolvable_roster_is_null_not_empty(tmp_path, monkeypatch):
    """UNKNOWN AND NONE ARE DIFFERENT SENTENCES. `[]` from the failure branch
    told the card "this agent holds no tools" — a claim about the agent — when
    the truth was "the registry would not answer". The card renders those
    opposite ways: `null` falls back to the stored list and labels the roster
    unreported, `[]` states a fact. Returning the confident one on a failure is
    the same shape as the bug this field was ADDED to fix."""
    client = _client(tmp_path)
    _make_agent(client, "scribe", tools=[])
    registry = client.app.state.platform.agents_registry

    def _boom(_name):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(registry, "definition", _boom)

    row = _row(client, "scribe")
    assert row["effective_tools"] is None, "unknown must not arrive as a roster"
    # The listing still renders — a display field may never break the page.
    assert row["name"] == "scribe"


def test_a_genuinely_empty_roster_is_still_empty(tmp_path, monkeypatch):
    """The other half, and the reason `None` had to be a new value rather than a
    re-use: `[]` must keep MEANING none, or the fix would just move the
    ambiguity."""
    client = _client(tmp_path)
    _make_agent(client, "scribe", tools=[])
    registry = client.app.state.platform.agents_registry
    real = registry.definition

    def _empty(name):
        definition = real(name)
        if definition is not None:
            definition.tools = []
        return definition

    monkeypatch.setattr(registry, "definition", _empty)
    assert _row(client, "scribe")["effective_tools"] == []


# --------------------------------------------------------------------------- #
# 2. list_agents: the roster reaches the MODEL, not just `data`.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_list_agents_puts_the_roster_where_the_model_can_read_it(tmp_path):
    """`AgentRuntime` hands the model `result.output` AND NOTHING ELSE, so a
    field added only to `data` would satisfy "the tool reports effective tools"
    while the one caller that acts on it still could not see it. Asserted
    against the OUTPUT text for that reason."""
    client = _client(tmp_path)
    _make_agent(client, "scribe", tools=["read_file", "write_file"])
    platform = client.app.state.platform
    tool = ListAgentsTool(platform, platform.agents_registry)

    result = await tool.execute({}, None)

    assert result.ok
    assert "scribe" in result.output
    assert "read_file" in result.output, "the roster must be in the TEXT"
    assert "2 tools" in result.output
    # …and structured for anything that reads `data`.
    row = next(r for r in result.data["dynamic"] if r["name"] == "scribe")
    assert row["effective_tools"] == ["read_file", "write_file"]


@pytest.mark.asyncio
async def test_list_agents_says_unknown_rather_than_none(tmp_path, monkeypatch):
    """Same dialect as the route. "holds no tools" reads to a supervisor as a
    reason NOT to delegate, so an unknown roster must never be rendered as an
    empty one."""
    client = _client(tmp_path)
    _make_agent(client, "scribe", tools=[])
    platform = client.app.state.platform
    monkeypatch.setattr(
        platform.agents_registry,
        "definition",
        lambda _n: (_ for _ in ()).throw(RuntimeError("nope")),
    )
    tool = ListAgentsTool(platform, platform.agents_registry)

    result = await tool.execute({}, None)

    assert result.ok
    assert "roster unavailable" in result.output
    assert "holds no tools" not in result.output


@pytest.mark.asyncio
async def test_a_long_roster_is_capped_and_says_so(tmp_path):
    """Cap, then SAY SO — the file walker's rule. A silently short roster reads
    as complete and the model concludes a tool is absent, which is precisely the
    five-gaps-in-five-releases failure this field exists to end."""
    client = _client(tmp_path)
    many = [f"tool_{i}" for i in range(30)]
    _make_agent(client, "scribe", tools=many)
    platform = client.app.state.platform
    tool = ListAgentsTool(platform, platform.agents_registry)

    result = await tool.execute({}, None)

    assert "30 tools" in result.output  # the true size, always
    assert "+18 more" in result.output  # 30 - the 12-name preview
    assert "tool_29" not in result.output  # the cap really bit
    # The full list is still available to anything reading `data`.
    row = next(r for r in result.data["dynamic"] if r["name"] == "scribe")
    assert row["effective_tools"] == many


# --------------------------------------------------------------------------- #
# 3. approve() is atomic: two clicks, one approval.
# --------------------------------------------------------------------------- #


@pytest.fixture
def platform(tmp_path):
    return build_platform(str(tmp_path))


def _file_proposal(platform, name="wc_lines", kind="tool", spec=None) -> str:
    record = platform.capabilities.create(
        kind=kind,
        name=name,
        rationale="counting lines came up four times today",
        scope="a tax folder survey",
        spec={"command": ["wc", "-l", "{path}"]} if spec is None else spec,
    )
    assert record is not None
    return record.id


def test_two_simultaneous_approvals_produce_one_approval(platform):
    """THE RACE IS REAL BECAUSE THE HANDLERS ARE SYNC. FastAPI runs `def`
    handlers in worker THREADS, so two clicks genuinely execute concurrently:
    both read PENDING, both ran `_apply`, both stamped the row. Driven here with
    two real threads released together by a barrier — a sequential double-call
    would pass even with the bug, because by the second call the first has
    already written APPROVED."""
    pid = _file_proposal(platform)
    store = platform.capabilities

    # `_apply` IS THE ASSERTION, and this is the part a weaker test misses.
    # A guard on the final write alone still lets both threads run `_apply` —
    # the second one loses the stamping race and raises, so the RESPONSES look
    # correct while the capability was created twice. Counting the applies is
    # what distinguishes "one approval" from "one approval that was reported".
    applies: list[str] = []
    real_apply = store._apply

    def _counting_apply(**kw):
        applies.append(kw.get("name", ""))
        # Widen the window the race needs, so a missing claim is caught every
        # run rather than on an unlucky one.
        threading.Event().wait(0.15)
        return real_apply(**kw)

    store._apply = _counting_apply

    outcomes: list[str] = []
    gate = threading.Barrier(2)

    def _go():
        gate.wait()
        try:
            _row, result = store.approve(pid)
            outcomes.append("applied" if result.ok else "refused")
        except ValueError:
            outcomes.append("rejected-as-decided")

    threads = [threading.Thread(target=_go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert applies == ["wc_lines"], f"the capability was created {len(applies)}x"
    assert sorted(outcomes) == ["applied", "rejected-as-decided"], outcomes
    assert store.get(pid).status == "approved"


def test_a_second_approval_after_the_first_is_a_409(platform):
    """The sequential case, which always worked — kept so the concurrent test
    above is clearly testing the CONCURRENCY and not this."""
    pid = _file_proposal(platform)
    client = _cap_client(platform)

    assert client.post(f"/capability/proposals/{pid}/approve").status_code == 200
    assert client.post(f"/capability/proposals/{pid}/approve").status_code == 409


def test_the_claim_is_released_when_an_approval_fails(platform):
    """A claim that leaked on the failure path would be worse than the race: the
    row stays PENDING by design so the user can fix the request and try again,
    and a stuck claim would refuse that retry forever."""
    pid = _file_proposal(platform, name="some-server", kind="mcp", spec={})
    client = _cap_client(platform)

    assert client.post(f"/capability/proposals/{pid}/approve").status_code == 409
    assert pid not in _cap_store._CLAIMS, "the claim must not outlive the attempt"
    # Still pending, still retryable — and reaching the same honest 409 again
    # proves the retry got as far as the apply rather than being refused by a
    # stale claim (which would 409 too, for the wrong reason).
    assert platform.capabilities.get(pid).status == "pending"
    assert client.post(f"/capability/proposals/{pid}/approve").status_code == 409


# --------------------------------------------------------------------------- #
# 4. The listing is bounded, and says when it bit.
# --------------------------------------------------------------------------- #


def test_the_listing_is_capped_and_reports_the_truncation(platform, monkeypatch):
    """Unbounded, this rebuilt every proposal ever filed — each with its spec and
    rationale — on every poll of the card. The cap is the easy half; the honest
    half is that `stats` still counts the whole table, so a capped listing is
    visible as capped instead of reading as all of them."""
    monkeypatch.setattr(_cap_store, "LIST_LIMIT", 3)
    for i in range(6):
        _file_proposal(platform, name=f"tool_{i}")

    out = _cap_client(platform).get("/capability/proposals").json()

    assert out["returned"] == 3
    assert len(out["proposals"]) == 3
    assert out["truncated"] is True
    assert out["stats"]["total"] == 6, "the count is of the WHOLE table"


def test_an_uncapped_listing_reports_no_truncation(platform):
    """The flag has to be earned. A `truncated: true` that is always true would
    be exactly as uninformative as the silent cap it replaced."""
    _file_proposal(platform)

    out = _cap_client(platform).get("/capability/proposals").json()
    assert out["returned"] == 1 and out["truncated"] is False


def test_the_cap_never_drops_a_pending_request(platform, monkeypatch):
    """WHICH rows the cap drops is the whole safety argument. Pending sorts
    first, so the cap bites into decided history — the part of this table nobody
    is waiting on. A cap that could hide a request awaiting the user would turn
    a bounded listing into a silently ignored ask."""
    monkeypatch.setattr(_cap_store, "LIST_LIMIT", 2)
    ids = [_file_proposal(platform, name=f"tool_{i}") for i in range(4)]
    # Decide two of them, leaving two pending.
    for pid in ids[:2]:
        platform.capabilities.reject(pid)

    out = _cap_client(platform).get("/capability/proposals").json()

    assert out["returned"] == 2
    assert {p["status"] for p in out["proposals"]} == {"pending"}
    assert {p["id"] for p in out["proposals"]} == set(ids[2:])
