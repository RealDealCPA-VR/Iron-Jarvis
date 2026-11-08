"""Wave D (readiness plan 0h.5), builder D2's daemon half — items 34, 36, 42.

  * 34 — a Reflex rule and a Sentinel can be EDITED in place: ``PATCH
    /reflex/rules/{id}`` and the new ``PATCH /sentinels/{name}`` are PARTIAL
    (omitted = keep, like ``PATCH /schedules/{name}``), the MERGED record
    passes exactly the add-time rules, and a refused edit changes NOTHING.
  * 36 — ``POST /build/git/init`` makes a folder a repository with one first
    commit of its files (the night commit's own screen leaves secrets out),
    so a night task can branch from it; a network spelling is refused by its
    text before anything is looked at; a folder already in a repository is
    left as it is.
  * 42 — a workflow saved with a tool step naming a tool this install does
    not have is refused at SAVE time: ``steps[i] (label): no tool named 'x'``.

Every guarantee here went red with its fix reverted (see the report).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import branches as B
from iron_jarvis.workflows.store import WorkflowStore


# --------------------------------------------------------------------------- #
# 34 — Reflex rules edit in place
# --------------------------------------------------------------------------- #


def _rule(client: TestClient, **over) -> dict:
    body = {"name": "r", "source": "comm", "match": "deploy", "action": "session",
            "task_template": "Do {text}", **over}
    resp = client.post("/reflex/rules", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_reflex_patch_edits_the_fields_given_and_keeps_the_rest(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        WorkflowStore(client.app.state.platform.engine).save("nightly", [{"agent": "builder", "task": "x"}])
        rule = _rule(client)
        rid = rule["id"]
        edited = client.patch(f"/reflex/rules/{rid}", json={"match": "invoice", "name": "Invoices"})
        assert edited.status_code == 200, edited.text
        got = edited.json()
        assert got["match"] == "invoice" and got["name"] == "Invoices"
        # Omitted = kept: the source, action, task template and the switch.
        assert got["source"] == "comm" and got["action"] == "session"
        assert got["task_template"] == "Do {text}" and got["enabled"] is True
        # The shape can change too: a message rule becomes a workflow rule.
        moved = client.patch(f"/reflex/rules/{rid}", json={"action": "workflow", "target": "nightly"})
        assert moved.status_code == 200
        assert moved.json()["action"] == "workflow" and moved.json()["target"] == "nightly"
        # The edit landed on disk: the list says the same.
        rows = client.get("/reflex/rules").json()["rules"]
        assert [r for r in rows if r["id"] == rid][0]["target"] == "nightly"


def test_reflex_patch_runs_the_add_time_rules_over_the_merged_rule_and_changes_nothing_when_refused(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        rule = _rule(client)
        rid = rule["id"]
        # A workflow action with no target is refused — and the switch given
        # in the SAME body is not flipped either: a refused edit is no edit.
        refused = client.patch(f"/reflex/rules/{rid}", json={"action": "workflow", "enabled": False})
        assert refused.status_code == 400
        assert "target" in refused.json()["detail"]
        same = [r for r in client.get("/reflex/rules").json()["rules"] if r["id"] == rid][0]
        assert same["action"] == "session" and same["enabled"] is True
        # A webhook source needs a slug (the merged match is empty after "").
        assert client.patch(f"/reflex/rules/{rid}", json={"source": "webhook", "match": ""}).status_code == 400
        # An unknown source or action is refused by name.
        assert client.patch(f"/reflex/rules/{rid}", json={"source": "pigeon"}).status_code == 400
        assert client.patch(f"/reflex/rules/{rid}", json={"action": "teleport"}).status_code == 400
        assert client.patch("/reflex/rules/nope", json={"name": "x"}).status_code == 404


def test_reflex_patch_to_a_remote_agent_drops_the_grounding_as_the_add_does(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        proj = client.post("/projects", json={"name": "Client A", "root": str(tmp_path)}).json()
        rule = _rule(client, project_id=proj["id"])
        assert rule["project_id"] == proj["id"]
        moved = client.patch(f"/reflex/rules/{rule['id']}", json={"action": "remote_agent", "target": "hermes"})
        assert moved.status_code == 200
        assert moved.json()["project_id"] is None
        assert p.reflex.get(rule["id"]).project_id is None


def test_reflex_store_update_never_writes_a_counter():
    from iron_jarvis.reflex.store import ReflexStore

    assert "fire_count" not in ReflexStore.EDITABLE and "last_error" not in ReflexStore.EDITABLE
    with pytest.raises(ValueError):
        ReflexStore.__dict__["update"](ReflexStore.__new__(ReflexStore), "x", fire_count=99)


# --------------------------------------------------------------------------- #
# 34 — Sentinels edit in place
# --------------------------------------------------------------------------- #


def test_sentinel_patch_is_partial_and_a_changed_watch_starts_a_new_baseline(tmp_path):
    watched = tmp_path / "in"
    watched.mkdir()
    other = tmp_path / "out"
    other.mkdir()
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        made = client.post("/sentinels", json={"name": "w", "path": str(watched), "glob": "*.pdf",
                                               "task": "file it", "risk": "low"})
        assert made.status_code == 200, made.text
        # A check records the baseline (the service's own rule).
        svc = client.app.state.platform.sentinels
        svc.check("w")
        assert svc.get("w").last_checked_at is not None
        # Edit the task only: the watch spec, the risk and the baseline stay.
        edited = client.patch("/sentinels/w", json={"task": "summarise it"})
        assert edited.status_code == 200, edited.text
        row = edited.json()
        assert row["task"] == "summarise it" and row["config"] == {"path": str(watched), "glob": "*.pdf"}
        assert row["risk"] == "low" and row["last_checked_at"] is not None
        # A new path resets the seen state: the next check is a baseline, never
        # a flood of "new" files from a folder it never watched.
        moved = client.patch("/sentinels/w", json={"path": str(other), "glob": ""})
        assert moved.status_code == 200, moved.text
        assert moved.json()["config"] == {"path": str(other)}
        assert moved.json()["last_checked_at"] is None
        assert svc.get("w").decoded_state() == {}
        # Risk, agent and the switch edit too; the name is the key (not in the body).
        more = client.patch("/sentinels/w", json={"risk": "med", "agent_type": "reviewer", "enabled": False})
        assert more.json()["risk"] == "med" and more.json()["agent_type"] == "reviewer"
        assert more.json()["enabled"] is False and more.json()["name"] == "w"


def test_sentinel_patch_refuses_a_bad_watch_in_words_and_changes_nothing(tmp_path):
    watched = tmp_path / "in"
    watched.mkdir()
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        client.post("/sentinels", json={"name": "w", "path": str(watched), "task": "t"})
        bad = client.patch("/sentinels/w", json={"path": "", "task": "changed too"})
        assert bad.status_code == 400
        assert "path" in bad.json()["detail"]
        row = [s for s in client.get("/sentinels").json()["sentinels"] if s["name"] == "w"][0]
        assert row["config"]["path"] == str(watched) and row["task"] == "t"
        assert client.patch("/sentinels/nope", json={"task": "x"}).status_code == 404


# --------------------------------------------------------------------------- #
# 42 — a tool step names a tool this install has
# --------------------------------------------------------------------------- #


def test_saving_a_tool_step_that_names_no_tool_of_this_install_is_refused_by_name(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        steps = [{"name": "List", "kind": "tool", "tool": "list_foldr", "args": {"path": "."}}]
        resp = client.post("/workflows", json={"name": "wf", "steps": steps})
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail.startswith("steps[0] (List): no tool named 'list_foldr'"), detail
        assert "Settings" in detail
        # A tool the registry holds saves.
        names = set(client.app.state.platform.registry.names())
        real = "read_file" if "read_file" in names else sorted(names)[0]
        ok = client.post("/workflows", json={"name": "wf", "steps": [{**steps[0], "tool": real}]})
        assert ok.status_code == 200, ok.text


def test_an_unreadable_registry_refuses_nothing_at_save(tmp_path, monkeypatch):
    with TestClient(create_app(str(tmp_path))) as client:
        reg = client.app.state.platform.registry

        def boom():
            raise RuntimeError("down")

        monkeypatch.setattr(reg, "names", boom)
        steps = [{"name": "List", "kind": "tool", "tool": "list_foldr", "args": {}}]
        assert client.post("/workflows", json={"name": "wf", "steps": steps}).status_code == 200


# --------------------------------------------------------------------------- #
# 36 — make this folder a repository (real git)
# --------------------------------------------------------------------------- #


def _git(*args: str, cwd: Path) -> str:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, env=env)
    assert r.returncode == 0, (args, r.stderr.decode("utf-8", "replace"))
    return r.stdout.decode("utf-8", "replace").rstrip()


@pytest.fixture()
def plain_folder(tmp_path) -> Path:
    folder = tmp_path / "shop"
    folder.mkdir()
    (folder / "app.py").write_bytes(b"x = 1\n")
    (folder / "notes.md").write_bytes(b"# notes\n")
    # A secret-named file the night commit's screen must leave out.
    (folder / ".env").write_bytes(b"TOKEN=abc\n")
    return folder


def test_init_repo_makes_a_repository_with_one_first_commit_that_leaves_secrets_out(plain_folder):
    before = B.inspect_folder(str(plain_folder))
    assert before["git"] and not before["repo"]
    got = B.init_repo(str(plain_folder))
    assert got["made"] is True and got["repo"] is True and got["can_branch"] is True, got
    assert (plain_folder / ".git").is_dir()
    # One commit, holding the files but never the secret-named one.
    assert _git("rev-list", "--count", "HEAD", cwd=plain_folder) == "1"
    tracked = set(_git("ls-files", cwd=plain_folder).splitlines())
    assert tracked == {"app.py", "notes.md"}, tracked
    assert [x["path"] for x in got["left_out"]] == [".env"]
    assert _git("log", "-1", "--format=%s", cwd=plain_folder) == B.FIRST_COMMIT_MESSAGE
    # The night shift's own folder check takes it now.
    import asyncio

    from iron_jarvis.daemon.routes import night as NR

    assert asyncio.run(NR._check_folder(str(plain_folder))) == str(plain_folder)


def test_init_repo_leaves_a_repository_alone_and_refuses_an_empty_folder(plain_folder, tmp_path):
    B.init_repo(str(plain_folder))
    head = _git("rev-parse", "HEAD", cwd=plain_folder)
    (plain_folder / "later.txt").write_bytes(b"new\n")
    again = B.init_repo(str(plain_folder))
    assert again["made"] is False and again["repo"] is True
    assert _git("rev-parse", "HEAD", cwd=plain_folder) == head  # nothing committed
    assert "later.txt" not in _git("ls-files", cwd=plain_folder)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(B.GitRefused) as exc:
        B.init_repo(str(empty))
    assert str(exc.value) == B.INIT_NEEDS_FILES


def test_init_repo_refuses_a_network_spelling_by_its_text_before_anything_is_looked_at(monkeypatch):
    from iron_jarvis.core import fs_policy

    def never(*_a, **_k):
        raise AssertionError("the folder rules ran on a network path")

    monkeypatch.setattr(fs_policy, "root_problem", never)
    for text in (r"\\host\share\proj", "//host/share/proj", r"\\?\UNC\host\share"):
        with pytest.raises(B.GitRefused) as exc:
            B.init_repo(text)
        assert str(exc.value) == B.NETWORK_FOLDER and exc.value.status == 400


def test_the_init_route_answers_the_facts_and_a_refusal_in_words(tmp_path, plain_folder):
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        resp = client.post("/build/git/init", json={"path": str(plain_folder)})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["made"] is True and body["can_branch"] is True
        assert body["left_out"] == [{"path": ".env", "why": body["left_out"][0]["why"]}]
        refused = client.post("/build/git/init", json={"path": r"\\host\share\x"})
        assert refused.status_code == 400
        assert refused.json()["detail"] == B.NETWORK_FOLDER
        nothing = client.post("/build/git/init", json={"path": ""})
        assert nothing.status_code == 400
