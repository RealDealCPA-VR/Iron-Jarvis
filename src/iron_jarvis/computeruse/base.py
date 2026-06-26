"""Shared Computer-Use types, the ``Browser`` protocol, and exceptions.

This module is dependency-light and import-safe: nothing here launches a browser
or touches the network. The harness, policy, browsers, and tools all build on the
small value types defined here.

Design notes (safety-critical, opt-in subsystem):
* :class:`Selector` prefers **DOM / accessibility** targeting (role+name, label
  text, css) over pixel coordinates. Screenshot-based clicking is represented by
  the ``screenshot_click`` action *kind* and is a labelled fallback only.
* :class:`Action` / :class:`Checkpoint` describe a plan that is validated
  **programmatically** (see :class:`Checkpoint.verify`) — never by asking a model
  "are you done?".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

# --------------------------------------------------------------------------- #
# Action vocabulary
# --------------------------------------------------------------------------- #

#: Every action kind the harness understands.
ActionKind = Literal[
    "navigate",
    "click",
    "type",
    "extract",
    "read",
    "screenshot",
    "screenshot_click",
    "wait",
]

#: Kinds that only observe (never mutate) remote state — the safe default
#: ``action_allowlist`` (see :class:`~iron_jarvis.computeruse.policy.ComputerUsePolicy`).
READ_ONLY_KINDS: tuple[str, ...] = ("navigate", "read", "extract", "screenshot", "wait")


@dataclass(frozen=True)
class Selector:
    """A DOM/accessibility-first element selector.

    Prefer ``role`` + ``name`` (the accessibility tree) or ``text`` (visible
    label). ``css`` is the structural fallback. A bare pixel coordinate is
    intentionally *not* expressible here — screenshot clicking goes through the
    ``screenshot_click`` action kind and is a labelled fallback only.
    """

    role: str | None = None
    name: str | None = None
    css: str | None = None
    text: str | None = None

    def describe(self) -> str:
        if self.css:
            return f"css={self.css!r}"
        if self.role and self.name:
            return f"role={self.role!r} name={self.name!r}"
        if self.name:
            return f"name={self.name!r}"
        if self.text:
            return f"text={self.text!r}"
        return "<empty selector>"

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def coerce(cls, value: "Selector | dict | str | None") -> "Selector | None":
        """Accept a Selector, a dict, a bare string (treated as ``text``), or None."""
        if value is None or isinstance(value, Selector):
            return value
        if isinstance(value, str):
            return cls(text=value)
        if isinstance(value, dict):
            return cls(
                role=value.get("role"),
                name=value.get("name"),
                css=value.get("css"),
                text=value.get("text"),
            )
        raise TypeError(f"cannot coerce {value!r} to Selector")


@dataclass
class Action:
    """One step in a checkpoint.

    * ``navigate``         — ``value`` is the URL.
    * ``click``            — DOM/a11y click on ``selector``.
    * ``type``             — type ``value`` into the field at ``selector``.
    * ``extract``          — return the text of ``selector`` (untrusted data).
    * ``read``             — snapshot the current page.
    * ``screenshot``       — capture a screenshot artifact.
    * ``screenshot_click`` — pixel/visual click; a labelled fallback that the
      harness performs **only** when ``fallback=True``.
    * ``wait``             — passive wait (``value`` optional hint).
    """

    kind: ActionKind
    selector: Selector | None = None
    value: str | None = None
    fallback: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "selector": self.selector.to_dict() if self.selector else None,
            "value": self.value,
            "fallback": self.fallback,
        }


@dataclass
class ActionResult:
    """Outcome of executing a single :class:`Action`."""

    action: Action
    ok: bool
    output: str = ""
    page: "Page | None" = None
    error: str | None = None
    retries: int = 0
    fallback_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.to_dict(),
            "ok": self.ok,
            "output": self.output,
            "error": self.error,
            "retries": self.retries,
            "fallback_used": self.fallback_used,
            "url": self.page.url if self.page else None,
        }


@dataclass
class Page:
    """A programmatically-inspectable view of the current page.

    ``a11y_tree`` is a *summary* of the accessibility tree (a list of
    ``{role, name, ...}`` nodes). ``text`` is the visible text — always treated
    as UNTRUSTED data (never instructions).
    """

    url: str
    a11y_tree: list[dict[str, Any]] = field(default_factory=list)
    text: str = ""

    def a11y_summary(self, limit: int = 40) -> str:
        rows = []
        for el in self.a11y_tree[:limit]:
            role = el.get("role", "")
            name = el.get("name", "")
            rows.append(f"- {role}: {name}".rstrip(": "))
        return "\n".join(rows)


@dataclass
class Checkpoint:
    """A decomposed task step with an INDEPENDENT, programmatic verification.

    ``verify`` is a predicate dict ``{"kind": ..., "arg": ...}`` evaluated against
    the live page after the checkpoint's actions run:

    * ``{"kind": "url_contains", "arg": "/dashboard"}``
    * ``{"kind": "text_present", "arg": "Welcome"}``
    * ``{"kind": "dom_has", "arg": {"role": "button", "name": "Sign out"}}``

    The harness NEVER asks a model whether the step succeeded — it evaluates this
    predicate directly.
    """

    name: str
    actions: list[Action]
    verify: dict[str, Any] | None = None


# --------------------------------------------------------------------------- #
# Element matching (shared by FakeBrowser and the policy classifier)
# --------------------------------------------------------------------------- #


def _eq(a: Any, b: Any) -> bool:
    return str(a).strip().lower() == str(b).strip().lower()


def match_element(selector: Selector, el: dict[str, Any]) -> bool:
    """True if accessibility/field node ``el`` satisfies ``selector``.

    Matching prefers exact role+name, then css/selector, then a name/text
    substring. Used by the deterministic :class:`FakeBrowser` and by the policy
    classifier to find the field a ``type`` action targets.
    """
    if selector.css:
        if _eq(el.get("css"), selector.css) or _eq(el.get("selector"), selector.css):
            return True
    if selector.role and selector.name:
        if _eq(el.get("role"), selector.role) and _eq(el.get("name"), selector.name):
            return True
    if selector.name and _eq(el.get("name"), selector.name):
        return True
    if selector.text:
        hay = f"{el.get('name', '')} {el.get('text', '')}".lower()
        if selector.text.lower() in hay:
            return True
    return False


# --------------------------------------------------------------------------- #
# Browser protocol
# --------------------------------------------------------------------------- #


@runtime_checkable
class Browser(Protocol):
    """The minimal, DOM/a11y-first surface the harness drives.

    Implementations: :class:`~iron_jarvis.computeruse.browser.FakeBrowser`
    (deterministic, offline) and
    :class:`~iron_jarvis.computeruse.browser.PlaywrightBrowser` (real, isolated
    incognito context).
    """

    async def navigate(self, url: str) -> Page: ...

    async def click(self, selector: Selector, *, fallback: bool = False) -> Page: ...

    async def type(self, selector: Selector, value: str) -> Page: ...

    async def extract(self, selector: Selector) -> str: ...

    async def read(self) -> Page: ...

    async def screenshot(self) -> bytes: ...

    async def aclose(self) -> None: ...


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


class ComputerUseError(Exception):
    """Base for all Computer-Use errors."""


class ComputerUseDisabled(ComputerUseError):
    """Raised/returned when the subsystem is used while disabled (opt-in, default off)."""


class PolicyDenied(ComputerUseError):
    """An action is denied by the domain/action allowlist."""


class ApprovalRequired(ComputerUseError):
    """A sensitive action needs explicit human approval before it may run."""


class InjectionDetected(ComputerUseError):
    """Suspected prompt injection / phishing in untrusted page/email/PDF text."""


class BudgetExceeded(ComputerUseError):
    """The run exceeded its step budget."""


class UnknownSelector(ComputerUseError):
    """No element matched the selector (recoverable: retry/fallback/re-read)."""
