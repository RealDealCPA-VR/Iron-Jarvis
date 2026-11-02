"""v1.424.0 (audit D5): OpenCode as the model behind a CHAT turn or an agent
run never runs its own tools — no card, no ledger row, no Undo was the old
shape (``opencode run`` with OpenCode's own default, ``"*": "allow"``).

THE VERDICTS ARE OPENCODE'S OWN. The fixture
``tests/fixtures/opencode_text_only_v14240.json`` was written by the REAL
installed opencode 1.18.18 (``IJ_REGEN_OPENCODE_TEXT_ONLY=1``, this PC only:
scratch ``XDG_*`` folders, ``opencode debug agent <name>`` — the agent's
resolved rules and the tools it OFFERS the model, no model call). The
simulator below is ``test_build_app_opencode_review_v1413``'s model of
OpenCode's merge, extended by what was MEASURED here:

* an agent's OWN ``permission`` from the user's config comes AFTER the
  config's (and so ``OPENCODE_PERMISSION``'s) rules;
* ``opencode run`` adds session rules after the agent's (question,
  plan_enter, plan_exit: deny);
* a tool is not OFFERED when the LAST rule whose permission matches it says
  ``deny`` with the pattern ``*`` (OpenCode's own ``disabled``: write and
  apply_patch ask as ``edit``).

The simulator is checked against every recorded run first, then judges a
wider set of user configs.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from iron_jarvis.hosted import opencode as O
from iron_jarvis.providers.adapters import opencode_cli as oc
from iron_jarvis.providers.adapters.base import LLMMessage
from tests.test_build_app_opencode_review_v1413 import AGENTS, DEFAULTS, _merge, _rules, _wild

FIXTURE = Path(__file__).parent / "fixtures" / "opencode_text_only_v14240.json"
FIXED_AGENT = "iron-jarvis-text-fixture"

#: OpenCode's own session rules for ``opencode run`` (read from the 1.18.18
#: program: ``permission:[{question deny},{plan_enter deny},{plan_exit deny}]``).
RUN_SESSION = [("question", "*", "deny"), ("plan_enter", "*", "deny"), ("plan_exit", "*", "deny")]
#: The tools ``opencode debug agent build`` listed on 1.18.18.
OFFERED = ["invalid", "question", "bash", "read", "glob", "grep", "edit", "write", "task",
           "webfetch", "todowrite", "websearch", "skill"]
#: Tools of a user's own MCP servers and plugins (their permission is their id).
OTHER_TOOLS = ["github_create_issue", "quickbooks_invoice_create", "my_plugin_tool", "apply_patch",
               "list", "lsp", "codesearch"]


# --------------------------------------------------------------------------- #
# The cases (a user's own config, each one allowing what it can)
# --------------------------------------------------------------------------- #
#: name -> {global, project, md_agents, env, default}: ``global`` is the
#: user's ``opencode.json``, ``project`` their project's ``.opencode/
#: opencode.json``, ``md_agents`` their ``agent/<name>.md`` files, ``env``
#: OpenCode variables already in their environment, ``default`` the agent
#: ``opencode run`` would use for them.
CASES: dict[str, dict[str, Any]] = {
    "permissive": {
        "global": {"permission": {"*": "allow", "bash": {"git *": "allow", "*": "allow"}, "web*": "allow"},
                   "tools": {"write": True}},
        "project": {"permission": {"edit": "allow", "Task": "allow"}},
        "default": "build",
    },
    "agent-level": {
        "global": {"permission": {"*": "allow"},
                   "agent": {"build": {"permission": {"bash": "allow", "edit": "allow"}}}},
        "default": "build",
    },
    "custom-default-agent": {
        "global": {"default_agent": "mine",
                   "agent": {"mine": {"mode": "primary", "permission": {"*": "allow"}}}},
        "default": "mine",
    },
    "string-permission": {"global": {"permission": "allow"}, "default": "build"},
    "inline-and-env": {
        "global": {},
        "env": {"OPENCODE_CONFIG_CONTENT": json.dumps({"permission": {"bash": "allow"},
                                                       "agent": {"helper": {"mode": "subagent"}}}),
                "OPENCODE_PERMISSION": json.dumps({"*": "allow", "edit": "allow"})},
        "default": "build",
    },
    "md-agent": {
        "global": {},
        "md_agents": {"build": "---\ndescription: mine\nmode: primary\npermission:\n  bash: allow\n---\nHi.\n"},
        "default": "build",
    },
}


def _layout(root: Path, case: dict[str, Any]) -> dict[str, Path]:
    """Write a case's files under ``root`` (scratch) and return the folders."""
    dirs = {k: root / k for k in ("cfg", "data", "cache", "state", "managed", "work")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    conf = dirs["cfg"] / "opencode"
    conf.mkdir(exist_ok=True)
    (conf / "opencode.json").write_text(json.dumps(case.get("global") or {}), encoding="utf-8")
    if case.get("project") is not None:
        (dirs["work"] / ".opencode").mkdir(exist_ok=True)
        (dirs["work"] / ".opencode" / "opencode.json").write_text(json.dumps(case["project"]), encoding="utf-8")
    for name, text in (case.get("md_agents") or {}).items():
        (conf / "agent").mkdir(exist_ok=True)
        (conf / "agent" / f"{name}.md").write_text(text, encoding="utf-8")
    dirs["conf"] = conf
    return dirs


def _reader_env(dirs: dict[str, Path], case: dict[str, Any]) -> dict[str, str]:
    """The environment the adapter would read: the user's own OpenCode
    variables, and Iron Jarvis's reader pointed at the scratch global folder."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("OPENCODE_") and k not in ("XDG_CONFIG_HOME", "XDG_DATA_HOME")}
    env.update(case.get("env") or {})
    env["IRONJARVIS_OPENCODE_CONFIG_DIR"] = str(dirs["conf"])
    return env


def _ours(dirs: dict[str, Path], case: dict[str, Any], monkeypatch) -> dict[str, str]:
    monkeypatch.setattr(oc, "_agent_name", lambda: FIXED_AGENT)
    env, name = oc.text_only_setup(_reader_env(dirs, case), str(dirs["work"]))
    assert name == FIXED_AGENT
    return {k: env[k] for k in ("OPENCODE_PERMISSION", "OPENCODE_CONFIG_CONTENT")}


# --------------------------------------------------------------------------- #
# Regeneration: the REAL OpenCode resolves each case (this PC only)
# --------------------------------------------------------------------------- #
def _scratch_env(dirs: dict[str, Path], extra_env: dict[str, str]) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPENCODE_")}
    env.update({"XDG_CONFIG_HOME": str(dirs["cfg"]), "XDG_DATA_HOME": str(dirs["data"]),
                "XDG_CACHE_HOME": str(dirs["cache"]), "XDG_STATE_HOME": str(dirs["state"]),
                "OPENCODE_TEST_MANAGED_CONFIG_DIR": str(dirs["managed"])})
    env.update(extra_env)
    return env


def _resolve(dirs: dict[str, Path], extra_env: dict[str, str], agent: str) -> dict[str, Any]:
    exe = shutil.which("opencode")
    assert exe, "opencode is not installed"
    done = subprocess.run([exe, "debug", "agent", agent], cwd=str(dirs["work"]), env=_scratch_env(dirs, extra_env),
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    data = json.loads(done.stdout)
    rules = [[r["permission"], r["pattern"], r["action"]] for r in data["permission"]
             if r["permission"] != "external_directory"]  # those name the PC's own folders
    return {"agent": agent, "rules": rules, "tools": data["tools"]}


def _regenerate(tmp_path: Path, monkeypatch) -> dict[str, Any]:
    out: dict[str, Any] = {"opencode": subprocess.run([shutil.which("opencode") or "opencode", "--version"],
                                                      capture_output=True, text=True, timeout=60).stdout.strip(),
                           "cases": {}}
    for name, case in CASES.items():
        dirs = _layout(tmp_path / name, case)
        ours = _ours(dirs, case, monkeypatch)
        user_env = dict(case.get("env") or {})
        runs = {
            # What the old adapter ran: the user's default agent, nothing added.
            "before": _resolve(dirs, user_env, case["default"]),
            # Iron Jarvis's agent and rules (what the adapter runs now).
            "ours": _resolve(dirs, {**user_env, **ours}, FIXED_AGENT),
            # If ``--agent`` ever fell back: the user's default agent under our rules.
            "fallback": _resolve(dirs, {**user_env, "OPENCODE_PERMISSION": ours["OPENCODE_PERMISSION"]},
                                 case["default"]),
        }
        if name == "permissive":
            # The audit's first idea alone: ``"*": "deny"`` in OPENCODE_PERMISSION.
            runs["star-only"] = _resolve(dirs, {"OPENCODE_PERMISSION": json.dumps({"*": "deny"})}, "build")
        out["cases"][name] = {"env": ours, "runs": runs}
    # ``opencode run`` itself (a model that does not exist: no model call):
    # does it take Iron Jarvis's agent, and what does it say when it cannot?
    dirs = _layout(tmp_path / "run-check", CASES["permissive"])
    ours = _ours(dirs, CASES["permissive"], monkeypatch)
    out["run"] = {"ours": _run_check(dirs, ours), "undefined": _run_check(dirs, {})}
    return out


def _run_check(dirs: dict[str, Path], extra_env: dict[str, str]) -> dict[str, Any]:
    """``opencode run --agent <ours> -m nonexistent/none``: the lines it said
    about the agent (colour codes dropped)."""
    exe = shutil.which("opencode")
    assert exe, "opencode is not installed"
    done = subprocess.run([exe, "run", "--format", "json", "--agent", FIXED_AGENT, "-m", "nonexistent/none"],
                          input="hi", cwd=str(dirs["work"]), env=_scratch_env(dirs, extra_env),
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    plain = re.sub(r"\x1b\[[0-9;]*m", "", done.stderr + "\n" + done.stdout)
    return {"agent_lines": [ln.strip() for ln in plain.splitlines() if 'agent "' in ln]}


@pytest.fixture(scope="module")
def recorded(tmp_path_factory) -> dict[str, Any]:
    if os.environ.get("IJ_REGEN_OPENCODE_TEXT_ONLY") == "1":
        mp = pytest.MonkeyPatch()
        try:
            data = _regenerate(tmp_path_factory.mktemp("oc-regen"), mp)
        finally:
            mp.undo()
        # Bytes, so the file has LF line ends on every PC (text mode writes CRLF here).
        FIXTURE.write_bytes((json.dumps(data, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# The simulator (OpenCode 1.18.18's resolution, as measured)
# --------------------------------------------------------------------------- #
def _as_map(perm: Any) -> dict[str, Any]:
    """A whole-config string rule is ``{"*": rule}``."""
    if isinstance(perm, str):
        return {"*": perm}
    return dict(perm) if isinstance(perm, dict) else {}


def _legacy(tools: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, on in tools.items():
        out["edit" if key in ("write", "edit", "patch") else key] = "allow" if on else "deny"
    return out


def _md_permission(text: str) -> dict[str, str]:
    """The ``permission:`` block of a simple ``.md`` agent (one level, the
    shape the cases use)."""
    out: dict[str, str] = {}
    lines = text.split("---")[1].splitlines() if text.startswith("---") else []
    inside = False
    for line in lines:
        if line.strip() == "permission:":
            inside = True
            continue
        if inside and line.startswith("  ") and ":" in line:
            k, v = line.strip().split(":", 1)
            out[k.strip().strip('"')] = v.strip()
        elif inside:
            inside = False
    return out


def simulate(case: dict[str, Any], extra_env: dict[str, str], agent: str) -> list[tuple[str, str, str]]:
    """The agent's resolved rules for ``opencode run`` in this case."""
    env = {**(case.get("env") or {}), **extra_env}
    glob = case.get("global") or {}
    proj = case.get("project") or {}
    inline: dict[str, Any] = {}
    if env.get("OPENCODE_CONFIG_CONTENT"):
        inline = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    # Config files in load order: global, project, inline; then the
    # environment's OPENCODE_PERMISSION; the legacy ``tools`` maps go first.
    perm: dict[str, Any] = {}
    legacy: dict[str, Any] = {}
    for src in (glob, proj, inline):
        if "permission" in src:
            perm = _merge(perm, _as_map(src["permission"]))
        if isinstance(src.get("tools"), dict):
            legacy = _merge(legacy, _legacy(src["tools"]))
    if env.get("OPENCODE_PERMISSION"):
        perm = _merge(perm, _as_map(json.loads(env["OPENCODE_PERMISSION"])))
    if legacy:
        perm = _merge(legacy, perm)
    # The agent's own config permission: .md files first, then the json
    # configs in load order (an inline agent of the same name merges in place).
    agent_perm: dict[str, Any] = {}
    md = (case.get("md_agents") or {}).get(agent)
    if md:
        agent_perm = _merge(agent_perm, _md_permission(md))
    for src in (glob, proj, inline):
        spec = (src.get("agent") or {}).get(agent)
        if isinstance(spec, dict) and "permission" in spec:
            agent_perm = _merge(agent_perm, _as_map(spec["permission"]))
    native = _rules(AGENTS[agent]) if agent in AGENTS else []
    rules = _rules(DEFAULTS) + native + _rules(perm) + _rules(agent_perm)
    return [r for r in rules if r[0] != "external_directory"]


def offered(rules: list[tuple[str, str, str]], tool: str, *, run: bool = True) -> bool:
    """OpenCode's own ``disabled`` (1.18.18): the last rule whose PERMISSION
    matches; a ``*``-pattern deny takes the tool away. ``run``: with
    ``opencode run``'s session rules after the agent's (``debug agent`` shows
    the agent's alone)."""
    perm = "edit" if tool in ("edit", "write", "apply_patch") else tool
    hit = None
    for p, pat, act in list(rules) + (RUN_SESSION if run else []):
        if _wild(perm, p):
            hit = (pat, act)
    return not (hit is not None and hit[0] == "*" and hit[1] == "deny")


# --------------------------------------------------------------------------- #
# 1. What the real OpenCode said (the fixture) — and the simulator agrees
# --------------------------------------------------------------------------- #
def test_the_fixture_was_written_by_opencode_1_18(recorded):
    assert "1.18" in recorded["opencode"]
    assert set(recorded["cases"]) == set(CASES)


@pytest.mark.parametrize("name", sorted(CASES))
def test_with_iron_jarviss_rules_opencode_offers_no_tool_at_all(recorded, name):
    """The real program, every case: our agent is offered NO tool."""
    tools = recorded["cases"][name]["runs"]["ours"]["tools"]
    assert tools and not any(tools.values()), (name, tools)


@pytest.mark.parametrize("name", sorted(CASES))
def test_before_the_users_own_opencode_offered_tools(recorded, name):
    """The hole the audit named, as OpenCode itself resolved it: the old
    ``opencode run`` (the user's default agent, nothing added)."""
    tools = recorded["cases"][name]["runs"]["before"]["tools"]
    assert any(tools.values()), (name, tools)


def test_opencode_run_takes_iron_jarviss_agent_and_says_when_it_cannot(recorded):
    """``opencode run`` with the agent defined says nothing about it; with it
    undefined it says it fell back — in exactly the words :func:`fell_back`
    looks for."""
    assert recorded["run"]["ours"]["agent_lines"] == []
    said = recorded["run"]["undefined"]["agent_lines"]
    assert said and "Falling back to default agent" in said[0]
    assert oc.fell_back("", "\n".join(said), FIXED_AGENT)
    assert not oc.fell_back("", "\n".join(said), oc.AGENT_PREFIX + "other")


def test_star_alone_lost_to_a_users_later_allow(recorded):
    """``OPENCODE_PERMISSION={"*": "deny"}`` alone: the user's ``"*"`` kept its
    place and their later allows won (bash, task, the web tools stayed)."""
    tools = recorded["cases"]["permissive"]["runs"]["star-only"]["tools"]
    assert tools["bash"] and tools["task"] and tools["webfetch"]


def test_an_agents_own_rules_come_after_the_environment(recorded):
    """Why Iron Jarvis runs an agent of its own: the user's
    ``agent.build.permission`` beat every deny in OPENCODE_PERMISSION."""
    tools = recorded["cases"]["agent-level"]["runs"]["fallback"]["tools"]
    assert tools["bash"] and tools["edit"]


@pytest.mark.parametrize("name", sorted(CASES))
@pytest.mark.parametrize("run", ["before", "ours", "fallback", "star-only"])
def test_the_simulator_agrees_with_opencode(recorded, name, run):
    """The model of OpenCode's merge reproduces every recorded run: the same
    rules in the same order, and the same tools offered."""
    got = recorded["cases"][name]["runs"].get(run)
    if got is None:
        pytest.skip("not recorded for this case")
    case = CASES[name]
    extra: dict[str, str]
    if run == "ours":
        extra = recorded["cases"][name]["env"]
    elif run == "fallback":
        extra = {"OPENCODE_PERMISSION": recorded["cases"][name]["env"]["OPENCODE_PERMISSION"]}
    elif run == "star-only":
        extra = {"OPENCODE_PERMISSION": json.dumps({"*": "deny"})}
    else:
        extra = {}
    sim = simulate(case, extra, got["agent"])
    assert [list(r) for r in sim] == got["rules"], (name, run)
    for tool, on in got["tools"].items():
        assert offered(sim, tool, run=False) == on, (name, run, tool)


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_code_still_builds_what_opencode_resolved(recorded, name, tmp_path, monkeypatch):
    """The recording is of THIS code's environment: a change to it without a
    new recording fails here (``IJ_REGEN_OPENCODE_TEXT_ONLY=1``)."""
    case = CASES[name]
    dirs = _layout(tmp_path, case)
    assert _ours(dirs, case, monkeypatch) == recorded["cases"][name]["env"]


# --------------------------------------------------------------------------- #
# 2. A wider set of the user's own rules, judged by the checked simulator
# --------------------------------------------------------------------------- #
WIDER: list[dict[str, Any]] = [
    {"global": {"permission": {"BASH": "allow", "Edit": "allow"}}, "default": "build"},
    {"global": {"permission": {"*": "deny", "github_*": "allow", "my_*": "allow"}}, "default": "build"},
    {"global": {"permission": {"bash": {"*": "deny", "git *": "allow"}}}, "default": "build"},
    {"global": {"tools": {"bash": True, "write": True}, "permission": {"*": "ask"}}, "default": "build"},
    {"global": {"permission": {"*": "allow"}},
     "project": {"permission": {"webfetch": "allow", "list": "allow", "lsp": "allow"}}, "default": "build"},
    {"global": {"agent": {"build": {"permission": {"*": "allow"}}, "plan": {"permission": {"*": "allow"}}}},
     "default": "build"},
    {"global": {}, "env": {"OPENCODE_PERMISSION": json.dumps({"codesearch": "allow", "*": "allow"})},
     "default": "build"},
    {"global": {}, "env": {"OPENCODE_CONFIG_CONTENT": json.dumps(
        {"permission": {"quickbooks_*": "allow"}, "agent": {}})}, "default": "build"},
]


@pytest.mark.parametrize("case", WIDER, ids=lambda c: json.dumps(c)[:70])
def test_no_rule_of_the_users_own_can_offer_a_tool(case, tmp_path, monkeypatch):
    dirs = _layout(tmp_path, case)
    ours = _ours(dirs, case, monkeypatch)
    rules = simulate(case, ours, FIXED_AGENT)
    for tool in OFFERED + OTHER_TOOLS:
        assert not offered(rules, tool), (case, tool)
    # A call of one of them is refused too: the last rule for it is a deny.
    for tool in ("bash", "edit", "github_create_issue"):
        last = [r for r in rules + RUN_SESSION if _wild(tool, r[0])][-1]
        assert last[2] == "deny", (case, tool, last)


#: The cases whose user config has no agent-level rule for the default agent.
NO_AGENT_RULES = ("permissive", "string-permission", "inline-and-env")


@pytest.mark.parametrize("name", NO_AGENT_RULES)
def test_should_run_ever_fall_back_the_global_rules_still_deny_every_tool(recorded, name, tmp_path,
                                                                           monkeypatch):
    """The second layer: if ``--agent`` ever fell back to the user's default
    agent (said as an error, see below), OPENCODE_PERMISSION alone still
    denies every tool where no agent-level rule of theirs follows it — OpenCode
    said so, and the simulator says so for the environment the code builds
    NOW (every key of theirs named; ``"*"`` alone lost, above)."""
    tools = recorded["cases"][name]["runs"]["fallback"]["tools"]
    assert not any(tools.values()), (name, tools)
    case = CASES[name]
    ours = _ours(_layout(tmp_path, case), case, monkeypatch)
    rules = simulate(case, {"OPENCODE_PERMISSION": ours["OPENCODE_PERMISSION"]}, case["default"])
    for tool in OFFERED + OTHER_TOOLS:
        assert not offered(rules, tool), (name, tool)


@pytest.mark.parametrize("case", [c for c in WIDER if "agent" not in (c.get("global") or {})],
                         ids=lambda c: json.dumps(c)[:70])
def test_should_run_ever_fall_back_a_users_later_allow_still_loses(case, tmp_path, monkeypatch):
    """The same layer against the wider set: a user's ``"github_*": "allow"``
    after their ``"*"`` is named ``deny`` in place (``"*"`` alone keeps its
    place before it and loses)."""
    ours = _ours(_layout(tmp_path, case), case, monkeypatch)
    rules = simulate(case, {"OPENCODE_PERMISSION": ours["OPENCODE_PERMISSION"]}, case["default"])
    for tool in OFFERED + OTHER_TOOLS:
        assert not offered(rules, tool), (case, tool)


def test_every_key_of_the_users_own_rules_is_named_deny(tmp_path, monkeypatch):
    case = CASES["permissive"]
    dirs = _layout(tmp_path, case)
    perm = json.loads(_ours(dirs, case, monkeypatch)["OPENCODE_PERMISSION"])
    for key in ("*", "bash", "web*", "edit", "Task"):
        assert perm.get(key) == "deny", (key, perm)
    for tool in oc.builtin_tools():
        assert perm.get(tool) == "deny", tool
    assert set(perm.values()) == {"deny"}


def test_the_users_own_inline_config_is_kept_and_its_agent_added(tmp_path, monkeypatch):
    case = CASES["inline-and-env"]
    dirs = _layout(tmp_path, case)
    content = json.loads(_ours(dirs, case, monkeypatch)["OPENCODE_CONFIG_CONTENT"])
    assert content["permission"] == {"bash": "allow"}  # theirs, untouched (denied by ours)
    assert content["agent"]["helper"] == {"mode": "subagent"}
    mine = content["agent"][FIXED_AGENT]
    assert mine["mode"] == "primary"
    assert mine["permission"]["*"] == "deny" and set(mine["permission"].values()) == {"deny"}


def test_a_config_content_iron_jarvis_cannot_add_to_starts_nothing(monkeypatch, tmp_path):
    for bad in ("not json", "[1, 2]", json.dumps({"agent": "x"})):
        with pytest.raises(oc.TextOnlyRefused, match="not started"):
            oc.text_only_setup({"OPENCODE_CONFIG_CONTENT": bad}, str(tmp_path))
    spawned: list = []

    def runner(argv, stdin=None, **kw):
        spawned.append(argv)
        return 0, "", ""

    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", "not json")
    with pytest.raises(oc.TextOnlyRefused):
        _complete(_adapter(runner))
    assert spawned == []


def test_the_users_files_are_only_read(tmp_path, monkeypatch):
    case = CASES["permissive"]
    dirs = _layout(tmp_path, case)
    files = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}
    _ours(dirs, case, monkeypatch)
    after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(q for q in tmp_path.rglob("*") if q.is_file())}
    assert after == before


