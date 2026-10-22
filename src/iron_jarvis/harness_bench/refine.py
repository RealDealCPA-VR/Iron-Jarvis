"""The Harness² loop: ``ironjarvis bench-harness run --refine`` (v1.370.0, IJ
harness plan Phase 7; Harness² principles 6–8 — google-research/harness2,
Apache-2.0, ideas only, no code taken).

PROPOSE – PROBE – COMPOSE, measured by the benchmark's own hidden checks:

1. THE CURRENT HARNESS is probed on the chosen tasks (the plain benchmark
   configuration for this runner/model — a completed run is reused under its
   marker, like any benchmark run).
2. EVIDENCE is written as FILES (principle 7): ``evidence/round-N/`` holds a
   ``README.md`` pointer, each task's statement, every probed harness's full
   trajectory per task (each tool call's input and output, the reply) with
   the check's VERDICT and its one-line reason, and the harness files
   themselves (plus the component tree the model reads). Never the hidden
   check, never its output (it can quote the answer key), never a score from
   a run that was not graded.
3. PROPOSE: a model — the runner's own model, as a harness-mode turn with
   read-only tools plus the two file writers, confined to a fresh copy of the
   evidence — is told only WHERE the evidence is (a short pointer, never a
   dump) and writes the harness files it changes into ``proposal/``.
   OMITTED IS INHERITED: a file it does not write keeps the base harness's.
4. VALIDATE (Phase 3's validator, ``components.read_project_layer``): known
   file names only (the four text components and ``plugins.toml`` — whose
   ``[[checks]]`` are refused here: a check RUNS a command, which is a
   person's call), every caller of the validator's rules, nothing promptguard
   flags, nothing the tripwire calls grader material, and an actual change.
   An invalid proposal is RETRIED ONCE (told why), then DROPPED.
5. PROBE each valid candidate on the SAME tasks with the same seed, runner,
   model, round budget and time limits — a candidate is its own benchmark
   configuration (its layer's hash is in the fingerprint), so its runs are
   resumable under their markers too. The candidate layer is read from
   outside the task's copy (``components.LAYER_SOURCE``): the agent's folder
   and the hidden check are the baseline's, byte for byte.
6. COMPOSE the winners — candidates that pass at least as many tasks as the
   current harness on the tasks BOTH runs scored. Parallel: per harness file,
   the best winner's version (best = most passes, then fewest tokens). A
   composition that differs from every single winner is validated and
   probed too. Sequential: each candidate already builds on the best one
   before it, so the composition is the best winner.
7. ADOPT only a composition that scores AT LEAST AS WELL as the current
   harness on the real hidden checks (``adopted.json`` says whether it was
   better or the same). Adopting WRITES A PROPOSED LAYER under the refine
   folder (``adopted/.ironjarvis/harness/``) — never into a project: the
   user copies it into a folder with ``bench-harness adopt`` (which refuses
   to overwrite a file that differs).

UNSCORED IS NOT ZERO (principle 4): a run whose check could not run, or that
did not run (infrastructure), leaves the comparison — two harnesses are
compared only on the tasks both scored.

THE BUDGET IS AN IDENTITY (principle 6): every proposer rollout and every
probe rollout is entered, as it happens, in the refine LEDGER by stage
(``ledger.json``); each stage's records must sum to its rollouts, the grand
totals must match, and the counts must stay inside the PLANNED budget (k
proposals with one retry each; one probe of every task for the baseline, each
candidate and the composition). A mismatch is written and then raised.

PARALLEL = k candidates from ONE round of evidence (breadth; they never see
each other, each in its own copy). SEQUENTIAL = k rounds of one candidate,
each round's evidence holding every probe so far and each candidate starting
from the best harness so far (depth). Both run one model call at a time on
this PC.

COMPLETION IS RECORDED (principle 5): a candidate's proposal is reused on a
rerun only under its ``complete.json`` marker, whose fingerprint covers the
refine configuration, the candidate, its base harness and the evidence it
read; anything else is proposed again. A refine folder whose saved
configuration differs is refused (``refine.json``), and so is a different
seed. Results: ``<home>/harness-bench/refine-<model>-<key>/``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import shutil
import time
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Sequence

from .. import __version__
from .bench import (
    BenchRefused,
    _canon,
    _now,
    _read_json,
    _sha,
    _slug,
    _write_json,
    bench_root,
    run_bench,
    TRAJECTORY_NAME,
)
from .catalog import Task, TaskError, load_tasks, select_tasks
from .ledger import BudgetLedger, BudgetMismatch, Rollout, differences, sum_fields
from .runners import (
    DEFAULT_ROUNDS,
    JARVIS_APPROVAL_MODE,
    JarvisRunner,
    LANE_SESSION_ID,
    counting_router,
    lane_cost,
    layer_tree_hash,
    parse_sse,
)
from .tripwire import leaks

MODES = ("parallel", "sequential")
MAX_K = 8
#: The harness files a proposal may write (Phase 3's text components and the
#: declarative tool rules).
PROPOSAL_FILES = ("system_prompt.md", "guardrails.md", "memory.md", "tool_guidance.md", "plugins.toml")
#: The proposer's turn: its rounds, its time, and the tools it may use (it
#: reads the evidence and writes its proposal — nothing else).
PROPOSER_ROUNDS = 30
PROPOSER_TIMEOUT_S = 900.0
PROPOSER_TOOLS = frozenset({"read_file", "list_files", "glob", "grep", "write_file", "edit_file"})
#: A parallel round's candidates are nudged to different components so k
#: proposals from one evidence round do not all make the same edit.
FOCUS = (
    "Concentrate on the tool guidance (tool_guidance.md).",
    "Concentrate on how the agent works (system_prompt.md).",
    "Concentrate on short notes that help it check its work (memory.md).",
    "Concentrate on what it must not do (guardrails.md).",
)
#: The text of one trajectory step / one reply kept in the evidence files.
EVIDENCE_OUTPUT_CHARS = 2000

POINTER = (
    "You are improving the HARNESS of a coding agent: the instructions and tool rules around a "
    "fixed model. Everything you need is in the folder evidence/ — read evidence/README.md first; "
    "it says what is where.\n"
    "{focus}\n"
    "Write ONLY the harness files you change into the folder proposal/ (allowed names: "
    + ", ".join(PROPOSAL_FILES) + "). A file you do not write keeps its current version. Keep each "
    "file short and GENERAL: the harness must help on coding tasks you have not seen, so never "
    "write a task's own answer, its file names or its test details into it.\n"
    "When you are done, reply with one sentence: what you changed and why."
)
RETRY_NOTE = "Your last proposal was refused: {why}. Fix that and write the proposal again."


# --------------------------------------------------------------------------- #
# the configuration
# --------------------------------------------------------------------------- #


def _module_sha() -> str:
    try:
        return hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    except OSError:
        return ""


@dataclass
class RefineSettings:
    provider: str
    model: str
    dialect: str = "auto"
    rounds: int = DEFAULT_ROUNDS
    tier: str = "all"
    tasks: tuple[str, ...] = ()
    max_tasks: int | None = None
    k: int = 2
    mode: str = "parallel"

    def identity(self) -> dict[str, Any]:
        return {
            "loop": "refine", "provider": self.provider, "model": self.model,
            "dialect": self.dialect, "rounds": self.rounds, "tier": self.tier,
            "tasks": list(self.tasks), "max_tasks": self.max_tasks, "k": self.k, "mode": self.mode,
            "version": __version__, "loop_sha256": _module_sha(),
        }

    @property
    def key(self) -> str:
        return _sha(_canon(self.identity()))

    @property
    def folder_name(self) -> str:
        return f"refine-{_slug(self.provider)}-{_slug(self.model)}-{self.key[:12]}"


def check_refine_config(folder: Path, settings: RefineSettings, seed: int | None) -> int:
    """The seed this invocation uses; :class:`BenchRefused` for an
    incompatible resume (another configuration, another seed)."""
    saved = _read_json(folder / "refine.json")
    if saved is None:
        return 0 if seed is None else int(seed)
    if saved.get("key") != settings.key:
        raise BenchRefused(
            f"The folder {folder} holds a refine run of a different setup, so it cannot be resumed "
            "with this one. Move that folder away to start it again."
        )
    saved_seed = int(saved.get("seed", 0))
    if seed is not None and int(seed) != saved_seed:
        raise BenchRefused(
            f"This refine run was started with --seed {saved_seed}, and you asked for --seed {seed}. "
            f"Run it with --seed {saved_seed} to resume it, or move {folder} away to start again."
        )
    return saved_seed


# --------------------------------------------------------------------------- #
# the budget ledger, by stage
# --------------------------------------------------------------------------- #


@dataclass
class Stage:
    stage: str  # baseline | propose | probe | compose
    label: str
    rollouts: list[Rollout] = field(default_factory=list)
    #: The stage's own records (the bench run records, or the proposer
    #: turn's receipt) — the other side of the identity.
    records: list[dict[str, Any]] = field(default_factory=list)

    def totals(self) -> dict[str, Any]:
        from dataclasses import asdict as _asdict

        out = sum_fields(_asdict(c) for r in self.rollouts for c in r.calls)
        out["model_calls"] = sum(len(r.calls) for r in self.rollouts)
        out["rollouts"] = len(self.rollouts)
        return out

    def record_totals(self) -> dict[str, Any]:
        return sum_fields(r for r in self.records if not r.get("reused"))


class RefineLedger:
    """Every rollout of one refine invocation, entered by stage."""

    def __init__(self, planned: dict[str, int]) -> None:
        self.planned = dict(planned)
        self.stages: list[Stage] = []

    def stage(self, stage: str, label: str) -> Stage:
        s = Stage(stage=stage, label=label)
        self.stages.append(s)
        return s

    def count(self, stage: str) -> int:
        return sum(len(s.rollouts) for s in self.stages if s.stage == stage)

    def grand_totals(self) -> dict[str, Any]:
        from dataclasses import asdict as _asdict

        out = sum_fields(_asdict(c) for s in self.stages for r in s.rollouts for c in r.calls)
        out["model_calls"] = sum(len(r.calls) for s in self.stages for r in s.rollouts)
        out["rollouts"] = sum(len(s.rollouts) for s in self.stages)
        return out

    def problems(self) -> list[str]:
        """Every way the ledger does not add up (empty = it does)."""
        out: list[str] = []
        for s in self.stages:
            diff = differences(s.record_totals(), s.totals())
            if diff:
                out.append(f"{s.stage} {s.label}: " + "; ".join(diff))
        grand = self.grand_totals()
        parts = sum_fields(s.record_totals() for s in self.stages)
        diff = differences(parts, grand)
        if diff:
            out.append("the stages do not sum to the whole: " + "; ".join(diff))
        proposals = self.count("propose")
        if proposals > self.planned.get("proposals", 0):
            out.append(f"{proposals} proposer rollouts, over the planned {self.planned.get('proposals')}")
        probes = sum(len(s.rollouts) for s in self.stages if s.stage in ("baseline", "probe", "compose"))
        if probes > self.planned.get("probe_runs", 0):
            out.append(f"{probes} probe rollouts, over the planned {self.planned.get('probe_runs')}")
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "planned": self.planned,
            "stages": [
                {"stage": s.stage, "label": s.label, "totals": s.totals(),
                 "record_totals": s.record_totals(),
                 "rollouts": [{"task_id": r.task_id, "runner": r.runner, "totals": r.totals(),
                               "calls": [asdict(c) for c in r.calls]} for r in s.rollouts]}
                for s in self.stages
            ],
            "totals": self.grand_totals(),
        }


# --------------------------------------------------------------------------- #
# scores
# --------------------------------------------------------------------------- #


@dataclass
class Score:
    verdicts: dict[str, bool | None]
    tokens_in: int | None = 0

    @property
    def scored(self) -> int:
        return sum(1 for v in self.verdicts.values() if v is not None)

    @property
    def passed(self) -> int:
        return sum(1 for v in self.verdicts.values() if v is True)


def score_of(records: Sequence[dict[str, Any]]) -> Score:
    tokens = sum_fields(records).get("tokens_in")
    return Score(verdicts={r["task_id"]: r.get("passed") for r in records}, tokens_in=tokens)


def compare(a: Score, b: Score) -> tuple[int, int, int]:
    """``(a's passes, b's passes, n)`` on the tasks BOTH scored — an unscored
    run on either side leaves the comparison."""
    common = [t for t, v in a.verdicts.items() if v is not None and b.verdicts.get(t) is not None]
    return (
        sum(1 for t in common if a.verdicts[t] is True),
        sum(1 for t in common if b.verdicts[t] is True),
        len(common),
    )


# --------------------------------------------------------------------------- #
# layers on disk
# --------------------------------------------------------------------------- #


def _harness_dir(base: Path) -> Path:
    from ..harness.components import HARNESS_PARTS

    return Path(base).joinpath(*HARNESS_PARTS)


def write_layer(base: Path, files: dict[str, str]) -> Path:
    """A folder ``base`` holding ``.ironjarvis/harness/<files>`` (replaced)."""
    hdir = _harness_dir(base)
    if hdir.exists():
        shutil.rmtree(hdir)
    hdir.mkdir(parents=True, exist_ok=True)
    for name, text in sorted(files.items()):
        (hdir / name).write_bytes(text.encode("utf-8"))
    return base


def read_layer_files(base: Path | None) -> dict[str, str]:
    if base is None:
        return {}
    hdir = _harness_dir(base)
    out: dict[str, str] = {}
    if hdir.is_dir():
        for path in sorted(hdir.iterdir()):
            if path.is_file():
                out[path.name] = path.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n")
    return out


def validate_candidate(
    base_files: dict[str, str], proposal: dict[str, str], layer_base: Path, tasks: Sequence[Task],
    known_tools: Any,
) -> tuple[dict[str, str], list[str], str]:
    """``(files, changed, problem)``. ``problem`` "" = a valid candidate,
    written to ``layer_base``. Omitted-is-inherited: ``files`` is the base
    harness with the proposal's files over it."""
    from ..harness.components import read_project_layer

    unknown = sorted(n for n in proposal if n not in PROPOSAL_FILES)
    if unknown:
        return {}, [], f"{', '.join(unknown)} is not a harness file it may write ({', '.join(PROPOSAL_FILES)})"
    plugins = proposal.get("plugins.toml")
    if plugins is not None:
        try:
            if tomllib.loads(plugins).get("checks"):
                return {}, [], "plugins.toml may hold [[rules]] only here: a [[checks]] entry runs a command"
        except tomllib.TOMLDecodeError as exc:
            return {}, [], f"plugins.toml is not valid TOML ({exc})"
    files = {**base_files, **{k: v for k, v in proposal.items()}}
    files = {k: v for k, v in files.items() if v.strip()}
    changed = sorted(n for n in set(files) | set(base_files) if files.get(n) != base_files.get(n))
    if not changed:
        return {}, [], "it changes nothing"
    write_layer(layer_base, files)
    layer = read_project_layer(layer_base, known_tools=known_tools, session_id=None)
    if not layer.used:
        return {}, [], layer.note or "the harness files could not be read"
    if layer.blocked:
        return {}, [], "part of it reads as an instruction aimed at the model (the prompt guard flagged it)"
    for task in tasks:
        found = leaks(task, _harness_dir(layer_base), "")
        if found:
            return {}, [], f"it carries the grader's material ({found[0]})"
    return files, changed, ""


