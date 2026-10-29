"""The benchmark's reports, in plain words.

* :func:`summarize` — one configuration's numbers from its run records. The
  pass rate counts SCORED runs only: an unscored run (its check could not
  run) leaves the denominator, and the count of unscored runs is always shown
  beside the rate (Harness² principle 4).
* :func:`config_report` — ``report.md`` for one configuration.
* :func:`combined_report` — ONE table across every configuration found under
  ``<home>/harness-bench/``, plus pass rates per category.

Cost is shown as two columns that are never added together: metered money and
the list-price equivalent of subscription work (v1.300.0). An unknown number
is "n/a", never 0.

v1.383.0 (IJ harness part 3, Phase 1):

* REPEATS. When a configuration ran its tasks more than once (``--repeats``),
  each repeat gets its own pass rate — its unscored and "did not run" runs
  leave THAT repeat's denominator, and the report counts them — and the
  configuration is given the MEAN of those rates with its lowest-to-highest
  RANGE, plus per-task counts ("3/3", "2/3", "0/3") sorted into passed every
  time / some of the time / never. Means of time, tool calls, tokens and cost
  are over every run, as before. A configuration that ran once reads as it
  always did: none of this appears.
* v1.383.0 review: "over N repeats" counts the repeats that had a scored run
  ("over 1 of 2 repeats (1 had no scored run)"); and a run of a task this
  invocation ran, from a repeat beyond this invocation's count (left by an
  earlier invocation that asked for more), is STALE: left out of every number
  and said in one line (``bench`` marks it; results.json keeps it).
* v1.396.0 (the long-jobs plan's Phase 7, MEASURED): a Jarvis setup's report
  gains its USAGE (exact tokens and list price per vendor; the measured
  percent of each subscription window), WHO DID WHAT and the team router's
  record; the combined report sets every TEAM setup beside the single-model
  setups of the same tiers and repeats and ANSWERS the plan's question from
  the numbers both hold (:mod:`.team_report`).
* THE EXACT MODEL. Every report names the model AS PASSED (and the effort),
  what the runs REPORTED they ran on ("model not reported" when a run named
  none — never "default"), and flags each run whose reported model differs
  (:func:`model_matches`: an alias such as ``sonnet`` matches the full id it
  stands for, ``claude-sonnet-4-6``; a dated snapshot matches its name).
"""

from __future__ import annotations

import re
from collections import Counter, OrderedDict
from typing import Any, Iterable, Mapping

from .catalog import CATEGORIES, TIERS
from .team_report import runner_label


def _mean(values: Iterable[Any]) -> float | None:
    nums = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return sum(nums) / len(nums) if nums else None


def _sum_known(values: Iterable[Any]) -> tuple[float, int]:
    """(sum of the known values, how many were unknown)."""
    total, unknown = 0.0, 0
    for v in values:
        if v is None:
            unknown += 1
        else:
            total += float(v)
    return total, unknown


#: What the "files touched outside" column can and cannot see — said in every
#: report, so a clean column is never read as "nothing outside was touched".
OUTSIDE_SCOPE = (
    "\"Touched outside its folder\" watches only the benchmark's own folder for "
    "this setup (everything in it but the run's own copy); a write anywhere else "
    "on this PC is not seen."
)
#: Said wherever a run named no model (Codex's events carry none).
NOT_REPORTED = "model not reported"
#: Said where a configuration's model is missing (an older record).
NOT_NAMED = "model not named"


def _unscored_by(r: Mapping[str, Any]) -> str:
    """Why a record without a verdict has none: ``infrastructure`` (the run
    never got a fair go), else the grader (its check could not run). Records
    written before the distinction existed count as the grader's."""
    return "infrastructure" if r.get("unscored_by") == "infrastructure" else "grader"


def repeat_of(r: Mapping[str, Any]) -> int:
    """The repeat a record belongs to (a record with none is repeat 1)."""
    try:
        return max(1, int(r.get("repeat") or 1))
    except (TypeError, ValueError):
        return 1


# --------------------------------------------------------------------------- #
# the model as passed and as reported
# --------------------------------------------------------------------------- #

#: Words a model name is split into to compare an alias with a full id.
_WORDS = re.compile(r"[a-z0-9.]+")
#: A dated snapshot suffix (``-20250929``, ``-2025-09-29``).
_SNAPSHOT = re.compile(r"-(\d{8}|\d{4}-\d{2}-\d{2})$")
#: A version written with a dot between two digits (``4.5``) — the same
#: version another vendor writes with a dash (``4-5``).
_DOTTED = re.compile(r"(?<=\d)\.(?=\d)")


