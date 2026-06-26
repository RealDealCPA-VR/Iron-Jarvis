"""The Computer-Use harness — decomposed, gated, programmatically-verified runs.

``ComputerUseHarness.run(task, checkpoints)`` executes a plan with every
safety-critical best practice wired in as *real code*:

* **Opt-in gate** — raises :class:`ComputerUseDisabled` unless ``policy.enabled``.
* **Step budget** — at most ``policy.max_steps`` actions; else
  :class:`BudgetExceeded`.
* **Allowlist gate** — domain/action off the allowlist ⇒ :class:`PolicyDenied`.
* **Approval gate** — sensitive/destructive actions create an
  :class:`ApprovalRequest`; the run proceeds only if an injected
  ``approval_resolver`` approves, otherwise it BLOCKS (``awaiting_approval`` /
  ``blocked``).
* **Injection stop** — every extracted/read page text is scanned; a hit stops the
  run with status ``blocked`` (untrusted content is never followed).
* **Retry + recovery** — failed actions retry up to ``policy.max_retries`` with a
  simple recovery (re-read the page, escalate to the fallback selector).
* **Programmatic verification** — after each checkpoint, its ``verify`` predicate
  is evaluated against the LIVE page. The model is never asked "are you done?".
* **Trace** — actions, results, screenshots, errors are recorded and persisted as
  JSON on the run.
"""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import Engine

from ..core.db import session_scope
from ..core.events import EventBus
from ..core.ids import utcnow
from .approvals import ApprovalQueue
from .base import (
    Action,
    ActionResult,
    BudgetExceeded,
    Browser,
    Checkpoint,
    ComputerUseDisabled,
    InjectionDetected,
    Page,
    PolicyDenied,
    UnknownSelector,
)
from .models import ApprovalRequest, ComputerUseRun
from .policy import ComputerUsePolicy
from .safety import detect_injection
from .trace import TraceRecorder

#: An approval resolver decides a single :class:`ApprovalRequest`:
#: ``True`` = approve & proceed, ``False`` = deny & block.
ApprovalResolver = Callable[[ApprovalRequest], bool]