# --------------------------------------------------------------------------- #
# evidence, as files
# --------------------------------------------------------------------------- #


def _verdict_word(record: dict[str, Any] | None) -> str:
    if record is None:
        return "not run"
    if record.get("passed") is True:
        return "passed"
    if record.get("passed") is False:
        return "failed"
    return "unscored"


def _trajectory_md(label: str, record: dict[str, Any] | None, steps: list[dict[str, Any]] | None) -> str:
    """One run as the improver reads it: the verdict (and its one-line
    reason, never the check's own output), the numbers, every step."""
    lines = [f"# {label}", ""]
    if record is None:
        return "\n".join(lines + ["This harness was not run on this task."]) + "\n"
    lines.append(f"Check: {_verdict_word(record)}" + (
        f" ({record.get('grade_reason')})" if record.get("grade_reason") and record.get("passed") is not True else ""
    ))
    if record.get("passed") is None and record.get("failure_reason"):
        lines.append(f"Not scored: {record.get('failure_reason')}")
    lines.append(
        f"Tool calls: {record.get('tool_calls')} · model rounds: {record.get('model_rounds')} · "
        f"tokens in/out: {record.get('tokens_in')}/{record.get('tokens_out')}"
    )
    if record.get("error"):
        lines.append(f"Run error: {record.get('error')}")
    lines += ["", "## What the agent did", ""]
    if not steps:
        lines.append("(no step-by-step record was kept for this run)")
    else:
        n = 0
        for step in steps:
            kind = step.get("kind")
            if kind == "call":
                n += 1
                args = json.dumps(step.get("args") or {}, ensure_ascii=False, sort_keys=True)
                lines.append(f"{n}. CALL {step.get('tool')} {args[:EVIDENCE_OUTPUT_CHARS]}")
            elif kind == "result":
                ok = "ok" if step.get("ok") else "FAILED"
                out = str(step.get("output") or "")[:EVIDENCE_OUTPUT_CHARS]
                lines.append(f"   RESULT ({ok}):")
                lines += ["   " + ln for ln in out.splitlines()] or ["   (no output)"]
            elif kind == "text":
                lines.append("SAID: " + str(step.get("text") or "")[:EVIDENCE_OUTPUT_CHARS])
            elif kind == "reply":
                lines += ["", "## The final reply", "", str(step.get("text") or "")[:EVIDENCE_OUTPUT_CHARS]]
            elif kind == "error":
                lines += ["", "## The turn failed", "", str(step.get("text") or "")]
    return "\n".join(lines).rstrip() + "\n"


