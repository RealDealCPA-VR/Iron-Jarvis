"""Third fix plan, group 17 — the callers of the tool gate (PG2's cause), and
three leftovers (PG3's words, PG5's command line, the night commit's seam).

1. EVERY CALLER OF THE TOOL GATE HONOURS THE TOOL'S OWN CHECK. A tool can ask
   about one call every time (``Tool.spend_check`` -> ``SpendCheck(ask=True)``,
   read through ``tools/base.spend_checks_for``, which adds the guard for
   writes into Git's and Iron Jarvis's own files). ``ToolRegistry.invoke``
   does not read it: each CALLER must. Finding PG2 was a caller that did not
   (the workflow engine's tool step). The scan below finds every
   ``<registry>.invoke(`` in ``src/iron_jarvis`` and holds a table that says,
   for each one, how the check is honoured:

   * ``READS``: the call's own function (or the lane function named in the
     table, which the call's function really calls) reads the tool's check;
   * ``CANNOT_REACH``: the call can only reach a fixed set of tools, proved
     here from the source AND against the real registry (``build_platform``):
     none of them has a check of its own, and none is a file edit the guard
     reads;
   * ``REFUSAL_ONLY``: the call passes ``deny_reason`` (it records a refusal;
     the tool never runs).

   A new call, or a changed count, fails with a sentence telling the author
   what to decide.

2. The low-trust words name the code tools (``core/trust.CODE_DENY``).
3. ``ironjarvis tools`` shows a stored "ask" for web search / web fetch as
   Settings shows it (``settings/writer.current``): allow.
4. ``harness/night_commit`` has public names for its screen
   (``judge_changes``, ``parse_diff_raw``), which "Try on…" calls too.
"""

from __future__ import annotations

import ast
import inspect
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

from iron_jarvis.tools.base import GUARDED_EDIT_TOOLS, Tool

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "iron_jarvis"

READS = "reads the tool's own check"
CANNOT_REACH = "cannot reach a tool with a check"
REFUSAL_ONLY = "refusal only"

#: The names a caller's code uses to read a tool's own check.
_CHECK_READERS = frozenset({"spend_checks_for", "_spend_checks_for", "spend_check"})


def _source(rel: str) -> str:
    # A source pin normalises CRLF (CI's Windows runners check out with it).
    return (SRC / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


def _tree(rel: str) -> ast.Module:
    return ast.parse(_source(rel), filename=rel)


def _functions(tree: ast.Module) -> "dict[str, ast.AST]":
    """Every function (and class) by its dotted name inside the module."""
    out: dict[str, ast.AST] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                out[name] = child
                walk(child, name + ".")
            else:
                walk(child, prefix)

    walk(tree, "")
    return out


def _invoke_calls(tree: ast.Module) -> "list[tuple[str, ast.Call, str]]":
    """``(enclosing function, call, receiver)`` of every ``X.invoke(...)``."""
    found: list[tuple[str, ast.Call, str]] = []
    stack: list[str] = []

    class V(ast.NodeVisitor):
        def _scope(self, node):
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scope

        def visit_Call(self, node):
            if isinstance(node.func, ast.Attribute) and node.func.attr == "invoke":
                found.append((".".join(stack), node, ast.unparse(node.func.value)))
            self.generic_visit(node)

    V().visit(tree)
    return found


def _is_registry(receiver: str) -> bool:
    """A tool registry by the name every caller gives it (``registry``,
    ``self._registry``, ``d.platform.registry``...)."""
    return receiver.split(".")[-1].lstrip("_") == "registry"


def _reads_check(node: ast.AST) -> bool:
    """The function CALLS the check reader: ``spend_checks_for(...)`` (or its
    import alias), ``<tool>.spend_check(...)``, or ``getattr(tool,
    "spend_check", ...)`` (then calls what it got). An import alone, a
    comment or a docstring does not count."""
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Name) and f.id in _CHECK_READERS:
            return True
        if isinstance(f, ast.Attribute) and f.attr in _CHECK_READERS:
            return True
        if (
            isinstance(f, ast.Name) and f.id == "getattr" and len(n.args) >= 2
            and isinstance(n.args[1], ast.Constant) and n.args[1].value in _CHECK_READERS
        ):
            return True
    return False


def _calls(node: ast.AST, name: str) -> bool:
    """The function calls ``name`` (a plain name or ``self.<name>``)."""
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if (isinstance(f, ast.Name) and f.id == name) or (isinstance(f, ast.Attribute) and f.attr == name):
                return True
    return False


