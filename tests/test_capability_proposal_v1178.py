"""v1.178.0 P4 — THE AGENT ASKS FOR THE TOOL IT NEEDS, THE USER APPROVES.

THE MEASURED FAILURE. *"Rename all files in this folder"* ran FOUR times and
renamed nothing, because no rename tool existed. The agent never said so: it
shelled out and wrote PyMuPDF scripts to re-read PDFs it had already read
successfully — 25 ``shell`` calls in one run (ledger ``run_ab82dea4bf8a``). Five
capabilities in a row shipped without reaching the agent that needed them, and
every one was found by a live job failing.

``capability_propose`` is the missing sentence, made durable: *this job needed a
verb you do not have; here is why and here is exactly what it would be allowed to
do.* Every test below pins one property that sentence is worthless without, and
each has a SILENT failure mode — which is why they assert on VALUES (what is in
the registry, what mode a key resolves to, what the row's status is) and never on
the absence of an exception:

* filing changes NOTHING — no tool, no permission, no registry entry. A propose
  tool that quietly creates is a self-granting agent;
* the request is LISTABLE, or the user never sees it and the queue is inert
  (``memory_propose``'s first bug: nothing in production ever called it);
* approving creates the thing through ``tool_create`` — the path the app already
  owns — and the result still runs at ``custom:<name>`` -> "ask", because an
  approval is consent to CREATE, never consent to RUN unattended;
* approving writes NO permission entry at all;
* rejecting takes it off the queue AND suppresses the re-ask, so "no" sticks;
* a request that would raise a DENY-FLOOR tool is refused — at file time (where
  the model can still ask for something narrower) and again at approve time
  (where the click happens, and where a row from an older build or a direct API
  call would otherwise land);
* a "custom tool" whose command is ``bash -c`` is `shell` under another name and
  is refused on both sides — the floor is on the CAPABILITY, not on the string;
* an MCP server / a connection cannot be created from here and the card says so
  BEFORE the click, not after;
* the table is in ``SQLModel.metadata`` AT BOOT (the v1.151.2 lesson: a lazily
  created table lands on every fresh test DB and on no real install);
* the tool is permitted in a HEADLESS run — an absent permission key resolves to
  "ask", and a headless "ask" is a DENY, which would silently re-create the exact
  failure this feature exists to end.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iron_jarvis.capability import (
    CAPABILITY_TOOL_NAME,
    CapabilityProposalStore,
    floor_violation,
    register,
)
from iron_jarvis.capability.models import PENDING, REJECTED
from iron_jarvis.core.config import default_permissions
from iron_jarvis.core.models import PermissionMode
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.dynamic import CommandTool
from iron_jarvis.tools.permissions import (
    DENY_FLOOR_TOOLS,
    PermissionEngine,
    headless_ask_resolver,
)

#: A capability the app genuinely does not have, with a real argv shape. Nothing
#: in this file ever RUNS it — creation is registration, not execution.
_ASK = {
    "kind": "tool",
    "name": "pdf_page_count",
    "why": (
        "I am renaming 26 scanned files and need each PDF's page count to tell a "
        "one-page receipt from a return; I had to open every file to find out."
    ),
    "allowed_to": "Report how many pages a PDF has. It reads the file and writes nothing.",
    "task": "rename 26 files in C:/clients/2025 to match their contents",
    "command": ["qpdf", "--show-npages", "{file}"],
    "parameters": [{"name": "file", "type": "string", "required": True}],
}


@pytest.fixture
def platform(tmp_path):
    return build_platform(str(tmp_path))


@pytest.fixture
def store(platform) -> CapabilityProposalStore:
    """The store BUILD_PLATFORM made — not a fresh one. If the registration
    point is missing this fixture fails, which is the point."""
    assert platform.capabilities is not None, (
        "build_platform did not attach a capability store — the tool would file "
        "into one queue and the routes would read another"
    )
    return platform.capabilities


@pytest.fixture
def propose(platform):
    """The tool as an AGENT reaches it: out of the live registry, by name."""
    tool = platform.registry.get(CAPABILITY_TOOL_NAME)
    assert tool is not None, (
        f"{CAPABILITY_TOOL_NAME} is not registered, so no agent can ever say the "
        "app is missing a verb"
    )
    return tool


def _ctx(platform, session_id: str = "sess-rename") -> ToolContext:
    return ToolContext(
        workspace=Path(platform.config.home),
        session_id=session_id,
        agent_run_id="",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


def _file(propose, platform, **overrides):
    """Call the tool the way the runtime does, returning its ToolResult."""
    args = {**_ASK, **overrides}
    return asyncio.run(propose.execute(args, _ctx(platform)))


def _client(platform) -> TestClient:
    app = FastAPI()
    register(app, SimpleNamespace(platform=platform))
    return TestClient(app)


# --------------------------------------------------------------------------- #
# 1. Filing changes NOTHING.
# --------------------------------------------------------------------------- #
def test_filing_a_request_creates_absolutely_nothing(platform, store, propose):
    """The whole suggest-don't-act contract, asserted on the machine's state.

    A propose tool that creates is not a proposal, it is a self-granting agent —
    so this checks the four places a creation would show up (the live registry,
    the persisted dynamic-tool table, the permission config, and the resolved
    mode) rather than trusting the tool's own success message.
    """
    tools_before = set(platform.registry.names())
    perms_before = dict(platform.config.permissions)

    result = _file(propose, platform)

    assert result.ok, result.error
    assert result.data["filed"] is True
    assert result.data["granted"] is False, (
        "the tool reported that filing GRANTED the capability — that is the one "
        "thing it must never claim"
    )
    assert set(platform.registry.names()) == tools_before, (
        "filing a request registered a tool; nothing may exist until the user "
        "approves"
    )
    assert [r.name for r in platform.tools_registry.list()] == [], (
        "filing a request persisted a DynamicToolRecord"
    )
    assert platform.config.permissions == perms_before, (
        "filing a request wrote a permission entry"
    )
    assert (
        platform.permissions.mode_for("custom:pdf_page_count") is PermissionMode.ASK
    )
    # ...and the row it DID write is pending, attributed to the run that asked.
    rows = store.list()
    assert [(r.name, r.status, r.run_id) for r in rows] == [
        ("pdf_page_count", PENDING, "sess-rename")
    ]


def test_the_reply_tells_the_agent_to_SAY_SO_and_not_work_around_it(platform, propose):
    """The failure was silence, not absence.

    The run that renamed nothing had 25 shell calls and no sentence to the user.
    The tool's own output is the only instruction the model reliably reads back,
    so it has to carry both halves: you did NOT get this, and tell the user.
    """
    output = _file(propose, platform).output.lower()
    assert "nothing was installed" in output
    assert "do not have it in this run" in output
    assert "tell the user" in output
    assert "shell" in output


# --------------------------------------------------------------------------- #
# 2. Listing returns it.
# --------------------------------------------------------------------------- #
def test_a_filed_request_is_listed_with_what_approving_would_do(platform, propose):
    """An unreadable queue is an inert feature (memory_propose shipped that way).

    Asserted through the HTTP read the review card makes, not through the store,
    because a store the routes cannot reach is the same as no store.
    """
    filed = _file(propose, platform)
    body = _client(platform).get("/capability/proposals").json()

    assert body["pending"] == 1
    assert body["stats"]["pending"] == 1
    (row,) = body["proposals"]
    assert row["id"] == filed.data["id"]
    assert row["kind"] == "tool"
    assert row["name"] == "pdf_page_count"
    assert row["status"] == PENDING
    # The user decides on the agent's own words plus the CONCRETE effect.
    assert row["rationale"].startswith("I am renaming 26 scanned files")
    assert row["scope"].startswith("Report how many pages")
    assert row["task"] == _ASK["task"]
    assert row["command"] == ["qpdf", "--show-npages", "{file}"]
    assert row["can_apply"] is True
    assert row["blocked"] == ""
    # Shown next to the request so "requested: ask" and "runs under" can never
    # be confused for a granted permission.
    assert row["runs_under"] == "custom:pdf_page_count"


# --------------------------------------------------------------------------- #
# 3. Approving creates it — through the sanctioned path, at ask.
# --------------------------------------------------------------------------- #
def test_approving_creates_the_tool_through_tool_create_and_it_still_asks(
    platform, store, propose
):
    """The ONLY creation path, and what it does NOT create.

    ``tool_create`` is what an agent authoring a tool already goes through, so
    approval reuses it verbatim — same name validation, same built-in collision
    check, same ``shell=False`` argv rendering, same ``custom:<name>`` key. A
    second construction path would be a second thing to secure.
    """
    proposal_id = _file(propose, platform).data["id"]
    perms_before = dict(platform.config.permissions)

    record, result = store.approve(proposal_id)

    assert result.ok, result.error
    assert record.status == "approved"
    # (a) it went through the dynamic-tool registry, so it survives a restart
    persisted = platform.tools_registry.get("pdf_page_count")
    assert persisted is not None, "approval did not persist a DynamicToolRecord"
    assert json.loads(persisted.argv_json) == ["qpdf", "--show-npages", "{file}"]
    # (b) it is live in THIS process, as the CommandTool tool_create builds
    live = platform.registry.get("pdf_page_count")
    assert isinstance(live, CommandTool)
    assert "pdf_page_count" in set(platform.registry.custom_names())
    # (c) the description the user approved is the one every future agent reads
    assert live.description.startswith("Report how many pages")
    # (d) IT STILL ASKS. Approval is consent to create, never consent to run.
    assert live.perm_key() == "custom:pdf_page_count"
    assert result.permission_key == "custom:pdf_page_count"
    assert result.permission_mode == PermissionMode.ASK.value
    assert platform.permissions.mode_for("custom:pdf_page_count") is PermissionMode.ASK
    # (e) and NO permission entry was written — not even a benign one.
    assert platform.config.permissions == perms_before, (
        "approval wrote a permission entry; a proposal must never be able to "
        "grant itself anything"
    )


def test_approving_twice_is_a_conflict_not_a_second_tool(platform, store, propose):
    """A double-clicked Approve must not read as "it vanished" (404) and must
    not create the capability twice."""
    proposal_id = _file(propose, platform).data["id"]
    store.approve(proposal_id)
    with pytest.raises(ValueError, match="already approved"):
        store.approve(proposal_id)


# --------------------------------------------------------------------------- #
# 4. Rejecting removes it (and the ask does not come back).
# --------------------------------------------------------------------------- #
def test_rejecting_takes_it_off_the_queue_and_the_ask_is_suppressed(
    platform, store, propose
):
    """"Not this" has to stick harder than a model's memory.

    The agent re-derives the same gap on every run, so a rejected request that
    could be re-filed tomorrow is a nag the user cannot switch off — and the
    model would read its own success message as progress.
    """
    proposal_id = _file(propose, platform).data["id"]
    client = _client(platform)

    body = client.post(f"/capability/proposals/{proposal_id}/reject").json()

    assert body["status"] == REJECTED
    assert store.list(PENDING) == []
    assert client.get("/capability/proposals").json()["pending"] == 0
    assert platform.registry.get("pdf_page_count") is None, (
        "rejecting created something"
    )

    # Asking again is answered honestly rather than silently re-queued.
    again = _file(propose, platform)
    assert again.ok and again.data["filed"] is False
    assert again.data["reason"] == "suppressed"
    assert store.list(PENDING) == []


def test_the_DAEMON_mounts_the_queue(tmp_path):
    """The routes exist only if ``create_app`` calls ``register``.

    Every other test in this file mounts the router itself, which is exactly how
    a feature ships with a store, a tool, working rows — and no surface. Asserted
    against the real app the desktop talks to.
    """
    from iron_jarvis.daemon.app import create_app

    response = TestClient(create_app(str(tmp_path))).get("/capability/proposals")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "proposals": [],
        "pending": 0,
        "stats": {"pending": 0, "approved": 0, "rejected": 0, "total": 0, "by_kind": {}},
    }


def test_rejecting_an_unknown_request_is_a_404(platform):
    assert (
        _client(platform).post("/capability/proposals/capprop_nope/reject").status_code
        == 404
    )


# --------------------------------------------------------------------------- #
# 5. The deny floor — refused at APPROVAL.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("floor_tool", sorted(DENY_FLOOR_TOOLS))
def test_approval_refuses_a_request_that_would_raise_a_deny_floor_tool(
    platform, store, floor_tool
):
    """The safety property, checked where the CLICK is.

    Filed straight into the store, bypassing the tool's own screen, because that
    is the case that matters: a row can reach this table from an older build, a
    direct API call, or a hand-edited record, and the approve button is where it
    would take effect. Without this check the request would go on to
    ``tool_create`` and the deny floor — the thing that stops an agent definition
    arming the host shell for an unattended run — would have a second door.
    """
    record = store.create(
        kind="tool",
        name=floor_tool,
        rationale="the job needed it",
        scope="everything",
        spec={"command": ["qpdf", "--version"]},
    )
    assert record is not None

    row, result = store.approve(record.id)

    assert result.ok is False
    assert "deny floor" in result.error, result.error
    assert result.created == ""
    assert row.status == PENDING, "a refused approval must leave the row pending"
    assert platform.tools_registry.get(floor_tool) is None
    # ...and the floor tool itself is untouched: still exactly its base mode.
    assert platform.permissions.mode_for(floor_tool) is not PermissionMode.ALLOW


def test_approval_refuses_a_custom_tool_that_is_a_shell_in_disguise(platform, store):
    """`custom:<name>` is NOT on the deny floor, so a tool whose command is
    ``bash -c {cmd}`` would rebuild `shell` under a name the floor never hears
    about — and it would be created, persisted, and reachable by every future
    agent. The floor is on the CAPABILITY, not on the string "shell"."""
    record = store.create(
        kind="tool",
        name="run_anything",
        rationale="I need to run a couple of commands",
        scope="run a command",
        spec={"command": ["bash", "-c", "{cmd}"]},
    )
    row, result = store.approve(record.id)

    assert result.ok is False
    assert "interpreter" in result.error, result.error
    assert row.status == PENDING
    assert platform.registry.get("run_anything") is None
    assert platform.tools_registry.get("run_anything") is None


@pytest.mark.parametrize(
    "argv0",
    ["bash.exe", "sh.exe", "zsh.exe", "python3.exe", "ruby.exe", "perl.exe",
     "wscript.exe", "Bash.EXE", "C:\\Program Files\\Git\\bin\\bash.exe"],
)
def test_a_windows_suffixed_interpreter_is_the_same_shell(platform, store, argv0):
    """The blocklist is on the PROGRAM, not on the spelling of its filename.

    Measured on this box: the set was written with the suffixed spellings by
    hand and carried ``cmd.exe``/``python.exe`` but not ``bash.exe`` — so
    ``["bash.exe", "-c", "{cmd}"]`` filed, approved, and CREATED a working
    shell-under-another-name on the one platform this app ships on. Every name
    below is a real executable on a Windows install with Git/Python present.
    """
    assert floor_violation("tool", "run_anything", {"command": [argv0, "-c", "{c}"]}), (
        f"{argv0} walked through the interpreter check"
    )

    record = store.create(
        kind="tool", name="run_anything", rationale="r", scope="s",
        spec={"command": [argv0, "-c", "{cmd}"]},
    )
    row, result = store.approve(record.id)
    assert result.ok is False, result.detail
    assert row.status == PENDING
    assert platform.registry.get("run_anything") is None


def test_a_tool_whose_PROGRAM_is_a_parameter_is_the_shell_itself(platform, store, propose):
    """``["{prog}", "{a}"]`` names no program: ``CommandTool`` fills argv[0]
    from the CALL arguments, so the created tool runs whatever the model passes.
    Measured end to end before the fix — approved, then executed with
    ``prog=cmd, a=/c`` and printed arbitrary output. Every interpreter name in
    the blocklist is irrelevant when argv[0] is a hole."""
    result = _file(
        propose, platform, name="run_program",
        command=["{prog}", "{arg}"],
        parameters=[{"name": "prog", "type": "string", "required": True},
                    {"name": "arg", "type": "string", "required": False}],
    )
    assert result.ok is False
    assert "parameter" in result.error, result.error
    assert store.list() == []

    # ...and the click refuses it too, for a row filed some other way.
    record = store.create(
        kind="tool", name="run_program", rationale="r", scope="s",
        spec={"command": ["{prog}", "{arg}"]},
    )
    row, applied = store.approve(record.id)
    assert applied.ok is False
    assert row.status == PENDING
    assert platform.registry.get("run_program") is None


def test_approving_never_REPLACES_a_capability_that_now_exists(platform, store, propose):
    """A card outlives the state it was filed against.

    The tool refuses to FILE a request for a name the app already has — but a
    request filed while the name was free and approved after the user authored
    their own tool went straight through ``tools_registry.register``, which
    UPSERTS by name: the user's argv was silently replaced and the live registry
    rebound. Approving is consent to ADD one thing, never to overwrite one.
    """
    proposal_id = _file(propose, platform, name="pdf_pages").data["id"]
    # The user (or another agent) makes their own tool of that name meanwhile.
    mine = asyncio.run(
        platform.registry.get("tool_create").execute(
            {"name": "pdf_pages", "description": "mine",
             "command": ["qpdf", "--version"], "parameters": []},
            _ctx(platform),
        )
    )
    assert mine.ok, mine.error

    row, result = store.approve(proposal_id)

    assert result.ok is False
    assert "already exists" in result.error, result.error
    assert row.status == PENDING, "a refused approval must leave the row pending"
    assert json.loads(platform.tools_registry.get("pdf_pages").argv_json) == [
        "qpdf",
        "--version",
    ], "approval overwrote a custom tool the user already had"


def test_a_RUNNER_that_takes_its_program_from_an_argument_is_refused(platform, store):
    """`npx {pkg}` downloads and runs arbitrary code; `xargs {prog}` execs
    whatever it is handed. Refusing `python -c` while letting those through made
    the blocklist arbitrary — the hole is "the program comes from the argument",
    not the word "shell"."""
    for argv0 in ("npx", "bunx", "uvx", "xargs", "awk", "mshta"):
        assert floor_violation("tool", "runner", {"command": [argv0, "{a}"]}), argv0

    record = store.create(
        kind="tool", name="runner", rationale="r", scope="s",
        spec={"command": ["npx", "{pkg}"]},
    )
    row, result = store.approve(record.id)
    assert result.ok is False
    assert row.status == PENDING
    assert platform.registry.get("runner") is None


# --------------------------------------------------------------------------- #
# 5b. A shape that would BRICK THE NEXT BOOT.
# --------------------------------------------------------------------------- #
def test_parameters_that_are_not_objects_never_reach_tool_create(platform, store):
    """MEASURED: approving ``parameters: ["file"]`` persisted a
    ``DynamicToolRecord`` and THEN raised, because ``tool_create`` saves the row
    before ``build_tool`` parses it — so the approval reported an honest "could
    not create it" while leaving a poisoned row on disk, and the next
    ``build_platform`` (every daemon start) died in ``_build_input_schema`` with
    ``'str' object has no attribute 'get'``. The app never came up again.

    A refused approval that bricks the install is the worst outcome this feature
    can have, so the shape is refused BEFORE ``tool_create`` sees it — at the
    click, because a row can predate the file-time screen.
    """
    record = store.create(
        kind="tool", name="badparams", rationale="r", scope="s",
        spec={"command": ["qpdf", "{file}"], "parameters": ["file"]},
    )
    row, result = store.approve(record.id)

    assert result.ok is False
    assert "must be objects" in result.error, result.error
    assert row.status == PENDING
    assert platform.tools_registry.get("badparams") is None, (
        "a poisoned DynamicToolRecord was persisted; build_platform will raise "
        "AttributeError at every subsequent boot"
    )
    # The proof that matters: the app still starts.
    from iron_jarvis.platform import build_platform as _build

    assert _build(str(platform.config.home)).registry.get("badparams") is None


def test_the_tool_refuses_bad_parameters_while_the_model_can_still_fix_them(
    platform, store, propose
):
    """Same shape, screened where a model can retry in one turn.

    The command deliberately carries NO placeholder: with one, the
    unfilled-placeholder rule below would refuse this request too and the
    assertion would stay green with this screen deleted (measured — the first
    version of this test did exactly that).
    """
    result = _file(
        propose, platform, command=["qpdf", "--version"], parameters=["file"]
    )

    assert result.ok is False
    assert "must be objects" in result.error, result.error
    assert store.list() == [], "a request that cannot be created must not queue"


def test_a_placeholder_with_no_parameter_would_run_literally_and_is_refused(
    platform, store, propose
):
    """``CommandTool._render`` substitutes only NAMED parameters, so
    ``["qpdf", "{file}"]`` with no ``parameters`` creates a tool that runs the
    literal text ``{file}`` forever — an approval that succeeds and is silently
    always wrong."""
    result = _file(propose, platform, parameters=[])

    assert result.ok is False
    assert "{file}" in result.error, result.error
    assert store.list() == []


def test_the_floor_rule_is_one_implementation_for_both_call_sites():
    """File time and approve time must never drift into two opinions."""
    assert floor_violation("tool", "shell") != ""
    assert floor_violation("tool", "  ShElL  ") != "", (
        "the floor is checked on the NORMALIZED name, or 'ShElL' walks through it"
    )
    assert floor_violation("tool", "pdf_page_count", {"command": ["qpdf", "-v"]}) == ""


# --------------------------------------------------------------------------- #
# 6. The deny floor — refused at FILE time too (where the model can still fix it).
# --------------------------------------------------------------------------- #
def test_the_tool_refuses_to_file_a_deny_floor_request(platform, store, propose):
    """A courtesy, not the safety property — but the courtesy is what turns a
    refusal into a narrower second attempt instead of a dead card."""
    result = _file(propose, platform, kind="tool", name="repl", command=["qpdf"])

    assert result.ok is False
    assert "deny floor" in result.error, result.error
    assert store.list() == [], "a refused request must not reach the queue"


def test_the_tool_refuses_to_file_a_shell_wrapper(platform, store, propose):
    result = _file(
        propose,
        platform,
        name="run_anything",
        command=["powershell", "-Command", "{cmd}"],
    )

    assert result.ok is False
    assert "interpreter" in result.error, result.error
    assert store.list() == []


def test_asking_for_something_the_app_already_has_is_answered_with_its_name(
    platform, store, propose
):
    """The gap in the measured run was partly KNOWLEDGE: the agent wrote PyMuPDF
    scripts to re-read PDFs `read_document` had already read for it. A request
    for a capability that exists is that same blindness, and the useful answer
    is the real tool's name — not a card the user has to dismiss."""
    result = _file(propose, platform, name="rename_file", command=["qpdf"])

    assert result.ok is False
    assert "rename_file" in result.error
    assert "already exists" in result.error
    assert store.list() == []