@dataclass
class Probed:
    label: str
    layer_base: Path | None
    files: dict[str, str]
    config_dir: Path | None
    records: list[dict[str, Any]]
    score: Score
    base: str = ""
    changed: list[str] = field(default_factory=list)
    summary: str = ""


def _rendered_tree(platform: Any, settings: RefineSettings, layer_base: Path | None) -> str:
    runner = JarvisRunner(platform, provider=settings.provider, model=settings.model,
                          round_budget=settings.rounds, dialect=settings.dialect, layer_dir=layer_base)
    return runner.harness().rendered


def build_evidence(
    folder: Path, tasks: Sequence[Task], probed: Sequence[Probed], rendered: dict[str, str],
) -> Path:
    """The evidence tree for one round (see the module docstring). Pure of
    run times and clocks, so the same probes give the same bytes (the
    candidates' markers key on its hash). BLOCKING."""
    if folder.exists():
        shutil.rmtree(folder)
    (folder / "harness").mkdir(parents=True)
    (folder / "tasks").mkdir()
    head = ["| Task | " + " | ".join(p.label for p in probed) + " |",
            "|---|" + "---|" * len(probed)]
    for task in tasks:
        cells = []
        for p in probed:
            rec = next((r for r in p.records if r.get("task_id") == task.id), None)
            cells.append(_verdict_word(rec))
        head.append(f"| {task.id} | " + " | ".join(cells) + " |")
    harness_lines = []
    for p in probed:
        hdir = folder / "harness" / p.label
        hdir.mkdir(parents=True)
        for name, text in sorted(p.files.items()):
            (hdir / name).write_bytes(text.encode("utf-8"))
        (hdir / "_what_the_model_reads.md").write_bytes(rendered.get(p.label, "").encode("utf-8"))
        what = "the built-in default (no files)" if not p.files else ", ".join(sorted(p.files))
        extra = f" — built on {p.base}, changed {', '.join(p.changed)}" if p.base else ""
        harness_lines.append(f"- harness/{p.label}/: {what}{extra}" + (f". Its author said: {p.summary}" if p.summary else ""))
    for task in tasks:
        tdir = folder / "tasks" / task.id
        tdir.mkdir(parents=True)
        (tdir / "task.md").write_bytes(f"# {task.id}\n\n{task.prompt}\n".encode("utf-8"))
        for p in probed:
            rec = next((r for r in p.records if r.get("task_id") == task.id), None)
            steps = None
            if p.config_dir is not None:
                steps = _read_json(p.config_dir / "runs" / task.id / TRAJECTORY_NAME)
            (tdir / f"{p.label}.md").write_bytes(
                _trajectory_md(f"{task.id} — harness {p.label}", rec,
                               steps if isinstance(steps, list) else None).encode("utf-8")
            )
    readme = [
        "# Evidence for improving the IJ harness",
        "",
        "The HARNESS is everything around the model: how it is told to work, its guardrails, its "
        "notes, its tool guidance, and declarative tool rules. The model and the tools themselves "
        "are fixed. The harness is a set of files (any file left out keeps the built-in default):",
        "",
        "- system_prompt.md — replaces \"How you work\".",
        "- guardrails.md — ADDED after four fixed rules that can never be removed.",
        "- memory.md — notes the agent reads.",
        "- tool_guidance.md — replaces the advice under the tool list (the tool list is fixed).",
        "- plugins.toml — [[rules]] with `tool` and `action` (\"ask\" or \"deny\") only.",
        "",
        "## The harnesses measured so far",
        "",
        *harness_lines,
        "",
        "Each harness folder also holds _what_the_model_reads.md: the whole harness section of the "
        "agent's prompt as the model saw it.",
        "",
        "## Results (a hidden check graded each run; unscored runs could not be graded)",
        "",
        *head,
        "",
        "## Where to look",
        "",
        "- tasks/<task>/task.md — what the agent was asked.",
        "- tasks/<task>/<harness>.md — what it did step by step under that harness: every tool call "
        "with its input and output, what it said, its final reply, and the check's verdict.",
        "",
        "Look at what went wrong or took long, and what the harness could have said to prevent it. "
        "Prefer a change that helps on any coding task over one that fits these tasks.",
    ]
    (folder / "README.md").write_bytes(("\n".join(readme) + "\n").encode("utf-8"))
    return folder