@dataclass(frozen=True)
class Site:
    #: How each call in this function honours the check, in source order.
    hows: tuple[str, ...]
    #: READS: the functions (same file) that must read the check.
    lanes: tuple[str, ...] = ()
    #: READS: ``(function, name)`` — that function must call ``name`` (the
    #: lane above, when it is a function of its own).
    must_call: tuple[tuple[str, str], ...] = ()
    #: CANNOT_REACH: the reason, in one line.
    why: str = ""


#: THE TABLE. Keyed by (file under src/iron_jarvis, enclosing function).
SITES: dict[tuple[str, str], Site] = {
    # The agent runtime: one spend map per step, read by its pause and by
    # the deny path of every call of the step.
    ("agents/runtime.py", "AgentRuntime._perceive_act_body._invoke"): Site(
        (READS,), lanes=("AgentRuntime._perceive_act_body",),
    ),
    # The POST chat lane: no card, a check that asks is refused.
    ("daemon/chat_turn.py", "run_chat_turn._registry_invoke"): Site(
        (READS,), lanes=("run_chat_turn",),
    ),
    # The stream chat lane: a check that asks shows its card.
    ("daemon/routes/chat.py", "chat_stream.gen._registry_invoke"): Site(
        (READS,), lanes=("chat_stream.gen",),
    ),
    # A folder's plugin check: the carrier's own check is read first
    # (`asks_first`); a command that would ask is not run, the model is told
    # to run it with its own command tool. Its follow-up calls
    # (process_read / process_stop) have no check (proved below).
    ("daemon/chat_turn.py", "harness_check_runner.invoke"): Site(
        (READS,), lanes=("harness_check_runner.asks_first",),
        must_call=(("harness_check_runner.run", "asks_first"),),
    ),
    # The finish check's and the project checkers' follow-up calls.
    ("daemon/chat_turn.py", "finish_invoke.invoke"): Site(
        (CANNOT_REACH,),
        why="only process_read / process_stop of a test run already started (harness/finish, harness/checkers)",
    ),
    # The documents batch button.
    ("daemon/routes/documents.py", "register.documents_batch_run"): Site(
        (CANNOT_REACH,), why="the one tool name batch_documents, written in the call",
    ),
    # The MCP server for coding tools: the capability families only.
    ("mcpserver/server.py", "_call_tool"): Site(
        (CANNOT_REACH,),
        why="only the names mcpserver.CAPABILITY_TOOLS admits (Browser, Memory's readers, Hand off)",
    ),
    # A batch of file changes: each step tool's own check, and the guard for
    # Git's and Iron Jarvis's own files (`guarded_target`), refuse the batch.
    ("tools/apply_changes.py", "ApplyChangesTool.execute"): Site(
        (READS,), lanes=("ApplyChangesTool._step_problem",),
        must_call=(("ApplyChangesTool.execute", "_step_problem"),),
    ),
    # A tool script: each call's own check refuses it (call it directly);
    # only readers are reachable, so the file-edit guard never applies.
    ("tools/tool_script.py", "ToolScriptTool.execute.on_call"): Site(
        (READS,), lanes=("ToolScriptTool._refusal",),
        must_call=(("ToolScriptTool.execute.on_call", "_refusal"),),
    ),
    # A workflow's tool step (PG2): the refusal is recorded through the deny
    # seam, then the real call runs only when the check did not ask.
    ("workflows/engine.py", "WorkflowEngine._run_tool_step"): Site(
        (REFUSAL_ONLY, READS), lanes=("WorkflowEngine._own_check_refusal",),
        must_call=(("WorkflowEngine._run_tool_step", "_own_check_refusal"),),
    ),
}

#: ``X.invoke(`` calls that are NOT a tool registry: (file, function) -> why.
NOT_A_REGISTRY: dict[tuple[str, str], str] = {}


def _all_invoke_sites() -> "dict[tuple[str, str], list[tuple[ast.Call, str]]]":
    out: dict[tuple[str, str], list[tuple[ast.Call, str]]] = {}
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        text = _source(rel)
        if ".invoke" not in text:
            continue
        for fn, call, receiver in _invoke_calls(ast.parse(text, filename=rel)):
            out.setdefault((rel, fn), []).append((call, receiver))
    return out


