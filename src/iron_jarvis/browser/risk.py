"""The ONE risk decision every acting browser tool passes through (D12, plan §8.5).

Ship 3 is the ship where Jarvis can CHANGE a page, and every tool it adds
operates on the user's real, logged-in browser: their bank, their email, their
client portal. A wrong click is not a failed test, it is an action taken in
someone's account. This module is the single place that decides whether such a
call goes straight through or stops at an approval card first.

**One call site, deliberately.** :func:`browser_risk_decision` is the only
function the acting tools call, and it is the only caller of
:func:`~iron_jarvis.computeruse.policy.escalate_browser`. A tool that built its
own ``Action`` and called the policy itself would be a second call site, and a
second call site is a rule that one of them will eventually skip — silently,
because a skipped escalation looks exactly like a call that was not sensitive.
The reviewer question for this ship is "can any of the four deny-floor tools run
without passing through ``escalate_browser``?", and the answer is only checkable
if there is one door.

**No second policy engine.** D12 is explicit: reuse the computer-use
classification semantics rather than creating a competing browser-specific one.
So the credentials/payment/PII/destructive vocabularies stay where they are, in
``computeruse/policy.py``, this module never copies them, and the browser's own
additions (the base-risk table, the Q03 justification seam) are the only new
rules here. Two vocabularies that can disagree is the defect being avoided: a
"Delete account" button that escalates in computer use and not in the browser
would be a hole nobody could see, because each half would look right alone.

**What this module does NOT do.** It never denies. Denial belongs to the
permission engine and to ``DENY_FLOOR_TOOLS`` (``tools/permissions.py``), which
run before ``execute`` is ever reached; the four PAGE_ACTION tools sit on that
floor, so an agent definition cannot arm them and a capability proposal naming
one is refused. This module answers the narrower question the permission engine
cannot: *given what this particular call is about to touch*, does the user have
to see it first. The two gates are in series, and neither substitutes for the
other.

Vocabulary (§7.1, mandatory): no user- or model-facing string here may call the
Chrome add-on an "extension" — ``VOCABULARY.md`` assigns that word to an MCP
server.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..computeruse.base import Action, Page, Selector
from ..computeruse.policy import ComputerUsePolicy, Decision, escalate_browser
from ..tools.base import RiskClass
from . import protocol as P

# --------------------------------------------------------------------------- #
# The base-risk table (plan §8.5)
# --------------------------------------------------------------------------- #

#: Every one of the fourteen browser tools and the risk class it declares.
#:
#: The table is here and not only on the tool classes because two readers need
#: it and only one of them holds a tool instance: the tools declare
#: ``risk_class`` for the registry (which puts it on the ledger row and the
#: ``tool.executed`` payload), and this module needs the same fact for a tool
#: NAME, which is all a risk decision is given. :func:`base_risk` is what keeps
#: the two honest — a tool whose class disagrees with this table is caught by
#: this ship's own pin rather than by a user discovering that a page-acting tool
#: was classified as a read.
#:
#: The four PAGE_ACTION names are the deny-floor four (D11), and no browser tool
#: declares EXTERNAL_COMMIT: a browser click that commits money escalates
#: *per call* on its target's words, which is what
#: :func:`~iron_jarvis.computeruse.policy.escalate_browser` is for. Declaring a
#: tool EXTERNAL_COMMIT outright would make every click on every page ask.
BASE_RISK: dict[str, RiskClass] = {
    # READ — observe only.
    "browser_get_status": RiskClass.READ,
    "browser_list_tabs": RiskClass.READ,
    "browser_get_active_tab": RiskClass.READ,
    "browser_read_page": RiskClass.READ,
    "browser_get_elements": RiskClass.READ,
    "browser_screenshot": RiskClass.READ,
    # LOCAL_UI — move the user's own view; no page state changes.
    "browser_activate_tab": RiskClass.LOCAL_UI,
    "browser_scroll": RiskClass.LOCAL_UI,
    "browser_create_tab": RiskClass.LOCAL_UI,
    "browser_close_tab": RiskClass.LOCAL_UI,
    # PAGE_ACTION — change a page's state. The deny-floor four.
    "browser_click": RiskClass.PAGE_ACTION,
    "browser_type": RiskClass.PAGE_ACTION,
    "browser_press_key": RiskClass.PAGE_ACTION,
    "browser_navigate": RiskClass.PAGE_ACTION,
}

#: The tools that CHANGE a page — the ones Q03's action justification constrains.
#:
#: Derived from :data:`BASE_RISK` rather than typed a second time, so a tool
#: promoted to PAGE_ACTION cannot be left out of the justification seam by a
#: reader who updated one list and not the other.
STATE_CHANGING_TOOLS: frozenset[str] = frozenset(
    name for name, risk in BASE_RISK.items() if risk is RiskClass.PAGE_ACTION
)

#: Tool name -> the wire method it sends. Used to shape the classifier's view of
#: the call (a click is not a type), and nothing else.
_TOOL_METHOD: dict[str, str] = {
    "browser_activate_tab": P.METHOD_ACTIVATE_TAB,
    "browser_scroll": P.METHOD_SCROLL,
    "browser_create_tab": P.METHOD_CREATE_TAB,
    "browser_close_tab": P.METHOD_CLOSE_TAB,
    "browser_click": P.METHOD_CLICK,
    "browser_type": P.METHOD_TYPE_TEXT,
    "browser_press_key": P.METHOD_PRESS_KEY,
    "browser_navigate": P.METHOD_NAVIGATE,
}

#: The css string used to tie the classifier's ``Selector`` to the one element in
#: its ``Page``. :meth:`ComputerUsePolicy._field_for` finds the target field by
#: running ``match_element`` over the a11y tree, and a snapshot element addressed
#: by ``element_id`` has no selector of its own to match on — so both sides are
#: given this sentinel and the match is exact.
#:
#: Chosen to contain none of the classifier's vocabulary words. It lands in
#: ``classify``'s haystack (the css of a selector is deliberately scanned there),
#: so a sentinel containing "pay" or "delete" would make EVERY targeted call
#: escalate, and the escalation would then mean nothing.
_TARGET_SENTINEL = "#ij-browser-target"

#: The reason a call is stopped because its TARGET could not be read.
#:
#: The escalation vocabulary can only judge words, so a call that arrives with no
#: words and no resolved element has not been judged at all. Every such call used
#: to be allowed, which made the whole destructive/payment/credential vocabulary
#: optional in practice: a "Delete account" button addressed as ``{"css":
#: "#btn-7"}`` resolves to a node and names nothing, and a bare
#: ``browser_press_key`` names nothing at all — while Enter on a focused "Delete
#: account" button is the same event as clicking it.
#:
#: It is also the one place this module inverted its own convention. ``base_risk``
#: fails safe to EXTERNAL_COMMIT on a name it has never heard of;
#: :func:`_named_in_request` fails closed on a missing needle; "we could not read
#: the target" resolved to *allow*. Now it asks, and the card says why, so the
#: user is answering the real question — *something on this page, and I cannot
#: tell you what* — rather than a card that never appeared.
#:
#: The remedy is cheap and it is the one §8.6 already prefers: address the target
#: by ``element_id`` from a snapshot, and the resolved accessible name is read.
UNCHECKED_TARGET_REASON = (
    "the target could not be checked: it has no accessible name"
)

#: The Q03 reason, verbatim from plan §9.5. Not reworded: the user reads it on
#: the approval card, and it is the only place they are told that the page they
#: are looking at tried to give Jarvis instructions.
INJECTION_JUSTIFICATION_REASON = (
    "page flagged for injection; this target was not in your request"
)


def base_risk(tool_name: str) -> RiskClass:
    """The declared :class:`RiskClass` for ``tool_name``.

    An unknown name is EXTERNAL_COMMIT, matching ``Tool.risk_class``'s own
    fail-safe default and for the same reason: a tool this table has never heard
    of must not be treated as harmless. That is not a theoretical case — a
    fifteenth browser tool added in a later ship reaches
    :func:`browser_risk_decision` by name before anyone remembers this dict, and
    the failure that default prevents is that tool acting on the user's bank with
    no card shown and nothing in the diff to notice.
    """
    return BASE_RISK.get(str(tool_name or ""), RiskClass.EXTERNAL_COMMIT)


def _classifier_action(
    tool_name: str,
    *,
    text: str,
    url: str,
    has_element: bool,
) -> Action:
    """The classifier's view of the call about to be made.

    Shaped by tool, because ``classify`` reads the action KIND: only ``type``
    runs the credential/payment/PII field arms, and only ``navigate`` consults
    the domain allowlist. Getting the kind wrong is not a cosmetic error — a
    ``type`` presented as a ``click`` would let a password reach a field with no
    card shown.

    The selector deliberately carries NO accessible name. The name travels
    separately as ``target_label`` so that
    :func:`~iron_jarvis.computeruse.policy.escalate_browser`'s own target rules
    are the thing that reads it; folding it in here would make those rules dead
    code that still looked live.
    """
    selector = Selector(css=_TARGET_SENTINEL) if has_element else Selector()
    method = _TOOL_METHOD.get(tool_name, "")
    if method == P.METHOD_TYPE_TEXT:
        return Action(kind="type", selector=selector, value=str(text or ""))
    if method == P.METHOD_NAVIGATE:
        return Action(kind="navigate", value=str(url or ""))
    # click and press_key. A key press is a click for classification purposes:
    # both commit whatever the focused control does, and Enter on a "Delete
    # account" button is the same event as clicking it.
    return Action(kind="click", selector=selector)


def _classifier_page(
    element: Mapping[str, Any] | None, page_url: str
) -> Page | None:
    """A one-element :class:`Page` carrying the resolved target, or ``None``.

    ``classify`` reads ``type`` and ``autocomplete`` off this to catch a password
    or card field whose visible name says nothing about either — the case D13B's
    scrubber marks ``sensitive`` and a human reader would miss. ``None`` when the
    target could not be resolved to a snapshot row, which is honest: the
    classifier then has only the words, and the target rules carry the decision.
    """
    if not element:
        return None
    row = dict(element)
    # The sentinel on both sides is what makes ``match_element`` find this row;
    # see _TARGET_SENTINEL. Set unconditionally, overriding any css the page
    # reported, because a page-supplied css is attacker-controlled text and a row
    # that failed to match would silently disarm the autocomplete arms.
    row["css"] = _TARGET_SENTINEL
    return Page(url=str(page_url or ""), a11y_tree=[row])


def _navigation_policy(
    policy: ComputerUsePolicy | None, url: str
) -> ComputerUsePolicy:
    """The policy ``classify`` should judge a NAVIGATE against.

    ``classify`` flags a navigation off ``domain_allowlist`` as sensitive. That
    is right when the user has configured an allowlist — it is the same list the
    computer-use page already shows, and D12 asks for one configuration rather
    than two. It is wrong when the list is EMPTY, which is the default: an empty
    allowlist in the computer-use subsystem means "nothing is allowed" because
    that subsystem is opt-in and drives a throwaway browser, whereas the Browser
    capability is gated by ``browser_access`` plus the deny floor and is meant to
    drive the user's own tabs. Reading the empty default as "every navigation is
    sensitive" would put an approval card in front of every single navigation,
    and a user clicking through an identical card every time is a user who has
    stopped reading it — which is how the cards that matter get approved too.

    So: an allowlist that EXISTS is enforced; an absent one is not invented. The
    substitute policy allowlists this URL's own host and changes nothing else, so
    every other arm of ``classify`` still runs against the real call.
    """
    if policy is not None and policy.domain_allowlist:
        return policy
    host = ComputerUsePolicy._host(url)
    return ComputerUsePolicy(domain_allowlist=[host] if host else [])


def _named_in_request(label: str, request_text: str | None) -> bool:
    """Whether the user's own request names ``label`` (Q03 point 5).

    Fails CLOSED in both directions the plan cares about: no request text (a
    scheduled run, a subagent, a turn whose text never reached the runtime) and
    no label (an icon button the page gave no accessible name) both answer
    ``False``, so the call requires approval. The alternative — treating "we
    could not check" as "it checked out" — is the failure this whole seam exists
    to prevent, and it would be invisible, because a missing request text looks
    exactly like a request that happened to match.

    Matching is a case-folded, whitespace-normalised substring test. Deliberately
    literal: the user asked for a button by the words on it, and any looser rule
    (stemming, fuzzy distance) is a rule that can be steered by a page choosing
    its button labels — which is precisely the attacker this check faces.
    """
    needle = " ".join(str(label or "").split()).strip().lower()
    haystack = " ".join(str(request_text or "").split()).strip().lower()
    if not needle or not haystack:
        return False
    return needle in haystack


def browser_risk_decision(
    tool_name: str,
    *,
    target_label: str = "",
    target_element: Mapping[str, Any] | None = None,
    text: str = "",
    url: str = "",
    page_url: str = "",
    page_flagged: bool = False,
    request_text: str | None = None,
    policy: ComputerUsePolicy | None = None,
) -> Decision:
    """The risk verdict for one browser tool call. The only door.

    Every acting tool calls this exactly once, before it sends a frame, and
    routes a ``requires_approval`` verdict to the existing
    :class:`~iron_jarvis.computeruse.approvals.ApprovalQueue` — the same
    consume-on-use queue computer use uses, so a browser ask renders as the
    approval card the user already knows and no second approval UI exists.

    Args:
        tool_name: the tool's ``name`` (``"browser_click"``, …). Its base class
            comes from :data:`BASE_RISK`; an unknown name fails safe to
            EXTERNAL_COMMIT and therefore asks.
        target_label: the resolved element's **accessible name** — the words the
            user would read on the control. This is the field the escalation
            vocabulary is scanned against, and it is the concrete reason §8.6
            prefers ``element_id`` targets: a CSS selector resolves to a node but
            gives the classifier nothing to read, so a "Delete account" button
            addressed as ``{"css": "#btn-7"}`` arrives here labelled with nothing.
            Pass the page's own accessible name, never the selector text. When
            the element could not be resolved, pass whatever the model named
            (``P.target_label(target)``) so something is scanned rather than
            nothing.
        target_element: the snapshot's element row for the target, when one was
            resolved. Its ``type``/``autocomplete`` are what catch a password or
            card field whose visible name says neither.
        text: for ``browser_type``, the text about to be typed. Scanned by
            ``classify`` and NEVER stored by this module — it is not returned, not
            logged, and not put in the reason string.
        url: for ``browser_navigate``, the destination.
        page_url: the URL of the page being acted on, for the classifier's
            ``Page``. Context only.
        page_flagged: whether the last read of THIS tab tripped the injection
            detector (Q03). :func:`tab_is_flagged` computes it from the snapshot
            cache.
        request_text: the user's own request text for this turn, reaching the
            tool through ``BrowserRuntime`` (see :func:`request_text_of`).
            ``None`` or empty fails closed to requiring approval on a flagged tab.
        policy: the live :class:`ComputerUsePolicy`, so the domain allowlist and
            the sensitivity vocabulary are one configuration and not two.

    Returns:
        Decision: ``allowed`` is always ``True``; this function escalates, it
        never denies. See the module docstring — denial is the permission
        engine's job, and a second denial path would produce refusals no
        permissions screen could explain.
    """
    base = base_risk(tool_name)

    decision = escalate_browser(
        base,
        _classifier_action(
            tool_name,
            text=text,
            url=url,
            has_element=bool(target_element),
        ),
        _classifier_page(target_element, page_url),
        target_label=str(target_label or ""),
        policy=(
            _navigation_policy(policy, url)
            if _TOOL_METHOD.get(tool_name) == P.METHOD_NAVIGATE
            else policy
        ),
    )

    # --- overrides, which may only RAISE ---------------------------------
    #
    # Both run after the policy call and neither can lower its verdict. An
    # override that could lower would be a way to reach the page WITHOUT the
    # escalation vocabulary, which is the one thing this module must not offer.
    if decision.requires_approval:
        return decision

    # 0) THE TARGET COULD NOT BE READ (see UNCHECKED_TARGET_REASON).
    #
    # A PAGE_ACTION call that carries neither an accessible name nor a resolved
    # snapshot row gave the vocabularies nothing to scan, so "allowed by policy"
    # above means only "we found no words", not "we looked and it was safe".
    # Fail CLOSED, matching ``base_risk``'s own fail-safe.
    #
    # NAVIGATE is the one PAGE_ACTION exempted, and only when it names a URL: it
    # never carries a label or an element by construction, and its target is the
    # destination — which IS read, by ``classify``'s destructive scan over
    # ``action.value`` and by the domain allowlist. A navigation with no URL is
    # not exempt (it is refused earlier by ``navigate_params``, and if that
    # refusal ever moved, this would be the wrong place to assume it).
    if (
        base is RiskClass.PAGE_ACTION
        and not str(target_label or "").strip()
        and not target_element
        and not (
            _TOOL_METHOD.get(tool_name) == P.METHOD_NAVIGATE
            and str(url or "").strip()
        )
    ):
        return Decision(
            allowed=True,
            requires_approval=True,
            reason=UNCHECKED_TARGET_REASON,
        )

    # 1) The page's own sensitivity mark (D13B). The content script sets
    # ``sensitive`` at capture time on a password or payment control, which is
    # strictly more than ``classify`` can see: the scrubber also nulls the value,
    # so by the time the row reaches here the evidence ``classify`` would have
    # used may be gone. Typing into a field the page itself called sensitive asks,
    # always.
    if (
        base is RiskClass.PAGE_ACTION
        and target_element
        and bool(dict(target_element).get("sensitive"))
    ):
        return Decision(
            allowed=True,
            requires_approval=True,
            reason="the page marks this field as sensitive",
        )

    # 2) Q03's ACTION JUSTIFICATION. After a flagged read on a tab, the next
    # state-changing call on that tab requires approval unless the target's name
    # appears in the user's OWN request. That is point 5 of Q03 — "deny actions
    # that appear to originate from page instructions rather than the user's
    # requested objective" — met by reusing the approval gate the user already
    # sees rather than by inventing a policy engine to decide intent.
    #
    # Scoped to PAGE_ACTION, the calls that CHANGE the page. A LOCAL_UI call
    # (scroll, activate a tab) commits nothing a hostile page could profit from,
    # and gating those would put a card in front of ordinary reading on any page
    # that happened to trip the detector — the noise that teaches a user to
    # approve without reading.
    if page_flagged and tool_name in STATE_CHANGING_TOOLS:
        # For a navigation the "target" is the destination, which is what the
        # user would have named.
        needle = str(target_label or "") or str(url or "")
        if not _named_in_request(needle, request_text):
            return Decision(
                allowed=True,
                requires_approval=True,
                reason=INJECTION_JUSTIFICATION_REASON,
            )

    return decision


def risk_row(tool_name: str, decision: Decision, *, tool: str = "") -> dict[str, Any]:
    """The ``risk`` object every acting tool puts in its result ``data`` (D24/§10.4).

    ``{"base", "decision", "reason", "tool"}``. It lands in the ledger's
    ``output`` column through ``ToolInvocation``, which is what makes D24's "risk
    result" reconstructible from a row months later — the ``tool.executed``
    payload carries the class, and this carries what was DONE about it.

    ``decision`` is the word a reader wants: ``"approved"`` means a card was
    shown, ``"allowed"`` means none was needed. Never the raw boolean, because
    ``requires_approval: false`` on a row reads as "approval was refused" to
    someone scanning an audit view at speed.

    TWO names, because they can legitimately differ. ``tool_name`` is the tool
    whose RULES applied, and it is what ``base`` is derived from — a tool may be
    judged by another's rules (``_ActingTool.risk_name``: ``browser_create_tab``
    carrying a URL is judged as a navigation, so the allowlist and the
    destructive vocabulary reach it). ``tool`` is the tool that actually RAN, and
    it defaults to ``tool_name`` so an ordinary call site says one name once. An
    auditor reading "browser_navigate" on a row for a tab that was OPENED would
    go looking for a page that was replaced and find one that was not, so the row
    carries both facts rather than one of them under an ambiguous key.
    """
    return {
        "base": base_risk(tool_name).value,
        "decision": "approved" if decision.requires_approval else "allowed",
        "reason": decision.reason,
        "tool": str(tool or tool_name or ""),
    }


def tab_is_flagged(snapshots: Any, tab_id: Any) -> bool:
    """Whether this tab's most recent snapshot tripped the injection detector.

    Reads the ``security`` note off the cached
    :class:`~iron_jarvis.browser.snapshot.PageSnapshot`, which is where the ONE
    detector (``computeruse/safety.detect_injection``) already put it during the
    read. No second scan and no second scanner — Q03 forbids both, and a second
    scan would be a second answer that could disagree with the warning the model
    was actually shown.

    Fails SAFE in the un-flagged direction on purpose. A tab with no cached
    snapshot answers ``False``, not ``True``, because an acting call against a
    tab with no snapshot is already refused ``STALE_SNAPSHOT`` upstream — making
    it ask instead would replace a precise remedy the model can act on with a
    card the user cannot answer usefully. The cache is invalidated on navigation,
    so "no snapshot" genuinely means "nobody has read this page", not "the flag
    was lost".
    """
    if snapshots is None or tab_id is None:
        return False
    try:
        snapshot = snapshots.get(tab_id)
    except Exception:  # noqa: BLE001 — a cache miss must never break an action
        return False
    if snapshot is None:
        return False
    security = getattr(snapshot, "security", None)
    return bool(security and dict(security).get("warning"))


def request_text_of(runtime: Any) -> str:
    """The user's own request text for this turn, or ``""``.

    Plan §9.5's seam: the text reaches a tool through a new optional field on
    ``BrowserRuntime``, set per turn by whichever lane is driving. Read through
    one helper rather than a ``getattr`` at each call site so there is one
    spelling of the attribute — a second spelling would read as empty forever and
    the justification check would fail closed on every flagged page, which looks
    like working security and is actually a check that never runs.

    Never raises, and never returns anything but a string: this is consulted on
    the acting path, where an exception would become a traceback in front of a
    model.
    """
    if runtime is None:
        return ""
    try:
        value = getattr(runtime, "request_text", "")
    except Exception:  # noqa: BLE001
        return ""
    return str(value or "")


__all__ = [
    "BASE_RISK",
    "INJECTION_JUSTIFICATION_REASON",
    "UNCHECKED_TARGET_REASON",
    "STATE_CHANGING_TOOLS",
    "base_risk",
    "browser_risk_decision",
    "request_text_of",
    "risk_row",
    "tab_is_flagged",
]