def evidence_hash(folder: Path) -> str:
    from .catalog import tree_hash

    return tree_hash(folder)


# --------------------------------------------------------------------------- #
# the proposer
# --------------------------------------------------------------------------- #


@dataclass
class Proposal:
    files: dict[str, str]
    summary: str
    problem: str = ""  # an infrastructure problem: the turn could not run / the demo model answered
    record: dict[str, Any] = field(default_factory=dict)


async def propose(
    platform: Any, settings: RefineSettings, workspace: Path, focus: str, rollout: Rollout,
    *, retry_why: str = "",
) -> Proposal:
    """ONE proposer turn in ``workspace`` (holding ``evidence/``): the
    pointer prompt, read-only tools + the file writers, every model call
    entered in ``rollout``. Returns the files it wrote into ``proposal/``."""
    from ..core import procs
    from ..daemon.chat_stream import stream_chat_turn
    from ..daemon.routes.chat import _approvals
    from ..daemon.schemas import ChatBody, ChatMessageBody
    from ..personas.builtins import BUILTIN_PERSONAS

    prompt = POINTER.format(focus=focus)
    if retry_why:
        prompt += "\n\n" + RETRY_NOTE.format(why=retry_why)
    (workspace / "proposal").mkdir(parents=True, exist_ok=True)
    body = ChatBody(
        messages=[ChatMessageBody(role="user", content=prompt)],
        provider=settings.provider, model=settings.model,
        workspace_dir=str(workspace), approval_mode=JARVIS_APPROVAL_MODE,
    )
    deadline = time.monotonic() + PROPOSER_TIMEOUT_S
    approvals = _approvals(SimpleNamespace(platform=platform))
    observed: dict[str, Any] = {}
    done: dict[str, Any] | None = None
    error = ""
    with counting_router(platform, rollout, observed):
        try:
            gen = await stream_chat_turn(
                platform, BUILTIN_PERSONAS, body,
                should_stop=lambda: time.monotonic() > deadline,
                round_budget=PROPOSER_ROUNDS, stay_in_chat=True,
                harness=settings.dialect, tool_ceiling=PROPOSER_TOOLS,
            )
            async with asyncio.timeout(PROPOSER_TIMEOUT_S + 60):
                async with contextlib.aclosing(gen) as frames:
                    async for chunk in frames:
                        for event, data in parse_sse(chunk):
                            if event == "approval":
                                approvals.resolve(str(data.get("id") or ""), "once")
                            elif event == "error":
                                error = str(data.get("detail") or data.get("title") or "the turn failed")
                            elif event == "done":
                                done = data
        except TimeoutError:
            error = "the proposer ran past its time limit"
        except Exception as exc:  # noqa: BLE001 — recorded, never raised past the loop
            error = f"{type(exc).__name__}: {exc}"
    await asyncio.to_thread(procs.end_session, LANE_SESSION_ID)
    record = _proposer_record(done, rollout)
    out = Proposal(files={}, summary="", record=record)
    if done is None:
        out.problem = error or "the proposer's turn ended without a reply"
        return out
    route = done.get("route") or {}
    if str(route.get("reason") or "") == "mock" or str(done.get("provider") or "") == "mock":
        out.problem = "the demo model answered, not a real model"
        return out
    if settings.provider and str(done.get("provider") or "") != settings.provider:
        out.problem = f"answered by {done.get('provider') or 'nothing'} instead of {settings.provider}"
        return out
    out.summary = " ".join(str(done.get("reply") or "").split())[:300]
    pdir = workspace / "proposal"
    for path in sorted(pdir.rglob("*")):
        if path.is_file():
            rel = path.relative_to(pdir).as_posix()
            out.files[rel] = path.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n")
    return out


