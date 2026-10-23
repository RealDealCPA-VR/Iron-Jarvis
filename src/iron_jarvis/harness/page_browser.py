"""THE AGENT'S BROWSER in a Build session (v1.376.0, IJ harness part 2,
Phase 5 — ``docs/IJ-HARNESS-PLAN-2.md``).

The user's decision (2026-10-22, plan decision 5, final): an INVISIBLE browser
the agent drives — the installed Microsoft Edge, a throwaway profile — with
its latest view shown in the Browser pane. v1.371.0's ``page_screenshot``
opened one page per call and threw the browser away; here the agent opens a
page, clicks, types, reads the console and the failed requests, and looks
again, in ONE browser that stays open for the session (``harness/page_act``
holds the tools).

ONE BROWSER PER SESSION. Keyed by the session's tool session id
(``build:<id>``); a helper agent of the session uses the SAME browser (its
calls run under its own session id, so :func:`browser_key` reads the helper's
parent) — never a second Edge per helper. Opened on first use, kept between
turns, CLOSED when the session is archived or deleted (``procs.end_session``
→ :func:`_end_hook`), after :data:`IDLE_CLOSE_S` without an action, at daemon
shutdown (``procs.end_all``), and when the user presses Stop (below).

WHO OWNS THE PROCESSES. Playwright starts its driver (``node.exe``) itself
(``asyncio.create_subprocess_exec`` inside the playwright package), so the
spawn cannot go through ``core/procs``' door; the driver is ADOPTED into a
kill-on-close Job right after it starts and before it is asked to launch
anything (``procs.adopt`` — it starts nothing on its own), so Edge and the
processes Edge starts are born inside the job (measured: driver, browser, gpu,
network, storage, renderers, crash reporter — all members). ONE EXCEPTION,
measured in the v1.376.0 review: ~2 s in, Edge has an ``identity_helper.exe``
that is NOT in the job (Windows starts it for Edge, in a job of its own, even
when ours allows no breakaway); it exits ~0.2 s after Edge goes. So the
browser's process list is the job's members AND the driver's whole tree, and
a close kills what the tree held outside the job too. Closing the browser
closes the context, stops the driver and then TERMINATES the job; a daemon
that dies for any reason closes the job's only handle and the kernel ends
everything in it. ``core/procs.ALLOWED_RAW_SPAWNS`` is unchanged: no file of
ours spawns anything here.

AT MOST :data:`MAX_BROWSERS` are open at once (v1.376.0 review): a new one
closes the least recently used browser that is not acting.

THE THREAD. Playwright's ASYNC API runs on ONE dedicated thread with its own
event loop (:class:`_Host`; a Proactor loop on Windows, which starts
subprocesses from any thread), shared by every session's browser. The
daemon's loop only ever awaits a future from it (``run_coroutine_threadsafe``
+ ``wrap_future``), so nothing here blocks the daemon's loop, and a cancel on
the daemon's side cancels the action's task on the browser thread. Decided
over the sync API: a sync call cannot be interrupted from another thread
(only killed), and its events arrive only while a call runs.

ONE ACTION AT A TIME per session (an ``asyncio.Lock`` on the browser thread;
a second action waits up to :data:`LOCK_WAIT_S` and then says the browser is
busy). Every action has a time limit that says so in words.

THE ADDRESS RULES (v1.371.0's, :func:`judge`), enforced on EVERY navigation:
the Browser pane's dev server (the turn's ``preview_url``, same origin) and
any address the user allowed for this browser are opened without asking;
another address on this PC or the user's network ASKS (``page_open``'s spend
check — "Allow once"; allowed, it stays allowed until this browser closes);
a public site is refused in words. A navigation the PAGE starts (a link, a
form, ``location = …``, a meta refresh, a popup) is checked by route
interception BEFORE its request is sent and stopped there when the rules
would not open it. A SERVER REDIRECT is followed by the browser without
asking the route (measured with Playwright 1.60), so it is caught when the
page arrives (``framenavigated``): the browser leaves that page at once and
returns to the last page it was allowed on — the request to that address was
made, but the page is never read, shown or acted on, and the tool result
says where it tried to go. Every action also re-checks the page it acts on.

STOP. A Stop never leaves an action running and never leaks a process: it
CLOSES the session's browser — at once when an action is under way (the tool
races its action against the turn's Stop, and a cancelled call closes it
too, because Playwright's driver would otherwise finish a click or a page
load on its own), and when the turn ends stopped (``routes/build``). The next
turn opens a fresh browser; the Browser pane keeps the last view.

THE VIEW. After every action the browser's address, title and a JPEG of what
it shows are kept in memory per session (:meth:`AgentBrowsers.view` /
:meth:`AgentBrowsers.shot`, newest :data:`MAX_VIEWS` sessions) for the
Browser pane (``GET /build/sessions/{id}/browser``).

Nothing here imports Playwright at module import; nothing raises into a
caller except the tools' own words (:class:`BrowserProblem`).
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import importlib
import itertools
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from ..core import procs
from ..core.logging import get_logger
from .preview import local_address, origin

log = get_logger(__name__)

#: A session's browser closes after this many seconds with no action.
IDLE_CLOSE_S = 600.0
#: Test / scratch hook: a shorter idle time, in seconds (clamped 2 .. 1 day).
IDLE_ENV = "IRONJARVIS_PAGE_BROWSER_IDLE_S"
#: The idle watcher looks at most this often.
IDLE_CHECK_MAX_S = 30.0
#: Starting Edge (and its driver).
START_TIMEOUT_S = 45.0
#: Opening a page (a dev server's first compile can take a while).
OPEN_TIMEOUT_S = 45.0
#: A click, a typed field, reading the page.
ACTION_TIMEOUT_S = 15.0
#: Past an action's own time limit, how long before the browser is taken to
#: be wedged and is closed (Playwright's own limits normally end it first).
HARD_BOUND_EXTRA_S = 10.0
#: After an action, how long a page that started loading may take to load.
SETTLE_MAX_S = 5.0
#: Closing the browser politely before its job is terminated.
CLOSE_TIMEOUT_S = 8.0
#: An action waits this long for the one before it in the same session.
LOCK_WAIT_S = 60.0
#: The window the agent's pages are drawn at.
VIEWPORT = {"width": 1280, "height": 800}
#: Lines kept per kind (console errors, failed requests, dialogs) between reads.
MAX_LOG_LINES = 50
#: One kept line, at most.
MAX_LINE_CHARS = 400
#: The visible text a read returns, at most.
MAX_TEXT_CHARS = 12_000
#: The start of the page an OPEN hands back (page_read has the rest).
OPEN_TEXT_HEAD = 1_000
#: Elements a read lists (what can be clicked or typed into).
MAX_ELEMENTS = 40
#: The pane's picture.
VIEW_JPEG_QUALITY = 70
#: Sessions whose last view is kept in memory.
MAX_VIEWS = 32
#: v1.376.0 review DECISION (item 4): at most this many agent browsers are
#: open at once, across every Build session (each Edge is ~8 processes and a
#: few hundred MB). Opening one more closes the session browser used least
#: recently that is not acting (its pane says why); when every one is acting,
#: the open is refused in words. One session's helpers share its browser.
MAX_BROWSERS = 4
#: The throwaway folder's name (in the SYSTEM temp folder).
WORK_PREFIX = "ij-agent-browser-"
#: A leftover folder older than this (a daemon that died) is removed.
STALE_WORK_S = 6 * 3600.0

#: Edge's own switches, beside Playwright's defaults: no first-run page, no
#: sign-in or sync, no extensions, no sound.
EDGE_ARGS = (
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-sync",
    "--disable-extensions",
    "--mute-audio",
)

NO_EDGE = (
    "The agent's browser needs Microsoft Edge on this PC, and Edge was not found. "
    "Install Microsoft Edge (https://www.microsoft.com/edge) to let the agent open pages."
)
NO_PLAYWRIGHT = (
    "The agent's browser is missing from this install of Iron Jarvis (its browser driver, "
    "Playwright, is not there). Reinstall Iron Jarvis to bring it back."
)
NOT_LOCAL = (
    "The agent's browser only opens pages on this PC or your own network "
    "(http://localhost:…, http://127.0.0.1:…, a 192.168.… or .local address). "
    "It does not open public websites."
)
NO_PAGE = "No page is open in the agent's browser. Call page_open with the page's address first."
#: v1.376.0 review: Windows would not let Iron Jarvis hold the browser's
#: processes (a kill-on-close job), so none is started.
NO_JOB = (
    "The agent's browser was not started: Iron Jarvis could not hold its processes so that "
    "they end when Iron Jarvis does (Windows refused a job object for them). Restart Iron "
    "Jarvis and try again."
)
STOPPED = "Stopped: you pressed Stop, so the agent's browser was closed."
#: v1.376.0 review: Edge closed under the agent (by the user, a crash).
DIED = (
    "The agent's browser had stopped (Edge was closed or crashed), so it was closed. "
    "Call page_open to start a fresh one."
)
FRESH_AFTER_DIED = (
    "The agent's browser had stopped (Edge was closed or crashed), so a fresh one was "
    "opened: what the page held before (typed text, a sign-in) is gone."
)
TOO_MANY = (
    f"{MAX_BROWSERS} Build sessions already have the agent's browser open and each is in the "
    "middle of an action, so another cannot start now. Try again in a moment."
)
BUSY = (
    "The agent's browser is still busy with another action in this session; "
    f"it did not free up within {LOCK_WAIT_S:g} s. Try again."
)


class BrowserProblem(Exception):
    """A failure the tools say in words (no browser, a refused address, a
    target that matched nothing, a time limit)."""


# --------------------------------------------------------------------------- #
# the address rules (pure)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Verdict:
    """What the rules say about one address: ``ok`` (open it), ``ask`` (a
    local address the user has not allowed for this browser), ``refuse``
    (not on this PC or the user's network)."""

    kind: str
    url: str = ""
    origin: str = ""


def normalize(url: Any) -> str:
    """An address as typed by a model: ``localhost:5173/x`` reads as
    ``http://localhost:5173/x``. Anything else is returned stripped."""
    raw = str(url or "").strip()
    if raw and "://" not in raw and not raw.startswith(("about:", "data:", "javascript:")):
        raw = "http://" + raw
    return raw


def judge(url: str, allowed: "set[str] | frozenset[str]", preview_origin: str) -> Verdict:
    """The address rules (v1.371.0's) for the agent's browser. ``about:blank``
    is always fine (it is nothing); a public or non-http address is refused;
    the Browser pane's dev server and an origin the user allowed for this
    browser are fine; any other local address asks. Pure; never raises."""
    raw = str(url or "").strip()
    if raw == "about:blank":
        return Verdict("ok", raw, "")
    clean = local_address(raw)
    if not clean:
        return Verdict("refuse")
    org = origin(clean)
    if org and (org == preview_origin or org in allowed):
        return Verdict("ok", clean, org)
    return Verdict("ask", clean, org)


def where(url: Any) -> str:
    """An address as the user reads it: host[:port] and path, no scheme, no
    query string or fragment (they can carry tokens), masked and cut —
    "localhost:5173/cart"."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""  # about:blank, a browser's own error page: no address to name
    host = parts.netloc.rsplit("@", 1)[-1]
    path = parts.path if parts.path not in ("", "/") else ""
    return _mask(host + path)[:120]


def blocked_words(kind: str, url: str) -> str:
    """The sentence for a navigation the rules stopped."""
    shown = where(url) or "another address"
    if kind == "refuse":
        return (
            f"The page tried to go to {shown}, which is not on this PC or your network; "
            "the agent's browser stopped it there (it does not open public websites)."
        )
    return (
        f"The page tried to go to {shown}, another address on this PC or your network that "
        "has not been allowed in this session; the agent's browser stopped it there. To go "
        "there, call page_open with that address (it asks the user first)."
    )


def _mask(text: Any) -> str:
    try:
        from ..detections.redact import mask

        return mask(str(text or ""))
    except Exception:  # noqa: BLE001 — never fails a line
        return str(text or "")


def _line(text: Any) -> str:
    flat = " ".join(str(text or "").split())
    flat = _mask(flat)
    return flat if len(flat) <= MAX_LINE_CHARS else flat[: MAX_LINE_CHARS - 1] + "…"


# --------------------------------------------------------------------------- #
# typing: never a password, a payment field or a file picker (pure)
# --------------------------------------------------------------------------- #

#: DECISION (v1.376.0): REFUSED, never carded. The agent works on the user's
#: own dev server; a card would let a model type a secret it got from
#: somewhere because a person clicked Allow on a busy afternoon.
TYPE_REFUSED_PASSWORD = (
    "The agent's browser does not type into password fields. If the page needs a sign-in, "
    "ask the user to sign in themselves, or to give the dev server a test account."
)
TYPE_REFUSED_PAYMENT = (
    "The agent's browser does not type into payment fields (card numbers, security codes, "
    "bank details)."
)
TYPE_REFUSED_FILE = "The agent's browser does not choose files for a page (a file picker)."
TYPE_REFUSED_SECRET = (
    "That text looks like a password, a key or a token, and the agent's browser does not "
    "type secrets into a page."
)


def typing_refusal(facts: dict[str, Any], text: str) -> str:
    """The sentence typing into this field is refused with, or "". ``facts``
    is what the page said about the element (:meth:`EdgeBackend.field`):
    ``tag``, ``type``, ``autocomplete`` and ``words`` (its label, name, id,
    placeholder). The vocabularies are computer use's (one list:
    ``computeruse/policy``). Pure."""
    from ..computeruse import policy as P

    kind = str(facts.get("type") or "").lower()
    auto = str(facts.get("autocomplete") or "").lower()
    words = str(facts.get("words") or "").lower()
    tokens = set(auto.split())
    if kind == "file":
        return TYPE_REFUSED_FILE
    if (
        kind in P._PASSWORD_FIELD_TYPES
        or tokens & P._PASSWORD_AUTOCOMPLETE
        or any(w in words for w in P._PASSWORD_WORDS)
    ):
        return TYPE_REFUSED_PASSWORD
    if (
        kind in P._PAYMENT_FIELD_TYPES
        or any(t.startswith("cc-") for t in tokens)
        or any(w in words for w in P._PAYMENT_WORDS)
    ):
        return TYPE_REFUSED_PAYMENT
    if text and _mask(text) != text:
        return TYPE_REFUSED_SECRET
    return ""


# --------------------------------------------------------------------------- #
# clicking: a button that reads as deleting, sending or paying asks (pure)
# --------------------------------------------------------------------------- #

#: DECISION (v1.376.0 review, item 3): a click whose button READS as deleting,
#: sending or paying asks the user ONCE, every time — the browser add-on's
#: risk door and computer use read the same words (D12: ONE vocabulary,
#: ``computeruse/policy``), and the agent's browser must not be weaker where
#: they apply: a local admin page the user allowed (a router, a NAS, a deploy
#: tool), or a dev server whose "Delete" reaches a shared database. The words
#: are the BUTTON's (read off the page), never the model's target, and they
#: are read again at the click.
CLICK_NOT_ASKED = (
    "That click was not made: the button reads \u201c{words}\u201d, which asks the user first, "
    "and the user was not asked about it as it reads now (the page may have changed it). Call "
    "page_click again to ask."
)
#: How long a button's checked words stand for its click.
CHECK_TTL_S = 300.0
#: A check waits this long for an action of the session to finish.
CHECK_LOCK_WAIT_S = 10.0


def risky_click_word(words: Any) -> str:
    """The word of computer use's destructive / payment vocabulary a
    button's words hold ("delete"), or "" (pure)."""
    from ..computeruse import policy as P

    label = " ".join(str(words or "").split()).lower()
    if not label:
        return ""
    for word in (*P._DESTRUCTIVE_WORDS, *P._PAYMENT_WORDS):
        if word in label:
            return word
    return ""


