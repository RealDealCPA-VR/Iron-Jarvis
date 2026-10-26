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

    [[members]]                # one per member (at most 10)
    name = "glm"
    provider = "custom"
    model = "glm-5.3-flash"
    roles = ["read", "code"]   # model_team.ROLES: read, code, review, test
    # description = "..."      # optional: what the planner's list says
    # account = "..."          # optional: an Iron-Proxy account id

Every part of the file is in the configuration's IDENTITY (and so its key,
folder and every run's fingerprint): a changed team — another member, another
model, another role — is a new configuration folder, never a reused record.
So are the router's thresholds (``team_record.MIN_RUNS`` / ``GOOD_RATE`` /
``FAIL_STREAK`` …, ``build_accounts.NEW_WORK_MAX_PERCENT`` …) and the router's
own source: tuned defaults are a new setup.

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
    MEMBER_PROMPT,
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
_PLANNER_KEYS = frozenset({"provider", "model"})
_MEMBER_KEYS = frozenset({"name", "provider", "model", "roles", "description", "account"})
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

    def identity(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name, "provider": self.provider, "model": self.model,
            "roles": sorted(self.roles), "description": self.description,
        }
        if self.account:
            out["account"] = self.account
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

    def identity(self) -> dict[str, Any]:
        return {
            "planner": {"provider": self.planner_provider, "model": self.planner_model},
            "members": [m.identity() for m in sorted(self.members, key=lambda m: m.name)],
        }

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
        members.append(Member(name=name, provider=m_provider, model=m_model,
                              roles=tuple(MT.clean_roles(roles)), description=description, account=account))
    return TeamSpec(planner_provider=p_provider, planner_model=p_model, members=tuple(members), source=source)


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

    # -- identity ---------------------------------------------------------- #

    def identity(self) -> dict[str, Any]:
        """The Jarvis runner's identity (unchanged) plus the TEAM, the router
        and the instructions every member is made with."""
        return {
            **super().identity(),
            "team": self.team.identity(),
            "router": router_identity(),
            "member_prompt_sha256": _sha(MEMBER_PROMPT),
            # A member's run stops at the home's step limit (it names none of
            # its own): another limit is another setup.
            "member_steps": int(getattr(self.platform.config, "max_agent_steps", 0) or 0),
        }

    def dry_run_lines(self) -> list[str]:
        lines = [f"Team: planner {self.team.planner_provider} / {self.team.planner_model}; "
                 f"{len(self.team.members)} member(s):"]
        lines += [f"  {m.words()}" for m in sorted(self.team.members, key=lambda m: m.name)]
        return lines

    # -- the members ------------------------------------------------------- #

    def member_specs(self) -> dict[str, tuple[str, str]]:
        return {m.name: (m.provider, m.model) for m in self.team.members}

    def check_ready(self) -> None:
        """Refuse a team run whose home holds a member the benchmark did not
        make (it would join the team), or whose file names an account that
        Iron-Proxy in this home does not hold. BLOCKING (cached reads)."""
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
        team invocation made that this file does not name). BLOCKING."""
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
            )

    def close(self) -> None:
        """Remove the members this runner made (their record stays). BLOCKING."""
        registry = self.platform.agents_registry
        for m in self.team.members:
            record = registry.get(m.name)
            if record is not None and _bench_made(record):
                registry.remove(m.name)

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
        return {"members": out, "thresholds": thresholds(), "read_at": time.time(), "read": self._reads}
