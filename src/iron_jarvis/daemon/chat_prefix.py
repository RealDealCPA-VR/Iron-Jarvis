"""The two chat lanes' ONE helper for a chat's stable prefix (the intelligence
plan, release 7). The contract and the pure parts are in ``context/prefix``;
the tool names a chat keeps between replies are in ``daemon/chat_kept``. This
module is the seam both lanes call, so they cannot drift:

``begin`` (when the reply starts) -> ``keep_conn`` (the switched-on
extensions' tools) -> ``attached`` (the attachments seam) -> ``keep_auto``
(after the arming pass) -> ``settle`` (right after the tool definitions are
built) -> ``when_sent`` (the Tools seam: every line that names the reply's
tools) -> ``later`` (a per-message section that used to join the system
prompt) -> ``place`` (where the query tail used to be joined to the system
prompt: the final plan, the messages to send, the save of the tool names).

MIRROR NOTE (lock-step): ``chat_turn.run_chat_turn`` and
``routes/chat.py``'s stream lane call the same methods at the same seams.

A CODING SESSION (``harness=``) is not touched: every method hands back what
it was given and ``place`` joins the tail to the system prompt as before.

NOTHING OF A MESSAGE OR A FILE IS KEPT. An attached file's text rides ONCE,
beside the message it came with, like what was remembered for that question.
Later replies send that message as the person's own words only; the file
stays NAMED for follow-ups by the carried-files block (v1.251.0), which rides
beside the newest message too. The only thing saved is tool NAMES.

WHAT STAYS FOR THE CHAT, AND WHAT DOES NOT. A tool definition the chat's last
reply was sent stays in the next reply's list, in the same place, only when
the reply could have armed it by itself anyway:

* a built-in tool NAMED on :data:`STAY_READERS` (readers of Iron Jarvis's
  own local data), while Auto is on for the chat. A tool that changes something is armed by
  the words of the message that asks for the change, exactly as before
  ("change needs intent"), so it is never kept;
* an extension's tool, through :meth:`StableTurn.keep_conn` and
  ``mcp/reach.widen``, at the tier that extension has in THIS reply (its
  approval, pins and trust decide);
* a tool the user ticked stays while it is ticked (the page sends the ticks).

Everything else the lanes arm by their own rules each reply. A kept name is a
definition to SEND: the registry's gate decides every call as before, and the
low-trust, Local only and text-only filters run after the keeping, so a tool
the reply may not use is dropped before anything is sent.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from ..context import prefix as P
from . import chat_kept

log = logging.getLogger(__name__)

ATTACH_HEAD = "\n\n# Attachments (provided by the user this turn)"

#: THE TOOLS THAT MAY STAY FOR A CHAT, BY NAME. A tool that is armed on a
#: reply ONLY because it stayed reads data that is already on this PC, starts
#: no process and makes no network or model call (read line by line; the
#: reasons are in ``docs/rules/chat.md``). Nine of them CAN do more when a
#: message asks: five workbook readers open the workbook that is open in
#: Excel (a ``live:`` path starts the Excel helper), and four file readers
#: read a scan with a model call per page. Armed only by staying they do
#: neither (``ToolContext.stayed_only``, set from :meth:`StableTurn.
#: stayed_only`): the workbook reader says in one sentence that the open
#: workbook is read when the message asks, and the file reader returns the
#: text it has (and a reading already made of that file) and says the
#: scanned pages were not read. A
#: tool that is NOT named here never stays, whatever it declares: a new
#: read-only tool is not sticky until someone adds it on purpose (a
#: registry-wide test holds that). NEVER here: anything in
#: ``egress.INTERNET_TOOLS`` (the web is armed by a message that asks for
#: it), any ``browser_*`` tool (it reaches the person's live tab),
#: ``view_image`` or anything else that makes a model call of its own,
#: anything that starts a process (``run_doctor``), ``code_load``, every tool
#: that changes something, and the two memory searches ``recall`` and
#: ``ltm_search``: each can send a query the MODEL wrote to a memory base
#: that is not on this PC (Notion, a cloud drive, a retrieval service,
#: another machine) which the reply's own grounding does not ask (``recall``
#: searches every base in a Local only chat, where grounding searches only
#: the ones on this PC; ``ltm_search`` searches every base in a project
#: bound to a few). They are armed by the words of a message, as before.
STAY_READERS = frozenset({
    # files on this PC (each read is still confined by the file policy)
    "read_file", "read_document", "list_files", "list_folder", "file_search",
    "extract_pdf", "pdf_form_fields", "compare_documents", "redact_scan", "image_info",
    # a workbook's contents, read only
    "excel_profile", "excel_query", "excel_read", "excel_sheet_spec", "excel_formula_check",
    "excel_accounts_diff",
    # history and the work record, in this PC's own database
    "history_search", "work_history",
    # Iron Jarvis's own guides, settings, lists and state (kept answers only: nothing is probed)
    "guide_search", "app_search", "app_status", "pack_status", "workflow_list", "chat_fullness",
    "config_list", "app_health", "code_search", "downloads_list",
    # arithmetic only: it reads and writes nothing
    "calculate",
})

#: The names on the list that can do MORE than read this PC's data when a
#: message asks (see above): what ``ToolContext.stayed_only`` is read for.
LIVE_WORKBOOK_READERS = frozenset({
    "excel_read", "excel_profile", "excel_query", "excel_formula_check", "excel_sheet_spec",
})
SCAN_READERS = frozenset({"read_file", "read_document", "extract_pdf", "redact_scan"})
#: ``file_search`` by meaning asks a model (one call for the query, and one a
#: file chunk while its index is built): armed only by staying it searches by
#: words.
MEANING_SEARCHERS = frozenset({"file_search"})

#: The person's own words NAME A FILE (a name with an extension, or a path)...
_NAMES_A_FILE = re.compile(
    r"(?:\b[\w\-]+\.[A-Za-z][A-Za-z0-9]{1,4}\b)"          # ledger.txt, c4.png
    r"|(?:\b[A-Za-z]:[\\/])|(?:\\\\[\w.\-]+)|(?:~[\\/])|(?:(?:^|\s)/[\w.\-]+/)",   # C:\x, \\host, ~/x, /a/b
)
#: ...or ask to read, open, scan or OCR a file.
_ASKS_TO_READ = re.compile(
    r"\bocr\b|\b(?:read|open|scan|transcribe)\w*\b[^.?!\n]{0,60}"
    r"\b(?:file|document|pdf|scan|image|picture|photo|page|attachment)s?\b",
    re.IGNORECASE,
)


def asks_for_a_file(text: Any) -> bool:
    """Do the person's own words plainly ask for a file: do they name one (a
    name with an extension, or a path), or say read, open, scan or OCR of a
    file? Then a file reader the chat already holds is asked for by THIS
    message, whichever reader the model picks. Never raises."""
    try:
        words = P.own_words(str(text or ""))
        return bool(_NAMES_A_FILE.search(words) or _ASKS_TO_READ.search(words))
    except Exception:  # noqa: BLE001 — cannot tell: the narrower behaviour stands
        return False


def may_stay(registry: Any, name: str, permissions: Any = None) -> bool:
    """May the built-in tool *name* stay for a chat without its words? Only a
    name on :data:`STAY_READERS` that is registered AND that the user's own
    setting lets run without asking. A kept tool is armed like any other, and
    arming grants the call: a tool the user set to "Ask before each use" or
    "Never run" after the chat was sent it must not stay (the words of a
    message arm it as before, by the arming pass's own rules). *permissions*
    is the platform's permission engine; without one nothing is known to be
    set otherwise. Never raises; cannot tell = it does not stay."""
    try:
        if name not in STAY_READERS:
            return False
        tool = registry.get(name)
        if tool is None:
            return False
        if permissions is None:
            return True
        perm_key = getattr(tool, "perm_key", None)
        key = perm_key() if callable(perm_key) else name
        return getattr(permissions.mode_for(key), "value", "") == "allow"
    except Exception:  # noqa: BLE001 — cannot tell: it does not stay
        return False


def reads_only(registry: Any, name: str) -> bool:
    """Is the EXTENSION tool *name* a reader by its own annotation (MCP
    ``readOnlyHint: true``, which the registry reads as
    ``Reversibility.READONLY``)? Unknown or unannotated = no: a tool that
    may change something is armed by the words of its own message, never
    kept. Never raises."""
    try:
        from ..tools.base import Reversibility

        tool = registry.get(name)
        return tool is not None and getattr(tool, "reversibility", None) == Reversibility.READONLY
    except Exception:  # noqa: BLE001
        return False


def keep_ceiling(ceiling: "int | None", standing: int, local: bool) -> int:
    """The most named tools a chat reply holds once kept readers are counted.

    * a model served from the owner's own machine or network, or one whose
      provider is not known yet (*local*): the lanes' standing cap
      (*standing*, six). A small local model picks worse from a wide menu;
    * every other model: the bound ``mcp/reach`` already puts on tools that
      continue from the reply before (``MAX_CONTINUED``, twelve);
    * a MEASURED ceiling always wins when it is lower than either.
    """
    from ..mcp.reach import MAX_CONTINUED

    base = int(standing) if local else max(int(standing), MAX_CONTINUED)
    return min(base, int(ceiling)) if ceiling is not None else base


def is_local_model(d: Any, provider: str) -> bool:
    """Is the reply's model served from the owner's own machine or network?
    ``providers.local.is_local_provider`` (``ollama``, ``custom``, the
    ``fleet-*`` servers) asked of the reply's provider: the pick, else the
    configured default. By name only (no adapter is built: this runs on the
    loop). True when the provider is not known at this point: the narrower
    cap. Under Auto routing it CANNOT be known here: the router's routing
    step picks the model from the finished request (its instructions,
    messages and this very tool list) inside the model call, after the tool
    list is settled, and it may fall back to another provider; asking it
    earlier would be a second routing call whose answer could differ."""
    from ..providers.local import is_local_provider

    try:
        name = (provider or "").strip() or str(getattr(d.platform.config, "default_provider", "") or "")
        if not name or name == "auto":
            return True
        return bool(is_local_provider(name))
    except Exception:  # noqa: BLE001 — cannot tell: the narrower cap
        return True


class AttachBlock(str):
    """``_prepare_attachments``'s block (the same string as ever), which also
    knows its two halves: ``files`` (each attached file's own text: data, it
    rides inside the fence) and ``once`` (Iron Jarvis's own lines: which
    tools can open or change each file in this reply, a picture that was not
    seen, the conversation's earlier files by name)."""

    files: str = ""
    once: str = ""

    def __new__(cls, text: str = "", files: str = "", once: str = "") -> "AttachBlock":
        self = super().__new__(cls, text)
        self.files = files
        self.once = once
        return self


class SentMessages(list):
    """The messages of ONE plain chat reply, as :meth:`StableTurn.place`
    built them. The ONE place everything added later in the reply passes
    through (a tool round's assistant turn and its results, a note in the
    user's place): its content is neutralised of the block's marker lines
    (``prefix.neutral``), so a file read in a tool round, or a model that
    echoes them, cannot put a second "added by the app" line in the request.
    A fenced result was neutralised already (``wrap_untrusted``); this covers
    the unfenced ones. Deterministic: the same text gives the same bytes."""

    @staticmethod
    def _clean(message: Any) -> Any:
        try:
            content = getattr(message, "content", None)
            if isinstance(content, str) and "[" in content:
                clean = P.neutral(content)
                if clean != content:
                    message.content = clean
        except Exception:  # noqa: BLE001 — the message goes as it came
            pass
        return message

    def append(self, message: Any) -> None:
        super().append(self._clean(message))

    def extend(self, messages: Any) -> None:
        super().extend(self._clean(m) for m in messages)

    def insert(self, index: Any, message: Any) -> None:
        super().insert(index, self._clean(message))

    def __iadd__(self, messages: Any) -> "SentMessages":
        self.extend(messages)
        return self


def _newest_words(body: Any) -> str:
    """The newest user message of *body*, as the person typed it ("" = none)."""
    for message in reversed(list(getattr(body, "messages", None) or [])):
        if str(getattr(message, "role", "") or "") == "user":
            content = getattr(message, "content", "")
            return content if isinstance(content, str) else ""
    return ""


def _user_index(messages: Any) -> int:
    for i in range(len(messages) - 1, -1, -1):
        if str(getattr(messages[i], "role", "") or "") == "user":
            return i
    return -1


class StableTurn:
    """One reply's hold on its chat's prefix. Built by :func:`begin`."""

    def __init__(self, *, active: bool, engine: Any = None, thread: str = "", turn: str = "",
                 kept: "chat_kept.Kept | None" = None, low_trust: bool = False) -> None:
        self.active = bool(active)
        self.engine = engine
        self.thread = thread
        self.turn = turn
        self.kept = kept or chat_kept.Kept()
        self.low_trust = bool(low_trust)
        self._later: list[str] = []
        self._when_sent: list = []
        self._built_last: "str | None" = None
        self._stayed: "frozenset[str]" = frozenset()
        self._asked: "frozenset[str]" = frozenset()
        self._order: list[str] = list(self.kept.tools)

    # -- the attachments seam ------------------------------------------------ #
    def attached(self, attach_block: Any) -> P.Tail:
        """The attachments section as it joins the tail: the files' own text
        as what was FOUND (inside the fence), then Iron Jarvis's own lines
        about them, read last. It rides ONCE, with this message."""
        whole = ATTACH_HEAD + str(attach_block or "")
        if not self.active or not isinstance(attach_block, AttachBlock):
            return P.found(whole)
        files, once = attach_block.files, attach_block.once
        if files.strip() and once.strip():
            return P.found(ATTACH_HEAD + files) + P.last(once)
        if files.strip():
            return P.found(ATTACH_HEAD + files)
        if once.strip():
            return P.last(ATTACH_HEAD + once)
        return P.found(whole)

    # -- the switched-on extensions' tools -------------------------------------- #
    def keep_conn(self, d: Any, conn_tools: list, cap: int) -> list:
        """The tools of the extensions switched on for this chat: the ones
        this message's words picked come FIRST and are always seated, then
        the readers the chat's last reply was sent, in the room that is left
        under *cap* (the lanes' own bound on a switched-on extension's
        tools), the most recently sent ones. (Their place in the tool LIST is
        :meth:`settle`'s: a tool the chat already sent keeps it.) Only tools
        of an extension that is STILL switched on and
        still registered stay: one that was removed, switched off or
        unregistered brings nothing, and neither does a tool that now asks
        before each use (held back, or its extension locked: it lost its
        standing approval, so only the words of a message bring it back, and
        it still asks). A new list; *conn_tools* on any doubt.

        A large extension's tools were picked again from each message's
        words, so its list changed with every message. Now the list holds
        still, and a tool the words want that has no room is found with the
        extension's own finder (``mcp/reach``), as a tool past the cap
        always was."""
        if not self.active or self.low_trust or not conn_tools:
            return conn_tools
        try:
            from ..mcp.reach import _held, server_of

            registry = d.platform.registry
            picks = list(conn_tools)
            on = {server_of(n) for n in picks if server_of(n)}
            stay = [
                n for n in self.kept.tools
                if n in self.kept.held and server_of(n) in on and reads_only(registry, n)
            ]
            stay = [n for n in stay if not _held(registry, n) and n not in picks]
            # This message's own picks are ALWAYS seated; kept readers take
            # only the room that is left, the most recently sent first.
            room = max(0, int(cap) - len(picks))
            if not stay or not room:
                return conn_tools
            return picks + stay[-room:]
        except Exception:  # noqa: BLE001 — the words' own pick stands
            log.warning("a chat's kept extension tools could not be joined", exc_info=True)
            return conn_tools

    def kept_app_tools(self) -> "list[str]":
        """The extension tools the chat already holds (``mcp/reach.widen``
        decides whether and how each joins this reply)."""
        if not self.active or self.low_trust:
            return []
        return [n for n in self.kept.tools if n in self.kept.held and n.startswith("mcp__")]

    def _trim(self, order: "list[str]", sent: "list[str]") -> "list[str]":
        """*order* cut to the most names a chat keeps: the STALEST leave (a
        name this reply was not sent, the longest-kept first), never one this
        reply holds."""
        over = len(order) - chat_kept.MAX_TOOLS
        if over <= 0:
            return order
        held = set(sent)
        out = list(order)
        for name in list(order):
            if over <= 0:
                break
            if name not in held:
                out.remove(name)
                over -= 1
        return out[-chat_kept.MAX_TOOLS:]

    # -- after the arming pass ------------------------------------------------ #
    def keep_auto(self, d: Any, body: Any, armed: list, auto_armed: list, ceiling: "int | None",
                  standing: int, provider: str = "") -> "tuple[list, list]":
        """``(armed, auto_armed)`` with the readers the chat already holds
        (see the module docstring). New lists; unchanged on any doubt.

        *ceiling* is the answering model's MEASURED tool ceiling
        (``CapabilityProfile.max_tools()``: None when nothing measured it),
        never the arming pass's own effective one (``ArmedSelection.ceiling``
        is the standing six whenever nothing was measured, so passing it
        would cap every chat at six). It bounds only how many kept readers
        stay: what this message's own words armed is never cut here."""
        if not self.active or self.low_trust or not self.kept.held:
            return armed, auto_armed
        try:
            if not bool(getattr(body, "auto_tools", False)):
                return armed, auto_armed
            registry = d.platform.registry
            permissions = getattr(d.platform, "permissions", None)
            explicit = [t for t in armed if t not in auto_armed]
            auto = P.keep_auto(
                explicit, auto_armed, [n for n in self.kept.tools if n in self.kept.held],
                may_stay=lambda name: may_stay(registry, name, permissions),
                ceiling=keep_ceiling(ceiling, standing, is_local_model(d, provider)),
            )
            # Armed ONLY because it stayed: not ticked, not armed by this
            # message's words (``stayed_only``).
            asked = set(explicit) | set(auto_armed)
            self._asked = frozenset(asked)
            self._stayed = frozenset(n for n in auto if n not in asked)
            return explicit + auto, auto
        except Exception:  # noqa: BLE001 — the arming pass's own answer stands
            log.warning("a chat's kept tools could not be joined", exc_info=True)
            return armed, auto_armed

    def stayed_only(self, body: Any = None) -> "frozenset[str]":
        """The tools armed on this reply ONLY because they stayed (for
        ``ToolContext.stayed_only``; empty for a coding session, at low trust
        and whenever nothing stayed). With "Use Excel in this chat" switched
        on for the message (``excel_now``) the workbook readers are not among
        them: the user's own switch asks for the open workbook. And a scan
        reader is NOT "only stayed" when this message's words armed any file
        reader, or when the person's own words plainly ask for a file
        (:func:`asks_for_a_file`): "what does c4.png say?" arms no tool by
        its words, and a message that arms ``read_document`` may be answered
        with ``read_file``; the scan is read either way, as before."""
        names = self._stayed
        try:
            if names and getattr(body, "excel_now", None) is not None:
                names = names - LIVE_WORKBOOK_READERS
            if names & SCAN_READERS and (self._asked & SCAN_READERS or asks_for_a_file(_newest_words(body))):
                names = names - SCAN_READERS
        except Exception:  # noqa: BLE001 — cannot tell: the narrower answer stands
            pass
        return names

    # -- right after the tool definitions are built ---------------------------- #
    def settle(self, armed: list, auto_armed: list, tool_specs: list, ask_armed: "list | None" = None):
        """The reply's tool names and definitions in the chat's kept order: a
        tool the chat already sent keeps its place, a new one goes at the END.
        ``(armed, auto_armed, tool_specs)``, with ``ask_armed`` fourth when it
        was given. Nothing is added or dropped."""
        if self.active:
            try:
                tool_specs = P.order_specs(tool_specs, self._order)
                # The names follow the definitions' order (a new tool's place
                # among the new ones is the registry's, in both), so the
                # sentence that lists them reads the same on the next reply.
                now = [P.spec_name(s) for s in tool_specs]
                armed = P.order_names(armed, now)
                auto_armed = P.order_names(auto_armed, now)
                if ask_armed is not None:
                    ask_armed = P.order_names(ask_armed, now)
            except Exception:  # noqa: BLE001 — the lists as they came
                log.warning("a chat's tool list could not be put in its kept order", exc_info=True)
        return (armed, auto_armed, tool_specs) if ask_armed is None else (armed, auto_armed, tool_specs, ask_armed)

    # -- a per-message section that used to join the system prompt ------------- #
    def later(self, text: Any) -> str:
        """Hold *text* for the once-only block beside the newest message and
        answer "" (a coding session: *text*, unchanged, for the system prompt)."""
        text = str(text or "")
        if not self.active:
            return text
        if text.strip():
            self._later.append(text)
        return ""

    def when_sent(self, build: Any) -> str:
        """A section that NAMES the reply's tools (the Tools seam: the armed
        names and every brief that follows them). For a plain chat reply it
        is built when the request is sent (:meth:`place`), from the tool list
        as it stands then, and rides beside the newest message with the
        other directives: a tool that joins after the seam (the lookup's
        head start) is named in this reply as it is in the next, and the
        system prompt names no tool. Answers "" (a coding session: the text,
        for the system prompt, as before)."""
        if not self.active:
            return str(build() or "")
        self._when_sent.append(build)
        return ""

    def _last_sections(self) -> str:
        """The sections read last, in order: the Tools seam (built now), then
        what ``later`` holds (the skills this message fits, what was looked
        up, this message's own directive). A builder that fails costs its
        own section only."""
        if self._built_last is None:
            built: list[str] = []
            for build in self._when_sent:
                try:
                    built.append(str(build() or ""))
                except Exception:  # noqa: BLE001 — never a failed reply over a brief
                    log.warning("a chat reply's tools section could not be built", exc_info=True)
            # Built ONCE: the old way (after a failed placing) reuses it.
            self._built_last = "".join(built)
        return self._built_last + "".join(self._later)

    # -- where the query tail used to join the system prompt -------------------- #
    async def place(self, d: Any, body: Any, *, system: str, tail: Any, plan: Any, msgs: list,
                    images: Any, tool_specs: Any, provider: str, model: str,
                    ctx_messages: Any) -> "tuple[str, list, Any]":
        """``(system, msgs, plan)`` to send.

        A coding session: ``system + tail`` and the rest as given. A plain
        chat reply: the system prompt WITHOUT the tail, the history planned
        once more with the tool definitions counted, and the tail placed
        beside the newest message as its once-only block. Then the chat's
        tool names are saved. On any fault the reply is sent the old way (the
        tail in the system prompt), and the fault is logged."""
        if not self.active:
            return system + tail, msgs, plan
        try:
            return await self._place(
                d, body, system=system, tail=tail, plan=plan, images=images, tool_specs=tool_specs,
                provider=provider, model=model, ctx_messages=ctx_messages,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — never a failed reply over where a section rides
            log.warning("a chat reply's added text could not be placed beside its message; "
                        "it rides in the instructions for this reply", exc_info=True)
            from ..core.today import today_line

            # The old way still knows the date (it used to ride beside the message).
            return system + tail + self._last_sections() + "\n\n" + today_line(), msgs, plan

    async def _place(self, d: Any, body: Any, *, system: str, tail: Any, plan: Any, images: Any,
                     tool_specs: Any, provider: str, model: str,
                     ctx_messages: Any) -> "tuple[str, list, Any]":
        from ..context.budget import CUT_STEP
        from ..core.today import today_line
        from ..providers.adapters.base import LLMMessage
        # Read where they live, at call time: both lanes plan through the one
        # ``chat_turn._plan_context`` (a patch of it reaches this plan too).
        from . import chat_turn as _ct

        items = list(ctx_messages if ctx_messages is not None else (getattr(body, "messages", None) or []))
        newest = _user_index(items)
        if newest < 0:
            # Nothing to place the block beside: never drop it in silence.
            raise RuntimeError("no user message to place the block beside")
        t = P.as_tail(tail)
        once = P.once_block(t + P.last(self._last_sections()), lead=today_line())
        riders = [""] * len(items)
        riders[newest] = once
        # ONE fixed sentence: only the block at the end of the newest message
        # is the app's (before the final plan, which prices it).
        system += "\n\n" + P.BLOCK_NOTE
        # The recap of the first plan rode in the middle of the system prompt;
        # the final plan's goes at its end.
        first_recap = str(getattr(plan, "recap", "") or "")
        if first_recap and ("\n\n" + first_recap) in system:
            system = system.replace("\n\n" + first_recap, "", 1)
        fixed = _ct._tool_spec_tokens(tool_specs, _ct._history_ratio(d, provider, model))
        final = _ct._plan_context(
            d, body, system, provider, model, messages=items,
            riders=riders, fixed_tokens=int(fixed or 0), step=CUT_STEP,
        )
        if newest >= 0 and riders[newest] and not (
            final.messages and final.messages[-1].get("rider") == riders[newest]
        ):
            # The plan did not carry the newest message's rider (a planner
            # fault falls back to a plain slice): never send a reply without
            # what was remembered and attached. The caller sends the old way.
            raise RuntimeError("the final plan lost the newest message's rider")
        # When the room is too small for the person's newest message AND the
        # whole block, the block gives first: what was found, then the rest.
        shed = self._shed_block(d, final, once, system, int(fixed or 0), provider, model)
        if final.recap:
            # The recap quotes the start of each left-out message: neutralised
            # like every other text that is not the one real block.
            system += "\n\n" + P.neutral(final.recap)
        # Release 6's record of this reply says how many messages the PLANNER
        # left out: that is this plan's count now (it counted the tools).
        try:
            from ..eval import reply_facts as _facts

            record = getattr(_facts.current(), "parts", None)
            if isinstance(record, dict):
                if final.dropped:
                    record["left_out"] = int(final.dropped)
                else:
                    record.pop("left_out", None)
        except Exception:  # noqa: BLE001 — a record never breaks a reply
            log.debug("could not note what the final plan left out", exc_info=True)
        out: list = []
        last_user = -1
        for i, m in enumerate(final.messages):
            rider = str(m.get("rider") or "")
            if shed is not None and rider == once:
                rider = shed
            # Everything that is not the one real block is neutralised of the
            # block's marker lines: an earlier message (a model that echoed
            # them cannot plant them in history) and the person's own words.
            # The same input gives the same bytes on every reply.
            msg = LLMMessage(role=m["role"], content=P.neutral(m["content"]) + rider)
            if m["role"] == "user":
                last_user = i
            out.append(msg)
        if last_user >= 0:
            target = out[last_user]
            rider = str(final.messages[last_user].get("rider") or "")
            if shed is not None and rider == once:
                rider = shed
            # The person's own words are what the next reply sends again.
            if once and rider and rider in (once, shed):
                target.stable_chars = len(target.content) - len(rider)
            if images:
                target.images = images
        await asyncio.to_thread(self._save, [P.spec_name(s) for s in tool_specs or ()])
        return system, SentMessages(out), final

    def _shed_block(self, d: Any, final: Any, once: str, system: str, fixed: int, provider: str,
                    model: str) -> "str | None":
        """What rides beside the newest message when the room cannot hold the
        person's own words AND the whole block: the block without what was
        found (one line says it was left out), else no block at all. None =
        the whole block rides (the usual case, and whenever nothing can be
        told). ORDER when something must give: older history (the planner's),
        then the found part of the block, then the rest of the block; the
        person's own words last (the planner clips them only when the system
        prompt and the reply reserve alone leave no room for them)."""
        try:
            if not once or not getattr(final, "suggest_larger", False) or len(final.messages) != 1:
                return None
            newest = final.messages[-1]
            if newest.get("rider") != once:
                return None
            from ..context.budget import estimate_tokens, output_reserve
            from . import chat_turn as _ct

            window = int(getattr(final, "window", 0) or 0)
            if window <= 0:
                return None
            ratio = _ct._history_ratio(d, provider, model)
            reserve = _ct._answer_reserve(d, provider, model, window)
            reserve = output_reserve(window) if reserve is None else int(reserve)
            words = P.neutral(newest["content"])
            room = (window - reserve - estimate_tokens(system, ratio) - int(fixed)
                    - estimate_tokens(words, ratio) - 4)
            if estimate_tokens(once, ratio) <= room:
                return None
            lighter = P.without_found(words + once)[len(words):]
            if lighter != once and estimate_tokens(lighter, ratio) <= room:
                return lighter
            return ""
        except Exception:  # noqa: BLE001 — the whole block rides, as planned
            log.debug("could not size the block against the room", exc_info=True)
            return None

    # -- the save ---------------------------------------------------------------- #
    def _save(self, sent_names: "list[str]") -> None:
        """BLOCKING. The chat's tool NAMES (the order it has ever sent them
        in, and which ones this reply holds): nothing else is written. A
        first reply with no chat yet writes under its turn and is given its
        chat at once when the page already linked it."""
        try:
            sent = chat_kept.clean_names(sent_names)
            order = self._trim(P.order_names([*self._order, *sent], self._order), sent)
            if order != self.kept.tools or set(sent) != set(self.kept.held):
                chat_kept.save_tools(self.engine, self.thread, self.turn, order, held=sent)
            if not self.thread and self.turn:
                chat = _chat_of(self.turn)
                if chat:
                    chat_kept.home_turn(self.engine, self.turn, chat)
        except Exception:  # noqa: BLE001 — a kept record is never worth a failed reply
            log.warning("a chat's kept tool names could not be saved", exc_info=True)


def _chat_of(turn_id: str) -> str:
    """The saved chat a first reply's turn was linked to already ("" = not
    yet: the link homes the row itself). Never raises."""
    try:
        from ..providers.egress import linked_thread

        return str(linked_thread(turn_id) or "")
    except Exception:  # noqa: BLE001
        return ""


async def begin(d: Any, body: Any, *, harness: Any = None, low_trust: bool = False) -> StableTurn:
    """This reply's :class:`StableTurn`. A coding session gets an inactive
    one. Reads the chat's kept tool names, off the loop; a new chat starts
    empty. Never raises."""
    if harness is not None:
        return StableTurn(active=False)
    engine = getattr(getattr(d, "platform", None), "engine", None)
    thread = str(getattr(body, "thread_id", "") or "").strip()[: chat_kept.ID_MAX]
    turn = str(getattr(body, "turn_id", "") or "").strip()[: chat_kept.ID_MAX]
    kept = chat_kept.Kept()
    if thread and engine is not None:
        try:
            kept = await asyncio.to_thread(chat_kept.load, engine, thread)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            kept = chat_kept.Kept()
    return StableTurn(active=True, engine=engine, thread=thread, turn=turn, kept=kept, low_trust=low_trust)


__all__ = [
    "ATTACH_HEAD", "AttachBlock", "LIVE_WORKBOOK_READERS", "MEANING_SEARCHERS", "SCAN_READERS", "STAY_READERS",
    "SentMessages", "asks_for_a_file",
    "StableTurn", "begin", "is_local_model", "keep_ceiling", "may_stay", "reads_only",
]
