"""The Reflex Router — turn an inbound signal into a running action.

Given a matched :class:`ReflexRule`, it starts the bound work: a saved workflow,
a remote agent, or a supervised session. The *creating* step is awaited (so
a caller/test immediately has a run-record or session id; its write runs off the
event loop since v1.505.0), while the long-running
part is launched in the background via the injected ``spawn_bg`` (the daemon's
task launcher) — a webhook POST never blocks on a multi-minute run.

Safety: a rule exists only because the user made it, and every launched action
still flows through the normal orchestrator + permission engine, so a remote
signal gets no more power than a local one. Nothing here bypasses a gate.
"""

from __future__ import annotations

import functools
import json as _json
import re as _re
from typing import Any, Callable
from urllib.parse import quote

from ..core.events import EventType
from ..core.logging import get_logger
from ..core.models import AgentType
from .models import ReflexRule
from .store import ReflexStore

log = get_logger("reflex")

#: A launcher: ``(session_id, coro) -> Task``. The daemon passes ``_spawn_bg``.
SpawnBg = Callable[[str, Any], Any]


async def _off_loop(fn: Any, *args: Any) -> Any:
    """A blocking SQLite write on a worker thread (v1.505.0, north-star 5.4):
    the orchestrator's ``off_loop_settled``, so a cancel waits for the write in
    flight. Lazy import, by import direction (the agent layer is heavy)."""
    from ..agents.orchestrator import off_loop_settled

    return await off_loop_settled(fn, *args)


#: Placeholders a rule's task_template can reference; filled from the triggering
#: signal's context. The generic set (body/text/slug) plus source-specific keys
#: (email {sender}/{subject}/{from}, calendar {title}/{start}, slack {channel}).
_TEMPLATE_KEYS = (
    "body", "text", "slug", "sender", "from", "subject", "title", "start", "channel",
    # Leap 05-a: a file or a download that started the rule.
    "path", "file", "folder", "site",
    # Leap 05-b: another task that ended short — which one (in words, "the
    # schedule “Nightly report”"), how it ended ("Failed"), why in one line
    # (from the ledger), and what the run itself said (fenced: data).
    "what", "outcome", "reason", "detail",
)


#: Placeholders that carry the signal's own words (a message, a subject, an
#: event title). What Jarvis reads from outside is data, never instructions
#: (v1.499.3): each is put in as a fenced untrusted block, so the rule's own
#: template stays the only instruction the session gets.
#: Leap 05-a: a file's NAME is chosen by whoever made the file (a website names
#: the files it sends), so ``{file}``, ``{path}`` (which ends in the name) and
#: ``{site}`` (a download's address) are fenced too; ``{folder}`` is the
#: folder the user chose, put in on one line (:func:`_short`).
#: Leap 05-b: ``{detail}`` is what the run that ended SAID (its summary, its
#: failed step's words): a model's words, or outside words it read, so it is
#: fenced; ``{what}``, ``{outcome}`` and ``{reason}`` are composed by code from
#: the record and go in on one line.
_FENCED_KEYS = frozenset({"body", "text", "subject", "title", "file", "path", "site", "detail"})

_PLACEHOLDER = _re.compile(r"\{(" + "|".join(_TEMPLATE_KEYS) + r")\}")
#: A fence marker written INSIDE outside text would close the fence early.
_FENCE_MARKER = _re.compile(r"\[(\s*(?:END\s+)?UNTRUSTED\s+CONTENT)", _re.IGNORECASE)


#: v1.502.0 (north-star 2.8): the sources whose signal is written OUTSIDE this
#: PC (a webhook's body, an email, a Slack message, a calendar invite, a phone
#: message). A rule they fire starts its work at LOW trust — the phone door's
#: rule (``comm/inbound._comm_trust``, ``config.comm_trust``) applied to every
#: signal — so a planted sentence cannot reach what an inbound message is kept
#: from. ``comm_trust = "full"`` (the owner's choice) keeps it full.
OUTSIDE_SOURCES: dict[str, str] = {
    "webhook": "a webhook",
    "email": "an email",
    "slack": "a Slack message",
    "calendar": "a calendar event",
    "comm": "a phone message",
    # Leap 05-a (D-05.2): work a downloaded file starts begins in the careful
    # mode used for outside messages, because the file came from a website.
    "download": "a downloaded file",
}

#: Leap 05-a (review): a FILE rule that watches a Downloads folder starts
#: carefully too (D-05.2's reason: those files came from websites).
DOWNLOADS_FOLDER_LABEL = "a file in a Downloads folder"


def watches_downloads(rule: Any) -> bool:
    """Is ``rule`` a file rule on a folder named Downloads (the browser's
    usual place, on any drive)? Never raises."""
    try:
        if str(getattr(rule, "source", "") or "") != "file" or not hasattr(rule, "decoded_start"):
            return False
        path = str(rule.decoded_start().get("path") or "").strip().strip("\"'").rstrip("\\/")
        return bool(path) and _re.split(r"[\\/]", path)[-1].casefold() == "downloads"
    except Exception:  # noqa: BLE001 — unreadable: as before
        return False


# -- the contexts each kind of signal carries (leap 05-a: built here once, so
# -- the router's own entry points and the start hub read the same shape) ----


def comm_context(text: str) -> dict[str, str]:
    """A phone message's context (``on_comm``)."""
    text = text or ""
    return {"text": text[:2000], "body": text[:2000], "slug": ""}


def email_context(*, sender: str, subject: str, body: str) -> dict[str, str]:
    """An inbound email's context (``on_email``): matching reads subject+body."""
    subject = subject or ""
    body = body or ""
    text = f"{subject}\n{body}".strip()
    return {
        "text": text[:2000],
        "body": body[:2000],
        "subject": subject[:500],
        "sender": (sender or "")[:200],
        "from": (sender or "")[:200],
        "slug": "",
    }


def calendar_context(*, title: str, start: str = "", description: str = "") -> dict[str, str]:
    """A calendar event's context (``on_calendar``)."""
    title = title or ""
    description = description or ""
    text = f"{title}\n{description}".strip()
    return {
        "text": text[:2000],
        "body": description[:2000],
        "title": title[:500],
        "start": (start or "")[:100],
        "slug": "",
    }


def slack_context(*, text: str, channel: str = "", sender: str = "") -> dict[str, str]:
    """A Slack message's context (``on_slack``)."""
    text = text or ""
    return {
        "text": text[:2000],
        "body": text[:2000],
        "channel": (channel or "")[:100],
        "sender": (sender or "")[:100],
        "slug": "",
    }


def file_context(paths: list[str], *, rule_id: str) -> dict[str, str]:
    """Leap 05-a: the context a rule-mode watcher hands the hub for the new
    file(s) that settled in its folder. ``text`` (what a keyword is matched
    against) is the file NAME; one start may carry several files (a rule with
    ``per_file`` off), named one per line."""
    import os

    paths = [str(p) for p in paths if str(p or "").strip()]
    first = paths[0] if paths else ""
    names = [os.path.basename(p) or p for p in paths]
    return {
        "rule_id": str(rule_id or ""),
        "path": "\n".join(paths)[:4000],
        "file": "\n".join(names)[:2000],
        "folder": os.path.dirname(first)[:500],
        "text": "\n".join(names)[:2000],
        "body": "",
        "slug": "",
    }


