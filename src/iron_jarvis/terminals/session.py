"""A single live terminal session — an id'd wrapper around a :class:`PtyBackend`."""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from .agent_state import PaneActivity

from ..core.ids import new_id, utcnow
from .backend import PtyBackend, default_backend
from .shells import resolve_shell

#: How much recent output a session retains — doubles as the scrollback replayed
#: to a RE-ATTACHING pane (tab switch / navigation) so it shows its history
#: instead of a blank screen, and as the context for the per-terminal AI assist.
TAIL_MAX_BYTES = 256 * 1024

#: ANSI escape sequences (CSI + OSC) — stripped from the AI-facing tail so the
#: model reads clean text instead of color/cursor noise.
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"  # CSI ... final byte
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC ... BEL / ST
    r"|\x1b[()#%*+][0-9A-Za-z]"  # charset designation (ESC ( B …) — ConPTY
    # interleaves these MID-STRING, which used to split mode banners like
    # "auto-accept edits on" so detection missed them (live-hit 2026-07-07)
    r"|\x1b[@-_]"  # lone two-byte escapes
)


#: The five capabilities a Build pane can be granted (plan §4.2, decision D20).
#: A FIXED set: a key outside it is dropped rather than stored, so a caller
#: cannot invent a capability name and have it round-trip through
#: ``terminals.json`` looking official.
PANE_CAPABILITY_KEYS: tuple[str, ...] = (
    "files",
    "shell",
    "browser",
    "extensions",
    "memory",
)

#: Of those five, the ones a gate actually READS today. Only ``browser`` is
#: enforced in the browser ships; ``files``/``shell``/``extensions``/``memory``
#: are recorded and displayed, and their enforcement seams are Phase 2 (they
#: reach into the permission engine and the agent runtime, which is not browser
#: work). This constant exists so no reader has to infer that from prose, and so
#: any surface claiming enforcement can be checked against it.
ENFORCED_PANE_CAPABILITIES: tuple[str, ...] = ("browser",)


def normalise_pane_capabilities(raw: Any) -> dict[str, bool]:
    """Any stored/posted capabilities value reduced to the five canonical keys.

    Always returns all five, always real ``bool``s, and never raises: a pane with
    no capabilities and a pane whose snapshot predates the field must read the
    same — everything ``False`` — so an existing pane can never gain a capability
    by upgrade.

    Truthiness is delegated to :func:`iron_jarvis.browser.panetokens.capability_enabled`
    rather than re-decided here, because the subtle case belongs to one owner:
    these values round-trip through JSON, and ``bool("false")`` is ``True``, which
    would turn every disabled capability back on across a single restart. The
    import is deferred to call time so importing ``terminals`` does not drag in
    the whole browser package.
    """
    from ..browser.panetokens import capability_enabled

    values = raw if isinstance(raw, Mapping) else {}
    return {key: capability_enabled(values.get(key)) for key in PANE_CAPABILITY_KEYS}


def _safe_replay_start(buf: bytes | bytearray, scan: int = 4096) -> int:
    """Byte offset where a TRUNCATED output stream can safely re-enter a
    terminal renderer.

    A rolling byte cap slices arbitrarily — mid UTF-8 code point, mid escape
    sequence — and replaying from the raw cut renders mojibake / a garbage
    head line (live-hit 2026-07-16: a re-attached Grok TUI pane came back
    "blocky"). Skip any leading UTF-8 continuation bytes, then re-enter at
    the earlier of the next ESC (a sequence starts there) or just past the
    next newline, looking at most ``scan`` bytes ahead."""
    i = 0
    while i < len(buf) and 0x80 <= buf[i] <= 0xBF:
        i += 1
    window = bytes(buf[i : i + scan])
    esc = window.find(b"\x1b")
    nl = window.find(b"\n")
    anchors = [a for a in (esc, nl + 1 if nl >= 0 else -1) if a >= 0]
    return i + min(anchors) if anchors else i


#: A terminal's answer to "what are you?" (Primary Device Attributes, DA1):
#: "a VT100 with advanced video" — the same bytes xterm.js sends.
_DA1_REPLY = "\x1b[?1;2c"

#: A DA1 query: CSI c or CSI 0 c.
_DA1_QUERY_RX = re.compile(rb"\x1b\[0?c")