# --------------------------------------------------------------------------- #
# 3. The adapter: the call carries it, and a run that did not take it is said
# --------------------------------------------------------------------------- #
_REPLY = json.dumps({"type": "text", "part": {"type": "text", "text": "ok"}})


def _adapter(runner):
    return oc.OpencodeCliAdapter(model="spark/fleet", allowed=lambda: ["spark/fleet"],
                                 runner=runner, which=lambda _n: "/usr/bin/opencode")


def _complete(adapter):
    return asyncio.run(adapter.complete(system="", messages=[LLMMessage(role="user", content="hi")], tools=[]))


def test_every_call_runs_as_a_fresh_agent_of_iron_jarviss_own(monkeypatch, tmp_path):
    conf = tmp_path / "conf"
    conf.mkdir()
    (conf / "opencode.json").write_text(json.dumps({"permission": {"bash": "allow"}}), encoding="utf-8")
    monkeypatch.setenv("IRONJARVIS_OPENCODE_CONFIG_DIR", str(conf))
    seen: list[tuple[list[str], dict[str, str]]] = []

    def runner(argv, stdin=None, **kw):
        seen.append((argv, kw["env"]))
        return 0, _REPLY, ""

    assert _complete(_adapter(runner)).text == "ok"
    assert _complete(_adapter(runner)).text == "ok"
    names = []
    for argv, env in seen:
        name = argv[argv.index("--agent") + 1]
        names.append(name)
        assert name.startswith(oc.AGENT_PREFIX)
        perm = json.loads(env["OPENCODE_PERMISSION"])
        assert perm["*"] == "deny" and perm["bash"] == "deny" and set(perm.values()) == {"deny"}
        agent = json.loads(env["OPENCODE_CONFIG_CONTENT"])["agent"][name]
        assert agent["mode"] == "primary" and agent["permission"] == perm
        # The rest of the environment is the app's own (PATH and friends).
        assert env.get("PATH") == os.environ.get("PATH")
    assert names[0] != names[1]