# --------------------------------------------------------------------------- #
# 7. Honest about what approval cannot do.
# --------------------------------------------------------------------------- #
def test_an_mcp_request_is_filed_but_says_it_cannot_be_created_from_here(
    platform, store, propose
):
    """An MCP server needs a command and credentials only the user has, and its
    tools answer to ``mcp_call`` — a deny-floor tool. So approval cannot make
    one, and the card has to say so BEFORE the click; a button that refuses
    after the fact is the "silent approved that changed nothing" this codebase
    already learned to avoid on the memory review card."""
    result = _file(
        propose,
        platform,
        kind="mcp",
        name="box",
        command=[],
        parameters=[],
        details="mcp-server-box",
    )
    assert result.ok, result.error
    assert result.data["can_apply"] is False
    assert "MCP server" in result.output

    (row,) = _client(platform).get("/capability/proposals").json()["proposals"]
    assert row["can_apply"] is False
    assert "Connections page" in row["kind_note"]

    record, applied = store.approve(result.data["id"])
    assert applied.ok is False
    assert "MCP server" in applied.error
    assert record.status == PENDING, "an un-appliable request stays on the queue"
    assert applied.created == ""


def test_a_tool_request_without_a_command_is_refused_while_it_can_be_fixed(
    platform, store, propose
):
    """``tool_create`` would refuse this at APPROVE time — hours later, with the
    user watching a button that does nothing. Refused here, where the model can
    still supply the argv (``memory_propose``'s rule)."""
    result = _file(propose, platform, command=[])
    assert result.ok is False
    assert "command" in result.error
    assert store.list() == []