def download_context(*, local_path: str, site: str) -> dict[str, str]:
    """Leap 05-a: a finished download's context: the VERIFIED local path
    (``browser/extension_backend.download_bus_payload``), its name and folder,
    and the site it came from (scheme, host and path, never a query:
    ``browser/downloads.site_of``)."""
    import os

    path = str(local_path or "")
    name = os.path.basename(path) or path
    return {
        "path": path[:1000],
        "file": name[:500],
        "folder": os.path.dirname(path)[:500],
        "site": str(site or "")[:300],
        "text": name[:500],
        "body": "",
        "slug": "",
    }


#: Leap 05-b: the kinds of task a run that ended can be, in words for a
#: card, a run's task and a rule's ``{what}``.
ENDED_KIND_WORDS: dict[str, str] = {
    "schedule": "the schedule",
    "workflow": "the workflow",
    "rule": "the run of the rule",
    "agent": "the agent run",
}
#: The verdicts a run that ended short carries (``ended_verdict``), and the
#: badge word each one is (``agents/outcome``'s words, every surface's).
ENDED_VERDICT_WORDS: dict[str, str] = {
    "failed": "Failed",
    "part_failed": "Part failed",
    "needs_you": "Needs you",
    "did_nothing": "Did nothing",
}


def ended_what_words(kind: str, name: str) -> str:
    """"the schedule “Nightly report”" — which task ended, in words (the
    name is one line, defanged). Never raises."""
    lead = ENDED_KIND_WORDS.get(str(kind or ""), "a task")
    flat = _short(str(name or ""))[:80]
    if not flat:
        return {"agent": "an agent run", "rule": "a rule's run"}.get(str(kind or ""), lead)
    if kind == "agent":
        return f"the run of your agent “{flat}”"
    return f"{lead} “{flat}”"


def run_ended_context(
    *,
    kind: str,
    name: str,
    verdict: str,
    reason: str,
    detail: str = "",
    href: str = "",
    run_id: str = "",
    by_rule_id: str = "",
    by_rule_name: str = "",
    by_rule_source: str = "",
    careful: bool = False,
    local_only: bool = False,
    does: str = "",
    ended_workflow: str = "",
    ended_agent: str = "",
) -> dict[str, str]:
    """Leap 05-b: the context of ONE run that ended short (a schedule's
    recorded outcome, a workflow's run, a rule's run, an agent run), for the
    ``run_ended`` rules that hear it. ``verdict`` is the ledger's
    (``failed`` | ``part_failed`` | ``needs_you`` | ``did_nothing``), never the
    model's words; ``reason`` is one line composed by code from the record;
    ``detail`` is what the run itself said (fenced in a task). ``by_rule_*``:
    the rule that started that run, if one did (a rule never starts itself
    again, and a run a ``run_ended`` rule started starts no other: the chain
    is one deep). ``careful``: that run started carefully, so the rule's work
    does too. ``local_only`` (review): that run was Local only, so the rule's
    work runs Local only, and what the run said never goes where a Local
    only run could not (:meth:`ReflexRouter.execute`). ``does``: what the
    task does, from its own record (a schedule's task words, the workflow it
    runs). ``ended_workflow`` / ``ended_agent`` (review 2): what a SCHEDULE's
    fire ran, from the fire's own records — the workflow its run was of, the
    custom agent its run was — so a rule for that workflow or agent hears
    it too (the fire is still ONE signal, ``ReflexStore.matching_run_ended``)."""
    what = ended_what_words(kind, name)
    word = ENDED_VERDICT_WORDS.get(str(verdict or ""), "Failed")
    return {
        "ended_kind": str(kind or "")[:20],
        "ended_name": " ".join(str(name or "").split())[:200],
        "ended_verdict": str(verdict or "")[:20],
        "what": what[:300],
        "outcome": word,
        "reason": " ".join(str(reason or "").split())[:300],
        "detail": str(detail or "")[:1500],
        "href": str(href or "")[:300],
        "run_id": str(run_id or "")[:100],
        "by_rule_id": str(by_rule_id or "")[:100],
        "by_rule_name": " ".join(str(by_rule_name or "").split())[:200],
        "by_rule_source": str(by_rule_source or "")[:20],
        "careful": "1" if careful else "",
        "local_only": "1" if local_only else "",
        "does": " ".join(str(does or "").split())[:200],
        "ended_workflow": " ".join(str(ended_workflow or "").split())[:200],
        "ended_agent": " ".join(str(ended_agent or "").split())[:200],
        # What a keyword (and a rehearsal's sample) reads: the task's name.
        "text": " ".join(str(name or "").split())[:500],
        "body": "",
        "slug": "",
    }


def _defanged(value: str) -> str:
    """Outside text that cannot close (or open) an untrusted fence."""
    return _FENCE_MARKER.sub(r"(\1", value or "")


def _fenced(value: str) -> str:
    from ..computeruse.safety import wrap_untrusted

    return wrap_untrusted(_defanged(value))


def _short(value: str) -> str:
    """A short field (sender, channel, time) kept on one line, defanged."""
    return " ".join(_defanged(value).split())[:200]


def _rehearsal_option() -> str:
    """The run option a rehearsal rides on (leap 06-a)."""
    from ..tools.rehearsal import REHEARSAL_OPTION

    return REHEARSAL_OPTION


def _render(template: str, context: dict[str, str]) -> str:
    """Fill the supported placeholders in ONE pass, never raises.

    One pass: a substituted value is never scanned again, so outside text
    holding ``{body}`` cannot pull another field in. The signal's own words go
    in fenced (:data:`_FENCED_KEYS`); the short fields go in on one line.
    """

    def _sub(m: "_re.Match[str]") -> str:
        key = m.group(1)
        value = str(context.get(key, "") or "")
        if key in _FENCED_KEYS:
            return "\n" + _fenced(value) + "\n" if value else ""
        return _short(value)

    return _PLACEHOLDER.sub(_sub, template or "").strip()


