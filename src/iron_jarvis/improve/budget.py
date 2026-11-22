"""THE NIGHT BUDGET (17-a): may a background model request be made NOW?

:func:`may_spend` is the ONE answer, for every suggestion maker the night
review runs (ideas 19, 20 and 21 ask it too). ``""`` means yes; anything
else is the reason, one plain sentence fragment that reads after
"Skipped: " on Improvements. It refuses, in this order:

* Emergency stop (nothing Iron Jarvis starts by itself runs);
* no model on record for the work (nothing to pin to);
* the demo model (it writes nothing anyone should keep), and Auto (the
  router chose per request; a pinned call needs one named model);
* Local only work with a model that leaves this network
  (``egress.stays_on_this_network``; the request is also MADE inside
  ``egress.local_only_scope(True)``, so the router's own gate refuses too);
* a model that bills per use, or may (``providers/billing.billing_kind``
  with ``unsure=COST_PAY``, the recap rule: a reading that fails is pay per
  use, never a guess that spends money);
* an account at its limit, or at :data:`~iron_jarvis.harness.library.
  ACCOUNT_BUSY_PERCENT` (70%) or more of a window
  (``harness/library.account_refusal``, lifted unchanged). With no account
  named, a subscription tool's Default account is the one asked: that is the
  login the call would use. Iron-Proxy's CACHED word only, never a network
  call; an account it has not listed is not refused here;
* the night's cap (``config.night_review_requests``, 1 to 20, default 6):
  :func:`count` is called right before each request is sent, and the tally
  is kept in ``<home>/night-review.json`` (a restart does not reset it).

:func:`needs_you` tells a refusal only the owner can change (a model that
bills per use, the demo model, Auto, Local only work on a model off this
network)
from one that passes by itself (an account that is busy tonight, the cap).

Pure reads and one small file. BLOCKING (the file); callers on the event
loop hop with ``asyncio.to_thread``. Never raises.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: The night's tally, in the home folder.
MARKER = "night-review.json"
#: How many requests a night by default (D-17.1), and the bounds.
DEFAULT_CAP = 6
CAP_MIN, CAP_MAX = 1, 20
#: Nights the tally file keeps (the newest).
KEEP_NIGHTS = 14

WHY_STOPPED = "Emergency stop is on"
WHY_NO_MODEL = "no model is on record for this work"
WHY_DEMO = "the demo model is never used for the night review"
WHY_AUTO = "this work's model was picked by Auto, so the night review cannot ask the same one"
WHY_PAY = "{name} bills per use, and the night review never spends money"
WHY_LOCAL = "this work was Local only, and {name} runs off this network, so nothing was sent"
WHY_ACCOUNT_LIMIT = "your {label} account is at its limit"
WHY_ACCOUNT_BUSY = "your {label} account was at {percent}% of its window"
WHY_CAP = "tonight's limit of {cap} requests was reached"

_LOCK = threading.Lock()
#: This process's own tally, ``{night: requests}`` (the file may be locked).
_MINE: dict[str, int] = {}


def reset() -> None:
    """Tests: forget what this process counted (as a restart does)."""
    with _LOCK:
        _MINE.clear()


def cap_of(config: Any) -> int:
    """The night's cap: ``night_review_requests``, bounded 1 to 20; anything
    that is not a whole number is the default."""
    raw = getattr(config, "night_review_requests", DEFAULT_CAP)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return DEFAULT_CAP
    return max(CAP_MIN, min(CAP_MAX, int(raw)))


def _read(home: Any) -> "dict[str, Any] | None":
    """The tally file: ``{}`` when there is none or it does not hold what
    Iron Jarvis wrote; None when it is there but could not be READ (locked):
    None is never answered by writing a new file."""
    try:
        text = (Path(str(home)) / MARKER).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        return None
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(home: Any, data: dict[str, Any]) -> None:
    path = Path(str(home)) / MARKER
    try:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        log.debug("night review: the tally could not be written", exc_info=True)


def _nights(data: "dict[str, Any] | None") -> dict[str, int]:
    raw = (data or {}).get("nights")
    out: dict[str, int] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                out[k] = v
    return out


def requests_tonight(home: Any, night: str) -> int:
    """Requests spent for ``night``: the larger of the file's count and this
    process's own. BLOCKING; never raises."""
    key = str(night or "")
    with _LOCK:
        mine = _MINE.get(key, 0)
        if not home:
            return mine
        try:
            data = _read(home)
        except Exception:  # noqa: BLE001
            data = None
        return max(mine, _nights(data).get(key, 0)) if data is not None else mine


def count(home: Any, night: str) -> int:
    """One request is about to be sent for ``night``: counted (this process
    and the file), and the new total returned. BLOCKING; never raises."""
    key = str(night or "")
    with _LOCK:
        try:
            data = _read(home) if home else None
        except Exception:  # noqa: BLE001
            data = None
        nights = _nights(data)
        total = max(_MINE.get(key, 0), nights.get(key, 0)) + 1
        _MINE[key] = total
        if home and data is not None:
            nights[key] = total
            keep = dict(sorted(nights.items())[-KEEP_NIGHTS:])
            _write(home, {**data, "nights": keep})
        return total


def tonight(config: Any) -> str:
    """The night it is now (the date the night window last opened)."""
    from ..harness import night as N
    from ..harness.night_morning import night_key

    start, end = N.window_of(config)
    return night_key(N.now_local(), start, end)


