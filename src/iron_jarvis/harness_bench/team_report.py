"""The report's TEAM and USAGE sections (v1.396.0, the long-jobs plan's Phase
7: MEASURED).

The plan's question: *does the team pass as many tasks as Claude alone while
using less of the Claude window?* The report answers it from numbers only:

* EXACT usage — the tokens and list-price equivalents each run's PARTS carry
  (:mod:`.ledger`: each model's share, from its own receipt, checked against
  the calls the bench saw), summed per vendor (Claude, Codex, local);
* MEASURED usage — the percent of each subscription window used across the
  benchmark, from the vendors' own readings (:mod:`.usage`), only inside one
  window period ("not comparable" when the window reset inside the span; "not
  known" when no reading came — never 0%);
* the ANSWER — one line: "passed X of Y vs Claude alone A of B, using N% less
  Claude list-price" over the runs BOTH setups ran, or "not enough runs to
  say" — never a number the records do not hold;
* WHO DID WHAT — per member and the planner: pieces by role, model calls,
  tokens, cost, and the team router's reasons;
* the ROUTER'S RECORD per member and role after the newest run, beside the
  thresholds it judged with — the numbers Phase 7 tunes from.

Every function is pure (records in, words out) and never raises on an old
record: a run with no parts or no readings reads as "not known".
"""

from __future__ import annotations

from collections import Counter, OrderedDict
from typing import Any, Iterable, Mapping

from . import usage as U

#: The runner a team setup is run with (``team.TEAM_RUNNER``; repeated here so
#: the report never imports the runners).
TEAM_RUNNER = "jarvis-team"
PLANNER = "planner"
#: Said beside every measured number (its honest limits).
MEASURED_LIMITS = (
    "A measured number is the vendor's own percent of the window, from the first reading in the "
    "span to the last. A reading arrives after its call, so the first call's own share is not in "
    "it; and the window is the whole account's, so any other use of that account in that time "
    "(another app, another session) is in it."
)
#: Said beside the router's record.
RECORD_NOTE = (
    "The router judges a member by the finish check and the reviewer's verdict, not by the "
    "benchmark's hidden checks: a piece can pass in this record and its task still fail."
)
#: The answer when the numbers cannot answer.
NOT_ENOUGH = "not enough runs to say"
#: v1.399.2: the answer when the planner handed NO piece to a team member in
#: any run the two setups are compared on. The team row is then the planner
#: working alone, and any "Yes" / "No" about the team would be the planner's
#: own run-to-run variation read as a verdict (the 2026-10-26 live run: 35 of
#: 36 both ways, "2% less" Claude list-price, and zero pieces handed out).
NO_MEMBER_WORK = (
    "No answer: the planner never handed a piece to a team member in these runs, so the team row "
    "is the planner working alone and says nothing about the team."
)


#: v1.400.0: how a TEAM-FIRST setup (the team file's ``team_first = true``:
#: the "Hand work to my team first" setting on) is named beside the plain
#: team, wherever a setup's runner is shown.
TEAM_FIRST_TAG = "team-first"


def is_team_first(config: Mapping[str, Any]) -> bool:
    """Was this setup a team run with team-first on? Read off the saved
    configuration's identity (``harness.team.team_first``); a configuration
    saved before v1.400.0 has no such key and is a plain team."""
    harness = config.get("harness") if isinstance(config, Mapping) else None
    team = harness.get("team") if isinstance(harness, Mapping) else None
    return config.get("runner") == TEAM_RUNNER and isinstance(team, Mapping) and team.get("team_first") is True


def runner_label(config: Mapping[str, Any]) -> str:
    """The Runner cell: the runner's name, and ``(team-first)`` for a team
    setup with team-first on — so the two team setups read apart."""
    runner = str(config.get("runner") or "?")
    return f"{runner} ({TEAM_FIRST_TAG})" if is_team_first(config) else runner


