"""The two chat lanes' ONE helper for a chat's stable prefix (the intelligence
plan, release 7). The contract and the pure parts are in ``context/prefix``;
what a chat keeps between replies is in ``daemon/chat_kept``. This module is
the seam both lanes call, so they cannot drift:

``begin`` (when the reply starts) -> ``attached`` (the attachments seam) ->
``priced`` (right before the compaction and the planner) -> ``keep_auto``
(after the arming pass) -> ``settle`` (right after the tool definitions are
built) -> ``later`` (a per-message section that used to join the system
prompt) -> ``place`` (where the query tail used to be joined to the system
prompt: the final plan, the messages to send, the saves).

MIRROR NOTE (lock-step): ``chat_turn.run_chat_turn`` and
``routes/chat.py``'s stream lane call the same methods at the same seams.

A CODING SESSION (``harness=``) is not touched: every method hands back what
it was given and ``place`` joins the tail to the system prompt as before.

WHAT STAYS FOR THE CHAT, AND WHAT DOES NOT. A tool definition the chat's last
reply was sent stays in the next reply's list, in the same place, only when
the reply could have armed it by itself anyway:

* a tool of Auto's own safe set that declares it ONLY READS
  (``Reversibility.READONLY``), while Auto is on for the chat. A tool that
  changes something is armed by the words of the message that asks for the
  change, exactly as before ("change needs intent"), so it is never kept;
* an extension's tool, through ``mcp/reach.widen`` and at the tier that
  extension has in THIS reply (its approval, pins and trust decide);
* a tool the user ticked stays while it is ticked (the page sends the ticks).

Everything else the lanes arm by their own rules each reply. A kept name is a
definition to SEND: the registry's gate decides every call as before, and the
low-trust, Local only and text-only filters run after the keeping, so a tool
the reply may not use is dropped before anything is sent.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..context import prefix as P
from . import chat_kept

log = logging.getLogger(__name__)

#: Tail kinds of this module (``prefix.Tail`` carries any kind; the once-only
#: block reads only its own three).
KEEP = "keep"      # the text of what is attached to THIS message: kept with it
REPLAY = "replay"  # earlier messages' kept text: priced here, sent with them

ATTACH_HEAD = "\n\n# Attachments (provided by the user this turn)"

#: Tools of Auto's safe set that only READ but do not declare
#: ``Reversibility.READONLY`` (a tool that declares nothing reads as one that
#: changes something, on purpose). They stay for a chat like the declared
#: readers. ``recall`` reads the same memory every reply is already grounded
#: in (and ``ltm_search``, which declares itself, reads). NOT here, on
#: purpose: ``view_image`` (it spends a model call) and the web tools (the web
#: is armed by a message that asks for it): each of those is armed by the
#: words of its own message, as before.
UNDECLARED_READERS = frozenset({
    "file_search", "recall", "guide_search", "app_search", "app_status", "image_info",
})

#: The most named tools a chat reply holds once kept readers are counted: the
#: bound ``mcp/reach`` already puts on tools that continue from the reply
#: before (``MAX_CONTINUED``). A model with a measured, narrower ceiling keeps
#: that ceiling (nothing is kept past it).
def _keep_ceiling(ceiling: "int | None", standing: int) -> int:
    from ..mcp.reach import MAX_CONTINUED

    if ceiling is not None and ceiling < standing:
        return int(ceiling)
    return max(int(standing), MAX_CONTINUED)


class AttachBlock(str):
    """``_prepare_attachments``'s block (the same string as ever), which also
    knows its two halves: ``kept`` (each attached file's own text, which
    stays with the message) and ``once`` (the lines that are true for this
    reply only: which tools can open or change each file now, a picture that
    was not seen, the conversation's earlier files)."""

    kept: str = ""
    once: str = ""

    def __new__(cls, text: str = "", kept: str = "", once: str = "") -> "AttachBlock":
        self = super().__new__(cls, text)
        self.kept = kept
        self.once = once
        return self


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
        #: id(message of the request) -> the kept block that rides beside it.
        self._beside: dict[int, str] = {}
        self._newest_key = ""
        self._keep_now = ""
        self._order: list[str] = list(self.kept.tools)

    # -- the attachments seam ------------------------------------------------ #
    def attached(self, attach_block: Any) -> P.Tail:
        """The attachments section as it joins the tail."""
        whole = ATTACH_HEAD + str(attach_block or "")
        if not self.active or not isinstance(attach_block, AttachBlock):
            return P.found(whole)
        kept_text = attach_block.kept
        if not kept_text.strip() or not P.kept_block(kept_text):
            return P.found(whole)  # nothing to keep, or too much: it rides once
        out = P.Tail(kept_text, KEEP)
        if attach_block.once.strip():
            # Iron Jarvis's own lines about the files (where each is, which
            # tools can open or change it in THIS reply): outside the fence.
            out = out + P.own(ATTACH_HEAD + attach_block.once)
        return out

    # -- right before the compaction and the planner -------------------------- #
    def priced(self, tail: Any, body: Any, pieces: "dict | None" = None) -> Any:
        """*tail* with the kept text of the chat's EARLIER messages joined for
        pricing (the compaction, the planner and the reply's recorded parts
        all read the tail), and remembered for :meth:`place`. Unchanged when
        the chat holds none."""
        if not self.active:
            return tail
        try:
            messages = list(getattr(body, "messages", None) or [])
            keys = P.keys_of(messages)
            newest = _user_index(messages)
            self._newest_key = keys[newest] if newest >= 0 else ""
            t = P.as_tail(tail)
            self._keep_now = P.kept_block(t.of(KEEP))
            replayed: list[str] = []
            for i, m in enumerate(messages):
                text = self.kept.files.get(keys[i]) if keys[i] else None
                if not text:
                    continue
                if i == newest:
                    if self._keep_now:
                        continue  # sent again with its files: the new text stands
                    self._keep_now = text  # the same message again, without its files
                else:
                    self._beside[id(m)] = text
                replayed.append(text)
            if not replayed:
                return tail
            joined = "".join(replayed)
            if isinstance(pieces, dict):
                pieces.setdefault("attachments", []).append(joined)
            return t + P.Tail(joined, REPLAY)
        except Exception:  # noqa: BLE001 — the reply goes on without the kept text
            log.warning("a chat's kept text could not be read back", exc_info=True)
            self._beside = {}
            return tail

    # -- after the arming pass ------------------------------------------------ #
    def keep_auto(self, d: Any, body: Any, armed: list, auto_armed: list, ceiling: "int | None",
                  standing: int) -> "tuple[list, list]":
        """``(armed, auto_armed)`` with the readers the chat already holds
        (see the module docstring). New lists; unchanged on any doubt."""
        if not self.active or self.low_trust or not self.kept.held:
            return armed, auto_armed
        try:
            if not bool(getattr(body, "auto_tools", False)):
                return armed, auto_armed
            from ..tools.autoselect import AUTO_SAFE_TOOLS
            from ..tools.base import Reversibility

            registry = d.platform.registry

            def may_stay(name: str) -> bool:
                if name not in AUTO_SAFE_TOOLS or name.startswith("mcp__"):
                    return False
                tool = registry.get(name)
                if tool is None:
                    return False
                return (
                    getattr(tool, "reversibility", None) == Reversibility.READONLY
                    or name in UNDECLARED_READERS
                )

            explicit = [t for t in armed if t not in auto_armed]
            auto = P.keep_auto(
                explicit, auto_armed, [n for n in self.kept.tools if n in self.kept.held],
                may_stay=may_stay, ceiling=_keep_ceiling(ceiling, standing),
            )
            return explicit + auto, auto
        except Exception:  # noqa: BLE001 — the arming pass's own answer stands
            log.warning("a chat's kept tools could not be joined", exc_info=True)
            return armed, auto_armed

    def keep_conn(self, d: Any, conn_tools: list, cap: int) -> list:
        """The tools of the extensions switched on for this chat: the ones the
        chat's last reply was sent come FIRST, in their order, then the ones
        this message's words picked, never more than *cap* in all (the lanes'
        own bound on a switched-on extension's tools) or than the words alone
        picked. Only tools of an extension that is STILL switched on and
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
            on = {server_of(n) for n in conn_tools if server_of(n)}
            stay = [
                n for n in self.kept.tools
                if n in self.kept.held and server_of(n) in on and registry.get(n) is not None
            ]
            stay = [n for n in stay if not _held(registry, n)]
            if not stay:
                return conn_tools
            limit = max(int(cap), len(conn_tools))
            out = stay[:limit]
            target = max(len(conn_tools), len(out))
            for name in conn_tools:
                if len(out) >= target:
                    break
                if name not in out:
                    out.append(name)
            return out
        except Exception:  # noqa: BLE001 — the words' own pick stands
            log.warning("a chat's kept extension tools could not be joined", exc_info=True)
            return conn_tools

    def kept_app_tools(self) -> "list[str]":
        """The extension tools the chat already holds (``mcp/reach.widen``
        decides whether and how each joins this reply)."""
        if not self.active or self.low_trust:
            return []
        return [n for n in self.kept.tools if n in self.kept.held and n.startswith("mcp__")]

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

    # -- where the query tail used to join the system prompt -------------------- #
    async def place(self, d: Any, body: Any, *, system: str, tail: Any, plan: Any, msgs: list,
                    images: Any, tool_specs: Any, provider: str, model: str,
                    ctx_messages: Any) -> "tuple[str, list, Any]":
        """``(system, msgs, plan)`` to send.

        A coding session: ``system + tail`` and the rest as given. A plain
        chat reply: the system prompt WITHOUT the tail, the history planned
        once more with the tool definitions counted and each message's rider
        beside it, and the tail placed beside the newest message. Then the
        chat's tool names and the newest message's kept text are saved. On
        any fault the reply is sent the old way (the tail in the system
        prompt), and the fault is logged."""
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
            return system + tail + "".join(self._later), msgs, plan

    async def _place(self, d: Any, body: Any, *, system: str, tail: Any, plan: Any, images: Any,
                     tool_specs: Any, provider: str, model: str,
                     ctx_messages: Any) -> "tuple[str, list, Any]":
        from ..context.budget import CUT_STEP
        from ..core.today import today_line
        from ..providers.adapters.base import LLMMessage
        # Read where they live, at call time: both lanes plan through the one
        # ``chat_turn._plan_context`` (a patch of it reaches this plan too).
        from . import chat_turn as _ct

        t = P.as_tail(tail)
        once = P.once_block(t + P.last("".join(self._later)), lead=today_line())
        items = list(ctx_messages if ctx_messages is not None else (getattr(body, "messages", None) or []))
        newest = _user_index(items)
        riders = [self._beside.get(id(m), "") for m in items]
        if newest >= 0:
            riders[newest] = self._keep_now + once
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
        if final.recap:
            system += "\n\n" + final.recap
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
            msg = LLMMessage(role=m["role"], content=m["content"] + rider)
            if m["role"] == "user":
                last_user = i
            out.append(msg)
        if last_user >= 0:
            target = out[last_user]
            sent = final.messages[last_user]
            rider = str(sent.get("rider") or "")
            # Everything before the once-only block is sent again next reply.
            if rider.endswith(once) and once:
                target.stable_chars = len(target.content) - len(once)
            if images:
                target.images = images
        await asyncio.to_thread(self._save, [P.spec_name(s) for s in tool_specs or ()])
        return system, out, final

    # -- the saves -------------------------------------------------------------- #
    def _save(self, sent_names: "list[str]") -> None:
        """BLOCKING. The chat's tool names (the order it has ever sent them
        in, and which ones this reply holds) and the newest message's kept
        text. A first reply with no chat yet writes under its turn and is
        given its chat at once when the page already linked it."""
        try:
            sent = chat_kept.clean_names(sent_names)
            order = P.order_names([*self._order, *sent], self._order)[-chat_kept.MAX_TOOLS:]
            if order != self.kept.tools or set(sent) != set(self.kept.held):
                chat_kept.save_tools(self.engine, self.thread, self.turn, order, held=sent)
            if self._keep_now and self._newest_key and self.kept.files.get(self._newest_key) != self._keep_now:
                chat_kept.save_file(self.engine, self.thread, self.turn, self._newest_key, self._keep_now)
            if not self.thread and self.turn:
                chat = _chat_of(self.turn)
                if chat:
                    chat_kept.home_turn(self.engine, self.turn, chat)
        except Exception:  # noqa: BLE001 — a kept record is never worth a failed reply
            log.warning("a chat's kept record could not be saved", exc_info=True)


def _chat_of(turn_id: str) -> str:
    """The saved chat a first reply's turn was linked to already ("" = not
    yet: the link homes the rows itself). Never raises."""
    try:
        from ..providers.egress import linked_thread

        return str(linked_thread(turn_id) or "")
    except Exception:  # noqa: BLE001
        return ""


async def begin(d: Any, body: Any, *, harness: Any = None, low_trust: bool = False) -> StableTurn:
    """This reply's :class:`StableTurn`. A coding session gets an inactive
    one. Reads what the chat keeps, off the loop; a new chat starts empty.
    Never raises."""
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


__all__ = ["ATTACH_HEAD", "AttachBlock", "KEEP", "REPLAY", "StableTurn", "UNDECLARED_READERS", "begin"]