def _name(provider: str, platform: Any) -> str:
    try:
        from ..providers.failure_words import heading, plain_name

        return heading(plain_name(provider, getattr(platform, "providers", None)) or provider)
    except Exception:  # noqa: BLE001 — wording only
        return provider


def _local_only_refusal(platform: Any, provider: str, model: str, local_only: bool) -> str:
    """Local only work and a model that leaves this network. A reading that
    fails refuses (fail closed: the work stays home)."""
    if not local_only:
        return ""
    try:
        from ..providers.egress import stays_on_this_network

        home_net = bool(stays_on_this_network(
            provider, getattr(platform, "providers", None), getattr(platform, "config", None), model,
        ))
    except Exception:  # noqa: BLE001
        home_net = False
    return "" if home_net else WHY_LOCAL.format(name=_name(provider, platform))


def _pays(platform: Any, provider: str, model: str) -> bool:
    try:
        from ..providers.billing import COST_PAY, billing_kind

        return billing_kind(
            provider, getattr(platform, "providers", None), getattr(platform, "config", None),
            model, unsure=COST_PAY,
        ) == COST_PAY
    except Exception:  # noqa: BLE001 — cannot tell: never spend money on a guess
        return True


def _account_refusal(platform: Any, provider: str, account: str) -> str:
    """The account rule (``harness/library.account_refusal``), in the night
    review's words. "" = it may be spent (or Iron-Proxy does not know it)."""
    try:
        from ..harness import build_accounts as BA
        from ..harness.library import REASON_ACCOUNT_LIMIT, account_refusal

        svc = getattr(platform, "iron_proxy", None)
        acct = " ".join(str(account or "").split())
        row: "dict[str, Any] | None" = None
        if acct:
            row = BA.find(svc, acct)
        else:
            vendor = BA.vendor_of(platform, provider)
            row = BA.default_for(svc, vendor) if vendor else None
            acct = str((row or {}).get("id") or "")
        if not acct:
            return ""
        refused = account_refusal(platform, {"account": acct})
        if not refused:
            return ""
        label = str((row or {}).get("vendor_label") or "coding tool")
        if refused == REASON_ACCOUNT_LIMIT:
            return WHY_ACCOUNT_LIMIT.format(label=label)
        usage = (row or {}).get("usage") if isinstance((row or {}).get("usage"), dict) else {}
        percent = usage.get("percent") if isinstance(usage, dict) else None
        if isinstance(percent, (int, float)) and not isinstance(percent, bool):
            return WHY_ACCOUNT_BUSY.format(label=label, percent=int(percent))
        return WHY_ACCOUNT_LIMIT.format(label=label)
    except Exception:  # noqa: BLE001 — unknown is not a refusal (the pinned pick refuses a parked one)
        return ""


def may_spend(platform: Any, *, provider: str, model: str, account: str = "",
              local_only: bool = False, night: str = "") -> str:
    """"" when ONE background request to ``provider`` / ``model`` (on
    ``account``) may be sent now for the night review; else why not. See the
    module docstring for the order. BLOCKING (the tally file); never raises."""
    config = getattr(platform, "config", None)
    try:
        from ..agents.orchestrator import emergency_stop_on

        if emergency_stop_on(config):
            return WHY_STOPPED
    except Exception:  # noqa: BLE001 — cannot read the switch: hold everything
        return WHY_STOPPED
    p = " ".join(str(provider or "").split())
    m = " ".join(str(model or "").split())
    if not p:
        return WHY_NO_MODEL
    if p.lower() == "mock" or p.lower().startswith("mock"):
        return WHY_DEMO
    if p.lower() == "auto":
        # The router chose per request; a pinned call needs ONE named model,
        # and a guess could land on one that bills per use.
        return WHY_AUTO
    local = _local_only_refusal(platform, p, m, bool(local_only))
    if local:
        return local
    if _pays(platform, p, m):
        return WHY_PAY.format(name=_name(p, platform))
    busy = _account_refusal(platform, p, account)
    if busy:
        return busy
    key = str(night or "") or tonight(config)
    cap = cap_of(config)
    if requests_tonight(getattr(config, "home", None), key) >= cap:
        return WHY_CAP.format(cap=cap)
    return ""


def needs_you(reason: str) -> bool:
    """Is ``reason`` one only the owner can change (so the Improvements page
    says it)? A model that bills per use, the demo model, and Local only work
    on a model off this network. A busy account and the cap pass by
    themselves; Emergency stop is the owner's own switch, already on screen."""
    text = str(reason or "")
    if not text:
        return False
    if text in (WHY_DEMO, WHY_AUTO):
        return True
    if text.startswith(WHY_LOCAL.split("{", 1)[0]):
        return True
    return text.endswith(WHY_PAY.split("}", 1)[1])


__all__ = [
    "CAP_MAX", "CAP_MIN", "DEFAULT_CAP", "MARKER", "WHY_ACCOUNT_BUSY", "WHY_ACCOUNT_LIMIT", "WHY_AUTO", "WHY_CAP",
    "WHY_DEMO", "WHY_LOCAL", "WHY_NO_MODEL", "WHY_PAY", "WHY_STOPPED", "cap_of", "count", "may_spend",
    "needs_you", "requests_tonight", "reset", "tonight",
]
