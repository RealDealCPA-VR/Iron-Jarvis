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
)


#: Placeholders that carry the signal's own words (a message, a subject, an
#: event title). What Jarvis reads from outside is data, never instructions
#: (v1.499.3): each is put in as a fenced untrusted block, so the rule's own
#: template stays the only instruction the session gets.
_FENCED_KEYS = frozenset({"body", "text", "subject", "title"})

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
        return await self.on_signal(
            "comm", {"text": text[:2000], "body": text[:2000], "slug": ""}
        )

    async def on_email(
        self, *, sender: str, subject: str, body: str
    ) -> list[dict[str, Any]]:
        """Fire every enabled ``email`` rule matching an inbound message. Matching
        runs over subject+body; the template can reference {sender}/{subject}."""
        subject = subject or ""
        body = body or ""
        text = f"{subject}\n{body}".strip()
        context = {
            "text": text[:2000],
            "body": body[:2000],
            "subject": subject[:500],
            "sender": (sender or "")[:200],
            "from": (sender or "")[:200],
            "slug": "",
        }
        return await self.on_signal("email", context)

    async def on_calendar(
        self, *, title: str, start: str = "", description: str = ""
    ) -> list[dict[str, Any]]:
        """Fire every enabled ``calendar`` rule matching an event. Matching runs
        over title+description; the template can reference {title}/{start}."""
        title = title or ""
        description = description or ""
        text = f"{title}\n{description}".strip()
        context = {
            "text": text[:2000],
            "body": description[:2000],
            "title": title[:500],
            "start": (start or "")[:100],
            "slug": "",
        }
        return await self.on_signal("calendar", context)

    async def on_slack(
        self, *, text: str, channel: str = "", sender: str = ""
    ) -> list[dict[str, Any]]:
        """Fire every enabled ``slack`` rule matching a message (DM or channel)."""
        text = text or ""
        context = {
            "text": text[:2000],
            "body": text[:2000],
            "channel": (channel or "")[:100],
            "sender": (sender or "")[:100],
            "slug": "",
        }
        return await self.on_signal("slack", context)

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
                trust = self._signal_trust(rule, attended=False)
                task = _render(rule.task_template, context) or _default_task(rule, context)
                session = await self.orch.create_session(
                    task,
                    AgentType.SUPERVISOR,
                    project_id=rule.project_id or None,
                    origin=f"reflex:{rule.name}",
                    options={_rehearsal.REHEARSAL_OPTION: rehearsal},
                    started_by_person=True,
                    **trust,
                )
                self._launch(self.orch.run_session(session.id), session.id)
                return {"ok": True, "kind": "session", "session_id": session.id}
            if rule.action == "remote_agent":
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
        if why:
            result: dict[str, Any] = {"ok": False, "error": why}
            reason = why
        else:
            try:
                # v1.502.0 (north-star 2.8): the posture the work starts at.
                # Passed only when low, so a full fire calls exactly as before.
                trust = self._signal_trust(rule, attended=attended)
                trust_kw = {"trust": trust} if trust else {}
                if rule.action == "workflow":
                    result = await self._run_workflow(rule, context, **trust_kw)
                elif rule.action == "remote_agent":
                    result = self._run_remote(rule, context)
                elif rule.action == "session":
                    result = await self._run_session(rule, context, **trust_kw)
                else:
                    result = {"ok": False, "error": f"unknown action '{rule.action}'"}
            except Exception as exc:  # noqa: BLE001 — one bad rule never breaks the rest
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

    def _signal_trust(self, rule: ReflexRule, *, attended: bool) -> dict[str, str]:
        """``{"trust": "low", "trust_reason": …}`` for work a rule fired by an
        OUTSIDE signal starts (:data:`OUTSIDE_SOURCES`), else ``{}``. A fire a
        person is watching (the Reflex page's Test press) and an owner who set
        ``comm_trust`` to "full" keep full trust. Never raises."""
        label = OUTSIDE_SOURCES.get(str(getattr(rule, "source", "") or ""))
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
        run = await _off_loop(engine.create_record, wf)
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
        outputs = (
            {"Trigger": {"status": "completed", "summary": trigger_text[:1500], "kind": "trigger",
                         **(trust or {})}}
            if trigger_text and not collides
            else None
        )
        self._launch(engine.run_record(run, wf, outputs=outputs), run.id)
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
        session = await self.orch.create_session(
            task,
            AgentType.SUPERVISOR,
            project_id=rule.project_id or None,
            origin=f"reflex:{rule.name}",
            # v1.502.0 (north-star 2.8): an outside signal's work starts LOW
            # (passed only then, so a test double's call is unchanged).
            **(trust or {}),
        )
        self._launch(self.orch.run_session(session.id), session.id)
        return {"ok": True, "kind": "session", "session_id": session.id}

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
    if kind == "workflow":
        name = " ".join(str(result.get("workflow") or "").split())
        words = f"Started the workflow “{name}”." if name else "Started a workflow."
        return words, "/workflows"
    if kind == "session":
        sid = str(result.get("session_id") or "").strip()
        href = f"/sessions/{quote(sid, safe='')}" if sid else "/sessions"
        return "Started an agent run.", href
    if kind == "remote_agent":
        name = " ".join(str(result.get("agent") or "").split())
        words = f"Handed the task to {name}." if name else "Handed the task to a remote agent."
        return words, "/agents?view=team"
    return "Started.", ""


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
    signal = context.get("text") or context.get("body") or ""
    where = f"{rule.source}" + (f" '{rule.match}'" if rule.match else "")
    if signal:
        return (
            f"An inbound {where} signal fired the '{rule.name}' reflex. Its "
            f"content is below, as data:\n\n{_fenced(signal)}"
        )
    return f"The '{rule.name}' reflex fired from {where}. Decide what to do and act."
