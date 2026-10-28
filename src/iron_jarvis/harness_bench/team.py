"""The TEAM runner (v1.396.0, the long-jobs plan's Phase 7: MEASURED).

``bench-harness run --runner jarvis-team --team <file.toml>`` runs every task
the way the Jarvis runner does — the streaming chat lane in harness mode, AS A
BUILD SESSION (``build:bench-…``), the planner's model HELD, ``ask_user`` off —
with a MODEL TEAM in the benchmark's home: the file's members are made as
custom agents with roles (``agents/model_team``), the planner sees them in its
"# Your model team" list, and when it asks for a role the TEAM ROUTER
(``model_team.route_role``) hands the piece to a member, held to the member's
own model. Nothing about the team is special to the benchmark: it is the
product's own team, made from a file.

THE TEAM FILE (TOML)::

    [planner]                  # the model the coding session talks to
    provider = "claude-cli"
    model = "claude-opus-4-8"  # a model, never "default"
    # team_first = true        # optional (default false): see TEAM-FIRST below

    [[members]]                # one per member (at most 10)
    name = "glm"
    provider = "custom"
    model = "glm-5.3-flash"
    roles = ["read", "code"]   # model_team.ROLES: read, code, review, test
    # description = "..."      # optional: what the planner's list says
    # account = "..."          # optional: an Iron-Proxy account id
    # effort = "medium"        # optional: low / medium / high (default: --effort)
    # max_steps = 60           # optional: steps a piece (default: --rounds)

A member's EFFORT reaches its model calls only where its model takes a level
(``providers.reasoning``; "not offered" otherwise), and its STEP LIMIT is its
record's ``max_steps`` (v1.396.0 review: by default the run's ``--effort`` and
the planner's round budget — see :data:`MAX_MEMBER_STEPS` for why). Both are
recorded on every part and are part of the setup.

BEFORE THE FIRST TASK (v1.396.0 review, :mod:`.preflight`): the planner and
every member are checked — the provider exists, is connected (a CLI's sign-in
by its CACHED answer only), a model server answers its one bounded model-list
request with the home's saved key and lists the model — and anything wrong is
refused in one sentence, in a dry run and a real run alike.

Every part of the file is in the configuration's IDENTITY (and so its key,
folder and every run's fingerprint): a changed team — another member, another
model, another role — is a new configuration folder, never a reused record.
So are the router's thresholds (``team_record.MIN_RUNS`` / ``GOOD_RATE`` /
``FAIL_STREAK`` …, ``build_accounts.NEW_WORK_MAX_PERCENT`` …) and the router's
own source: tuned defaults are a new setup.

TEAM-FIRST (v1.400.0). ``team_first = true`` under ``[planner]`` runs the team
with the user's "Hand work to my team first" setting ON (``Config.team_first``:
the planner is told to hand reading, test runs, first drafts and reviews to the
team by role — ``agents/model_team.team_section``). The runner sets the
setting to the file's value, IN THE PROCESS ONLY (never written to any
config.toml, never the user's home), for that invocation's runs, and puts it
back when the invocation ends (:meth:`JarvisTeamRunner.prepare` /
:meth:`~JarvisTeamRunner.close`). It is part of the identity ONLY when true:
a file without the key, or with ``team_first = false``, is the team exactly as
before (the same identity, the same folder), and ``true`` is a new folder. The
report shows such a setup as ``jarvis-team (team-first)``.

TEAM MODE (v1.403.0). ``team = true`` under ``[planner]`` runs the planner as
a CODING SESSION's team LEAD (``harness/team``): it holds no tool that changes
a file or runs a command — enforced, not asked — and hands each piece to its
WORKERS (the members with the ``code`` role, one or two) while Iron Jarvis
hands every change to its REVIEWER (the member with ``review`` and not
``code``) before the reply ends. The runner arms the session's team around
every task's turn exactly as a Build session with a team does
(:meth:`JarvisTeamRunner.turn_scope`). It is in the identity ONLY when true
(``team_mode`` — and the team module's source), and the report shows such a
setup with ``lead (locked)``. This is how the 0-of-108 result (a planner told
to hand work out handed out nothing) is measured again.

WHERE THE TEAM LIVES. In the benchmark's own scratch home (``--root``), never
the user's — the members are made there before the first task
(:meth:`JarvisTeamRunner.prepare`) and removed again when the invocation ends
(:meth:`close`), so a single-model row run in the same home afterwards ("Claude
alone") sees no team. The team router's RECORD per member and role
(``team_record.TeamRoleRun``) stays in that home's database: it is "the record
the router built during the benchmark", read after every run into the run's
record (``team_records``) for the report's tuning table. A member a stopped
invocation left behind is removed by the next run of either runner; a member
the benchmark did not make refuses both (it would join the team, or turn a
single-model row into a team).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .runners import (
    DEFAULT_DIALECT,
    DEFAULT_ROUNDS,
    EFFORTS,
    MEMBER_PROMPT,
    NOT_OFFERED,
    JarvisRunner,
    _bench_made,
    _members,
    _module_bytes,
    _sha,
)

#: The runner's name (``--runner jarvis-team``).
TEAM_RUNNER = "jarvis-team"
#: At most this many members (the planner's team list shows ten).
MAX_MEMBERS = 10
#: A member's name: what the planner calls it and the registry keys it by.
_NAME_RX = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_PLANNER_KEYS = frozenset({"provider", "model", "team_first", "team"})
_MEMBER_KEYS = frozenset({"name", "provider", "model", "roles", "description", "account", "effort", "max_steps"})
#: v1.396.0 review: a member's step limit when the file names none is the
#: planner's own round budget (``--rounds``, 60 by default). A member handed a
#: coding piece used to get the HOME's ``max_agent_steps`` (12 — what every
#: helper on the turn's own model got in the earlier single-model rows, where
#: no helper was ever started), so a piece the single-model rows' model
#: finishes in its 60 rounds could be cut off and read as the team being
#: worse. Now a member gets as many steps a piece as the model it is compared
#: with gets for the whole task; the task's time limit still bounds the run.
#: The file may name 1 to :data:`MAX_MEMBER_STEPS`.
MAX_MEMBER_STEPS = 200
_TOP_KEYS = frozenset({"planner", "members"})


def _refused(text: str) -> Exception:
    from .bench import BenchRefused

    return BenchRefused(text)


@dataclass(frozen=True)
class Member:
    name: str
    provider: str
    model: str
    roles: tuple[str, ...]
    description: str = ""
    account: str = ""
    #: v1.396.0 review: the member's reasoning level as the FILE names it
    #: ("" = the run's ``--effort``) and its step limit a piece (0 = the
    #: planner's round budget). The runner resolves both
    #: (:meth:`JarvisTeamRunner.member_effort` / :meth:`~JarvisTeamRunner.member_steps`).
    effort: str = ""
    max_steps: int = 0

    def identity(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name, "provider": self.provider, "model": self.model,
            "roles": sorted(self.roles), "description": self.description,
        }
        if self.account:
            out["account"] = self.account
        if self.effort:
            out["effort"] = self.effort
        if self.max_steps:
            out["max_steps"] = self.max_steps
        return out

    def words(self) -> str:
        return (f"{self.name}: {self.provider} / {self.model}, roles {', '.join(self.roles)}"
                + (f", account {self.account}" if self.account else ""))


@dataclass(frozen=True)
class TeamSpec:
    planner_provider: str
    planner_model: str
    members: tuple[Member, ...] = field(default_factory=tuple)
    #: Where it was read from (never in the identity: the same team in
    #: another file is the same team).
    source: str = ""
    #: v1.400.0: the file's ``team_first`` under ``[planner]`` — the runs use
    #: the "Hand work to my team first" setting (see the module docstring).
    team_first: bool = False
    #: v1.403.0: the file's ``team`` under ``[planner]`` — the planner runs as
    #: a coding session's team LEAD (``harness/team``: locked — no tool that
    #: changes a file or runs something), the members holding ``code`` as its
    #: WORKERS and the member holding only ``review`` as its REVIEWER.
    team_mode: bool = False

    def identity(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "planner": {"provider": self.planner_provider, "model": self.planner_model},
            "members": [m.identity() for m in sorted(self.members, key=lambda m: m.name)],
        }
        # Only when ON: a team without it (every folder made before v1.400.0,
        # and ``team_first = false``) keeps exactly its identity.
        if self.team_first:
            out["team_first"] = True
        # v1.403.0: the same rule — a file without ``team`` (or false) keeps
        # exactly its identity; ``team = true`` is a new setup.
        if self.team_mode:
            out["team_mode"] = True
        return out

    def session_team(self) -> Any:
        """v1.403.0: the coding session's team of a ``team = true`` file —
        its workers (members holding ``code``) and its reviewer (the member
        holding ``review`` and not ``code``), or None for any other file."""
        if not self.team_mode:
            return None
        from ..harness.team import SessionTeam

        workers = tuple(m.name for m in self.members if "code" in m.roles)
        reviewer = next((m.name for m in self.members if "review" in m.roles and "code" not in m.roles), "")
        return SessionTeam(workers=workers, reviewer=reviewer)

    @property
    def key(self) -> str:
        import json

        return hashlib.sha256(json.dumps(self.identity(), sort_keys=True).encode("utf-8")).hexdigest()


def _text(value: Any, where: str, key: str, *, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise _refused(f"The team file's {where} needs {key} as a word in quotes.")
    return value.strip()


def parse_team(data: Any, *, source: str = "") -> TeamSpec:
    """A team file's parsed TOML → :class:`TeamSpec`, or ``BenchRefused`` in
    one plain sentence naming what is wrong."""
    from ..agents import model_team as MT
    from ..core.models import AgentType
    from .bench import MODEL_NOT_A_MODEL, NOT_A_MODEL

    if not isinstance(data, dict):
        raise _refused("The team file must hold a [planner] table and [[members]] tables.")
    unknown = sorted(set(data) - _TOP_KEYS)
    if unknown:
        raise _refused("The team file has keys it does not know: " + ", ".join(unknown)
                       + " (it takes [planner] and [[members]]).")
    planner = data.get("planner")
    if not isinstance(planner, dict):
        raise _refused("The team file needs a [planner] table with provider and model.")
    extra = sorted(set(planner) - _PLANNER_KEYS)
    if extra:
        raise _refused("The team file's [planner] has keys it does not know: " + ", ".join(extra) + ".")
    p_provider = _text(planner.get("provider"), "[planner]", "provider")
    p_model = _text(planner.get("model"), "[planner]", "model")
    if p_model.lower() in NOT_A_MODEL:
        raise _refused("The team file's planner: " + MODEL_NOT_A_MODEL.format(model=p_model)
                       .replace("--model ", "model "))
    team_first = planner.get("team_first", False)
    if not isinstance(team_first, bool):
        raise _refused("The team file's [planner] team_first is true or false (without quotes).")
    raw = data.get("members")
    if not isinstance(raw, list) or not raw:
        raise _refused("The team file needs at least one [[members]] table.")
    if len(raw) > MAX_MEMBERS:
        raise _refused(f"The team file names {len(raw)} members; a team has at most {MAX_MEMBERS}.")
    reserved = {t.value for t in AgentType} | {"planner"}
    members: list[Member] = []
    seen: set[str] = set()
    for i, row in enumerate(raw, 1):
        where = f"member {i}"
        if not isinstance(row, dict):
            raise _refused(f"The team file's {where} is not a table.")
        extra = sorted(set(row) - _MEMBER_KEYS)
        if extra:
            raise _refused(f"The team file's {where} has keys it does not know: " + ", ".join(extra) + ".")
        name = _text(row.get("name"), where, "name")
        where = f"member {name!r}"
        if not _NAME_RX.match(name):
            raise _refused(f"The team file's {where}: a name is 1 to 40 lowercase letters, digits, "
                           "'-' or '_', starting with a letter.")
        if name in reserved:
            raise _refused(f"The team file's {where}: {name} is the name of one of Iron Jarvis's own "
                           "agents; pick another.")
        if name in seen:
            raise _refused(f"The team file names {name!r} twice.")
        seen.add(name)
        m_provider = _text(row.get("provider"), where, "provider")
        m_model = _text(row.get("model"), where, "model")
        if m_model.lower() in NOT_A_MODEL:
            raise _refused(f"The team file's {where}: " + MODEL_NOT_A_MODEL.format(model=m_model)
                           .replace("--model ", "model "))
        roles = row.get("roles")
        if not isinstance(roles, list) or not roles or not all(isinstance(r, str) for r in roles):
            raise _refused(f"The team file's {where} needs roles, a list such as [\"read\", \"code\"].")
        bad = MT.unknown_roles(roles)
        if bad:
            raise _refused(f"The team file's {where} has roles Iron Jarvis does not know: "
                           + ", ".join(bad) + " (the roles are " + ", ".join(MT.ROLE_IDS) + ").")
        description = _text(row.get("description"), where, "description", required=False)
        account = _text(row.get("account"), where, "account", required=False)
        effort = _text(row.get("effort"), where, "effort", required=False).lower()
        if effort and effort not in EFFORTS:
            raise _refused(f"The team file's {where}: effort is one of {', '.join(EFFORTS)}.")
        steps = row.get("max_steps", 0)
        if isinstance(steps, bool) or not isinstance(steps, int) or (
                "max_steps" in row and not 1 <= steps <= MAX_MEMBER_STEPS):
            raise _refused(f"The team file's {where}: max_steps is a whole number from 1 to "
                           f"{MAX_MEMBER_STEPS}.")
        members.append(Member(name=name, provider=m_provider, model=m_model,
                              roles=tuple(MT.clean_roles(roles)), description=description, account=account,
                              effort=effort, max_steps=int(steps or 0)))
    team_mode = planner.get("team", False)
    if not isinstance(team_mode, bool):
        raise _refused("The team file's [planner] team is true or false (without quotes).")
    if team_mode:
        # v1.403.0: a coding session's team — one or two WORKERS (the members
        # holding code) and at most one REVIEWER (holding review, not code);
        # every member is one or the other.
        from ..harness.team import MAX_WORKERS

        workers = [m for m in members if "code" in m.roles]
        reviewers = [m for m in members if "review" in m.roles and "code" not in m.roles]
        others = [m.name for m in members if m not in workers and m not in reviewers]
        if not workers:
            raise _refused("With team = true the team file needs a worker: a member with the code role.")
        if len(workers) > MAX_WORKERS:
            raise _refused(f"With team = true a team has at most {MAX_WORKERS} workers (members with the code "
                           f"role); this file names {len(workers)}.")
        if len(reviewers) > 1:
            raise _refused("With team = true a team has at most one reviewer (a member with the review role "
                           "and not code); this file names " + ", ".join(m.name for m in reviewers) + ".")
        if others:
            raise _refused("With team = true every member is a worker (code) or the reviewer (review); "
                           + ", ".join(others) + " is neither.")
    return TeamSpec(planner_provider=p_provider, planner_model=p_model, members=tuple(members), source=source,
                    team_first=team_first, team_mode=team_mode)


def load_team_file(path: str | Path) -> TeamSpec:
    """Read and check a team file; ``BenchRefused`` in words when it cannot be
    read or is not a team."""
    import tomllib

    where = Path(path)
    try:
        data = tomllib.loads(where.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _refused(f"The team file {where} could not be read ({exc.strerror or exc}).") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise _refused(f"The team file {where} is not valid TOML ({exc}).") from None
    return parse_team(data, source=str(where))


#: v1.400.0: a dry run's line for the team's team-first switch.
TEAM_FIRST_ON_LINE = ("Team-first: on (the planner is told to hand reading, test runs, first drafts and "
                      "reviews to the team by role).")
TEAM_FIRST_OFF_LINE = "Team-first: off (the planner is shown the team and decides for itself)."


def team_first_line(on: bool) -> str:
    return TEAM_FIRST_ON_LINE if on else TEAM_FIRST_OFF_LINE


#: v1.403.0: a dry run's line for a ``team = true`` file.
TEAM_MODE_LINE = ("Team lead: locked (the planner runs as a coding session's team lead: no tool that "
                  "changes a file or runs a command; the members with the code role are its workers, the "
                  "member with the review role its reviewer).")


def router_identity() -> dict[str, Any]:
    """The team router as it decides (part of the team runner's identity):
    its thresholds by value — the numbers the benchmark is run to tune — and
    its own source, so a changed router is a new setup."""
    from ..agents import model_team as MT
    from ..agents import team_record as TR
    from ..harness import build_accounts as BA

    return {
        "router_sha256": _sha(_module_bytes(MT) + b"\0" + _module_bytes(TR)),
        "thresholds": thresholds(),
        "burst": {"piece_percent": MT.PIECE_PERCENT, "placed_recent_s": MT.PLACED_RECENT_S,
                  "unknown_burst_max": MT.UNKNOWN_BURST_MAX},
        "takeover_max_percent": BA.TAKEOVER_MAX_PERCENT,
    }


def thresholds() -> dict[str, Any]:
    """The four numbers Phase 7 tunes, as they are now (the report says them
    beside the record they judged)."""
    from ..agents import team_record as TR
    from ..harness import build_accounts as BA

    return {
        "min_runs": TR.MIN_RUNS, "good_rate": TR.GOOD_RATE, "fail_streak": TR.FAIL_STREAK,
        "record_window": TR.RECORD_WINDOW, "piece_fails": TR.PIECE_FAILS,
        "new_work_max_percent": BA.NEW_WORK_MAX_PERCENT,
    }


class JarvisTeamRunner(JarvisRunner):
    """The Jarvis runner with a model team (see the module docstring)."""

    name = TEAM_RUNNER

    def __init__(self, platform: Any, *, team: TeamSpec, round_budget: int = DEFAULT_ROUNDS,
                 dialect: str = DEFAULT_DIALECT, effort: str = "") -> None:
        super().__init__(platform, provider=team.planner_provider, model=team.planner_model,
                         round_budget=round_budget, dialect=dialect, effort=effort)
        self.team = team
        self._reads = 0
        #: v1.396.0 review: what the checks before the first task could only
        #: SAY (``check_ready``): a dry run lists them, a real run echoes them.
        self.ready_notes: list[str] = []
        #: v1.400.0: the home's ``team_first`` value before :meth:`prepare`
        #: set the file's (put back by :meth:`close`); None = not set by us.
        self._team_first_before: bool | None = None

    # -- identity ---------------------------------------------------------- #

    def identity(self) -> dict[str, Any]:
        """The Jarvis runner's identity (unchanged) plus the TEAM, the router
        and the instructions every member is made with."""
        out = {
            **super().identity(),
            "team": self.team.identity(),
            "router": router_identity(),
            "member_prompt_sha256": _sha(MEMBER_PROMPT),
            # v1.396.0 review: each member's step limit a piece and the level
            # its calls SEND, as resolved: another limit or level is another
            # setup.
            "member_steps": {m.name: self.member_steps(m) for m in self.team.members},
            "member_effort": {m.name: self.member_effort(m) for m in self.team.members},
        }
        if self.team.team_mode:
            # v1.403.0: the coding-session team's own rules (the lead's lock,
            # its brief, the worker rules) are part of a team-mode setup.
            from ..harness import team as harness_team

            out["team_mode_sha256"] = _sha(_module_bytes(harness_team))
        return out

    # -- v1.403.0: the planner as a coding session's team LEAD -------------- #

    def lead_locked(self) -> bool:
        return bool(self.team.team_mode)

    def turn_scope(self) -> Any:
        """A ``team = true`` file's turns run with the coding session's team
        armed (``harness/team.SESSION_TEAM``) — exactly as a Build session with
        a team arms it — so the planner is the team LEAD."""
        import contextlib

        team = self.team.session_team()
        if team is None:
            return contextlib.nullcontext()
        from ..harness import team as harness_team

        @contextlib.contextmanager
        def _armed():
            token = harness_team.set_team(team)
            try:
                yield
            finally:
                harness_team.reset_team(token)

        return _armed()

    def dry_run_lines(self) -> list[str]:
        lines = [f"Team: planner {self.team.planner_provider} / {self.team.planner_model}; "
                 f"{len(self.team.members)} member(s):"]
        # v1.400.0: whether the planner is told to hand work to the team first.
        lines.append("  " + team_first_line(self.team.team_first))
        # v1.403.0: whether the planner runs as the coding session's team lead.
        if self.team.team_mode:
            lines.append("  " + TEAM_MODE_LINE)
        for m in sorted(self.team.members, key=lambda m: m.name):
            effort = self.member_effort(m)
            lines.append(f"  {m.words()}")
            lines.append(f"    {m.name}: effort {effort or 'none asked'}"
                         + (f" ({m.provider} / {m.model} takes no level)" if effort == NOT_OFFERED else "")
                         + f"; at most {self.member_steps(m)} steps a piece")
        if self.ready_notes:
            lines.append("Checked before the first task:")
            lines += [f"  {note}" for note in self.ready_notes]
        return lines

    # -- the members ------------------------------------------------------- #

    def member_specs(self) -> dict[str, tuple[str, str]]:
        return {m.name: (m.provider, m.model) for m in self.team.members}

    def member_steps(self, member: Member) -> int:
        """The steps a member's piece may take: its file's, else the planner's
        round budget (:data:`MAX_MEMBER_STEPS` explains why)."""
        return int(member.max_steps or self.round_budget)

    def member_effort(self, member: Member) -> str | None:
        """The level a member's calls SEND: its file's effort, else the run's
        ``--effort``, when its model takes it; ``"not offered"`` when it does
        not; None when no effort was asked at all."""
        from ..providers.reasoning import supports

        level = member.effort or self.effort
        if not level:
            return None
        return level if supports(member.provider, member.model, level) else NOT_OFFERED

    def call_reasoning(self, who: str) -> str:
        """The level a call by ``who`` (a member's name) is sent with ("" =
        none: the planner's own calls carry the turn's level already)."""
        member = next((m for m in self.team.members if m.name == who), None)
        level = self.member_effort(member) if member is not None else None
        return level if level and level != NOT_OFFERED else ""

    def member_facts(self) -> dict[str, dict[str, Any]]:
        return {m.name: {"effort_passed": self.member_effort(m), "max_steps": self.member_steps(m)}
                for m in self.team.members}

    def check_ready(self) -> None:
        """Refuse a team run whose home holds a member the benchmark did not
        make (it would join the team), whose file names an account that
        Iron-Proxy in this home does not hold, or whose planner or members
        cannot run (:mod:`.preflight`: an unknown provider, a CLI signed out
        by its cached answer, a model server that does not answer, wants a key
        the home lacks, or does not list the model). What cannot be known
        without starting a CLI is kept in :attr:`ready_notes`. BLOCKING
        (cached reads; at most one bounded request per model server)."""
        from . import preflight as PF

        ready = PF.check_models(self.platform, [
            ("planner", self.team.planner_provider, self.team.planner_model),
            *((m.name, m.provider, m.model) for m in sorted(self.team.members, key=lambda m: m.name)),
        ])
        if ready.problems:
            raise _refused("This team cannot run yet:\n  " + "\n  ".join(ready.problems))
        notes = list(ready.notes)
        for line in (PF.roles_note(self.team.members), PF.iron_proxy_note(self.platform)):
            if line:
                notes.append(line)
        self.ready_notes = notes
        mine = {m.name for m in self.team.members}
        foreign = sorted(r.name for r in _members(self.platform) if not _bench_made(r))
        if foreign:
            raise _refused(
                "This benchmark's home has model-team members the team file does not make ("
                + ", ".join(foreign) + "), so they would join the team. Run it with a --root that has "
                "no other team."
            )
        clash = sorted(name for name in mine
                       if (r := self.platform.agents_registry.get(name)) is not None and not _bench_made(r))
        if clash:
            raise _refused("This benchmark's home already has an agent named " + ", ".join(clash)
                           + " that the benchmark did not make; rename the member in the team file.")
        wanted = [m for m in self.team.members if m.account]
        if wanted:
            from ..harness import build_accounts as BA

            snap = BA.snapshot(getattr(self.platform, "iron_proxy", None))
            held = {str(p.get("id") or "") for p in (snap[0] if snap else [])}
            missing = [m for m in wanted if m.account not in held]
            if missing:
                raise _refused(
                    "The team file names an account for " + ", ".join(m.name for m in missing)
                    + (", but Iron-Proxy is not on in the benchmark's home, so no account can be pinned"
                       if snap is None else ", but Iron-Proxy in the benchmark's home holds no such account")
                    + ": leave the account out (the member then runs on this PC's own sign-in)."
                )

    def prepare(self) -> None:
        """Make the file's members (and remove members an earlier, stopped
        team invocation made that this file does not name), and set the
        home's "Hand work to my team first" setting to the file's value for
        this invocation — in the process only, put back by :meth:`close`.
        BLOCKING."""
        config = getattr(self.platform, "config", None)
        if config is not None and self._team_first_before is None:
            self._team_first_before = getattr(config, "team_first", False) is True
            config.team_first = bool(self.team.team_first)
        mine = {m.name for m in self.team.members}
        registry = self.platform.agents_registry
        for record in _members(self.platform):
            if _bench_made(record) and record.name not in mine:
                registry.remove(record.name)
        for m in self.team.members:
            registry.register(
                m.name, MEMBER_PROMPT.format(name=m.name), [],
                description=m.description or f"{m.name}: {', '.join(m.roles)} on {m.model}",
                provider=m.provider, model=m.model, roles=list(m.roles), account=m.account,
                # v1.396.0 review: its own step limit (never the home's 12).
                max_steps=self.member_steps(m),
            )

    def close(self) -> None:
        """Remove the members this runner made (their record stays), and put
        the home's team-first setting back as it was. BLOCKING."""
        try:
            registry = self.platform.agents_registry
            for m in self.team.members:
                record = registry.get(m.name)
                if record is not None and _bench_made(record):
                    registry.remove(m.name)
        finally:
            before, self._team_first_before = self._team_first_before, None
            config = getattr(self.platform, "config", None)
            if before is not None and config is not None:
                config.team_first = before

    async def team_records_after(self) -> dict[str, Any] | None:
        """The router's record for every member and role after a run — the
        rows of the member's CURRENT model (decision 7) — and the thresholds
        it was judged with."""
        return await asyncio.to_thread(self._read_records)

    def _read_records(self) -> dict[str, Any]:
        from ..agents import model_team as MT
        from ..agents import team_record as TR

        out: dict[str, Any] = {}
        try:
            models: dict[str, tuple[str, str]] = {}
            for m in self.team.members:
                record = self.platform.agents_registry.get(m.name)
                models[m.name] = (m.provider, MT.pinned_model(self.platform, record) if record else m.model)
            got = TR.records_for(self.platform.engine, [m.name for m in self.team.members], models=models)
            for m in self.team.members:
                rows = got.get(m.name, {})
                out[m.name] = {role: rows.get(role, TR.RoleRecord()).view() for role in m.roles}
        except Exception:  # noqa: BLE001 — a record that cannot be read is said as none
            out = {m.name: {} for m in self.team.members}
        # When it was read (the report shows the NEWEST: runs a second apart
        # share a finished_at to the second), and in this runner's order.
        self._reads += 1
        return {"members": out, "thresholds": thresholds(), "read_at": time.time(), "read": self._reads,
                # v1.396.0 review: whether the router HAD usage numbers to
                # judge an account by (Iron-Proxy's cached snapshot). Off, its
                # 80% rule never applied — the report says so.
                "usage_numbers": self._usage_numbers()}

    def _usage_numbers(self) -> bool:
        try:
            from ..harness import build_accounts as BA

            return BA.snapshot(getattr(self.platform, "iron_proxy", None)) is not None
        except Exception:  # noqa: BLE001 — not readable: none
            return False