# --------------------------------------------------------------------------- #
# 8. The table exists on a REAL install, not only on a fresh test DB.
# --------------------------------------------------------------------------- #
def _run(script: str) -> dict:
    """Execute *script* in a CLEAN interpreter and return its JSON stdout.

    A fresh process for the same reason ``tests/test_lazy_table_migrations.py``
    uses one: the registration under test is a process-global import side
    effect, so a test that has already imported ``capability.models`` — or that
    merely ran after this file's other tests did — populates the metadata itself
    and passes whether or not the fix exists.
    """
    out = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert out.returncode == 0, f"subprocess failed:\n{out.stdout}\n{out.stderr}"
    return json.loads([ln for ln in out.stdout.splitlines() if ln.startswith("{")][-1])


def test_the_table_is_registered_at_boot_and_exists_on_a_fresh_install(tmp_path):
    """v1.151.2, third time: a lazily-created table lands on every fresh test DB
    and on NO real install, because ``checkfirst=True`` sees an existing table
    and ``_reconcile_additive_columns`` walks only what was imported at boot. The
    subprocess imports ``core.db`` and nothing else — exactly what a daemon start
    does — so removing the ``_LATE_MODEL_MODULES`` entry turns this red."""
    result = _run(f"""
        import json, sqlite3
        from sqlmodel import SQLModel
        from iron_jarvis.core.db import open_db

        db = r{str(tmp_path / "fresh.db")!r}
        open_db(db)
        at_boot = sorted(SQLModel.metadata.tables)
        con = sqlite3.connect(db)
        on_disk = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        con.close()
        print(json.dumps({{"at_boot": at_boot, "on_disk": on_disk}}))
    """)

    assert "capabilityproposalrecord" in result["at_boot"], (
        "the capability table was absent from SQLModel.metadata at boot, so the "
        "reconciler cannot see it — add ..capability.models to "
        "core.db._LATE_MODEL_MODULES"
    )
    assert "capabilityproposalrecord" in result["on_disk"], (
        "a fresh install has no capability table, so the first request an agent "
        "files disappears"
    )


