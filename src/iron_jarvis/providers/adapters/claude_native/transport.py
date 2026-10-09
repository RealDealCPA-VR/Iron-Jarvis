"""One Claude CLI process per request, spoken over stream-json (v1.300.0).

Their ``Client._run`` (NousResearch claude-subscription-directsdk, MIT, commit
ef73726, ``directsdk.py``) adapted to asyncio and to Iron Jarvis's rules:

* PER REQUEST, a private temp dir holds ``tools.json`` (the inert MCP
  manifest), ``settings.json`` (``{"env": {"CLAUDE_CODE_EXTRA_BODY": ...}}`` —
  full tool schemas never touch argv or the environment block, so no OS
  length limit applies), ``system.md`` and ``mcp.json``. The CLI runs in a
  STABLE private cwd: native writes its cwd into the prompt, and a fresh one
  per call moved the cached prefix on every tool round.
* THE CHILD ENV is ``os.environ`` copied, never the app's vault, through the
  shared ``claude_models.child_env``: an inherited API key / auth token / base
  URL / Bedrock-Vertex-Foundry switch would bill the API (or another cloud)
  instead of the subscription, silently — so it is STRIPPED, and the NAMES
  (never the values) are logged once per process.
* THE ADMISSION RELAY (``admission.Admission``) is the CLI's
  ``ANTHROPIC_BASE_URL``: exactly one upstream Messages request per call, its
  streamed answer captured before native recovery can replace it.
* FRAMES are written one by one; every historical user frame is
  ``shouldQuery: false`` and its zero-turn acknowledgment is awaited.
* STDOUT is read on a thread into an asyncio queue; a READ-IDLE timeout
  (default 180 s, reset by every event) plus the adapter's overall cap.
* STOP IS A KILL. A ``CancelledError`` at any await — or a consumer that walks
  away from the stream — kills the process TREE (``sandbox/native._kill_tree``,
  the v1.287.0 discipline), closes the relay and re-raises.

Nothing blocking runs on the loop: file writes, the relay's socket, the spawn,
stdin writes, the kill and the relay's shutdown all hop to a worker thread.

IRON-PROXY ACCOUNTS (v1.301.0): a call may carry an account (``account_env`` —
the vendor-CLI home Iron-Proxy picked, applied AFTER the login-env guard so the
home survives it). Such a call never marks the whole provider signed out on the
shared probe (that ACCOUNT is parked instead), and ``run`` fills an
:class:`Attempt` the adapter reads to report a limit to Iron-Proxy. A call with
no account is byte-identical to v1.300.0.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from ...cli_auth import (
    DEFAULT_PROBE,
    SIGN_IN_FIX,
    cli_failure_message,
    is_sign_in_refusal,
    note_cli_failure,
)
from ..base import TRANSIENT_STATUS, LLMResponse, ProviderError, ToolCall, parse_retry_after
from . import frames as F
from .admission import CONSUMED_MESSAGE, Admission
from .inert_mcp import inert_mcp_command

PROVIDER = "claude-cli"
BINARY = "claude"
DEFAULT_IDLE_TIMEOUT_S = 180.0
DEFAULT_UPSTREAM = "https://api.anthropic.com"

#: Switches that would move the CLI to another cloud backend (stripped, with
#: every ``ANTHROPIC_*`` variable, by the shared login env).
BACKEND_SWITCHES = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
_FALSY = ("", "0", "false", "no", "off")

#: Variables an inherited environment may carry that would override what this
#: request decides (the effort flag, the extra body).
_POPPED = ("CLAUDE_CODE_EXTRA_BODY", "CLAUDE_CODE_EFFORT_LEVEL")
#: Their fixed settings: no tool search, no telemetry, NO native retries (the
#: relay owns the one request), no native compaction (the app owns history),
#: and no token-budget reminder (replaying it moved the cached prefix).
_FIXED_ENV = {
    "ENABLE_TOOL_SEARCH": "false",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "CLAUDE_CODE_MAX_RETRIES": "0",
    "DISABLE_AUTO_COMPACT": "1",
    "DISABLE_COMPACT": "1",
    "CLAUDE_CODE_TOTAL_TOKENS_REMINDER": "off",
}

#: Error text seen when a natively streamed answer and its final message
#: disagree — the stream already reached the user, so this is never papered over.
STREAM_MISMATCH = "claude-cli: the CLI's final text differs from what it streamed"

#: The sentence for a relay request that died before ANY answer arrived (no
#: HTTP status, or a 200 cut off before a word reached the user). Native's own
#: retries are off (CLAUDE_CODE_MAX_RETRIES=0, and the relay refuses them), so
#: this is TRANSIENT: the router retries / fails over exactly like any other
#: cloud provider's dropped connection.
DROPPED = "claude-cli: the connection to Anthropic dropped before an answer arrived — try again"

#: Anthropic's words for a credential that no longer works. A 403 is only
#: "signed out" when it reads like this (or is typed authentication_error);
#: any other 403 (an organisation/permission refusal) keeps its own words.
_AUTH_WORDS = re.compile(
    r"oauth token|token (?:has )?(?:been )?(?:expired|revoked)|invalid (?:x-api-key|api key|bearer)"
    r"|authentication",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# Environment
# --------------------------------------------------------------------------- #
log = logging.getLogger("ironjarvis.claude_native")
_STRIP_LOGGED = False
_STRIP_LOCK = threading.Lock()


def overrides(env: dict[str, str]) -> list[str]:
    """The NAMES of routing overrides set in ``env`` (never their values):
    any non-empty ``ANTHROPIC_*`` and a truthy backend switch."""
    found = sorted(k for k, v in env.items() if k.startswith("ANTHROPIC_") and v)
    found += [k for k in BACKEND_SWITCHES if str(env.get(k, "")).strip().lower() not in _FALSY]
    return found


def _note_stripped(names: list[str]) -> None:
    """ONE INFO line per process, the first time stripping changed anything —
    names only, never values."""
    global _STRIP_LOGGED
    if not names:
        return
    with _STRIP_LOCK:
        if _STRIP_LOGGED:
            return
        _STRIP_LOGGED = True
    log.info(
        "claude-cli: ignoring %s from the environment — the Claude subscription is used",
        ", ".join(names),
    )


def child_env(
    base: dict[str, str] | None = None, *, max_output_tokens: int | None = None
) -> dict[str, str]:
    """The CLI's environment: the user's own login, never an override.

    claude-cli IS "use my subscription", so a stray ``ANTHROPIC_API_KEY`` /
    auth token / base URL / Bedrock-Vertex-Foundry switch in the inherited
    environment is STRIPPED (not obeyed, not refused): it would otherwise bill
    the API or another cloud, silently. Built in two layers:
    (1) ``providers.claude_models.child_env`` — the ONE shared login env (drops
    every ``ANTHROPIC_*`` and the backend switches), the stripped NAMES logged
    once per process; (2) this request's pops and fixed settings on top. The
    relay's ``ANTHROPIC_BASE_URL`` is added by the transport afterwards."""
    from ...claude_models import child_env as login_env

    source = dict(os.environ if base is None else base)
    env = login_env(source)
    _note_stripped([k for k in overrides(source) if k not in env])
    for key in _POPPED:
        env.pop(key, None)
    env.update(_FIXED_ENV)
    if max_output_tokens:
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(int(max_output_tokens))
    return env


