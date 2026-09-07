"""Computer-Use policy — the §17-style allowlist + sensitivity gate.

Mirrors the sandbox ``SandboxPolicy`` pattern: a frozen-ish dataclass parsed from
config, plus pure decision functions the harness consults before every action.

Two layers:

* **Allowlists** (hard gate): ``domain_allowlist`` + ``action_allowlist``. An
  action off either list is *denied* outright.
* **Sensitivity classification** (consent gate): typing credentials / payment /
  PII, or destructive/transactional clicks (delete/buy/pay/send/transfer/confirm)
  require explicit human approval even when allowlisted.

The subsystem is OPT-IN: ``enabled`` defaults to ``False``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from ..tools.base import RiskClass
from .base import READ_ONLY_KINDS, Action, Page, Selector, match_element

# --------------------------------------------------------------------------- #
# Keyword vocabularies for sensitivity classification
# --------------------------------------------------------------------------- #

_PASSWORD_FIELD_TYPES = {"password", "passwd", "pwd"}
_PAYMENT_FIELD_TYPES = {"creditcard", "credit-card", "cc", "card", "tel-cc"}

#: HTML ``autocomplete`` tokens that mark a control as credential / payment.
_PASSWORD_AUTOCOMPLETE = {"current-password", "new-password"}
_PAYMENT_AUTOCOMPLETE = {
    "cc-number",
    "cc-csc",
    "cc-exp",
    "cc-exp-month",
    "cc-exp-year",
}

_PASSWORD_WORDS = ("password", "passphrase", "passwd", "pwd")
_PAYMENT_WORDS = (
    "credit card",
    "card number",
    "cardnumber",
    "card-number",
    "cvv",
    "cvc",
    "security code",
    "payment",
    "iban",
)
_PII_WORDS = (
    "ssn",
    "social security",
    "passport",
    "date of birth",
    "dob",
    "bank account",
    "routing number",
    "driver's license",
    "tax id",
    "national id",
)

#: Destructive / transactional verbs that make a click/type/navigate need approval.
#:
#: ONE list, shared by ``classify`` (computer use) and :func:`escalate_browser`
#: (the browser), which is D12's rule: a "Delete account" button that escalated
#: in one subsystem and not the other would be a hole nobody could see, because
#: each half would look right alone.
#:
#: Matching is a case-folded SUBSTRING test, so every entry errs toward asking
#: ("order" matches "reorder"). That direction is deliberate — a false card costs
#: one click, a missed one costs an action in the user's account — but it is also
#: why this list is not simply everything a reviewer can think of: an entry that
#: fires on ordinary prose puts an identical card in front of ordinary work, and
#: a user who clicks through an identical card every time is a user who has
#: stopped reading the cards that matter.
#:
#: Deliberately NOT here, having been considered: "approve", "accept", "publish",
#: "share", "archive". Each is common in ordinary page furniture and in typed
#: text, and none of them destroys data, moves money or gives away access. They
#: are the entries whose card volume would buy the least.
_DESTRUCTIVE_WORDS = (
    # destroy data
    "delete",
    "remove",
    "destroy",
    "erase",
    "wipe",
    "trash",
    "terminate",
    # money
    "buy",
    "purchase",
    "order",
    "checkout",
    "pay",
    "payment",
    "send",
    "transfer",
    "wire",
    "withdraw",
    "sell",
    "trade",
    "confirm",
    "submit order",
    "place order",
    # the account itself, and the access to it
    "deactivate",
    "close account",
    "revoke",
    "grant access",
    "reset password",
    "change email",
    "disable two-factor",
    "disable 2fa",
    "cancel subscription",
    "unsubscribe",
    "sign out",
    "log out",
    "log off",
)


@dataclass
class Decision:
    """Result of :meth:`ComputerUsePolicy.check`."""

    allowed: bool
    requires_approval: bool
    reason: str


@dataclass
class ComputerUsePolicy:
    """Allowlists + budgets for the Computer-Use subsystem (opt-in, default off)."""

    enabled: bool = False
    domain_allowlist: list[str] = field(default_factory=list)
    action_allowlist: list[str] = field(default_factory=lambda: list(READ_ONLY_KINDS))
    isolation: str = "isolated"
    max_steps: int = 20
    max_retries: int = 2

    # -- construction -------------------------------------------------------
    @classmethod
    def from_config(cls, data: dict[str, Any] | None) -> "ComputerUsePolicy":
        """Build a policy from a ``Config.computer_use`` dict."""
        d = dict(data or {})
        return cls(
            enabled=bool(d.get("enabled", False)),
            domain_allowlist=list(d.get("domain_allowlist", [])),
            action_allowlist=list(d.get("action_allowlist", list(READ_ONLY_KINDS))),
            isolation=str(d.get("isolation", "isolated")),
            max_steps=int(d.get("max_steps", 20)),
            max_retries=int(d.get("max_retries", 2)),
        )

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _host(url: str | None) -> str:
        if not url:
            return ""
        parsed = urlparse(url if "://" in url else f"http://{url}")
        return (parsed.hostname or "").lower()

    def domain_allowed(self, url: str | None) -> bool:
        host = self._host(url)
        if not host:
            return False
        for entry in self.domain_allowlist:
            e = entry.lower().lstrip(".")
            if host == e or host.endswith("." + e):
                return True
        return False

    @staticmethod
    def _normalize_kind(kind: str) -> str:
        # Screenshot clicking is a click variant for allowlist purposes.
        return "click" if kind == "screenshot_click" else kind

    def action_kind_allowed(self, kind: str) -> bool:
        return kind in self.action_allowlist or self._normalize_kind(kind) in self.action_allowlist

    @staticmethod
    def _field_for(action: Action, page: Page | None) -> dict[str, Any] | None:
        if page is None or action.selector is None:
            return None
        for el in page.a11y_tree:
            if match_element(action.selector, el):
                return el
        return None

    # -- classification -----------------------------------------------------
    def classify(self, action: Action, page: Page | None) -> dict[str, Any]:
        """Flag credentials / payment / PII typing and destructive actions.

        Returns ``{"sensitive": bool, "reason": str}``. Pure (no I/O); the harness
        turns a sensitive verdict into a human-approval gate.
        """
        kind = action.kind
        sel = action.selector or Selector()
        # The agent-supplied css/value is part of the haystack so credential-y
        # words in a css selector can't slip past the keyword scan.
        hay_parts = [sel.name or "", sel.text or "", sel.css or "", action.value or ""]
        field = self._field_for(action, page)
        autocomplete = ""
        if field:
            autocomplete = str(field.get("autocomplete", "")).lower()
            hay_parts.extend(
                [
                    str(field.get("name", "")),
                    str(field.get("type", "")),
                    str(field.get("autocomplete", "")),
                ]
            )
        hay = " ".join(hay_parts).lower()
        field_type = str(field.get("type", "")).lower() if field else ""

        # 1) Typing into a credential / payment / PII field. The real
        # ``PlaywrightBrowser`` snapshot now carries the control's DOM ``type``
        # and ``autocomplete``, so these branches fire even when the agent
        # selected the field via a css selector with no credential keywords.
        if kind == "type":
            if (
                field_type in _PASSWORD_FIELD_TYPES
                or autocomplete in _PASSWORD_AUTOCOMPLETE
                or any(w in hay for w in _PASSWORD_WORDS)
            ):
                return {"sensitive": True, "reason": "typing into a password field"}
            if (
                field_type in _PAYMENT_FIELD_TYPES
                or autocomplete in _PAYMENT_AUTOCOMPLETE
                or any(w in hay for w in _PAYMENT_WORDS)
            ):
                return {"sensitive": True, "reason": "typing payment/credit-card data"}
            if any(w in hay for w in _PII_WORDS):
                return {"sensitive": True, "reason": "typing personal/PII data"}

        # 2) Destructive / transactional click (or typing such a command, or
        # navigating to such a destination).
        #
        # ``navigate`` is in this list because a URL is a target with words on it:
        # https://bank.test/transfer and https://shop.test/checkout commit the
        # same things the buttons of those names do, and ``action.value`` (the
        # URL) is already in the haystack. Arm 3 below only asks whether the HOST
        # is allowed, which answers nothing about what the path does — and on the
        # browser's default install there is no allowlist at all, so without this
        # line a transactional destination reached the user's real logged-in
        # browser with no card.
        if kind in ("click", "screenshot_click", "type", "navigate"):
            for word in _DESTRUCTIVE_WORDS:
                if word in hay:
                    return {
                        "sensitive": True,
                        "reason": f"destructive/transactional action ({word!r})",
                    }

        # 3) Navigating off the domain allowlist (also denied by ``check``).
        if kind == "navigate" and not self.domain_allowed(action.value):
            return {"sensitive": True, "reason": "navigating off the domain allowlist"}

        return {"sensitive": False, "reason": ""}

    # -- the gate -----------------------------------------------------------
    def check(self, action: Action, page: Page | None) -> Decision:
        """Allow / deny / require-approval for a single action.

        * Deny if the target domain (navigate) or the action kind is not allowed.
        * Require approval if :meth:`classify` flags the action sensitive.
        * Otherwise allow.
        """
        if action.kind == "navigate" and not self.domain_allowed(action.value):
            return Decision(
                allowed=False,
                requires_approval=False,
                reason=f"domain not on allowlist: {self._host(action.value)!r}",
            )

        if not self.action_kind_allowed(action.kind):
            return Decision(
                allowed=False,
                requires_approval=False,
                reason=f"action kind not on allowlist: {action.kind!r}",
            )

        verdict = self.classify(action, page)
        if verdict["sensitive"]:
            return Decision(
                allowed=True,
                requires_approval=True,
                reason=verdict["reason"],
            )

        return Decision(allowed=True, requires_approval=False, reason="allowed by policy")


# --------------------------------------------------------------------------- #
# Browser escalation (D12, plan §8.2) — Ship 3
# --------------------------------------------------------------------------- #
#
# This lives BESIDE ``classify`` rather than in the browser package because the
# keyword vocabularies it needs already live here. D12's own words are the rule:
# "reuse existing computer-use classification semantics rather than creating a
# competing browser-specific policy engine." Two vocabularies that can disagree
# is the defect this placement avoids — a "Delete account" button that escalates
# in computer use and does not escalate in the browser would be a hole nobody
# could see, because both halves would look correct in isolation.


def escalate_browser(
    base: RiskClass,
    action: Action,
    page: Page | None,
    *,
    target_label: str = "",
    policy: "ComputerUsePolicy | None" = None,
) -> Decision:
    """Map a browser tool's base risk plus its target to a Decision.

    Reuses :meth:`ComputerUsePolicy.classify` for the credentials/payment/PII/
    destructive wording, then folds in the click or key target's own accessible
    name, which ``classify`` cannot see because a click carries no typed value.
    Returns the SAME three-field :class:`Decision` every computer-use caller
    already handles, so a browser ask renders as the approval card the user
    already knows and no second approval UI exists to drift from the first.

    Rules, in this order (plan §8.2's table, verbatim):

    1. ``base`` is READ or LOCAL_UI — allowed, no approval. Reading a page and
       scrolling the user's own view commit nothing.
    2. ``classify`` reports sensitive — approval, carrying *its* reason.
    3. ``target_label`` matches :data:`_DESTRUCTIVE_WORDS` — approval.
    4. ``target_label`` matches :data:`_PAYMENT_WORDS` — approval.
    4b. ``target_label`` matches :data:`_PASSWORD_WORDS` or :data:`_PII_WORDS` —
       approval. Numbered out of the plan's table because the plan's table left
       them out: ``classify`` runs those two vocabularies only over a ``type``
       action's own haystack, which never contains the browser's label, so a
       control named "Password" or "SSN" escalated on nothing.
    5. ``base`` is PAGE_ACTION — allowed, no approval.
    6. anything else (EXTERNAL_COMMIT, or a class this function does not know) —
       approval. Not in the plan's table because no browser tool declares
       EXTERNAL_COMMIT; here because :attr:`Tool.risk_class` DEFAULTS to it, so a
       future acting tool that forgets to declare must ask rather than act. The
       silent failure that catches: a fifteenth tool added with no ``risk_class``
       line, reaching the user's logged-in bank with no card shown.

    ``target_label`` is the snapshot element's **accessible name** — the visible
    words on the button, as the user reads them. That is the concrete reason the
    plan prefers ``element_id`` targets over CSS selectors: a selector like
    ``"#btn-7"`` gives this classifier nothing to read, so a "Delete account"
    button addressed that way would arrive here labelled with nothing and pass
    rule 3. Do not "simplify" the label away, and do not replace it with the
    selector text: that edit silently disarms every escalation below, and every
    test above rule 2 would still pass because ``classify`` catches the loud
    cases on its own.

    Args:
        base: the tool's declared :class:`~iron_jarvis.tools.base.RiskClass`.
        action: the classifier's view of what is about to happen. Built by
            :mod:`iron_jarvis.browser.risk`, which is the ONE caller — a tool
            that built its own would be a second policy call site, and one of
            them would eventually skip a rule.
        page: a one-element :class:`Page` carrying the resolved target element,
            or ``None`` when the target could not be resolved to a snapshot row.
            ``classify`` reads ``autocomplete``/``type`` off it, which is how
            typing into a password field escalates even when the field's visible
            name says nothing about passwords.
        target_label: the target's accessible name (see above).
        policy: the live :class:`ComputerUsePolicy`, so the domain allowlist the
            user already configured is the one consulted. Optional, and a fresh
            default instance is used when absent — never a module-level shared
            one, because a caller mutating it would make this function's answer
            depend on call order, and "pure" would stop being true.

    Returns:
        Decision: ``allowed`` is always ``True``. This function escalates; it
        never denies. Denial belongs to the permission engine and the deny floor
        (``DENY_FLOOR_TOOLS``), which run before a tool's ``execute`` — an
        ``allowed=False`` here would be a second denial path whose refusal text
        no permissions screen could explain.
    """
    if base in (RiskClass.READ, RiskClass.LOCAL_UI):
        return Decision(allowed=True, requires_approval=False, reason="allowed by policy")

    resolved = policy if policy is not None else ComputerUsePolicy()
    verdict = resolved.classify(action, page)
    if verdict.get("sensitive"):
        return Decision(
            allowed=True,
            requires_approval=True,
            reason=str(verdict.get("reason") or "sensitive action"),
        )

    label = str(target_label or "").strip().lower()
    if label:
        for word in _DESTRUCTIVE_WORDS:
            if word in label:
                return Decision(
                    allowed=True,
                    requires_approval=True,
                    reason=f"destructive/transactional target ({word!r})",
                )
        for word in _PAYMENT_WORDS:
            if word in label:
                return Decision(
                    allowed=True,
                    requires_approval=True,
                    reason="payment/transactional target",
                )
        # The credential and PII vocabularies, scanned against the SAME label.
        #
        # They were missing, and the gap was invisible: ``classify`` runs them
        # only for a ``type`` action and only over the selector/value/field
        # haystack, which the browser deliberately leaves nameless (the label
        # travels separately, so THESE rules are the ones that read it). The
        # result was that a control labelled literally "Password" or "SSN"
        # escalated on nothing at all unless the page also reported a
        # ``type="password"`` row — which a css- or role-addressed target never
        # resolves. A field the user reads as "Password" must ask whatever the
        # markup underneath it says.
        for word in _PASSWORD_WORDS:
            if word in label:
                return Decision(
                    allowed=True,
                    requires_approval=True,
                    reason=f"credential target ({word!r})",
                )
        for word in _PII_WORDS:
            if word in label:
                return Decision(
                    allowed=True,
                    requires_approval=True,
                    reason=f"personal/PII target ({word!r})",
                )

    if base is RiskClass.PAGE_ACTION:
        return Decision(allowed=True, requires_approval=False, reason="allowed by policy")

    # Rule 6. Fail-safe: an undeclared or unknown class asks.
    return Decision(
        allowed=True,
        requires_approval=True,
        reason=f"undeclared browser risk class ({getattr(base, 'value', base)!r})",
    )