def _asks_device_attributes(data: bytes) -> bool:
    """Does this output ask the terminal for its device attributes? (v1.245.0)

    Measured: Windows ConPTY sends a DA1 query as a shell starts and holds ALL
    of the child's output until something answers — first byte at 3.05 s
    unanswered, 0.06 s answered. An attached pane's xterm answers it; nothing
    else did, so a shell with no pane attached (every pane the daemon restores
    at boot, a Creative Studio session) stalled three seconds before its first
    byte. ``TerminalSession.read`` answers when no pane is attached to.
    """
    return bool(_DA1_QUERY_RX.search(data))


#: How far one attached pane may fall behind the live stream before it is cut
#: loose (v1.243.0). Generous — a TUI repainting a big screen is a few hundred
#: KB — so only a genuinely stalled reader (a phone on a dead link) ever hits it.
ATTACH_BACKLOG_MAX_BYTES = 8 * 1024 * 1024


class OutputSubscription:
    """One attached pane's share of a session's live output (v1.243.0).

    Every chunk :meth:`TerminalSession.read` takes off the PTY is appended to
    EVERY subscription, whichever caller did the reading. Before this, a read
    handed each chunk to exactly one caller: a second attach (a phone, a second
    window, a reload racing its own close) split the stream with the first, and
    the background drain could take the chunk in flight at the instant a pane
    attached. Either way a pane lost bytes out of the middle of an escape
    sequence and rendered the rest of it as text.

    Bounded: a reader ``limit`` bytes behind is marked :attr:`overflowed` and
    its backlog dropped. The route then closes that socket and the pane
    reconnects to a fresh replay — the daemon never buffers a dead link's
    gigabytes, and a pane never renders a stream with a hole in it.
    """

    def __init__(self, lock: threading.Lock, limit: int) -> None:
        # The SESSION's read lock: pushes happen under it (inside read()), and
        # take() holds it too, so a chunk is never half-delivered.
        self._lock = lock
        self._limit = limit
        self._chunks: deque[bytes] = deque()
        self._size = 0
        self.overflowed = False

    def _push(self, data: bytes) -> None:
        """Called by :meth:`TerminalSession.read` with the read lock held."""
        if self.overflowed:
            return
        self._chunks.append(data)
        self._size += len(data)
        if self._size > self._limit:
            self.overflowed = True
            self._chunks.clear()
            self._size = 0

    def take(self) -> bytes:
        """Everything delivered since the last take, as one chunk (``b""`` if none)."""
        with self._lock:
            if not self._chunks:
                return b""
            data = b"".join(self._chunks)
            self._chunks.clear()
            self._size = 0
        return data