def _live(records: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [r for r in records if not r.get("stale") and not r.get("interrupted")]


def _add(a: Any, b: Any) -> Any:
    return None if a is None or b is None else a + b


def _repeat(r: Mapping[str, Any]) -> int:
    try:
        return max(1, int(r.get("repeat") or 1))
    except (TypeError, ValueError):
        return 1


def run_parts(config: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, dict[str, Any]] | None:
    """A run's parts: its own (a Jarvis run since v1.396.0); one part for a
    CLI runner's run or an older Jarvis run (the whole run on its one model —
    its vendor from the runner or the provider); None when the run has no
    numbers at all (it did not run)."""
    parts = record.get("parts")
    if isinstance(parts, Mapping) and parts:
        return {str(k): dict(v) for k, v in parts.items() if isinstance(v, Mapping)}
    if record.get("tokens_in") is None and record.get("cost_list_price_usd") is None:
        return None
    runner = str(config.get("runner") or record.get("runner") or "")
    provider = str(config.get("provider") or record.get("provider") or "")
    vendor = {"claude": "claude", "codex": "codex"}.get(runner) or U.vendor_of(
        provider, on_network=(record.get("extra") or {}).get("on_this_network") is True
        if isinstance(record.get("extra"), Mapping) else None)
    return {PLANNER: {
        "provider": provider, "model": str(config.get("model") or ""), "vendor": vendor, "receipts": 1,
        "pieces": {}, "model_calls": record.get("model_calls"),
        "tokens_in": record.get("tokens_in"), "tokens_out": record.get("tokens_out"),
        "tokens_cached": record.get("tokens_cached"),
        "cost_metered_usd": record.get("cost_metered_usd"),
        "cost_list_price_usd": record.get("cost_list_price_usd"),
    }}


def _vendor_totals(parts_list: list[dict[str, dict[str, Any]] | None]) -> dict[str, dict[str, Any]]:
    """Per vendor: tokens and money summed over runs (an unknown part makes
    that number unknown), how many runs used it, how many runs had no
    numbers."""
    out: dict[str, dict[str, Any]] = {}
    for parts in parts_list:
        if parts is None:
            continue
        used: set[str] = set()
        for part in parts.values():
            vendor = str(part.get("vendor") or "other")
            calls, tin, tout = part.get("model_calls"), part.get("tokens_in"), part.get("tokens_out")
            # An idle member (no call, nothing used) is not a use of its vendor;
            # an unknown number is (it keeps the total unknown).
            if not ((calls or 0) > 0 or (tin or 0) > 0 or (tout or 0) > 0 or tin is None or tout is None):
                continue
            row = out.setdefault(vendor, {"tokens_in": 0, "tokens_out": 0, "cost_metered_usd": 0.0,
                                          "cost_list_price_usd": 0.0, "runs": 0, "model_calls": 0})
            used.add(vendor)
            for key in ("tokens_in", "tokens_out", "cost_metered_usd", "cost_list_price_usd"):
                row[key] = _add(row[key], part.get(key))
            row["model_calls"] = _add(row["model_calls"], part.get("model_calls"))
        for vendor in used:
            out[vendor]["runs"] += 1
    return out


def _windows_order(name: str) -> tuple[int, str]:
    return ({"5h": 0, "primary": 0, "7d": 1, "secondary": 1}.get(name, 2), name)


def measured_usage(records: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Per vendor (claude / codex): one entry per ACCOUNT the setup's calls
    ran on — ``{account, name, calls, runs, runs_without_reading, windows:
    [{window, window_mins, words, result}]}`` with ``result`` from
    :func:`usage.measured_span` (and ``{"state": "not_known"}`` for an account
    whose calls carried no reading at all)."""
    acc: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    for r in sorted(_live(records), key=lambda r: str(r.get("started_at") or "")):
        accounts = r.get("accounts")
        if not isinstance(accounts, Mapping):
            continue
        for key, a in accounts.items():
            if not isinstance(a, Mapping):
                continue
            row = acc.setdefault(str(key), {
                "vendor": str(a.get("vendor") or ""), "account": str(a.get("account") or ""),
                "name": str(a.get("name") or ""), "calls": 0, "runs": 0, "runs_without_reading": 0,
                "windows": OrderedDict(),
            })
            row["calls"] += int(a.get("calls") or 0)
            row["runs"] += 1
            windows = a.get("windows") if isinstance(a.get("windows"), Mapping) else {}
            if not windows:
                row["runs_without_reading"] += 1
            for wname, w in windows.items():
                if isinstance(w, Mapping):
                    row["windows"].setdefault(str(wname), []).append(w)
    out: dict[str, list[dict[str, Any]]] = {}
    for row in acc.values():
        windows = []
        for wname in sorted(row["windows"], key=_windows_order):
            runs = row["windows"][wname]
            mins = next((w.get("window_mins") for w in runs if w.get("window_mins")), None)
            if not row["account"]:
                result = {"state": "not_comparable",
                          "why": "the calls did not name their account (Iron-Proxy picked one per call), "
                                 "so the readings may be of more than one account"}
            else:
                result = U.measured_span(runs)
            windows.append({"window": wname, "window_mins": mins,
                            "words": U.window_words(wname, mins), "result": result})
        out.setdefault(row["vendor"], []).append({
            "account": row["account"], "name": row["name"] or U.account_words(row["account"]),
            "calls": row["calls"], "runs": row["runs"], "runs_without_reading": row["runs_without_reading"],
            "windows": windows,
        })
    return out


def _who_did_what(records: list[Mapping[str, Any]],
                  parts_list: list[dict[str, dict[str, Any]] | None]) -> "OrderedDict[str, dict[str, Any]]":
    """Per part (the planner, then each member): its model, pieces by role,
    calls, tokens and money summed over the runs, and the router's reasons
    (from the helper rows) with how often each was given."""
    out: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    for parts in parts_list:
        if parts is None:
            continue
        for name in sorted(parts, key=lambda n: (n != PLANNER, n)):
            part = parts[name]
            row = out.setdefault(name, {
                "provider": part.get("provider") or "", "model": part.get("model") or "",
                "vendor": part.get("vendor") or "", "pieces": Counter(), "model_calls": 0,
                "tokens_in": 0, "tokens_out": 0, "cost_metered_usd": 0.0, "cost_list_price_usd": 0.0,
                "reasons": Counter(), "states": Counter(),
            })
            for key, n in (part.get("pieces") or {}).items():
                row["pieces"][str(key)] += int(n or 0)
            for key in ("model_calls", "tokens_in", "tokens_out", "cost_metered_usd", "cost_list_price_usd"):
                row[key] = _add(row[key], part.get(key))
    for r in records:
        for h in r.get("helpers") or ():
            if not isinstance(h, Mapping):
                continue
            member = str(h.get("member") or "")
            row = out.get(member) if member in out else None
            if row is None:
                continue
            if h.get("reason"):
                row["reasons"][(str(h.get("role") or ""), str(h["reason"]))] += 1
            row["states"][str(h.get("state") or "")] += 1
    return out


def _newest_team_records(records: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    rows = [r for r in records if isinstance(r.get("team_records"), Mapping)]
    if not rows:
        return None

    def when(r: Mapping[str, Any]) -> tuple[float, int]:
        # Its own read time (runs a second apart share a finished_at), then
        # the runner's own count of reads.
        rec = r["team_records"]
        at = rec.get("read_at")
        return (float(at) if isinstance(at, (int, float)) else 0.0, int(rec.get("read") or 0))

    return max(rows, key=when)["team_records"]


def summarize_usage(config: Mapping[str, Any], records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Everything the usage and team sections read, for one setup."""
    live = _live(records)
    parts_list = [run_parts(config, r) for r in live]
    walls = [float(r["wall_s"]) for r in live if isinstance(r.get("wall_s"), (int, float))]
    return {
        "vendors": _vendor_totals(parts_list),
        "runs_without_numbers": sum(1 for p in parts_list if p is None),
        "measured": measured_usage(live),
        "who": _who_did_what(live, parts_list),
        "router_record": _newest_team_records(live),
        "wall_s_total": sum(walls) if walls else None,
        "wall_s_mean": (sum(walls) / len(walls)) if walls else None,
    }


# --------------------------------------------------------------------------- #
# words
# --------------------------------------------------------------------------- #


def _money(v: Any) -> str:
    return "n/a" if v is None else f"${float(v):.4f}"


def _tok(v: Any) -> str:
    return "n/a" if v is None else f"{int(v):,}"


def _secs(v: Any) -> str:
    if v is None:
        return "n/a"
    v = float(v)
    return f"{v:.1f} s" if v < 120 else f"{v / 60:.1f} min"


def exact_words(vendors: Mapping[str, Mapping[str, Any]], vendor: str) -> str:
    """``12,345 in / 678 out, $0.1234 list price`` for a vendor, or "not
    used"."""
    row = vendors.get(vendor)
    if not row:
        return "not used"
    text = f"{_tok(row.get('tokens_in'))} in / {_tok(row.get('tokens_out'))} out"
    if vendor in ("claude", "codex"):
        text += f", {_money(row.get('cost_list_price_usd'))} list price"
        if row.get("cost_metered_usd"):
            text += f" + {_money(row.get('cost_metered_usd'))} billed"
    elif row.get("cost_metered_usd"):
        text += f", {_money(row.get('cost_metered_usd'))} billed"
    return text


def result_words(result: Mapping[str, Any]) -> str:
    state = result.get("state")
    if state == "measured":
        return (f"+{float(result['points']):g} points ({result['first'].get('percent')}% to "
                f"{result['last'].get('percent')}%, {result.get('readings')} readings)")
    if state == "not_comparable":
        return f"not comparable: {result.get('why')}"
    return f"{U.NOT_KNOWN}: no reading came"


def measured_words(measured: Mapping[str, list[Mapping[str, Any]]], vendor: str,
                   vendors: Mapping[str, Any]) -> str:
    """One account's windows in words, per account; "not used" when the setup
    made no call on that vendor; "not known" when calls carried no reading."""
    rows = measured.get(vendor) or []
    if not rows:
        return "not used" if vendor not in vendors else f"{U.NOT_KNOWN}: no reading came"
    texts = []
    for a in rows:
        if not a["windows"]:
            words = f"{U.NOT_KNOWN}: no reading came"
        else:
            words = "; ".join(f"{w['words']} window {result_words(w['result'])}" for w in a["windows"])
            if a.get("runs_without_reading"):
                # Said, never hidden: those runs' use is only in the span when
                # a later reading came.
                words += f" ({a['runs_without_reading']} run(s) on this account carried no reading)"
        texts.append((f"{a['name']}: " if len(rows) > 1 else "") + words)
    return " · ".join(texts)


def _scored(rows: Iterable[Mapping[str, Any]]) -> tuple[int, int]:
    rows = [r for r in rows if r.get("passed") is not None]
    return sum(1 for r in rows if r.get("passed") is True), len(rows)


def _vendor_of_planner(provider: str) -> str:
    return U.vendor_of(provider)


def alone_label(config: Mapping[str, Any]) -> str:
    vendor = _vendor_of_planner(str(config.get("provider") or ""))
    return {"claude": "Claude", "codex": "Codex"}.get(vendor, str(config.get("model") or "the planner"))


def _key(r: Mapping[str, Any]) -> tuple[str, int]:
    return (str(r.get("task_id") or ""), _repeat(r))


def find_alone(team_config: Mapping[str, Any], team_records: list[Mapping[str, Any]],
               configs: list[tuple[Mapping[str, Any], list[Mapping[str, Any]]]]
               ) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]] | None:
    """The single-model setup to answer against: Iron Jarvis on the team's
    PLANNER (same provider and model) alone — with the same effort first,
    then the same Iron Jarvis version, then the one sharing the most runs
    with the team, the newest on a tie."""
    team_keys = {_key(r) for r in _live(team_records)}
    best = None
    for config, records in configs:
        if config.get("runner") != "jarvis":
            continue
        if (config.get("provider"), config.get("model")) != (team_config.get("provider"), team_config.get("model")):
            continue
        shared = len(team_keys & {_key(r) for r in _live(records)})
        # The same effort as the team's planner first (another effort is
        # another setup), then the same Iron Jarvis (v1.400.0 review: a root
        # keeps every night's folders, and tonight's alone row shares every
        # task and repeat with LAST night's team — picked as the newest, it
        # re-answered last night's team against another harness), then the
        # most runs in common, then the newest.
        same_effort = str(config.get("effort") or "") == str(team_config.get("effort") or "")
        same_version = str(config.get("version") or "") == str(team_config.get("version") or "")
        rank = (same_effort, same_version, shared, str(config.get("created_at") or ""))
        if best is None or rank > best[0]:
            best = (rank, config, records)
    return (best[1], best[2]) if best else None