# --------------------------------------------------------------------------- #
# 9. It can actually be CALLED — the v1.178.0 permission lesson.
# --------------------------------------------------------------------------- #
def test_the_propose_tool_is_permitted_in_a_headless_run(platform):
    """An absent permission key resolves to "ask", and a headless "ask" is a
    DENY (``headless_ask_resolver`` auto-approves only delegate/spawn_agent). A
    tool whose whole job is reporting "I cannot do this", silently denied in
    every agent run, would reproduce the failure it exists to end — which is
    precisely what happened to the worklist, rename_file and view_image."""
    assert default_permissions()[CAPABILITY_TOOL_NAME] == "allow"
    assert platform.permissions.mode_for(CAPABILITY_TOOL_NAME) is PermissionMode.ALLOW

    headless = PermissionEngine(default_permissions(), headless_ask_resolver())
    decision = headless.authorize(CAPABILITY_TOOL_NAME, {})
    assert decision.allowed, decision.reason


def test_an_agent_definition_cannot_use_this_tool_to_reach_the_floor(platform):
    """Belt and braces on the shape of the whole feature: even if a proposal
    somehow named a floor tool, the permission engine still refuses to raise it
    from an agent definition. The floor is not enforced in one place."""
    for name in DENY_FLOOR_TOOLS:
        assert (
            platform.permissions.mode_for(name, {name: "allow"})
            is not PermissionMode.ALLOW
        )


