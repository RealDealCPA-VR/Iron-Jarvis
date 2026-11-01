"""v1.420.0 review (C1.10): the defects the independent review found in
"OpenCode never changes a rule of the user's own", each pinned red first.

Verdicts use the OpenCode 1.18.18 simulator of
``test_build_app_opencode_review_v1413`` (config files merged in load order,
global before project; a pattern map deep-merged; ``OPENCODE_PERMISSION``
merged LAST; the last matching rule wins).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from iron_jarvis.hosted import opencode as O
from tests.test_build_app_opencode_review_v1413 import _merge, config_permission, env_for, verdict


def _two_files(tmp_path: Path, monkeypatch, global_perm: dict, project_perm: dict) -> Path:
    conf = tmp_path / "global"
    conf.mkdir()
    monkeypatch.setenv("IRONJARVIS_OPENCODE_CONFIG_DIR", str(conf))
    (conf / "opencode.json").write_text(json.dumps({"permission": global_perm}), encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "opencode.json").write_text(json.dumps({"permission": project_perm}), encoding="utf-8")
    return proj


@pytest.mark.parametrize(("global_perm", "project_perm", "command"), [
    # The project loosens the global rule: OpenCode reads "allow".
    ({"bash": "ask"}, {"bash": "allow"}, "npm test"),
    # Two pattern maps deep-merge: "git *" is allowed after the global "*": "ask".
    ({"bash": {"*": "ask"}}, {"bash": {"git *": "allow"}}, "git status"),
])
@pytest.mark.parametrize("mode", ["ask", "edits", "plan"])
def test_rules_that_differ_between_config_files_are_not_tightened(tmp_path, monkeypatch, global_perm,
                                                                  project_perm, command, mode):
    """The user's rules are read "stricter wins" across files, so a rule that
    one file says ``ask`` and a later one ``allow`` read as exactly what
    ``"*": "ask"`` says — and Iron Jarvis's ``"*"`` landed after it and turned
    every allowed command into a card."""
    proj = _two_files(tmp_path, monkeypatch, global_perm, project_perm)
    env, _note = O.child_env({}, mode, str(proj))
    ours = json.loads(env["OPENCODE_PERMISSION"]) if env.get("OPENCODE_PERMISSION") else None
    user = _merge(global_perm, project_perm)
    agent = "plan" if mode == "plan" else "build"
    assert verdict(agent, config_permission(user, None, None), "bash", command) == "allow"
    assert verdict(agent, config_permission(user, None, ours), "bash", command) == "allow", ours


@pytest.mark.parametrize("theirs", [{"bash": "deny"}, {"bash": "allow"}, {"web*": "deny"}])
@pytest.mark.parametrize("mode", ["ask", "edits", "plan", "full"])
def test_an_opencode_permission_already_in_the_environment_is_kept(tmp_path, monkeypatch, theirs, mode):
    """OpenCode reads ONE ``OPENCODE_PERMISSION``. A user who set their own in
    the environment Iron Jarvis starts from had it REPLACED by Iron Jarvis's
    (which leaves their tools out, so a ``"bash": "deny"`` of theirs fell to
    OpenCode's default allow)."""
    conf = tmp_path / "empty-conf"
    conf.mkdir()
    monkeypatch.setenv("IRONJARVIS_OPENCODE_CONFIG_DIR", str(conf))
    folder = tmp_path / "w"
    folder.mkdir()
    env, _note = O.child_env({"OPENCODE_PERMISSION": json.dumps(theirs)}, mode, str(folder))
    final = json.loads(env["OPENCODE_PERMISSION"])
    agent = "plan" if mode == "plan" else "build"
    for tool, pattern in (("bash", "npm test"), ("webfetch", "https://example.com")):
        if not any(O._wild(tool, k) for k in theirs):
            continue
        before = verdict(agent, config_permission({}, None, theirs), tool, pattern)
        assert verdict(agent, config_permission({}, None, final), tool, pattern) == before, (tool, final)
    if mode == "plan":
        assert verdict("plan", config_permission({}, None, final), "edit", "a.py") == "deny"


#: A pattern map and the legacy ``tools`` map together (claim 1 + question a).
COMBOS: list[tuple[dict[str, Any], dict[str, Any]]] = [
    ({"bash": {"git *": "allow", "*": "ask"}}, {"bash": False}),
    ({"bash": {"git *": "allow", "*": "ask"}}, {"webfetch": False}),
    ({"web*": "deny"}, {"write": True}),
    ({}, {"bash": True, "edit": False}),
]


@pytest.mark.parametrize("mode", ["ask", "edits", "plan", "full"])
@pytest.mark.parametrize(("user", "tools"), COMBOS)
def test_a_pattern_map_and_the_legacy_tools_map_together_are_not_changed(tmp_path, monkeypatch, mode, user,
                                                                         tools):
    env = env_for(tmp_path, monkeypatch, mode, user, tools)
    agent = "plan" if mode == "plan" else "build"
    named = set(user) | {("edit" if k in ("write", "edit", "patch") else k) for k in tools}
    for tool in ("bash", "edit", "webfetch", "websearch"):
        if not any(O._wild(tool, k) for k in named):
            continue
        if mode == "plan" and tool == "edit":
            continue
        for pattern in ("git status", "rm -rf build", "a.py", "https://x.example"):
            a = verdict(agent, config_permission(user, tools, env), tool, pattern)
            b = verdict(agent, config_permission(user, tools, None), tool, pattern)
            assert a == b, (mode, user, tools, tool, pattern, env)


# --------------------------------------------------------------------------- #
# "Allow for this session" is kept per conversation OF ONE HOME
# --------------------------------------------------------------------------- #
@pytest.fixture()
def _fresh_always(monkeypatch):
    monkeypatch.setattr(O, "SESSION_ALWAYS", {})


def test_an_allow_for_the_session_never_crosses_to_another_home(tmp_path, _fresh_always):
    """``SESSION_ALWAYS`` is the whole process's. Two Iron Jarvis homes in one
    process (every test of the suite starts its own app on the SAME recorded
    ``ses_`` ids) shared answers: a later run of the recorded "Allow for this
    session" scenario had its first card answered by an earlier run, and
    ``test_a_deny_is_opencodes_reject_and_allow_for_the_session_its_always``
    timed out waiting for a card whenever it ran after the golden test."""
    from iron_jarvis.hosted.driver import Ask
    from tests.test_build_app_opencode_rules_v1420 import _deliver, _driver, _recorded_request

    sid = "ses_cccccccccccccccccccccccc"
    first, *_ = _driver(sid)
    first.catalog_path = tmp_path / "home-a" / "hosted" / "opencode_models.json"
    first.answered(Ask("ask_1", "approval", "bash", 0, {}, {"command": "echo one"}, "", True), "allow_session")
    other, wrote, asks, _ = _driver(sid)
    other.catalog_path = tmp_path / "home-b" / "hosted" / "opencode_models.json"
    _deliver(other, _recorded_request("oc_bash_session"), "bash")
    assert len(asks) == 1 and wrote == [], "another home's answer was used"
    same, wrote, asks, _ = _driver(sid)
    same.catalog_path = tmp_path / "home-a" / "hosted" / "opencode_models.json"
    _deliver(same, _recorded_request("oc_bash_session"), "bash")
    assert asks == [] and wrote and wrote[0]["result"]["outcome"]["optionId"] == "always"
