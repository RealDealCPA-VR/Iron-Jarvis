"""Whose failure was it? The ONE place a run that went wrong is classified.

A benchmark number is only honest when it measures the agent. Two kinds of
"did not succeed" therefore stay apart:

* INFRASTRUCTURE — the run never got a fair go: the account's usage or rate
  limit, a model server that could not be reached or was overloaded, a
  sign-in or key refusal (Iron-Proxy's included), the CLI missing or refusing
  to start, the demo model answering, or another provider answering (a
  failover). Such a run is UNSCORED (``passed=None``): it leaves the pass
  rate's denominator and the report counts it as "did not run: <reason>".
  A run the tripwire refused (the agent would have seen the grader) is
  infrastructure too: nothing was measured — and so is a run whose task
  SETUP step failed (v1.383.0: the tree the agent was to start from was never
  made).
* AGENT — the agent had its go and did not finish the job: it reached the
  time limit, used every round / turn it had, or STOPPED PART-WAY for a
  reason no infrastructure words explain after it had started work (a
  context window it outgrew, a crash in the harness itself). Those runs are
  GRADED like any other (no deliverable or a wrong answer is a fail).

The default leans toward SCORING (review round 2): only POSITIVE evidence of
infrastructure — the runner's facts, or its error's own words — makes a run
unscored. An unexplained stop counts as infrastructure only when nothing had
happened yet (no model reply, no tool): calling a part-way stop "did not
run" would drop exactly the failures a harness benchmark exists to count,
and inflate the pass rate.

A run that ended normally is neither; the grader decides.

Each runner states what it OBSERVED as plain facts (``RunOutcome.facts``) —
never a verdict — and :func:`classify_failure` turns them into one. The words
of an infrastructure failure come from the runner's own error text
(:data:`INFRA_WORDS`), so the report can say WHICH limit or refusal it was.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

#: Kinds :func:`classify_failure` answers with ("" = the run ended normally).
INFRASTRUCTURE = "infrastructure"
AGENT = "agent"

#: The facts a runner may report (anything else is ignored):
#: ``finished`` (bool) — the runner reached its own end: a final reply, a
#: result line, a completed turn; ``timed_out`` (bool) — stopped at the task's
#: time limit; ``cut_off`` (bool) — the agent used every round or turn it had;
#: ``cli_missing`` / ``start_failed`` (bool) — the CLI is not installed / its
#: process could not start; ``demo_answered`` (bool) — the demo model replied;
#: ``answered_by`` (str) — the provider that replied INSTEAD of the one asked
#: for; ``refused`` (str) — the bench refused to start the run (the tripwire);
#: ``fix_kind`` (str) — the chat lane's own diagnosis of its error frame
#: (``sign_in`` / ``key``); ``acted`` (bool) — the agent had started work
#: before it stopped: the model answered at least once, or a tool ran;
#: ``sandbox_refused`` (bool) — the CLI's own sandbox refused the model's
#: commands (round 3: Codex on Windows), so it never had a fair go;
#: ``setup_failed`` (str) — the task's setup step did not make the tree the
#: agent was to start from (v1.383.0), so no runner started.
FACT_KEYS = frozenset({
    "finished", "timed_out", "cut_off", "cli_missing", "start_failed",
    "demo_answered", "answered_by", "refused", "fix_kind", "acted",
    "sandbox_refused", "setup_failed",
})

#: An infrastructure failure, by its own words → the plain reason. First
#: match wins, so the specific shapes come before the general ones.
INFRA_WORDS: tuple[tuple[str, str], ...] = (
    (r"usage limit|hit your (?:session |weekly )?limit|out of (?:usage )?credits|quota",
     "the account's usage limit was reached"),
    (r"rate[ _-]?limit|\b429\b|too many requests", "the provider's rate limit was hit"),
    (r"credit balance|billing|payment required|\b402\b", "the account has no credit left"),
    (r"iron-proxy", "Iron-Proxy could not lend an account"),
    # Word-bounded: "catalog in" and "design in" are not a sign-in.
    (r"not logged in|please run /login|\bsign[ -]?in\b|\blog ?in\b|unauthori[sz]ed|\b401\b|\b403\b"
     r"|authentication|invalid (?:api )?key|api key",
     "the account is not signed in or its key was refused"),
    (r"overloaded|\b529\b|\b50[0234]\b|service unavailable|bad gateway|internal server error",
     "the model service was overloaded or failing"),
    (r"unreachable|could not (?:be )?reach|connection (?:refused|reset|error|aborted)"
     r"|connecterror|getaddrinfo|name resolution|network|stream disconnected"
     r"|error sending request|timed out connecting|fetch failed|socket hang up"
     r"|request timed out|econn(?:reset|refused|aborted)|etimedout|enotfound|eai_again",
     "the model server could not be reached"),
)

_RUNNER_NAMES = {"claude": "Claude Code", "codex": "Codex", "jarvis": "Iron Jarvis"}


@dataclass(frozen=True)
class Failure:
    kind: str = ""
    reason: str = ""

    @property
    def infrastructure(self) -> bool:
        return self.kind == INFRASTRUCTURE


def infra_reason(text: str) -> str:
    """The plain reason an infrastructure failure's own words give, or ""."""
    lowered = (text or "").lower()
    for pattern, reason in INFRA_WORDS:
        if re.search(pattern, lowered):
            return reason
    return ""