_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")


def loopback_unproxied(env: dict[str, str]) -> dict[str, str]:
    """The relay is a plain-HTTP LOOPBACK URL: when the environment names a
    proxy, the CLI must not send its one request through it (a proxy cannot
    reach this machine's 127.0.0.1). ``127.0.0.1`` and ``localhost`` are added
    to ``NO_PROXY`` (kept: whatever was there). The UPSTREAM leg honours the
    proxy in the relay itself (``admission.proxy_for``). Mutates and returns
    ``env``; untouched when no proxy is set."""
    if not any(str(v).strip() for k, v in env.items() if k.upper() in _PROXY_VARS):
        return env
    keys = [k for k in env if k.upper() == "NO_PROXY"] or ["NO_PROXY"]
    for key in keys:
        have = [h.strip() for h in env.get(key, "").split(",") if h.strip()]
        have += [h for h in ("127.0.0.1", "localhost") if h not in have]
        env[key] = ",".join(have)
    return env


def upstream_of(base: dict[str, str] | None) -> str:
    """Where the relay forwards: an explicitly injected fixture's base URL
    (a test's loopback upstream), else Anthropic — an INHERITED base URL is
    stripped, never followed."""
    if base is not None and base.get("ANTHROPIC_BASE_URL"):
        return base["ANTHROPIC_BASE_URL"]
    return DEFAULT_UPSTREAM


# --------------------------------------------------------------------------- #
# The stable working directory
# --------------------------------------------------------------------------- #
_WORKDIR_LOCK = threading.Lock()
_FALLBACK_WORKDIR: str | None = None