def _proposer_record(done: dict[str, Any] | None, rollout: Rollout) -> dict[str, Any]:
    """The proposer turn's own receipt (the lane's done usage) — the other
    side of the ledger identity, exactly as the Jarvis runner keeps it."""
    if done is None:
        totals = rollout.totals()
        return {k: totals[k] for k in ("tokens_in", "tokens_out", "cost_metered_usd", "cost_list_price_usd")}
    usage = done.get("usage") or {}
    metered, listed = lane_cost(usage, done.get("route") or {})
    return {
        "tokens_in": int(usage.get("input_tokens", 0) or 0),
        "tokens_out": int(usage.get("output_tokens", 0) or 0),
        "cost_metered_usd": metered, "cost_list_price_usd": listed,
    }


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #


@dataclass
class RefineResult:
    folder: Path
    settings: RefineSettings
    seed: int
    tasks: list[str]
    baseline: Probed | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    composition: dict[str, Any] | None = None
    adopted: dict[str, Any] | None = None
    ledger: RefineLedger | None = None
    dry_run: bool = False
    plan_lines: list[str] = field(default_factory=list)
    report_path: Path | None = None


def planned_budget(settings: RefineSettings, n_tasks: int) -> dict[str, int]:
    """The most rollouts this configuration may spend: k proposals with one
    retry each; one probe of every task for the baseline, each candidate and
    one composition."""
    return {
        "proposals": settings.k * 2,
        "probe_runs": n_tasks * (1 + settings.k + 1),
    }


async def _probe(
    platform: Any, home: Path, settings: RefineSettings, seed: int, tasks: Sequence[Task],
    label: str, layer_base: Path | None, ledger: RefineLedger, stage: str,
    echo: Callable[[str], None],
) -> Probed:
    runner = JarvisRunner(platform, provider=settings.provider, model=settings.model,
                          round_budget=settings.rounds, dialect=settings.dialect, layer_dir=layer_base)
    probe_ledger = BudgetLedger()
    st = ledger.stage(stage, label)
    echo(f"probe {label}:")
    try:
        res = await run_bench(
            runner=runner, home=home, tasks=list(tasks), names=[t.id for t in tasks], seed=seed,
            echo=echo, tier="all", ledger=probe_ledger,
        )
    finally:
        st.rollouts = list(probe_ledger.rollouts)
    st.records = list(res.records)
    return Probed(label=label, layer_base=layer_base, files=read_layer_files(layer_base),
                  config_dir=res.config_dir, records=list(res.records), score=score_of(res.records))


def _rank(p: Probed, baseline: Probed) -> tuple:
    mine, _theirs, _n = compare(p.score, baseline.score)
    tokens = p.score.tokens_in if p.score.tokens_in is not None else 10**12
    return (-mine, tokens, p.label)


