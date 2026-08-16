"""ROSTER COVERAGE (v1.178.0): a capability that is not on a roster does not exist.

Five capabilities shipped into the registry, the permission table and chat's
auto-arming — and reached NO agent session, because nothing added them to a
definition in ``agents/types.py``. ``runtime.py`` advertises exactly
``registry.specs(agent_def.tools)``, so a name that is not on the list is not a
tool the model can see:

    history_search  v1.142  — the memory steward's own prompt told the session to
                              "pull more of it with history_search"
    workflow_list   v1.172  — an agent authoring workflows could not see the ones
                              the user already had
    view_image      v1.174  — registered platform-wide since v1.89; "gives any
                              agent EYES", on no definition, so no eyes
    worklist_*      v1.177.0 — a bulk run had no durable record and finished nothing
    rename_file     v1.177.2 — the acceptance job IS "rename these files" and
                              nothing on any roster could rename one

Every one was found by a LIVE JOB FAILING, never by a test. This file turns the
pattern into assertions: names on a roster must exist in the registry, and the
tools the acceptance job needs must be REACHABLE by the definition that runs it.

Reach is measured through ``registry.specs(definition.tools)`` — the exact call
the runtime makes — because that call SILENTLY DROPS any name the registry does
not serve. A roster entry the registry lost is invisible at every other layer.
"""

from __future__ import annotations

import pytest

from iron_jarvis.agents.supervisor import WORKLIST_MARKER, with_worklist
from iron_jarvis.agents.types import AgentDefinition, get_agent_definition
from iron_jarvis.core.models import AgentType
from iron_jarvis.tools.builtins import default_registry
from iron_jarvis.worklist import WORKLIST_TOOL_NAMES

# `custom:*` and `mcp:*` are ALLOWLIST SENTINELS, not tool names. ToolRegistry
# .specs() expands them at advertise time (`wild` / `mcp_wild` in registry.py)
# to every user-authored / connected-MCP tool. No tool is ever registered under
# either literal string, so an existence check that does not exclude them
# reports two permanent false positives and gets muted — which is exactly how a
# check like this stops catching the real thing.
_SENTINELS = ("custom:*", "mcp:*")

# THE ACCEPTANCE JOB — "organise this folder of documents", the job the app is
# repeatedly tested on: survey the folder, read every file (including the PDFs
# and the scans), then rename each file to what it turned out to be. Each entry
# names the release that had to ADD it after a live run failed without it.
#
# These strings are PRINTED by a failing assertion, so they stay ASCII: the
# Windows console this project is developed on renders an em dash as a
# replacement char, and a loud message is only loud if it is readable.
_ACCEPTANCE_TOOLS = {
    "list_files": "survey the folder - the job's first move",
    "read_file": "read a plain file",
    "read_document": "read pdf/docx/xlsx/pptx/csv as text",
    "extract_pdf": "pull text + tables out of a PDF",
    # v1.174.0: on the platform since v1.89, on no definition until v1.174 — a
    # scanned receipt is unreadable without it and the run reports an empty file.
    "view_image": "v1.174.0 - eyes; a scan is unreadable without it",
    # v1.177.2: the traced run shelled out instead and renamed nothing.
    "rename_file": "v1.177.2 - the job is literally 'rename these files'",
}


def _missing_from_registry(registry, names) -> list[str]:
    """Names ``registry`` does not serve, sentinels excluded (see _SENTINELS)."""
    have = set(registry.names())
    return sorted({n for n in names if n not in _SENTINELS and n not in have})


def _advertised(registry, definition: AgentDefinition) -> set[str]:
    """The tool names a RUN of ``definition`` would actually see.

    ``registry.specs(agent_def.tools)`` is what ``agents/runtime.py`` calls to
    build the provider's tool list, so this is reach as the model experiences
    it — not what the roster list claims.
    """
    return {
        spec.get("name") for spec in registry.specs(list(definition.tools))
    } - {None}


def _missing_reach(registry, definition: AgentDefinition, wanted) -> list[str]:
    """Of ``wanted``, the names ``definition`` cannot reach on ``registry``."""
    advertised = _advertised(registry, definition)
    return sorted(n for n in wanted if n not in advertised)