def test_a_run_that_fell_back_to_the_users_agent_is_an_error_not_an_answer():
    def runner(argv, stdin=None, **kw):
        name = argv[argv.index("--agent") + 1]
        return 0, _REPLY, f'! agent "{name}" not found. Falling back to default agent\n'

    with pytest.raises(RuntimeError, match="did not take the tool-free setup"):
        _complete(_adapter(runner))


def test_a_run_where_opencode_ran_its_own_tool_is_an_error_not_an_answer():
    used = json.dumps({"type": "tool_use", "part": {"type": "tool", "tool": "bash",
                                                     "state": {"status": "completed"}}})

    def runner(argv, stdin=None, **kw):
        return 0, used + "\n" + _REPLY, ""

    with pytest.raises(RuntimeError, match=r"ran its own tools \(bash\)"):
        _complete(_adapter(runner))


def test_a_call_of_a_tool_it_does_not_offer_ran_nothing():
    """OpenCode answers a call of a tool it does not offer with its ``invalid``
    tool: nothing ran, the answer stands."""
    invalid = json.dumps({"type": "tool_use", "part": {"type": "tool", "tool": "invalid",
                                                        "state": {"status": "completed"}}})

    def runner(argv, stdin=None, **kw):
        return 0, invalid + "\n" + _REPLY, ""

    assert _complete(_adapter(runner)).text == "ok"