_DECIDE = (
    "Decide how this call honours a tool's OWN check (Tool.spend_check, read "
    "through tools/base.spend_checks_for, which also adds the guard for writes "
    "into Git's and Iron Jarvis's own files) — ToolRegistry.invoke does not "
    "read it — and add the call to SITES in tests/test_third_plan_g17_gate_"
    "callers.py: READS (its function or lane reads the check and refuses or "
    "asks), CANNOT_REACH (a fixed set of tool names, none with a check: prove "
    "it in the registry test) or REFUSAL_ONLY (it passes deny_reason)."
)


def test_every_registry_invoke_is_in_the_table_and_no_entry_is_stale():
    found = _all_invoke_sites()
    registry_sites: dict[tuple[str, str], int] = {}
    for key, calls in found.items():
        for _call, receiver in calls:
            if _is_registry(receiver):
                registry_sites[key] = registry_sites.get(key, 0) + 1
            else:
                assert key in NOT_A_REGISTRY, (
                    f"{key[0]} :: {key[1]} calls {receiver}.invoke(...). If that is a tool "
                    "registry, name it `registry` (or add it to SITES); if it is not, add it "
                    "to NOT_A_REGISTRY with the reason."
                )
    for key, count in registry_sites.items():
        assert key in SITES, f"A NEW call of the tool gate: {key[0]} :: {key[1]}. {_DECIDE}"
        assert len(SITES[key].hows) == count, (
            f"{key[0]} :: {key[1]} now calls the tool gate {count} time(s), the table "
            f"says {len(SITES[key].hows)}. {_DECIDE}"
        )
    stale = sorted(set(SITES) - set(registry_sites))
    assert stale == [], (
        f"SITES names calls that are gone (or moved to another function): {stale}. "
        "Update the table; a call that moved must be decided again."
    )
    # The scan really sees the calls (a parser that finds nothing passes the
    # loop above vacuously).
    assert sum(registry_sites.values()) == 11


@pytest.mark.parametrize("key", sorted(SITES), ids=lambda k: f"{k[0]}::{k[1]}")
def test_each_call_honours_the_check_the_way_the_table_says(key):
    rel, fn = key
    site = SITES[key]
    tree = _tree(rel)
    funcs = _functions(tree)
    calls = [c for f, c, r in _invoke_calls(tree) if f == fn and _is_registry(r)]
    assert len(calls) == len(site.hows)
    for call, how in zip(sorted(calls, key=lambda c: (c.lineno, c.col_offset)), site.hows):
        kws = {k.arg for k in call.keywords if k.arg}
        if how == REFUSAL_ONLY:
            assert "deny_reason" in kws, (
                f"{rel}:{call.lineno} is listed as REFUSAL_ONLY but passes no deny_reason: "
                "it can run the tool."
            )
        elif how == READS:
            assert site.lanes, f"{key}: a READS entry names the function that reads the check"
            for lane in site.lanes:
                assert lane in funcs, f"{rel} has no function {lane} (the table is stale)"
                assert _reads_check(funcs[lane]), (
                    f"{rel} :: {lane} no longer reads the tool's own check "
                    "(spend_checks_for / spend_check), yet its call of the tool gate "
                    f"at line {call.lineno} relies on it. {_DECIDE}"
                )
            for caller, name in site.must_call:
                assert caller in funcs, f"{rel} has no function {caller} (the table is stale)"
                assert _calls(funcs[caller], name), (
                    f"{rel} :: {caller} no longer calls {name}, the lane that reads the "
                    f"tool's own check before the call at line {call.lineno}. {_DECIDE}"
                )
        else:
            assert how == CANNOT_REACH and site.why, key


# --------------------------------------------------------------------------- #
# CANNOT_REACH, proved: the names each call can reach, from the source, then
# against the REAL registry.
# --------------------------------------------------------------------------- #


def _literal_first_args(rel: str, func_name: str, within: "str | None" = None) -> "list[object]":
    """The first argument of every call of the plain name ``func_name`` in
    ``rel`` (inside function ``within`` when given): a literal, or the
    unparsed text of anything that is not one."""
    tree = _tree(rel)
    root = _functions(tree)[within] if within else tree
    out: list[object] = []
    for n in ast.walk(root):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == func_name:
            arg = n.args[0] if n.args else None
            out.append(arg.value if isinstance(arg, ast.Constant) else ast.unparse(arg) if arg else None)
    return out


def _can_ask(registry, name: str) -> str:
    """Why a call of ``name`` could meet a check that asks, or ""."""
    tool = registry.get(name)
    if tool is None:
        return ""
    if type(tool).spend_check is not Tool.spend_check:
        return f"{name} has a check of its own ({type(tool).__name__}.spend_check)"
    if name in GUARDED_EDIT_TOOLS:
        return f"{name} is a file edit the guard for Git's and Iron Jarvis's own files reads"
    return ""