def _private_dir(path: str | Path) -> bool:
    """Our own real directory, closed to others (POSIX); on Windows a real
    directory that is not a junction/symlink (the per-user temp is ACL'd)."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISDIR(info.st_mode):
        return False
    if hasattr(os, "getuid"):
        return info.st_uid == os.getuid() and not info.st_mode & 0o077  # type: ignore[attr-defined]
    isjunction = getattr(os.path, "isjunction", None)
    return not (isjunction is not None and isjunction(path))


def _shared_candidate() -> Path | None:
    base = Path(tempfile.gettempdir())
    if os.name == "nt":
        return base / "ironjarvis-claude-cwd"
    if not hasattr(os, "getuid"):
        return None
    try:
        parent = base.stat().st_mode
    except OSError:
        return None
    if parent & 0o022 and not parent & stat.S_ISVTX:
        return None  # others could rename our directory out from under us
    return base / f"ironjarvis-claude-cwd-{os.getuid()}"  # type: ignore[attr-defined]


def stable_workdir() -> str:
    """ONE cwd for every request of this OS user (their ``shared_workdir``),
    else one private dir per process, re-made at the same path after a prune.
    Native embeds its cwd in the request, so a moving cwd is a cache miss."""
    global _FALLBACK_WORKDIR
    with _WORKDIR_LOCK:
        shared = _shared_candidate()
        if shared is not None:
            try:
                shared.mkdir(mode=0o700, exist_ok=True)
                if _private_dir(shared):
                    os.utime(shared)
                    return str(shared)
            except OSError:
                pass
        if _FALLBACK_WORKDIR is not None:
            try:
                os.makedirs(_FALLBACK_WORKDIR, mode=0o700, exist_ok=True)
                if _private_dir(_FALLBACK_WORKDIR):
                    os.utime(_FALLBACK_WORKDIR)
                    return _FALLBACK_WORKDIR
            except OSError:
                pass
        _FALLBACK_WORKDIR = tempfile.mkdtemp(prefix="ironjarvis-claude-cwd-")
        return _FALLBACK_WORKDIR


# --------------------------------------------------------------------------- #
# The request
# --------------------------------------------------------------------------- #
@dataclass
class NativeCall:
    """Everything one CLI invocation needs, decided before any I/O."""

    exe: str
    model_arg: str | None
    system: str
    frames: list[dict]
    manifest: list[dict]
    body: str
    names: F.ToolNames
    effort: str = ""
    budget_usd: float = 0.0
    idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_S
    total_timeout_s: float = 240.0
    max_output_tokens: int | None = None
    #: The env to start from (tests); None = ``os.environ``.
    base_env: dict[str, str] | None = None
    #: v1.301.0: the Iron-Proxy account this call runs as — the variables to SET
    #: (its ``CLAUDE_CONFIG_DIR``) and to UNSET, applied after ``child_env``.
    #: None = no account: the default login, exactly as before.
    account_env: dict[str, str] | None = None
    account_unset: tuple[str, ...] = ()


def with_account(env: dict[str, str], call: NativeCall) -> dict[str, str]:
    """``env`` with the call's account applied (unset first, then set) — after
    the shared login guard, so the account's home survives it. Untouched (the
    same object) when the call carries no account."""
    if call.account_env is None:
        return env
    for key in call.account_unset:
        env.pop(key, None)
    env.update(call.account_env)
    return env


def build_argv(call: NativeCall, root: Path) -> list[str]:
    """Their argv, with ``--mcp-config`` pointing at a FILE (a JSON string on
    the command line does not survive an npm ``.cmd`` shim's cmd.exe quoting)
    plus ``--effort`` and our ``--max-budget-usd``."""
    argv = [call.exe, "-p"]
    if call.model_arg:
        argv += ["--model", call.model_arg]
    argv += [
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--tools", "",
        "--system-prompt-file", str(root / "system.md"),
        "--settings", str(root / "settings.json"),
        "--setting-sources", "",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--max-turns", "1",
        "--permission-mode", "dontAsk",
        "--no-session-persistence",
        "--mcp-config", str(root / "mcp.json"),
    ]
    if call.effort:
        argv += ["--effort", call.effort]
    if call.budget_usd:
        argv += ["--max-budget-usd", f"{call.budget_usd:.2f}"]
    return argv


def marked_frames(frames: list[dict]) -> list[dict]:
    """Copies of ``frames`` with every historical user frame ``shouldQuery:
    false`` — only the last frame asks the model anything."""
    out = []
    last = len(frames) - 1
    for i, frame in enumerate(frames):
        frame = copy.deepcopy(frame)
        if frame.get("type") == "user" and i < last:
            frame["shouldQuery"] = False
        out.append(frame)
    return out


def _encode(frame: dict) -> bytes:
    return (json.dumps(frame, allow_nan=False) + "\n").encode("utf-8")


# --------------------------------------------------------------------------- #
# Process + resources (sync; always called off the loop)
# --------------------------------------------------------------------------- #
class _Resources:
    """The temp dir, the relay and the process of ONE request.

    Every method is blocking and runs in a worker thread. ``kill`` may be
    called from any thread at any time: the handle is published under a lock,
    so a cancel that lands while the spawn is in flight is honoured by the
    spawning thread itself (the ``run_cli`` shape)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cancelled = False
        self.root: Path | None = None
        self.admission: Admission | None = None
        self.proc: subprocess.Popen | None = None
        self.argv: list[str] = []
        self.threads: list[threading.Thread] = []
        self.stderr_tail: deque[bytes] = deque(maxlen=64)

    def prepare(self, call: NativeCall, env: dict[str, str]) -> dict[str, str]:
        root = Path(tempfile.mkdtemp(prefix="ij-claude-"))
        self.root = root
        (root / "tools.json").write_text(json.dumps(call.manifest), encoding="utf-8")
        (root / "settings.json").write_text(
            json.dumps({"env": {"CLAUDE_CODE_EXTRA_BODY": call.body}}), encoding="utf-8"
        )
        (root / "system.md").write_text(call.system, encoding="utf-8")
        command = inert_mcp_command(str(root / "tools.json"))
        mcp = {"mcpServers": {F.SERVER: {"command": command[0], "args": command[1:]}}}
        (root / "mcp.json").write_text(json.dumps(mcp), encoding="utf-8")
        upstream = upstream_of(call.base_env)
        # The queried frame lets the relay keep the cache breakpoint off the
        # per-request context native appends to the turn it answers.
        queried = call.frames[-1]["message"]["content"]
        admission = Admission(upstream, call.idle_timeout_s, queried=queried)
        with self.lock:
            self.admission = admission
            cancelled = self.cancelled
        if cancelled:
            # Teardown ran while this thread was still preparing (a Stop during
            # setup): it could not see the relay or the files yet, so release
            # them here. Both releases are idempotent.
            admission.close()
            shutil.rmtree(root, ignore_errors=True)
            raise asyncio.CancelledError()
        env = loopback_unproxied(dict(env))
        env["ANTHROPIC_BASE_URL"] = admission.url
        self.argv = build_argv(call, root)
        return env

    def spawn(self, env: dict[str, str]) -> subprocess.Popen:
        popen_kw: dict[str, Any] = {}
        if os.name != "nt":
            popen_kw["start_new_session"] = True  # killpg reaches the whole tree
        with self.lock:
            if self.cancelled:
                raise asyncio.CancelledError()
        proc = subprocess.Popen(  # noqa: S603 — argv list, no shell
            self.argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=stable_workdir(),
            env=env,
            **popen_kw,
        )
        with self.lock:
            self.proc = proc
            cancelled = self.cancelled
        if cancelled:
            _kill_tree(proc)
            raise asyncio.CancelledError()
        return proc

    def kill(self) -> None:
        with self.lock:
            self.cancelled = True
            proc = self.proc
            admission = self.admission
        if admission is not None:
            admission.abort()
        if proc is not None and proc.poll() is None:
            _kill_tree(proc)

    def close(self) -> None:
        """Teardown, whatever happened: the tree dies if it is still up, the
        relay closes, readers are joined, pipes closed, files removed."""
        self.kill()
        proc = self.proc
        if proc is not None:
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001 — a wedged reap must not hang teardown
                pass
        for t in self.threads:
            t.join(timeout=5)
        if proc is not None:
            # A reader still parked in read() (something outlived the kill
            # and holds the pipe) keeps its pipe: on Windows, closing a handle
            # under a pending read blocks until that read returns — measured:
            # teardown then waited out the holder's whole life. The daemon
            # reader thread closes nothing and dies with the pipe.
            readers_done = not any(t.is_alive() for t in self.threads)
            pipes = (proc.stdin, proc.stdout, proc.stderr) if readers_done else (proc.stdin,)
            for pipe in pipes:
                try:
                    if pipe and not pipe.closed:
                        pipe.close()
                except Exception:  # noqa: BLE001
                    pass
        if self.admission is not None:
            try:
                self.admission.close()
            except Exception:  # noqa: BLE001
                pass
        if self.root is not None:
            shutil.rmtree(self.root, ignore_errors=True)

    def stderr_text(self) -> str:
        return b"".join(self.stderr_tail).decode("utf-8", errors="replace")[-2000:]


def _kill_tree(proc: subprocess.Popen) -> None:
    """The ONE tree-kill (sandbox/native, v1.287.0)."""
    from ....sandbox.native import _kill_tree as kill_tree

    kill_tree(proc)


# --------------------------------------------------------------------------- #
# Event sources
# --------------------------------------------------------------------------- #
class _Unreadable(RuntimeError):
    pass


def _parse_line(raw: bytes | str) -> Any:
    line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    line = line.strip()
    if not line:
        return None
    try:
        event = json.loads(line)
    except ValueError:
        return _Unreadable(f"claude-cli: unreadable CLI output: {line[:300]!r}")
    if not isinstance(event, dict):
        return _Unreadable(f"claude-cli: unreadable CLI output: {line[:300]!r}")
    return event


class _Timeouts:
    def __init__(self, call: NativeCall, started: float) -> None:
        self.idle = max(0.05, float(call.idle_timeout_s))
        self.total = max(0.05, float(call.total_timeout_s))
        self.deadline = started + self.total

    def window(self) -> tuple[float, bool]:
        """``(seconds to wait, is_total)`` — the smaller of the two bounds."""
        remaining = self.deadline - time.monotonic()
        if remaining <= self.idle:
            return max(0.0, remaining), True
        return self.idle, False

    def error(self, is_total: bool) -> ProviderError:
        if is_total:
            return ProviderError(f"claude-cli: CLI timed out after {self.total:.0f}s", transient=True)
        return ProviderError(
            f"claude-cli: CLI timed out — no output for {self.idle:.0f}s", transient=True
        )


class _ProcSource:
    """Live process: frames go to stdin, events come from a reader thread."""

    def __init__(self, res: _Resources, proc: subprocess.Popen, timeouts: _Timeouts) -> None:
        self.res, self.proc, self.t = res, proc, timeouts
        self.queue: asyncio.Queue = asyncio.Queue()
        self.stdin_open = True
        loop = asyncio.get_running_loop()

        def post(item: Any) -> None:
            try:
                loop.call_soon_threadsafe(self.queue.put_nowait, item)
            except RuntimeError:
                pass  # the loop is gone; nobody is listening

        def read_stdout() -> None:
            try:
                for raw in proc.stdout:  # type: ignore[union-attr]
                    item = _parse_line(raw)
                    if item is not None:
                        post(item)
            except Exception as exc:  # noqa: BLE001 — a dead pipe ends the stream
                post(_Unreadable(f"claude-cli: lost the CLI's output ({type(exc).__name__})"))
            finally:
                try:
                    proc.wait()
                except Exception:  # noqa: BLE001
                    pass
                post(None)

        def read_stderr() -> None:
            try:
                for chunk in iter(lambda: proc.stderr.read1(4096), b""):  # type: ignore[union-attr]
                    res.stderr_tail.append(chunk)
            except Exception:  # noqa: BLE001
                pass

        for target in (read_stdout, read_stderr):
            t = threading.Thread(target=target, daemon=True, name="claude-cli-reader")
            t.start()
            res.threads.append(t)

    async def _bounded(self, work: Callable[[], Any]) -> None:
        wait, is_total = self.t.window()
        try:
            await asyncio.wait_for(asyncio.to_thread(work), timeout=wait)
        except TimeoutError:
            raise self.t.error(is_total) from None

    async def write(self, frame: dict) -> bool:
        """False when the CLI has stopped reading (it exited — go read why)."""
        if not self.stdin_open:
            return False
        data = _encode(frame)

        def work() -> None:
            self.proc.stdin.write(data)  # type: ignore[union-attr]
            self.proc.stdin.flush()  # type: ignore[union-attr]

        try:
            await self._bounded(work)
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            self.stdin_open = False
            return False
        return True

    async def close_stdin(self) -> None:
        if not self.stdin_open:
            return
        self.stdin_open = False
        try:
            await self._bounded(self.proc.stdin.close)  # type: ignore[union-attr]
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            pass

    async def receive(self) -> Any:
        wait, is_total = self.t.window()
        try:
            return await asyncio.wait_for(self.queue.get(), timeout=wait)
        except TimeoutError:
            raise self.t.error(is_total) from None

    async def returncode(self) -> int:
        code = self.proc.poll()
        if code is None:
            code = await asyncio.to_thread(self.proc.wait, 5)
        return int(code)


class _ListSource:
    """An injected ``runner(argv, stdin)`` already ran: replay its stdout.

    The test-double seam (v1.287.0: ``runner`` is for doubles only). Every
    frame was handed over at once, so writes are no-ops; the acknowledgment
    and assembly logic is the SAME code that reads a live process."""

    def __init__(self, code: int, stdout: str, stderr: str, res: _Resources) -> None:
        items = [_parse_line(line) for line in (stdout or "").splitlines()]
        self.items: deque = deque(i for i in items if i is not None)
        self.code = int(code)
        if stderr:
            res.stderr_tail.append(stderr.encode("utf-8", errors="replace"))

    async def write(self, frame: dict) -> bool:
        return True

    async def close_stdin(self) -> None:
        return None

    async def receive(self) -> Any:
        return self.items.popleft() if self.items else None

    async def returncode(self) -> int:
        return self.code


# --------------------------------------------------------------------------- #
# Failure wording
# --------------------------------------------------------------------------- #
def failure_message(detail: str, code: int | None = 1, *, probe: bool = True) -> str:
    """The sentence for a failed run — sign-in refusals mapped to the remedy
    by the ONE place that knows them (``cli_auth``, v1.234.0), and the shared
    probe told so availability turns honest at once. ``probe=False`` (a call
    run as an Iron-Proxy account, v1.301.0): the probe is NOT told — one
    account's refusal does not sign the whole provider out."""
    detail = (detail or "").strip() or "the CLI failed without saying why"
    result = json.dumps({"type": "result", "is_error": True, "result": detail})
    msg = cli_failure_message(PROVIDER, BINARY, int(code if code is not None else 1), result, "")
    if probe:
        note_cli_failure(BINARY, msg)
    return msg


def signed_out_message(native_error: str, *, probe: bool = True) -> str:
    msg = f"{PROVIDER}: {SIGN_IN_FIX[BINARY]} (the CLI said: {(native_error or '').strip()[:120]})"
    if not probe:
        return msg  # an Iron-Proxy account: that account is parked, not the provider
    try:
        DEFAULT_PROBE.mark_signed_out(BINARY, "not signed in (a request was refused)")
    except Exception:  # noqa: BLE001 — feedback is best-effort
        pass
    return msg


def _native_error_of(event: dict) -> tuple[str, str] | None:
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    code = event.get("error") or message.get("error")
    if not code:
        return None
    detail = "\n".join(
        str(b.get("text", ""))
        for b in message.get("content") or []
        if isinstance(b, dict) and b.get("type") == "text"
    )
    return (detail or str(code)), str(code)


def _diagnose(
    events: list[dict], code: int | None, stderr: str, fallback: str, *, probe: bool = True
) -> RuntimeError:
    """The best sentence for a run that ended before it answered."""
    for event in events:
        if event.get("type") == "assistant":
            found = _native_error_of(event)
            if found:
                detail, err_code = found
                if err_code == "authentication_failed":
                    return RuntimeError(signed_out_message(detail, probe=probe))
                return RuntimeError(failure_message(detail, code, probe=probe))
    for event in reversed(events):
        if event.get("type") == "result" and event.get("is_error"):
            return RuntimeError(failure_message(
                str(event.get("result") or event.get("subtype") or ""), code, probe=probe))
    if stderr.strip():
        return RuntimeError(failure_message(stderr.strip()[-400:], code, probe=probe))
    return RuntimeError(f"{PROVIDER}: {fallback}" + (f" (exit {code})" if code not in (None, 0) else ""))


# --------------------------------------------------------------------------- #
# Final assembly — their logic, our vocabulary
# --------------------------------------------------------------------------- #
@dataclass
class Collected:
    assistants: list[dict] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)
    stopped: bool = False
    emitted: str = ""
    native_error: str | None = None
    native_error_code: str | None = None