class ComputerUseHarness:
    """Runs a checkpointed Computer-Use task with full safety enforcement."""

    def __init__(
        self,
        browser: Browser,
        policy: ComputerUsePolicy,
        trace: TraceRecorder,
        approvals: ApprovalQueue,
        *,
        approval_resolver: ApprovalResolver | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self.browser = browser
        self.policy = policy
        self.trace = trace
        self.approvals = approvals
        self.approval_resolver = approval_resolver
        self.event_bus = event_bus
        self._engine: Engine | None = getattr(approvals, "engine", None)
        self._page: Page | None = None
        self._steps = 0

    # ------------------------------------------------------------------ #
    # Run
    # ------------------------------------------------------------------ #
    async def run(self, task: str, checkpoints: list[Checkpoint]) -> ComputerUseRun:
        # Opt-in gate (default OFF). Prefer APIs/UI only when explicitly enabled.
        if not self.policy.enabled:
            raise ComputerUseDisabled(
                "Computer Use is disabled (opt-in). Enable it explicitly before use."
            )

        run = ComputerUseRun(task=task, status="running", steps=0, trace_json="[]")
        run = self._persist(run)
        self.trace.start(run.id)
        self._page = None
        self._steps = 0

        try:
            for cp in checkpoints:
                blocked = await self._run_checkpoint(run, cp)
                if blocked is not None:
                    # A terminal, non-exception stop (awaiting_approval / blocked).
                    return self._finish(run, blocked)
                # Programmatic verification against the LIVE page (never a model).
                ok, detail = await self._verify(cp)
                self.trace.record_note(
                    "verify", checkpoint=cp.name, ok=ok, detail=detail
                )
                if not ok:
                    self.trace.record_error(
                        f"checkpoint {cp.name!r} failed verification: {detail}",
                        where="verify",
                    )
                    return self._finish(run, "failed")
            return self._finish(run, "completed")
        except InjectionDetected as exc:
            # Untrusted content tried to steer us — stop, do not comply.
            self.trace.record_error(str(exc), where="injection")
            return self._finish(run, "blocked")

    # ------------------------------------------------------------------ #
    # Checkpoint
    # ------------------------------------------------------------------ #
    async def _run_checkpoint(self, run: ComputerUseRun, cp: Checkpoint) -> str | None:
        """Run a checkpoint's actions. Returns a terminal status to stop, else None."""
        for action in cp.actions:
            # --- step budget ---------------------------------------------
            if self._steps >= self.policy.max_steps:
                self.trace.record_error(
                    f"step budget exceeded ({self.policy.max_steps})", where="budget"
                )
                run.steps = self._steps
                self._persist(run)
                raise BudgetExceeded(
                    f"exceeded max_steps={self.policy.max_steps} at checkpoint {cp.name!r}"
                )
            self._steps += 1
            run.steps = self._steps
            self.trace.record_action(action, checkpoint=cp.name)

            # --- policy gate (allowlist + sensitivity) -------------------
            decision = self.policy.check(action, self._page)
            if not decision.allowed:
                self.trace.record_error(
                    f"policy denied: {decision.reason}", where="policy"
                )
                self._persist(run)
                raise PolicyDenied(decision.reason)

            if decision.requires_approval:
                stop = await self._handle_approval(run, action, decision.reason)
                if stop is not None:
                    return stop  # awaiting_approval / blocked

            # --- execute with retry + recovery ---------------------------
            result = await self._execute_with_retry(action)
            self.trace.record_result(result)
            if not result.ok:
                self.trace.record_error(
                    result.error or "action failed", where=action.kind
                )
                # A hard action failure means the checkpoint cannot be trusted.
                self._persist(run)
                return "failed"
        return None

    # ------------------------------------------------------------------ #
    # Approval gate
    # ------------------------------------------------------------------ #
    async def _handle_approval(
        self, run: ComputerUseRun, action: Action, reason: str
    ) -> str | None:
        req = self.approvals.create_request(run.id, action, reason)
        run.status = "awaiting_approval"
        self._persist(run)
        self.trace.record_approval(req.id, "pending", reason)

        resolver = self.approval_resolver
        if resolver is None:
            # No human present: BLOCK and wait. The request stays pending.
            self.trace.record_note("blocked_awaiting_approval", request_id=req.id)
            return "awaiting_approval"

        granted = bool(resolver(req))
        if granted:
            self.approvals.approve(req.id)
            self.trace.record_approval(req.id, "approved", reason)
            run.status = "running"
            self._persist(run)
            return None  # proceed to execute

        self.approvals.deny(req.id)
        self.trace.record_approval(req.id, "denied", reason)
        return "blocked"

    # ------------------------------------------------------------------ #
    # Execution + recovery
    # ------------------------------------------------------------------ #
    async def _execute_with_retry(self, action: Action) -> ActionResult:
        attempts = 0
        last_error = ""
        use_fallback = action.fallback
        while True:
            try:
                return await self._execute(action, fallback=use_fallback, retries=attempts)
            except InjectionDetected:
                raise  # never retry an injection — propagate to stop the run
            except (UnknownSelector, Exception) as exc:  # noqa: BLE001
                if isinstance(exc, (PolicyDenied, BudgetExceeded)):
                    raise
                last_error = str(exc)
                attempts += 1
                if attempts > self.policy.max_retries:
                    return ActionResult(
                        action=action,
                        ok=False,
                        error=f"{last_error} (after {attempts - 1} retries)",
                        retries=attempts - 1,
                    )
                # Simple recovery: re-read the page; escalate to fallback selector.
                self.trace.record_note(
                    "recovery", attempt=attempts, error=last_error, action=action.kind
                )
                try:
                    self._page = await self.browser.read()
                except Exception:  # noqa: BLE001
                    pass
                if action.kind in ("click", "screenshot_click"):
                    use_fallback = True

    async def _execute(self, action: Action, *, fallback: bool, retries: int) -> ActionResult:
        kind = action.kind

        if kind == "navigate":
            page = await self.browser.navigate(action.value or "")
            self._page = page
            self._scan(page.text, where="navigate")
            return ActionResult(action, ok=True, output=page.url, page=page, retries=retries)

        if kind == "read":
            page = await self.browser.read()
            self._page = page
            self._scan(page.text, where="read")
            return ActionResult(action, ok=True, output=page.url, page=page, retries=retries)

        if kind == "extract":
            if action.selector is None:
                return ActionResult(action, ok=False, error="extract requires a selector")
            text = await self.browser.extract(action.selector)
            self._scan(text, where="extract")
            return ActionResult(action, ok=True, output=text, page=self._page, retries=retries)

        if kind == "click":
            if action.selector is None:
                return ActionResult(action, ok=False, error="click requires a selector")
            page = await self.browser.click(action.selector, fallback=fallback)
            self._page = page
            return ActionResult(
                action, ok=True, output=page.url, page=page, retries=retries,
                fallback_used=fallback,
            )

        if kind == "type":
            if action.selector is None:
                return ActionResult(action, ok=False, error="type requires a selector")
            page = await self.browser.type(action.selector, action.value or "")
            self._page = page
            return ActionResult(action, ok=True, output="typed", page=page, retries=retries)

        if kind == "screenshot":
            data = await self.browser.screenshot()
            self.trace.record_screenshot(data, label="screenshot")
            return ActionResult(action, ok=True, output=f"{len(data)} bytes", retries=retries)

        if kind == "screenshot_click":
            # Screenshot-based clicking is a LABELLED FALLBACK ONLY.
            if not action.fallback:
                return ActionResult(
                    action,
                    ok=False,
                    error=(
                        "screenshot-based clicking is a fallback; set fallback=True "
                        "to use it (prefer DOM/a11y selectors)"
                    ),
                )
            if action.selector is None:
                return ActionResult(action, ok=False, error="screenshot_click requires a selector")
            data = await self.browser.screenshot()
            self.trace.record_screenshot(data, label="screenshot_click")
            page = await self.browser.click(action.selector, fallback=True)
            self._page = page
            return ActionResult(
                action, ok=True, output=page.url, page=page, retries=retries,
                fallback_used=True,
            )

        if kind == "wait":
            return ActionResult(action, ok=True, output="waited", retries=retries)

        return ActionResult(action, ok=False, error=f"unknown action kind: {kind!r}")

    def _scan(self, text: str | None, *, where: str) -> None:
        verdict = detect_injection(text)
        if verdict["flagged"]:
            self.trace.record_note(
                "injection_detected", where=where, reason=verdict["reason"]
            )
            raise InjectionDetected(verdict["reason"])

    # ------------------------------------------------------------------ #
    # Programmatic verification
    # ------------------------------------------------------------------ #
    async def _verify(self, cp: Checkpoint) -> tuple[bool, str]:
        if not cp.verify:
            return True, "no predicate"
        # Always evaluate against a freshly read, live page.
        try:
            page = await self.browser.read()
            self._page = page
        except Exception:  # noqa: BLE001
            page = self._page
        if page is None:
            return False, "no page to verify"

        kind = cp.verify.get("kind")
        arg = cp.verify.get("arg")

        if kind == "url_contains":
            ok = bool(arg) and str(arg) in page.url
            return ok, f"url={page.url!r} contains {arg!r}: {ok}"

        if kind == "text_present":
            ok = bool(arg) and str(arg) in page.text
            return ok, f"text_present {arg!r}: {ok}"

        if kind == "dom_has":
            ok = self._dom_has(page, arg)
            return ok, f"dom_has {arg!r}: {ok}"

        return False, f"unknown verify kind: {kind!r}"

    @staticmethod
    def _dom_has(page: Page, arg: Any) -> bool:
        for el in page.a11y_tree:
            if isinstance(arg, dict):
                role_ok = "role" not in arg or str(el.get("role", "")).lower() == str(arg["role"]).lower()
                name_ok = "name" not in arg or str(arg["name"]).lower() in str(el.get("name", "")).lower()
                if role_ok and name_ok:
                    return True
            else:
                needle = str(arg).lower()
                hay = f"{el.get('role', '')} {el.get('name', '')}".lower()
                if needle in hay:
                    return True
        return False

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    def _persist(self, run: ComputerUseRun) -> ComputerUseRun:
        run.trace_json = self.trace.to_json()
        if self._engine is None:
            return run
        with session_scope(self._engine) as db:
            merged = db.merge(run)
            db.commit()
            db.refresh(merged)
        return merged

    def _finish(self, run: ComputerUseRun, status: str) -> ComputerUseRun:
        run.status = status
        run.steps = self._steps
        run.finished_at = utcnow()
        run.trace_json = self.trace.to_json()
        merged = self._persist(run)
        if self.event_bus is not None:
            # Fire-and-forget observability hook (best-effort; never blocks).
            try:
                import asyncio

                asyncio.ensure_future(
                    self.event_bus.publish(
                        "computeruse.run_finished",
                        {"run_id": merged.id, "status": status, "steps": merged.steps},
                    )
                )
            except Exception:  # noqa: BLE001
                pass
        return merged
