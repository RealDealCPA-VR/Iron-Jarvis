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
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Iterable, Mapping

from .catalog import CATEGORIES, TIERS


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


def _unscored_by(r: Mapping[str, Any]) -> str:
    """Why a record without a verdict has none: ``infrastructure`` (the run
    never got a fair go), else the grader (its check could not run). Records
    written before the distinction existed count as the grader's."""
    return "infrastructure" if r.get("unscored_by") == "infrastructure" else "grader"


def summarize(records: list[Mapping[str, Any]]) -> dict[str, Any]:
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
    }


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
    who = config.get("runner", "?")
    provider = config.get("provider") or ""
    model = config.get("model") or "its default model"
    return f"{who} · {provider + ' / ' if provider else ''}{model}"


def config_report(config: Mapping[str, Any], records: list[Mapping[str, Any]]) -> str:
    s = summarize(records)
    lines = [
        f"# Harness benchmark: {_label(config)}",
        "",
        f"Iron Jarvis {config.get('version', '?')} · seed {config.get('seed', 0)} · "
        f"configuration {str(config.get('key', ''))[:12]}",
        "",
        f"**Pass rate {_pct(s['pass_rate'])}** — {s['passed']} passed, {s['failed']} failed "
        f"of {s['scored']} scored; {s['unscored']} unscored (left out of the rate): "
        f"{s['unscored_infrastructure']} did not run (infrastructure: a limit, an unreachable "
        f"server, a refused sign-in, another model answering) and {s['unscored_grader']} where "
        f"the check could not run; {s['hit_a_limit']} stopped on their own (a time or round limit, "
        f"or part-way after starting work; scored); {s['interrupted']} interrupted; "
        f"{s['reused']} reused from an earlier run.",
        "",
        OUTSIDE_SCOPE,
        "",
        "| Task | Category | Result | Tool calls | Rounds | Time (s) | Tokens in | Tokens out | Metered $ | List-price $ | Note |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in records:
        if r.get("interrupted"):
            lines.append(
                f"| {r.get('task_id')} | {r.get('category') or '?'} | interrupted | n/a | n/a | n/a "
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
        lines.append(
            f"| {r.get('task_id')} | {r.get('category')} | {_result_word(r)} | "
            f"{_num(r.get('tool_calls'), '{:.0f}')} | {_num(r.get('model_rounds'), '{:.0f}')} | "
            f"{_num(r.get('wall_s'))} | {_num(r.get('tokens_in'), '{:,.0f}')} | "
            f"{_num(r.get('tokens_out'), '{:,.0f}')} | {_num(r.get('cost_metered_usd'), '{:.4f}')} | "
            f"{_num(r.get('cost_list_price_usd'), '{:.4f}')} | {str(note).replace('|', '/')[:160]} |"
        )
    lines += ["", "## By tier", "", "| Tier | Pass rate | Passed / scored | Unscored |", "|---|---|---|---|"]
    for tier, t in s["per_tier"].items():
        lines.append(f"| {tier} | {_pct(t['pass_rate'])} | {t['passed']} / {t['scored']} | {t['unscored']} |")
    lines += ["", "## By category", "", "| Category | Passed / scored | Unscored |", "|---|---|---|"]
    for category, c in s["per_category"].items():
        lines.append(f"| {category} | {c['passed']} / {c['scored']} | {c['unscored']} |")
    lines += [
        "",
        f"Means per run: {_num(s['mean_tool_calls'])} tool calls, {_num(s['mean_model_rounds'])} model "
        f"rounds, {_num(s['mean_wall_s'])} s. Tokens: {_tokens(s['tokens_in'], s['tokens_in_unknown'], s['tasks'])} in, "
        f"{_tokens(s['tokens_out'], s['tokens_out_unknown'], s['tasks'])} out. Cost: "
        f"{_money(s['cost_metered_usd'], s['cost_metered_unknown'], s['tasks'])} metered, "
        f"{_money(s['cost_list_price_usd'], s['cost_list_price_unknown'], s['tasks'])} at list price "
        "(subscription work, not billed).",
        "",
    ]
    return "\n".join(lines)


def combined_report(configs: list[tuple[Mapping[str, Any], list[Mapping[str, Any]]]]) -> str:
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
        "different setup ran on a different harness or Iron Jarvis version.",
        "",
        OUTSIDE_SCOPE,
        "",
        "| Runner | Model | Iron Jarvis | Setup | Tasks | Passed / scored | Pass rate | Did not run | "
        "Check could not run | Interrupted | Mean tool calls | Mean rounds | Mean time (s) | "
        "Tokens in | Tokens out | Metered $ | List-price $ |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for config, records in configs:
        s = summarize(records)
        model = (config.get("provider") or "") + (" / " if config.get("provider") else "") + (
            config.get("model") or "default")
        lines.append(
            f"| {config.get('runner')} | {model} | {config.get('version', '?')} | "
            f"{str(config.get('key', ''))[:12]} | {s['tasks']} | "
            f"{s['passed']} / {s['scored']} | {_pct(s['pass_rate'])} | {s['unscored_infrastructure']} | "
            f"{s['unscored_grader']} | {s['interrupted']} | "
            f"{_num(s['mean_tool_calls'])} | {_num(s['mean_model_rounds'])} | "
            f"{_num(s['mean_wall_s'])} | {_tokens(s['tokens_in'], s['tokens_in_unknown'], s['tasks'])} | "
            f"{_tokens(s['tokens_out'], s['tokens_out_unknown'], s['tasks'])} | "
            f"{_money(s['cost_metered_usd'], s['cost_metered_unknown'], s['tasks'])} | "
            f"{_money(s['cost_list_price_usd'], s['cost_list_price_unknown'], s['tasks'])} |"
        )
    lines += ["", "## Pass rate by tier", "", "| Runner | Model | Setup | " + " | ".join(TIERS) + " |",
              "|" + "---|" * (3 + len(TIERS))]
    for config, records in configs:
        s = summarize(records)
        cells = []
        for tier in TIERS:
            t = s["per_tier"].get(tier)
            cells.append("-" if t is None else f"{_pct(t['pass_rate'])} ({t['passed']} / {t['scored']})" + (
                f", {t['unscored']} unscored" if t["unscored"] else ""))
        lines.append(
            f"| {config.get('runner')} | {config.get('model') or 'default'} | "
            f"{str(config.get('key', ''))[:12]} | " + " | ".join(cells) + " |"
        )
    lines += ["", "## Pass rate by category (passed / scored)", ""]
    header = "| Runner | Model | Setup | " + " | ".join(CATEGORIES) + " |"
    lines += [header, "|" + "---|" * (3 + len(CATEGORIES))]
    for config, records in configs:
        s = summarize(records)
        cells = []
        for category in CATEGORIES:
            c = s["per_category"].get(category)
            cells.append("-" if c is None else f"{c['passed']} / {c['scored']}" + (
                f" ({c['unscored']} unscored)" if c["unscored"] else ""))
        lines.append(
            f"| {config.get('runner')} | {config.get('model') or 'default'} | "
            f"{str(config.get('key', ''))[:12]} | " + " | ".join(cells) + " |"
        )
    if not configs:
        lines += ["", "No benchmark runs were found yet."]
    lines.append("")
    return "\n".join(lines)