def _cost(final: dict) -> float | None:
    value = final.get("total_cost_usd")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and value >= 0 else None


def _usage(src: Any) -> dict[str, Any]:
    if not isinstance(src, dict) or not all(
        isinstance(src.get(k), (int, float)) and not isinstance(src.get(k), bool)
        for k in ("input_tokens", "output_tokens")
    ):
        raise RuntimeError(f"{PROVIDER}: the CLI's answer carried no token usage")

    def num(key: str) -> int:
        value = src.get(key)
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0

    read, created = num("cache_read_input_tokens"), num("cache_creation_input_tokens")
    return {
        # TOTAL prompt tokens processed: uncached + cache read + cache written.
        "input_tokens": num("input_tokens") + read + created,
        "output_tokens": num("output_tokens"),
        "cache_read_input_tokens": read,
        "cache_creation_input_tokens": created,
    }


def _reads_as_auth(admission: Any, got: Collected, said: str) -> bool:
    """A 403 that means "your login no longer works" (typed, or in those words)."""
    error_type = admission.error_type() if hasattr(admission, "error_type") else ""
    # NOT the CLI's own ``authentication_failed`` code: the real CLI (2.1.288)
    # stamps it on EVERY upstream 403, including "your organization does not
    # have access to this model" — reading it here marked a signed-in user
    # signed out (measured live). Anthropic's own type and words decide.
    return (
        error_type == "authentication_error"
        or is_sign_in_refusal(said)
        or bool(_AUTH_WORDS.search(said))
    )