def compares_line(team_config: Mapping[str, Any], alone_config: Mapping[str, Any]) -> str:
    """Which single-model row a team's answer compares with (v1.400.0
    review): with every night's folders in one root, two alone rows can sit
    in the same table, and only this line says which one the answer read —
    and, when it is another Iron Jarvis, that it is."""
    a_ver = str(alone_config.get("version") or "?")
    t_ver = str(team_config.get("version") or "?")
    line = (f"The answer compares with {str(alone_config.get('key', ''))[:12]} "
            f"({runner_label(alone_config)}, Iron Jarvis {a_ver})")
    if a_ver != t_ver:
        line += f", which ran on another Iron Jarvis than this team ({t_ver})"
    a_eff = str(alone_config.get("effort") or "")
    t_eff = str(team_config.get("effort") or "")
    if a_eff != t_eff:
        line += (f", at effort {a_eff or 'none asked'} (this team's planner: "
                 f"{t_eff or 'none asked'})")
    return line + "."


#: v1.401.0: the helper row of the review Iron Jarvis's own FINISH CHECK hands
#: the team's reviewer (``harness.finish.REVIEW_CALL_ID`` — pinned equal). The
#: PLANNER did not hand it out, so it never counts as "the planner handed a
#: piece" (:func:`planner_handed`); it is team work all the same
#: (:func:`handed_to_a_member`, :func:`finish_reviewed`).
FINISH_REVIEW_ID = "ij-finish-review"