def classify_failure(runner: str, ok_run: bool, facts: Mapping[str, Any], error: str = "") -> Failure:
    """Whose failure a run was — see the module docstring. ``ok_run`` is the
    runner's own "finished as configured" flag; ``facts`` what it observed;
    ``error`` its error text (only read for the WORDS of an infrastructure
    failure, never to decide the agent's)."""
    name = _RUNNER_NAMES.get(runner, runner or "the runner")
    f = {k: v for k, v in dict(facts or {}).items() if k in FACT_KEYS}
    if f.get("refused"):
        return Failure(INFRASTRUCTURE, str(f["refused"]))
    if f.get("setup_failed"):
        return Failure(INFRASTRUCTURE, str(f["setup_failed"]))
    if f.get("cli_missing"):
        return Failure(INFRASTRUCTURE, f"{name} is not installed on this PC")
    if f.get("start_failed"):
        return Failure(INFRASTRUCTURE, f"{name} could not start")
    if f.get("sandbox_refused"):
        # Before the time limit and "finished": whatever the model wrote after
        # its commands were refused, a setting it never saw decided the run.
        return Failure(INFRASTRUCTURE, f"{name}'s sandbox refused its commands")
    if f.get("demo_answered"):
        return Failure(INFRASTRUCTURE, "the demo model answered instead of a real model")
    if f.get("answered_by"):
        return Failure(INFRASTRUCTURE, f"another provider answered ({f['answered_by']})")
    if f.get("timed_out"):
        return Failure(AGENT, "it reached the time limit")
    if f.get("cut_off"):
        return Failure(AGENT, "it used every round it had")
    if ok_run or f.get("finished"):
        return Failure()
    words = infra_reason(error)
    if not words and f.get("fix_kind") == "sign_in":
        words = "the account is not signed in"
    elif not words and f.get("fix_kind") == "key":
        words = "the provider's key is missing or was refused"
    if words:
        return Failure(INFRASTRUCTURE, words)
    detail = " ".join(str(error or "").split())[:200]
    suffix = f" ({detail})" if detail else ""
    if f.get("acted"):
        # It had its go — the model answered, a tool ran — and stopped for a
        # reason no infrastructure words explain: the agent's (or its
        # harness's) own failure, graded. Never "did not run".
        return Failure(AGENT, "it stopped part-way" + suffix)
    return Failure(INFRASTRUCTURE, f"{name} stopped before doing anything" + suffix)