def upstream_failure(got: Collected, admission: Any, *, probe: bool = True) -> Exception:
    """The error for a relayed request that did not end in a complete 200.

    In plain words, classified for the router — the technical detail goes to
    an INFO log line (statuses, counts and exception type names only; never a
    header, a credential or a body):

    * 401, or a 403 that reads as auth -> the SAME sign-in remedy the
      logged-out path gives (``signed_out_message``, which tells the probe);
    * another status -> that status (429/5xx/529 transient by TRANSIENT_STATUS,
      with ``Retry-After``) and Anthropic's own message;
    * no status, or a 200 cut off before any text reached the user ->
      :data:`DROPPED`, TRANSIENT (native's retries are off, so nobody else
      will retry it); a local relay bug (not a network error) stays permanent;
    * a 200 cut off AFTER text streamed -> unchanged (the router's committed
      path owns it: the user already saw part of an answer)."""
    status = admission.status if isinstance(admission.status, int) else None
    capture = admission.capture
    said = (admission.error_text() or "").strip()
    first = (
        f"first upstream attempt: status {admission.status}, capture "
        + ("complete" if capture.complete else "incomplete")
        + (f", relay failure {admission.failure}" if admission.failure else "")
        + f", native retries denied: {admission.denied}"
    )
    log.info("claude-cli: the request did not complete (%s)", first)
    if status == 401 or (status == 403 and _reads_as_auth(admission, got, said)):
        # Anthropic's own words first: after a refused retry the CLI's text is
        # the relay's refusal marker, which means nothing to the user.
        cli_said = got.native_error or ""
        if CONSUMED_MESSAGE in cli_said:
            cli_said = ""
        return ProviderError(
            signed_out_message(said or cli_said or f"HTTP {status}", probe=probe), status_code=status
        )
    if status is not None and status != 200:
        words = said[:300] or "no reason given"
        if status == 429:
            text = f"{PROVIDER}: Anthropic is rate-limiting this account right now — try again shortly"
        elif status in TRANSIENT_STATUS:
            text = f"{PROVIDER}: Anthropic is busy or briefly unavailable (HTTP {status}) — try again shortly"
        else:
            text = f"{PROVIDER}: Anthropic refused the request (HTTP {status})"
        return ProviderError(
            f"{text}. Anthropic said: {words}",
            status_code=status,
            retry_after=parse_retry_after(getattr(admission, "retry_after", None)),
        )
    if got.emitted:
        # Part of the answer already reached the user: unchanged since v1.300.0.
        if said:
            first += ", upstream said: " + said[:500]
        return ProviderError(
            f"{PROVIDER}: incomplete upstream response ({first})"
            + (f": {got.native_error}" if got.native_error else "")
        )
    if status is None and admission.failure and getattr(admission, "failure_network", None) is False:
        return ProviderError(
            f"{PROVIDER}: Iron Jarvis could not pass the request on to Anthropic "
            "(an internal relay error, not your connection)"
        )
    return ProviderError(DROPPED, transient=True)


