"""The two chat lanes' ONE helper for a chat's stable prefix (the intelligence
plan, release 7). The contract and the pure parts are in ``context/prefix``;
the tool names a chat keeps between replies are in ``daemon/chat_kept``. This
module is the seam both lanes call, so they cannot drift:

``begin`` (when the reply starts) -> ``keep_conn`` (the switched-on
extensions' tools) -> ``attached`` (the attachments seam) -> ``keep_auto``
(after the arming pass) -> ``settle`` (right after the tool definitions are
built) -> ``later`` (a per-message section that used to join the system
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

* a tool of Auto's own safe set that only READS (it declares
  ``Reversibility.READONLY``, or is one of :data:`UNDECLARED_READERS`),
  while Auto is on for the chat. A tool that changes something is armed by
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
from typing import Any

from ..context import prefix as P
from . import chat_kept

log = logging.getLogger(__name__)

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


def keep_ceiling(ceiling: "int | None", standing: int, local: bool) -> int:
    """The most named tools a chat reply holds once kept readers are counted.

    * a model with a MEASURED, narrower ceiling keeps that ceiling;
    * a model served from the owner's own machine or network (*local*) with
      no measured ceiling keeps the lanes' standing cap (*standing*, six): a
      small local model picks worse from a wide menu, and nothing has
      measured this one;
    * every other model: the bound ``mcp/reach`` already puts on tools that
      continue from the reply before (``MAX_CONTINUED``, twelve).
    """
    from ..mcp.reach import MAX_CONTINUED

    if ceiling is not None and ceiling < standing:
        return int(ceiling)
    if local:
        return int(standing)
    return max(int(standing), MAX_CONTINUED)


def is_local_model(d: Any, provider: str) -> bool:
    """Is the reply's model served from the owner's own machine or network?
    ``providers.local.is_local_provider`` (``ollama``, ``custom``, the
    ``fleet-*`` servers) asked of the reply's provider: the pick, else the
    configured default. By name only (no adapter is built: this runs on the
    loop). True when the provider cannot be read (the narrower cap)."""
    from ..providers.local import is_local_provider

    try:
        name = (provider or "").strip() or str(getattr(d.platform.config, "default_provider", "") or "")
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

    # -- after the arming pass ------------------------------------------------ #
    def keep_auto(self, d: Any, body: Any, armed: list, auto_armed: list, ceiling: "int | None",
                  standing: int, provider: str = "") -> "tuple[list, list]":
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
                may_stay=may_stay,
                ceiling=keep_ceiling(ceiling, standing, is_local_model(d, provider)),
            )
            return explicit + auto, auto
        except Exception:  # noqa: BLE001 — the arming pass's own answer stands
            log.warning("a chat's kept tools could not be joined", exc_info=True)
            return armed, auto_armed

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
        riders = [""] * len(items)
        if newest >= 0:
            riders[newest] = once
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
            rider = str(final.messages[last_user].get("rider") or "")
            # The person's own words are what the next reply sends again.
            if once and rider == once:
                target.stable_chars = len(target.content) - len(once)
            if images:
                target.images = images
        await asyncio.to_thread(self._save, [P.spec_name(s) for s in tool_specs or ()])
        return system, out, final

    # -- the save ---------------------------------------------------------------- #
    def _save(self, sent_names: "list[str]") -> None:
        """BLOCKING. The chat's tool NAMES (the order it has ever sent them
        in, and which ones this reply holds): nothing else is written. A
        first reply with no chat yet writes under its turn and is given its
        chat at once when the page already linked it."""
        try:
            sent = chat_kept.clean_names(sent_names)
            order = P.order_names([*self._order, *sent], self._order)[-chat_kept.MAX_TOOLS:]
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
    "ATTACH_HEAD", "AttachBlock", "StableTurn", "UNDECLARED_READERS", "begin", "is_local_model", "keep_ceiling",
]
