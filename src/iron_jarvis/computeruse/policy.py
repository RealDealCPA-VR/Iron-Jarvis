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

#: Destructive / transactional verbs that make a click/type need approval.
_DESTRUCTIVE_WORDS = (
    "delete",
    "remove",
    "destroy",
    "buy",
    "purchase",
    "order",
    "checkout",
    "pay",
    "payment",
    "send",
    "transfer",
    "wire",
    "confirm",
    "submit order",
    "place order",
    "deactivate",
    "close account",
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

        # 2) Destructive / transactional click (or typing such a command).
        if kind in ("click", "screenshot_click", "type"):
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