def assemble(
    got: Collected,
    *,
    returncode: int,
    admission: Any,
    names: F.ToolNames,
    stderr: str = "",
    probe: bool = True,
) -> tuple[LLMResponse, str]:
    """``(response, remainder)`` — ``remainder`` is final text the stream had
    not yet emitted (always a suffix; anything else is :data:`STREAM_MISMATCH`).
    ``probe=False``: a call run as an Iron-Proxy account (see :func:`failure_message`)."""
    assistants, stopped = list(got.assistants), got.stopped
    used = bool(admission is not None and getattr(admission, "used", False))
    if used:
        capture = admission.capture
        if admission.status != 200 or not capture.complete:
            raise upstream_failure(got, admission, probe=probe)
        assistants = [capture.message]
        stopped = True
    handled = bool(admission is not None and getattr(admission, "denied", 0)) or (
        used and assistants[0].get("stop_reason") == "refusal"
    )
    if got.native_error and not handled:
        if got.native_error_code == "authentication_failed" and not used:
            # No usable login: native refuses before any upstream request.
            raise RuntimeError(signed_out_message(got.native_error, probe=probe))
        raise RuntimeError(failure_message(got.native_error, returncode, probe=probe))
    if len(got.results) != 1 or not assistants or not stopped:
        events = got.results
        raise _diagnose(
            events, returncode, stderr,
            "the CLI ended without a complete answer (an assistant message, message_stop "
            "and one result are required)",
            probe=probe,
        )
    final = got.results[0]
    blocks = [b for a in assistants for b in (a.get("content") or []) if isinstance(b, dict)]
    calls: list[ToolCall] = []
    for block in blocks:
        if block.get("type") == "tool_use":
            ours = names.ours(block.get("name"))  # outside the inventory -> raises
            args = block.get("input")
            calls.append(ToolCall(id=str(block.get("id") or ""), name=ours,
                                  arguments=args if isinstance(args, dict) else {}))
    # --max-turns 1 with a tool batch ends as error_max_turns + exit 1: success.
    boundary = bool(calls) and final.get("subtype") == "error_max_turns" and returncode == 1
    if not boundary and not handled and (
        returncode != 0 or final.get("is_error") or final.get("subtype") != "success"
    ):
        raise RuntimeError(failure_message(
            str(final.get("result") or f"the CLI request failed ({final.get('subtype')})"),
            returncode,
            probe=probe,
        ))
    usage = _usage(assistants[0].get("usage") if used else final.get("usage"))
    cost = _cost(final)
    if cost is not None:
        usage["cost_usd"] = cost
    text = "".join(str(b.get("text", "")) for b in blocks if b.get("type") == "text")
    remainder = ""
    if got.emitted != text:
        if text.startswith(got.emitted):
            remainder = text[len(got.emitted):]
        else:
            raise RuntimeError(STREAM_MISMATCH)
    if calls:
        finish = "tool_use"
    elif any(a.get("stop_reason") in ("max_tokens", "model_context_window_exceeded") for a in assistants):
        finish = "max_tokens"
    else:
        finish = "stop"
    raw = F.carrier(
        assistants, text,
        [{"id": c.id, "name": c.name, "input": c.arguments} for c in calls],
    )
    # v1.323.0: the thinking blocks' text, display-only (the carrier above
    # still holds the signed blocks for replay).
    thinking = "\n\n".join(
        str(b.get("thinking")) for b in blocks
        if b.get("type") == "thinking" and isinstance(b.get("thinking"), str) and b.get("thinking")
    )
    return LLMResponse(text=text, tool_calls=calls, finish_reason=finish, usage=usage,
                       raw_blocks=[raw], thinking=thinking), remainder


