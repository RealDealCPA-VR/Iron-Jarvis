"""v1.424.0 (review of audit D5): OpenCode behind a chat turn or an agent run —
what the first cut left open, MEASURED with the real installed opencode.

THE FIXTURE ``tests/fixtures/opencode_text_only_review_v14240.json`` was
written by the REAL opencode 1.18.18 (``IJ_REGEN_OPENCODE_REVIEW=1``, this PC
only): scratch ``XDG_*`` / managed / work folders, and a STAND-IN model server
on this PC (an OpenAI-compatible stream, answering a fixed script — no model,
no network call for the answer) that OpenCode's own ``@ai-sdk/openai-
compatible`` provider talks to. The user's config is the dangerous one: every
tool allowed, and their default agent's own ``bash`` allowed. Recorded per run:
OpenCode's JSON events (ids and times dropped), the tool names it OFFERED the
model in each request, whether the stand-in's ``bash`` call really wrote its
file, the exit code and OpenCode's agent warning.

1. A FALLBACK IS CLOSED BEFORE THE RUN. ``--agent`` with an agent OpenCode
   cannot find falls back to the DEFAULT agent; with the first cut's setup
   that ran the user's own agent and its ``bash`` RAN (``missing-agent-old``)
   — the after-the-run check could only say so afterwards. The inline config
   now names Iron Jarvis's agent as ``default_agent``: OpenCode's default
   lookup throws, the run ends before any model request
   (``missing-agent``, ``subagent``).
2. A CALL OPENCODE REFUSED RAN NOTHING. Under Iron Jarvis's agent no tool is
   offered; a model that calls one anyway gets OpenCode's own refusal
   (``tool_use`` with ``status: "error"``, "Model tried to call unavailable
   tool …") and nothing runs. The first cut called that "OpenCode ran its own
   tools (bash)" and threw the answer away.
3. A REPLY CUT AT THE OUTPUT LIMIT SAYS SO: ``step_finish`` ``reason:
   "length"`` is ``finish_reason="max_tokens"`` (the chat's Continue).
"""

from __future__ import annotations

import asyncio
import http.server
import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from iron_jarvis.daemon.chat_turn import _truncated_by
from iron_jarvis.providers.adapters import opencode_cli as oc
from iron_jarvis.providers.adapters.base import LLMMessage
from iron_jarvis.providers.adapters.prompted_tools import PromptedToolsAdapter

FIXTURE = Path(__file__).parent / "fixtures" / "opencode_text_only_review_v14240.json"
FIXED_AGENT = "iron-jarvis-text-review"

#: The user's own config (scratch): every tool allowed, and the default
#: agent's own bash allowed — what an AGENT-level rule does is the point.
USER_CONFIG: dict[str, Any] = {
    "permission": {"*": "allow", "bash": "allow"},
    "agent": {"build": {"permission": {"bash": "allow"}}},
    "autoupdate": False,
}
#: The stand-in model's ``bash`` call (writes a file in the scratch work folder).
PROOF = "proof.txt"
BASH_ARGS = {"command": f"echo pwned> {PROOF}", "description": "make a file"}
#: OpenCode 1.18.18's words for a call it refused before running anything:
#: the first MEASURED below, the other two read from the program.
REFUSALS = (
    "Model tried to call unavailable tool",
    "The user has specified a rule which prevents you from using this specific tool call",
    "The user rejected permission to use this specific tool call",
)
#: The keys dropped from each recorded event (ids, times, OpenCode's snapshot).
VOLATILE = {"id", "sessionID", "messageID", "timestamp", "callID", "snapshot", "time", "metadata"}