def _target_key(target: Any) -> str:
    try:
        return json.dumps(target, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001
        return str(target)


# --------------------------------------------------------------------------- #
# Edge, Playwright, the throwaway folder
# --------------------------------------------------------------------------- #


def edge_executable() -> str:
    """The installed Microsoft Edge ("" when there is none). The doctor's own
    list of places (``onboarding.doctor._browser_candidates``), Edge only —
    the user's decision names Edge. Reached by module path (``onboarding``
    re-exports a FUNCTION named ``doctor``). BLOCKING (file checks)."""
    try:
        doctor = importlib.import_module("iron_jarvis.onboarding.doctor")
        candidates = list(doctor._browser_candidates())
    except Exception:  # noqa: BLE001
        candidates = [shutil.which("msedge") or ""]
    for path in candidates:
        if not path:
            continue
        base = os.path.basename(path).lower()
        if "msedge" not in base and "microsoft edge" not in path.lower():
            continue
        if os.path.isfile(path):
            return str(path)
    return ""


def playwright_problem() -> str:
    """"" when Playwright and its driver are here, else why not (in words).
    BLOCKING (file checks)."""
    try:
        from playwright._impl._driver import compute_driver_executable  # noqa: PLC0415

        node, cli = compute_driver_executable()
    except Exception:  # noqa: BLE001
        return NO_PLAYWRIGHT
    if not os.path.isfile(cli) or not os.path.isfile(node):
        return NO_PLAYWRIGHT
    return ""


def _new_work() -> Path:
    """BLOCKING: the browser's throwaway folder, in the SYSTEM temp folder —
    never in Iron Jarvis's home (a running browser locks ~60 profile files
    and a backup walks the whole home, the v1.337.1 / v1.371.0 lesson) and
    never the user's Edge profile. The profile is seeded so a download, if
    one ever started, lands in this folder too (downloads are refused
    anyway, ``accept_downloads=False``)."""
    work = Path(tempfile.mkdtemp(prefix=WORK_PREFIX))
    default = work / "profile" / "Default"
    default.mkdir(parents=True, exist_ok=True)
    (work / "downloads").mkdir(exist_ok=True)
    (work / "tmp").mkdir(exist_ok=True)  # the driver's and Edge's temp folder
    (default / "Preferences").write_text(
        json.dumps({"download": {
            "default_directory": str(work / "downloads"),
            "prompt_for_download": False,
            "directory_upgrade": True,
        }}),
        encoding="utf-8",
    )
    return work


def _remove_work(work: Path) -> None:
    """BLOCKING: delete the throwaway folder (a stopped browser's files can
    stay locked for a moment, so a few tries). Never raises."""
    for _ in range(15):
        shutil.rmtree(work, ignore_errors=True)
        if not work.exists():
            return
        time.sleep(0.2)


def _sweep_stale(now: float | None = None, folder: "Path | None" = None) -> int:
    """BLOCKING: remove leftover throwaway folders of a daemon that died
    (their Edge died with its job, so nothing holds them). Only folders older
    than :data:`STALE_WORK_S` that can be RENAMED (Windows refuses to rename a
    folder with a file open inside — another running Iron Jarvis's browser).
    Returns how many were removed. Never raises."""
    base = Path(folder or tempfile.gettempdir())
    now = time.time() if now is None else now
    removed = 0
    try:
        rows = list(base.glob(WORK_PREFIX + "*"))
    except OSError:
        return 0
    for old in rows:
        try:
            if not old.is_dir() or now - old.stat().st_mtime < STALE_WORK_S:
                continue
            gone = old.with_name(old.name + ".gone")
            os.rename(old, gone)
        except OSError:
            continue
        shutil.rmtree(gone, ignore_errors=True)
        removed += 1
    return removed


#: v1.376.0 review: a request a Local only browser refused (its proxy).
LOCAL_ONLY_REFUSED = "stopped: not on this PC or your network (Local only)"
#: Chromium's words for a request its proxy would not carry.
_PROXY_REFUSALS = ("ERR_PROXY_CONNECTION_FAILED", "ERR_TUNNEL_CONNECTION_FAILED")
LOCAL_ONLY_CHANGED = (
    "Local only was turned {state} after the agent's browser opened, so that browser was "
    "closed: the browser is started for the session's Local only setting."
)
LOCAL_ONLY_FRESH = (
    "Local only was turned {state} after the agent's browser opened, so a fresh one was "
    "started for it."
)


def local_only_bypass() -> str:
    """The addresses a LOCAL ONLY agent browser reaches (v1.376.0 review,
    DECISION for item 2b), as Chromium's ``--proxy-bypass-list``: built from
    ``providers/egress``' ONE definition of "this PC or your own network" —
    its address blocks, ``localhost`` and its sub-names, single-label names
    (``<local>``) and the private name suffixes — judged by NAME, never by a
    DNS answer, exactly like ``egress.private_host``. Everything else goes to
    a proxy that refuses it (:class:`EdgeBackend`), so it never leaves."""
    from ..providers import egress

    rules = ["localhost", "*.localhost", "<local>"]
    rules += [str(net) for net in egress._PRIVATE_NETS]
    rules += ["*" + suffix for suffix in egress.PRIVATE_SUFFIXES]
    return ";".join(rules)


def _local_only_now() -> bool:
    """Whether the work in flight in THIS context is Local only (the turn's
    or the helper's scope). Unknown reads as Local only — the stricter."""
    try:
        from ..providers.egress import current_scope

        return bool(current_scope().on)
    except Exception:  # noqa: BLE001
        return True


#: The temp folder the NEXT Playwright driver started in this context gets
#: (:func:`_driver_env_hook`); "" = the daemon's own.
_DRIVER_TEMP: ContextVar[str] = ContextVar("ij_page_driver_temp", default="")


def _driver_env_hook() -> None:
    """v1.376.0 review: Playwright's driver makes a ``playwright-artifacts-*``
    folder in ITS temp folder for each browser, and a browser that crashed or
    a daemon that was killed left it in the PC's temp folder for good. The
    driver's environment is Playwright's ``get_driver_env()`` (a copy of the
    daemon's), so it is wrapped ONCE to give the driver — and so Edge — the
    browser's own throwaway folder as TEMP/TMP while :data:`_DRIVER_TEMP` is
    set (only around this module's start; computer use's Playwright and
    every other caller see Playwright's own environment unchanged). Never
    the daemon's ``os.environ``: other threads start children from it."""
    try:
        from playwright._impl import _transport  # noqa: PLC0415 — lazy
    except Exception:  # noqa: BLE001 — no Playwright: nothing starts anyway
        return
    current = _transport.get_driver_env
    if getattr(current, "_ij_temp", False):
        return

    def get_driver_env() -> dict:
        env = current()
        temp = _DRIVER_TEMP.get()
        if temp:
            env["TEMP"] = env["TMP"] = temp
        return env

    get_driver_env._ij_temp = True  # type: ignore[attr-defined]
    _transport.get_driver_env = get_driver_env


def idle_close_s() -> float:
    """The idle time a browser is closed after (:data:`IDLE_CLOSE_S`, or the
    scratch hook :data:`IDLE_ENV`)."""
    raw = os.environ.get(IDLE_ENV, "").strip()
    if raw:
        try:
            return max(2.0, min(86_400.0, float(raw)))
        except ValueError:
            pass
    return float(IDLE_CLOSE_S)


# --------------------------------------------------------------------------- #
# the real browser: Edge through Playwright (runs on the browser thread)
# --------------------------------------------------------------------------- #

_WORDS_OF_ELEMENTS = """
(max) => {
  const sel = 'a[href],button,input,select,textarea,summary,[role=button],[role=link],'
    + '[role=tab],[role=checkbox],[role=menuitem],[role=switch],[contenteditable=true]';
  const roleOf = (el) => {
    const r = el.getAttribute('role');
    if (r) return r;
    const t = el.tagName;
    if (t === 'A') return 'link';
    if (t === 'BUTTON' || t === 'SUMMARY') return 'button';
    if (t === 'SELECT') return 'combobox';
    if (t === 'TEXTAREA') return 'textbox';
    if (t === 'INPUT') {
      const k = (el.getAttribute('type') || 'text').toLowerCase();
      if (k === 'checkbox' || k === 'radio') return k;
      if (k === 'submit' || k === 'button' || k === 'reset') return 'button';
      if (k === 'password') return 'password field';
      if (k === 'file') return 'file picker';
      return 'textbox';
    }
    return 'textbox';
  };
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    if (out.length >= max) break;
    const box = el.getBoundingClientRect();
    if (!box.width || !box.height) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    const isInput = el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT';
    const label = el.getAttribute('aria-label')
      || (el.labels && el.labels[0] && el.labels[0].innerText)
      || el.getAttribute('placeholder')
      || (isInput ? '' : el.innerText)
      || ((el.tagName === 'INPUT' && ['submit', 'button'].includes((el.type || '').toLowerCase())) ? el.value : '')
      || el.getAttribute('title') || el.getAttribute('name') || '';
    out.push({role: roleOf(el), name: String(label).replace(/\\s+/g, ' ').trim().slice(0, 80),
              disabled: !!el.disabled});
  }
  return out;
}
"""

#: The facts of the field typing will REALLY reach. Playwright's fill() does
#: not type into the element it was given when that is not a field: it
#: follows it (``retarget(node, 'follow-label')`` in Playwright's injected
#: script) — from inside a button-like element to it, and from a <label>, or
#: anything inside one, to the label's control. v1.376.0 review: the facts were
#: read off the element GIVEN, so a target naming the <label> of a password
#: field (or a span inside it) read as "no type, no words" and the password
#: was typed. The same walk is made here, and the words of both elements are
#: kept.
_FIELD_FACTS = """
(el) => {
  let t = el;
  if (!t.matches('input, textarea, select') && !t.isContentEditable) {
    t = t.closest('button, [role=button], [role=checkbox], [role=radio]') || t;
  }
  if (!t.matches('a, input, textarea, button, select, [role=link], [role=button], [role=checkbox], [role=switch], [role=radio]')
      && !t.isContentEditable) {
    const lab = t.closest('label');
    if (lab && lab.control) t = lab.control;
  }
  const wordsOf = (e) => [e.getAttribute('aria-label'),
    e.labels && e.labels[0] && e.labels[0].innerText,
    e.getAttribute('placeholder'), e.getAttribute('name'), e.id, e.getAttribute('title')];
  const words = [...wordsOf(t), ...(t === el ? [] : wordsOf(el))].filter(Boolean).join(' ');
  const kind = (t.getAttribute('type') || (t.tagName === 'INPUT' ? t.type : '') || '').toLowerCase();
  return {
    tag: t.tagName.toLowerCase(),
    type: kind,
    autocomplete: (t.getAttribute('autocomplete') || '').toLowerCase(),
    words: words.toLowerCase(),
  };
}
"""

#: What a click target READS as: its own words and those of the button or
#: link it sits in (a click on a span inside "Delete account" presses that
#: button) — visible text, aria-label, a submit's value, title, alt.
_CLICK_WORDS = """
(el) => {
  const host = el.closest('button, [role=button], a, [role=link], input[type=submit], input[type=button], summary') || el;
  const bits = [];
  for (const e of new Set([el, host])) {
    bits.push(e.getAttribute('aria-label'), e.innerText, e.getAttribute('title'), e.getAttribute('alt'));
    if (e.tagName === 'INPUT') bits.push(e.value);
  }
  return bits.filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim().slice(0, 200);
}
"""

#: Playwright's network errors, in words.
_NET_WORDS = {
    "ERR_CONNECTION_REFUSED": "nothing answered there (is the dev server running?)",
    "ERR_NAME_NOT_RESOLVED": "that name does not resolve to anything",
    "ERR_ADDRESS_UNREACHABLE": "that address cannot be reached",
    "ERR_CONNECTION_TIMED_OUT": "the connection timed out",
    "ERR_CONNECTION_RESET": "the server dropped the connection",
    "ERR_EMPTY_RESPONSE": "the server answered with nothing",
    "ERR_BLOCKED_BY_CLIENT": "the address rules stopped it",
    "ERR_ABORTED": "the page did not load (it may have started a download, which is not allowed)",
}


def playwright_words(exc: BaseException, *, what: str, limit_s: float) -> str:
    """A Playwright failure in words: its time limit, a known network error,
    else its first line (masked, cut)."""
    name = type(exc).__name__
    first = str(exc).strip().splitlines()[0] if str(exc).strip() else name
    if name == "TimeoutError" or "Timeout" in first and "exceeded" in first:
        return f"{what} did not finish within {limit_s:g} s."
    for code, words in _NET_WORDS.items():
        if code in first:
            return f"{what} failed: {words} ({code})."
    return f"{what} failed: {_line(first)[:300]}"


def _driver_pid(cm: Any) -> int:
    """The Playwright driver's pid (``node.exe``), read off the context
    manager's transport; 0 when the shape is not the one measured."""
    try:
        return int(cm._connection._transport._proc.pid)
    except Exception:  # noqa: BLE001
        return 0


class EdgeBackend:
    """One session's Edge, driven by Playwright's async API. Every coroutine
    runs on the browser thread; :meth:`kill` is the one method another
    thread may call (it terminates the job)."""

    def __init__(self, *, edge: str, work: Path, guard: Callable[[str], Verdict],
                 note: Callable[[str, str], None], background: bool = False,
                 extra_args: "tuple[str, ...]" = (), local_only: bool = False) -> None:
        self._edge = edge
        self._work = work
        self._guard = guard
        self._note = note
        self._background = background
        self._extra = tuple(extra_args)
        #: v1.376.0 review: a Local only browser runs behind a proxy that
        #: refuses every public address (:func:`local_only_bypass`). The
        #: "proxy" is a port on 127.0.0.1 this browser holds BOUND and never
        #: listens on: every connection to it is refused at once, and no
        #: other program can take the port while the browser lives.
        self.local_only = bool(local_only)
        self._refuser: Any = None
        self._cm: Any = None
        self._pw: Any = None
        self._ctx: Any = None
        self._page: Any = None
        self._job: Any = None
        self._driver = 0
        self._known: set[int] = set()
        #: The tree's processes as psutil objects (the no-job fallback kills
        #: through these: psutil refuses a pid that was reused since).
        self._procs: dict[int, Any] = {}
        self._killed = False
        self._kill_lock = threading.Lock()
        #: Pages being sent away by the rules (``_leave``), awaited by
        #: :meth:`quiet` before the return to the last allowed page.
        self._leaving: set[asyncio.Task] = set()
        #: The context closed on its own (Edge exited: the user closed it, a
        #: crash) — v1.376.0 review: the browser is then replaced, never used.
        self._ctx_closed = False
        #: Every process seen in the driver's tree (psutil objects, which
        #: refuse a reused pid) — one of Edge's lives outside the job
        #: (measured), and a polite close takes its parent away before the
        #: kill could find it by the tree (v1.376.0 review).
        self._tree_seen: dict[int, Any] = {}

    # -- life ----------------------------------------------------------------
    async def start(self) -> None:
        from playwright.async_api import async_playwright  # noqa: PLC0415 — lazy

        _driver_env_hook()
        token = _DRIVER_TEMP.set(str(self._work / "tmp"))
        try:
            self._cm = async_playwright()
            self._pw = await self._cm.start()
        finally:
            _DRIVER_TEMP.reset(token)
        self._driver = _driver_pid(self._cm)
        if self._driver:
            # BEFORE the driver is asked to launch anything: every process it
            # starts from here on is born in the job (procs.adopt).
            self._job = await asyncio.to_thread(procs.adopt, self._driver, background=self._background)
            self._known.add(self._driver)
        if self._job is None and sys.platform == "win32":
            # v1.376.0 review DECISION: on Windows the job is what ends the
            # browser when the daemon is KILLED (no code of ours runs then).
            # No job — no job object, access refused, a driver whose pid is
            # not where it was measured — means no Edge: the driver stops
            # here and the tool says why.
            await self._stop_driver()
            raise BrowserProblem(NO_JOB)
        args = [*EDGE_ARGS, *self._extra]
        if self.local_only:
            args += self._refusing_proxy()
        self._ctx = await self._pw.chromium.launch_persistent_context(
            str(self._work / "profile"),
            executable_path=self._edge,
            headless=True,
            viewport=dict(VIEWPORT),
            accept_downloads=False,
            downloads_path=str(self._work / "downloads"),
            ignore_https_errors=True,
            handle_sigint=False,
            handle_sigterm=False,
            handle_sighup=False,
            args=args,
            timeout=START_TIMEOUT_S * 1000,
        )
        await self._ctx.route("**/*", self._route)
        self._ctx.on("close", lambda *_a: setattr(self, "_ctx_closed", True))
        self._ctx.on("page", self._adopt_page)
        for page in list(self._ctx.pages):
            self._adopt_page(page)
        if self._page is None:
            self._adopt_page(await self._ctx.new_page())
        self._remember(self._members())

    async def _stop_driver(self) -> None:
        """The driver alone (nothing launched yet): Playwright's own stop,
        then its process if it is still there."""
        driver = _tree_procs(self._driver)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._cm.__aexit__(None, None, None), CLOSE_TIMEOUT_S)
        self._cm = self._pw = None
        await asyncio.to_thread(_kill_procs, driver)

    def _refusing_proxy(self) -> list[str]:
        """Edge's switches for Local only: every address outside this PC and
        the user's network goes to a proxy that refuses it (a request, a
        WebSocket, a beacon, a server redirect's next hop, a preconnect) —
        measured: none of them reaches the server, while this PC still
        answers. The proxy is Chromium's own setting, so it holds below
        anything a page can do; the address is passed as switches, not
        Playwright's ``proxy`` option (which also proxies loopback)."""
        import socket

        refuser = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        refuser.bind(("127.0.0.1", 0))  # bound, never listening: refuses
        self._refuser = refuser
        port = refuser.getsockname()[1]
        return [f"--proxy-server=http://127.0.0.1:{port}", f"--proxy-bypass-list={local_only_bypass()}"]

    def _drop_refuser(self) -> None:
        refuser, self._refuser = self._refuser, None
        if refuser is not None:
            with contextlib.suppress(Exception):
                refuser.close()

    def _remember(self, pids: "list[int]") -> None:
        self._known.update(pids)
        try:
            import psutil

            for pid in pids:
                if pid not in self._procs:
                    with contextlib.suppress(Exception):
                        self._procs[pid] = psutil.Process(pid)
        except Exception:  # noqa: BLE001
            pass

    def _note_tree(self) -> list[int]:
        rows = _tree_procs(self._driver)
        for proc in rows:
            self._tree_seen.setdefault(proc.pid, proc)
        return [proc.pid for proc in rows]

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(self._note_tree)
        try:
            if self._ctx is not None:
                await self._ctx.close()
        finally:
            self._ctx = None
            if self._cm is not None:
                with contextlib.suppress(Exception):
                    await self._cm.__aexit__(None, None, None)
            self._cm = self._pw = None

    def kill(self) -> list[int]:
        """Thread-safe, BLOCKING: end the driver, Edge and everything Edge
        started (the job), then release the job. Returns the pids it ended.
        Once only: a pid freed by the first kill may belong to someone else
        a moment later (the user's own Edge), so a second call does nothing."""
        with self._kill_lock:
            if self._killed:
                return []
            members = self._members()  # read BEFORE the flag: it empties the list
            # v1.376.0 review: the driver's whole tree, read BEFORE the job
            # ends it — Edge's identity_helper.exe lives OUTSIDE the job
            # (measured), so the job alone does not end it.
            self._note_tree()
            tree = list(self._tree_seen.values())
            self._killed = True
            if self._job is None:
                self._remember(members)
            pids = sorted(set(members) | self._known | {p.pid for p in tree})
            job = self._job
            try:
                if job is not None:
                    try:
                        job.terminate()
                    finally:
                        job.close()
                    self._job = None
                else:
                    _kill_procs(list(self._procs.values()))
                inside = set(members)
                _kill_procs([p for p in tree if p.pid not in inside and p.pid != os.getpid()])
            finally:
                # The Local only proxy's port is let go only once Edge is gone.
                self._drop_refuser()
            return pids

    def _members(self) -> list[int]:
        if self._killed:
            return []
        job = self._job
        if job is not None:
            try:
                return list(job.member_pids())
            except Exception:  # noqa: BLE001
                return []
        return _tree_of(self._driver)

    def pids(self) -> list[int]:
        """The browser's processes: the job's members, every process the
        driver's tree holds (one of Edge's lives outside the job, measured —
        v1.376.0 review) and every one seen before."""
        if self._killed:
            return sorted(self._known | set(self._tree_seen))
        return sorted(set(self._members()) | self._known | set(self._note_tree()))

    def alive(self) -> bool:
        """False once Edge or the driver has gone (closed by the user, a
        crash): the manager then replaces this browser (v1.376.0 review)."""
        if self._killed or self._ctx is None or self._ctx_closed:
            return False
        proc = self._procs.get(self._driver)
        if proc is not None:
            try:
                return bool(proc.is_running())
            except Exception:  # noqa: BLE001
                return False
        return True

    # -- events --------------------------------------------------------------
    def _adopt_page(self, page: Any) -> None:
        page.on("console", self._on_console)
        page.on("pageerror", self._on_page_error)
        page.on("request", self._on_request)
        page.on("requestfailed", self._on_request_failed)
        page.on("response", self._on_response)
        page.on("dialog", self._on_dialog)
        page.on("framenavigated", lambda frame, p=page: self._on_navigated(p, frame))
        page.on("close", lambda _p=None, p=page: self._on_close(p))
        self._page = page  # the newest page (a popup the page opened) is the one acted on

    def _on_close(self, page: Any) -> None:
        if self._page is page:
            rest = [p for p in (self._ctx.pages if self._ctx is not None else []) if p is not page]
            self._page = rest[-1] if rest else None

    def _on_console(self, msg: Any) -> None:
        try:
            if msg.type != "error":
                return
            text = str(msg.text or "")
            if text.startswith("Failed to load resource"):
                return  # the browser's echo of a failed request — listed there
            loc = msg.location or {}
            at = ""
            if loc.get("url"):
                at = f" ({where(loc.get('url')) or 'the page'}, line {loc.get('lineNumber', 0)})"
            self._note("console", f"{text}{at}")
        except Exception:  # noqa: BLE001
            pass

    def _on_page_error(self, exc: Any) -> None:
        try:
            name = getattr(exc, "name", "") or "Error"
            message = getattr(exc, "message", "") or str(exc)
            stack = str(getattr(exc, "stack", "") or "").splitlines()
            at = next((s.strip() for s in stack[1:] if s.strip().startswith("at ")), "")
            self._note("console", f"Uncaught {name}: {message}" + (f" ({at})" if at else ""))
        except Exception:  # noqa: BLE001
            pass

    def _on_request(self, request: Any) -> None:
        """A SERVER REDIRECT's next hop. The browser follows a redirect
        without asking the route (measured), so its hop is judged here, the
        moment the browser starts it: one the rules would not open is
        stopped by sending the page away at once (the request has left; its
        page is never loaded, read or acted on)."""
        try:
            if request.redirected_from is None or not request.is_navigation_request():
                return
            frame = request.frame
            if frame.parent_frame is not None:
                return
            verdict = self._guard(request.url)
            if verdict.kind == "ok":
                return
            self._note("blocked", f"{verdict.kind}|{request.url}")
            self._leave_soon(frame.page)
        except Exception:  # noqa: BLE001
            pass

    def _on_request_failed(self, request: Any) -> None:
        try:
            failure = str(request.failure or "")
            if "ERR_ABORTED" in failure or "ERR_BLOCKED_BY_CLIENT" in failure:
                return  # a page that moved on / the rules' own stop (noted as blocked)
            if self.local_only and any(code in failure for code in _PROXY_REFUSALS):
                failure = LOCAL_ONLY_REFUSED
            self._note("failed", f"{request.method} {where(request.url)} — {failure}")
        except Exception:  # noqa: BLE001
            pass

    def _on_response(self, response: Any) -> None:
        try:
            if int(response.status) >= 400:
                self._note(
                    "failed",
                    f"{response.request.method} {where(response.url)} → {response.status} "
                    f"{response.status_text}".rstrip(),
                )
        except Exception:  # noqa: BLE001
            pass

    async def _on_dialog(self, dialog: Any) -> None:
        """Who answers a page's dialog (v1.376.0 review DECISION): never the
        user's yes. A confirm or a prompt is answered Cancel, an alert is
        closed — and a page that asks before it is LEFT (``beforeunload``,
        unsaved changes) is left: only a navigation the agent's own action
        started (or the rules' return to an allowed page) raises it, and
        answering "stay" cancelled that navigation — page_open then failed
        with words about a download and the agent could not leave the page.
        Each one is listed for page_read, in words."""
        kind = str(getattr(dialog, "type", "") or "")
        message = str(getattr(dialog, "message", "") or "")
        if kind == "beforeunload":
            words = "the page asked before leaving it (unsaved changes?); the agent's browser left it"
        elif kind == "alert":
            words = f"alert: {message} (closed)"
        else:
            words = f"{kind or 'dialog'}: {message} (answered Cancel: the agent's browser never says yes for you)"
        with contextlib.suppress(Exception):
            self._note("dialog", words)
        with contextlib.suppress(Exception):
            if kind == "beforeunload":
                await dialog.accept()
            else:
                await dialog.dismiss()

    def _on_navigated(self, page: Any, frame: Any) -> None:
        """A page that ARRIVED somewhere the rules would not open (a server
        redirect: the route is not asked for its hops) is left at once."""
        try:
            if frame.parent_frame is not None:
                return
            url = str(frame.url or "")
            if not url.startswith(("http://", "https://")):
                return
            verdict = self._guard(url)
            if verdict.kind == "ok":
                return
            self._note("blocked", f"{verdict.kind}|{url}")
            self._leave_soon(page)
        except Exception:  # noqa: BLE001
            pass

    def _leave_soon(self, page: Any) -> None:
        task = asyncio.ensure_future(self._leave(page))
        self._leaving.add(task)
        task.add_done_callback(self._leaving.discard)

    async def _leave(self, page: Any) -> None:
        with contextlib.suppress(Exception):
            await page.goto("about:blank", timeout=10_000)

    async def quiet(self) -> None:
        """Wait (briefly) for a page the rules sent away to have left — so
        the return to the last allowed page is never undone by it."""
        if self._leaving:
            with contextlib.suppress(Exception):
                await asyncio.wait(set(self._leaving), timeout=10)

    async def _route(self, route: Any, request: Any) -> None:
        """Every request the page makes passes here first; a NAVIGATION of a
        top-level page the rules would not open is stopped before it is sent."""
        try:
            nav = request.is_navigation_request()
            top = nav and request.frame.parent_frame is None
        except Exception:  # noqa: BLE001 — a service worker's request has no frame
            top = False
        try:
            if top:
                verdict = self._guard(request.url)
                if verdict.kind != "ok":
                    self._note("blocked", f"{verdict.kind}|{request.url}")
                    await route.abort("blockedbyclient")
                    return
            await route.continue_()
        except Exception:  # noqa: BLE001 — a page that closed meanwhile
            pass

    # -- the page ------------------------------------------------------------
    def _current(self) -> Any:
        if self._page is None or self._page.is_closed():
            raise BrowserProblem(NO_PAGE)
        return self._page

    def url(self) -> str:
        try:
            return str(self._current().url or "")
        except BrowserProblem:
            return ""

    async def title(self) -> str:
        with contextlib.suppress(Exception):
            return str(await asyncio.wait_for(self._current().title(), 5.0))
        return ""

    async def goto(self, url: str, timeout_s: float) -> None:
        try:
            page = self._page
            if page is None or page.is_closed():
                # Every page closed (a popup that closed itself and the page
                # that opened it): opening an address makes a fresh one.
                page = await self._ctx.new_page()
                self._adopt_page(page)
            await page.goto(url, wait_until="load", timeout=timeout_s * 1000)
        except Exception as exc:  # noqa: BLE001
            raise BrowserProblem(playwright_words(exc, what=f"Opening {where(url)}", limit_s=timeout_s)) from None

    async def settle(self) -> None:
        page = self._page
        if page is None:
            return
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("load", timeout=SETTLE_MAX_S * 1000)
        await asyncio.sleep(0.3)

    async def _resolve(self, target: Any, purpose: str) -> Any:
        """THE TARGET: computer use's rule (``computeruse.browser.locator_for``:
        css, role + name, label, text), then the obvious fallbacks for a bare
        string (a button or link of that name for a click; a field labelled,
        placeholdered or named so for typing). Exactly one element, or the
        one visible element, is used; none or several is said in words."""
        from ..computeruse.base import Selector
        from ..computeruse.browser import locator_for

        page = self._current()
        try:
            sel = Selector.coerce(target if target not in (None, "") else None)
        except TypeError:
            sel = None
        if sel is None or not (sel.css or sel.name or sel.text):
            raise BrowserProblem(
                "Say what to act on: the words on it (\"Add to cart\"), {\"role\": \"button\", "
                "\"name\": \"Add to cart\"}, a field's label, or a css selector."
            )
        plain = "" if (sel.css or sel.role) else (sel.text or sel.name or "")
        cands: list[Any] = []
        if purpose == "click" and plain:
            cands += [page.get_by_role("button", name=plain), page.get_by_role("link", name=plain)]
        if purpose == "type" and plain:
            cands += [
                page.get_by_label(plain), page.get_by_placeholder(plain),
                page.get_by_role("textbox", name=plain), page.get_by_role("searchbox", name=plain),
                page.get_by_role("combobox", name=plain),
            ]
        cands.append(locator_for(page, sel))
        shown = sel.describe()
        several = 0
        for loc in cands:
            try:
                n = await loc.count()
                if n == 1:
                    return loc
                if n > 1:
                    visible = loc.filter(visible=True)
                    if await visible.count() == 1:
                        return visible
                    several = max(several, n)
            except Exception:  # noqa: BLE001 — a bad css selector, a closed page
                continue
        if several:
            raise BrowserProblem(
                f"{several} things on the page match {_line(shown)}. Be more exact: give "
                "{\"role\": \"button\", \"name\": \"…\"} with the whole name, or a css selector."
            )
        raise BrowserProblem(
            f"Nothing on the page matches {_line(shown)}. Call page_read to see what can be "
            "clicked or typed into."
        )

    async def click(self, target: Any, timeout_s: float) -> str:
        loc = await self._resolve(target, "click")
        try:
            name = " ".join(str(await loc.inner_text(timeout=2000) or "").split())[:80]
        except Exception:  # noqa: BLE001
            name = ""
        try:
            await loc.click(timeout=timeout_s * 1000)
        except Exception as exc:  # noqa: BLE001
            raise BrowserProblem(
                playwright_words(exc, what="The click", limit_s=timeout_s)
                + " (the element may be hidden, covered or disabled)"
            ) from None
        return name

    async def label(self, target: Any) -> str:
        """What the element a click would press READS as (v1.376.0 review)."""
        loc = await self._resolve(target, "click")
        try:
            words = await loc.evaluate(_CLICK_WORDS, timeout=5000)
        except Exception:  # noqa: BLE001
            words = ""
        return " ".join(str(words or "").split())[:200]

    async def field(self, target: Any) -> "tuple[Any, dict[str, Any]]":
        loc = await self._resolve(target, "type")
        try:
            facts = await loc.evaluate(_FIELD_FACTS, timeout=5000)
        except Exception:  # noqa: BLE001
            facts = {}
        return loc, (facts if isinstance(facts, dict) else {})

    async def fill(self, loc: Any, text: str, submit: bool, timeout_s: float) -> None:
        try:
            await loc.fill(text, timeout=timeout_s * 1000)
            if submit:
                await loc.press("Enter", timeout=timeout_s * 1000)
        except Exception as exc:  # noqa: BLE001
            raise BrowserProblem(
                playwright_words(exc, what="Typing", limit_s=timeout_s)
                + " (the field may be hidden, read-only or not a text field)"
            ) from None

    async def text(self) -> str:
        page = self._current()
        try:
            body = await page.inner_text("body", timeout=ACTION_TIMEOUT_S * 1000)
        except Exception:  # noqa: BLE001 — a page with no body yet
            body = ""
        return str(body or "")

    async def elements(self) -> list[dict[str, Any]]:
        page = self._current()
        try:
            rows = await page.evaluate(_WORDS_OF_ELEMENTS, MAX_ELEMENTS)
        except Exception:  # noqa: BLE001
            return []
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    async def screenshot(self, *, jpeg: bool) -> bytes:
        page = self._current()
        if jpeg:
            return await page.screenshot(type="jpeg", quality=VIEW_JPEG_QUALITY, timeout=10_000)
        return await page.screenshot(type="png", timeout=10_000)