def _finish_review_rows(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [h for h in (record.get("helpers") or ())
            if isinstance(h, Mapping) and str(h.get("id") or "") == FINISH_REVIEW_ID]


def _a_member(name: Any) -> bool:
    """A helper row's ``member`` that names a member (not ``role:<id>``: a role
    the team router could not place started nothing)."""
    text = str(name or "")
    return bool(text) and not text.startswith("role:")


def finish_reviewed(record: Mapping[str, Any]) -> bool:
    """Did Iron Jarvis's own finish check hand this run's change to a team
    member — the team's review at the finish (v1.401.0, "Hand work to my team
    first" on)? A row the router could not place (no member) does not count."""
    return any(_a_member(h.get("member")) for h in _finish_review_rows(record))


def planner_handed(record: Mapping[str, Any]) -> bool:
    """Did the PLANNER hand at least one piece to a team member? A member's
    part with a model call or a piece, or a helper row naming a member (a piece
    the member then failed, or one its hold refused, still went to the team) —
    never the finish check's own review (v1.401.0: Iron Jarvis handed that
    one, not the planner; its piece and its model calls are left out of the
    member's part)."""
    reviews = Counter(str(h.get("member") or "") for h in _finish_review_rows(record))
    parts = record.get("parts")
    if isinstance(parts, Mapping):
        for name, part in parts.items():
            if str(name) == PLANNER or not isinstance(part, Mapping):
                continue
            pieces = part.get("pieces") or {}
            try:
                own = sum(int(v or 0) for v in pieces.values()) if isinstance(pieces, Mapping) else 0
            except (TypeError, ValueError):
                own = 1 if any(pieces.values()) else 0
            if own - reviews.get(str(name), 0) > 0:
                return True
            if (part.get("model_calls") or 0) > 0 and not reviews.get(str(name)):
                return True
    return any(
        isinstance(h, Mapping) and str(h.get("member") or "") and str(h.get("id") or "") != FINISH_REVIEW_ID
        for h in (record.get("helpers") or ())
    )


def handed_to_a_member(record: Mapping[str, Any]) -> bool:
    """Did a team member work in this run at all? The planner handed it a
    piece (:func:`planner_handed`), or — v1.401.0 — Iron Jarvis's own finish
    check handed it the change to review (:func:`finish_reviewed`). Only when
    neither did is the team row the planner working alone."""
    return planner_handed(record) or finish_reviewed(record)


def handed_count(records: Iterable[Mapping[str, Any]]) -> tuple[int, int, int]:
    """``(handed, ran, did_not_run)`` over a setup's live runs (v1.400.0):
    how many runs the PLANNER handed at least one piece to a team member
    (:func:`planner_handed` — v1.401.0: never the finish check's own review),
    of the runs that ran — a run that never got a fair go (an account limit,
    a server down: ``unscored_by == "infrastructure"``) and in which no member
    worked is counted apart. v1.400.0 review: a run that DID hand a piece and
    then went unscored because the member could not work (its server down,
    its account's limit: ``member_infra``) is a run in which work went out —
    it counts as ran."""
    live = _live(records)
    handed = [r for r in live if planner_handed(r)]
    ran = [r for r in live if r.get("unscored_by") != "infrastructure" or handed_to_a_member(r)]
    return len(handed), len(ran), len(live) - len(ran)


def reviewed_count(records: Iterable[Mapping[str, Any]]) -> tuple[int, int]:
    """``(reviewed, ran)`` (v1.401.0): in how many of the runs that ran (as
    :func:`handed_count` counts them) Iron Jarvis's own finish check handed
    the change to the team's reviewer (:func:`finish_reviewed`)."""
    live = _live(records)
    ran = [r for r in live if r.get("unscored_by") != "infrastructure" or handed_to_a_member(r)]
    return sum(1 for r in ran if finish_reviewed(r)), len(ran)


def handed_line(records: Iterable[Mapping[str, Any]]) -> str:
    """The first number to look for in a team setup (v1.400.0): "The planner
    handed at least one piece to a team member in N of M runs." ("" with no
    run; "Not known: …" when every run did not run). v1.401.0: when Iron
    Jarvis's own finish check handed the change to the team's reviewer, a
    second sentence says in how many runs — never counted as the planner's."""
    records = list(records)
    m, n, skipped = handed_count(records)
    if not n and not skipped:
        return ""
    if not n:
        # v1.400.0 review: "in 0 of 0 runs" read like a number.
        return ("Not known: the 1 run did not run, so it could not hand a piece to a team member."
                if skipped == 1 else
                f"Not known: all {skipped} runs did not run, so none could hand a piece to a team member.")
    line = (f"The planner handed at least one piece to a team member in {m} of {n} "
            f"run{'' if n == 1 else 's'}" + (f" ({skipped} did not run)" if skipped else "") + ".")
    reviewed, _ran = reviewed_count(records)
    if reviewed:
        line += (f" Iron Jarvis's own finish check handed the change to the team's reviewer in "
                 f"{reviewed} of {n} run{'' if n == 1 else 's'}.")
    return line


def answer(team: tuple[Mapping[str, Any], list[Mapping[str, Any]]],
           alone: tuple[Mapping[str, Any], list[Mapping[str, Any]]] | None) -> str:
    """The plan's question, answered only from numbers both setups hold:
    their pass counts over the runs (task, repeat) BOTH ran, and the planner
    vendor's list-price over those runs."""
    t_config, t_records = team
    label = alone_label(t_config)
    if alone is None:
        return f"{NOT_ENOUGH}: there is no {label}-alone row (Iron Jarvis on the planner's model, no team) yet."
    a_config, a_records = alone
    t_by = {_key(r): r for r in _live(t_records)}
    a_by = {_key(r): r for r in _live(a_records)}
    both = sorted(set(t_by) & set(a_by))
    if not both:
        return f"{NOT_ENOUGH}: the team and {label} alone have no run of the same task and repeat."
    # Only runs BOTH sides had a fair go at and were scored on: a run one
    # side did not run (an account limit, an unreachable model) would count
    # its near-zero usage against the other side's whole one.
    keys = [k for k in both if t_by[k].get("passed") is not None and a_by[k].get("passed") is not None]
    if not keys:
        return f"{NOT_ENOUGH}: no run both made was scored for both the team and {label} alone."
    x, n = _scored(t_by[k] for k in keys)
    a, _n = _scored(a_by[k] for k in keys)
    # v1.399.2: a team row whose planner handed nothing to a member is the
    # planner alone — no verdict about the team, before any other verdict.
    # (v1.401.0: a run in which the finish check handed the change to the
    # team's reviewer is team work too — `handed_to_a_member`.)
    m = sum(1 for k in keys if handed_to_a_member(t_by[k]))
    # v1.396.0 review: the PASS comparison leads, and a verdict is given only
    # from the numbers — a team that passed fewer tasks never gets a "less
    # usage" headline, and what the team spent BEYOND the planner's
    # subscription (Codex list price, local tokens) is said beside it.
    passes = (f"The team passed {x} of {n}, {label} alone {a} of {n} "
              f"(the {n} run{'' if n == 1 else 's'} both made and both were scored on).")
    vendor = _vendor_of_planner(str(t_config.get("provider") or ""))
    t_rows = [t_by[k] for k in keys]
    a_rows = [a_by[k] for k in keys]
    extra = _beyond(t_config, t_rows, vendor, "the team") + _beyond(a_config, a_rows, vendor, f"{label} alone")
    if 0 < m < n:
        passes += (f" Team members worked in {m} of the {n} run{'' if n == 1 else 's'}; "
                   f"in the rest the planner worked alone.")
    tasks = "more tasks" if x > a else "as many tasks"
    if x < a:
        verdict = "No: the team passed fewer tasks."
    if vendor not in ("claude", "codex"):
        if x >= a:
            verdict = f"The team passed {tasks}; the planner has no list price to compare."
        if m == 0:
            verdict = NO_MEMBER_WORK
        return " ".join([verdict, passes, *extra])
    name = {"claude": "Claude", "codex": "Codex"}[vendor]
    t_cost = _vendor_list_price(t_config, t_rows, vendor)
    a_cost = _vendor_list_price(a_config, a_rows, vendor)
    if t_cost is None or a_cost is None:
        if x >= a:
            verdict = f"Not known: the team passed {tasks}, but {name} list-price is not known for every run."
        usage = f"{name} list-price is not known for every run, so it is not compared."
    elif a_cost <= 0:
        if x >= a:
            verdict = f"Not known: the team passed {tasks}, but {label} alone used no {name} list-price to compare with."
        usage = f"{label} alone used no {name} list-price, so there is no percent to compare."
    else:
        pct = (a_cost - t_cost) / a_cost * 100
        if x >= a:
            verdict = (f"Yes on these runs: {tasks}, with less {name} list-price." if t_cost < a_cost
                       else f"No: {tasks}, but not less {name} list-price.")
        usage = (f"{name} list-price: {_money(t_cost)} for the team vs {_money(a_cost)} alone "
                 f"({abs(pct):.0f}% {'less' if pct >= 0 else 'more'}).")
    if m == 0:
        verdict = NO_MEMBER_WORK
    return " ".join([verdict, passes, usage, *extra])


def _beyond(config: Mapping[str, Any], records: list[Mapping[str, Any]], planner_vendor: str,
            who: str) -> list[str]:
    """``["Beyond Claude, the team also used …"]`` — every vendor other than
    the planner's that ``who`` used on these runs (Codex list price, local
    tokens, anything else), from the runs' own parts; [] when none."""
    totals = _vendor_totals([run_parts(config, r) for r in records])
    bits = []
    for vendor in ("codex", "claude", "local", "other"):
        row = totals.get(vendor)
        if vendor == planner_vendor or not row:
            continue
        if vendor in ("claude", "codex"):
            cost = row.get("cost_list_price_usd")
            bits.append(f"{_money(cost) if cost is not None else 'an unknown amount'} of "
                        f"{U.VENDOR_WORDS[vendor]} list-price")
        else:
            words = "local models" if vendor == "local" else "other models"
            bits.append(f"{_tok(row.get('tokens_in'))} tokens in / {_tok(row.get('tokens_out'))} out on {words}"
                        + (f" ({_money(row.get('cost_metered_usd'))} billed)" if row.get("cost_metered_usd") else ""))
    if not bits:
        return []
    name = U.VENDOR_WORDS.get(planner_vendor, "the planner's model")
    return [f"Beyond {name}, {who} also used " + "; ".join(bits) + "."]


def _vendor_list_price(config: Mapping[str, Any], records: list[Mapping[str, Any]], vendor: str) -> float | None:
    total = 0.0
    for r in records:
        parts = run_parts(config, r)
        if parts is None:
            return None
        for part in parts.values():
            if part.get("vendor") == vendor:
                cost = part.get("cost_list_price_usd")
                if cost is None:
                    return None
                total += float(cost)
    return total


def _tiers_and_repeats(records: list[Mapping[str, Any]]) -> tuple[frozenset[str], int]:
    live = _live(records)
    return frozenset(str(r.get("tier") or "core") for r in live), max((_repeat(r) for r in live), default=1)


def _rate_words(s: Mapping[str, Any]) -> str:
    def pct(v: Any) -> str:
        return "n/a" if v is None else f"{float(v) * 100:.0f}%"

    if s.get("repeats", 1) > 1:
        rng = s.get("range")
        return pct(s.get("mean_pass_rate")) + (f" ({pct(rng[0])}–{pct(rng[1])})" if rng else "")
    return pct(s.get("pass_rate"))


def _model_cell(config: Mapping[str, Any]) -> str:
    provider = config.get("provider") or ""
    return provider + (" / " if provider else "") + (config.get("model") or "model not named")


def who_lines(u: Mapping[str, Any]) -> list[str]:
    who = u.get("who") or {}
    if not who:
        return []
    lines = [
        "| Who | Model | Pieces by role | Model calls | Tokens in | Tokens out | Metered $ | List-price $ |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, row in who.items():
        pieces = ", ".join(f"{k} {n}" for k, n in sorted(row["pieces"].items())) or "-"
        model = f"{row['provider']} / {row['model']}" if row["provider"] else (row["model"] or "-")
        lines.append(
            f"| {name} | {model} | {pieces} | {_tok(row['model_calls'])} | {_tok(row['tokens_in'])} | "
            f"{_tok(row['tokens_out'])} | {_money(row['cost_metered_usd'])} | {_money(row['cost_list_price_usd'])} |"
        )
    reasons = []
    for name, row in who.items():
        for (role, why), n in row["reasons"].most_common(5):
            reasons.append(f"- {name}" + (f" ({role})" if role else "") + f": \"{why}\" × {n}")
    if reasons:
        lines += ["", "Why each piece went where it did (the team router's own words):", *reasons]
    return lines


#: v1.396.0 review: said when the router had NO usage numbers in a run (its
#: rule that keeps new work off an account at a window's limit never fired).
NO_USAGE_NUMBERS = (
    "In {runs} the router had no usage numbers (Iron-Proxy was off in the benchmark's home), so its rule "
    "that keeps new work off an account at {pct}% of a window never applied: every piece went by role and "
    "record alone."
)


def no_usage_line(records: Iterable[Mapping[str, Any]]) -> str:
    """:data:`NO_USAGE_NUMBERS` for the team runs that recorded the router had
    no usage numbers ("" when none did, or no run said)."""
    said = [r for r in _live(list(records)) if isinstance(r.get("team_records"), Mapping)
            and "usage_numbers" in r["team_records"]]
    without = [r for r in said if r["team_records"].get("usage_numbers") is False]
    if not without:
        return ""
    th = without[-1]["team_records"].get("thresholds") or {}
    runs = "every run" if len(without) == len(said) else f"{len(without)} of {len(said)} runs"
    return NO_USAGE_NUMBERS.format(runs=runs, pct=f"{float(th.get('new_work_max_percent') or 80):g}")


def piece_lines(records: Iterable[Mapping[str, Any]]) -> list[str]:
    """v1.396.0 review: the numbers to tune the router's "good record" from,
    beside the HIDDEN CHECK's verdict on the same runs — per member and role:
    the pieces (done / failed by the helper's own state), the runs they were
    in, how many of those runs passed the hidden check, and which tasks."""
    rows: "OrderedDict[tuple[str, str], dict[str, Any]]" = OrderedDict()
    for r in _live(list(records)):
        key = _key(r)
        for h in r.get("helpers") or ():
            if not isinstance(h, Mapping) or not str(h.get("member") or ""):
                continue
            member, role = str(h["member"]), str(h.get("role") or "") or "by name"
            row = rows.setdefault((member, role), {"pieces": 0, "done": 0, "failed": 0, "runs": {}, "tasks": set()})
            row["pieces"] += 1
            state = str(h.get("state") or "")
            row["done"] += state == "done"
            row["failed"] += state == "failed"
            row["runs"][key] = r.get("passed")
            row["tasks"].add(key[0])
    if not rows:
        return []
    lines = [
        "| Member | Role | Pieces | Done | Failed | Runs it worked in | Those runs: passed the hidden check / scored "
        "| Tasks |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for (member, role), row in sorted(rows.items()):
        verdicts = list(row["runs"].values())
        scored = [v for v in verdicts if v is not None]
        tasks = sorted(row["tasks"])
        shown = ", ".join(tasks[:6]) + (f", and {len(tasks) - 6} more" if len(tasks) > 6 else "")
        lines.append(f"| {member} | {role} | {row['pieces']} | {row['done']} | {row['failed']} | {len(verdicts)} | "
                     f"{sum(1 for v in scored if v is True)} / {len(scored)} | {shown} |")
    return lines


def record_lines(u: Mapping[str, Any], records: Iterable[Mapping[str, Any]] = ()) -> list[str]:
    rec = u.get("router_record")
    if not isinstance(rec, Mapping):
        return []
    th = rec.get("thresholds") or {}
    lines = [
        f"The thresholds it judged with: a record is judged after {th.get('min_runs')} pieces, is good at "
        f"{float(th.get('good_rate') or 0) * 100:.0f}% passed or more, and is not good after "
        f"{th.get('fail_streak')} failures in a row (over the newest {th.get('record_window')}); no new "
        f"work goes to an account at {th.get('new_work_max_percent')}% of a window.",
        "",
        "| Member | Role | Pieces | Passed | Failed in a row | Judged |",
        "|---|---|---|---|---|---|",
    ]
    for member, roles in (rec.get("members") or {}).items():
        if not roles:
            lines.append(f"| {member} | - | 0 | 0 | 0 | no record |")
        for role, view in (roles or {}).items():
            lines.append(
                f"| {member} | {role} | {view.get('runs', 0)} | {view.get('passed', 0)} | "
                f"{view.get('streak', 0)} | {view.get('words') or view.get('status') or '-'} |"
            )
    lines += ["", RECORD_NOTE]
    records = list(records)
    pieces = piece_lines(records)
    if pieces:
        lines += ["", "The same pieces beside the hidden check, over the runs (the router never sees this column):",
                  "", *pieces]
    usage = no_usage_line(records)
    if usage:
        lines += ["", usage]
    return lines


def usage_section(config: Mapping[str, Any], records: list[Mapping[str, Any]]) -> list[str]:
    """A setup's own usage, who did what and the router's record (its
    ``report.md``). Empty for a setup whose runs carry neither parts nor
    readings (a run written before v1.396.0 reads as it always did)."""
    live = _live(records)
    if not any(isinstance(r.get("parts"), Mapping) or isinstance(r.get("accounts"), Mapping) for r in live):
        return []
    u = summarize_usage(config, live)
    vendors = u["vendors"]
    lines = []
    if config.get("runner") == TEAM_RUNNER:
        # v1.400.0: did the planner hand the team anything at all?
        handed = handed_line(live)
        if handed:
            lines += ["", "## The team at work", "", handed]
    lines += [
        "", "## Usage", "",
        f"Time: {_secs(u['wall_s_total'])} in all, {_secs(u['wall_s_mean'])} a run.",
        "",
        "| | Exact (tokens and list price, summed over the runs) | Measured (the window's own percent) |",
        "|---|---|---|",
        f"| Claude | {exact_words(vendors, 'claude')} | {measured_words(u['measured'], 'claude', vendors)} |",
        f"| Codex | {exact_words(vendors, 'codex')} | {measured_words(u['measured'], 'codex', vendors)} |",
        f"| Local | {exact_words(vendors, 'local')} | (no window) |",
    ]
    if vendors.get("other"):
        lines.append(f"| Other | {exact_words(vendors, 'other')} | (no window) |")
    if u["runs_without_numbers"]:
        lines.append(f"\n{u['runs_without_numbers']} run(s) carry no usage numbers (they did not run).")
    lines += ["", MEASURED_LIMITS]
    who = who_lines(u)
    if who:
        lines += ["", "## Who did what", "", *who]
    rec = record_lines(u, live)
    if rec:
        lines += ["", "## What the team router learned", "", *rec]
    return lines


def side_by_side(configs: list[tuple[Mapping[str, Any], list[Mapping[str, Any]], Mapping[str, Any]]]
                 ) -> list[str]:
    """The combined report's comparison: every TEAM setup beside the
    single-model setups of the same tiers and repeats, and its answer."""
    teams = [(c, r, s) for c, r, s in configs if c.get("runner") == TEAM_RUNNER]
    if not teams:
        return []
    lines = ["", "## The team beside one model", ""]
    pairs = [(c, r) for c, r, _s in configs]
    for t_config, t_records, t_s in teams:
        shape = _tiers_and_repeats(t_records)
        # v1.400.0: the OTHER team setups of the same planner (the plain team
        # beside the team-first one) sit in the same table, each its own row.
        planner = (t_config.get("provider"), t_config.get("model"), str(t_config.get("effort") or ""))
        others = [(c, r, s) for c, r, s in teams
                  if c is not t_config and _tiers_and_repeats(r) == shape
                  and (c.get("provider"), c.get("model"), str(c.get("effort") or "")) == planner]
        peers = [(c, r, s) for c, r, s in configs
                 if c.get("runner") != TEAM_RUNNER and _tiers_and_repeats(r) == shape]
        alone = find_alone(t_config, t_records, pairs)
        if alone is not None and not any(c is alone[0] for c, _r, _s in peers):
            # v1.400.0 review: the row the answer compares with is always in
            # the table, even when its tiers or repeats differ (a setup cut
            # short, another night's): it was answered against, unseen.
            peers += [(c, r, s) for c, r, s in configs if c is alone[0]]
        tiers = ", ".join(sorted(shape[0])) or "-"
        lines += [
            f"**Team {str(t_config.get('key', ''))[:12]}{', ' + TEAM_FIRST_TAG if is_team_first(t_config) else ''}** "
            f"(planner {_model_cell(t_config)}; tier {tiers}; "
            f"{shape[1]} repeat(s)).",
            "",
            "| Setup | Runner | Model | Tasks | Pass rate (range) | Per repeat | Passed / scored | Time (a run) "
            "| Claude exact | Claude measured | Codex exact | Codex measured | Local tokens |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for config, records, s in [(t_config, t_records, t_s), *others, *peers]:
            u = summarize_usage(config, records)
            v = u["vendors"]
            local = v.get("local")
            per_repeat = ", ".join(f"{p['passed']}/{p['scored']}" for p in (s.get("per_repeat") or {}).values())
            # A row's numbers are over ITS runs: a setup that ran more tasks
            # says so here (the answer below is over the runs both made).
            tasks = len({str(r.get("task_id") or "") for r in _live(records)})
            lines.append(
                f"| {str(config.get('key', ''))[:12]} | {runner_label(config)} | {_model_cell(config)} | {tasks} | "
                f"{_rate_words(s)} | {per_repeat or '-'} | {s['passed']} / {s['scored']} | "
                f"{_secs(u['wall_s_total'])} ({_secs(u['wall_s_mean'])}) | "
                f"{exact_words(v, 'claude')} | {measured_words(u['measured'], 'claude', v)} | "
                f"{exact_words(v, 'codex')} | {measured_words(u['measured'], 'codex', v)} | "
                f"{(_tok(local['tokens_in']) + ' in / ' + _tok(local['tokens_out']) + ' out') if local else 'none'} |"
            )
        handed = handed_line(t_records)
        lines += [""] + ([f"**The team at work:** {handed}", ""] if handed else [])
        compares = compares_line(t_config, alone[0]) if alone is not None else ""
        lines += ([compares, ""] if compares else [])
        lines += [f"**The answer:** {answer((t_config, t_records), alone)}", ""]
        # v1.402.0 (the autonomy metrics): how often each would have asked a
        # person sitting there ("Ask when risky", as a fresh install asks).
        asks = asks_line(t_s, next((s for c, _r, s in configs if alone is not None and c is alone[0]), None),
                         alone_label(t_config))
        lines += ([asks, ""] if asks else [])
        differ = _differing_tasks((t_config, t_records), alone)
        if differ:
            lines += [f"Tasks where they differ (team / {alone_label(t_config)} alone, passed of scored):", *differ, ""]
        u = summarize_usage(t_config, t_records)
        who = who_lines(u)
        if who:
            lines += ["Who did what:", "", *who, ""]
        rec = record_lines(u, t_records)
        if rec:
            lines += ["What the team router learned (the numbers to tune from):", "", *rec, ""]
    lines += [MEASURED_LIMITS]
    return lines


def asks_line(team_s: Mapping[str, Any], alone_s: "Mapping[str, Any] | None", alone_name: str) -> str:
    """v1.402.0: "Would have asked you: the team 1.8 times per task; <planner>
    alone 2.1." — "" when neither recorded its calls."""
    def per(s: "Mapping[str, Any] | None") -> str:
        value = (s or {}).get("would_ask_per_task")
        return "not known" if value is None else f"{float(value):.1f} times per task"

    if not (team_s or {}).get("would_ask_runs") and not (alone_s or {}).get("would_ask_runs"):
        return ""
    words = f"**Would have asked you:** the team {per(team_s)}"
    if alone_s is not None:
        words += f"; {alone_name} alone {per(alone_s)}"
    return words + " (\"Ask when risky\", as a fresh install asks; every card answered \"Allow once\")."


def _differing_tasks(team: tuple[Mapping[str, Any], list[Mapping[str, Any]]],
                     alone: tuple[Mapping[str, Any], list[Mapping[str, Any]]] | None) -> list[str]:
    if alone is None:
        return []
    t_by = {_key(r): r for r in _live(team[1])}
    a_by = {_key(r): r for r in _live(alone[1])}
    both = set(t_by) & set(a_by)
    out = []
    for task in sorted({k[0] for k in both}):
        keys = [k for k in both if k[0] == task]
        tx, ty = _scored(t_by[k] for k in keys)
        ax, ay = _scored(a_by[k] for k in keys)
        if (tx, ty) != (ax, ay):
            out.append(f"- {task}: {tx}/{ty} / {ax}/{ay}")
    return out