# --------------------------------------------------------------------------- #
# The stand-in model server (an OpenAI-compatible chat-completions stream)
# --------------------------------------------------------------------------- #
class _Model(http.server.BaseHTTPRequestHandler):
    script = "stop"
    offered: list[list[str]] = []

    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        body = json.dumps({"object": "list", "data": [{"id": "m", "object": "model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _chunk(self, delta: dict, finish: str | None = None) -> None:
        evt: dict[str, Any] = {"id": "c1", "object": "chat.completion.chunk", "created": 0, "model": "m",
                               "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        if finish:
            evt["usage"] = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
        self.wfile.write(("data: " + json.dumps(evt) + "\n\n").encode())

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            req = json.loads(raw)
        except ValueError:
            req = {}
        # Only the tool NAMES offered are kept (never the prompt).
        type(self).offered.append(sorted((t.get("function") or {}).get("name") or "?"
                                         for t in req.get("tools") or []))
        answered = any(m.get("role") == "tool" for m in req.get("messages") or [])
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if type(self).script == "length":
            self._chunk({"role": "assistant", "content": "The first half of the ans"})
            self._chunk({}, "length")
        elif type(self).script == "bash" and not answered:
            self._chunk({"role": "assistant", "tool_calls": [
                {"index": 0, "id": "call_1", "type": "function",
                 "function": {"name": "bash", "arguments": json.dumps(BASH_ARGS)}}]})
            self._chunk({}, "tool_calls")
        else:
            self._chunk({"role": "assistant", "content": "done"})
            self._chunk({}, "stop")
        self.wfile.write(b"data: [DONE]\n\n")


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _ours(work: Path, reader_env: dict[str, str], monkeypatch) -> dict[str, str]:
    monkeypatch.setattr(oc, "_agent_name", lambda: FIXED_AGENT)
    env, name = oc.text_only_setup(reader_env, str(work))
    assert name == FIXED_AGENT
    return {k: env[k] for k in ("OPENCODE_PERMISSION", "OPENCODE_CONFIG_CONTENT")}


def _layout(root: Path, port: int) -> dict[str, Path]:
    dirs = {k: root / k for k in ("cfg", "data", "cache", "state", "managed", "work")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    conf = dirs["cfg"] / "opencode"
    conf.mkdir(exist_ok=True)
    cfg = dict(USER_CONFIG)
    cfg["provider"] = {"fake": {"npm": "@ai-sdk/openai-compatible", "name": "Fake",
                                "options": {"baseURL": f"http://127.0.0.1:{port}/v1", "apiKey": "dummy"},
                                "models": {"m": {"name": "M", "limit": {"context": 100000, "output": 4000}}}}}
    (conf / "opencode.json").write_text(json.dumps(cfg), encoding="utf-8")
    dirs["conf"] = conf
    return dirs


def _scratch_env(dirs: dict[str, Path]) -> dict[str, str]:
    # PWD is dropped: OpenCode takes its folder from it (measured), and a
    # shell's PWD would point it at the folder the test was started from.
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPENCODE_") and k not in ("PWD", "OLDPWD")}
    env.update({"XDG_CONFIG_HOME": str(dirs["cfg"]), "XDG_DATA_HOME": str(dirs["data"]),
                "XDG_CACHE_HOME": str(dirs["cache"]), "XDG_STATE_HOME": str(dirs["state"]),
                "OPENCODE_TEST_MANAGED_CONFIG_DIR": str(dirs["managed"]),
                "OPENCODE_DISABLE_MODELS_FETCH": "1", "OPENCODE_DISABLE_AUTOUPDATE": "1",
                "OPENCODE_DISABLE_SHARE": "1", "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
                "OPENCODE_DISABLE_CLAUDE_CODE": "1", "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1"})
    return env


def _record(root: Path, run: str, script: str, monkeypatch) -> dict[str, Any]:
    exe = shutil.which("opencode")
    assert exe, "opencode is not installed"
    _Model.script, _Model.offered = script, []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Model)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        dirs = _layout(root / run, srv.server_address[1])
        env = _scratch_env(dirs)
        reader = {k: v for k, v in env.items() if k != "XDG_CONFIG_HOME"}
        reader["IRONJARVIS_OPENCODE_CONFIG_DIR"] = str(dirs["conf"])
        argv = [exe, "run", "--format", "json"]
        if run != "before":
            ours = _ours(dirs["work"], reader, monkeypatch)
            content = json.loads(ours["OPENCODE_CONFIG_CONTENT"])
            if run.startswith("missing-agent"):
                content["agent"].pop(FIXED_AGENT)  # the agent OpenCode cannot find
                if run == "missing-agent-old":
                    content.pop("default_agent", None)  # the first cut's shape
            elif run == "subagent":
                content["agent"][FIXED_AGENT]["mode"] = "subagent"
            env["OPENCODE_PERMISSION"] = ours["OPENCODE_PERMISSION"]
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps(content)
            argv += ["--agent", FIXED_AGENT]
        argv += ["-m", "fake/m"]
        done = subprocess.run(argv, input="hi", cwd=str(dirs["work"]), env=env, capture_output=True,
                              text=True, encoding="utf-8", timeout=180)
    finally:
        srv.shutdown()
    events = []
    for line in done.stdout.splitlines():
        try:
            events.append(_scrub(json.loads(line)))
        except ValueError:
            pass
    plain = re.sub(r"\x1b\[[0-9;]*m", "", done.stderr + "\n" + done.stdout)
    return {"script": script, "code": done.returncode, "events": events,
            "offered": [o for o in _Model.offered],
            "proof_written": (dirs["work"] / PROOF).exists(),
            "agent_lines": [ln.strip() for ln in plain.splitlines() if 'agent "' in ln]}


#: run -> the stand-in's script.
RUNS = {"before": "bash", "ours": "bash", "missing-agent-old": "bash", "missing-agent": "bash",
        "subagent": "bash", "length": "length", "stop": "stop"}


@pytest.fixture(scope="module")
def recorded(tmp_path_factory) -> dict[str, Any]:
    if os.environ.get("IJ_REGEN_OPENCODE_REVIEW") == "1":
        mp = pytest.MonkeyPatch()
        try:
            root = tmp_path_factory.mktemp("oc-review")
            out: dict[str, Any] = {
                "opencode": subprocess.run([shutil.which("opencode") or "opencode", "--version"],
                                           capture_output=True, text=True, timeout=60).stdout.strip(),
                "runs": {run: _record(root, run, script, mp) for run, script in RUNS.items()},
            }
            dirs = _layout(root / "env", 9)
            reader = {k: v for k, v in _scratch_env(dirs).items() if k != "XDG_CONFIG_HOME"}
            reader["IRONJARVIS_OPENCODE_CONFIG_DIR"] = str(dirs["conf"])
            out["env"] = _ours(dirs["work"], reader, mp)
        finally:
            mp.undo()
        FIXTURE.write_bytes((json.dumps(out, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _stdout(run: dict[str, Any]) -> str:
    """The recorded events as OpenCode's JSON lines (ids and times dropped)."""
    return "\n".join(json.dumps(e) for e in run["events"]) + "\n"


def _adapter(stdout: str, err: str = "", code: int = 0):
    def runner(argv, stdin=None, **kw):
        return code, stdout, err

    return oc.OpencodeCliAdapter(model="spark/fleet", allowed=lambda: ["spark/fleet"],
                                 runner=runner, which=lambda _n: "/usr/bin/opencode")


def _complete(adapter, tools=None):
    return asyncio.run(adapter.complete(system="", messages=[LLMMessage(role="user", content="hi")],
                                        tools=tools or []))


def _tool_events(run: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in run["events"] if e.get("type") == "tool_use"]


# --------------------------------------------------------------------------- #
# What the real OpenCode did
# --------------------------------------------------------------------------- #
def test_the_fixture_was_written_by_opencode_1_18(recorded):
    assert "1.18" in recorded["opencode"]
    assert set(recorded["runs"]) == set(RUNS)


def test_before_the_users_own_opencode_ran_the_models_bash(recorded):
    """The audit's hole, end to end: the old ``opencode run`` offered bash and
    the stand-in's call wrote its file."""
    run = recorded["runs"]["before"]
    assert any("bash" in o for o in run["offered"])
    assert [e["part"]["state"]["status"] for e in _tool_events(run)] == ["completed"]
    assert run["proof_written"] is True


def test_with_iron_jarviss_agent_no_tool_is_offered_and_none_runs(recorded):
    run = recorded["runs"]["ours"]
    assert run["offered"] and all(o == [] for o in run["offered"])
    assert run["proof_written"] is False and run["agent_lines"] == [] and run["code"] == 0
    (call,) = _tool_events(run)
    assert call["part"]["tool"] == "bash" and call["part"]["state"]["status"] == "error"
    assert call["part"]["state"]["error"].startswith(REFUSALS[0])


def test_the_first_cuts_fallback_ran_the_users_own_agent(recorded):
    """Why the fallback must be closed BEFORE the run: with the agent missing
    and no default named, ``--agent`` fell back to the user's ``build`` agent
    and its own ``bash: allow`` RAN the call."""
    run = recorded["runs"]["missing-agent-old"]
    assert run["agent_lines"] and "Falling back to default agent" in run["agent_lines"][0]
    assert run["proof_written"] is True
    assert [e["part"]["state"]["status"] for e in _tool_events(run)] == ["completed"]


@pytest.mark.parametrize("run", ["missing-agent", "subagent"])
def test_with_iron_jarviss_default_a_fallback_ends_before_any_model_request(recorded, run):
    """With Iron Jarvis's agent named as the default, OpenCode's fallback
    lookup throws: no model request at all, nothing ran, a failed exit."""
    got = recorded["runs"][run]
    assert got["agent_lines"] and "Falling back to default agent" in got["agent_lines"][0]
    assert got["offered"] == [] and got["proof_written"] is False
    assert got["code"] != 0 and not _tool_events(got)
    # The adapter says it as an error too (the warning is still there).
    with pytest.raises(RuntimeError):
        _complete(_adapter(_stdout(got), "\n".join(got["agent_lines"]), got["code"]))


def test_a_cut_reply_ends_in_a_length_step(recorded):
    run = recorded["runs"]["length"]
    reasons = [e["part"].get("reason") for e in run["events"] if e.get("type") == "step_finish"]
    assert reasons == ["length"]


# --------------------------------------------------------------------------- #
# The code
# --------------------------------------------------------------------------- #
def test_the_code_names_its_own_agent_as_the_default(tmp_path, monkeypatch):
    """The belt that closes the fallback, in the environment the code builds —
    replacing a ``default_agent`` of the user's own for this process only."""
    conf = tmp_path / "conf"
    conf.mkdir()
    env = {"IRONJARVIS_OPENCODE_CONFIG_DIR": str(conf),
           "OPENCODE_CONFIG_CONTENT": json.dumps({"default_agent": "mine", "agent": {"mine": {"mode": "primary"}}})}
    out, name = oc.text_only_setup(env, str(tmp_path))
    content = json.loads(out["OPENCODE_CONFIG_CONTENT"])
    assert content["default_agent"] == name and content["agent"][name]["mode"] == "primary"
    assert content["agent"]["mine"] == {"mode": "primary"}  # theirs kept


def test_the_recording_is_of_this_codes_environment(recorded, tmp_path, monkeypatch):
    dirs = _layout(tmp_path, 9)
    reader = {k: v for k, v in _scratch_env(dirs).items() if k != "XDG_CONFIG_HOME"}
    reader["IRONJARVIS_OPENCODE_CONFIG_DIR"] = str(dirs["conf"])
    assert _ours(dirs["work"], reader, monkeypatch) == recorded["env"]


def test_a_call_opencode_refused_is_not_a_tool_that_ran(recorded):
    """The recorded refusal: nothing ran, so the answer stands (it used to be
    "OpenCode ran its own tools (bash)", a false claim)."""
    out = _stdout(recorded["runs"]["ours"])
    assert oc.tools_run(out) == []
    assert _complete(_adapter(out)).text == "done"


def test_a_tool_that_ran_is_still_an_error(recorded):
    out = _stdout(recorded["runs"]["before"])
    assert oc.tools_run(out) == ["bash"]
    with pytest.raises(RuntimeError, match=r"ran its own tools \(bash\)"):
        _complete(_adapter(out))


@pytest.mark.parametrize("error", ["Command failed: exit 1", "", "boom: Model tried to call unavailable tool"])
def test_any_other_error_counts_as_ran(error):
    """Fail closed: only OpenCode's own refusals (at the START of the error)
    say "ran nothing"."""
    evt = {"type": "tool_use", "part": {"type": "tool", "tool": "edit",
                                         "state": {"status": "error", "error": error}}}
    assert oc.tools_run(json.dumps(evt)) == ["edit"]


@pytest.mark.parametrize("words", REFUSALS)
def test_each_refusal_ran_nothing(words):
    assert tuple(getattr(oc, "REFUSED_CALL_ERRORS", ())) == REFUSALS
    evt = {"type": "tool_use", "part": {"type": "tool", "tool": "bash",
                                         "state": {"status": "error", "error": words + ". More."}}}
    assert oc.tools_run(json.dumps(evt)) == []


def test_a_reply_cut_at_the_output_limit_says_so(recorded):
    resp = _complete(_adapter(_stdout(recorded["runs"]["length"])))
    assert resp.text == "The first half of the ans"
    assert resp.finish_reason == "max_tokens" and _truncated_by(resp) is True


def test_a_whole_reply_is_stop(recorded):
    resp = _complete(_adapter(_stdout(recorded["runs"]["stop"])))
    assert resp.text == "done" and resp.finish_reason == "stop" and _truncated_by(resp) is False


def test_the_tools_wrapper_passes_the_cut_on(recorded):
    """In a turn with tools the router wraps OpenCode (prompted tools); a plain
    cut answer keeps its finish word through the wrapper."""
    wrapped = PromptedToolsAdapter(_adapter(_stdout(recorded["runs"]["length"])))
    resp = _complete(wrapped, tools=[{"name": "read_file", "description": "read",
                                      "input_schema": {"type": "object", "properties": {}}}])
    assert resp.finish_reason == "max_tokens" and _truncated_by(resp) is True


def test_every_key_of_every_source_is_denied_beside_a_whole_string_rule(tmp_path):
    """A global ``"permission": "allow"`` made ``user_rules`` answer one
    string, and the project's later ``"github_*": "allow"`` was never named —
    it kept its place after the ``"*"`` deny (the fallback layer)."""
    conf = tmp_path / "conf"
    conf.mkdir()
    (conf / "opencode.json").write_text(json.dumps({"permission": "allow"}), encoding="utf-8")
    work = tmp_path / "work"
    (work / ".opencode").mkdir(parents=True)
    (work / ".opencode" / "opencode.json").write_text(
        json.dumps({"permission": {"*": "allow", "github_*": "allow"}, "tools": {"my_tool": True}}),
        encoding="utf-8")
    env = {"IRONJARVIS_OPENCODE_CONFIG_DIR": str(conf),
           "OPENCODE_CONFIG_CONTENT": json.dumps({"permission": {"quickbooks_*": "allow"}})}
    out, name = oc.text_only_setup(env, str(work))
    perm = json.loads(out["OPENCODE_PERMISSION"])
    for key in ("*", "github_*", "my_tool", "quickbooks_*"):
        assert perm.get(key) == "deny", (key, perm)
    assert set(perm.values()) == {"deny"}
    assert json.loads(out["OPENCODE_CONFIG_CONTENT"])["agent"][name]["permission"] == perm


# --------------------------------------------------------------------------- #
# Build's Code view: byte for byte master's (the user picked the real tool)
# --------------------------------------------------------------------------- #
#: ``hosted.opencode.child_env`` per mode, captured at this lane's commit,
#: where ``src/iron_jarvis/hosted/opencode.py`` is master's own blob
#: (da671a45d07dd0027592bd7049077eb859190264) — none of the chat provider's
#: tool-free setup may reach it.
_ASK_READ = '{"*":"allow","*.env":"ask","*.env.*":"ask","*.env.example":"allow"}'
_ASK_BASE = ('{"*":"ask","read":' + _ASK_READ + ',"glob":"allow","grep":"allow","list":"allow",'
             '"lsp":"allow","todoread":"allow","todowrite":"allow","skill":"allow"')
BUILD_GOLDEN: dict[str, dict[str, dict[str, Any]]] = {
    "none": {
        "ask": {"perm": _ASK_BASE + ',"task":"allow"}', "note": ""},
        "edits": {"perm": _ASK_BASE + ',"edit":"allow","task":"allow"}', "note": ""},
        "plan": {"perm": _ASK_BASE + ',"edit":"deny"}', "note": ""},
        "full": {"perm": '{"*":"allow"}', "note": ""},
    },
    "permissive": {
        "ask": {"perm": None, "note": "Your OpenCode config sets one permission rule for every tool, so that "
                                      "rule decides in this session, whatever mode is chosen here."},
        "edits": {"perm": None, "note": "Your OpenCode config sets one permission rule for every tool, so that "
                                        "rule decides in this session, whatever mode is chosen here."},
        "plan": {"perm": '{"edit":"deny"}', "note": "Your OpenCode config sets a permission rule for every "
                                                    "tool, so that rule decides in this session; Plan still "
                                                    "changes no file."},
        "full": {"perm": '{"*":"allow"}', "note": ""},
    },
}


@pytest.mark.parametrize("config", sorted(BUILD_GOLDEN))
def test_build_code_view_is_byte_identical_to_master(config, tmp_path):
    from iron_jarvis.hosted import opencode as O

    conf = tmp_path / "conf"
    conf.mkdir()
    if config == "permissive":
        (conf / "opencode.json").write_text(json.dumps(USER_CONFIG), encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    for mode, want in BUILD_GOLDEN[config].items():
        env, note = O.child_env({"IRONJARVIS_OPENCODE_CONFIG_DIR": str(conf)}, mode, str(work))
        assert env.get("OPENCODE_PERMISSION") == want["perm"], (config, mode)
        assert note == want["note"], (config, mode)
        assert "OPENCODE_CONFIG_CONTENT" not in env, (config, mode)
    assert O.ACP_ARGV == ("acp", "--hostname", "127.0.0.1", "--port", "0", "--mdns=false")


def test_the_last_step_decides(recorded):
    """A reply's earlier steps (a refused call, ``tool-calls``) do not decide;
    its LAST ``step_finish`` does — the recorded shapes, re-ordered."""
    steps = [e for e in recorded["runs"]["ours"]["events"] if e.get("type") == "step_finish"]
    cut = json.loads(json.dumps(steps[-1]))
    cut["part"]["reason"] = "length"
    text = [e for e in recorded["runs"]["length"]["events"] if e.get("type") == "text"]
    out = "\n".join(json.dumps(e) for e in [steps[0], *text, cut])
    assert oc.finish_of(out) == "max_tokens"
    assert oc.finish_of("\n".join(json.dumps(e) for e in [cut, *text, steps[-1]])) == "stop"
