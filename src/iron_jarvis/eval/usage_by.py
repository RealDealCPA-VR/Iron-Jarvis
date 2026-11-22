"""What everything costs, by folder, client, automation and agent (next
leaps 46-a, R2.19; ``docs/next-leaps/10-your-computers-and-models-as-one.md``).

NO NEW COUNTING. Every model call is already on exactly one usage row
(``AgentRun``: an agent run's, a chat reply's ``session_id="chat"`` with its
``thread_id``, a tool call's own row; ``eval.call_ledger``). This module reads
those rows ONCE for a window and says whose work each was:

* **Folder**: a chat's folder (``wall.thread_owners``: the chat's saved
  folder, a Chat › Code session's checkout, else its project's root) or an
  agent run's (``wall.session_owners``: its workspace, else its project's
  root). One folder per row; none reads :data:`NO_FOLDER`.
* **Client**: the confirmed client whose folder that is
  (``ClientMap.client_of``); none reads :data:`NO_CLIENT`. A waiting name is
  never a client here.
* **Automation**: the run's ``origin`` prefix (``schedule:``, ``reflex:``,
  ``workflow:``, ``goal:``, ``assignment:``, ``app:``, ``remote:``). A run
  with no automation origin of its own (a helper a schedule's run delegated
  to) is its parent's, walked up ``AgentRun.parent_id``. Chats and the runs
  you started read :data:`NOT_AUTOMATION`.
* **Agent**: the roster name the run proves (``roster.session_roster_name``);
  a chat reply reads :data:`CHAT_AGENT`; a step's own row outside chat
  (``call_ledger.STEP_SESSION``: not linked to its run) :data:`NO_AGENT`.

Each row has THREE FIGURES KEPT APART, never added together:

* ``pay_per_use``: money (``pricing.recorded_cost``, the one rule), with
  ``measured`` True only when every paid row's figure was the vendor's own
  (``AgentRun.cost_source == "vendor"``); a row priced from the table, an
  older row, or one with no price reads estimated. Tokens whose price is not
  known are counted apart (``price_unknown_tokens``), never as $0.
* ``plan``: a flat subscription's work, in TOKENS. NEVER MONEY (the list-price
  equivalent stays on the old totals only). ``window_share`` says "about N% of
  a 5-hour window" only once an account's windows were read before and after
  the work; nothing records that yet, so it is None (tokens only).
* ``free``: the user's own hardware (``usage_view.usage_row_is_local``, per
  model, so a proxy that sends a name to the cloud is not free).

An AUTOMATION row also says how many times it asked you: the
``interrupt.asked`` events (every card or question put to the person,
``core.approvals.InterruptNote``) of the same runs and chats, in the window.
The demo model's rows and rows that moved nothing are left out (the Usage
page's rule, ``usage_view.is_noise_row``).

LIGHT ON THE PC: one bounded read of the window (at most :data:`RUNS_MAX`
usage rows, newest first; ``truncated`` says so), batched IN reads for the
runs, chats and goals they name, at most :data:`ASKS_MAX` ask events.
BLOCKING (SQLite): the route is a plain ``def`` and the chat tool runs it off
the event loop. Never raises: a read that fails answers empty rows with
``error`` set.

``keep`` (the chat tool): a filter over each usage row's :class:`Who` (the
client wall, Local only); what it drops is counted in ``left_out`` and in no
figure, so an answer never adds in work it may not name.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from . import pricing

log = logging.getLogger(__name__)

GROUP_FOLDER = "folder"
GROUP_CLIENT = "client"
GROUP_AUTOMATION = "automation"
GROUP_AGENT = "agent"
GROUPS = (GROUP_FOLDER, GROUP_CLIENT, GROUP_AUTOMATION, GROUP_AGENT)

DAYS_DEFAULT = 30
DAYS_MIN = 1
DAYS_MAX = 365

#: Usage rows one read considers, at most (newest first).
RUNS_MAX = 200_000
#: Ask events one read considers, at most (newest first).
ASKS_MAX = 50_000
#: Rows one answer carries, at most (the biggest first); the rest are summed
#: in ``rest``.
ROWS_MAX = 200
#: Parent hops walked to find the automation a helper run belongs to.
PARENT_HOPS = 6
_IN_CHUNK = 400
#: ``wall.thread_owners`` / ``session_owners`` read this many ids a call.
_OWNERS_CHUNK = 1000

# The words a row reads (VOCABULARY.md: plain words, no engine words).
NO_FOLDER = "No folder"
NO_CLIENT = "No client"
NOT_AUTOMATION = "Not an automation"
CHAT_AGENT = "Chat"
NO_AGENT = "Not linked to an agent"
QUEUED_TASKS = "Queued tasks"
OTHER_APPS = "Other apps"
CLIENTS_UNREADABLE = (
    "Your clients could not be read just now, so nothing is listed by client. Nothing was changed."
)

#: An automation's origin prefix -> (the kind's word, whether its suffix is
#: a NAME worth showing). Mirrors ``components/sessions/OriginChip``.
AUTOMATION_KINDS: dict[str, tuple[str, bool]] = {
    "schedule": ("Schedule", True),
    "reflex": ("Rule", True),
    "workflow": ("Workflow", True),
    "goal": ("Goal", True),
    "assignment": (QUEUED_TASKS, False),
    "app": (OTHER_APPS, False),
    "remote": ("Paired computer", True),
}

#: The chat session's id and a step's own row outside chat
#: (``call_ledger.STEP_SESSION``).
CHAT_SESSION = "chat"
STEP_SESSION = "tool-use"

_ATTEMPT_RX = re.compile(r"^(.+)~try\d+$")


def clamp_days(days: Any) -> int:
    try:
        n = int(days)
    except (TypeError, ValueError):
        return DAYS_DEFAULT
    return max(DAYS_MIN, min(DAYS_MAX, n))


def clean_group(group: Any) -> str:
    g = str(group or "").strip().lower()
    return g if g in GROUPS else ""


@dataclass(frozen=True)
class Who:
    """Whose work one usage row (or ask) was."""

    folder: str = ""
    folder_key: str = ""
    client_id: str = ""
    client_name: str = ""
    auto_key: str = ""
    auto_kind: str = ""
    auto_name: str = ""
    agent_key: str = ""
    agent_label: str = ""
    #: A Local only chat or run.
    local_only: bool = False


NOBODY = Who()


@dataclass
class _Fig:
    usd: float = 0.0
    pay_tokens: int = 0
    paid_rows: int = 0
    measured_rows: int = 0
    unknown_tokens: int = 0
    plan_tokens: int = 0
    free_tokens: int = 0
    asks: int = 0
    runs: int = 0

    def add(self, other: "_Fig") -> None:
        self.usd += other.usd
        self.pay_tokens += other.pay_tokens
        self.paid_rows += other.paid_rows
        self.measured_rows += other.measured_rows
        self.unknown_tokens += other.unknown_tokens
        self.plan_tokens += other.plan_tokens
        self.free_tokens += other.free_tokens
        self.asks += other.asks
        self.runs += other.runs

    def view(self) -> dict[str, Any]:
        return {
            "pay_per_use": {
                "usd": round(self.usd, 6),
                "tokens": self.pay_tokens,
                # Measured only when every paid row was the vendor's own
                # figure; nothing paid is not "measured".
                "measured": self.paid_rows > 0 and self.measured_rows == self.paid_rows,
                "price_unknown_tokens": self.unknown_tokens,
            },
            # Never money: tokens, and the window share once it is known.
            "plan": {"tokens": self.plan_tokens, "window_share": None, "window": ""},
            "free": {"tokens": self.free_tokens},
            "asks": self.asks,
            "runs": self.runs,
        }


@dataclass
class _Run:
    id: str
    session_id: str
    parent_id: str
    thread_id: str
    provider: str
    model: str
    tokens: int
    input_tokens: int
    output_tokens: int
    stored: float
    source: str


@dataclass
class _Read:
    runs: list[_Run] = field(default_factory=list)
    who: dict[str, Who] = field(default_factory=dict)
    asks: dict[str, int] = field(default_factory=dict)
    truncated: bool = False
    #: False when the client tables could not be read: nothing is listed by
    #: client then (never "No client" for everything).
    clients_ok: bool = True


def _now() -> datetime:
    from ..core.ids import utcnow

    return utcnow()


def _chunks(items: list[str], size: int = _IN_CHUNK) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _bare_thread(value: Any) -> str:
    """A chat id as the usage rows name it: a ``build:`` prefix and an
    attempt's ``~try<n>`` read as the chat itself (``eval.autonomy``'s
    rule)."""
    t = str(value or "").strip()
    if t.startswith("build:"):
        t = t[len("build:"):]
    m = _ATTEMPT_RX.match(t)
    return m.group(1) if m else t


def _agent_words(name: str) -> str:
    """The roster name as the eye reads it: ``custom:tax-reader`` ->
    "tax-reader", ``remote:hermes`` -> "hermes", ``builder`` -> "Builder"."""
    n = str(name or "").strip()
    if not n:
        return ""
    for prefix in ("custom:", "remote:", "dynamic:"):
        if n.lower().startswith(prefix):
            return n[len(prefix):].strip() or n
    return n[:1].upper() + n[1:]


def automation_of(origin: Any, goal_names: dict[str, str] | None = None) -> tuple[str, str, str]:
    """``(key, kind word, name)`` of an automation origin, or ``("", "", "")``
    for one that is not an automation. A goal's name comes from
    ``goal_names`` (its id when it is not known). Pure."""
    o = " ".join(str(origin or "").split())
    if ":" not in o:
        return "", "", ""
    prefix, _, rest = o.partition(":")
    prefix = prefix.strip().lower()
    entry = AUTOMATION_KINDS.get(prefix)
    if entry is None:
        return "", "", ""
    word, named = entry
    rest = rest.strip()
    if not named:
        return prefix, word, ""
    if prefix == "goal":
        name = (goal_names or {}).get(rest) or ""
        return f"goal:{rest}", word, name or "a goal"
    return f"{prefix}:{rest.lower()}", word, rest


def automation_label(kind: str, name: str) -> str:
    """"Rule “invoice rule”", "Queued tasks"."""
    if not name:
        return kind
    return f"{kind} “{name}”"


def _session_rows(db: Any, ids: list[str]) -> dict[str, dict[str, Any]]:
    from sqlmodel import select

    from ..core.models import Session

    out: dict[str, dict[str, Any]] = {}
    for part in _chunks(ids):
        stmt = select(
            Session.id, Session.origin, Session.agent_name, Session.agent_type, Session.options_json,
        ).where(Session.id.in_(part))  # type: ignore[attr-defined]
        for sid, origin, agent_name, agent_type, options_json in db.exec(stmt):
            try:
                opts = json.loads(options_json or "{}")
            except (TypeError, ValueError):
                opts = {}
            out[str(sid)] = {
                "origin": str(origin or ""),
                "agent": str(agent_name or "").strip()
                or str(getattr(agent_type, "value", agent_type) or ""),
                "local_only": isinstance(opts, dict) and opts.get("local_only") is True,
            }
    return out


def _parents(db: Any, run_ids: list[str]) -> dict[str, tuple[str, str]]:
    """``{run id: (session id, parent run id)}`` for runs outside the window."""
    from sqlmodel import select

    from ..core.models import AgentRun

    out: dict[str, tuple[str, str]] = {}
    for part in _chunks(run_ids):
        stmt = select(AgentRun.id, AgentRun.session_id, AgentRun.parent_id).where(
            AgentRun.id.in_(part)  # type: ignore[attr-defined]
        )
        for rid, sid, pid in db.exec(stmt):
            out[str(rid)] = (str(sid or ""), str(pid or ""))
    return out


def _goal_names(db: Any, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    try:
        from sqlmodel import select

        from ..goals.models import GoalContractRecord as G

        out: dict[str, str] = {}
        for part in _chunks(ids):
            for gid, name in db.exec(select(G.id, G.name).where(G.id.in_(part))):  # type: ignore[attr-defined]
                if str(name or "").strip():
                    out[str(gid)] = " ".join(str(name).split())
        return out
    except Exception:  # noqa: BLE001 — a goal with no name reads "a goal"
        log.debug("usage by: goal names could not be read", exc_info=True)
        return {}


def _folders(engine: Any, cmap: Any, ids: list[str], *, chats: bool) -> dict[str, str]:
    from ..workmap.clients import wall

    out: dict[str, str] = {}
    fn = wall.thread_owners if chats else wall.session_owners
    for part in _chunks(ids, _OWNERS_CHUNK):
        got = fn(engine, cmap, part)
        if got is None:
            raise RuntimeError("the folders could not be read")
        out.update(got)
    return out


def _read(platform: Any, days: int, now: datetime) -> _Read:
    """The window's usage rows and whose each one was. RAISES on a read
    fault (the caller answers with ``error``)."""
    from sqlmodel import select

    from ..core.db import session_scope
    from ..core.models import AgentRun, EventRecord
    from ..workmap import keys
    from ..workmap.clients import store
    from ..workmap.clients.store import ClientMap

    engine = platform.engine
    cutoff = now - timedelta(days=days)
    got = _Read()
    with session_scope(engine) as db:
        stmt = (
            select(
                AgentRun.id, AgentRun.session_id, AgentRun.parent_id, AgentRun.thread_id,
                AgentRun.provider, AgentRun.model, AgentRun.input_tokens, AgentRun.output_tokens,
                AgentRun.cost_usd, AgentRun.cost_source,
            )
            .where(AgentRun.created_at >= cutoff)  # type: ignore[operator]
            .order_by(AgentRun.created_at.desc())  # type: ignore[union-attr]
            .limit(RUNS_MAX + 1)
        )
        for seen, (rid, sid, pid, tid, prov, model, tin, tout, cost, source) in enumerate(db.exec(stmt)):
            if seen >= RUNS_MAX:
                # More rows than one read considers: the oldest are not
                # counted, and the answer says so.
                got.truncated = True
                break
            provider = str(prov or "")
            if provider.strip().lower() == "mock":
                continue  # the demo model: no AI model ran
            tin_i, tout_i = max(0, int(tin or 0)), max(0, int(tout or 0))
            try:
                stored = float(cost or 0.0)
            except (TypeError, ValueError):
                stored = 0.0
            if tin_i + tout_i <= 0 and stored <= 0:
                continue  # moved nothing (a probe, a refused call)
            got.runs.append(_Run(
                id=str(rid), session_id=str(sid or ""), parent_id=str(pid or ""),
                thread_id=_bare_thread(tid), provider=provider, model=str(model or ""),
                tokens=tin_i + tout_i, input_tokens=tin_i, output_tokens=tout_i,
                stored=stored, source=str(source or ""),
            ))
        asks: list[tuple[str, str]] = []
        estmt = (
            select(EventRecord.payload_json)
            .where(EventRecord.type == "interrupt.asked")
            .where(EventRecord.created_at >= cutoff)  # type: ignore[operator]
            .order_by(EventRecord.created_at.desc())  # type: ignore[union-attr]
            .limit(ASKS_MAX)
        )
        for payload in db.exec(estmt):  # one column: sqlmodel hands back the value
            try:
                data = json.loads(payload or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            asks.append((str(data.get("session_id") or "").strip(), _bare_thread(data.get("thread_id"))))

        run_sessions = {r.session_id for r in got.runs if r.session_id not in (CHAT_SESSION, STEP_SESSION)}
        sessions = sorted(run_sessions | {s for s, _ in asks if s and s not in (CHAT_SESSION, STEP_SESSION)})
        srows = _session_rows(db, sessions)
        # A helper run with no automation of its own belongs to its parent's:
        # AgentRun.parent_id, the runs in the window first, then a bounded
        # read of the ones before it (at most PARENT_HOPS rounds).
        by_run: dict[str, tuple[str, str]] = {r.id: (r.session_id, r.parent_id) for r in got.runs}
        missing = sorted({r.parent_id for r in got.runs if r.parent_id and r.parent_id not in by_run})
        for _hop in range(PARENT_HOPS):
            if not missing:
                break
            more = _parents(db, missing)
            by_run.update(more)
            missing = sorted({pid for _sid, pid in more.values() if pid and pid not in by_run})
        extra = sorted({s for s, _p in by_run.values() if s and s not in srows
                        and s not in (CHAT_SESSION, STEP_SESSION)})
        if extra:
            srows.update(_session_rows(db, extra))
        goal_ids = sorted({
            o.partition(":")[2].strip() for o in (v["origin"] for v in srows.values())
            if o.lower().startswith("goal:")
        })
        goals = _goal_names(db, goal_ids)
    #: A session's parent run (any of its runs that names one).
    parent_of: dict[str, str] = {}
    for sid, pid in by_run.values():
        if sid and pid:
            parent_of.setdefault(sid, pid)

    try:
        cmap = store.load_map(engine)
        if not isinstance(cmap, ClientMap):
            raise TypeError("not a client map")
    except Exception:  # noqa: BLE001 — no client is named; folders still read
        log.debug("usage by: the client map could not be read", exc_info=True)
        cmap = store.EMPTY_MAP
        got.clients_ok = False
    threads = sorted({r.thread_id for r in got.runs if r.session_id == CHAT_SESSION and r.thread_id}
                     | {t for s, t in asks if t and (not s or s == CHAT_SESSION)})
    thread_folders = _folders(engine, cmap, threads, chats=True) if threads else {}
    session_folders = _folders(engine, cmap, sessions, chats=False) if sessions else {}
    try:
        from ..providers.egress import local_only_thread_ids

        local_threads = local_only_thread_ids(engine)
    except Exception:  # noqa: BLE001
        local_threads = None
    if local_threads is None:
        # Unreadable: every chat counts as Local only (fail closed for a
        # reader that keeps Local only work home).
        local_threads = frozenset(threads)

    def _automation_of_session(sid: str) -> tuple[str, str, str]:
        """Its own automation, else the nearest parent's (a loop or a
        parent that cannot be read ends the walk: not an automation)."""
        seen: set[str] = set()
        current = sid
        for _ in range(PARENT_HOPS + 1):
            if not current or current in seen:
                break
            seen.add(current)
            row = srows.get(current)
            if row is not None:
                found = automation_of(row["origin"], goals)
                if found[0]:
                    return found
            parent_run = parent_of.get(current)
            if not parent_run:
                break
            current = by_run.get(parent_run, ("", ""))[0]
        return "", "", ""

    def _client(folder: str) -> tuple[str, str]:
        root = cmap.client_of(folder) if folder else None
        if root is None:
            return "", ""
        entry = cmap.names.get(root.client_id)
        name = str(entry[0]) if entry else root.name
        return root.client_id, name

    def _folder_who(folder: str) -> tuple[str, str]:
        shown = keys.home_folder(str(folder or "").strip())
        return shown, keys.folder_key(shown)

    for sid in sessions:
        row = srows.get(sid) or {}
        folder, fkey = _folder_who(session_folders.get(sid, ""))
        cid, cname = _client(folder)
        akey, akind, aname = _automation_of_session(sid)
        agent = str(row.get("agent") or "")
        got.who["s:" + sid] = Who(
            folder=folder, folder_key=fkey, client_id=cid, client_name=cname,
            auto_key=akey, auto_kind=akind, auto_name=aname,
            agent_key=agent.lower(), agent_label=_agent_words(agent) or NO_AGENT,
            local_only=bool(row.get("local_only")),
        )
    for tid in threads:
        folder, fkey = _folder_who(thread_folders.get(tid, ""))
        cid, cname = _client(folder)
        got.who["t:" + tid] = Who(
            folder=folder, folder_key=fkey, client_id=cid, client_name=cname,
            agent_key=CHAT_SESSION, agent_label=CHAT_AGENT, local_only=tid in local_threads,
        )
    # Asks count where that run or chat has model use in this window.
    used = {_key_of(r) for r in got.runs}
    for sid, tid in asks:
        key = ("s:" + sid) if sid and sid not in (CHAT_SESSION, STEP_SESSION) else ("t:" + tid if tid else "")
        if key and key in used:
            got.asks[key] = got.asks.get(key, 0) + 1
    return got


def _key_of(r: _Run) -> str:
    if r.session_id == CHAT_SESSION:
        return "t:" + r.thread_id if r.thread_id else "chat:"
    if r.session_id == STEP_SESSION:
        return "step:"
    return "s:" + r.session_id


def _who_of(read: _Read, r: _Run) -> Who:
    key = _key_of(r)
    who = read.who.get(key)
    if who is not None:
        return who
    if key == "chat:":
        # A reply with no saved chat (a new chat before its first save, the
        # browser's sidebar): no folder, but a chat.
        return Who(agent_key=CHAT_SESSION, agent_label=CHAT_AGENT)
    return NOBODY


def _row_key(group: str, who: Who) -> tuple[str, str, str, str]:
    """``(key, label, kind, folder)`` of the row ``who`` falls in."""
    if group == GROUP_FOLDER:
        if not who.folder_key:
            return "", NO_FOLDER, "", ""
        return who.folder_key, who.folder, "", who.folder
    if group == GROUP_CLIENT:
        if not who.client_id:
            return "", NO_CLIENT, "", ""
        return who.client_id, who.client_name, "", ""
    if group == GROUP_AUTOMATION:
        if not who.auto_key:
            return "", NOT_AUTOMATION, "", ""
        return who.auto_key, automation_label(who.auto_kind, who.auto_name), who.auto_kind, ""
    if not who.agent_key:
        return "", NO_AGENT, "", ""
    return who.agent_key, who.agent_label, "", ""


def usage_by(
    platform: Any,
    group: str,
    days: int = DAYS_DEFAULT,
    *,
    now: datetime | None = None,
    keep: "Callable[[Who], bool | str] | None" = None,
) -> dict[str, Any]:
    """The rows for ``group`` over the last ``days`` (see the module
    docstring). ``keep`` answers for each usage row's :class:`Who`: True or
    "" keeps it; False or a reason word drops it, counted in ``left_out``
    and ``left_out_by[reason]`` ("held" for False) and in no figure. A
    ``keep`` that raises drops the row. Never raises."""
    g = clean_group(group) or GROUP_FOLDER
    n = clamp_days(days)
    when = now or _now()
    out: dict[str, Any] = {
        "group": g, "days": n, "since": (when - timedelta(days=n)).isoformat(),
        "rows": [], "rows_total": 0, "rest": None, "totals": _Fig().view(),
        "truncated": False, "left_out": 0, "left_out_by": {}, "error": "",
    }
    try:
        read = _read(platform, n, when)
    except Exception:  # noqa: BLE001 — said in words, never a traceback
        log.warning("usage by %s could not be read", g, exc_info=True)
        out["error"] = "Usage could not be read just now. Nothing was changed."
        return out
    out["truncated"] = read.truncated
    if g == GROUP_CLIENT and not read.clients_ok:
        out["error"] = CLIENTS_UNREADABLE
        return out
    manager = getattr(platform, "providers", None)
    kinds: dict[tuple[str, str], str] = {}
    priced: dict[tuple[str, str], bool] = {}

    def _kind(provider: str, model: str) -> str:
        k = (provider, model)
        if k not in kinds:
            from .usage_view import usage_row_is_local

            if usage_row_is_local(provider, model, manager):
                kinds[k] = "free"
            elif pricing.is_list_price_equivalent(provider, model):
                kinds[k] = "plan"
            else:
                kinds[k] = "pay"
        return kinds[k]

    figs: dict[tuple[str, str, str, str], _Fig] = {}
    asked_for: dict[tuple[str, str, str, str], set[str]] = {}
    total = _Fig()
    #: ``who`` -> "" (kept) or the reason it was dropped.
    kept_cache: dict[Who, str] = {}
    for r in read.runs:
        who = _who_of(read, r)
        if keep is not None:
            why = kept_cache.get(who)
            if why is None:
                try:
                    said = keep(who)
                except Exception:  # noqa: BLE001 — unsure: not told
                    said = False
                why = "" if said is True or said == "" else ("held" if said is False else str(said))
                kept_cache[who] = why
            if why:
                out["left_out"] += 1
                out["left_out_by"][why] = out["left_out_by"].get(why, 0) + 1
                continue
        f = _Fig(runs=1)
        kind = _kind(r.provider, r.model)
        if kind == "free":
            f.free_tokens = r.tokens
        elif kind == "plan":
            f.plan_tokens = r.tokens
        else:
            usd = pricing.recorded_cost(r.provider, r.model, r.input_tokens, r.output_tokens, r.stored)
            f.usd = max(0.0, float(usd or 0.0))
            f.pay_tokens = r.tokens
            pk = (r.provider, r.model)
            if pk not in priced:
                priced[pk] = pricing.is_priced(r.provider, r.model)
            if f.usd <= 0 and r.source != pricing.SOURCE_VENDOR and not priced[pk]:
                f.unknown_tokens = r.tokens
            else:
                f.paid_rows = 1
                f.measured_rows = 1 if r.source == pricing.SOURCE_VENDOR else 0
        rk = _row_key(g, who)
        figs.setdefault(rk, _Fig()).add(f)
        total.add(f)
        asked_for.setdefault(rk, set()).add(_key_of(r))
    for rk, keys_ in asked_for.items():
        n_asks = sum(read.asks.get(k, 0) for k in keys_)
        figs[rk].asks = n_asks
        total.asks += n_asks

    def _weight(item: tuple[tuple[str, str, str, str], _Fig]) -> tuple:
        (key, label, _k, _f), fig = item
        return (key == "", -round(fig.usd, 6), -(fig.plan_tokens + fig.free_tokens + fig.pay_tokens), label.lower())

    ordered = sorted(figs.items(), key=_weight)
    rows = [{"key": key, "label": label, "kind": kind, "folder": folder, **fig.view()}
            for (key, label, kind, folder), fig in ordered]
    out["rows_total"] = len(rows)
    if len(rows) > ROWS_MAX:
        rest = _Fig()
        for _rk, fig in ordered[ROWS_MAX:]:
            rest.add(fig)
        out["rest"] = {"count": len(rows) - ROWS_MAX, **rest.view()}
        rows = rows[:ROWS_MAX]
    out["rows"] = rows
    out["totals"] = total.view()
    return out


__all__ = [
    "AUTOMATION_KINDS", "CHAT_AGENT", "DAYS_DEFAULT", "DAYS_MAX", "DAYS_MIN", "GROUPS",
    "GROUP_AGENT", "GROUP_AUTOMATION", "GROUP_CLIENT", "GROUP_FOLDER", "NOBODY", "NOT_AUTOMATION",
    "NO_AGENT", "NO_CLIENT", "NO_FOLDER", "OTHER_APPS", "QUEUED_TASKS", "Who", "automation_label",
    "automation_of", "clamp_days", "clean_group", "usage_by",
]