def _winners(probed: Sequence[Probed], baseline: Probed) -> list[Probed]:
    out = []
    for p in probed:
        mine, theirs, n = compare(p.score, baseline.score)
        if n > 0 and mine >= theirs:
            out.append(p)
    return sorted(out, key=lambda p: _rank(p, baseline))


async def run_refine(
    *,
    platform: Any,
    home: Path,
    settings: RefineSettings,
    seed: int | None = None,
    tasks: Sequence[Task] | None = None,
    dry_run: bool = False,
    echo: Callable[[str], None] = lambda _s: None,
) -> RefineResult:
    """The loop (see the module docstring)."""
    if settings.mode not in MODES:
        raise BenchRefused(f"--mode must be one of {', '.join(MODES)}")
    if not 1 <= int(settings.k) <= MAX_K:
        raise BenchRefused(f"--k must be 1 to {MAX_K}")
    all_tasks = list(tasks) if tasks is not None else await asyncio.to_thread(load_tasks)
    folder = bench_root(home) / settings.folder_name
    use_seed = await asyncio.to_thread(check_refine_config, folder, settings, seed)
    try:
        chosen = select_tasks(all_tasks, names=list(settings.tasks) or None, seed=use_seed,
                              max_tasks=settings.max_tasks, tier=settings.tier)
    except TaskError as exc:
        raise BenchRefused(str(exc)) from exc
    if not chosen:
        raise BenchRefused("no task matches --tasks / --tier")
    result = RefineResult(folder=folder, settings=settings, seed=use_seed, tasks=[t.id for t in chosen])
    plan = planned_budget(settings, len(chosen))
    if dry_run:
        result.dry_run = True
        result.plan_lines = [
            f"{settings.folder_name} (seed {use_seed}): {settings.mode}, k={settings.k}, "
            f"{len(chosen)} task(s): {', '.join(result.tasks)}",
            f"At most {plan['proposals']} proposer turns and {plan['probe_runs']} task runs "
            "(completed runs are reused).",
        ]
        return result
    folder.mkdir(parents=True, exist_ok=True)
    created = (_read_json(folder / "refine.json") or {}).get("created_at") or _now()
    await asyncio.to_thread(_write_json, folder / "refine.json", {
        "key": settings.key, "folder": settings.folder_name, "seed": use_seed,
        **settings.identity(), "created_at": created,
    })
    ledger = RefineLedger(plan)
    result.ledger = ledger
    try:
        known = set(platform.registry.names())
    except Exception:  # noqa: BLE001
        known = None
    rendered: dict[str, str] = {"current": _rendered_tree(platform, settings, None)}

    # 1. the current harness
    baseline = await _probe(platform, home, settings, use_seed, chosen, "current", None,
                            ledger, "baseline", echo)
    result.baseline = baseline
    probed: list[Probed] = [baseline]

    rounds = 1 if settings.mode == "parallel" else settings.k
    per_round = settings.k if settings.mode == "parallel" else 1
    for r in range(1, rounds + 1):
        evidence = folder / "evidence" / f"round-{r}"
        await asyncio.to_thread(build_evidence, evidence, chosen, probed, rendered)
        ev_hash = await asyncio.to_thread(evidence_hash, evidence)
        # The base a candidate starts from: the current harness (parallel), or
        # the best probed so far (sequential — ties go to the newer one).
        if settings.mode == "sequential":
            ranked = sorted(
                [p for p in probed if compare(p.score, baseline.score)[2] > 0],
                key=lambda p: (-compare(p.score, baseline.score)[0], -probed.index(p)),
            )
            base = ranked[0] if ranked else baseline
        else:
            base = baseline
        for i in range(1, per_round + 1):
            label = f"c{i}" if settings.mode == "parallel" else f"c{r}"
            focus = FOCUS[(i - 1) % len(FOCUS)] if settings.mode == "parallel" else (
                "Build on the best harness so far (" + base.label + "); fix what its runs still got wrong."
            )
            cand = await _candidate(
                platform, settings, folder, label, base, focus, ev_hash, evidence, chosen, known, ledger, echo,
            )
            result.candidates.append(cand["view"])
            if cand["layer_base"] is None:
                continue
            p = await _probe(platform, home, settings, use_seed, chosen, label, cand["layer_base"],
                             ledger, "probe", echo)
            p.base, p.changed, p.summary = base.label, cand["changed"], cand["summary"]
            cand["view"]["score"] = {"passed": p.score.passed, "scored": p.score.scored}
            mine, theirs, n = compare(p.score, baseline.score)
            cand["view"]["versus_current"] = {"passed": mine, "current_passed": theirs, "compared": n}
            rendered[label] = _rendered_tree(platform, settings, cand["layer_base"])
            probed.append(p)

    # 6. compose the winners
    candidates = [p for p in probed if p is not baseline]
    winners = _winners(candidates, baseline)
    composition: Probed | None = None
    comp_view: dict[str, Any] = {"winners": [w.label for w in winners]}
    if not winners:
        comp_view["note"] = "No candidate scored at least as well as the current harness."
    elif settings.mode == "sequential" or len(winners) == 1:
        composition = winners[0]
        comp_view.update({"from": [composition.label], "probed_as": composition.label})
    else:
        chosen_files: dict[str, str] = {}
        source: dict[str, str] = {}
        for w in winners:
            for name in w.changed:
                if name not in source:
                    source[name] = w.label
                    chosen_files[name] = w.files.get(name, "")
        if len(set(source.values())) == 1:
            composition = next(w for w in winners if w.label == next(iter(source.values())))
            comp_view.update({"from": [composition.label], "probed_as": composition.label})
        else:
            comp_base = folder / "composition" / "layer"
            files = {**baseline.files, **chosen_files}
            files, changed, problem = await asyncio.to_thread(
                validate_candidate, baseline.files, {k: v for k, v in files.items()}, comp_base, chosen, known,
            )
            comp_view["from"] = sorted(set(source.values()))
            comp_view["files_from"] = source
            if problem:
                comp_view["note"] = f"The composition was not valid: {problem}. The best winner stands alone."
                composition = winners[0]
                comp_view["probed_as"] = composition.label
            else:
                composition = await _probe(platform, home, settings, use_seed, chosen, "composition",
                                           comp_base, ledger, "compose", echo)
                composition.changed = changed
                comp_view["probed_as"] = "composition"
    if composition is not None:
        mine, theirs, n = compare(composition.score, baseline.score)
        comp_view["versus_current"] = {"passed": mine, "current_passed": theirs, "compared": n}
        if n > 0 and mine >= theirs:
            result.adopted = await asyncio.to_thread(
                _adopt, folder, composition, baseline, mine, theirs, n,
            )
    result.composition = comp_view

    problems = ledger.problems()
    await asyncio.to_thread(_write_outputs, folder, result, problems)
    if problems:
        raise BudgetMismatch("the refine budget does not add up: " + "; ".join(problems))
    return result


