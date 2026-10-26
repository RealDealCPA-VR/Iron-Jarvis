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
    then the one sharing the most runs with the team, the newest on a tie."""
    team_keys = {_key(r) for r in _live(team_records)}
    best = None
    for config, records in configs:
        if config.get("runner") != "jarvis":
            continue
        if (config.get("provider"), config.get("model")) != (team_config.get("provider"), team_config.get("model")):
            continue
        shared = len(team_keys & {_key(r) for r in _live(records)})
        # The same effort as the team's planner first (another effort is
        # another setup), then the most runs in common, then the newest.
        same_effort = str(config.get("effort") or "") == str(team_config.get("effort") or "")
        rank = (same_effort, shared, str(config.get("created_at") or ""))
        if best is None or rank > best[0]:
            best = (rank, config, records)
    return (best[1], best[2]) if best else None


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
    x, y = _scored(t_by[k] for k in both)
    a, b = _scored(a_by[k] for k in both)
    if not y or not b:
        return f"{NOT_ENOUGH}: {'the team' if not y else label + ' alone'} has no scored run among the runs both made."
    line = f"passed {x} of {y} vs {label} alone {a} of {b}"
    vendor = _vendor_of_planner(str(t_config.get("provider") or ""))
    if vendor not in ("claude", "codex"):
        return line + f" (over the {len(both)} runs both made; the planner has no list price to compare)."
    t_cost = _vendor_list_price(t_config, [t_by[k] for k in both], vendor)
    a_cost = _vendor_list_price(a_config, [a_by[k] for k in both], vendor)
    name = {"claude": "Claude", "codex": "Codex"}[vendor]
    if t_cost is None or a_cost is None:
        return line + f" (over the {len(both)} runs both made); {name} list-price is not known for every run, so the usage is not compared."
    if a_cost <= 0:
        return line + f" (over the {len(both)} runs both made); {label} alone used no {name} list-price, so there is no percent to compare."
    pct = (a_cost - t_cost) / a_cost * 100
    more = "less" if pct >= 0 else "more"
    return (line + f", using {abs(pct):.0f}% {more} {name} list-price "
            f"({_money(t_cost)} vs {_money(a_cost)}, over the {len(both)} runs both made).")


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


def record_lines(u: Mapping[str, Any]) -> list[str]:
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
    return lines + ["", RECORD_NOTE]


def usage_section(config: Mapping[str, Any], records: list[Mapping[str, Any]]) -> list[str]:
    """A setup's own usage, who did what and the router's record (its
    ``report.md``). Empty for a setup whose runs carry neither parts nor
    readings (a run written before v1.396.0 reads as it always did)."""
    live = _live(records)
    if not any(isinstance(r.get("parts"), Mapping) or isinstance(r.get("accounts"), Mapping) for r in live):
        return []
    u = summarize_usage(config, live)
    vendors = u["vendors"]
    lines = [
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
    rec = record_lines(u)
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
        peers = [(c, r, s) for c, r, s in configs
                 if c.get("runner") != TEAM_RUNNER and _tiers_and_repeats(r) == shape]
        alone = find_alone(t_config, t_records, pairs)
        tiers = ", ".join(sorted(shape[0])) or "-"
        lines += [
            f"**Team {str(t_config.get('key', ''))[:12]}** (planner {_model_cell(t_config)}; tier {tiers}; "
            f"{shape[1]} repeat(s)).",
            "",
            "| Setup | Runner | Model | Tasks | Pass rate (range) | Per repeat | Passed / scored | Time (a run) "
            "| Claude exact | Claude measured | Codex exact | Codex measured | Local tokens |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for config, records, s in [(t_config, t_records, t_s), *peers]:
            u = summarize_usage(config, records)
            v = u["vendors"]
            local = v.get("local")
            per_repeat = ", ".join(f"{p['passed']}/{p['scored']}" for p in (s.get("per_repeat") or {}).values())
            # A row's numbers are over ITS runs: a setup that ran more tasks
            # says so here (the answer below is over the runs both made).
            tasks = len({str(r.get("task_id") or "") for r in _live(records)})
            lines.append(
                f"| {str(config.get('key', ''))[:12]} | {config.get('runner')} | {_model_cell(config)} | {tasks} | "
                f"{_rate_words(s)} | {per_repeat or '-'} | {s['passed']} / {s['scored']} | "
                f"{_secs(u['wall_s_total'])} ({_secs(u['wall_s_mean'])}) | "
                f"{exact_words(v, 'claude')} | {measured_words(u['measured'], 'claude', v)} | "
                f"{exact_words(v, 'codex')} | {measured_words(u['measured'], 'codex', v)} | "
                f"{(_tok(local['tokens_in']) + ' in / ' + _tok(local['tokens_out']) + ' out') if local else 'none'} |"
            )
        lines += ["", f"**The answer:** {answer((t_config, t_records), alone)}", ""]
        differ = _differing_tasks((t_config, t_records), alone)
        if differ:
            lines += [f"Tasks where they differ (team / {alone_label(t_config)} alone, passed of scored):", *differ, ""]
        u = summarize_usage(t_config, t_records)
        who = who_lines(u)
        if who:
            lines += ["Who did what:", "", *who, ""]
        rec = record_lines(u)
        if rec:
            lines += ["What the team router learned (the numbers to tune from):", "", *rec, ""]
    lines += [MEASURED_LIMITS]
    return lines


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