def test_finish_invoke_reaches_only_process_read_and_process_stop(platform):
    reached = {"process_read", "process_stop"}
    # Where its answer goes: only as `invoke=` (harness/finish) or `stop=`
    # (harness/checkers), in both chat lanes.
    seen: list[str] = []
    total = 0
    for rel in ("daemon/chat_turn.py", "daemon/routes/chat.py"):
        for n in ast.walk(_tree(rel)):
            if isinstance(n, ast.Call):
                f = n.func
                if (isinstance(f, ast.Name) and f.id == "finish_invoke") or (
                    isinstance(f, ast.Attribute) and f.attr == "finish_invoke"
                ):
                    total += 1
            if isinstance(n, ast.keyword) and isinstance(n.value, ast.Call):
                f = n.value.func
                if (isinstance(f, ast.Name) and f.id == "finish_invoke") or (
                    isinstance(f, ast.Attribute) and f.attr == "finish_invoke"
                ):
                    seen.append(n.arg)
    assert total == len(seen) == 4 and set(seen) == {"invoke", "stop"}, (seen, total)
    # What those two do with it: literal tool names only.
    names = _literal_first_args("harness/finish.py", "invoke") + _literal_first_args("harness/checkers.py", "stop")
    assert names and set(names) <= reached, names
    for name in reached:
        assert platform.registry.get(name) is not None, name
        assert _can_ask(platform.registry, name) == ""


def test_the_documents_batch_reaches_only_batch_documents(platform):
    (call,) = [c for f, c, r in _invoke_calls(_tree("daemon/routes/documents.py")) if f == "register.documents_batch_run"]
    assert isinstance(call.args[0], ast.Constant) and call.args[0].value == "batch_documents"
    assert platform.registry.get("batch_documents") is not None
    assert _can_ask(platform.registry, "batch_documents") == ""


def test_the_mcp_server_reaches_only_capability_tools_and_none_has_a_check(platform):
    from iron_jarvis.mcpserver.server import CAPABILITY_TOOLS

    (call,) = [c for f, c, r in _invoke_calls(_tree("mcpserver/server.py")) if f == "_call_tool"]
    kw = {k.arg: ast.unparse(k.value) for k in call.keywords if k.arg}
    # The gate is told the pane's names (`allowed`, from permitted_tool_names).
    assert kw.get("allowed_names") == "allowed"
    fn = _functions(_tree("mcpserver/server.py"))["_call_tool"]
    assert _calls(fn, "permitted_tool_names")
    reachable = sorted(
        n for n in platform.registry.names() if any(c.admits(n) for c in CAPABILITY_TOOLS.values())
    )
    assert any(n.startswith("browser_") for n in reachable) and "memory_search" in reachable
    asks = [why for n in reachable if (why := _can_ask(platform.registry, n))]
    assert asks == [], (
        "A tool the MCP server serves has a check of its own, and mcpserver/server._call_tool "
        f"does not read checks: {asks}. Read it there (spend_checks_for) before serving it."
    )


