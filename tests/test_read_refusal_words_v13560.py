"""v1.356.0 integrate (G81, lane 6C's notes): every refusal of a folder says
it in words a user can act on, and never names the environment variable.

Lane 6C gave the read gate (``fs_policy.fs_read_ok``) its sentence. Three
other doors still had their own words:

* ``file_search`` with a ``root`` answered "root is protected or outside
  IRONJARVIS_FS_ALLOWLIST" — it now answers the read gate's own sentence;
* the REPL child's audit hook said "reading X is outside
  IRONJARVIS_FS_ALLOWLIST (...)" — now "that folder isn't one Jarvis may
  read";
* ``fs_policy.root_problem``'s WORK-folder branch (a project root, a chat's
  work folder, a schedule's folder) refused a folder outside the user's
  "Folders Jarvis may read" as "protected or outside the app's allowed file
  roots" — it now says where that is changed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from iron_jarvis.core import fs_policy
from iron_jarvis.core.config import Config
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext

ENV = "IRONJARVIS_FS_ALLOWLIST"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    yield
    fs_policy.use_config(None)


def _folders(tmp_path: Path) -> tuple[Path, Path]:
    a = tmp_path / "Clients"
    c = tmp_path / "Private"
    for p in (a, c):
        p.mkdir()
        (p / "notes.txt").write_text(f"inside {p.name}", encoding="utf-8")
    return a, c


def _ctx(platform, ws: Path) -> ToolContext:
    ws.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        workspace=ws, session_id="rw", agent_run_id="r1",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
    )


def test_file_search_outside_the_chosen_folders_says_the_read_gates_sentence(tmp_path):
    a, c = _folders(tmp_path)
    platform = build_platform(str(tmp_path / "home"))
    cfg = Config(project_root=tmp_path, home=tmp_path / "h", readable_folders=[str(a)])
    fs_policy.use_config(cfg)
    tool = platform.registry.get("file_search")
    res = asyncio.run(tool.execute({"query": "inside", "root": str(c)}, _ctx(platform, tmp_path / "ws")))
    assert res.ok is False
    assert res.error == fs_policy.READ_REFUSAL
    assert ENV not in (res.error or "")
    # Control: inside a chosen folder the search runs.
    ok = asyncio.run(tool.execute({"query": "inside", "root": str(a), "mode": "content"}, _ctx(platform, tmp_path / "ws")))
    assert ok.ok is True, ok.error


def test_a_protected_root_is_still_refused_by_file_search(tmp_path):
    platform = build_platform(str(tmp_path / "home"))
    protected = Path(platform.config.home) / "secrets"
    protected.mkdir(parents=True, exist_ok=True)
    tool = platform.registry.get("file_search")
    res = asyncio.run(tool.execute({"query": "x", "root": str(protected)}, _ctx(platform, tmp_path / "ws")))
    assert res.ok is False and "protected" in (res.error or "")


def test_the_repl_refuses_a_read_outside_the_installation_limit_in_words(tmp_path, monkeypatch):
    a, c = _folders(tmp_path)
    monkeypatch.setenv(ENV, str(a))
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"

    async def body():
        try:
            return await platform.registry.invoke(
                "repl",
                {"code": f"print(open({str(c / 'notes.txt')!r}).read())", "timeout_s": 30},
                _ctx(platform, ws),
                platform.permissions,
                session_allow=["repl"],
            )
        finally:
            if platform.repl is not None:
                await platform.repl.dispose_all()

    res = asyncio.run(body())
    text = f"{res.error or ''}\n{res.output or ''}"
    assert "that folder isn't one Jarvis may read" in text, text
    assert "IRONJARVIS_FS_ALLOWLIST" not in text


def test_a_work_folder_outside_the_chosen_folders_says_where_to_change_them(tmp_path):
    a, c = _folders(tmp_path)
    cfg = Config(project_root=tmp_path, home=tmp_path / "h", readable_folders=[str(a)])
    fs_policy.use_config(cfg)
    why = fs_policy.root_problem(c)
    assert why is not None and "Settings › Permissions" in why
    assert "allowed file roots" not in why
    assert fs_policy.root_problem(a) is None, "control: a chosen folder is a fine work folder"


def test_the_installation_limit_keeps_its_old_work_folder_words(tmp_path, monkeypatch):
    """Settings cannot change the installation's limit, so its refusal does
    not send the user there."""
    a, c = _folders(tmp_path)
    monkeypatch.setenv(ENV, str(a))
    why = fs_policy.root_problem(c)
    assert why is not None and "Settings › Permissions" not in why