# --------------------------------------------------------------------------- #
# 1. Every name on every builtin roster is a tool that exists
# --------------------------------------------------------------------------- #
def test_every_builtin_roster_tool_exists_in_the_registry(platform):
    """A roster naming a tool that was renamed or removed fails HERE, loudly.

    Run against the PLATFORM registry (~88 tools), not ``default_registry``:
    the builtin registry serves only the seven file tools, so it can say nothing
    about `extract_pdf` or `worklist_add`. Nothing is silently skipped — a name
    absent from both is reported.
    """
    broken: dict[str, list[str]] = {}
    for agent_type in AgentType:
        definition = get_agent_definition(agent_type)
        missing = _missing_from_registry(platform.registry, definition.tools)
        if missing:
            broken[agent_type.value] = missing
    assert not broken, (
        "agent definitions in agents/types.py name tools the registry does not "
        "serve - a rename or a removal left the roster behind, and every session "
        f"of these types advertises one fewer tool than the list claims: {broken}"
    )


def test_the_allowlist_sentinels_are_not_registered_tools(platform):
    """Why the exclusion above exists, pinned: the sentinels are expansions and
    the registry holds nothing under those names, so an unfiltered check would
    report them forever."""
    for sentinel in _SENTINELS:
        assert platform.registry.get(sentinel) is None
        assert sentinel not in platform.registry.names()
    assert _missing_from_registry(platform.registry, list(_SENTINELS)) == []


def test_default_registry_serves_the_file_core_including_rename(platform):
    """The builtin registry is the OTHER half of the v1.177.2 hole: a roster
    name is worthless if nothing registers the tool. ``rename_file`` is asserted
    at BOTH layers so a fix at one layer alone cannot read as complete."""
    names = set(default_registry().names())
    assert {"read_file", "write_file", "edit_file", "rename_file",
            "list_files", "grep"} <= names
    # The builtin registry's names must survive into the platform's, or the
    # rosters that name them are pointing at the shadowed copy.
    assert names <= set(platform.registry.names())


# --------------------------------------------------------------------------- #
# 1b. THE DIRECTION THAT ACTUALLY FAILED (added in review)
# --------------------------------------------------------------------------- #
# The check above runs roster -> registry: a roster naming a tool nobody serves.
# That has never once been the bug. All five incidents in this file's header ran
# the OTHER way — the tool was registered, permissioned and auto-armed, and no
# roster named it — and the only guard against that here was the six hand-listed
# entries of ``_ACCEPTANCE_TOOLS``. A capability shipped next release still
# reaches no session and every test above stays green.
#
# So: every registered tool must be reachable by SOME builtin definition, or be
# listed below. The list is a SNAPSHOT taken in review, not a blessing — the
# names under OPEN are current gaps of exactly the shape that shipped five
# times, and this file cannot fix them (that is a change to ``agents/types.py``,
# which it does not own). What the assertion buys is the forcing function that
# was missing: registering a NEW tool and giving it to nobody now goes red at
# the moment it happens instead of at the next live job.
#
# Custom/MCP tools are excluded by construction, not by name: the `custom:*` and
# `mcp:*` sentinels already reach every one of them, so a registry holding them
# is covered. (A bare test platform has none, but a fixture that registered one
# would otherwise report a phantom orphan.)
_OFF_ROSTER_BY_DESIGN = {
    # Reached through `with_worklist`, not through a roster — asserted in §3.
    "worklist_add", "worklist_next", "worklist_done", "worklist_status",
}
_OFF_ROSTER_OPEN = {
    # Documents/media the acceptance job plausibly needs, on no definition:
    "convert_document",   # `_DOCUMENT_TOOLS` has read/write/extract_pdf, not this
    "batch_documents",    # whole-folder read without blowing the window
    "list_folder",        # lists a REAL folder (Downloads/Documents) — the
                          # acceptance job's folder is not always the workspace
    "image_convert", "image_resize", "image_info",  # view_image's siblings; only
                          # view_image was added in v1.174
    # Author + see a workflow but not run one:
    "workflow_run",
    # Skills: agents can search/load (`_KNOWLEDGE_TOOLS`) but not save one:
    "skill_create",
    # Web: researcher has browse/web_extract/web_search, not these:
    "web_fetch", "web_look",
    "delegate_remote", "sentinel_add",
    # Plausibly withheld on purpose (cost, consent, secrets) — NOT verified here,
    # which is why they sit under OPEN rather than BY_DESIGN:
    "repl", "run_code", "secret_set", "secret_list",
    "pixio_generate", "pixio_models", "pixio_params", "pixio_status",
    "pixio_upload",
}
_OFF_ROSTER = _OFF_ROSTER_BY_DESIGN | _OFF_ROSTER_OPEN