class ReflexRouter:
    def __init__(
        self, platform: Any, orchestrator: Any, spawn_bg: SpawnBg | None = None
    ) -> None:
        self.p = platform
        self.orch = orchestrator
        self.spawn_bg = spawn_bg
        self.store = ReflexStore(platform.engine)
        #: Leap 06-a: the LAST signal each rule matched since the daemon
        #: started, kept in memory only — what a rehearsal of that rule runs
        #: on (``sample_for``). Never written anywhere.
        self._last_signal: dict[str, dict[str, str]] = {}
        #: Leap 05-a: the clock the hourly limit and its once-a-day notice
        #: read (a test injects one).
        from ..core.ids import utcnow

        self.clock = utcnow
        #: Leap 05-a: the ONE entry every kind of start goes through
        #: (``triggers/starts.StartHub``); callers reach it by
        #: ``starts.hub_for(router)``.
        from ..triggers.starts import StartHub

        self.start_hub = StartHub(self)

    def sample_for(self, rule: ReflexRule) -> dict[str, str]:
        """The signal a REHEARSAL of ``rule`` runs on (leap 06-a): the last
        real one it matched since the daemon started, else the words
        "(sample)"."""
        last = self._last_signal.get(rule.id)
        if last:
            return dict(last)
        return {"text": "(sample)", "body": "(sample)", "slug": rule.match}

    def _remember(self, rules: list[ReflexRule], context: dict[str, str]) -> None:
        for r in rules:
            self._last_signal[r.id] = dict(context)
        while len(self._last_signal) > 200:
            self._last_signal.pop(next(iter(self._last_signal)))

    # -- signal entry points ----------------------------------------------
    async def on_webhook(self, slug: str, body: Any) -> list[dict[str, Any]]:
        """Fire every enabled rule bound to this webhook slug."""
        rules = self.store.matching_webhook(slug)
        if not rules:
            return []
        context = {
            "slug": slug,
            "body": _json.dumps(body)[:2000] if not isinstance(body, str) else body[:2000],
            "text": _text_of(body),
        }
        self._remember(rules, context)
        return [await self.execute(r, context) for r in rules]

    async def on_signal(
        self, source: str, context: dict[str, str]
    ) -> list[dict[str, Any]]:
        """Fire every enabled rule of ``source`` whose keyword matches the signal's
        ``text``. The generic entry point for every text-carrying source
        (comm/email/calendar/slack) — the source-specific wrappers below just build
        a ``context`` and delegate here. Webhooks keep their own exact-slug path."""
        rules = self.store.matching(source, context.get("text", ""))
        self._remember(rules, context)
        return [await self.execute(r, context) for r in rules]

    async def on_comm(self, text: str) -> list[dict[str, Any]]:
        """Fire every enabled comm rule whose keyword matches this message."""
        return await self.on_signal("comm", comm_context(text))

    async def on_email(
        self, *, sender: str, subject: str, body: str
    ) -> list[dict[str, Any]]:
        """Fire every enabled ``email`` rule matching an inbound message. Matching
        runs over subject+body; the template can reference {sender}/{subject}."""
        return await self.on_signal(
            "email", email_context(sender=sender, subject=subject, body=body)
        )

    async def on_calendar(
        self, *, title: str, start: str = "", description: str = ""
    ) -> list[dict[str, Any]]:
        """Fire every enabled ``calendar`` rule matching an event. Matching runs
        over title+description; the template can reference {title}/{start}."""
        return await self.on_signal(
            "calendar", calendar_context(title=title, start=start, description=description)
        )

    async def on_slack(
        self, *, text: str, channel: str = "", sender: str = ""
    ) -> list[dict[str, Any]]:
        """Fire every enabled ``slack`` rule matching a message (DM or channel)."""
        return await self.on_signal(
            "slack", slack_context(text=text, channel=channel, sender=sender)
        )

    async def on_file(self, context: dict[str, str]) -> list[dict[str, Any]]:
        """Leap 05-a: a rule-mode watcher saw new file(s) settle in the folder
        it watches for the rule ``context["rule_id"]`` — fire THAT rule (the
        watcher is the rule's own; no other rule hears it). A rule that is off
        or gone starts nothing (``[]``); a paused one answers ``held`` (its
        files wait, ``sentinels.service``). Never raises."""
        rid = str((context or {}).get("rule_id") or "")
        try:
            rule = await _off_loop(self.store.get, rid) if rid else None
        except Exception:  # noqa: BLE001 — unreadable: nothing starts
            log.warning("file rule %s could not be read", rid, exc_info=True)
            return []
        if rule is None or not rule.enabled or rule.source != "file":
            return []
        self._remember([rule], context)
        return [await self.execute(rule, context)]

    async def on_download(self, context: dict[str, str]) -> list[dict[str, Any]]:
        """Leap 05-a: a download finished in Your browser — fire every enabled
        ``download`` rule whose keyword is in the file's name and whose site
        (when it names one) is the download's. Never raises."""
        ctx = context or {}
        try:
            rules = await _off_loop(
                self.store.matching_download, str(ctx.get("file") or ""), str(ctx.get("site") or "")
            )
        except Exception:  # noqa: BLE001 — unreadable: nothing starts
            log.warning("download rules could not be read", exc_info=True)
            return []
        self._remember(rules, ctx)
        return [await self.execute(r, ctx) for r in rules]

    async def on_run_ended(self, context: dict[str, str]) -> list[dict[str, Any]]:
        """Leap 05-b: another task ended short (``run_ended_context``) — fire
        every enabled ``run_ended`` rule that listens for it. Two loop guards,
        read here so every caller has them: a run that a ``run_ended`` rule
        started starts no rule of this kind (the chain is ONE deep), and a
        run a rule started never starts that same rule. The hourly limit
        holds as for any start. Never raises."""
        ctx = context or {}
        if str(ctx.get("by_rule_source") or "") == "run_ended":
            return []
        mine = str(ctx.get("by_rule_id") or "")
        try:
            rules = await _off_loop(self.store.matching_run_ended, dict(ctx))
        except Exception:  # noqa: BLE001 — unreadable: nothing starts
            log.warning("the rules for a task that ended could not be read", exc_info=True)
            return []
        rules = [r for r in rules if r.id != mine]
        self._remember(rules, ctx)
        return [await self.execute(r, ctx) for r in rules]

    async def start(self, action: str, *, target: str = "", task: str = "") -> dict[str, Any]:
        """Fire an action MANUALLY (e.g. a ``/run`` command) without a stored
        rule — same execution path, no persistence."""
        rule = ReflexRule(
            name=f"manual:{action}",
            source="comm",
            match="",
            action=action,
            target=target,
            task_template=task,
        )
        return await self.execute(rule, {"text": task, "body": task, "slug": ""})

    # -- execution ---------------------------------------------------------
    async def rehearse(
        self, rule: ReflexRule, context: dict[str, str], *, rehearsal: str
    ) -> dict[str, Any]:
        """Start ``rule``'s work as a REHEARSAL (leap 06-a): through the same
        door as a real fire, with the run carrying ``rehearsal`` so every
        call that would change something is held. The rule's row, its fire
        count, ``reflex.fired`` and every failure notice are left alone: a
        rehearsal is not a fire. The work starts at the posture a real
        signal's would (an outside source starts low), and the person who
        pressed Rehearse started it (Emergency stop admits it).

        * ``session`` — the run is created and launched; returns its id.
        * ``remote_agent`` — nothing is sent; one rehearsed row says what
          would have gone to whom (``tools/rehearsal.SIDE_DOORS``).
        * ``workflow`` — not offered yet: refused in words, nothing ran.

        Never raises: a failed start comes back as ``{"ok": False, "error"}``."""
        from ..tools import rehearsal as _rehearsal

        try:
            if rule.action == "session":
                # The real fire's OWN door (``_run_session``), so a change to
                # it (its agent, its folder, its options) reaches a rehearsal.
                # Leap 05-b (review): with the signal, so a rehearsal of a
                # "when another task fails" rule on a careful run's end starts
                # carefully too, as the real fire would.
                trust = self._signal_trust(rule, attended=False, context=context)
                return await self._run_session(rule, context, trust=trust, rehearsal=rehearsal)
            if rule.action == "remote_agent":
                # Review 2: as the real fire would, a Local only task's end
                # is not handed on (nothing is recorded as "would have gone").
                refused = local_only_hand_off_words(rule, context)
                if refused:
                    return {"ok": False, "error": refused}
                agent = " ".join(str(rule.target or "").split()) or "a remote agent"
                # The row is the rehearsal's own (no run exists to hold it).
                await _off_loop(functools.partial(
                    _rehearsal.record_side_door,
                    self.p.engine,
                    session_id=rehearsal,
                    door="remote_agent",
                    words=f"hand the task to the remote agent {agent}",
                    target=agent,
                ))
                return {"ok": True, "kind": "remote_agent", "agent": agent}
            if rule.action == "workflow":
                name = " ".join(str(rule.target or "").split())
                return {
                    "ok": False,
                    "error": (
                        f"This rule starts the workflow “{name}”. A workflow cannot be "
                        "rehearsed yet, so nothing ran."
                    ),
                }
            return {"ok": False, "error": f"unknown action '{rule.action}'"}
        except Exception as exc:  # noqa: BLE001 — a rehearsal that cannot start says why
            log.warning("rehearsal of rule %r could not start", rule.name, exc_info=True)
            return {"ok": False, "error": _exception_words(exc)}

    async def execute(
        self, rule: ReflexRule, context: dict[str, str], *, attended: bool = False
    ) -> dict[str, Any]:
        """Launch the rule's bound action; return a small result dict. Never
        raises — a bad rule reports an error and the others still fire.

        ``attended`` (v1.446.0): a person is watching this fire (the Reflex
        page's Test press) and reads its answer, so a failure is not ANNOUNCED
        (``reflex.failed``) — the answer says it. The phone's ``/run`` (a
        ``manual:`` rule) is attended the same way."""
        # v1.446.0 (review item 20): what the row said BEFORE this fire, so a
        # rule failing again for the same reason is one notice, not one per
        # signal (an email rule under Emergency stop would buzz the phone for
        # every message).
        previous_error = str(getattr(rule, "last_error", "") or "")
        reason = ""
        # EMERGENCY STOP (v1.351.0 re-review, G13): asked FIRST, for every
        # action. Only the ``session`` action ends in ``create_session``; a
        # ``workflow`` action's non-agent steps and a ``remote_agent`` hand-off
        # create no session, so the switch's one refusal never saw them and a
        # webhook / email / phone keyword still started them while the setting
        # said "rules … do not start". The row (and a webhook's ack) carries
        # the switch's own sentence — never ``EmergencyStopOn: …``.
        stop = getattr(self.orch, "emergency_stop_reason", None)
        why = stop(f"reflex:{rule.name}") if callable(stop) else ""
        unattended = not attended and not str(rule.name or "").startswith("manual:")
        slot = ""
        if why:
            result: dict[str, Any] = {"ok": False, "error": why}
            reason = why
        else:
            # Leap 05-a (D-05.3): a rule starts at most ``max_fires_per_hour``
            # times an hour on its own; the next signal PAUSES it, and the
            # pause is said once a day. A paused rule starts nothing until
            # Resume. Its row, fire count and failure notices are left alone:
            # a held signal is not a failed start. A person's press (Run now)
            # is not held, and is not counted. Review: the gate COUNTS the
            # start as it lets it through (one locked step, so a burst of
            # signals cannot all pass it); a start that fails gives it back.
            if unattended:
                held, slot = await self._hold_if_paused(rule)
                if held is not None:
                    return held
            try:
                # v1.502.0 (north-star 2.8): the posture the work starts at.
                # Passed only when low, so a full fire calls exactly as before.
                trust = self._signal_trust(rule, attended=attended, context=context)
                trust_kw = {"trust": trust} if trust else {}
                # Leap 05-b (review): what a Local only task said never goes
                # where its run could not. Review 3: a BACKSTOP today — a
                # remote agent is refused below, and a workflow rule's Trigger
                # reads only ``text`` — for a later action that reads
                # ``detail`` (a session rule's run is Local only itself,
                # ``_run_session``).
                context = _kept_here(rule, context)
                # Review 2: a remote agent's work cannot be Local only, so a
                # Local only task's end is not handed to one at all (its
                # name, what it does and how it ended included); the row
                # says why.
                refused = local_only_hand_off_words(rule, context)
                if refused:
                    result = {"ok": False, "error": refused}
                    reason = _lower_first(refused.rstrip("."))
                elif rule.action == "workflow":
                    result = await self._run_workflow(rule, context, **trust_kw)
                elif rule.action == "remote_agent":
                    result = self._run_remote(rule, context)
                elif rule.action == "session":
                    result = await self._run_session(rule, context, **trust_kw)
                else:
                    result = {"ok": False, "error": f"unknown action '{rule.action}'"}
                if result.get("ok") and not attended:
                    # Leap 05-b (review): "try once more" — the task that
                    # ended runs again, once, named by this rule.
                    again = await self._once_more(rule, context)
                    if again:
                        result = {**result, "again": again}
            except Exception as exc:  # noqa: BLE001 — one bad rule never breaks the rest
                from ..agents.resolve import UnattendedRefusal
                from ..workflows.engine import UnknownStepKind
                from ..workflows.validate import BlockRefused

                if isinstance(exc, UnattendedRefusal):
                    # Leap 05-a: its agent is gone or cannot take a rule —
                    # the refusal's own sentence, on the row and the bell.
                    result = {"ok": False, "error": str(exc)}
                    reason = str(exc)
                elif isinstance(exc, (UnknownStepKind, BlockRefused)):
                    # Idea 09 (09-a review): a stored workflow with a step of
                    # a kind this version does not know, or a block that
                    # would run wrongly, says its own sentence, never the
                    # class name ("UnknownStepKind: …").
                    result = {"ok": False, "error": str(exc)}
                    reason = _lower_first(str(exc).rstrip("."))
                else:
                    log.exception("reflex rule %r failed", rule.name)
                    result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    reason = _exception_words(exc)

        # v1.231.0 (audit AE6): the row carries the outcome either way — a
        # failed start is written as ``last_error`` where the user looks,
        # and is NOT counted as a fire.
        if result.get("ok"):
            # Readiness 0j.5 item 15: what started, in plain words, and where
            # to see it — the Test press's answer and the row's last fire
            # both say it (never "Fired session · session <id>").
            words, href = result_words(result)
            result = {**result, "words": words, "href": href}
        elif slot and getattr(self, "store", None) is not None:
            # Leap 05-a (review): the start the gate counted did not happen,
            # so it is not counted toward the hourly limit.
            try:
                await _off_loop(functools.partial(self.store.release_start, rule.id, slot))
            except Exception:  # noqa: BLE001 — a count left too high only pauses sooner
                log.warning("rule %r: could not uncount a failed start", rule.name, exc_info=True)
        # v1.505.0 (north-star 5.4): off the loop — a webhook's fire must not
        # park the daemon behind a long writer — and still BEFORE the fire is
        # announced (a page that re-reads the row on ``reflex.fired`` finds it).
        await _off_loop(functools.partial(
            self.store.mark_result,
            rule.id,
            ok=bool(result.get("ok")),
            detail=str(result.get("error") or result.get("words") or _result_detail(result)),
            href=str(result.get("href") or "") if result.get("ok") else "",
        ))
        await self._publish(rule, result)
        if not result.get("ok"):
            await self._announce_failure(
                rule,
                reason or _reason_words(rule, str(result.get("error") or "")),
                error=str(result.get("error") or "failed"),
                previous_error=previous_error,
                attended=attended,
            )
        return {"rule": rule.name, "rule_id": rule.id, **result}

    def _now(self) -> Any:
        clock = getattr(self, "clock", None)
        if callable(clock):
            return clock()
        from ..core.ids import utcnow

        return utcnow()

    async def _hold_if_paused(self, rule: ReflexRule) -> "tuple[dict[str, Any] | None, str]":
        """Leap 05-a (D-05.3): ``(None, slot)`` when ``rule`` may start now —
        the start is COUNTED as it passes (``slot``, given back by
        ``release_start`` when the start fails); else ``(held answer, "")``
        (the rule is paused; when THIS signal paused it, the pause is
        announced, at most once a day). Never raises: a limit that cannot be
        read lets the start go on (its own writes would fail loudly)."""
        store = getattr(self, "store", None)
        if store is None:
            return None, ""
        try:
            gate = await _off_loop(functools.partial(store.start_gate, rule.id, now=self._now(), reserve=True))
        except Exception:  # noqa: BLE001
            log.warning("rule %r: its hourly limit could not be read", rule.name, exc_info=True)
            return None, ""
        if gate.get("ok"):
            return None, str(gate.get("slot") or "")
        words = str(gate.get("words") or "Paused.")
        if gate.get("tripped"):
            await self._announce_pause(rule, words)
        return {
            "rule": rule.name, "rule_id": rule.id, "ok": False,
            "held": True, "paused": True, "error": words,
        }, ""

    async def _announce_pause(self, rule: ReflexRule, words: str) -> None:
        """Publish ``reflex.paused`` (the bell's row: Resume, or Keep it
        paused) — at most ONCE A DAY per rule (``ReflexStore.note_told``).
        Never raises."""
        try:
            if not await _off_loop(functools.partial(self.store.note_told, rule.id, now=self._now())):
                return
        except Exception:  # noqa: BLE001
            return
        bus = getattr(self.p, "event_bus", None)
        if bus is None:
            return
        try:
            await bus.publish(
                EventType.REFLEX_PAUSED,
                {
                    "rule": rule.name,
                    "rule_id": rule.id,
                    "source": rule.source,
                    "reason": words[:300],
                    "limit": int(getattr(rule, "max_fires_per_hour", 0) or 0),
                },
            )
        except Exception:  # noqa: BLE001 — the bus must never block a reflex
            pass

    def _signal_trust(
        self, rule: ReflexRule, *, attended: bool, context: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """``{"trust": "low", "trust_reason": …}`` for work a rule fired by an
        OUTSIDE signal starts (:data:`OUTSIDE_SOURCES`), else ``{}``. A fire a
        person is watching (the Reflex page's Test press) and an owner who set
        ``comm_trust`` to "full" keep full trust. Leap 05-b: a ``run_ended``
        rule's work starts carefully when the run that ended did (its words
        reach the rule's task as data). Never raises."""
        label = OUTSIDE_SOURCES.get(str(getattr(rule, "source", "") or ""))
        if not label and watches_downloads(rule):
            # Leap 05-a (review, D-05.2): what lands in a Downloads folder
            # came from websites, like a download rule's file.
            label = DOWNLOADS_FOLDER_LABEL
        if (
            not label
            and str(getattr(rule, "source", "") or "") == "run_ended"
            and str((context or {}).get("careful") or "")
        ):
            label = CAREFUL_RUN_LABEL
        if attended or not label:
            return {}
        cfg = getattr(self.p, "config", None)
        if str(getattr(cfg, "comm_trust", "low") or "low").strip().lower() == "full":
            return {}
        return {"trust": "low", "trust_reason": f"started by {label} (rule {rule.name})"}

    async def _run_workflow(
        self, rule: ReflexRule, context: dict[str, str], *, trust: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        from ..workflows.engine import WorkflowEngine
        from ..workflows.store import WorkflowStore

        # v1.170.0: resolve through ``load_def`` — the ONE stored-record -> def
        # seam — so a webhook/comm/email-triggered run keeps the def's PROJECT
        # PIN. Building the def by hand here silently dropped the pin: the same
        # workflow ran grounded from the dashboard but ungrounded from a signal.
        wf = WorkflowStore(self.p.engine).load_def(rule.target)
        if wf is None:
            return {"ok": False, "error": f"no saved workflow '{rule.target}'"}
        # v1.200.0 — context spine, with a precedence rule: the DEF'S OWN PIN
        # WINS. A def pinned to project A and triggered by a rule tagged
        # project B keeps A — the pin is part of what the workflow IS, and a
        # trigger must not silently re-ground someone else's process. The
        # rule's project only grounds a def that carries NO pin of its own
        # (which used to run project-agnostic even when the rule knew better).
        if wf.project_id is None and rule.project_id:
            wf.project_id = rule.project_id
        engine = WorkflowEngine(self.p, self.orch)
        # Persists the run record now — off the loop (v1.505.0, north-star
        # 5.4), and awaited, so the record exists before its run is launched.
        # Leap 05-b: the record names the rule that started it (its end never
        # starts that rule again; a run_ended rule's run starts no other).
        from ..triggers.starts import rule_started_by

        run = await _off_loop(functools.partial(engine.create_record, wf, started_by=rule_started_by(rule)))
        # v1.122.0: the signal's payload used to be DROPPED for workflow
        # actions. It now rides the run as a synthetic "Trigger" output, so any
        # step can reference {{Trigger}} in its task/args/message — the webhook
        # body (or inbound message text) flows into the process.
        trigger_text = (context.get("text") or context.get("body") or "").strip()
        # Reserved-name guard: if the def has a REAL step named "Trigger", the
        # synthetic pre-seeded output would mark that step completed before it
        # ran (wrong context chains, wrong run-history truth) — the def owns
        # the name, so the injection simply steps aside.
        collides = any(s.name == "Trigger" for s in wf.steps)
        # v1.502.0 (north-star 2.8): the Trigger output is marked as a
        # signal's words (``kind`` "trigger": an agent step's task renders it
        # FENCED, ``workflows/engine.render_template``) and carries the run's
        # posture, so its agent steps start low like the session action.
        # The intelligence plan, release 3: a signal that fired with NO words
        # still fired, so ``{{Trigger}}`` is a value that was given (an empty
        # one), exactly as an input given as "" is. Without the seed a step
        # that names it now stops with "no value for Trigger"
        # (``workflows/checks.missing_values``). The posture rides only a
        # signal that brought words, as before: there is no outside text here.
        outputs = (
            {"Trigger": {"status": "completed", "summary": trigger_text[:1500], "kind": "trigger",
                         **((trust or {}) if trigger_text else {})}}
            if not collides
            else None
        )
        work = engine.run_record(run, wf, outputs=outputs)
        # Leap 05-b (review 2): the task that ended was Local only, so the
        # workflow this rule starts runs Local only too (every run its steps
        # create is, ``orchestrator.create_session``), as the card's Limits
        # line says.
        local = rule.source == "run_ended" and bool(str((context or {}).get("local_only") or ""))
        self._launch(_kept_local(work, True) if local else work, run.id)
        return {"ok": True, "kind": "workflow", "workflow": wf.name, "run_id": run.id}

    def _run_remote(self, rule: ReflexRule, context: dict[str, str]) -> dict[str, Any]:
        # Deliberately NOT project-grounded: a remote agent runs on someone
        # else's endpoint with no access to this box's project context, so
        # there is no grounding seam to thread ``rule.project_id`` through.
        from ..agents.remote import RemoteAgentRegistry

        reg = RemoteAgentRegistry(self.p.engine)
        record = reg.get(rule.target)
        if record is None:
            return {"ok": False, "error": f"no remote agent '{rule.target}'"}
        task = _render(rule.task_template, context) or _default_task(rule, context)
        previous_error = str(getattr(rule, "last_error", "") or "")
        self._launch(
            self._remote_then_tell(
                rule, reg.run(record, task, self.p.secrets.get), previous_error
            ),
            f"reflex-remote-{rule.id}",
        )
        return {"ok": True, "kind": "remote_agent", "agent": rule.target}

    async def _remote_then_tell(
        self, rule: ReflexRule, run: Any, previous_error: str
    ) -> Any:
        """v1.446.0 (review item 20): the hand-off runs in the background, so
        a remote agent that is down (no answer, a refusal) used to fail where
        nobody looked: the row said it fired and nothing else. Its failure now
        lands on the rule's row and is announced like a failed start. A
        ``manual:`` rule (the phone's ``/run``) is never announced."""
        import inspect

        out = (await run) if inspect.isawaitable(run) else run
        try:
            if isinstance(out, dict) and out.get("ok"):
                return out
            detail = str(out.get("detail") or "") if isinstance(out, dict) else ""
            error = f"remote agent '{rule.target}' failed: {detail or 'no answer'}"
            await _off_loop(functools.partial(self.store.mark_result, rule.id, ok=False, detail=error))
            await self._announce_failure(
                rule,
                _remote_words(rule.target, detail),
                error=error,
                previous_error=previous_error,
                attended=False,
                late=True,
            )
        except Exception:  # noqa: BLE001 — telling never breaks the hand-off
            log.warning(
                "reflex rule %r: could not record a remote failure", rule.name, exc_info=True
            )
        return out

    async def _run_session(
        self, rule: ReflexRule, context: dict[str, str], *, trust: dict[str, str] | None = None,
        rehearsal: str = "",
    ) -> dict[str, Any]:
        task = _render(rule.task_template, context) or _default_task(rule, context)
        # v1.200.0 — context spine: a rule tagged with a project spawns a
        # session CARRYING it, so the whole grounding pipeline (the runtime's
        # project brief/instructions/knowledge, the memory fabric) fires for
        # reflex work exactly as it does for a Projects task. An inbound
        # "client emailed the missing 1099" used to run with zero client
        # context. ``or None`` keeps a legacy ""/blank row honest.
        # v1.231.0 (audit AE17): stamped ``reflex:<rule>`` — the origin is
        # what the runtime's ask allowlist reads, so an unstamped reflex
        # session could never pause on an ask-tier tool; and the project's
        # own folder is resolved by create_session (AE1), not here.
        # Leap 05-a: WHO does the work — the rule's custom agent, resolved at
        # fire time exactly the way a schedule's is (one helper,
        # ``agents/resolve``; refused in words when it is gone or cannot be
        # given unattended work) — else the supervisor, as before.
        from ..agents.resolve import UnattendedAgent, resolve_for_unattended

        agent_name = str(getattr(rule, "agent_name", "") or "").strip()
        if agent_name:
            # Off the loop: an agent's allowance is a database read.
            who = await _off_loop(functools.partial(
                resolve_for_unattended, self.p, agent_name, who="rule", default=AgentType.SUPERVISOR,
            ))
        else:
            who = UnattendedAgent(agent_type=AgentType.SUPERVISOR)
        options: dict[str, Any] = {}
        if not str(rule.name or "").startswith("manual:"):
            # The run names its rule, so an "Always for this rule" is kept on
            # the RULE (``runtime.grant_scopes`` reads it off the run's own
            # row, never from anything the model wrote).
            options[RULE_OPTION] = rule.id
            # Leap 05-b: and what kind of rule it is, so the run's own end
            # never starts another "when another task fails" rule after a
            # rule of that kind started it (the chain is one deep).
            options[RULE_SOURCE_OPTION] = str(rule.source or "")
            # Idea 39: a rule made in a chat reports each run there (and a
            # Local only chat's rule runs Local only).
            options.update(await _off_loop(functools.partial(rule_report_options, self.p, rule)))
        if rehearsal:
            # Leap 06-a: a REHEARSAL's run carries it.
            options[_rehearsal_option()] = rehearsal
            # Leap 05-c: and what the rule did when it was rehearsed, so Keep
            # it never writes Limits for what the rule no longer does.
            from .limits import SHAPE_OPTION, shape as _limits_shape

            options[SHAPE_OPTION] = _limits_shape(rule)
        elif not str(rule.name or "").startswith("manual:"):
            # Leap 05-c: a real run carries the Limits kept after a rehearsal
            # (the folders it may write in) in its OWN options; the runtime
            # makes them the calls' write scope. A rehearsal carries none: it
            # shows what the rule would do, and Keep it approves that.
            from .limits import LIMITS_OPTION, options_for as _limits_options

            kept = _limits_options(rule)
            if kept is not None:
                options[LIMITS_OPTION] = kept
            if getattr(rule, "limits_stop_json", "") and getattr(self, "store", None) is not None:
                await _off_loop(functools.partial(self.store.clear_stop, rule.id))
        if rule.source == "run_ended" and str((context or {}).get("local_only") or ""):
            # Leap 05-b (review): the task that ended was Local only, and its
            # words are in this task, so this run is Local only too (refused
            # by name, at create_session, when its model leaves this network).
            options["local_only"] = True
            # Review 5: and its report keeps those words home unless the chat
            # it reports to is Local only (read when it reports).
            from ..agents import chat_delivery as _cd

            report = options.get(_cd.REPORT_OPTION)
            if isinstance(report, dict):
                options[_cd.REPORT_OPTION] = {**report, _cd.REPORT_WORDS_HOME: True}
        extra: dict[str, Any] = {}
        root = str(getattr(rule, "workspace_root", "") or "").strip()
        if root:
            extra["workspace_root"] = root
        if who.agent_name:
            extra["agent_name"] = who.agent_name
        for key in ("provider", "model", "max_steps", "approval_mode"):
            value = getattr(who, key)
            if value:
                extra[key] = value
        session = await self.orch.create_session(
            task,
            who.agent_type,
            project_id=rule.project_id or None,
            origin=f"reflex:{rule.name}",
            # v1.502.0 (north-star 2.8): an outside signal's work starts LOW
            # (passed only then, so a test double's call is unchanged).
            **(trust or {}),
            **({"options": options} if options else {}),
            **extra,
            # Leap 06-a: the person who pressed Rehearse started it (passed
            # only then: a real fire's call is unchanged).
            **({"started_by_person": True} if rehearsal else {}),
        )
        self._launch(self._run_as(who, session.id), session.id)
        return {"ok": True, "kind": "session", "session_id": session.id}

    async def _once_more(self, rule: ReflexRule, context: dict[str, str]) -> str:
        """Leap 05-b (review): a ``run_ended`` rule with ``again`` ("tell me
        why and try once more") runs the task that ended once more, named by
        this rule, so the one-deep guard holds: if the second try fails too,
        nothing more starts. A workflow: a run of that saved workflow whose
        record says this rule started it. A schedule: that schedule's fire,
        once the fire that ended has finished, whose signal names this rule
        (``triggers/starts.once_more_by``). A Local only task's second try is
        Local only. Returns what it did in words ("" when the rule has no
        ``again``). Never raises."""
        try:
            if str(rule.source or "") != "run_ended" or rule.decoded_start().get("again") is not True:
                return ""
            kind = str(context.get("ended_kind") or "")
            name = " ".join(str(context.get("ended_name") or "").split())
            ran = " ".join(str(context.get("ended_workflow") or "").split())
            if kind == "schedule" and ran and rule.decoded_start().get("what") == "workflow":
                # Review 2: a rule for a WORKFLOW heard it fail inside a
                # schedule's fire; its card says it runs that workflow once
                # more, so that is what runs (named by this rule).
                kind, name = "workflow", ran
            if kind not in ("schedule", "workflow") or not name:
                return ""
            local_only = bool(str(context.get("local_only") or ""))
            if kind == "workflow":
                return await self._workflow_once_more(rule, name, local_only)
            scheduler = getattr(self.p, "scheduler", None)
            if scheduler is None or await _off_loop(functools.partial(scheduler.get, name)) is None:
                return f"The schedule “{name}” is no longer there, so it was not run once more."
            self._launch(self._schedule_once_more(scheduler, name, rule, local_only), f"reflex-again-{rule.id}")
            return once_more_schedule_words(name)
        except Exception:  # noqa: BLE001 — the rule's own start stands
            log.warning("rule %r: the task could not be run once more", rule.name, exc_info=True)
            return "The task could not be run once more."

    async def _workflow_once_more(self, rule: ReflexRule, name: str, local_only: bool) -> str:
        from ..triggers.starts import rule_started_by
        from ..workflows.engine import WorkflowEngine
        from ..workflows.store import WorkflowStore

        wf = await _off_loop(functools.partial(WorkflowStore(self.p.engine).load_def, name))
        if wf is None:
            return f"The workflow “{name}” is no longer saved, so it was not run once more."
        engine = WorkflowEngine(self.p, self.orch)
        run = await _off_loop(functools.partial(engine.create_record, wf, started_by=rule_started_by(rule)))
        work = engine.run_record(run, wf)
        self._launch(_kept_local(work, True) if local_only else work, run.id)
        return f"Runs the workflow “{wf.name}” once more."

    async def _schedule_once_more(self, scheduler: Any, name: str, rule: ReflexRule, local_only: bool) -> Any:
        """The schedule's second try: after the fire that ended lets go of it
        (its delivery and report finish after the signal), one Run now whose
        ONE signal names this rule. Review 2: when that fire is still running
        after :data:`ONCE_MORE_WAIT_S`, nothing runs (a Run now would only
        stamp the schedule's row "Skipped: already running" over its
        "Failed"), and the rule's row says so."""
        from ..triggers import starts as _starts

        if not await _off_loop(functools.partial(scheduler.wait_until_idle, name, ONCE_MORE_WAIT_S)):
            store = getattr(self, "store", None)
            if store is not None:
                try:
                    await _off_loop(functools.partial(
                        store.amend_result, rule.id, once_more_schedule_words(name),
                        once_more_still_running_words(name),
                    ))
                except Exception:  # noqa: BLE001 — the row keeps its first words
                    log.warning("rule %r: could not say the second try did not run", rule.name, exc_info=True)
            return None
        token = _starts.once_more_by(rule)
        try:
            return await _kept_local(scheduler.run_now(name), local_only)
        finally:
            _starts.once_more_done(token)

    def _run_as(self, who: Any, session_id: str) -> Any:
        """The run, the way the schedule's fire runs it: a model team member
        held to its own model, a custom agent with its definition, else
        exactly ``run_session(id)`` as before (a test double's call)."""
        if getattr(who, "member", None) is not None:
            from ..agents import model_team as _mt

            return _mt.run_as_member(
                self.p, who.member,
                lambda: self.orch.run_session(session_id, definition=who.definition),
            )
        if getattr(who, "definition", None) is not None:
            return self.orch.run_session(session_id, definition=who.definition)
        return self.orch.run_session(session_id)

    # -- helpers -----------------------------------------------------------
    def _launch(self, coro: Any, session_id: str) -> Any:
        if self.spawn_bg is not None:
            return self.spawn_bg(session_id, coro)
        import asyncio

        return asyncio.ensure_future(coro)

    async def _publish(self, rule: ReflexRule, result: dict[str, Any]) -> None:
        bus = getattr(self.p, "event_bus", None)
        if bus is None:
            return
        try:
            await bus.publish(
                EventType.REFLEX_FIRED,
                {
                    "rule": rule.name,
                    "source": rule.source,
                    "match": rule.match,
                    "action": rule.action,
                    "ok": bool(result.get("ok")),
                    "detail": result.get("error") or result.get("kind", ""),
                },
            )
        except Exception:  # noqa: BLE001 — the bus must never block a reflex
            pass


    async def _announce_failure(
        self,
        rule: ReflexRule,
        reason: str,
        *,
        error: str,
        previous_error: str,
        attended: bool,
        late: bool = False,
    ) -> None:
        """Publish ``reflex.failed``: the bell's attention row, the tray's
        notice and a default phone alert ("Your rule X couldn't start: why").

        Said ONCE per failure episode: a rule whose row already carried this
        very error before the fire says nothing new (the row still records
        it). Never for a fire a person is watching (``attended``, a
        ``manual:`` rule) and never for a ``comm`` rule: the message that
        fired it gets the reply "Rule X could not start: …" from the inbound
        poller (comm/inbound), so the phone hears it ONCE. Never raises."""
        if attended or str(rule.name or "").startswith("manual:"):
            return
        if rule.source == "comm" and not late:
            # ``late``: a remote hand-off that failed AFTER the reply said
            # it started — nobody has heard that one.
            return
        if previous_error and previous_error[:500] == (error or "failed")[:500]:
            return
        bus = getattr(self.p, "event_bus", None)
        if bus is None:
            return
        try:
            await bus.publish(
                EventType.REFLEX_FAILED,
                {
                    "rule": rule.name,
                    "rule_id": rule.id,
                    "source": rule.source,
                    "action": rule.action,
                    "target": rule.target,
                    "reason": (reason or CANNOT_START_WORDS)[:300],
                },
            )
        except Exception:  # noqa: BLE001 — the bus must never block a reflex
            pass


#: v1.446.0: the reason when nothing better is known.
CANNOT_START_WORDS = "it could not start its action"

#: Leap 05-a: the run option naming the rule a run was started by (its id),
#: which ``runtime.grant_scopes`` turns into the ``rule`` scope of a standing
#: OK ("Always for this rule").
RULE_OPTION = "rule_id"
#: Leap 05-b: the run option naming the SOURCE of the rule that started a
#: run (``run_ended`` stops a chain at one).
RULE_SOURCE_OPTION = "rule_source"
#: Leap 05-b: why a ``run_ended`` rule's work starts carefully.
CAREFUL_RUN_LABEL = "the end of a task that ran carefully"
#: Leap 05-b (review): how long "try once more" waits for the schedule's
#: fire that ended to let go of it (its delivery and report).
ONCE_MORE_WAIT_S = 900.0


def _kept_here(rule: ReflexRule, context: dict[str, str]) -> dict[str, str]:
    """Leap 05-b (review): the context a rule's action gets. When the task
    that ended was Local only, an action other than a session is not given
    what that task said (``detail``). A remote agent is handed nothing at all
    (review 2, :func:`local_only_hand_off_words`). A session rule keeps it:
    its run is Local only. Review 3: no action reads ``detail`` past this
    today (a workflow rule's ``Trigger`` output is ``text``, the task's
    name), so this is a BACKSTOP for a later one that does — a workflow runs
    Local only, but a step of it can send a message. Pure."""
    if (
        str(getattr(rule, "source", "") or "") == "run_ended"
        and str((context or {}).get("local_only") or "")
        and str(getattr(rule, "action", "") or "") != "session"
    ):
        return {**context, "detail": ""}
    return context


def local_only_hand_off_words(rule: ReflexRule, context: dict[str, str]) -> str:
    """Leap 05-b (review 2): the row's words when a ``run_ended`` rule would
    hand a Local only task's end to a REMOTE AGENT, else "". A remote agent
    runs on another computer, so its work can never be Local only: nothing of
    that task (its name, what it does, how it ended, what it said) is handed
    on. Pure."""
    if (
        str(getattr(rule, "source", "") or "") != "run_ended"
        or str(getattr(rule, "action", "") or "") != "remote_agent"
        or not str((context or {}).get("local_only") or "")
    ):
        return ""
    agent = _short(str(getattr(rule, "target", "") or ""))[:80]
    which = f"the remote agent “{agent}”" if agent else "a remote agent"
    return f"The task that ended was Local only, so it was not handed to {which}."


def once_more_schedule_words(name: str) -> str:
    """What "try once more" says it does for a schedule (the row's words)."""
    return f"Runs the schedule “{name}” once more."


def once_more_still_running_words(name: str) -> str:
    """Review 2: the row's words when the fire that ended was still running
    after :data:`ONCE_MORE_WAIT_S`, so the second try did not run."""
    minutes = int(ONCE_MORE_WAIT_S // 60)
    return f"The schedule “{name}” was still running {minutes} minutes later, so it was not run once more."


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


async def _kept_local(coro: Any, local_only: bool) -> Any:
    """Await ``coro`` inside a Local only scope when ``local_only`` (every
    run it creates is Local only, ``orchestrator.create_session``)."""
    if not local_only:
        return await coro
    from ..providers.egress import local_only_scope

    with local_only_scope(True):
        return await coro


def rule_report_options(platform: Any, rule: ReflexRule) -> dict[str, Any]:
    """Idea 39 for a rule (leap 05-a): the run options that make a rule's
    run report into the chat it was made in — ``chat_thread_id``,
    ``report_from`` (kind ``rule``) and, for a Local only chat,
    ``local_only`` (the run is Local only). ``{}`` for a rule made on a page
    or set to Stop reporting. BLOCKING (one row read); never raises."""
    from ..agents import chat_delivery as _cd

    chat = _cd.clean_chat_thread_id(getattr(rule, "chat_thread_id", "") or "")
    if not chat:
        return {}
    out: dict[str, Any] = {
        "chat_thread_id": chat,
        _cd.REPORT_OPTION: {"kind": "rule", "name": rule.name, "id": rule.id, "href": "/reflex"},
    }
    engine = getattr(platform, "engine", None)
    try:
        from ..providers.egress import thread_local_only

        if engine is not None and thread_local_only(engine, chat):
            out["local_only"] = True
    except Exception:  # noqa: BLE001 — unreadable: the chat decides nothing
        log.warning("could not read whether a rule's chat is Local only", exc_info=True)
    return out


def _reason_words(rule: ReflexRule, error: str) -> str:
    """Plain words for a failed start the router wrote itself (bell, tray,
    phone). The rule's row keeps the router's own short error."""
    target = (rule.target or "").strip()
    if error.startswith("no saved workflow"):
        return f"the workflow “{target}” it starts is no longer saved"
    if error.startswith("no remote agent"):
        return f"the remote agent “{target}” it hands work to is no longer set up"
    if error.startswith("unknown action"):
        return f"it names an action Iron Jarvis doesn't know (“{rule.action}”)"
    return _masked(error) or CANNOT_START_WORDS


def _remote_words(agent: str, detail: str) -> str:
    """Plain words for a remote agent's failed hand-off."""
    name = (agent or "").strip() or "the remote agent"
    text = (detail or "").strip()
    if not text or text.startswith("request failed"):
        return f"the remote agent “{name}” could not be reached"
    if "did not answer" in text:
        return f"the remote agent “{name}” did not answer in time"
    return f"the remote agent “{name}” said: {_masked(text)}"


def _exception_words(exc: BaseException) -> str:
    """A raised start error, in the failure-words rule's plain sentence."""
    try:
        from ..providers.failure_words import run_failure_words

        reason, _technical = run_failure_words(exc, started=False)
        return reason
    except Exception:  # noqa: BLE001
        return CANNOT_START_WORDS


def _masked(text: str) -> str:
    try:
        from ..providers.failure_words import mask_failure_text

        out = mask_failure_text(text)
    except Exception:  # noqa: BLE001
        out = ""
    out = " ".join(str(out or "").split())
    return out if len(out) <= 240 else out[:239] + "…"


def result_words(result: dict[str, Any]) -> tuple[str, str]:
    """``(words, href)`` for a successful fire (readiness 0j.5 item 15): what
    the rule started, in plain words, and the page where it can be seen. The
    Rules page shows both on the Test press's answer and on the row's last
    fire. An id is never part of the words. Never raises."""
    kind = str(result.get("kind") or "")
    # Leap 05-b (review): what "try once more" did, said after it.
    again = " ".join(str(result.get("again") or "").split())[:300]
    if kind == "workflow":
        name = " ".join(str(result.get("workflow") or "").split())
        words = f"Started the workflow “{name}”." if name else "Started a workflow."
        href = "/workflows"
    elif kind == "session":
        sid = str(result.get("session_id") or "").strip()
        href = f"/sessions/{quote(sid, safe='')}" if sid else "/sessions"
        words = "Started an agent run."
    elif kind == "remote_agent":
        name = " ".join(str(result.get("agent") or "").split())
        words = f"Handed the task to {name}." if name else "Handed the task to a remote agent."
        href = "/agents?view=team"
    else:
        words, href = "Started.", ""
    return (f"{words} {again}" if again else words), href


def _result_detail(result: dict[str, Any]) -> str:
    """One line naming what a successful fire started (for ``last_result``)."""
    kind = str(result.get("kind") or "")
    ref = result.get("run_id") or result.get("session_id") or result.get("agent") or ""
    target = result.get("workflow") or ""
    return " ".join(x for x in (kind, str(target), str(ref)) if x).strip()


def summarize_fires(fired: list[dict[str, Any]]) -> dict[str, Any]:
    """The honest ack fields for a signal that matched rules (v1.231.0, AE6).

    ``fired`` counts only the rules that actually STARTED their action;
    ``failed`` names each rule that matched but could not start, with the
    reason. ``reflexes_fired`` is the pre-v1.231.0 key, kept (additive API)
    but now equal to ``fired`` — it used to count failures as fires."""
    ok = [f for f in fired if f.get("ok")]
    failed = [
        {"rule": str(f.get("rule") or ""), "error": str(f.get("error") or "failed")}
        for f in fired
        if not f.get("ok")
    ]
    return {"fired": len(ok), "failed": failed, "reflexes_fired": len(ok)}


def _text_of(body: Any) -> str:
    """Best-effort human text from a webhook body for {text} interpolation."""
    if isinstance(body, str):
        return body[:2000]
    if isinstance(body, dict):
        for key in ("text", "message", "body", "content", "title"):
            v = body.get(key)
            if isinstance(v, str) and v.strip():
                return v[:2000]
    return ""


def _default_task(rule: ReflexRule, context: dict[str, str]) -> str:
    if rule.source == "run_ended" and context.get("ended_kind"):
        # Leap 05-b: which task ended and how come from the record (one
        # line each); what the run itself said goes in fenced, as data.
        what = _short(context.get("what") or "a task")
        lead = (
            f"{what[:1].upper()}{what[1:]} ended: {_short(context.get('outcome') or 'Failed')}. "
            f"{_short(context.get('reason') or '')}".strip()
        )
        where = _short(context.get("href") or "")
        does = _short(context.get("does") or "")
        out = f"{lead} It started the rule '{_short(rule.name)}'."
        if does:
            # Review: what the task does, from its own record.
            out += f" That task {does.rstrip('.')}."
        out += " Find out why it fell short and say so in plain words."
        if where:
            out += f" Its record is at {where} in Iron Jarvis."
        detail = context.get("detail") or ""
        if detail:
            out += f" What the run itself said, as data:\n\n{_fenced(detail)}"
        return out
    if rule.source in ("file", "download") and context.get("path"):
        # Leap 05-a: the file's name was chosen by whoever made it, so its
        # location goes in fenced, as data.
        what = (
            f"A new file landed in the folder {_short(context.get('folder') or '')}"
            if rule.source == "file"
            else "A download finished in your browser"
        )
        return (
            f"{what}, and it started the rule '{_short(rule.name)}'. Decide what to do with "
            f"it and act. Where the file is, as data:\n\n{_fenced(context.get('path') or '')}"
        )
    signal = context.get("text") or context.get("body") or ""
    where = f"{rule.source}" + (f" '{rule.match}'" if rule.match else "")
    if signal:
        return (
            f"An inbound {where} signal fired the '{rule.name}' reflex. Its "
            f"content is below, as data:\n\n{_fenced(signal)}"
        )
    return f"The '{rule.name}' reflex fired from {where}. Decide what to do and act."
