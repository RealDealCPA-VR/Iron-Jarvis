"""The side panel's conversation — run in the daemon, narrated to the browser.

THE SILENT FAILURE THIS MODULE PREVENTS: a second Jarvis. A chat window docked
in a browser is the easiest place in the product to accidentally build one —
its own loop, its own tool list, its own idea of what the user has allowed —
and the copy would drift from the real one the first time a gate changed. So
this module runs NO model, holds NO agent loop and decides NO policy. It builds
a ``ChatBody``, hands it to the same ``stream_chat_turn`` the dashboard's
``POST /chat/stream`` uses, and translates that turn's SSE frames into
``browser.panel_event`` frames. Everything that decides anything — the persona,
the router, the arming pass, the permission engine, the approval registry, the
usage ledger — is reached, not reimplemented.

THE SIDEBAR IS SCOPED TO THE BROWSER. It is a Jarvis window docked in Chrome,
and what it may reach is the browser and nothing else — reading tabs, reading a
page, and (at ``interactive``, behind the approval card) acting on one.
Anything else the user wants Jarvis to do — write a file, fetch a URL, run a
command, start an agent — belongs in the Iron Jarvis window, where the user is
looking at the surface that owns those capabilities. That is not a UI
preference; it is the security boundary of this whole feature, and it is
enforced by a HARD CEILING passed into the turn, not by hoping the model stays
on topic.

WHY A CEILING WAS NECESSARY, stated plainly because the first cut of this
module shipped without one and was exploited:

    ARMING IS GRANTING. ``routes/chat.py`` writes ``overrides[name] = "allow"``
    for every armed name and calls the result ``armed_grant``; the mid-turn
    card predicate is False for anything in that set. This module passes
    ``auto_tools=True`` with no explicit picks, so the ordinary autoselect pass
    armed whatever the user's SENTENCE suggested — and it was granted. With
    ``browser_access`` at ``read_only``, "write a file called pwn.txt with the
    summary" armed ``write_file`` and wrote a real file with ZERO approval
    cards. "read the open tab and open the url it mentions" armed ``web_fetch``,
    the page's own text named an attacker's URL, and the daemon fetched it —
    also with no card. ``_filter_browser_tools`` stopped neither: it returns
    early unless a ``browser_*`` name is present, so on those turns it
    inspected nothing at all.

    The ceiling is therefore not a second copy of the browser policy. It is a
    bound on the SET OF NAMES a panel turn may arm at all, computed live from
    the registry (:func:`browser_tool_ceiling`) as exactly the ``browser_*``
    family, and handed to the chat lane as ``tool_ceiling``. Anything the
    autoselect pass picks outside it is DROPPED BEFORE ARMING. Membership is
    tested with ``in``, so an unknown name is refused, never allowed, and an
    unreadable registry yields an EMPTY ceiling — a panel turn with no tools,
    which is the fail-closed direction.

``escalate_to_agent`` AND ``workflow_draft`` ARE OUTSIDE THE CEILING, and this
is the half that matters most. Both are appended to the turn as special specs
rather than armed, so a ceiling that filtered only the armed list would leave
them reachable — and ``escalate_to_agent`` hands the request to an agent
session with its OWN full tool set, which walks straight around every bound
this module holds. A sidebar that can start an agent is not scoped to the
browser. Neither name is a ``browser_*`` name, so the ceiling excludes both by
construction; :func:`_translate` still has a branch for the ``done`` payload
they ride in, because a turn that ends with neither text nor an explanation is
an empty answer, and the panel would render nothing at all.

WHAT THE PANEL MAY DO WITHIN THE CEILING FOLLOWS ``browser_access`` EXACTLY,
and it is not this module that enforces it. The turn arms itself through the
ordinary chat path, whose ``_filter_browser_tools`` gate reads the live
setting: ``read_only`` leaves the inspection tools, ``interactive`` leaves the
full set with the deny floor intact, so a page-acting call still pauses for the
approval card. THE CEILING IS AN ADDITIONAL BOUND, NEVER A REPLACEMENT — every
existing gate still runs, and this module adds no tool name of its own (it
picks nothing explicitly, precisely because a panel that armed
``browser_click`` would have consented on the user's behalf to the very click
the card exists to ask about). ``off`` never gets this far:
``ExtensionBackend._handle_panel`` refuses the frame and says so.

TWO MORE BOUNDS THIS MODULE OWNS, both closing demonstrated holes:

* **An approval this panel never saw is not this panel's to answer.**
  ``core.approvals.resolve`` has no ownership check of its own, so before
  v1.242.0 the only thing separating a panel from an agent session's pending
  ask was that ids are unguessable. That is a real defence and it was the
  ENTIRE defence, undocumented and untested. :class:`PanelTurns` now records
  every id it actually emitted (:attr:`PanelTurns._offered`) and refuses
  anything else out loud.
* **A pairing token must not be able to spend money at socket speed.** Before
  the sidebar, that credential could only ANSWER daemon-initiated directives;
  it can now INITIATE paid model turns, from a credential whose whole security
  boundary is one Pair press. Twelve sequential Sends produced twelve billed
  runs with no throttle. See :data:`PANEL_TURNS_PER_WINDOW`.

WHAT STOP AND STEER CANNOT DO — and the panel's own copy says both, because a
surface that implies otherwise is lying about what a click did:

* **Steering cannot interrupt a half-generated sentence.** A note joins the
  conversation at the next TOOL-ROUND boundary, which is the one place the
  turn's loop is already re-entrant and already checks for stop. If the turn
  ends before a boundary comes round, the note was never taken — and this
  module says so (:data:`STEER_NOT_TAKEN`), because the panel marks a note
  pending until it hears otherwise and would otherwise show a correction as
  landed when it never was.
* **Stop does not kill a tool that is already executing.** That tool runs on a
  worker thread (v1.228.0); the thread finishes and its write lands. Stop ends
  the answer being generated and prevents the NEXT round. A file already being
  written is still written.

ONE MORE HONEST EDGE, and it is why ``done`` is emitted from a ``finally``: a
stopped turn returns WITHOUT a terminal ``done`` SSE frame (it persists the
CANCELLED usage row and returns). A translator that only forwarded what it saw
would leave the panel spinning "working…" forever on the one press whose whole
purpose was to end that state.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from types import SimpleNamespace
from typing import Any

from ..core.logging import get_logger
from ..core.turns import TURNS
from . import protocol as P

logger = get_logger(__name__)

#: What the panel is told when a queued steer note was never consumed. The
#: panel renders its own sentence for this from the ``done`` frame; this is the
#: reason carried alongside it so a log or a later surface can say why.
STEER_NOT_TAKEN = "the turn ended before a step boundary came round"

#: Browser access words a panel turn may run under. ``off`` — and an unreadable
#: or unknown setting — is not here, and the omission is the gate: this tuple is
#: consulted with ``in``, so a word nobody has reasoned about fails CLOSED.
PANEL_ACCESS_ALLOWED: tuple[str, ...] = ("read_only", "interactive")

#: The prefix that defines the sidebar's ceiling. ONE string, read against the
#: LIVE registry rather than a list of names written here, for the same reason
#: ``_filter_browser_tools`` uses a prefix: a ceiling that enumerated names
#: would silently stop covering the browser tools a later ship registers, and
#: the failure would be a sidebar that cannot see the page — annoying — or, if
#: someone "fixed" it by widening the ceiling, a sidebar that can write files.
BROWSER_TOOL_PREFIX = "browser_"

#: How long the sidebar's send budget looks back, in seconds, and how many
#: turns fit in it.
#:
#: THE SILENT FAILURE: unbounded spend from a credential whose entire security
#: boundary is one Pair press. Until the sidebar, the pairing token could only
#: ANSWER directives the daemon had initiated; it can now START paid model
#: turns, and a client driving the socket in a loop is limited only by how fast
#: it can write frames — twelve sequential Sends produced twelve billed runs.
#:
#: THE NUMBERS, and why these: a panel is single-turn (a Send while running is
#: refused outright), so the only way to reach the limit is to ask twelve
#: questions in a minute and read none of the answers. A person typing into a
#: sidebar does not do that; a loop does. Twelve is comfortably above any human
#: rate and far below "unbounded", which is the only property that matters —
#: the bound exists so an abusive client hits a wall with a sentence, not so a
#: user is rationed. The window is ROLLING and measured on the event loop's
#: monotonic clock, never on wall-clock time, so a system clock change cannot
#: widen it and nothing here asserts a duration.
PANEL_TURN_WINDOW_S = 60.0
PANEL_TURNS_PER_WINDOW = 12


def browser_tool_ceiling(platform: Any) -> frozenset[str]:
    """Every ``browser_*`` name this daemon actually registers — the ceiling.

    THE SILENT FAILURE THIS PREVENTS: a sidebar with the general tool set. See
    the module docstring for the demonstration; in one line, a panel turn runs
    ``auto_tools=True`` on a sentence nobody vetted, and arming is granting.

    FAIL CLOSED, twice over. A registry that cannot be read answers an EMPTY
    ceiling — a turn with no tools at all — rather than ``None``, which the
    chat lane reads as "no ceiling" and is precisely the state this function
    exists to prevent. And membership is later tested with ``in``, so a name
    this set has never heard of is dropped rather than allowed.

    Read LIVE, per turn, and never cached: a tool registered after boot (an MCP
    server, a later ship) belongs to the browser family or it does not, and a
    ceiling frozen at install time would answer for a registry that no longer
    exists.
    """
    try:
        names = platform.registry.names()
    except Exception:  # noqa: BLE001 — an unreadable registry is not a grant
        logger.debug("panel tool ceiling unreadable; arming nothing", exc_info=True)
        return frozenset()
    return frozenset(
        str(name) for name in names if str(name).startswith(BROWSER_TOOL_PREFIX)
    )


def parse_sse(chunk: str) -> list[tuple[str, dict[str, Any]]]:
    """Split one SSE chunk into ``(event, data)`` pairs.

    ``stream_chat_turn`` yields whole frames (``event:``/``data:``/blank line)
    and bare ``": keepalive"`` comments. A comment, an unparseable ``data:``
    and a frame with no ``event:`` all answer NOTHING rather than raising: this
    runs inside the socket's read loop, and a malformed frame must cost the
    panel one missing line, never the connection.
    """
    out: list[tuple[str, dict[str, Any]]] = []
    for block in str(chunk or "").split("\n\n"):
        event = ""
        data = ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if not event:
            continue
        try:
            payload = json.loads(data) if data else {}
        except (ValueError, TypeError):
            continue
        out.append((event, payload if isinstance(payload, dict) else {}))
    return out


class PanelTurns:
    """One panel conversation: start it, narrate it, stop it, answer its asks.

    Process-local and single-turn on purpose, for the reason the approval and
    turn registries are: there is exactly ONE add-on socket
    (``ExtensionBackend`` holds one connection), so there is exactly one panel
    to narrate to. State that outlived the process would only manufacture
    orphans — a turn id nothing is running.
    """

    def __init__(self, platform: Any, personas: dict | None = None) -> None:
        self.platform = platform
        self.personas = personas or {}
        #: The running turn's task and id, or ``(None, "")``.
        self._task: asyncio.Task | None = None
        self._turn_id: str = ""
        #: Notes queued by ``steer`` and not yet consumed, oldest first.
        self._steers: list[dict[str, str]] = []
        #: The connection the running turn is narrating to.
        self._conn: Any = None
        #: Approval ids THIS panel emitted a card for. ``resolve`` has no
        #: ownership check of its own, so without this the panel could answer
        #: an ask filed by an agent session on another thread — proven, and the
        #: only thing that had ever stopped it was that ids are unguessable.
        self._offered: set[str] = set()
        #: Monotonic timestamps of the turns started inside the rolling
        #: window. See :data:`PANEL_TURNS_PER_WINDOW`.
        self._starts: list[float] = []
        #: Whether the running turn has streamed a single visible token yet.
        #: The panel renders from ``delta`` frames, so a turn that ends with
        #: none of them renders as an EMPTY answer unless this module says
        #: something true instead (:meth:`_translate`, the ``done`` branch).
        self._delta_seen = False

    # --- narration --------------------------------------------------------

    @property
    def running(self) -> bool:
        task = self._task
        return task is not None and not task.done()

    async def emit(self, conn: Any, event: str, payload: dict[str, Any]) -> None:
        """Send one ``browser.panel_event``. Never raises — a dead socket is an
        expected outcome mid-turn (the user closed the browser), and an
        exception here would take down the read loop that owns this call."""
        if conn is None:
            return
        try:
            await conn.send(P.panel_event_frame(event, payload))
        except Exception:  # noqa: BLE001 — narration must never break the socket
            logger.debug("panel event send failed (%s)", event, exc_info=True)

    # --- the action vocabulary -------------------------------------------

    async def handle(self, conn: Any, action: str, params: dict[str, Any]) -> None:
        """Apply one ``browser.panel`` action.

        Every branch ANSWERS. An action this daemon does not know is reported
        to the panel rather than dropped: a panel from a newer add-on pressing
        a button that does nothing, silently, is the failure this whole module
        is written against.
        """
        act = str(action or "")
        params = params if isinstance(params, dict) else {}
        if act == P.PANEL_ACTION_OPEN:
            await self.emit(conn, P.PANEL_EVENT_STATE, {"running": self.running})
            return
        if act == P.PANEL_ACTION_SEND:
            await self._send(conn, str(params.get("text") or "").strip())
            return
        if act == P.PANEL_ACTION_STOP:
            await self._stop(conn)
            return
        if act == P.PANEL_ACTION_STEER:
            await self._steer(conn, params)
            return
        if act in (P.PANEL_ACTION_APPROVE, P.PANEL_ACTION_DENY):
            await self._decide(conn, act, str(params.get("id") or ""))
            return
        if act == P.PANEL_ACTION_CLOSE:
            # The panel is gone: a turn narrating to nobody keeps billing.
            # Stop is cooperative, so this is a request, not a kill.
            if self._turn_id:
                TURNS.stop(self._turn_id)
            return
        await self.emit(
            conn,
            P.PANEL_EVENT_ERROR,
            {"text": f"Iron Jarvis does not know the sidebar action {act!r}."},
        )

    async def _send(self, conn: Any, text: str) -> None:
        if not text:
            await self.emit(
                conn, P.PANEL_EVENT_ERROR, {"text": "There was nothing to send."}
            )
            return
        if self.running:
            # REFUSED, not queued. A second turn would race the first for the
            # same panel and the user would watch two answers interleave with
            # no way to tell which question either belonged to.
            await self.emit(
                conn,
                P.PANEL_EVENT_ERROR,
                {
                    "text": "Iron Jarvis is still working on the last message."
                    " Stop it first."
                },
            )
            return
        refusal = self._over_budget()
        if refusal:
            await self.emit(conn, P.PANEL_EVENT_ERROR, {"text": refusal,
                                                        "reason": "rate_limited"})
            return
        self._conn = conn
        self._turn_id = f"panel_{secrets.token_hex(8)}"
        self._steers.clear()
        # A new question is a new set of cards. Ids from the last turn cannot
        # be answered any more anyway (their turn has gone), and keeping them
        # would only widen what this panel may resolve.
        self._offered.clear()
        self._delta_seen = False
        await self.emit(conn, P.PANEL_EVENT_STATE, {"running": True})
        self._task = asyncio.ensure_future(self._run(conn, text, self._turn_id))

    def _over_budget(self) -> str:
        """"" if this Send fits the rolling budget, else the sentence to say.

        THE SILENT FAILURE: a paired browser billing the user at socket speed.
        The refusal NAMES the limit and when it lifts, because a rate limit the
        user cannot see reads as the sidebar being broken — they press Send,
        nothing happens, and nothing says why.

        Monotonic, off the event loop's own clock. A timestamp list rather than
        a counter+reset: a fixed bucket lets a client spend the whole allowance
        at the end of one window and the whole of the next immediately after,
        which is twice the rate the constant claims.
        """
        try:
            now = asyncio.get_running_loop().time()
        except RuntimeError:  # pragma: no cover — no loop means no turn either
            return ""
        self._starts = [t for t in self._starts if now - t < PANEL_TURN_WINDOW_S]
        if len(self._starts) < PANEL_TURNS_PER_WINDOW:
            self._starts.append(now)
            return ""
        wait_s = int(PANEL_TURN_WINDOW_S - (now - self._starts[0])) + 1
        return (
            f"The sidebar is limited to {PANEL_TURNS_PER_WINDOW} messages a"
            f" minute, and that many have just been sent. Try again in about"
            f" {wait_s} seconds, or use the Iron Jarvis window."
        )

    async def _stop(self, conn: Any) -> None:
        if not self._turn_id or not TURNS.stop(self._turn_id):
            # Nothing addressable is running. Say so plainly rather than
            # leaving the panel's Stop button looking like it worked on
            # something: the state frame is what flips it back to idle.
            await self.emit(conn, P.PANEL_EVENT_STATE, {"running": self.running})

    async def _steer(self, conn: Any, params: dict[str, Any]) -> None:
        note = str(params.get("text") or "").strip()
        note_id = str(params.get("id") or "")
        if not note:
            return
        if not self.running:
            # A note with no turn to join was never taken, and the panel marks
            # it pending until something says otherwise. `done` is the frame
            # that flushes pending notes as not taken.
            await self.emit(
                conn, P.PANEL_EVENT_DONE, {"reason": STEER_NOT_TAKEN, "text": ""}
            )
            return
        self._steers.append({"id": note_id, "text": note})

    async def _decide(self, conn: Any, action: str, approval_id: str) -> None:
        """Answer a mid-turn approval through THE approval registry.

        The same one ``POST /chat/approvals/{id}`` answers and the same one the
        waiting turn is parked on — reached through ``routes.chat._approvals``
        rather than read off the platform here, because that helper owns the
        fallback a test double needs and a second reading of it is how two
        surfaces come to answer different copies. A chat-lane ask is announced
        ONLY on its own stream (it is deliberately absent from
        ``GET /chat/approvals/pending``), which is why the panel is told down
        this socket and answers back down it.

        ``approve`` resolves ``once``, never ``conversation``: the panel shows
        one card for one call, and widening a grant the user was not asked to
        widen is a consent nobody gave.

        AN ID THIS PANEL NEVER OFFERED IS REFUSED, and that check is new.
        ``resolve`` has no ownership check of its own — proven by answering an
        ask filed by a separate agent session, on another thread, with a
        ``panel.approve``, and getting no error at all. The only thing that had
        ever stopped it was that ids are unguessable, which is a real defence
        and was also the ENTIRE defence, undocumented and untested. The
        registry is process-local and shared by chat, the agent runtime and
        MCP, so the panel must bring its own answer to "was this card mine?":
        :attr:`_offered`, written at the one place a card is emitted
        (:meth:`_translate`). The refusal is SPOKEN, like every other path out
        of this class — a button that silently does nothing is the failure this
        module is written against.
        """
        from ..daemon.routes.chat import _approvals

        if approval_id not in self._offered:
            await self.emit(
                conn,
                P.PANEL_EVENT_ERROR,
                {
                    "text": "That request was not asked in this sidebar, so"
                    " the sidebar will not answer it.",
                    "reason": "not_this_panel",
                },
            )
            return
        decision = "once" if action == P.PANEL_ACTION_APPROVE else "deny"
        try:
            ok = _approvals(SimpleNamespace(platform=self.platform)).resolve(
                approval_id, decision
            )
        except Exception:  # noqa: BLE001 — an answer must never break the socket
            logger.debug("panel approval resolve failed", exc_info=True)
            ok = False
        if not ok:
            await self.emit(
                conn,
                P.PANEL_EVENT_ERROR,
                {"text": "That request was already answered or has expired."},
            )

    # --- the turn ---------------------------------------------------------

    async def take_steer(self) -> str:
        """Hand the turn every queued note, and tell the panel each landed.

        Called BY the turn at its tool-round boundary, so being called is what
        makes a note consumed — there is no second flag to drift. The
        ``steered`` frames are emitted here, at the moment of consumption, for
        the same reason: the panel shows a note as pending until this frame,
        and any earlier emission would tell the user their correction landed
        before it had.
        """
        if not self._steers:
            return ""
        taken = self._steers[:]
        self._steers.clear()
        for note in taken:
            await self.emit(
                self._conn,
                P.PANEL_EVENT_STEERED,
                {"id": note["id"], "text": note["text"]},
            )
        return "\n".join(note["text"] for note in taken)

    async def _run(self, conn: Any, text: str, turn_id: str) -> None:
        """Run one turn and translate its SSE frames into panel events."""
        from ..daemon.chat_stream import stream_chat_turn
        from ..daemon.schemas import ChatBody, ChatMessageBody

        body = ChatBody(
            messages=[ChatMessageBody(role="user", content=text)],
            # NO explicit tool picks: arming is granting (see the module
            # docstring). `auto_tools` runs the ordinary selection pass, so the
            # read tier is armed and the page-acting tier is armed
            # VISIBLE-BUT-UNGRANTED and pauses for the card.
            auto_tools=True,
            turn_id=turn_id,
        )
        # THE CEILING. Computed here, per turn, from the live registry, and
        # passed to the chat lane — which drops anything outside it BEFORE the
        # armed list becomes `armed_grant`. Without it the autoselect pass
        # armed (and thereby granted) whatever the user's sentence suggested:
        # `write_file` wrote a real file from a read_only sidebar with no card.
        ceiling = browser_tool_ceiling(self.platform)
        emitted_done = False
        try:
            gen = await stream_chat_turn(
                self.platform,
                self.personas,
                body,
                steer_source=self.take_steer,
                tool_ceiling=ceiling,
            )
            async for chunk in gen:
                for event, data in parse_sse(chunk):
                    if event == "done":
                        emitted_done = True
                    await self._translate(conn, event, data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — an honest failure, never silence
            logger.debug("panel turn failed", exc_info=True)
            await self.emit(
                conn,
                P.PANEL_EVENT_ERROR,
                {"text": f"Iron Jarvis could not finish that: {exc}"},
            )
            emitted_done = True
        finally:
            self._task = None
            self._turn_id = ""
            if not emitted_done:
                # A STOPPED turn returns with no terminal `done` frame — it
                # writes the CANCELLED usage row and returns. Without this the
                # panel would spin "working…" forever on the one press whose
                # entire purpose was to end that state.
                await self.emit(
                    conn, P.PANEL_EVENT_DONE, {"reason": "stopped", "text": ""}
                )
            # Anything still queued was never read by anybody. The `done`
            # above is what the panel turns into "not taken".
            self._steers.clear()

    def _empty_answer(self, data: dict[str, Any]) -> str:
        """What to say when a turn ends having said nothing (F5).

        THE SILENT FAILURE: a sidebar that swallows a question. Two of the
        three ways a turn can end wordless are DECLARED EXITS the panel does
        not implement — ``escalate_to_agent`` and ``workflow_draft`` ride in
        the ``done`` payload rather than in tokens — and neither is offered to
        a panel turn any more, because the ceiling excludes both (module
        docstring). This method still handles them, for two reasons: the
        ``escalate`` flag is ALSO set by the chat lane's own tool-round
        exhaustion, which no ceiling can prevent; and a surface whose honesty
        depends on a bound in another module is one refactor from lying again.

        Every sentence names the Iron Jarvis window, because that is where the
        work the sidebar declined to do can actually be done.
        """
        if data.get("escalate"):
            reason = str(data.get("escalate_reason") or "").strip()
            tail = f" ({reason})" if reason else ""
            return (
                "This needs the full Iron Jarvis agent, which the sidebar does"
                f" not start{tail}. Ask again in the Iron Jarvis window."
            )
        if data.get("workflow_draft"):
            return (
                "Iron Jarvis drafted a reusable workflow for this. The sidebar"
                " cannot show it — open the Iron Jarvis window to review and"
                " save it."
            )
        return "Iron Jarvis finished this turn without an answer."

    async def _translate(self, conn: Any, event: str, data: dict[str, Any]) -> None:
        """One SSE frame -> zero or one ``browser.panel_event``.

        The mapping is deliberately narrow. ``round``, ``reset`` and
        ``approval_resolved`` are the turn's own bookkeeping and have no panel
        vocabulary; forwarding them as something else would invent a frame the
        panel would have to guess the meaning of.
        """
        if event == "token":
            piece = str(data.get("text") or "")
            if piece:
                self._delta_seen = True
            await self.emit(conn, P.PANEL_EVENT_DELTA, {"text": piece})
            return
        if event == "tool_call":
            name = str(data.get("name") or "a step")
            if data.get("status") == "finished":
                word = "finished" if data.get("ok") else "could not finish"
                await self.emit(
                    conn, P.PANEL_EVENT_TOOL, {"name": name, "text": f"{name} {word}."}
                )
            else:
                await self.emit(
                    conn, P.PANEL_EVENT_TOOL, {"name": name, "text": f"Running {name}…"}
                )
            return
        if event == "approval":
            tool = str(data.get("tool") or "a step")
            approval_id = str(data.get("id") or "")
            # THE ONE PLACE A CARD IS EMITTED, so the one place ownership is
            # recorded. `_decide` refuses anything absent from this set.
            if approval_id:
                self._offered.add(approval_id)
            await self.emit(
                conn,
                P.PANEL_EVENT_APPROVAL,
                {
                    "id": approval_id,
                    "tool": tool,
                    "text": f"Iron Jarvis wants to run {tool}. Allow it?",
                },
            )
            return
        if event == "done":
            # NEVER AN EMPTY ANSWER. The panel paints its reply from `delta`
            # frames and ignores this frame's text entirely, so a turn that
            # streamed nothing renders as a question that vanished. Three
            # turns end that way and every one used to: an `escalate` and a
            # `workflow_draft` ride HOME IN THIS PAYLOAD with no token frames
            # at all (`_translate` had no branch for either, which is F5), and
            # a model can simply answer with an empty string. Say something
            # TRUE instead of nothing — and say it as a `delta`, because that
            # is the only frame the panel renders as words.
            text = str(data.get("text") or "")
            if not self._delta_seen and not text:
                await self.emit(
                    conn, P.PANEL_EVENT_DELTA, {"text": self._empty_answer(data)}
                )
            await self.emit(conn, P.PANEL_EVENT_DONE, {"text": text})
            return
        if event == "error":
            await self.emit(
                conn,
                P.PANEL_EVENT_ERROR,
                {
                    "text": str(
                        data.get("detail") or "Iron Jarvis could not finish that."
                    )
                },
            )
            return


def install(deps: Any) -> PanelTurns | None:
    """Give the add-on socket a panel handler, and return it.

    Wired HERE and not in the transport because ``ExtensionBackend`` holds no
    policy and knows nothing about chat — the same arrangement
    ``access_reader`` and ``snapshot_invalidator`` already use. ``None`` when
    there is no browser backend to install onto (a test app built without
    one), never an exception: a missing capability must not break boot.
    """
    platform = getattr(deps, "platform", None)
    backend = getattr(getattr(platform, "browser", None), "backend", None)
    if backend is None:
        return None
    turns = PanelTurns(platform, getattr(deps, "_PERSONAS", {}) or {})
    try:
        backend.panel_handler = turns.handle
    except Exception:  # noqa: BLE001 — a stand-in backend that refuses the attribute
        logger.debug("browser backend takes no panel_handler", exc_info=True)
        return None
    return turns