def _alive(backend: Any) -> bool:
    """Whether a session's browser still works (a backend with no ``alive``
    — a test's fake — always does)."""
    probe = getattr(backend, "alive", None)
    if probe is None:
        return True
    try:
        return bool(probe())
    except Exception:  # noqa: BLE001
        return False


def _tree_of(pid: int) -> list[int]:
    if not pid:
        return []
    try:
        import psutil

        root = psutil.Process(pid)
        return [root.pid, *(c.pid for c in root.children(recursive=True))]
    except Exception:  # noqa: BLE001
        return []


def _tree_procs(pid: int) -> list[Any]:
    """The driver and every process below it, as psutil objects (which
    refuse a pid that was reused since they were read). [] when unknown."""
    if not pid:
        return []
    try:
        import psutil

        root = psutil.Process(pid)
        return [root, *root.children(recursive=True)]
    except Exception:  # noqa: BLE001
        return []


def _kill_procs(rows: list[Any]) -> None:
    """The fallback with no job (off Windows, or a job that could not be
    made): kill each recorded process of THIS browser (psutil objects, which
    refuse a pid reused since they were recorded)."""
    for proc in rows:
        with contextlib.suppress(Exception):
            proc.kill()


# --------------------------------------------------------------------------- #
# the browser thread
# --------------------------------------------------------------------------- #