# --------------------------------------------------------------------------- #
# What one attempt did (v1.301.0: read by the adapter to report to Iron-Proxy)
# --------------------------------------------------------------------------- #
#: Upstream statuses that may mean "this ACCOUNT is limited" — handed to
#: Iron-Proxy, whose own classifier decides. NOT 503/529: an overload is the
#: provider's, not one account's — it is never reported and never rotates.
LIMIT_STATUSES = (429,)


@dataclass
class Attempt:
    """Filled by :func:`run` whatever happened (in its ``finally``)."""

    #: Did the relay forward a request upstream at all?
    relay_used: bool = False
    status: int | None = None
    #: The allow-listed response headers (``admission.exposed_headers``).
    headers: dict[str, str] = field(default_factory=dict)
    #: Anthropic's own words for a non-200 (bounded), '' when none.
    error_text: str = ""
    #: A 403 that reads as "your login no longer works" (``_reads_as_auth``).
    auth_refusal: bool = False
    #: The CLI's own error words when it refused WITHOUT asking upstream.
    native_error: str = ""
    #: True once any text reached the caller.
    emitted: bool = False

    def limit_report(self) -> dict[str, Any] | None:
        """What to hand Iron-Proxy for this failed attempt, or None (not a
        limit/auth signal: a model-access 403, a 400, a dropped connection).

        Relayed: 429, 401, or a 403 that reads as auth — with the
        status, the allow-listed headers and Anthropic's words. Not relayed:
        the CLI refused locally (its usage-limit or sign-in wording) — the
        text alone. Iron-Proxy's classifier decides (``parked``)."""
        if self.relay_used:
            status = self.status
            if status in LIMIT_STATUSES or status == 401 or (status == 403 and self.auth_refusal):
                return {"status": status, "headers": dict(self.headers), "text": self.error_text[:4000]}
            return None
        if self.native_error.strip():
            return {"text": self.native_error[:4000]}
        return None