def test_the_real_cli_receives_the_environment(tmp_path, monkeypatch):
    """The default runner (``run_cli``) hands the environment to the process:
    a stand-in CLI prints what it was given."""
    import sys

    script = tmp_path / "fake_opencode.py"
    script.write_text(
        "import json, os, sys\n"
        "sys.stdin.read()\n"
        "agent = sys.argv[sys.argv.index('--agent') + 1]\n"
        "perm = json.loads(os.environ['OPENCODE_PERMISSION'])\n"
        "inline = json.loads(os.environ['OPENCODE_CONFIG_CONTENT'])\n"
        "ok = perm['*'] == 'deny' and inline['agent'][agent]['permission']['*'] == 'deny'\n"
        "print(json.dumps({'type': 'text', 'part': {'type': 'text', 'text': 'tools off' if ok else 'ON'}}))\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        shim = tmp_path / "opencode.cmd"
        shim.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        shim = tmp_path / "opencode"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
    adapter = oc.OpencodeCliAdapter(model="spark/fleet", allowed=lambda: ["spark/fleet"],
                                    which=lambda _n: str(shim))
    assert _complete(adapter).text == "tools off"


# --------------------------------------------------------------------------- #
# 4. Build's Code view is untouched (the user picked the real tool there)
# --------------------------------------------------------------------------- #
def test_build_code_view_keeps_opencodes_own_tools(tmp_path, monkeypatch):
    conf = tmp_path / "conf"
    conf.mkdir()
    monkeypatch.setenv("IRONJARVIS_OPENCODE_CONFIG_DIR", str(conf))
    env, _note = O.child_env({}, "full", str(tmp_path))
    assert json.loads(env["OPENCODE_PERMISSION"]) == {"*": "allow"}
    assert "OPENCODE_CONFIG_CONTENT" not in env
    assert "--agent" not in O.ACP_ARGV
    src = (Path(O.__file__).read_text(encoding="utf-8"))
    assert "text_only_setup" not in src and "deny_all" not in src
