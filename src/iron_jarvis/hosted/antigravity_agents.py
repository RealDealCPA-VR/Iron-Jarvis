"""v1.447.0: the user's OWN Antigravity agents, offered in Build › Code's mode
menu (``agent:<name>``, as OpenCode's own agents are since v1.441.0), and
Antigravity's own MCP servers for the Session tab.

MEASURED on this PC (agy 1.5.3, 2026-11-07, a scratch ``USERPROFILE``/``HOME``
and a scratch working folder, no sign-in, NO model call; recordings in
``tests/fixtures/build_app_v14470``):

* ``agy --log-file <F> agents`` prints the agents a session can RUN AS, one
  name per line on stdout, nothing else (no description): with
  ``~/.gemini/config/agents/{tax-reviewer,plain-one,hidden-one,sub-only}.md``
  planted it printed ``hidden-one``, ``plain-one``, ``tax-reviewer`` — a
  ``mainAgent: false`` agent (a helper only) is not listed; a ``hidden: true``
  one IS. A folder's own ``.agents/agents/`` were not listed from an
  untrusted folder. It works signed out ("Auth mode is unspecified" in its
  log) and asks no model anything. Iron Jarvis reads it from its own empty
  probe folder, so the list is the user's GLOBAL agents.
* ``agy acp --agent <name>``: a NEW conversation runs as that agent (its own
  system prompt reached the stand-in model; without the flag it did not). A
  conversation KEEPS the agent it was opened with: resumed without the flag
  it still ran as the agent, and a conversation opened without one — even an
  EMPTY one (``session/new``, no prompt yet) — resumed WITH the flag still ran
  without it. An unknown name runs the default agent with no warning over
  ACP. Antigravity's own ``conversation_summaries.db`` records it
  (``agent_name``). So the agent is chosen once, before the conversation
  starts: :func:`agent_change_problem` refuses a change after that, in words.
* ``agy mcp list`` (no language server, no log, no network): a table ``NAME
  TYPE STATUS COMMAND/URL`` of the servers in Antigravity's OWN settings,
  STATUS ``enabled`` | ``disabled``, or "No MCP servers configured." The
  COMMAND/URL column holds the user's addresses and arguments (keys in a URL
  or ``--token`` arguments included): it is NEVER read — only NAME and STATUS
  (:func:`parse_mcp_list`). It says whether a server is turned on, not whether
  it connected (ACP reports no connection state).

WHEN: the agent list is read in the background (never on a request's path),
one read at a time, when nothing was read yet, the kept read is older than
:data:`AGENTS_TTL_S`, or the agents folder or the program changed (stats
only); a failed read waits :data:`RETRY_AFTER_FAIL_S`. The suite turns both
reads off (:data:`AUTO_PROBE`, ``tests/conftest.py``). The MCP list is read once per process start, off the
loop, in the session's folder.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from .events import clip

log = logging.getLogger(__name__)

#: A mode that runs one of the user's own Antigravity agents: ``agent:<name>``.
AGENT_PREFIX = "agent:"
#: Antigravity's flag for the agent a NEW conversation runs as.
AGENT_FLAG = "--agent"
#: The suite turns these reads off (tests/conftest.py); a test that drives
#: them turns it on. Its own switch: the model read's tests count runs.
AUTO_PROBE = True
#: A kept read older than this is read again (in the background).
AGENTS_TTL_S = 600.0
#: The whole read may take this long (it answered in about a second; a cold
#: first start of the 225 MB program can take far longer — the model read's
#: own bound).
PROBE_TIMEOUT_S = 45.0
#: After a read Antigravity did not answer, the next one waits this long.
RETRY_AFTER_FAIL_S = 120.0
MAX_AGENTS = 30
#: An agent's name as Iron Jarvis passes it (Antigravity allows a colon:
#: its 1.4 changelog fixed "a custom agent's name contains a colon").
_NAME_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,79}$")

_LOCK = threading.Lock()
_RUNNING: set[str] = set()
_FAILED_AT: dict[str, float] = {}
#: The agents the last read listed (memory; the service's file behind it).
LAST_AGENTS: list[str] = []

#: The words the mode menu says under an agent row.
AGENT_ABOUT = ("Antigravity's own agent “{name}”. Follows Antigravity's own rules and the agent's own "
               "settings. Chosen when the conversation starts: Antigravity keeps it for that conversation.")
#: A change of agent once the conversation exists (measured: it keeps its own).
AGENT_KEPT = ("Antigravity keeps the agent a conversation started with, so this session stays {current}. "
              "Start a new session to work with {wanted}.")
UNKNOWN_AGENT = "Antigravity did not list an agent named “{agent}” — choose one from the mode menu."
DEFAULT_AGENT_WORDS = "Antigravity's default agent"


# --------------------------------------------------------------------------- #
# Names and modes
# --------------------------------------------------------------------------- #
def agent_of(mode: Any) -> str:
    """The agent an ``agent:<name>`` mode names, or "" (any other mode)."""
    text = str(mode or "")
    if not text.startswith(AGENT_PREFIX):
        return ""
    name = text[len(AGENT_PREFIX):]
    return name if _NAME_RX.match(name) else ""


def parse_agents(text: str) -> list[str]:
    """``agy agents`` stdout -> the agents' names, in its order, each once.
    A line that is not a plain name is left out (never passed to ``--agent``)."""
    out: list[str] = []
    for line in str(text or "").splitlines():
        name = line.strip()
        if name and _NAME_RX.match(name) and name not in out:
            out.append(name)
        if len(out) >= MAX_AGENTS:
            break
    return out


def known_agents() -> list[str]:
    return list(LAST_AGENTS)


def agent_mode_rows() -> list[dict[str, Any]]:
    """The mode menu's rows for the user's own Antigravity agents."""
    return [{"id": AGENT_PREFIX + name, "label": name, "dangerous": False,
             "about": clip(AGENT_ABOUT.format(name=name), 300)} for name in known_agents()]


def agent_words(mode: Any) -> str:
    name = agent_of(mode)
    return f"“{name}”" if name else DEFAULT_AGENT_WORDS


def agent_change_problem(current_mode: Any, wanted_mode: Any, harness_session_id: Any) -> str:
    """"" when the change is fine; else one sentence: a conversation that
    exists keeps the agent it was opened with (measured), so a change of agent
    after ``session/new`` would show one agent while another runs."""
    if agent_of(current_mode) == agent_of(wanted_mode) or not str(harness_session_id or ""):
        return ""
    return AGENT_KEPT.format(current=agent_words(current_mode), wanted=agent_words(wanted_mode))


# --------------------------------------------------------------------------- #
# The kept read
# --------------------------------------------------------------------------- #
def read_doc(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        if path.is_file() and path.stat().st_size < 1_000_000:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        pass
    return {}


def load(path: Path | None) -> None:
    """BLOCKING-light: what was kept (only when memory holds nothing yet)."""
    if LAST_AGENTS:
        return
    names = read_doc(path).get("agents")
    if isinstance(names, list):
        LAST_AGENTS[:] = [n for n in names if isinstance(n, str) and _NAME_RX.match(n)][:MAX_AGENTS]


def save(path: Path, doc: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(json.dumps(doc, ensure_ascii=False).encode("utf-8"))
        os.replace(tmp, path)
    except OSError:
        log.debug("could not keep Antigravity's agent list", exc_info=True)


def watch_signature(gemini_dir: Path, argv0: list[str] | None = None) -> list[list[Any]]:
    """BLOCKING (a stat each): the agents folder and the program."""
    files = [Path(gemini_dir) / "config" / "agents"]
    if argv0:
        files.append(Path(argv0[0]))
    out: list[list[Any]] = []
    for p in files:
        try:
            st = p.stat()
            out.append([str(p), int(st.st_size), int(st.st_mtime_ns)])
        except OSError:
            out.append([str(p), -1, 0])
    return out


def is_stale(doc: dict[str, Any], *, watch: list[list[Any]] | None = None, now: float | None = None) -> bool:
    at = doc.get("fetched_at")
    if not isinstance(at, (int, float)) or isinstance(at, bool):
        return True
    if (now if now is not None else time.time()) - float(at) > AGENTS_TTL_S:
        return True
    return watch is not None and doc.get("watch") != watch


def reading(path: Path) -> bool:
    with _LOCK:
        return str(path) in _RUNNING


def probe(argv0: list[str], env: dict[str, str], cwd: Path, log_file: Path,
          timeout: float = PROBE_TIMEOUT_S) -> list[str] | None:
    """BLOCKING: one ``agy --log-file F agents`` through ``core.procs.run``
    (its own kill-on-close Job). None when it did not answer."""
    from ..core import procs

    try:
        done = procs.run([*argv0, "--log-file", str(log_file), "agents"], timeout=timeout, cwd=str(cwd), env=env)
    except Exception:  # noqa: BLE001 — Windows could not start it, or it timed out
        log.debug("Antigravity's agent list could not be read", exc_info=True)
        return None
    if getattr(done, "returncode", 1) != 0:
        return None
    out = done.stdout
    text = out.decode("utf-8", "replace") if isinstance(out, (bytes, bytearray)) else str(out or "")
    return parse_agents(text)


def read_now(path: Path, home: Path, argv0: list[str], gemini_dir: Path) -> bool:
    """BLOCKING: read the list now and keep it (memory and file)."""
    from . import antigravity as A
    from .antigravity_catalog import probe_env
    from .process import build_env

    cwd = home / "hosted" / "agy-probe"
    cwd.mkdir(parents=True, exist_ok=True)
    log_file = home / "hosted" / "agy-agents.log"
    A.trim_log(log_file)
    names = probe(argv0, probe_env(build_env(None)), cwd, log_file)
    if names is None:
        return False
    LAST_AGENTS[:] = names
    save(path, {"agents": names, "fetched_at": time.time(), "watch": watch_signature(gemini_dir, argv0)})
    return True


def refresh_soon(path: Path, home: Path, argv0: list[str], gemini_dir: Path) -> bool:
    """BLOCKING-light (stats, one small read): start ONE background read when
    the kept list is missing, old or about a changed folder. True when a read
    is under way after this call."""
    if not AUTO_PROBE:
        return False
    key = str(path)
    with _LOCK:
        if key in _RUNNING:
            return True
        failed = _FAILED_AT.get(key)
    if failed is not None and time.monotonic() - failed < RETRY_AFTER_FAIL_S:
        return False
    if not is_stale(read_doc(path), watch=watch_signature(gemini_dir, argv0)):
        return False
    with _LOCK:
        if key in _RUNNING:
            return True
        _RUNNING.add(key)
    threading.Thread(target=_refresh, args=(path, home, list(argv0), gemini_dir), name="antigravity-agents",
                     daemon=True).start()
    return True


def _refresh(path: Path, home: Path, argv0: list[str], gemini_dir: Path) -> None:
    ok = False
    try:
        ok = read_now(path, home, argv0, gemini_dir)
    except Exception:  # noqa: BLE001 — a background read never raises anywhere
        log.debug("could not read Antigravity's agent list", exc_info=True)
    finally:
        with _LOCK:
            if ok:
                _FAILED_AT.pop(str(path), None)
            else:
                _FAILED_AT[str(path)] = time.monotonic()
            _RUNNING.discard(str(path))


# --------------------------------------------------------------------------- #
# Its own MCP servers (``agy mcp list``)
# --------------------------------------------------------------------------- #
NO_SERVERS = "No MCP servers configured."
#: ``agy mcp list``'s STATUS words -> the Session tab's states.
MCP_STATUS = {"enabled": "on", "disabled": "off"}


def parse_mcp_list(text: str) -> list[dict[str, str]] | None:
    """``agy mcp list`` stdout -> ``[{name, state}]`` (``on`` / ``off``), or
    None when it is not the table it prints (not known). Only the NAME and
    STATUS columns are read — the COMMAND/URL column (addresses, keys in a
    URL, a ``--token`` argument) never is: each row is cut at the column the
    header puts STATUS's end."""
    from . import tool_setup as TS

    lines = [ln.rstrip("\r") for ln in str(text or "").splitlines() if ln.strip()]
    if not lines:
        return None
    if lines[0].strip() == NO_SERVERS:
        return []
    head = lines[0]
    m_type, m_status, m_cmd = head.find("TYPE"), head.find("STATUS"), head.find("COMMAND/URL")
    if not head.startswith("NAME") or not (0 < m_type < m_status < m_cmd):
        return None
    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        name = clip(line[:m_type].strip(), TS.NAME_CHARS)
        status = line[m_status:m_cmd].strip().lower()
        if not name or status not in MCP_STATUS:
            continue
        if not any(r["name"] == name for r in rows):
            rows.append({"name": name, "state": MCP_STATUS[status]})
        if len(rows) >= TS.MAX_SERVERS:
            break
    return rows


def read_mcp_list(argv0: list[str], env: dict[str, str], cwd: str,
                  timeout: float = PROBE_TIMEOUT_S) -> list[dict[str, str]] | None:
    """BLOCKING: one ``agy mcp list`` (its own kill-on-close Job; Antigravity's
    updater off). None when it did not answer or printed something else —
    and while the suite turns these reads off (:data:`AUTO_PROBE`)."""
    from ..core import procs
    from .antigravity_catalog import probe_env

    if not AUTO_PROBE:
        return None
    try:
        done = procs.run([*argv0, "mcp", "list"], timeout=timeout, cwd=cwd or None, env=probe_env(env))
    except Exception:  # noqa: BLE001
        log.debug("Antigravity's MCP list could not be read", exc_info=True)
        return None
    if getattr(done, "returncode", 1) != 0:
        return None
    out = done.stdout
    return parse_mcp_list(out.decode("utf-8", "replace") if isinstance(out, (bytes, bytearray)) else str(out or ""))