async def _candidate(
    platform: Any, settings: RefineSettings, folder: Path, label: str, base: Probed, focus: str,
    ev_hash: str, evidence: Path, tasks: Sequence[Task], known: Any, ledger: RefineLedger,
    echo: Callable[[str], None],
) -> dict[str, Any]:
    """Propose (or reuse under its marker), validate, retry once."""
    cdir = folder / "candidates" / label
    fingerprint = _sha(_canon({
        "refine": settings.key, "label": label, "base": base.label,
        "base_files": base.files, "evidence": ev_hash,
    }))
    view: dict[str, Any] = {"label": label, "base": base.label}
    marker = await asyncio.to_thread(_read_json, cdir / "complete.json")
    layer_base = cdir / "layer"
    if isinstance(marker, dict) and marker.get("fingerprint") == fingerprint:
        stored = marker.get("candidate") or {}
        view.update(stored, reused=True)
        echo(f"candidate {label}: reused ({stored.get('status')})")
        if stored.get("status") == "valid" and layer_tree_hash(layer_base) == marker.get("layer_sha256"):
            return {"view": view, "layer_base": layer_base, "changed": stored.get("changed", []),
                    "summary": stored.get("summary", "")}
        if stored.get("status") != "valid":
            return {"view": view, "layer_base": None, "changed": [], "summary": ""}
    if cdir.exists():
        await asyncio.to_thread(shutil.rmtree, cdir)
    why = ""
    attempts = 0
    for attempt in (1, 2):
        attempts = attempt
        work = cdir / f"attempt-{attempt}"
        await asyncio.to_thread(shutil.copytree, evidence, work / "evidence")
        st = ledger.stage("propose", f"{label}/attempt-{attempt}")
        rollout = Rollout(task_id=f"propose:{label}", runner="improver")
        st.rollouts.append(rollout)
        echo(f"candidate {label}: proposing (attempt {attempt})...")
        prop = await propose(platform, settings, work, focus, rollout, retry_why=why)
        st.records.append(prop.record)
        if prop.problem:
            view.update(status="dropped", reason=prop.problem, attempts=attempts)
            echo(f"candidate {label}: dropped ({prop.problem})")
            # An infrastructure problem is not written as complete: rerun next time.
            return {"view": view, "layer_base": None, "changed": [], "summary": ""}
        files, changed, problem = await asyncio.to_thread(
            validate_candidate, base.files, prop.files, layer_base, tasks, known,
        )
        if not problem:
            view.update(status="valid", reason="", attempts=attempts, changed=changed,
                        summary=prop.summary, files=sorted(files))
            echo(f"candidate {label}: valid, changes {', '.join(changed)}")
            await asyncio.to_thread(_write_json, cdir / "complete.json", {
                "fingerprint": fingerprint, "candidate": view, "layer_sha256": layer_tree_hash(layer_base),
                "written_at": _now(),
            })
            return {"view": view, "layer_base": layer_base, "changed": changed, "summary": prop.summary}
        why = problem
        echo(f"candidate {label}: refused ({problem})")
    view.update(status="invalid", reason=why, attempts=attempts)
    await asyncio.to_thread(_write_json, cdir / "complete.json", {
        "fingerprint": fingerprint, "candidate": view, "written_at": _now(),
    })
    if layer_base.exists():
        await asyncio.to_thread(shutil.rmtree, layer_base)
    return {"view": view, "layer_base": None, "changed": [], "summary": ""}


def _adopt(folder: Path, composition: Probed, baseline: Probed, mine: int, theirs: int, n: int) -> dict[str, Any]:
    """Write the adopted composition as a PROPOSED layer under the refine
    folder (never into a project)."""
    target = folder / "adopted"
    write_layer(target, composition.files)
    view = {
        "from": composition.label,
        "changed": composition.changed,
        "files": sorted(composition.files),
        "passed": mine, "current_passed": theirs, "compared": n,
        "better": mine > theirs,
        "layer": str(target / ".ironjarvis" / "harness"),
        "how_to_use": (
            "Nothing was applied to any project. To use it in a project folder: "
            f"ironjarvis bench-harness adopt --from \"{target}\" --folder <the project folder>"
        ),
        "written_at": _now(),
    }
    _write_json(folder / "adopted.json", view)
    return view