class _Host:
    """ONE thread with its own event loop for every session's browser."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None and self._thread is not None and self._thread.is_alive():
                return self._loop
            ready = threading.Event()
            box: dict[str, Any] = {}

            def _run() -> None:
                loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                box["loop"] = loop
                ready.set()
                try:
                    loop.run_forever()
                finally:
                    with contextlib.suppress(Exception):
                        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                        for t in pending:
                            t.cancel()
                        if pending:
                            loop.run_until_complete(asyncio.wait(pending, timeout=5))
                    with contextlib.suppress(Exception):
                        loop.close()

            thread = threading.Thread(target=_run, name="ij-agent-browser", daemon=True)
            thread.start()
            ready.wait(10)
            self._loop, self._thread = box["loop"], thread
            return self._loop

    def on_thread(self) -> bool:
        return self._thread is not None and threading.current_thread() is self._thread

    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def stop(self) -> None:
        with self._lock:
            loop, thread = self._loop, self._thread
            self._loop = self._thread = None
        if loop is None or thread is None:
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(loop.stop)
        if threading.current_thread() is not thread:
            thread.join(10)


# --------------------------------------------------------------------------- #
# the sessions' browsers
# --------------------------------------------------------------------------- #


@dataclass
class _Session:
    key: str
    backend: Any
    work: Path
    lock: asyncio.Lock
    allowed: set[str] = field(default_factory=set)
    preview_origin: str = ""
    last_used: float = field(default_factory=time.monotonic)
    last_good: str = ""
    console: collections.deque = field(default_factory=lambda: collections.deque(maxlen=MAX_LOG_LINES))
    failed: collections.deque = field(default_factory=lambda: collections.deque(maxlen=MAX_LOG_LINES))
    dialogs: collections.deque = field(default_factory=lambda: collections.deque(maxlen=MAX_LOG_LINES))
    #: How many lines fell out of each deque since the last read.
    dropped: dict[str, int] = field(default_factory=lambda: {"console": 0, "failed": 0, "dialog": 0})
    #: Navigations the rules stopped since the last action result.
    blocked: list[str] = field(default_factory=list)
    #: Sentences the NEXT action result opens with, once (a browser started
    #: fresh after Edge died) — v1.376.0 review.
    notes: list[str] = field(default_factory=list)
    #: Started behind the Local only proxy (v1.376.0 review).
    local_only: bool = False
    #: The order of use across sessions (a counter: two actions within one
    #: clock tick of Windows' 15.6 ms monotonic clock would tie on time).
    used_seq: int = 0


@dataclass
class ActResult:
    """What one action left: where the browser is, and what happened."""

    url: str = ""
    title: str = ""
    did: str = ""
    blocked: list[str] = field(default_factory=list)
    new_console: int = 0
    new_failed: int = 0
    text: str = ""
    elements: list[dict[str, Any]] = field(default_factory=list)
    console: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    dialogs: list[str] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)
    picture: bytes = b""


class AgentBrowsers:
    """Every session's agent browser. The public methods are called from the
    daemon's loop (``async``) or from any thread (the blocking ones)."""

    def __init__(self, backend_factory: "Callable[..., Any] | None" = None) -> None:
        self._host = _Host()
        self._factory = backend_factory
        self._sessions: dict[str, _Session] = {}  # browser thread only
        self._starting: dict[str, asyncio.Lock] = {}  # browser thread only
        self._idle_task: "asyncio.Task[None] | None" = None
        self._lock = threading.Lock()
        #: key -> the backend's kill (any thread), for a close the browser
        #: thread cannot answer.
        self._kills: dict[str, Callable[[], list[int]]] = {}
        #: key -> the backend's pids (any thread: the job's member list).
        self._pids: dict[str, Callable[[], list[int]]] = {}
        #: key -> the origins allowed for that browser (mirror, any thread).
        self._allowed: dict[str, set[str]] = {}
        self._urls: dict[str, str] = {}
        self._views: "collections.OrderedDict[str, dict[str, Any]]" = collections.OrderedDict()
        self._swept = False
        self._use_counter = itertools.count(1)
        #: (key, target) -> (the words a click's check read, when): what the
        #: user was asked about (v1.376.0 review). Any thread.
        self._checked: dict[tuple[str, str], tuple[str, float]] = {}
        #: Test seam: extra Edge switches (a host-resolver rule).
        self.extra_args: tuple[str, ...] = ()

    # -- reads (any thread) ----------------------------------------------------
    def is_open(self, key: str) -> bool:
        with self._lock:
            return key in self._kills

    def open_keys(self) -> list[str]:
        with self._lock:
            return list(self._kills)

    def pids(self, key: str) -> list[int]:
        """The processes of ``key``'s open browser (the driver, Edge and what
        Edge started); [] when none is open."""
        with self._lock:
            read = self._pids.get(key)
        if read is None:
            return []
        try:
            return list(read())
        except Exception:  # noqa: BLE001
            return []

    def allowed(self, key: str, url: str) -> bool:
        """True when ``url``'s origin is allowed for ``key``'s open browser."""
        org = origin(url)
        with self._lock:
            return bool(org) and org in self._allowed.get(key, set())

    def current_url(self, key: str) -> str:
        with self._lock:
            return self._urls.get(key, "") if key in self._kills else ""

    def view(self, key: str) -> "dict[str, Any] | None":
        """The pane's view of ``key``'s browser (no picture bytes), or None
        when it never opened a page."""
        with self._lock:
            row = self._views.get(key)
            if row is None:
                return None
            out = {k: v for k, v in row.items() if k != "shot"}
            out["shot"] = bool(row.get("shot"))
            return out

    def shot(self, key: str) -> bytes:
        with self._lock:
            row = self._views.get(key) or {}
            return bytes(row.get("shot") or b"")

    def forget_view(self, key: str) -> None:
        with self._lock:
            self._views.pop(key, None)

    def _set_view(self, key: str, **fields: Any) -> None:
        with self._lock:
            row = self._views.pop(key, {})
            row.update(fields)
            self._views[key] = row
            while len(self._views) > MAX_VIEWS:
                self._views.popitem(last=False)

    # -- the daemon's side -----------------------------------------------------
    async def run(self, coro: Any) -> Any:
        """Await ``coro`` on the browser thread. A cancel here cancels it there."""
        fut = asyncio.run_coroutine_threadsafe(coro, self._host.loop())
        return await asyncio.wrap_future(fut)

    # Each action reads the CALLER's Local only scope (the turn's, or the
    # helper's) here, on the daemon's side, and hands it over explicitly.
    async def open(self, key: str, url: str, *, preview: str = "", background: bool = False) -> ActResult:
        return await self.run(self._act(key, "open", url=url, preview=preview, background=background,
                                        local_only=_local_only_now()))

    async def click(self, key: str, target: Any, *, preview: str = "") -> ActResult:
        return await self.run(self._act(key, "click", target=target, preview=preview,
                                        local_only=_local_only_now()))

    async def type(self, key: str, target: Any, text: str, *, submit: bool = False,
                   preview: str = "") -> ActResult:
        return await self.run(self._act(key, "type", target=target, text=text, submit=submit,
                                        preview=preview, local_only=_local_only_now()))

    async def read(self, key: str, *, preview: str = "") -> ActResult:
        return await self.run(self._act(key, "read", preview=preview, local_only=_local_only_now()))

    async def picture(self, key: str, *, preview: str = "") -> ActResult:
        return await self.run(self._act(key, "picture", preview=preview, local_only=_local_only_now()))

    async def click_words(self, key: str, target: Any) -> str:
        """What ``target`` on ``key``'s page reads as, for page_click's check
        (v1.376.0 review); remembered as the words that check judged."""
        words = await self.run(self._click_words(key, target))
        with self._lock:
            self._checked[(key, _target_key(target))] = (words, time.monotonic())
        return words

    def checked_words(self, key: str, target: Any) -> "str | None":
        """The words page_click's check read for ``target`` (None: never, or
        too long ago)."""
        with self._lock:
            row = self._checked.get((key, _target_key(target)))
        if row is None or time.monotonic() - row[1] > CHECK_TTL_S:
            return None
        return row[0]

    async def close(self, key: str, reason: str = "") -> list[int]:
        """Close ``key``'s browser (nothing open: nothing to do)."""
        if not self.is_open(key):
            return []
        return await asyncio.to_thread(self.close_blocking, key, reason)

    def close_blocking(self, key: "str | None", reason: str = "") -> list[int]:
        """BLOCKING, any thread but the browser's: close ``key``'s browser
        (``None`` = every one). Bounded: when the browser thread does not
        answer in time, each job is terminated from here. Returns the pids
        ended. Never raises."""
        keys = self.open_keys() if key is None else ([key] if self.is_open(key) else [])
        if not keys:
            return []
        if self._host.on_thread():
            for k in keys:
                asyncio.ensure_future(self._close(k, reason))
            return []
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._close_many(keys, reason), self._host.loop(),
            )
            return list(fut.result(timeout=CLOSE_TIMEOUT_S + 6))
        except Exception:  # noqa: BLE001 — the thread is wedged: kill from here
            log.warning("agent browser close did not answer; terminating its job", exc_info=True)
            out: list[int] = []
            for k in keys:
                with self._lock:
                    kill = self._kills.pop(k, None)
                    self._pids.pop(k, None)
                    self._allowed.pop(k, None)
                if kill is not None:
                    with contextlib.suppress(Exception):
                        out += kill()
            return out

    def shutdown(self) -> list[int]:
        """Daemon shutdown: every browser closed, the browser thread stopped."""
        out = self.close_blocking(None, "shutdown")
        if self._host.running() and not self._host.on_thread():
            self._host.stop()
        return out

    # -- the browser thread's side ------------------------------------------------
    def _guard_for(self, sess: "_Session") -> Callable[[str], Verdict]:
        def guard(url: str) -> Verdict:
            return judge(url, sess.allowed, sess.preview_origin)

        return guard

    def _note_for(self, sess: "_Session") -> Callable[[str, str], None]:
        def note(kind: str, text: str) -> None:
            if kind == "blocked":
                verdict, _, url = text.partition("|")
                words = blocked_words(verdict, url)
                if words not in sess.blocked:
                    sess.blocked.append(words)
                return
            box = {"console": sess.console, "failed": sess.failed, "dialog": sess.dialogs}.get(kind)
            if box is None:
                return
            if len(box) == box.maxlen:
                sess.dropped[kind] = sess.dropped.get(kind, 0) + 1
            box.append(_line(text))

        return note

    async def _start(self, key: str, background: bool, local_only: bool = False) -> _Session:
        if not self._swept:
            self._swept = True
            await asyncio.to_thread(_sweep_stale)
        edge = await asyncio.to_thread(edge_executable) if self._factory is None else "edge"
        if not edge:
            raise BrowserProblem(NO_EDGE)
        if self._factory is None:
            problem = await asyncio.to_thread(playwright_problem)
            if problem:
                raise BrowserProblem(problem)
        work = await asyncio.to_thread(_new_work)
        sess = _Session(key=key, backend=None, work=work, lock=asyncio.Lock(), local_only=bool(local_only))
        factory = self._factory or EdgeBackend
        # ``local_only`` only when on: a test's stand-in predating it takes no
        # such keyword.
        backend = factory(
            edge=edge, work=work, guard=self._guard_for(sess), note=self._note_for(sess),
            background=background, extra_args=self.extra_args,
            **({"local_only": True} if local_only else {}),
        )
        sess.backend = backend
        with self._lock:
            self._kills[key] = backend.kill
            self._pids[key] = backend.pids
            self._allowed[key] = sess.allowed
        try:
            await asyncio.wait_for(backend.start(), START_TIMEOUT_S)
        except BaseException as exc:
            with self._lock:
                self._kills.pop(key, None)
                self._pids.pop(key, None)
                self._allowed.pop(key, None)
            with contextlib.suppress(Exception):
                await asyncio.to_thread(backend.kill)
            await asyncio.to_thread(_remove_work, work)
            if isinstance(exc, BrowserProblem):
                raise
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            raise BrowserProblem(
                playwright_words(exc, what="Starting the agent's browser", limit_s=START_TIMEOUT_S)
            ) from None
        self._sessions[key] = sess
        if self._idle_task is None or self._idle_task.done():
            self._idle_task = asyncio.ensure_future(self._idle_watch())
        return sess

    async def _session(self, key: str, *, create: bool, background: bool,
                       local_only: bool = False) -> "_Session | None":
        sess = self._sessions.get(key)
        if sess is not None and not sess.lock.locked() and sess.local_only != bool(local_only):
            # v1.376.0 review: the Local only proxy is chosen when Edge
            # starts, so a browser started for the other setting is never
            # used: it closes, and page_open starts one for this turn's.
            state = "on" if local_only else "off"
            await self._close(key, "local only changed")
            if not create:
                raise BrowserProblem(LOCAL_ONLY_CHANGED.format(state=state))
            fresh = await self._session(key, create=True, background=background, local_only=local_only)
            if fresh is not None:
                fresh.notes.append(LOCAL_ONLY_FRESH.format(state=state))
            return fresh
        if sess is not None and not sess.lock.locked() and not _alive(sess.backend):
            # v1.376.0 review: Edge went away under the agent (closed by the
            # user, a crash). Its job, its folder and its entry go — never a
            # browser that fails every call until the idle timer — and the
            # next page_open starts a fresh one, saying so.
            await self._close(key, "crashed")
            if not create:
                raise BrowserProblem(DIED)
            fresh = await self._session(key, create=True, background=background, local_only=local_only)
            if fresh is not None:
                fresh.notes.append(FRESH_AFTER_DIED)
            return fresh
        if sess is not None or not create:
            return sess
        gate = self._starting.setdefault(key, asyncio.Lock())
        async with gate:
            sess = self._sessions.get(key)
            if sess is None:
                await self._make_room(key)
                sess = await self._start(key, background, local_only)
        self._starting.pop(key, None)
        return sess

    async def _click_words(self, key: str, target: Any) -> str:
        sess = self._sessions.get(key)
        if sess is None:
            raise BrowserProblem(NO_PAGE)
        try:
            await asyncio.wait_for(sess.lock.acquire(), CHECK_LOCK_WAIT_S)
        except TimeoutError:
            raise BrowserProblem(BUSY) from None
        try:
            if self._sessions.get(key) is not sess:
                raise BrowserProblem(NO_PAGE)
            self._check_here(sess)
            probe = getattr(sess.backend, "label", None)
            if probe is None:
                return ""
            async with asyncio.timeout(ACTION_TIMEOUT_S):
                return str(await probe(target) or "")
        finally:
            sess.lock.release()

    async def _make_room(self, key: str) -> None:
        """At most :data:`MAX_BROWSERS` open: the least recently used one that
        is not acting is closed ("room"); none free → refused in words."""
        while len(self._sessions) >= MAX_BROWSERS:
            free = [s for k, s in self._sessions.items() if k != key and not s.lock.locked()]
            if not free:
                raise BrowserProblem(TOO_MANY)
            oldest = min(free, key=lambda s: s.used_seq)
            await self._close(oldest.key, "room")

    async def _act(self, key: str, op: str, *, preview: str = "", background: bool = False,
                   local_only: bool = False, **kw: Any) -> ActResult:
        if op == "open" and judge(str(kw.get("url") or ""), set(), "").kind == "refuse":
            raise BrowserProblem(NOT_LOCAL)  # before a browser is started for nothing
        sess = await self._session(key, create=(op == "open"), background=background,
                                   local_only=local_only)
        if sess is None:
            raise BrowserProblem(NO_PAGE)
        try:
            await asyncio.wait_for(sess.lock.acquire(), LOCK_WAIT_S)
        except TimeoutError:
            raise BrowserProblem(BUSY) from None
        try:
            if self._sessions.get(key) is not sess:
                raise BrowserProblem(NO_PAGE)  # closed while this waited
            if sess.local_only != bool(local_only):
                # Local only changed while this waited behind another action.
                await self._close(key, "local only changed")
                raise BrowserProblem(LOCAL_ONLY_CHANGED.format(state="on" if local_only else "off"))
            sess.last_used = time.monotonic()
            sess.used_seq = next(self._use_counter)
            # The page the user's Browser pane shows NOW (the turn's own
            # preview_url): opened without asking. A page the agent opened
            # under an earlier one stays allowed (sess.allowed).
            sess.preview_origin = origin(preview)
            limit = OPEN_TIMEOUT_S if op == "open" else ACTION_TIMEOUT_S
            bound = asyncio.timeout(limit + HARD_BOUND_EXTRA_S)
            try:
                async with bound:
                    return await self._do(sess, op, limit, **kw)
            except TimeoutError:
                if not bound.expired():
                    raise
                # The action's own limits did not hold (a wedged driver):
                # close the browser rather than leave anything running.
                await self._close(key, "timed out")
                raise BrowserProblem(
                    f"The agent's browser did not answer within {limit + HARD_BOUND_EXTRA_S:g} s, "
                    "so it was closed. Call page_open to start a fresh one."
                ) from None
            except BrowserProblem:
                raise
            except asyncio.CancelledError:
                # v1.376.0 review: a cancel (Stop, a helper's deadline, its
                # run ending) that lands while this call is ACTING closes the
                # browser — Playwright's driver would finish the click or the
                # page load on its own. A call cancelled while it only WAITED
                # for the lock never gets here: it touched nothing, and the
                # action that holds the browser carries on.
                asyncio.ensure_future(self._close(key, "stopped"))
                raise
            except Exception as exc:  # noqa: BLE001 — v1.376.0 review: always words
                # A Playwright error outside an action's own words (Edge went
                # away mid-action: "Target page, context or browser has been
                # closed"). A dead browser is closed so the next page_open
                # starts a fresh one; anything else is its first line.
                if not _alive(sess.backend):
                    with contextlib.suppress(Exception):
                        await asyncio.shield(asyncio.ensure_future(self._close(key, "crashed")))
                    raise BrowserProblem(DIED) from None
                raise BrowserProblem(
                    playwright_words(exc, what="The agent's browser", limit_s=limit)
                ) from None
        finally:
            sess.last_used = time.monotonic()
            sess.lock.release()

    def _check_here(self, sess: _Session) -> None:
        """Every action re-checks the page it acts on (belt and braces under
        the route and the arrival guard)."""
        url = sess.backend.url()
        if url.startswith(("http://", "https://")):
            verdict = judge(url, sess.allowed, sess.preview_origin)
            if verdict.kind != "ok":
                raise BrowserProblem(
                    f"The agent's browser is on {where(url)}, which is not an address it may "
                    "act on. Call page_open with your dev server's address."
                )

    async def _do(self, sess: _Session, op: str, limit: float, **kw: Any) -> ActResult:
        b = sess.backend
        sess.blocked = []
        out = ActResult()
        if op == "open":
            url = str(kw.get("url") or "")
            verdict = judge(url, sess.allowed, sess.preview_origin)
            if verdict.kind == "refuse":
                raise BrowserProblem(NOT_LOCAL)
            # The gate already asked when it had to (page_open's spend check):
            # this origin is allowed for this browser from now on.
            if verdict.origin:
                sess.allowed.add(verdict.origin)
            await b.goto(verdict.url or url, limit)
            out.did = f"Opened {where(verdict.url or url)}"
            with contextlib.suppress(Exception):
                out.text = _mask((await b.text())[:OPEN_TEXT_HEAD])
        elif op == "click":
            self._check_here(sess)
            probe = getattr(b, "label", None)
            if probe is not None:
                # The button's words AT the click: a risky one is pressed only
                # when page_click's check showed the user these very words.
                words = str(await probe(kw.get("target")) or "")
                if risky_click_word(words):
                    asked = self.checked_words(sess.key, kw.get("target"))
                    if asked is None or " ".join(asked.split()).lower() != " ".join(words.split()).lower():
                        raise BrowserProblem(CLICK_NOT_ASKED.format(words=_line(words)[:80]))
            name = await b.click(kw.get("target"), limit)
            await b.settle()
            out.did = f"Clicked \"{_line(name)}\"" if name else "Clicked"
        elif op == "type":
            self._check_here(sess)
            text = str(kw.get("text") or "")
            loc, facts = await b.field(kw.get("target"))
            refusal = typing_refusal(facts, text)
            if refusal:
                raise BrowserProblem(refusal)
            await b.fill(loc, text, bool(kw.get("submit")), limit)
            if kw.get("submit"):
                await b.settle()
            out.did = "Typed" + (" and pressed Enter" if kw.get("submit") else "")
        elif op == "read":
            self._check_here(sess)
            out.text = _mask(await b.text())
            out.elements = [
                {"role": _line(e.get("role")), "name": _line(e.get("name")), "disabled": bool(e.get("disabled"))}
                for e in await b.elements()
            ]
            out.console, out.failed, out.dialogs = list(sess.console), list(sess.failed), list(sess.dialogs)
            out.dropped = dict(sess.dropped)
            sess.console.clear()
            sess.failed.clear()
            sess.dialogs.clear()
            sess.dropped = {"console": 0, "failed": 0, "dialog": 0}
        elif op == "picture":
            self._check_here(sess)
            try:
                out.picture = await b.screenshot(jpeg=False)
            except BrowserProblem:
                raise
            except Exception as exc:  # noqa: BLE001 — said in words, like every action
                raise BrowserProblem(playwright_words(exc, what="The screenshot", limit_s=10)) from None
        else:  # pragma: no cover — the tools name the ops
            raise BrowserProblem(f"unknown browser action {op!r}")
        await self._after(sess, out)
        return out

    async def _after(self, sess: _Session, out: ActResult) -> None:
        """Where the browser is now: back to the last allowed page after a
        stopped navigation, the counts of what is waiting to be read, and the
        pane's view (address, title, picture)."""
        b = sess.backend
        await b.quiet()
        url = b.url()
        if sess.blocked and not (url.startswith(("http://", "https://")) and
                                 judge(url, sess.allowed, sess.preview_origin).kind == "ok"):
            # A stopped navigation leaves an error or blank page: go back.
            if sess.last_good:
                with contextlib.suppress(BrowserProblem):
                    await b.goto(sess.last_good, OPEN_TIMEOUT_S)
            url = b.url()
        if url.startswith(("http://", "https://")) and judge(url, sess.allowed, sess.preview_origin).kind == "ok":
            sess.last_good = url
        out.url = url
        out.title = await b.title()
        out.blocked = [*sess.notes, *sess.blocked]
        sess.notes.clear()
        out.new_console = len(sess.console)
        out.new_failed = len(sess.failed)
        picture = b""
        with contextlib.suppress(Exception):
            picture = await asyncio.wait_for(b.screenshot(jpeg=True), 12.0)
        now = _iso_now()
        with self._lock:
            self._urls[sess.key] = url
        # The pane's view says WHERE (host and path) — never the raw address,
        # whose query string can carry a token.
        fields: dict[str, Any] = {"where": where(url), "blank": (not url) or url.startswith("about:"),
                                  "title": _line(out.title), "at": now, "open": True,
                                  "note": " ".join(out.blocked)[:600]}
        if picture:
            fields.update(shot=picture, shot_at=now)
        self._set_view(sess.key, **fields)

    async def _close_many(self, keys: list[str], reason: str) -> list[int]:
        done = await asyncio.gather(*(self._close(k, reason) for k in keys), return_exceptions=True)
        out: list[int] = []
        for part in done:
            if isinstance(part, list):
                out += part
        return out

    async def _close(self, key: str, reason: str) -> list[int]:
        """Close ``key``'s browser: the context, then the driver, then the
        job (whatever is left of the tree), then the throwaway folder. An
        action still running (Stop) fails on its own once its pages go."""
        sess = self._sessions.pop(key, None)
        with self._lock:
            kill = self._kills.pop(key, None)
            self._pids.pop(key, None)
            self._allowed.pop(key, None)
            self._urls.pop(key, None)
            for gone in [k for k in self._checked if k[0] == key]:
                self._checked.pop(gone, None)
        if sess is None and kill is None:
            return []
        pids: list[int] = []
        if sess is not None:
            try:
                await asyncio.wait_for(sess.backend.close(), CLOSE_TIMEOUT_S)
            except BaseException:  # noqa: BLE001 — the job ends it either way
                pass
        if kill is not None:
            with contextlib.suppress(Exception):
                pids = await asyncio.to_thread(kill)
        if sess is not None:
            await asyncio.to_thread(_remove_work, sess.work)
        with self._lock:
            had_view = sess is not None and sess.key in self._views
        if had_view:
            self._set_view(key, open=False, closed_reason=reason, closed_at=_iso_now())
        return pids

    async def _idle_watch(self) -> None:
        """Close a browser nobody has used for :func:`idle_close_s` (one that
        is acting right now is never idle)."""
        while self._sessions:
            idle = idle_close_s()
            await asyncio.sleep(max(0.5, min(IDLE_CHECK_MAX_S, idle / 4)))
            now = time.monotonic()
            for key, sess in list(self._sessions.items()):
                if sess.lock.locked():
                    continue
                if now - sess.last_used >= idle:
                    await self._close(key, "idle")


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# the one instance, its key, and the run's end
# --------------------------------------------------------------------------- #