def test_a_newly_registered_tool_reaches_some_agent_roster(platform):
    """A tool nobody can call is the bug this whole file exists for."""
    reachable: set[str] = set()
    for agent_type in AgentType:
        reachable |= set(get_agent_definition(agent_type).tools)
    sentinel_covered = set(platform.registry.custom_names()) | set(
        platform.registry.mcp_names()
    )
    orphans = sorted(
        set(platform.registry.names()) - reachable - sentinel_covered - _OFF_ROSTER
    )
    assert not orphans, (
        "these tools are registered, permissioned and armed - and named by NO "
        "agent definition, so no agent session can call them. That is exactly "
        "how history_search, workflow_list, view_image, the worklist and "
        "rename_file each shipped dead. Put each one on the roster that needs "
        "it in agents/types.py, or add it to _OFF_ROSTER_OPEN above WITH the "
        f"reason it is deliberately unreachable: {orphans}"
    )


# --------------------------------------------------------------------------- #
# 2. The acceptance job's coverage table
# --------------------------------------------------------------------------- #
def test_builder_reaches_every_acceptance_job_tool(platform):
    """The document-organisation job, tool by tool, against the BUILDER.

    BUILDER because that is what actually runs it: ``SessionCreate.agent_type``
    defaults to ``"builder"`` and ``orchestrator.run_session`` sends every
    non-SUPERVISOR session to ``runtime.run`` with the canonical definition.
    """
    builder = get_agent_definition(AgentType.BUILDER)
    missing = _missing_reach(platform.registry, builder, _ACCEPTANCE_TOOLS)
    assert not missing, (
        "the BUILDER cannot reach these acceptance-job tools, so a live "
        "'organise this folder' run will fail the same way the release named "
        "beside each one did: "
        + "; ".join(f"{n} ({_ACCEPTANCE_TOOLS[n]})" for n in missing)
    )


@pytest.mark.parametrize("tool_name", sorted(_ACCEPTANCE_TOOLS))
def test_acceptance_tool_is_on_the_builder_roster_by_name(tool_name):
    """The same table read off the roster LIST rather than through specs(), so
    a registry that stopped serving a tool and a roster that stopped naming it
    are distinguishable failures instead of one ambiguous red."""
    builder = get_agent_definition(AgentType.BUILDER)
    assert tool_name in builder.tools, (
        f"{tool_name} is not on the builder roster - {_ACCEPTANCE_TOOLS[tool_name]}"
    )


# --------------------------------------------------------------------------- #
# 3. The worklist reaches a BULK run
# --------------------------------------------------------------------------- #
def test_bulk_builder_reaches_the_worklist_tools(platform):
    """v1.177.0: the wrapped builder is what a bulk job runs, so the four
    worklist tools must be advertised by the WRAP, not merely registered."""
    bulk = with_worklist(get_agent_definition(AgentType.BUILDER))
    missing = _missing_reach(platform.registry, bulk, WORKLIST_TOOL_NAMES)
    assert not missing, (
        "with_worklist(builder) cannot reach the worklist tools, so a bulk run "
        "has no durable record and finishes nothing while reporting progress: "
        f"{missing}"
    )
    assert WORKLIST_MARKER in bulk.system_prompt, (
        "the roster half is fixed and the prompt half is open - a definition "
        "holding the worklist tools with no instruction to use them is the "
        "v1.142 hole half-fixed"
    )


