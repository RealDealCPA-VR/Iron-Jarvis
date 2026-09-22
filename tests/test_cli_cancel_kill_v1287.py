"""chat-01 (v1.287.0): Stop kills the subscription CLI, and the cap is a cap.

The CLI adapters (codex-cli, claude-cli, opencode-cli) awaited
``asyncio.to_thread(subprocess.run(..., timeout=cap))``. Two defects:

* Stop / a closed tab / Retry cancelled only the AWAIT. The thread and the CLI
  ran on to completion, spending the user's plan and a pool thread, and every
  Retry started another copy.
* On a timeout ``subprocess.run`` killed only the direct child and then drained
  the pipes with NO timeout, so a helper process holding stdout (an npm ``.cmd``
  shim's node, a CLI's own helper) made the "cap" wait for the helper.

Every case here drives the REAL adapter with its REAL default runner (no
``runner=`` double) against a fake CLI launched through a shim, which is the
shape an npm install really has: shim -> CLI -> helper. The fake CLI writes its
own PID and its helper's PID to a file, so the pins assert that the processes
are GONE, not that a marker file stayed unwritten.
"""

from __future__ import annotations

import asyncio
import os
import stat
import sys
import time
from pathlib import Path

import psutil
import pytest

from iron_jarvis.providers.adapters import opencode_cli as oc
from iron_jarvis.providers.adapters import subprocess_cli as sc
from iron_jarvis.providers.adapters.base import LLMMessage, ProviderError

#: The fake CLI's (and its helper's) natural lifetime. Every "it died" check
#: below is measured against this, never against an absolute bar.
_LIFETIME_S = 40


def _fake_cli(tmp_path: Path) -> tuple[str, Path]:
    """A shim that starts a python "CLI" which starts a helper holding stdout.

    Returns ``(shim_path, pids_file)``. The CLI reads its prompt from stdin
    like the real ones, then sleeps; the helper inherits stdout and sleeps too.
    """
    pids = tmp_path / "pids.txt"
    script = tmp_path / "fake_cli.py"
    script.write_text(
        "import os, subprocess, sys, time\n"
        f"helper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep({_LIFETIME_S})'])\n"
        f"tmp = {str(pids)!r} + '.tmp'\n"
        "with open(tmp, 'w') as f:\n"
        "    f.write(f'{os.getpid()} {helper.pid}')\n"
        f"os.replace(tmp, {str(pids)!r})\n"
        "sys.stdin.read()\n"
        f"time.sleep({_LIFETIME_S})\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        shim = tmp_path / "fake_cli.cmd"
        shim.write_text(f'@"{sys.executable}" "{script}"\r\n', encoding="utf-8")
    else:
        shim = tmp_path / "fake_cli"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}"\n', encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    return str(shim), pids


def _adapter(kind: str, shim: str):
    """The real adapter for ``kind``, built by its real factory, default runner."""
    if kind == "codex-cli":
        return sc.make_codex_cli(which=lambda _b: shim)
    if kind == "claude-cli":
        return sc.make_claude_cli(which=lambda _b: shim)
    return oc.make_opencode_cli(
        model="spark/fleet", allowed=lambda: ["spark/fleet"], which=lambda _b: shim
    )


def _complete(adapter):
    return adapter.complete(
        system="", messages=[LLMMessage(role="user", content="hi")], tools=[]
    )


async def _read_pids(pids: Path, task: asyncio.Task) -> list[int]:
    """Wait for the fake CLI to announce itself (the thing the pin needs)."""
    deadline = time.monotonic() + _LIFETIME_S / 2
    while not pids.exists():
        assert not task.done(), f"the CLI call ended before the CLI started: {task!r}"
        assert time.monotonic() < deadline, "the fake CLI never started"
        await asyncio.sleep(0.05)
    return [int(p) for p in pids.read_text().split()]


def _alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


async def _still_alive_after_grace(pid_list: list[int]) -> list[int]:
    """PIDs still alive after a grace far shorter than their own lifetime.

    A killed tree is gone within a moment; one nobody killed lives on for
    ``_LIFETIME_S``. Polls, so a fast kill returns fast.
    """
    deadline = time.monotonic() + _LIFETIME_S / 4
    while time.monotonic() < deadline:
        left = [p for p in pid_list if _alive(p)]
        if not left:
            return []
        await asyncio.sleep(0.1)
    return [p for p in pid_list if _alive(p)]


def _reap(pid_list: list[int]) -> None:
    """Never leave a stray fake CLI behind, whatever the verdict."""
    for pid in pid_list:
        try:
            psutil.Process(pid).kill()
        except psutil.Error:
            pass


_KINDS = ["codex-cli", "claude-cli", "opencode-cli"]


@pytest.mark.parametrize("kind", _KINDS)
async def test_stop_kills_the_cli_and_its_helper(tmp_path, kind):
    shim, pids = _fake_cli(tmp_path)
    task = asyncio.create_task(_complete(_adapter(kind, shim)))
    pid_list = await _read_pids(pids, task)
    try:
        task.cancel()  # the user presses Stop
        with pytest.raises(asyncio.CancelledError):
            await task  # re-raised: the ledger's CANCELLED row depends on it
        left = await _still_alive_after_grace(pid_list)
        assert not left, (
            f"{kind}: Stop left the CLI tree running (pids {left} of {pid_list}); "
            f"it would have run on for up to {_LIFETIME_S}s on the user's plan"
        )
    finally:
        _reap(pid_list)


@pytest.mark.parametrize("kind", _KINDS)
async def test_the_cap_is_a_bound_with_a_helper_holding_the_pipe(
    tmp_path, monkeypatch, kind
):
    # 3 s, not 1: the fake CLI must have started (and named its PIDs) before
    # the cap fires, even on a loaded machine.
    monkeypatch.setattr(oc if kind == "opencode-cli" else sc, "_TIMEOUT_S", 3)
    shim, pids = _fake_cli(tmp_path)
    t0 = time.monotonic()
    with pytest.raises(ProviderError) as info:
        await _complete(_adapter(kind, shim))
    took = time.monotonic() - t0
    pid_list = [int(p) for p in pids.read_text().split()] if pids.exists() else []
    try:
        # Typed transient, so the router fails over — unchanged by the fix.
        assert info.value.transient is True
        assert "timed out" in str(info.value)
        # A 3 s cap must not wait for the helper's own exit. Measured against
        # the helper's lifetime (the old code took all of it), not a fixed bar.
        assert took < _LIFETIME_S / 4, (
            f"{kind}: a 3 s cap took {took:.1f}s — it waited for the helper "
            f"({_LIFETIME_S}s) instead of killing the tree"
        )
        assert pid_list, "the fake CLI never started"
        left = await _still_alive_after_grace(pid_list)
        assert not left, f"{kind}: the timed-out CLI tree is still running: {left}"
    finally:
        _reap(pid_list)


async def test_an_injected_runner_keeps_the_plain_two_arg_seam():
    """Test doubles pass ``runner(argv, stdin)``; that seam must keep working."""
    seen: dict = {}

    def runner(argv, stdin):
        seen["argv"], seen["stdin"] = argv, stdin
        return 0, "the answer", ""

    adapter = sc.SubprocessCliAdapter(
        "codex-cli", "codex",
        argv_builder=lambda _p, _m: ["exec", "-"],
        parse=lambda out: out,
        runner=runner,
        which=lambda _b: "/x/codex",
    )
    resp = await _complete(adapter)
    assert resp.text == "the answer"
    assert seen["argv"] == ["/x/codex", "exec", "-"]
    assert "hi" in seen["stdin"]
