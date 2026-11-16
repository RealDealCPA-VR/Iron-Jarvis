"""v1.505.0 (north-star 5.5): every child process starts through ``core/procs``
or sits on this file's allow list, each entry with its reason.

The rule in ``CLAUDE.md`` ("Every child process starts through core/procs,
bounded and killed as a tree") was true in spirit and false in letter: a
handful of places start a process themselves, each with its own bound and
tree-kill. Nothing kept a NEW one from appearing unnoticed. This scan is the
rule: a process start under ``src/iron_jarvis`` outside ``core/procs.py`` must
be listed below, by file and the function it sits in, with one line saying how
it is bounded and how it is killed. The test fails on an unlisted site AND on a
listed entry that no longer has one (the list never goes stale).

A "process start" is any reference (a call, or the function handed to
``asyncio.to_thread``) to ``subprocess.Popen / run / call / check_call /
check_output / getoutput / getstatusoutput``, ``asyncio.create_subprocess_*``,
a loop's ``subprocess_exec / subprocess_shell``, ``os.system / popen /
startfile / spawn* / posix_spawn* / exec*``, under any import alias.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis"
PROCS = "core/procs.py"

_SUBPROCESS = {"Popen", "run", "call", "check_call", "check_output", "getoutput", "getstatusoutput"}
_ASYNCIO = {"create_subprocess_exec", "create_subprocess_shell"}
_LOOP_METHODS = {"subprocess_exec", "subprocess_shell"}


def _os_starts(name: str) -> bool:
    return (
        name in {"system", "popen", "startfile"}
        or name.startswith(("spawn", "posix_spawn", "exec"))
    )


def _forbidden(module: str, name: str) -> bool:
    if module == "subprocess":
        return name in _SUBPROCESS
    if module == "asyncio":
        return name in _ASYNCIO
    if module == "os":
        return _os_starts(name)
    return False


#: Folders that are not the app's code, with why (each must still exist).
EXCLUDED_DIRS: dict[str, str] = {
    "harness_bench/tasks/": (
        "benchmark task fixtures: the repo a coding model is handed and its hidden "
        "checks, copied to a throwaway folder and run by harness_bench/grading "
        "through procs.run (bounded, killed as a tree); the daemon never imports them"
    ),
}

#: file (relative to src/iron_jarvis) -> {function qualname: why it may}.
#: Each reason says how the process is BOUNDED and how it is KILLED.
ALLOWED: dict[str, dict[str, str]] = {
    "codehost.py": {
        "popen_detached": (
            "the code host and its launcher must OUTLIVE the daemon (a restart keeps "
            "a coding reply going), so they are kept out of every core/procs Job on "
            "purpose; the launcher exits at once, the host ends itself by its orphan "
            "rules (why_exit: ORPHAN_MAX_S with no daemon attached) and owns its "
            "tools' Jobs, started through core.procs.spawn"
        ),
    },
    "daemon/cli.py": {
        "_source_repo_root": (
            "`ij rollback` finds its checkout without importing the platform: one "
            "`git rev-parse`, timeout=10, no shell; a git that hangs is killed by "
            "subprocess.run's timeout (no grandchildren)"
        ),
        "up": (
            "`ij up`, a developer command never run by the packaged app: `pnpm start` "
            "for the dashboard, lives as long as the command, terminate() at exit "
            "(NOT a tree kill: a node child can outlive it)"
        ),
    },
    "daemon/routes/agents.py": {
        "_open_folder": (
            "a hand-off to the OS file manager (os.startfile / open / xdg-open) the "
            "user pressed for: it returns at once and the window it opens is the "
            "user's own, so it is never waited on and never killed"
        ),
    },
    "daemon/routes/documents.py": {
        "_open_native": (
            "a hand-off to the OS-associated app (os.startfile / open / xdg-open) "
            "the user pressed for: it returns at once and the app is the user's own, "
            "so it is never waited on and never killed"
        ),
    },
    "daemon/routes/editor.py": {
        "_launch_url": (
            "an editor URL handed to the OS protocol handler (os.startfile / open / "
            "xdg-open) the user pressed for: it returns at once and the editor is "
            "the user's own, so it is never waited on and never killed"
        ),
    },
    "iron_proxy/service.py": {
        "IronProxyService._spawn": (
            "Iron-Proxy is a long-lived service with its own life cycle: start "
            "bounded by START_TIMEOUT_S, its pid and create time recorded so a "
            "crash orphan is found again, stopped by _kill_owned through "
            "sandbox.native._kill_tree (its own session/group off Windows)"
        ),
    },
    "mcp/client.py": {
        "StdioTransport._ensure_started._spawn": (
            "a stdio MCP server: born SUSPENDED and put in its own kill-on-close Job "
            "before it runs (the core/procs.spawn shape, done here because the "
            "transport owns the Job handle), every request bounded by its timeout, "
            "torn down by _kill (terminals.backend._tree_kill on its Job / killpg)"
        ),
    },
    "providers/claude_models.py": {
        "_run_handshake": (
            "the model picker's one `claude -p` initialize exchange: "
            "communicate(timeout=), and on a timeout the TREE is killed "
            "(sandbox.native._kill_tree) before the pipes are drained for 5 s"
        ),
    },
    "repl/session.py": {
        "ReplSession._spawn": (
            "the per-session REPL worker when NOT isolated (the isolated one starts "
            "through core.procs.spawn just above): every exchange is bounded by its "
            "timeout, and _kill ends it with procs.kill_tree"
        ),
    },
    "terminals/backend.py": {
        "PosixPtyBackend.start": (
            "os.execvpe runs INSIDE the pty.fork child (it becomes the terminal's "
            "shell); the pane lives as long as the user keeps it, and kill() ends "
            "it with killpg on its own session"
        ),
        "_win_taskkill_tree": (
            "the tree kill itself: `taskkill /T /F /PID`, timeout=10, no window, "
            "the fallback when the terminal's Job could not be made"
        ),
        "PipeBackend.start": (
            "a Build terminal's shell: lives as long as the user keeps the pane, "
            "assigned to its own kill-on-close Job at once (_win_job_for), killed "
            "by _tree_kill (TerminateJobObject, taskkill /T as the fallback)"
        ),
    },
    "tools/builtins.py": {
        "ShellTool.execute": (
            "the PLACEHOLDER shell, shadowed in the running app by "
            "sandbox/shell_tool (core/procs); it runs only in bare test registries: "
            "timeout=60 on a worker thread, NOT a tree kill (shell=True ends cmd.exe only)"
        ),
    },
}


#: Calls that run the function they are HANDED: ``to_thread(subprocess.run,
#: …)`` starts a process as surely as calling it.
_RUNNERS = {"to_thread", "run_in_executor", "partial", "submit", "Thread", "call_soon"}


class _Scan(ast.NodeVisitor):
    """Finds process starts: a CALL of a starter, or a starter handed to a
    runner. A starter named in a type annotation or an ``isinstance`` check
    (``proc: subprocess.Popen``) starts nothing and is not reported."""

    def __init__(self) -> None:
        self.modules: dict[str, str] = {}  # local alias -> module
        self.names: dict[str, tuple[str, str]] = {}  # local name -> (module, attr)
        self.scope: list[str] = []
        self.hits: list[tuple[str, int, str]] = []  # (qualname, line, what)

    def _qual(self) -> str:
        return ".".join(self.scope) or "<module>"

    def _starter(self, node: ast.AST) -> str:
        """``"module.attr"`` when ``node`` names a process starter, else ""."""
        if isinstance(node, ast.Attribute):
            base = node.value
            if isinstance(base, ast.Name) and base.id in self.modules:
                module = self.modules[base.id]
                if _forbidden(module, node.attr):
                    return f"{module}.{node.attr}"
            elif node.attr in _LOOP_METHODS:
                return f"<loop>.{node.attr}"
        elif isinstance(node, ast.Name) and node.id in self.names:
            module, attr = self.names[node.id]
            return f"{module}.{attr}"
        return ""

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            if a.name in ("subprocess", "os", "asyncio"):
                self.modules[a.asname or a.name] = a.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module in ("subprocess", "os", "asyncio") and node.level == 0:
            for a in node.names:
                if _forbidden(node.module, a.name):
                    self.names[a.asname or a.name] = (node.module, a.name)

    def _scoped(self, node, name: str) -> None:
        self.scope.append(name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._scoped(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._scoped(node, node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scoped(node, node.name)

    def visit_Call(self, node: ast.Call) -> None:
        what = self._starter(node.func)
        if what:
            self.hits.append((self._qual(), node.lineno, what))
        func = node.func
        runner = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if runner in _RUNNERS:
            for arg in [*node.args, *(k.value for k in node.keywords)]:
                handed = self._starter(arg)
                if handed:
                    self.hits.append((self._qual(), node.lineno, handed))
        self.generic_visit(node)


def _sites() -> dict[str, dict[str, list[str]]]:
    """file -> {qualname: ["line: what", ...]} for every process start."""
    found: dict[str, dict[str, list[str]]] = {}
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel == PROCS or rel.startswith(tuple(EXCLUDED_DIRS)):
            continue
        # CI's Windows runners check out with CRLF: normalise at the reader.
        source = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
        scan = _Scan()
        scan.visit(ast.parse(source, filename=rel))
        for qual, line, what in scan.hits:
            found.setdefault(rel, {}).setdefault(qual, []).append(f"{line}: {what}")
    return found


def test_every_child_process_starts_through_procs_or_is_listed():
    found = _sites()
    unlisted = [
        f"{rel} :: {qual} ({', '.join(lines)})"
        for rel, quals in found.items()
        for qual, lines in quals.items()
        if qual not in ALLOWED.get(rel, {})
    ]
    assert not unlisted, (
        "A child process starts outside core/procs. Start it through core/procs "
        "(bounded, killed as a tree), or add it to ALLOWED with how it is bounded "
        "and killed:\n  " + "\n  ".join(unlisted)
    )


def test_no_allow_list_entry_is_stale():
    found = _sites()
    stale = [
        f"{rel} :: {qual}"
        for rel, quals in ALLOWED.items()
        for qual in quals
        if qual not in found.get(rel, {})
    ]
    stale += [f"{folder} (folder gone)" for folder in EXCLUDED_DIRS if not (SRC / folder).is_dir()]
    assert not stale, "These ALLOWED entries start no process any more; remove them:\n  " + "\n  ".join(stale)


def test_every_allow_list_entry_says_how_it_is_bounded_and_killed():
    thin = [
        f"{rel} :: {qual}"
        for rel, quals in ALLOWED.items()
        for qual, why in quals.items()
        if len(" ".join(str(why).split())) < 25
    ]
    assert not thin, "An ALLOWED entry needs its reason (bounded how, killed how):\n  " + "\n  ".join(thin)


def test_the_scan_sees_every_shape_of_a_process_start():
    """The scan itself: each shape is caught, under an alias too, and the
    harmless names beside them (``subprocess.PIPE``, ``os.path``) are not."""
    source = "\n".join([
        "import subprocess, os, asyncio",
        "import subprocess as sp",
        "from subprocess import Popen as P",
        "from os import startfile",
        "def a(): subprocess.run(['x'])",
        "def b(): sp.Popen(['x'], stdout=subprocess.PIPE)",
        "def c(): P(['x'])",
        "async def d(): await asyncio.create_subprocess_exec('x')",
        "async def e(): await asyncio.to_thread(subprocess.check_output, ['x'])",
        "def f(): os.system('x'); os.startfile('x'); os.execv('x', []); os.spawnl(0, 'x')",
        "def g(): startfile('x')",
        "async def h(loop): await loop.subprocess_exec(object, 'x')",
        "def ok(): os.path.join('a'); subprocess.TimeoutExpired; os.environ.get('x')",
        "def ok2(p: subprocess.Popen) -> P: return isinstance(p, sp.Popen)",
        "class K:",
        "    proc: subprocess.Popen | None = None",
        "    def m(self): self.proc = subprocess.Popen(['x'])",
    ])
    scan = _Scan()
    scan.visit(ast.parse(source))
    quals = {q for q, _line, _what in scan.hits}
    assert quals == {"a", "b", "c", "d", "e", "f", "g", "h", "K.m"}, quals
    whats = [w for q, _line, w in scan.hits if q == "f"]
    assert whats == ["os.system", "os.startfile", "os.execv", "os.spawnl"], whats