#: The daemon's agent browsers (a module-level singleton, like
#: ``harness/process.TABLE``: the run's end and daemon shutdown reach it
#: through ``procs``' end hooks).
BROWSERS = AgentBrowsers()


def browser_key(ctx: Any) -> str:
    """The browser a tool call drives: its session's (``build:<id>``) — and a
    helper agent of a Build session drives ITS session's browser (the helper's
    calls run under the helper's own session id; the turn's helper parent,
    ``harness/helpers.HELPER_PARENT``, names the Build session)."""
    sid = str(getattr(ctx, "session_id", "") or "")
    try:
        from .helpers import HELPER_PARENT

        parent = HELPER_PARENT.get()
    except Exception:  # noqa: BLE001
        parent = None
    psid = str(getattr(parent, "session_id", "") or "") if parent is not None else ""
    if psid.startswith("build:") and psid != sid:
        return psid
    return sid or "chat"


def _end_hook(session_id: "str | None") -> list[int]:
    """``procs.end_session`` / ``end_all`` hook: an archived or deleted
    session's browser closes; at shutdown every browser closes and the
    browser thread stops. Cheap when nothing is open (every agent run's end
    calls it)."""
    if session_id is None:
        return BROWSERS.shutdown()
    if not BROWSERS.is_open(session_id):
        return []
    return BROWSERS.close_blocking(session_id, "ended")


