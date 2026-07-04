"""list_folder: agents can see the user's REAL folders (policy-gated)."""

from __future__ import annotations

import pytest

from iron_jarvis.documents.tools import ListFolderTool


class _Ctx:
    def __init__(self, ws):
        self.workspace = ws
        self.session_id = "s"
        self.agent_run_id = "r"


@pytest.mark.asyncio
async def test_lists_absolute_folder_biggest_first(tmp_path):
    (tmp_path / "big.bin").write_bytes(b"x" * 500)
    (tmp_path / "small.txt").write_text("hi")
    (tmp_path / "sub").mkdir()
    res = await ListFolderTool().execute({"path": str(tmp_path)}, _Ctx(tmp_path))
    assert res.ok, res.error
    lines = res.output.splitlines()
    assert "3 entries" in lines[0]
    assert "big.bin" in lines[1]  # biggest first
    assert any("DIR" in ln and "sub" in ln for ln in lines)


@pytest.mark.asyncio
async def test_not_a_folder_and_denied(tmp_path, monkeypatch):
    res = await ListFolderTool().execute({"path": str(tmp_path / "nope")}, _Ctx(tmp_path))
    assert not res.ok and "not a folder" in res.error

    import iron_jarvis.documents.tools as m

    monkeypatch.setattr(m, "fs_read_ok", lambda p: (False, "blocked by policy"))
    res2 = await ListFolderTool().execute({"path": str(tmp_path)}, _Ctx(tmp_path))
    assert not res2.ok and "read denied" in res2.error


@pytest.mark.asyncio
async def test_limit_caps_output(tmp_path):
    for i in range(15):
        (tmp_path / f"f{i}.txt").write_text("x" * i)
    res = await ListFolderTool().execute({"path": str(tmp_path), "limit": 5}, _Ctx(tmp_path))
    assert res.ok and "showing 5" in res.output
    assert res.data["total"] == 15