def _plain_model(name: str) -> tuple[str, str]:
    """``(name, snapshot date)`` of a model id: lowered, without Claude's
    ``[1m]`` context tag, a provider prefix or an ``-latest`` alias suffix,
    with dotted versions written with dashes; the date is ``""`` when the id
    carries none."""
    text = (name or "").strip().lower()
    text = text.removesuffix("[1m]")
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    text = _DOTTED.sub("-", text).removesuffix("-latest")
    m = _SNAPSHOT.search(text)
    if m is None:
        return text, ""
    return text[: m.start()], m.group(1).replace("-", "")


def model_matches(passed: str | None, reported: str | None) -> bool:
    """Whether the model a run REPORTED is the one it was PASSED: the same
    name; the same name with a dated snapshot or a provider prefix, written
    with a dot or a dash (``4.5`` / ``4-5``), or behind an ``-latest`` alias;
    or an alias with no version in it (``sonnet``, ``opus``) that is one of
    the reported id's words (``claude-sonnet-4-6``). Two DIFFERENT snapshot
    dates of one name are two models (v1.383.0 review: ``…-20240620`` and
    ``…-20241022`` used to match). A run that reported nothing never
    "differs" — it is listed as not reported instead."""
    if not reported or not passed:
        return True
    (a, a_date), (b, b_date) = _plain_model(passed), _plain_model(reported)
    if a_date and b_date and a_date != b_date:
        return False
    if a == b:
        return True
    if not any(ch.isdigit() for ch in a):
        return set(_WORDS.findall(a)) <= set(_WORDS.findall(b.replace("-", " ")))
    return False