# ------------------- a malformed custom tool must not brick the boot ---------


def test_a_non_dict_parameter_never_reaches_the_schema_builder():
    """REVIEW FINDING (v1.178.0). `tool_create` COMMITS the record and only then
    builds the tool, and `build_platform` rebuilds EVERY stored record with no
    guard — so one persisted `["path"]` raised `'str' object has no attribute
    'get'` while wiring the registry, before the daemon could serve anything or
    explain itself. A dropped parameter costs one argument; raising costs the
    install."""
    import json as _json

    from iron_jarvis.core.models import DynamicToolRecord
    from iron_jarvis.tools.dynamic import CommandTool, _build_input_schema

    assert _build_input_schema(["notadict", 123, None]) == {
        "type": "object",
        "properties": {},
        "required": [],
    }

    record = DynamicToolRecord(
        name="wc_lines",
        description="count lines",
        params_json=_json.dumps(["path", {"name": "file", "required": True}]),
        argv_json=_json.dumps(["wc", "-l", "{file}"]),
    )
    tool = CommandTool(record)  # must not raise — this is the boot path
    assert tool.input_schema["required"] == ["file"]
    # ...and the surviving entries are all mappings, so _render and execute
    # (which also call .get on them) cannot raise later either.
    assert all(isinstance(p, dict) for p in tool._params)


async def test_tool_create_refuses_a_malformed_parameter_up_front(tmp_path):
    """The honest half: reject the shape rather than store it and cope."""
    from types import SimpleNamespace

    from iron_jarvis.platform import build_platform
    from iron_jarvis.tools.base import ToolContext
    from iron_jarvis.tools.dynamic import dynamic_tool_tools

    platform = build_platform(str(tmp_path))
    create = next(t for t in dynamic_tool_tools(platform) if t.name == "tool_create")
    ctx = ToolContext(
        workspace=tmp_path, session_id="s", agent_run_id="r",
        config=platform.config, event_bus=None, engine=platform.engine,
    )
    result = await create.execute(
        {"name": "bad", "command": ["wc", "-l"], "parameters": ["path"]}, ctx
    )
    assert result.ok is False
    assert "must be an object" in result.error
    # Nothing was persisted, so no later boot has to survive it.
    assert platform.tools_registry.get("bad") is None