class TerminalSession:
    """One real shell the user can type into, streamed over a WebSocket.

    The backend is injectable (tests pass a :class:`FakeBackend`); when omitted
    the best backend for the current OS is built via :func:`default_backend`.
    """

    def __init__(
        self,
        cwd: str | None = None,
        shell: str | None = None,
        *,
        argv: list[str] | None = None,
        cols: int = 80,
        rows: int = 24,
        backend: PtyBackend | None = None,
    ) -> None:
        if argv is None:
            shell, argv = resolve_shell(shell)
        self.id = new_id("term")
        self.cwd = cwd or str(Path.home())
        self.shell = shell or "shell"
        self.argv = list(argv)
        self.cols = cols
        self.rows = rows
        self.created_at = utcnow()
        self.backend: PtyBackend = backend if backend is not None else default_backend()
        self._started = False
        # Serializes write(): the WS handler, the studio /say endpoint, and the
        # studio automode background thread all type into the same PTY — without
        # a lock a Shift+Tab keystroke can land in the middle of a typed brief.
        self._write_lock = threading.Lock()
        # Serializes read() (v1.243.0). A Build pane's pump on the event loop
        # and the background drain thread can both read, and each chunk must
        # land in the tail and in every attached pane's subscription exactly
        # once, in order. subscribe() takes the same lock, so a pane's replay
        # snapshot and its live share can never overlap or leave a gap.
        self._read_lock = threading.Lock()
        self._subscribers: list[OutputSubscription] = []
        # Bounded tail of recent output — context for the per-terminal AI assist.
        self._tail = bytearray()
        # True once the tail has been head-trimmed (or restored from a sliced
        # snapshot): its first bytes may sit mid-sequence, so replay serves
        # from a safe boundary instead (see _safe_replay_start).
        self._tail_truncated = False
        # True when we fell back to a pipe-based shell (no real TTY) because the
        # PTY backend spawned a shell that died immediately (e.g. a frozen build
        # missing the ConPTY host exe). Commands still run; fancy TTY apps don't.
        self.degraded = False
        # --- Background auto-drain (for the Creative Studio) ------------------
        # A Build-page pane drains the PTY through its WebSocket; the Studio,
        # by contrast, drives the CLI purely over HTTP with NO socket attached.
        # Without a reader the output is never consumed: the tail stays blank,
        # auto-mode detection is blind, and a chatty full-screen TUI (Claude
        # Code) STALLS the moment the OS output buffer fills. start_autodrain()
        # spawns a reader that keeps the PTY flowing regardless. It steps aside
        # whenever a live pane is attached (see add_consumer) so the two never
        # race for the same bytes.
        self._consumers = 0
        self._consumer_lock = threading.Lock()
        self._drain_thread: threading.Thread | None = None
        self._drain_stop = threading.Event()
        # monotonic timestamp of the last NON-EMPTY read — "is the CLI actively
        # printing?" signal for the studio's phase detection (a running TUI
        # repaints its status bar about once a second).
        self.last_output_at: float = 0.0
        # Set the instant kill() runs. Real backends terminate the child
        # FIRE-AND-FORGET, and Windows can keep reporting the process alive
        # for a beat after terminate() returns (slow ConPTY teardown on
        # Server 2025 turned this into a CI-visible race: a say/write in
        # that window still saw alive=True). A session we killed is dead to
        # every caller the moment we killed it.
        self._killed = False

    def start(self, env: dict | None = None) -> "TerminalSession":
        """Spawn the shell (idempotent)."""
        if not self._started:
            self.backend.start(self.argv, self.cwd, env, self.cols, self.rows)
            self._started = True
        return self

    def write(self, data: str | bytes) -> None:
        with self._write_lock:  # one writer at a time — keystrokes never interleave
            self.backend.write(data)

    def read(self, max_bytes: int = 65536) -> bytes:
        """Non-blocking read of pending output (``b""`` if nothing ready).

        Every chunk is also delivered to each attached pane's
        :class:`OutputSubscription` (v1.243.0), so it no longer matters WHO
        reads — the pane's own pump, a second pane's, or the background drain
        thread. Serialized, so the tail and every subscription see each chunk
        once, in order."""
        answer_da1 = False
        with self._read_lock:
            data = self.backend.read_nonblocking(max_bytes)
            if data:
                self._tail += data
                if len(self._tail) > TAIL_MAX_BYTES:
                    del self._tail[: len(self._tail) - TAIL_MAX_BYTES]
                    self._tail_truncated = True
                self.last_output_at = time.monotonic()
                self.output_seq += 1
                for sub in self._subscribers:
                    sub._push(data)
                # Nobody attached means nobody to answer the terminal's
                # questions — see _asks_device_attributes (v1.245.0).
                answer_da1 = not self._subscribers and _asks_device_attributes(data)
        if answer_da1:
            try:
                self.write(_DA1_REPLY)
            except Exception:  # noqa: BLE001 — a dying PTY needs no answer
                pass
        return data

    # --- Live attaches (v1.243.0) -------------------------------------------

    def subscribe(
        self, limit: int = ATTACH_BACKLOG_MAX_BYTES
    ) -> tuple[bytes, OutputSubscription]:
        """Attach a live pane: its replay AND its share of everything after.

        The scrollback snapshot and the registration happen under ONE hold of
        the read lock, so no chunk can land between them — every byte is in
        the replay or in the subscription, never both and never neither."""
        with self._read_lock:
            history = self.scrollback_bytes()
            sub = OutputSubscription(self._read_lock, limit)
            self._subscribers.append(sub)
        self.add_consumer()
        return history, sub

    def unsubscribe(self, sub: OutputSubscription) -> None:
        """Detach a pane (idempotent). The last one out hands the PTY to the
        background drain — see :meth:`remove_consumer`."""
        with self._read_lock:
            try:
                self._subscribers.remove(sub)
            except ValueError:
                return  # already detached — keep the consumer count balanced
        self.remove_consumer()

    # --- Live consumers + background auto-drain --------------------------

    def add_consumer(self) -> None:
        """Register a live output consumer (a Build-page WebSocket pane). While
        any consumer is attached it does the reading, so the background
        auto-drain steps aside rather than run a second reader."""
        with self._consumer_lock:
            self._consumers += 1

    def remove_consumer(self) -> None:
        """Drop a previously-registered live consumer.

        THE LAST ONE OUT STARTS THE DRAIN (v1.243.0). A PTY nobody reads fills
        its output pipe, and the program in it BLOCKS on its next write — so a
        Claude left working in a Build pane simply stopped when the user
        switched to another page, and resumed only when they came back (the
        Studio hit this first; see start_autodrain). The drain keeps output
        flowing into the tail and steps aside again when a pane re-attaches."""
        with self._consumer_lock:
            if self._consumers > 0:
                self._consumers -= 1
            nobody = self._consumers == 0
        if nobody and self.alive:
            self.start_autodrain()

    @property
    def has_consumer(self) -> bool:
        with self._consumer_lock:
            return self._consumers > 0

    def start_autodrain(self) -> None:
        """Begin draining output in the background so it's captured even when no
        WebSocket is attached (the Creative Studio case). Idempotent — safe to
        call more than once on the same session."""
        if self._drain_thread is not None:
            return
        self._drain_stop.clear()
        thread = threading.Thread(
            target=self._drain_loop, name=f"drain-{self.id}", daemon=True
        )
        self._drain_thread = thread
        thread.start()

    def _drain_loop(self) -> None:
        """Keep the PTY flowing into the tail. Yields to a live pane (which
        reads and fills the tail itself); when alone, reads and discards the
        live bytes — the scrollback captured in the tail is what the Studio
        tail endpoint serves and what auto-mode detection reads."""
        while not self._drain_stop.is_set():
            if not self.alive:
                break
            if self.has_consumer:  # a WS pane is reading — one reader is enough
                # (a read here would no longer steal from it — read() delivers
                # to every attached pane — but two pollers is wasted work)
                time.sleep(0.05)
                continue
            try:
                data = self.read()  # into _tail; live bytes discarded (nobody watching)
            except Exception:  # pragma: no cover - a dying backend just ends the loop
                break
            if not data:
                time.sleep(0.03)

    def output_tail(self) -> str:
        """Recent output as CLEAN text (ANSI stripped) for the AI assist.

        Only the last ~32KB is decoded — the AI needs a short window, and the
        full scrollback can be up to :data:`TAIL_MAX_BYTES`."""
        text = bytes(self._tail[-32 * 1024:]).decode("utf-8", "replace")
        return _ANSI_RE.sub("", text)

    def scrollback_bytes(self) -> bytes:
        """The raw recent output (with ANSI intact) to REPLAY into a re-attaching
        pane so it renders its history instead of a blank screen. A truncated
        tail is served from a safe boundary — its raw head can sit mid escape
        sequence / mid code point and would render as garbage."""
        if self._tail_truncated:
            return bytes(self._tail[_safe_replay_start(self._tail):])
        return bytes(self._tail)

    def resize(self, cols: int, rows: int) -> None:
        self.cols = cols
        self.rows = rows
        self.backend.resize(cols, rows)

    def kill(self) -> None:
        self._drain_stop.set()  # stop the background reader before the PTY dies
        self._killed = True  # alive flips NOW — backend termination may lag
        self.backend.kill()

    @property
    def alive(self) -> bool:
        return self._started and not self._killed and self.backend.is_alive()

    @property
    def exit_code(self) -> int | None:
        return self.backend.exit_code

    # ---- what the agent in this pane is doing (v1.217.0) ------------------
    #: What the Launch catalog started here, when it knows ("claude" /
    #: "codex" / "pi"). Set by the caller; beats sniffing the scrollback.
    #: None for an ordinary shell.
    agent_cli: str | None = None
    #: v1.245.0: the CLI that was running here when the daemon last stopped.
    #: Set on RESTORE — the shell comes back fresh, the CLI died with the old
    #: daemon — so the pane can offer a one-click Resume instead of a chip
    #: claiming a CLI that is not running. Cleared on Resume or Dismiss.
    resume_cli: str | None = None
    #: v1.245.0: bumped on every non-empty read — how the periodic snapshot
    #: knows a pane printed since the last write.
    output_seq: int = 0
    #: A human handle for this pane, unique among live panes. Agents address
    #: panes by name; the id stays the stable machine handle.
    pane_name: str | None = None
    #: What this pane is ALLOWED to do (v1.238.0, plan §4.2 / D20) — the
    #: server-side half of the Capabilities checklist. ``None`` means "no
    #: capabilities": an upgraded install, a restored snapshot written before
    #: the field existed, and a hand-made session all read that way, and every
    #: gate treats it as all-``False``. Never read this dict directly — a raw
    #: value can be a JSON string — read :meth:`capability` or
    #: :meth:`effective_capabilities`.
    capabilities: dict[str, bool] | None = None
    #: Identity this pane exports to anything it runs (v1.217.0) — see
    #: `TerminalManager.create`. A plain dict, not the process environment:
    #: the shell is already spawned by the time we know the pane's id.
    pane_env_extra: dict[str, str] | None = None

    def pane_env(self) -> dict[str, str]:
        """The `IRONJARVIS_*` identity for this pane, or `{}` for a pane that
        predates it (a restored snapshot, a hand-made session)."""
        return dict(self.pane_env_extra or {})

    # ---- capabilities (v1.238.0) -----------------------------------------

    def effective_capabilities(self) -> dict[str, bool]:
        """The five capabilities as real booleans — what every surface shows."""
        return normalise_pane_capabilities(self.capabilities)

    def capability(self, name: str) -> bool:
        """Whether this pane has ``name`` enabled RIGHT NOW. Fail-closed.

        The one question a gate asks. A pane with no ``capabilities``, a pane
        whose snapshot predates the field, a name outside the canonical five,
        and a value that merely looks affirmative all answer ``False``.
        """
        return self.effective_capabilities().get(name, False)

    def update_capabilities(self, patch: Any) -> dict[str, bool]:
        """PARTIAL update: a key the caller did not send keeps its value.

        The Capabilities popover toggles ONE box and PATCHes, so a merge that
        replaced the whole mapping would silently clear the other four — the
        same shape of bug the remote-agent registry paid for when a re-POST
        destroyed the credential it did not carry. Sending ``{"files": false}``
        is how a capability is turned off; omitting it is how it is kept.
        """
        merged = self.effective_capabilities()
        if isinstance(patch, Mapping):
            supplied = {k: v for k, v in patch.items() if k in PANE_CAPABILITY_KEYS}
            merged = normalise_pane_capabilities({**merged, **supplied})
        self.capabilities = merged
        return dict(merged)

    def activity(self, *, seen: bool = True) -> "PaneActivity":
        """Classify what is happening in this pane, from its own output tail.

        `seen` is a fact about the UI (has the user looked since the last
        output?), so the caller supplies it — it is the only thing separating
        `done` from `idle`. See `terminals/agent_state.py` for why an
        unrecognised pane reports `unknown` rather than `idle`.
        """
        from . import agent_state

        # ONE classification per output change (v1.245.0). The Build page
        # polls every 2.5 s and each poll decoded a 32 KB tail and ran the
        # classifier's regexes over it for every pane — 93% of the daemon's
        # traffic, all of it on the one event loop. Nothing it reads changes
        # unless the pane printed, its CLI changed, or it died.
        alive = self.alive
        key = (self.output_seq, len(self._tail), self.agent_cli, seen, alive)
        cached = getattr(self, "_activity_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        result = agent_state.classify(
            self.output_tail(),
            cli=self.agent_cli,
            seen=seen,
            alive=alive,
        )
        self._activity_cache = (key, result)
        return result

    def info(self) -> dict[str, Any]:
        from .ai_clis import RESUME_COMMANDS

        act = self.activity()
        return {
            "id": self.id,
            "cwd": self.cwd,
            "shell": self.shell,
            "argv": list(self.argv),
            "cols": self.cols,
            "rows": self.rows,
            "alive": self.alive,
            "exit_code": self.exit_code,
            "degraded": self.degraded,
            "created_at": self.created_at.isoformat(),
            # v1.217.0 additive. `state` is never absent and never guesses:
            # "unknown" is a real answer meaning "we cannot tell", NOT a
            # missing field and NOT completion.
            "name": self.pane_name,
            "agent_cli": act.cli,
            "state": act.state.value,
            "state_line": act.line,
            # v1.245.0 additive: the CLI a restart ended, and the command that
            # resumes its last conversation ("" = no known way, so no button).
            "resume_cli": self.resume_cli,
            "resume_command": RESUME_COMMANDS.get(self.resume_cli or "", ""),
            # v1.238.0 additive, and ALWAYS all five keys: a surface that has to
            # ask whether the field is present would render a pane's
            # capabilities differently depending on when the pane was made.
            "capabilities": self.effective_capabilities(),
        }