def _model_facts(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    """What the runs were passed, what they reported, and every run whose
    reported model differs from the one passed."""
    passed = Counter(str(r.get("model_passed") or "") for r in records if r.get("model_passed"))
    efforts = Counter(str(r.get("effort_passed")) for r in records if r.get("effort_passed"))
    reported: Counter[str] = Counter()
    not_reported = 0
    mismatches: list[dict[str, Any]] = []
    for r in records:
        if not r.get("model_passed") and "model_reported" not in r:
            continue  # a record from before v1.383.0: nothing to say
        got = r.get("model_reported")
        if got:
            reported[str(got)] += 1
        else:
            not_reported += 1
        if got and not model_matches(r.get("model_passed"), got):
            mismatches.append({
                "task_id": r.get("task_id"), "repeat": repeat_of(r),
                "passed": r.get("model_passed"), "reported": got,
            })
    return {
        "models_passed": dict(passed), "efforts_passed": dict(efforts),
        "models_reported": dict(reported), "model_not_reported": not_reported,
        "model_mismatches": mismatches,
    }


# --------------------------------------------------------------------------- #
# the numbers
# --------------------------------------------------------------------------- #


def _rates(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if r.get("passed") is not None]
    unscored = [r for r in rows if r.get("passed") is None]
    passed = sum(1 for r in scored if r.get("passed") is True)
    return {
        "runs": len(rows),
        "scored": len(scored),
        "passed": passed,
        "unscored": len(unscored),
        "unscored_infrastructure": sum(1 for r in unscored if _unscored_by(r) == "infrastructure"),
        "unscored_grader": sum(1 for r in unscored if _unscored_by(r) == "grader"),
        "pass_rate": (passed / len(scored)) if scored else None,
    }


def _repeats(records: list[Mapping[str, Any]], interrupted: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Per repeat: its own rate (its unscored runs leave ITS denominator);
    across repeats: the mean of those rates and their range; per task: how
    many of its scored runs passed, sorted into every time / some / never."""
    numbers = sorted({repeat_of(r) for r in [*records, *interrupted]} or {1})
    per_repeat: "OrderedDict[int, dict[str, Any]]" = OrderedDict()
    for k in numbers:
        rows = [r for r in records if repeat_of(r) == k]
        per_repeat[k] = {**_rates(rows), "interrupted": sum(1 for r in interrupted if repeat_of(r) == k)}
    rates = [v["pass_rate"] for v in per_repeat.values() if v["pass_rate"] is not None]
    #: (review) how many repeats had a scored run — the ones the mean and the
    #: range are over.
    scored_repeats = len(rates)
    per_task: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    for r in records:
        t = per_task.setdefault(str(r.get("task_id")), {"runs": 0, "scored": 0, "passed": 0, "unscored": 0})
        t["runs"] += 1
        if r.get("passed") is None:
            t["unscored"] += 1
        else:
            t["scored"] += 1
            t["passed"] += 1 if r.get("passed") is True else 0
    groups: dict[str, list[str]] = {"every_time": [], "some_of_the_time": [], "never": [], "not_scored": []}
    for task_id, t in per_task.items():
        if not t["scored"]:
            groups["not_scored"].append(task_id)
        elif t["passed"] == t["scored"]:
            groups["every_time"].append(task_id)
        elif t["passed"]:
            groups["some_of_the_time"].append(task_id)
        else:
            groups["never"].append(task_id)
    return {
        "repeats": max(numbers),
        "scored_repeats": scored_repeats,
        "per_repeat": dict(per_repeat),
        "mean_pass_rate": (sum(rates) / len(rates)) if rates else None,
        "range": (min(rates), max(rates)) if rates else None,
        "per_task": dict(per_task),
        "task_groups": groups,
    }


def _asks(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    """v1.402.0 (the autonomy metrics): how often a person sitting there WOULD
    HAVE BEEN ASKED (``facts.would_have_asked`` — every call replayed through
    the real permission engine under the default posture, no grants), and the
    cards / questions the runner answered itself (``facts.asks``, Jarvis).
    ``per_task`` is over the runs that recorded their calls."""
    rows = [
        (r.get("facts") or {}).get("would_have_asked") for r in records
    ]
    rows = [w for w in rows if isinstance(w, Mapping)]
    asked = sum(int(w.get("asked") or 0) for w in rows)
    not_known = sum(int(w.get("not_known") or 0) for w in rows)
    by_tool: dict[str, int] = {}
    for w in rows:
        for tool, n in (w.get("by_tool") or {}).items():
            by_tool[str(tool)] = by_tool.get(str(tool), 0) + int(n or 0)
    answered = [(r.get("facts") or {}).get("asks") for r in records]
    answered = [a for a in answered if isinstance(a, Mapping)]
    return {
        "would_ask_runs": len(rows),
        "would_ask": asked,
        "would_ask_not_known": not_known,
        "would_ask_per_task": (asked / len(rows)) if rows else None,
        "would_ask_by_tool": dict(sorted(by_tool.items(), key=lambda kv: (-kv[1], kv[0]))[:5]),
        "auto_answered": sum(int(a.get("cards") or 0) + int(a.get("questions") or 0) for a in answered),
    }


def asks_words(s: Mapping[str, Any]) -> str:
    """One sentence: "Would have asked you 2.3 times per task …" — or what is
    not known."""
    if not s.get("would_ask_runs"):
        return "Would have asked you: not known (no run recorded its calls)."
    per = s.get("would_ask_per_task")
    tools = ", ".join(f"{t} {n}" for t, n in (s.get("would_ask_by_tool") or {}).items())
    words = (
        f"Would have asked you {per:.1f} times per task (\"Ask first\", how a fresh install "
        f"asks, every card answered \"Allow once\": one card per round for calls of one kind, no "
        f"grants; over {s['would_ask_runs']} run(s)"
        + (f"; most often for {tools}" if tools else "")
        + ")."
    )
    if s.get("would_ask_not_known"):
        words += f" {s['would_ask_not_known']} call(s) could not be mapped to a Jarvis tool: not known."
    if s.get("auto_answered"):
        words += f" The benchmark answered {s['auto_answered']} card(s) or question(s) itself."
    return words


def summarize(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    # (review) A STALE run — a repeat beyond this invocation's count, left by
    # an earlier one — counts in nothing; the reports say how many there were.
    stale = [r for r in records if r.get("stale")]
    records = [r for r in records if not r.get("stale")]
    interrupted = [r for r in records if r.get("interrupted")]
    records = [r for r in records if not r.get("interrupted")]
    scored = [r for r in records if r.get("passed") is not None]
    unscored = [r for r in records if r.get("passed") is None]
    passed = sum(1 for r in scored if r.get("passed") is True)
    per_category: "OrderedDict[str, dict[str, int]]" = OrderedDict()
    for category in CATEGORIES:
        rows = [r for r in records if r.get("category") == category]
        if not rows:
            continue
        cat_scored = [r for r in rows if r.get("passed") is not None]
        per_category[category] = {
            "tasks": len(rows),
            "scored": len(cat_scored),
            "passed": sum(1 for r in cat_scored if r.get("passed") is True),
            "unscored": len(rows) - len(cat_scored),
        }
    per_tier: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    for tier in TIERS:
        # A record from before tiers existed is a core task's.
        rows = [r for r in records if (r.get("tier") or "core") == tier]
        if not rows:
            continue
        tier_scored = [r for r in rows if r.get("passed") is not None]
        tier_passed = sum(1 for r in tier_scored if r.get("passed") is True)
        per_tier[tier] = {
            "tasks": len(rows),
            "scored": len(tier_scored),
            "passed": tier_passed,
            "unscored": len(rows) - len(tier_scored),
            "pass_rate": (tier_passed / len(tier_scored)) if tier_scored else None,
        }
    tokens_in, tin_unknown = _sum_known(r.get("tokens_in") for r in records)
    tokens_out, tout_unknown = _sum_known(r.get("tokens_out") for r in records)
    metered, metered_unknown = _sum_known(r.get("cost_metered_usd") for r in records)
    listed, listed_unknown = _sum_known(r.get("cost_list_price_usd") for r in records)
    return {
        "tasks": len(records),
        "scored": len(scored),
        "passed": passed,
        "failed": len(scored) - passed,
        "unscored": len(unscored),
        "unscored_infrastructure": sum(1 for r in unscored if _unscored_by(r) == "infrastructure"),
        "unscored_grader": sum(1 for r in unscored if _unscored_by(r) == "grader"),
        "interrupted": len(interrupted),
        "interrupted_tasks": [r.get("task_id") for r in interrupted],
        "pass_rate": (passed / len(scored)) if scored else None,
        "hit_a_limit": sum(1 for r in records if r.get("failure_kind") == "agent"),
        "reused": sum(1 for r in records if r.get("reused")),
        "mean_tool_calls": _mean(r.get("tool_calls") for r in records),
        "mean_model_rounds": _mean(r.get("model_rounds") for r in records),
        "mean_wall_s": _mean(r.get("wall_s") for r in records),
        "tokens_in": int(tokens_in), "tokens_in_unknown": tin_unknown,
        "tokens_out": int(tokens_out), "tokens_out_unknown": tout_unknown,
        "cost_metered_usd": metered, "cost_metered_unknown": metered_unknown,
        "cost_list_price_usd": listed, "cost_list_price_unknown": listed_unknown,
        "per_category": dict(per_category),
        "per_tier": dict(per_tier),
        "files_outside": sum(1 for r in records if r.get("files_outside")),
        "stale": len(stale),
        "stale_repeats": sorted({repeat_of(r) for r in stale}),
        **_repeats(records, interrupted),
        **_model_facts(records),
        **_asks(records),
    }


# --------------------------------------------------------------------------- #
# words
# --------------------------------------------------------------------------- #


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def _num(value: float | None, fmt: str = "{:.1f}") -> str:
    return "n/a" if value is None else fmt.format(value)


def _money(total: float, unknown: int, count: int) -> str:
    if count and unknown == count:
        return "n/a"
    text = f"${total:.4f}"
    return text + (f" (+{unknown} n/a)" if unknown else "")


def _tokens(total: int, unknown: int, count: int) -> str:
    if count and unknown == count:
        return "n/a"
    return f"{total:,}" + (f" (+{unknown} n/a)" if unknown else "")


def _result_word(r: Mapping[str, Any]) -> str:
    if r.get("interrupted"):
        return "interrupted"
    if r.get("passed") is True:
        return "passed"
    if r.get("passed") is False:
        return "failed"
    if _unscored_by(r) == "infrastructure":
        return "did not run"
    return "unscored"


def _label(config: Mapping[str, Any]) -> str:
    who = runner_label(config)
    provider = config.get("provider") or ""
    model = config.get("model") or NOT_NAMED
    return f"{who} · {provider + ' / ' if provider else ''}{model}"


def _model_cell(config: Mapping[str, Any]) -> str:
    """``provider / model`` as passed — never "default"."""
    provider = config.get("provider") or ""
    return provider + (" / " if provider else "") + (config.get("model") or NOT_NAMED)


def _effort_cell(config: Mapping[str, Any], s: Mapping[str, Any]) -> str:
    """The effort as passed: what the runs say they were passed (``not
    offered`` for a Jarvis pair that takes no level), else the
    configuration's, else "not set"."""
    efforts = s.get("efforts_passed") or {}
    if efforts:
        return ", ".join(sorted(efforts))
    return str(config.get("effort") or "not set")


def _reported_cell(s: Mapping[str, Any]) -> str:
    """What the runs reported they ran on, with counts when they disagree."""
    reported = s.get("models_reported") or {}
    missing = int(s.get("model_not_reported") or 0)
    parts = [f"{name} ({n})" if len(reported) > 1 or missing else name for name, n in sorted(reported.items())]
    if missing:
        parts.append(f"{NOT_REPORTED} ({missing})" if reported else NOT_REPORTED)
    return ", ".join(parts) or "n/a"


def _mismatch_words(s: Mapping[str, Any]) -> str:
    rows = s.get("model_mismatches") or []
    if not rows:
        return "none"
    return f"{len(rows)}: " + "; ".join(
        f"{m['task_id']}" + (f" repeat {m['repeat']}" if m["repeat"] > 1 else "")
        + f" reported {m['reported']}" for m in rows[:5]
    ) + ("; …" if len(rows) > 5 else "")


def _model_sentence(config: Mapping[str, Any], s: Mapping[str, Any]) -> str:
    mismatches = s.get("model_mismatches") or []
    flag = ""
    if mismatches:
        flag = (f" **{len(mismatches)} run(s) reported a different model than the one passed** "
                f"({_mismatch_words(s)}).")
    return (
        f"Model as passed: {_model_cell(config)}, effort {_effort_cell(config, s)}. "
        f"Model the runs reported: {_reported_cell(s)}." + flag
    )


def _range_words(s: Mapping[str, Any]) -> str:
    rng = s.get("range")
    if s.get("repeats", 1) <= 1:
        return "one run"
    if rng is None:
        return "n/a"
    return f"{_pct(rng[0])}–{_pct(rng[1])}"


def _repeats_words(s: Mapping[str, Any]) -> str:
    """``"3 repeats"`` — or ``"1 of 2 repeats (1 had no scored run)"`` when a
    repeat had no scored run (the mean and the range are over the others)."""
    n, k = s["repeats"], s.get("scored_repeats", s["repeats"])
    return f"{n} repeats" if k >= n else f"{k} of {n} repeats ({n - k} had no scored run)"


def _repeats_cell(s: Mapping[str, Any]) -> str:
    n, k = s["repeats"], s.get("scored_repeats", s["repeats"])
    return f"{n}" if k >= n else f"{n} ({n - k} with no scored run)"


def _stale_words(s: Mapping[str, Any]) -> str:
    """``"repeat 3"`` / ``"repeats 2–3"`` / ``"repeats 2, 4"``: the repeats the
    stale runs belong to."""
    reps = list(s.get("stale_repeats") or [])
    if len(reps) == 1:
        return f"repeat {reps[0]}"
    if reps == list(range(reps[0], reps[-1] + 1)):
        return f"repeats {reps[0]}–{reps[-1]}"
    return "repeats " + ", ".join(str(k) for k in reps)


def _stale_line(s: Mapping[str, Any]) -> str:
    return (f"Left out of these numbers: {s['stale']} run(s) of {_stale_words(s)} from an earlier "
            "invocation that asked for more repeats than this one (kept in results.json, marked stale).")


def _task_count(t: Mapping[str, Any]) -> str:
    return f"{t['passed']}/{t['scored']}" + (f" (+{t['unscored']} unscored)" if t["unscored"] else "")


def _repeat_section(s: Mapping[str, Any]) -> list[str]:
    """The "across repeats" part of a configuration's report."""
    n = s["repeats"]
    lines = [
        "",
        f"## Across {n} repeats",
        "",
        f"Mean pass rate {_pct(s['mean_pass_rate'])}, ranging from {_range_words(s)} across the "
        "repeats. Each repeat's rate counts its scored runs only: its unscored and \"did not run\" "
        "runs leave that repeat's denominator.",
        "",
        "| Repeat | Pass rate | Passed / scored | Did not run | Check could not run | Interrupted |",
        "|---|---|---|---|---|---|",
    ]
    for k, v in s["per_repeat"].items():
        lines.append(
            f"| {k} | {_pct(v['pass_rate'])} | {v['passed']} / {v['scored']} | "
            f"{v['unscored_infrastructure']} | {v['unscored_grader']} | {v['interrupted']} |"
        )
    lines += ["", *_task_group_lines(s)]
    return lines


def _task_group_lines(s: Mapping[str, Any]) -> list[str]:
    per_task = s["per_task"]
    groups = s["task_groups"]
    words = {
        "every_time": "Passed every time", "some_of_the_time": "Passed some of the time",
        "never": "Never passed", "not_scored": "Never scored",
    }
    lines = []
    for key in ("every_time", "some_of_the_time", "never", "not_scored"):
        ids = groups.get(key) or []
        if key == "not_scored" and not ids:
            continue
        listed = ", ".join(f"{i} ({_task_count(per_task[i])})" for i in ids) or "none"
        lines.append(f"- {words[key]} ({len(ids)}): {listed}")
    return lines


def config_report(config: Mapping[str, Any], records: list[Mapping[str, Any]]) -> str:
    s = summarize(records)
    repeated = s["repeats"] > 1
    head = (
        f"**Mean pass rate {_pct(s['mean_pass_rate'])}, range {_range_words(s)} over {_repeats_words(s)}**"
        if repeated else f"**Pass rate {_pct(s['pass_rate'])}**"
    )
    of = "scored runs" if repeated else "scored"
    lines = [
        f"# Harness benchmark: {_label(config)}",
        "",
        f"Iron Jarvis {config.get('version', '?')} · seed {config.get('seed', 0)} · "
        f"configuration {str(config.get('key', ''))[:12]}",
        "",
        f"{head} — {s['passed']} passed, {s['failed']} failed "
        f"of {s['scored']} {of}; {s['unscored']} unscored (left out of the rate): "
        f"{s['unscored_infrastructure']} did not run (infrastructure: a limit, an unreachable "
        f"server, a refused sign-in, another model answering) and {s['unscored_grader']} where "
        f"the check could not run; {s['hit_a_limit']} stopped on their own (a time or round limit, "
        f"or part-way after starting work; scored); {s['interrupted']} interrupted; "
        f"{s['reused']} reused from an earlier run.",
        "",
        *([_stale_line(s), ""] if s["stale"] else []),
        _model_sentence(config, s),
        "",
        OUTSIDE_SCOPE,
        "",
        ("| Task | Repeat | Category | Result | Tool calls | Rounds | Time (s) | Tokens in | Tokens out "
         "| Metered $ | List-price $ | Note |" if repeated else
         "| Task | Category | Result | Tool calls | Rounds | Time (s) | Tokens in | Tokens out | Metered $ "
         "| List-price $ | Note |"),
        "|---|---|---|---|---|---|---|---|---|---|---|---|" if repeated else "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    shown = [r for r in records if not r.get("stale")]
    order = sorted(shown, key=lambda r: (repeat_of(r), str(r.get("task_id")))) if repeated else shown
    for r in order:
        rep = f" {repeat_of(r)} |" if repeated else ""
        if r.get("interrupted"):
            lines.append(
                f"| {r.get('task_id')} |{rep} {r.get('category') or '?'} | interrupted | n/a | n/a | n/a "
                "| n/a | n/a | n/a | n/a | the run started but was never recorded (it was stopped part-way) |"
            )
            continue
        note = r.get("error") or ("; ".join(r.get("notes") or []))
        if r.get("passed") is None and _unscored_by(r) == "infrastructure":
            note = str(r.get("grade_reason") or "did not run") + (f" ({note})" if note else "")
        elif r.get("passed") is None and r.get("grade_reason"):
            note = (note + "; " if note else "") + str(r.get("grade_reason"))
        if r.get("files_outside"):
            note = (note + "; " if note else "") + f"touched {len(r['files_outside'])} file(s) outside its folder"
        if r.get("model_reported") and not model_matches(r.get("model_passed"), r.get("model_reported")):
            note = (f"reported model {r.get('model_reported')}, not {r.get('model_passed')}"
                    + ("; " + note if note else ""))
        lines.append(
            f"| {r.get('task_id')} |{rep} {r.get('category')} | {_result_word(r)} | "
            f"{_num(r.get('tool_calls'), '{:.0f}')} | {_num(r.get('model_rounds'), '{:.0f}')} | "
            f"{_num(r.get('wall_s'))} | {_num(r.get('tokens_in'), '{:,.0f}')} | "
            f"{_num(r.get('tokens_out'), '{:,.0f}')} | {_num(r.get('cost_metered_usd'), '{:.4f}')} | "
            f"{_num(r.get('cost_list_price_usd'), '{:.4f}')} | {str(note).replace('|', '/')[:160]} |"
        )
    if repeated:
        lines += _repeat_section(s)
    lines += ["", "## By tier", "", "| Tier | Pass rate | Passed / scored | Unscored |", "|---|---|---|---|"]
    for tier, t in s["per_tier"].items():
        lines.append(f"| {tier} | {_pct(t['pass_rate'])} | {t['passed']} / {t['scored']} | {t['unscored']} |")
    lines += ["", "## By category", "", "| Category | Passed / scored | Unscored |", "|---|---|---|"]
    for category, c in s["per_category"].items():
        lines.append(f"| {category} | {c['passed']} / {c['scored']} | {c['unscored']} |")
    # v1.396.0: usage per vendor, who did what, the router's record (a
    # setup whose runs carry neither parts nor readings adds nothing).
    from .team_report import usage_section

    lines += usage_section(config, records)
    lines += [
        "",
        f"Means per run: {_num(s['mean_tool_calls'])} tool calls, {_num(s['mean_model_rounds'])} model "
        f"rounds, {_num(s['mean_wall_s'])} s. Tokens: {_tokens(s['tokens_in'], s['tokens_in_unknown'], s['tasks'])} in, "
        f"{_tokens(s['tokens_out'], s['tokens_out_unknown'], s['tasks'])} out. Cost: "
        f"{_money(s['cost_metered_usd'], s['cost_metered_unknown'], s['tasks'])} metered, "
        f"{_money(s['cost_list_price_usd'], s['cost_list_price_unknown'], s['tasks'])} at list price "
        "(subscription work, not billed).",
        "",
        asks_words(s),
        "",
    ]
    return "\n".join(lines)


def _setup(config: Mapping[str, Any]) -> str:
    """The Setup cell: the configuration's fingerprint, and the tool dialect
    a Jarvis setup spoke (v1.366.0) so two dialect rows read apart."""
    key = str(config.get("key", ""))[:12]
    harness = config.get("harness")
    dialect = harness.get("dialect") if isinstance(harness, Mapping) else None
    # v1.370.0: a refine probe ran a CANDIDATE harness layer — said, so its
    # row never reads as the default harness.
    layer = isinstance(harness, Mapping) and bool(harness.get("layer_sha256"))
    words = ", ".join(w for w in (f"{dialect} dialect" if dialect else "", "candidate layer" if layer else "") if w)
    return f"{key} ({words})" if words else key


def combined_report(configs: list[tuple[Mapping[str, Any], list[Mapping[str, Any]]]]) -> str:
    summaries = [(config, records, summarize(records)) for config, records in configs]
    repeated = any(s["repeats"] > 1 for _c, _r, s in summaries)
    lines = [
        "# Harness benchmark: every configuration",
        "",
        "Pass rates count scored runs only. Unscored runs are listed beside them in two kinds: "
        "\"Did not run\" (infrastructure: a usage or rate limit, an unreachable or overloaded "
        "server, a refused sign-in, the demo model or another provider answering, the CLI "
        "missing) and \"Check could not run\" (the grader). A run that reached its own time or "
        "round limit, or stopped part-way after it had started work, is scored. Metered money "
        "and list-price equivalents are separate columns. "
        "Setup is the configuration's fingerprint: rows with the same runner and model but a "
        "different setup ran on a different harness or Iron Jarvis version. Model is the model "
        "as passed to the runner.",
        "",
        OUTSIDE_SCOPE,
        "",
    ]
    if repeated:
        lines += [
            "Where a setup ran its tasks more than once, Pass rate is the mean of its repeats' rates "
            "and Range is the lowest to the highest of them; Passed / scored counts every run.",
            "",
            "| Runner | Model | Effort | Iron Jarvis | Setup | Tasks | Repeats | Passed / scored | Pass rate | "
            "Range | Did not run | Check could not run | Interrupted | Mean tool calls | Mean rounds | "
            "Mean time (s) | Tokens in | Tokens out | Metered $ | List-price $ |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
    else:
        lines += [
            "| Runner | Model | Effort | Iron Jarvis | Setup | Tasks | Passed / scored | Pass rate | Did not run | "
            "Check could not run | Interrupted | Mean tool calls | Mean rounds | Mean time (s) | "
            "Tokens in | Tokens out | Metered $ | List-price $ |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
    for config, _records, s in summaries:
        tasks = len(s["per_task"]) if repeated else s["tasks"]
        middle = (
            f"{tasks} | {_repeats_cell(s)} | {s['passed']} / {s['scored']} | "
            f"{_pct(s['mean_pass_rate'] if s['repeats'] > 1 else s['pass_rate'])} | {_range_words(s)} | "
            if repeated else f"{s['tasks']} | {s['passed']} / {s['scored']} | {_pct(s['pass_rate'])} | "
        )
        lines.append(
            f"| {runner_label(config)} | {_model_cell(config)} | {_effort_cell(config, s)} | "
            f"{config.get('version', '?')} | {_setup(config)} | {middle}{s['unscored_infrastructure']} | "
            f"{s['unscored_grader']} | {s['interrupted']} | "
            f"{_num(s['mean_tool_calls'])} | {_num(s['mean_model_rounds'])} | "
            f"{_num(s['mean_wall_s'])} | {_tokens(s['tokens_in'], s['tokens_in_unknown'], s['tasks'])} | "
            f"{_tokens(s['tokens_out'], s['tokens_out_unknown'], s['tasks'])} | "
            f"{_money(s['cost_metered_usd'], s['cost_metered_unknown'], s['tasks'])} | "
            f"{_money(s['cost_list_price_usd'], s['cost_list_price_unknown'], s['tasks'])} |"
        )
    stale = [(config, s) for config, _records, s in summaries if s["stale"]]
    if stale:
        lines += [""] + [
            f"Left out of {runner_label(config)} · {_model_cell(config)} ({_setup(config)}): {s['stale']} run(s) "
            f"from an earlier invocation that asked for more repeats ({_stale_words(s)}; kept in its "
            "results.json, marked stale)."
            for config, s in stale
        ]
    lines += ["", "## The model each setup ran on", "",
              "| Runner | Model as passed | Effort | Model the runs reported | Runs that reported another model |",
              "|---|---|---|---|---|"]
    for config, _records, s in summaries:
        lines.append(
            f"| {runner_label(config)} | {_model_cell(config)} | {_effort_cell(config, s)} | "
            f"{_reported_cell(s)} | {_mismatch_words(s)} |"
        )
    if repeated:
        lines += ["", "## Each task across repeats", ""]
        for config, _records, s in summaries:
            lines.append(f"**{runner_label(config)} · {_model_cell(config)}** ({_setup(config)}), "
                         f"{s['repeats']} repeat(s):")
            lines += [*_task_group_lines(s), ""]
    lines += ["", "## Pass rate by tier", "", "| Runner | Model | Setup | " + " | ".join(TIERS) + " |",
              "|" + "---|" * (3 + len(TIERS))]
    for config, _records, s in summaries:
        cells = []
        for tier in TIERS:
            t = s["per_tier"].get(tier)
            cells.append("-" if t is None else f"{_pct(t['pass_rate'])} ({t['passed']} / {t['scored']})" + (
                f", {t['unscored']} unscored" if t["unscored"] else ""))
        lines.append(
            f"| {runner_label(config)} | {config.get('model') or NOT_NAMED} | "
            f"{_setup(config)} | " + " | ".join(cells) + " |"
        )
    lines += ["", "## Pass rate by category (passed / scored)", ""]
    header = "| Runner | Model | Setup | " + " | ".join(CATEGORIES) + " |"
    lines += [header, "|" + "---|" * (3 + len(CATEGORIES))]
    for config, _records, s in summaries:
        cells = []
        for category in CATEGORIES:
            c = s["per_category"].get(category)
            cells.append("-" if c is None else f"{c['passed']} / {c['scored']}" + (
                f" ({c['unscored']} unscored)" if c["unscored"] else ""))
        lines.append(
            f"| {runner_label(config)} | {config.get('model') or NOT_NAMED} | "
            f"{_setup(config)} | " + " | ".join(cells) + " |"
        )
    # v1.402.0 (the autonomy metrics): how often each setup would have asked
    # a person sitting there — every call replayed through the real
    # permission engine under "Ask first" (as a fresh install asks), no grants.
    lines += ["", "## How often you would have been asked", "",
              "Every tool call replayed through the real permission engine under \"Ask first\" "
              "(how a fresh install asks), every card answered \"Allow once\" (no grants): one card "
              "per round for calls of one kind, as the app shows them; Codex reports no rounds, so "
              "each of its calls counts. The pass rate above is the share of tasks finished with no "
              "help: each task is one prompt, and nobody nudged.", "",
              "| Runner | Model | Setup | Would have asked you (per task) | Calls not known |",
              "|---|---|---|---|---|"]
    for config, _records, s in summaries:
        per = s.get("would_ask_per_task")
        lines.append(
            f"| {runner_label(config)} | {config.get('model') or NOT_NAMED} | {_setup(config)} | "
            f"{'not known' if per is None else f'{per:.1f}'} | {s.get('would_ask_not_known', 0)} |"
        )
    # v1.396.0: a TEAM setup beside the single-model ones, and the answer.
    from .team_report import side_by_side

    lines += side_by_side(summaries)
    if not configs:
        lines += ["", "No benchmark runs were found yet."]
    lines.append("")
    return "\n".join(lines)