procs.add_end_hook(_end_hook)


def self_check(url: str) -> dict[str, Any]:
    """BLOCKING: the release check for a packaged build (``ironjarvis
    page-browser-check <url>``). Opens ``url`` (a page on this PC) in a fresh
    agent browser of its own, reads its text, closes it, and says what
    happened — including which of its processes are still alive after the
    close (none, or the check fails). Never raises."""
    import psutil

    browsers = AgentBrowsers()
    out: dict[str, Any] = {"ok": False, "edge": edge_executable(), "playwright": playwright_problem() or "ok"}

    async def _go() -> None:
        key = "self-check"
        try:
            opened = await browsers.open(key, url, preview=url)
            read = await browsers.read(key, preview=url)
            out.update(where=where(opened.url), title=opened.title, text=read.text[:300],
                       console=read.console, failed=read.failed)
            pids = browsers.pids(key)
            out["pids"] = pids
            await browsers.close(key, "check")
            await asyncio.sleep(1.0)
            out["alive_after_close"] = [p for p in pids if psutil.pid_exists(p)]
            out["ok"] = bool(read.text.strip()) and not out["alive_after_close"]
        except BrowserProblem as exc:
            out["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001 — the check reports, never raises
            out["error"] = f"{type(exc).__name__}: {exc}"

    try:
        asyncio.run(_go())
    finally:
        browsers.shutdown()
    return out


def close_for_stop(session_id: str) -> list[int]:
    """The turn ended STOPPED (``routes/build.recorded_turn``): the session's
    browser closes. BLOCKING; never raises."""
    try:
        return BROWSERS.close_blocking(session_id, "stopped")
    except Exception:  # noqa: BLE001
        return []


__all__ = [
    "ACTION_TIMEOUT_S", "AgentBrowsers", "BROWSERS", "BrowserProblem", "EdgeBackend", "IDLE_CLOSE_S",
    "IDLE_ENV", "NOT_LOCAL", "NO_EDGE", "NO_PAGE", "NO_PLAYWRIGHT", "OPEN_TIMEOUT_S", "STOPPED",
    "Verdict", "blocked_words", "browser_key", "close_for_stop", "edge_executable", "idle_close_s",
    "judge", "normalize", "playwright_problem", "risky_click_word", "typing_refusal", "where",
]