def _record_attempt(attempt: Attempt, admission: Any, got: Collected) -> None:
    """Copy what the attempt did into ``attempt`` (never raises)."""
    try:
        attempt.emitted = bool(got.emitted)
        if admission is not None and getattr(admission, "used", False):
            attempt.relay_used = True
            attempt.status = admission.status if isinstance(admission.status, int) else None
            attempt.headers = dict(getattr(admission, "headers", None) or {})
            said = (admission.error_text() or "").strip()
            attempt.error_text = said[:4000]
            attempt.auth_refusal = attempt.status == 403 and _reads_as_auth(admission, got, said)
            return
        text = got.native_error or ""
        if not text:
            for row in got.results:
                if row.get("is_error"):
                    text = str(row.get("result") or "")
                    break
        attempt.native_error = text[:4000]
    except Exception:  # noqa: BLE001 — a report is best-effort, never the failure
        pass


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #
Runner = Callable[[list[str], str], tuple[int, str, str]]


async def _teardown(res: _Resources) -> None:
    """Shielded: a second cancel must not abandon the kill half-way. A cancel
    that lands here is re-raised once teardown has finished."""
    again = False
    task = asyncio.ensure_future(asyncio.to_thread(res.close))
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            again = True
            res.kill()  # make sure the wait inside close() is short
    if again:
        raise asyncio.CancelledError()


async def run(
    call: NativeCall, *, runner: Runner | None = None, attempt: Attempt | None = None
) -> AsyncIterator[dict]:
    """Drive one request. Yields ``{"type": "text", "text": d}`` deltas as the
    CLI streams them, then ``{"type": "final", "response": LLMResponse}``.

    The consumer MUST ``aclose()`` this generator when it stops early (the
    adapter does, in a ``finally``) — that is what kills the process.
    ``attempt`` (v1.301.0) is filled with what the request did, whatever
    happened, before the teardown.
    """
    started = time.monotonic()
    env = with_account(
        child_env(call.base_env, max_output_tokens=call.max_output_tokens), call
    )
    probe = call.account_env is None
    res = _Resources()
    timeouts = _Timeouts(call, started)
    frames = marked_frames(call.frames)
    got = Collected()
    try:
        child = await asyncio.to_thread(res.prepare, call, env)
        if runner is None:
            proc = await asyncio.to_thread(res.spawn, child)
            source: Any = _ProcSource(res, proc, timeouts)
        else:
            stdin = b"".join(_encode(f) for f in frames).decode("utf-8")
            code, out, err = await asyncio.to_thread(runner, list(res.argv), stdin)
            source = _ListSource(code, out, err, res)

        # 1. history, one frame at a time; each replayed user frame is acked.
        early: list[dict] = []
        for frame in frames:
            if not await source.write(frame):
                break  # the CLI stopped reading — read what it said below
            if frame.get("shouldQuery") is False:
                while True:
                    event = await source.receive()
                    if isinstance(event, _Unreadable):
                        raise RuntimeError(str(event))
                    if event is None:
                        raise _diagnose(early, await source.returncode(), res.stderr_text(),
                                        "the CLI exited before acknowledging the conversation history",
                                        probe=probe)
                    early.append(event)
                    if event.get("type") == "result":
                        if event.get("num_turns") != 0 or event.get("is_error"):
                            raise _diagnose(
                                early, None, res.stderr_text(),
                                "this Claude Code version did not acknowledge history replay "
                                "(expected a zero-turn result) — update Claude Code",
                                probe=probe,
                            )
                        break
        await source.close_stdin()

        # 2. the answer.
        while True:
            event = await source.receive()
            if event is None:
                break
            if isinstance(event, _Unreadable):
                raise RuntimeError(str(event))
            kind = event.get("type")
            if kind == "assistant":
                found = _native_error_of(event)
                if found:
                    got.native_error, got.native_error_code = found
                elif isinstance(event.get("message"), dict):
                    got.assistants.append(event["message"])
            elif kind == "result":
                got.results.append(event)
            elif kind == "stream_event":
                native = event.get("event") if isinstance(event.get("event"), dict) else {}
                if native.get("type") == "message_stop":
                    got.stopped = True
                delta = native.get("delta") if isinstance(native.get("delta"), dict) else {}
                piece = delta.get("text")
                if delta.get("type") == "text_delta" and isinstance(piece, str) and piece:
                    got.emitted += piece
                    yield {"type": "text", "text": piece}
                elif delta.get("type") == "thinking_delta":
                    # v1.323.0: reasoning streams as a `thinking` frame —
                    # display-only. NOT `got.emitted` (that is the ANSWER text
                    # the final is checked against, and what decides whether
                    # an account retry is still allowed).
                    thought = delta.get("thinking")
                    if isinstance(thought, str) and thought:
                        yield {"type": "thinking", "text": thought}
        returncode = await source.returncode()
        response, remainder = assemble(
            got, returncode=returncode, admission=res.admission, names=call.names,
            stderr=res.stderr_text(), probe=probe,
        )
        if remainder:
            got.emitted += remainder
            yield {"type": "text", "text": remainder}
        yield {"type": "final", "response": response}
    finally:
        if attempt is not None:
            _record_attempt(attempt, res.admission, got)
        await _teardown(res)