def _write_outputs(folder: Path, result: RefineResult, problems: list[str]) -> None:
    ledger = result.ledger
    history = _read_json(folder / "ledger.json") or {"invocations": []}
    history.setdefault("invocations", []).append({
        "at": _now(), "seed": result.seed, "tasks": result.tasks,
        **(ledger.to_dict() if ledger is not None else {}), "problems": problems,
    })
    _write_json(folder / "ledger.json", history)
    _write_json(folder / "results.json", {
        "settings": result.settings.identity(), "seed": result.seed, "tasks": result.tasks,
        "current": {"passed": result.baseline.score.passed, "scored": result.baseline.score.scored}
        if result.baseline else None,
        "candidates": result.candidates, "composition": result.composition, "adopted": result.adopted,
        "totals": ledger.grand_totals() if ledger is not None else None,
    })
    (folder / "report.md").write_bytes(refine_report(result, problems).encode("utf-8"))
    result.report_path = folder / "report.md"


def refine_report(result: RefineResult, problems: list[str]) -> str:
    s = result.settings
    lines = [
        f"# Harness refine: {s.provider} / {s.model}",
        "",
        f"{s.mode.capitalize()}, k={s.k}, seed {result.seed}, {len(result.tasks)} task(s): "
        + ", ".join(result.tasks) + ".",
        "",
        "Two harnesses are compared only on the tasks both of their runs scored; a run whose check "
        "could not run, or that did not run, leaves the comparison.",
        "",
        "| Harness | Built on | Changed | Passed / scored | Versus current | Note |",
        "|---|---|---|---|---|---|",
    ]
    if result.baseline is not None:
        lines.append(f"| current | - | - | {result.baseline.score.passed} / {result.baseline.score.scored} | - | |")
    for c in result.candidates:
        score = c.get("score") or {}
        vs = c.get("versus_current") or {}
        lines.append(
            f"| {c.get('label')} | {c.get('base', '-')} | {', '.join(c.get('changed') or []) or '-'} | "
            + (f"{score.get('passed')} / {score.get('scored')}" if score else "-")
            + " | " + (f"{vs.get('passed')} vs {vs.get('current_passed')} on {vs.get('compared')}" if vs else "-")
            + f" | {c.get('status')}" + (f": {c.get('reason')}" if c.get("reason") else "") + " |"
        )
    comp = result.composition or {}
    lines += ["", "## Composition", ""]
    if comp.get("note"):
        lines.append(comp["note"])
    if comp.get("winners"):
        lines.append("Winners: " + ", ".join(comp["winners"]) + ".")
    vs = comp.get("versus_current")
    if vs:
        lines.append(f"It passed {vs['passed']} where the current harness passed {vs['current_passed']}, "
                     f"on {vs['compared']} task(s) both scored.")
    lines += ["", "## Adopted", ""]
    if result.adopted:
        a = result.adopted
        lines.append(
            ("It scored BETTER than the current harness. " if a["better"] else
             "It scored THE SAME as the current harness — adopting it is your call. ")
            + a["how_to_use"]
        )
    else:
        lines.append("Nothing was adopted: the current harness stays.")
    if result.ledger is not None:
        t = result.ledger.grand_totals()
        lines += [
            "", "## Budget", "",
            f"{t['rollouts']} rollouts, {t['model_calls']} model calls, {t['tokens_in']} tokens in, "
            f"{t['tokens_out']} tokens out. Planned at most: {result.ledger.planned['proposals']} proposer "
            f"turns and {result.ledger.planned['probe_runs']} task runs.",
        ]
        for st in result.ledger.stages:
            tt = st.totals()
            lines.append(f"- {st.stage} {st.label}: {tt['rollouts']} rollouts, {tt['model_calls']} calls, "
                         f"{tt['tokens_in']} in / {tt['tokens_out']} out")
        if problems:
            lines += ["", "THE LEDGER DOES NOT ADD UP: " + "; ".join(problems)]
        else:
            lines += ["", "Every stage's records equal its ledger, and the totals match."]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# adopt: copy an adopted layer into a project folder (the user's command)
# --------------------------------------------------------------------------- #


def adopt_into(source: Path, folder: Path, *, known_tools: Any = None) -> list[str]:
    """Copy the adopted layer at ``source`` (a refine folder's ``adopted``, or
    any folder holding ``.ironjarvis/harness/``) into ``folder``'s harness
    files. Refuses (``BenchRefused``) when the source is not a valid layer,
    when ``folder`` already has a DIFFERENT file of the same name (named;
    nothing is copied), or when the result would not be a valid layer (what
    was copied is removed again). Returns the files written. BLOCKING."""
    from ..harness.components import read_project_layer

    source = Path(source)
    if (source / "adopted").is_dir() and not _harness_dir(source).is_dir():
        source = source / "adopted"
    layer = read_project_layer(source, known_tools=known_tools, session_id=None)
    if not layer.used:
        raise BenchRefused(f"{source} holds no valid harness files ({layer.note or 'none found'}).")
    files = read_layer_files(source)
    target = _harness_dir(folder)
    clash = []
    for name, text in files.items():
        path = target / name
        if path.exists():
            current = path.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n")
            if current != text:
                clash.append(name)
    if clash:
        raise BenchRefused(
            f"{folder} already has its own {', '.join(sorted(clash))} in .ironjarvis/harness, and it "
            "differs; nothing was copied. Merge it by hand, or move that file away first."
        )
    written = []
    for name, text in sorted(files.items()):
        path = target / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))
            written.append(name)
    check = read_project_layer(folder, known_tools=known_tools, session_id=None)
    if not check.used:
        for name in written:
            with contextlib.suppress(OSError):
                (target / name).unlink()
        raise BenchRefused(f"With these files the folder's harness would not be valid ({check.note}); nothing was kept.")
    return written


__all__ = [
    "MODES",
    "RefineLedger",
    "RefineResult",
    "RefineSettings",
    "Score",
    "adopt_into",
    "build_evidence",
    "compare",
    "planned_budget",
    "run_refine",
    "validate_candidate",
]