def test_with_worklist_does_not_mutate_the_shared_builtin_definition():
    """``get_agent_definition`` hands back the MODULE-LEVEL record. Appending to
    its tools or its prompt in place would rewrite the builder for every later
    run in the process — the shared-mutable trap that made ``specs()``
    permanently rewrite tool schemas (v1.165.0)."""
    base = get_agent_definition(AgentType.BUILDER)
    tools_before = list(base.tools)
    prompt_before = base.system_prompt

    bulk = with_worklist(base)

    assert bulk is not base
    assert set(WORKLIST_TOOL_NAMES) <= set(bulk.tools)   # the copy got them
    assert base.tools == tools_before                    # the shared record did not
    assert base.system_prompt == prompt_before
    assert get_agent_definition(AgentType.BUILDER).tools == tools_before
    assert not (set(WORKLIST_TOOL_NAMES) & set(get_agent_definition(
        AgentType.BUILDER).tools))
    # Idempotent: wrapping the wrap adds nothing and doubles no prompt.
    twice = with_worklist(bulk)
    assert twice.tools == bulk.tools
    assert twice.system_prompt == bulk.system_prompt


# --------------------------------------------------------------------------- #
# 4. Proof the checks above can go red
# --------------------------------------------------------------------------- #
# A coverage test that cannot fail is worse than no test: it reads as proof.
# These mutate the roster IN MEMORY (monkeypatch — never the source, so the
# mutation cannot outlive the test) into the exact shape a shipped release was
# in, and require the helpers to name the missing tool.
def _crippled(base: AgentDefinition, *, drop: str = "", rename: tuple[str, str] = ()):
    tools = []
    for name in base.tools:
        if name == drop:
            continue
        tools.append(rename[1] if rename and name == rename[0] else name)
    return AgentDefinition(
        type=base.type,
        system_prompt=base.system_prompt,
        tools=tools,
        permission_overrides=dict(base.permission_overrides),
    )


def test_acceptance_check_catches_a_tool_dropped_from_the_roster(
    platform, monkeypatch
):
    """The v1.177.2 state, reconstructed: a builder with no ``rename_file``."""
    from iron_jarvis.agents import types as agent_types

    base = get_agent_definition(AgentType.BUILDER)
    monkeypatch.setitem(
        agent_types._DEFINITIONS, AgentType.BUILDER, _crippled(base, drop="rename_file")
    )
    builder = get_agent_definition(AgentType.BUILDER)
    assert "rename_file" not in builder.tools
    assert _missing_reach(platform.registry, builder, _ACCEPTANCE_TOOLS) == [
        "rename_file"
    ]


def test_existence_check_catches_a_tool_that_was_renamed_away(platform, monkeypatch):
    """The other direction: the roster still names a tool, the registry no
    longer serves it under that name. ``specs()`` would drop it in silence."""
    from iron_jarvis.agents import types as agent_types

    base = get_agent_definition(AgentType.BUILDER)
    monkeypatch.setitem(
        agent_types._DEFINITIONS,
        AgentType.BUILDER,
        _crippled(base, rename=("read_document", "read_document_v2")),
    )
    builder = get_agent_definition(AgentType.BUILDER)
    assert _missing_from_registry(platform.registry, builder.tools) == [
        "read_document_v2"
    ]
    # And the sentinels on that same list still do NOT show up as missing.
    assert "custom:*" in builder.tools and "mcp:*" in builder.tools


def test_worklist_check_catches_a_wrap_that_lost_a_tool(platform, monkeypatch):
    """A ``with_worklist`` that stopped appending one of the four is caught by
    name, not by a vague 'bulk runs are broken'."""
    from iron_jarvis.agents import supervisor as supervisor_mod

    real = supervisor_mod.with_worklist

    def lossy(base):
        wrapped = real(base)
        return AgentDefinition(
            type=wrapped.type,
            system_prompt=wrapped.system_prompt,
            tools=[t for t in wrapped.tools if t != "worklist_next"],
            permission_overrides=dict(wrapped.permission_overrides),
        )

    monkeypatch.setattr(supervisor_mod, "with_worklist", lossy)
    bulk = supervisor_mod.with_worklist(get_agent_definition(AgentType.BUILDER))
    assert _missing_reach(platform.registry, bulk, WORKLIST_TOOL_NAMES) == [
        "worklist_next"
    ]
