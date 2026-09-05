"""v1.228.0 (audit Wave 2, T3 + T6) — an unwritable workspace is refused UP
FRONT, with the reason, and the model is told which OS it is on.

T3, THE MEASURED FAILURE: live chat session_793e1a55ae47 (2026-08-26) was
bound to ``C:\\Users``. ``fs_policy.usable_workspace_root`` accepted it
(absolute + is_dir + allowlist-clean + not protected — never "can I save a
file here"), the grounding block told the model it was "bound to the folder",
and the first ``write_document`` died with ``PermissionError: [Errno 13]
Permission denied: 'C:\\Users\\.Qwen_Local_Models_Research.docx.tmp-47352'``
— a hidden sibling temp file the user can neither see nor fix.

Pinned here:
* ``usable_workspace_root`` PROBES writability (``dir_writable``: create a
  temp file, delete it) — a real RX-only folder and the real system roots are
  refused; an OSError from the probe is "not writable" on any platform;
* ``_root_problem`` (project create/patch/task) has a FOURTH answer naming the
  folder and "not writable";
* ``POST /sessions`` and ``POST /agents/{name}/spawn`` answer 400 with the
  reason AND run the probe OFF the event loop (it creates a file in a folder
  the user picked — a share can stall); ``POST /projects/{id}/task`` too;
* the chat grounding block (shared by BOTH lanes) says "not writable" in its
  honest wording, and ``_resolve_tool_workspace`` no longer binds the turn to
  such a folder;
* ``write_document`` / ``write_file`` / ``edit_file`` in a bound-but-unwritable
  folder say "cannot write in <workspace>: ... not writable" — never the
  ``.tmp-<pid>`` filename — while ``safe_path``'s own escape refusal keeps its
  words (it is a PermissionError with no errno).

T6: ``tool_create``'s description carries this machine's OS (on Windows:
cmd.exe, no POSIX mv/ls/cp) and the runtime's ``# Environment`` block gains
``- OS:`` — a tool authored around ``mv`` on the dev box (Git's mv.EXE on ITS
PATH) died 22/22 on the packaged install because nothing ever said Windows.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import iron_jarvis.core.fs_policy as fs_policy
import iron_jarvis.daemon.routes.projects as projects_routes
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.fs_policy import dir_writable, usable_workspace_root
from iron_jarvis.core.models import AgentType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import _resolve_tool_workspace, _workspace_grounding_block
from iron_jarvis.daemon.routes.projects import _root_problem
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.sandbox.native import host_os_line
from iron_jarvis.tools.base import ToolContext, unwritable_workspace_error

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ctx(platform, workspace, session_id: str = "ws-s1") -> ToolContext:
    return ToolContext(
        workspace=Path(workspace), session_id=session_id, agent_run_id="ws-r1",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
    )


async def _invoke(platform, name, args, workspace):
    return await platform.registry.invoke(
        name, args, _ctx(platform, workspace), platform.permissions, session_allow=None
    )


def _is_writable_dir(folder) -> bool:
    p = Path(folder) / ".ij_v1228_probe.txt"
    try:
        p.write_text("x", encoding="utf-8")
    except OSError:
        return False
    try:
        p.unlink()
    except OSError:
        pass
    return True


def _principal() -> str:
    """The account icacls grants to: ``whoami`` (``machine\\user``), NOT
    ``%USERNAME%`` — on a box whose computer name equals the user name a bare
    ``VR`` resolves to the MACHINE account and the user is left with no access
    at all (unreadable, not read-only — measured while writing this file)."""
    try:
        out = subprocess.run(["whoami"], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        out = ""
    return out or os.environ.get("USERNAME", "")


def _make_readonly_dir(base: Path, seed: tuple[str, ...] = ()) -> Path | None:
    """A real RX-only folder via icacls (Windows only); files in ``seed`` are
    created BEFORE the lock. None when the platform cannot build one."""
    if os.name != "nt":
        return None
    d = base / "readonly_ws"
    d.mkdir(exist_ok=True)
    for name in seed:
        (d / name).write_text("before\n", encoding="utf-8")
    user = _principal()
    try:
        # The folder AND each seeded file: cutting the folder's inheritance
        # also strips what the file inherited, which makes it unreadable
        # rather than read-only, so the file gets its own explicit (RX).
        for target in (*(d / name for name in seed), d):  # files FIRST
            subprocess.run(
                ["icacls", str(target), "/inheritance:r", "/grant:r", f"{user}:(RX)"],
                check=True, capture_output=True, text=True,
            )
    except Exception:  # noqa: BLE001
        return None
    return None if _is_writable_dir(d) else d


def _restore_dir(d: Path | None) -> None:
    if d is None or os.name != "nt":
        return
    user = _principal()
    subprocess.run(["icacls", str(d), "/grant", f"{user}:(F)"], capture_output=True, text=True)
    subprocess.run(["icacls", str(d), "/reset", "/T"], capture_output=True, text=True)
    subprocess.run(["icacls", str(d), "/grant", f"{user}:(F)", "/T"], capture_output=True, text=True)


@pytest.fixture
def readonly_dir(tmp_path):
    d = _make_readonly_dir(tmp_path, seed=("note.txt",))
    if d is None:
        pytest.skip("could not build a read-only folder on this platform")
    yield d
    _restore_dir(d)


@pytest.fixture
def client(tmp_path, monkeypatch):
    async def _no_run(self, session_id, definition=None):
        return self.get_session(session_id)

    monkeypatch.setattr(Orchestrator, "run_session", _no_run)
    return TestClient(create_app(str(tmp_path / "home")))


def _unwritable(monkeypatch):
    """Make the probe say 'not writable' for every folder, on any platform —
    and record whether it was asked ON the event loop."""
    seen: dict = {}

    def _probe(path):
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        return False

    monkeypatch.setattr(fs_policy, "dir_writable", _probe)
    return seen


_WIN = os.name == "nt"
_REAL_UNWRITABLE = [
    p for p in ("C:\\Users", "C:\\") if _WIN and Path(p).is_dir() and not _is_writable_dir(p)
]


# ---------------------------------------------------------------------------
# the predicate
# ---------------------------------------------------------------------------


def test_probe_failure_makes_the_folder_unusable(tmp_path, monkeypatch):
    """Platform-independent: the probe raising OSError is 'not writable', and
    the ONE predicate every door uses says so."""
    assert usable_workspace_root(tmp_path) is True
    real_open = os.open

    def _deny_probe(path, *a, **kw):
        if ".ij-probe-" in os.fspath(path):
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real_open(path, *a, **kw)

    monkeypatch.setattr(os, "open", _deny_probe)
    assert dir_writable(tmp_path) is False
    assert usable_workspace_root(tmp_path) is False


def test_probe_is_one_open_never_tempfiles_retry_loop(tmp_path, monkeypatch):
    """tempfile.mkstemp/NamedTemporaryFile retry PermissionError TMP_MAX times
    on Windows (os.access lies about ACLs) — a probe built on them spun for
    minutes on a real RX-only folder. Pin: the probe never calls them."""
    calls: list[str] = []
    monkeypatch.setattr(tempfile, "NamedTemporaryFile",
                        lambda *a, **kw: calls.append("ntf") or (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(tempfile, "mkstemp",
                        lambda *a, **kw: calls.append("mkstemp") or (_ for _ in ()).throw(OSError()))
    assert dir_writable(tmp_path) is True
    assert calls == []


def test_probe_leaves_nothing_behind(tmp_path):
    assert dir_writable(tmp_path) is True
    assert list(tmp_path.iterdir()) == []


def test_readonly_folder_is_not_a_usable_workspace(readonly_dir):
    assert dir_writable(readonly_dir) is False
    assert usable_workspace_root(readonly_dir) is False


@pytest.mark.parametrize("folder", _REAL_UNWRITABLE or ["<none>"])
def test_system_roots_the_app_cannot_write_in_are_refused(folder):
    if folder == "<none>":
        pytest.skip("no real unwritable system folder on this machine (elevated?)")
    assert usable_workspace_root(folder) is False


# ---------------------------------------------------------------------------
# the project-root door: a fourth honest answer
# ---------------------------------------------------------------------------


def test_root_problem_names_not_writable(tmp_path, monkeypatch):
    assert _root_problem(str(tmp_path)) is None
    _unwritable(monkeypatch)
    problem = _root_problem(str(tmp_path))
    assert problem is not None
    assert "not writable" in problem and str(tmp_path) in problem, problem
    assert "save files in" in problem


def test_root_problem_on_a_real_readonly_folder(readonly_dir):
    problem = _root_problem(str(readonly_dir))
    assert problem is not None and "not writable" in problem, problem


def test_root_problem_keeps_the_protected_answer_first(tmp_path, monkeypatch):
    """A protected folder is named as protected, not as 'not writable'."""
    _unwritable(monkeypatch)
    monkeypatch.setattr(fs_policy, "is_protected_path", lambda p: True)
    problem = _root_problem(str(tmp_path))
    assert problem is not None and "protected" in problem and "not writable" not in problem


def test_project_create_and_patch_refuse_an_unwritable_root(client, tmp_path, monkeypatch):
    ok = tmp_path / "ok"
    ok.mkdir()
    r = client.post("/projects", json={"name": "P", "root": str(ok)})
    assert r.status_code == 200, r.text[:300]
    pid = r.json()["id"]
    _unwritable(monkeypatch)
    bad = tmp_path / "bad"
    bad.mkdir()
    r = client.post("/projects", json={"name": "Q", "root": str(bad)})
    assert r.status_code == 400 and "not writable" in r.json()["detail"], r.text[:300]
    r = client.patch(f"/projects/{pid}", json={"root": str(bad)})
    assert r.status_code == 400 and "not writable" in r.json()["detail"], r.text[:300]


def test_project_task_refuses_an_older_row_off_the_loop(client, tmp_path, monkeypatch):
    """An older project row predates the probe; the task route asks again —
    and asks OFF the event loop (the probe creates a file in a user folder)."""
    root = tmp_path / "older"
    root.mkdir()
    r = client.post("/projects", json={"name": "Old", "root": str(root)})
    assert r.status_code == 200, r.text[:300]
    pid = r.json()["id"]
    seen = _unwritable(monkeypatch)
    r = client.post(f"/projects/{pid}/task", json={"text": "write it", "output": "chat"})
    assert r.status_code == 400, r.text[:300]
    assert "not writable" in r.json()["detail"], r.json()["detail"]
    assert seen.get("on_loop") is False, "the writability probe ran ON the event loop"


# ---------------------------------------------------------------------------
# POST /sessions + POST /agents/{name}/spawn: 400 with the reason, off-loop
# ---------------------------------------------------------------------------


def test_post_sessions_refuses_an_unwritable_workspace_root(client, tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    seen = _unwritable(monkeypatch)
    r = client.post("/sessions", json={
        "task": "write a report here", "wait": False,
        "workspace_root": str(ws), "origin": "chat",
    })
    assert r.status_code == 400, (r.status_code, r.text[:300])
    detail = r.json()["detail"]
    assert "not writable" in detail and str(ws) in detail, detail
    assert seen.get("on_loop") is False, "the writability probe ran ON the event loop"


def test_post_sessions_refuses_a_real_readonly_folder(client, readonly_dir):
    r = client.post("/sessions", json={
        "task": "write a report here", "wait": False,
        "workspace_root": str(readonly_dir), "origin": "chat",
    })
    assert r.status_code == 400, (r.status_code, r.text[:300])
    assert "not writable" in r.json()["detail"]


def test_post_sessions_still_accepts_a_writable_folder(client, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    r = client.post("/sessions", json={
        "task": "write a report here", "wait": False,
        "workspace_root": str(ws), "origin": "chat",
    })
    assert r.status_code == 200, r.text[:300]
    assert Path(r.json()["workspace_path"]) == ws


def test_agent_spawn_refuses_an_unwritable_workspace_root(client, tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    seen = _unwritable(monkeypatch)
    r = client.post("/agents/builder/spawn", json={
        "task": "write a report here", "wait": False, "workspace_root": str(ws),
    })
    assert r.status_code == 400, (r.status_code, r.text[:300])
    detail = r.json()["detail"]
    assert "not writable" in detail and str(ws) in detail, detail
    assert seen.get("on_loop") is False, "the writability probe ran ON the event loop"


# ---------------------------------------------------------------------------
# the chat lanes: no false grounding, honest wording (shared block)
# ---------------------------------------------------------------------------


def test_chat_lane_does_not_bind_to_an_unwritable_folder(tmp_path, monkeypatch):
    picked = tmp_path / "picked"
    picked.mkdir()
    _unwritable(monkeypatch)
    resolved = _resolve_tool_workspace(tmp_path / "uploads", str(picked), "")
    assert resolved[1] is False and resolved[0] == tmp_path / "uploads"
    block = _workspace_grounding_block(str(picked), resolved)
    assert "not accessible" in block and "not writable" in block, block
    assert str(picked) in block


def test_chat_grounding_block_on_a_real_readonly_folder(tmp_path, readonly_dir):
    resolved = _resolve_tool_workspace(tmp_path / "uploads", str(readonly_dir), "")
    assert resolved[1] is False
    block = _workspace_grounding_block(str(readonly_dir), resolved)
    assert "not writable" in block, block


def test_both_chat_lanes_render_the_same_grounding_block():
    """The wording lives in ONE function; both lanes must call it (the
    v1.210.0 lock-step pair) — pinned by call site, since a lane that stopped
    calling it would keep every unit test above green."""
    import iron_jarvis.daemon.chat_turn as ct
    import iron_jarvis.daemon.routes.chat as rc
    from pathlib import Path as _P

    call = "_workspace_grounding_block(body.workspace_dir, _ws_resolved)"
    assert call in _P(ct.__file__).read_text(encoding="utf-8")
    assert call in _P(rc.__file__).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# the write tools: the folder, not the temp file
# ---------------------------------------------------------------------------


def test_unwritable_error_keeps_the_confinement_words():
    ws = Path("C:/ws") if _WIN else Path("/ws")
    escape = PermissionError("path '../x' escapes the session workspace")
    assert "escapes the session workspace" in unwritable_workspace_error(escape, ws)
    denied = PermissionError(13, "Permission denied", "C:\\ws\\.a.docx.tmp-1")
    text = unwritable_workspace_error(denied, ws)
    assert text.startswith(f"cannot write in {ws}:") and "not writable" in text
    assert ".tmp-" not in text


async def test_write_document_names_the_folder_not_a_temp_file(platform, readonly_dir):
    res = await _invoke(platform, "write_document",
                        {"path": "ij_v1228.docx", "content": "# hi"}, readonly_dir)
    assert not res.ok
    err = res.error or ""
    assert ".tmp-" not in err, err
    assert err.startswith(f"cannot write in {readonly_dir}") and "not writable" in err, err


async def test_write_file_names_the_folder(platform, readonly_dir):
    res = await _invoke(platform, "write_file", {"path": "t.txt", "content": "t"}, readonly_dir)
    assert not res.ok
    assert (res.error or "").startswith(f"cannot write in {readonly_dir}"), res.error
    assert "Errno" not in (res.error or "")


async def test_edit_file_names_the_folder(platform, readonly_dir):
    res = await _invoke(platform, "edit_file",
                        {"path": "note.txt", "old": "before", "new": "after"}, readonly_dir)
    assert not res.ok
    assert (res.error or "").startswith(f"cannot write in {readonly_dir}"), res.error
    assert (readonly_dir / "note.txt").read_text(encoding="utf-8") == "before\n"


async def test_write_file_escape_is_still_refused_with_its_own_words(platform, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    res = await _invoke(platform, "write_file", {"path": "../out.txt", "content": "t"}, ws)
    assert not res.ok
    assert "escapes the session workspace" in (res.error or ""), res.error


# ---------------------------------------------------------------------------
# T6: the model is told the OS
# ---------------------------------------------------------------------------


def test_host_os_line_is_honest_per_platform():
    win = host_os_line("Windows")
    assert win.startswith("Windows") and "cmd.exe" in win and "no POSIX mv/ls/cp" in win
    assert host_os_line("Linux").startswith("Linux") and "POSIX" in host_os_line("Linux")
    assert host_os_line("Darwin").startswith("Darwin")
    live = host_os_line()
    assert (live.startswith("Windows")) == _WIN


def test_tool_create_description_names_this_machines_os(platform):
    spec = platform.registry.get("tool_create").spec()
    text = spec.get("description") or ""
    assert "This machine's OS:" in text, text[-200:]
    if _WIN:
        assert "Windows" in text and "cmd.exe" in text and "no POSIX mv/ls/cp" in text
    else:
        assert "POSIX" in text


class _CapturingMock(MockLLMAdapter):
    def __init__(self, box: dict):
        super().__init__()
        self._box = box

    async def complete(self, **kw):
        self._box["system"] = kw.get("system", "")
        return await super().complete(**kw)


async def test_runtime_environment_block_names_the_os_both_shapes(platform, tmp_path):
    expected = f"- OS: {host_os_line()}"
    box: dict = {}
    platform.providers.register("cap-os", lambda: _CapturingMock(box))
    orch = Orchestrator(platform)
    # scratch shape
    sess = await orch.create_session("plain task", AgentType.BUILDER, provider="cap-os")
    await orch.run_session(sess.id)
    assert "# Environment" in box["system"] and expected in box["system"], box["system"][-600:]
    # direct (in-folder) shape
    folder = tmp_path / "proj-root"
    folder.mkdir()
    box.clear()
    sess = await orch.create_session(
        "in-folder task", AgentType.BUILDER, provider="cap-os", workspace_root=str(folder)
    )
    await orch.run_session(sess.id)
    assert "directly in the project folder" in box["system"]
    assert expected in box["system"], box["system"][-600:]