def test_the_plugin_check_runner_reads_the_carriers_check_and_its_follow_ups_have_none(platform):
    names = _literal_first_args("daemon/chat_turn.py", "invoke", within="harness_check_runner")
    assert set(names) == {"shell", "process_start", "process_read", "process_stop"}, names
    # The two that START a command have a check (so `asks_first` matters),
    # the two that follow one up have none.
    assert _can_ask(platform.registry, "shell") and _can_ask(platform.registry, "process_start")
    assert _can_ask(platform.registry, "process_read") == ""
    assert _can_ask(platform.registry, "process_stop") == ""
    # `asks_first` is asked about the carrier, before either way of running.
    run = _functions(_tree("daemon/chat_turn.py"))["harness_check_runner.run"]
    order = [n.func.id for n in ast.walk(run) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert order.index("asks_first") < order.index("via_shell")
    assert order.index("asks_first") < order.index("via_process")


def test_a_tool_script_cannot_reach_a_file_edit_the_guard_reads(platform):
    # tool_script reads each tool's OWN check (`_refusal`), but the guard for
    # Git's and Iron Jarvis's own files lives outside the tools: it holds only
    # because no file edit can be called from a script.
    from iron_jarvis.tools.tool_script import NAME

    script = platform.registry.get(NAME)
    assert script is not None
    reachable = [n for n in GUARDED_EDIT_TOOLS if platform.registry.get(n) is not None]
    assert reachable
    for name in reachable:
        assert script._fixed_refusal(name, platform.registry.get(name)), name  # noqa: SLF001


def test_a_batch_reads_the_guard_for_git_and_iron_jarvis_files_itself():
    # (handed to ``asyncio.to_thread``: it resolves paths.)
    lane = _functions(_tree("tools/apply_changes.py"))["ApplyChangesTool._step_problem"]
    assert any(isinstance(n, ast.Name) and n.id == "guarded_target" for n in ast.walk(lane))


# --------------------------------------------------------------------------- #
# 2. The low-trust words name the code tools (PG3 leftover).
# --------------------------------------------------------------------------- #


def test_the_low_trust_words_name_running_code():
    from iron_jarvis.core.trust import CODE_DENY

    src = (ROOT / "dashboard" / "lib" / "trustWords.ts").read_text(encoding="utf-8").replace("\r\n", "\n")
    block = src[src.index("export const LOW_TRUST_FAMILIES") : src.index("export const LOW_TRUST_CANNOT")]
    families = {
        m.group(1): set(re.findall(r'"([a-z_]+)"', m.group(2)))
        for m in re.finditer(r'words:\s*"([^"]+)",\s*tools:\s*\[([^\]]*)\]', block)
    }
    holding = [w for w, tools in families.items() if set(CODE_DENY) <= tools]
    assert holding, f"no family in lib/trustWords.ts holds {sorted(CODE_DENY)}"
    start = src.index("export const LOW_TRUST_CANNOT")
    sentence = "".join(re.findall(r'"([^"]*)"', src[start : src.index(";", start)]))
    assert "can't run code it writes" in sentence


# --------------------------------------------------------------------------- #
# 3. `ironjarvis tools` shows a stored "ask" for the web readers as allow.
# --------------------------------------------------------------------------- #


def test_the_command_line_shows_web_search_and_fetch_as_they_behave(platform, project_root, monkeypatch):
    from typer.testing import CliRunner

    from iron_jarvis.daemon import cli

    perms = platform.config.permissions
    perms["web_search"] = "ask"   # stored by an older version
    perms["web_fetch"] = "ask"
    perms["shell"] = "ask"        # control: an ordinary ask stays ask
    monkeypatch.setattr(cli, "build_platform", lambda root: platform)
    monkeypatch.setenv("COLUMNS", "220")
    result = CliRunner().invoke(cli.app, ["tools", "--root", project_root])
    assert result.exit_code == 0, result.output

    def mode(name: str) -> str:
        for line in result.output.splitlines():
            cells = [c.strip() for c in re.split(r"[│|]", line)]
            if name in cells:
                return cells[cells.index(name) + 1]
        raise AssertionError(f"{name} not in the table:\n{result.output}")

    assert mode("web_search") == "allow"
    assert mode("web_fetch") == "allow"
    assert mode("shell") == "ask"


def test_the_command_line_reads_the_writers_rule_not_a_copy():
    tools_cmd = _functions(_tree("daemon/cli.py"))["tools"]
    text = ast.unparse(tools_cmd)
    assert "WEB_READER_TOOLS" not in text and "web_search" not in text
    assert _calls(_functions(_tree("daemon/cli.py"))["shown_tool_mode"], "current")


# --------------------------------------------------------------------------- #
# 4. The night commit's screen has public names ("Try on…" calls them too).
# --------------------------------------------------------------------------- #


def test_the_night_commit_screen_has_public_names(tmp_path):
    from iron_jarvis.harness import night_commit as N

    for public in ("judge_changes", "parse_diff_raw"):
        fn = getattr(N, public)
        assert public in N.__all__
        doc = inspect.getdoc(fn) or ""
        assert "night commit" in doc and "Try on" in doc, public
    raw = ":000000 100644 0000000 1111111 A\0notes.txt\0:100644 100644 2222222 3333333 M\0.env\0"
    assert N.parse_diff_raw(raw) == [("A", "100644", "notes.txt"), ("M", "100644", ".env")]
    # The private names stay, as thin calls of the public ones.
    assert N._parse_raw(raw) == N.parse_diff_raw(raw)  # noqa: SLF001
    (tmp_path / ".env").write_text("x", encoding="utf-8")
    entries = [("A", "100644", ".env")]
    assert N.judge_changes(tmp_path, entries, "HEAD") == {".env": N.SECRET}
    assert N._judge(tmp_path, entries, "HEAD") == N.judge_changes(tmp_path, entries, "HEAD")  # noqa: SLF001
