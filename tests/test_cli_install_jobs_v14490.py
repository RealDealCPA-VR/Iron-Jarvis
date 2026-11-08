"""v1.449.0 (readiness Wave C, item 20) — "Install Claude Code for me".

A Claude Pro / ChatGPT Plus subscriber WITHOUT the command-line tool used to
be sent to a vendor web page with a PowerShell command to paste. Now the
connect door offers one press that opens a Build terminal running the
vendor's OWN documented installer — a set-up job in the launch catalog
(``terminals/ai_clis.SETUP_LAUNCHES``), asked for BY NAME, never a command
the page sends. Nothing is installed silently: the terminal shows every
step, and the Sign in press takes over once the tool is found.

Pinned here: the two jobs and their exact lines (the vendors' pages, read
2026-11-07, are named in the module); the Command Prompt form for a cmd
pane; the real route typing the installer into a NEW pane and pressing
Enter; the jobs kept OFF the Launch menu and the CLI list; the dashboard's
twin (``lib/cliInstall.ts``) naming the same ids and sending no command.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.terminals import ai_clis
from iron_jarvis.terminals.backend import FakeBackend

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"

CLAUDE_PS = "irm https://claude.ai/install.ps1 | iex"
CLAUDE_CMD = "curl -fsSL https://claude.ai/install.cmd -o install.cmd && install.cmd && del install.cmd"
CODEX_PS = "irm https://chatgpt.com/codex/install.ps1 | iex"
CODEX_CMD = 'powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"'


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


# --------------------------------------------------------------------------- #
# the catalog
# --------------------------------------------------------------------------- #
def test_the_two_install_jobs_are_catalog_entries_with_the_vendors_own_lines():
    """MUTATION: change a line, drop a job, or give it an agent_cli -> red."""
    claude = ai_clis.launch_entry("claude-code-install")
    codex = ai_clis.launch_entry("codex-install")
    assert claude is not None and codex is not None
    assert claude["label"] == "Install Claude Code" and codex["label"] == "Install Codex"
    assert claude["command"] == CLAUDE_PS and claude["command_cmd"] == CLAUDE_CMD
    assert codex["command"] == CODEX_PS and codex["command_cmd"] == CODEX_CMD
    # A plain shell, not an AI CLI: the pane-state classifier and the bell
    # read AI_CLIS as "the CLIs", and an installer is neither.
    assert claude["agent_cli"] == "" and codex["agent_cli"] == ""
    assert ai_clis.INSTALL_JOBS == {"claude": "claude-code-install", "codex": "codex-install"}
    # The route's own normalising applies to a job id too.
    assert ai_clis.launch_entry("  Claude-Code-Install ") is claude


def test_the_jobs_name_where_their_lines_were_read():
    """The vendors move installers; the module says which page each line
    came from and when it was read, so the next reader can check."""
    src = _read(ROOT / "src" / "iron_jarvis" / "terminals" / "ai_clis.py")
    assert "https://code.claude.com/docs/en/setup" in src
    assert "https://github.com/openai/codex" in src
    assert "read 2026-11-07" in src


def test_the_install_jobs_stay_off_the_launch_menu_and_the_cli_list():
    """MUTATION: append a job to AI_CLIS -> red (the Launch menu would
    offer "Install Claude Code" beside Claude Code itself)."""
    ids = {c["id"] for c in ai_clis.AI_CLIS}
    assert "claude-code-install" not in ids and "codex-install" not in ids
    assert {r["id"] for r in ai_clis.detect_ai_clis(probe=False)} == ids


# --------------------------------------------------------------------------- #
# launch_line: the shell decides the form
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("shell", ["pwsh", "powershell", "PowerShell", None, "", "bash"])
def test_every_shell_but_cmd_gets_the_powershell_line(monkeypatch, shell):
    monkeypatch.setattr(ai_clis.shutil, "which", lambda exe: None)
    assert ai_clis.launch_line(ai_clis.launch_entry("claude-code-install"), shell) == CLAUDE_PS
    assert ai_clis.launch_line(ai_clis.launch_entry("codex-install"), shell) == CODEX_PS


@pytest.mark.parametrize("shell", ["cmd", "CMD", " cmd "])
def test_a_command_prompt_pane_gets_the_cmd_form(monkeypatch, shell):
    """``irm`` does not exist in cmd.exe; the vendors publish a CMD line.
    MUTATION: drop the command_cmd branch of launch_line -> red."""
    monkeypatch.setattr(ai_clis.shutil, "which", lambda exe: f"/usr/bin/{exe}")
    assert ai_clis.launch_line(ai_clis.launch_entry("claude-code-install"), shell) == CLAUDE_CMD
    assert ai_clis.launch_line(ai_clis.launch_entry("codex-install"), shell) == CODEX_CMD


def test_the_starter_and_every_launch_menu_line_are_unchanged(monkeypatch):
    """The cmd branch is for jobs that CARRY a cmd form only."""
    monkeypatch.setattr(ai_clis.shutil, "which", lambda exe: f"/usr/bin/{exe}")
    assert ai_clis.launch_line(ai_clis.launch_entry("ollama-starter"), "cmd") == "ollama pull llama3.2"
    for entry in ai_clis.AI_CLIS:
        assert ai_clis.launch_line(entry, "cmd") == entry["command"]


# --------------------------------------------------------------------------- #
# through the real route
# --------------------------------------------------------------------------- #
class _RecordingBackend(FakeBackend):
    """Keeps everything typed into each shell (the v1302 harness shape)."""

    instances: list["_RecordingBackend"] = []
    lock = threading.Lock()

    def __init__(self) -> None:
        super().__init__()
        self.env: dict | None = None
        self.written: list[str] = []
        with _RecordingBackend.lock:
            _RecordingBackend.instances.append(self)

    def start(self, argv, cwd, env, cols, rows) -> None:  # type: ignore[override]
        self.env = dict(env) if env is not None else None
        super().start(argv, cwd, env, cols, rows)

    def write(self, data) -> None:  # type: ignore[override]
        self.written.append(data if isinstance(data, str) else data.decode())
        super().write(data)


def _backend_of(pane_id: str) -> _RecordingBackend:
    found = [
        b for b in _RecordingBackend.instances
        if b.env is not None and b.env.get("IRONJARVIS_PANE_ID") == pane_id
    ]
    assert found, f"no shell was started for pane {pane_id}"
    return found[-1]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(tmp_path / "no-bundle.mjs"))
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_NODE", raising=False)
    _RecordingBackend.instances = []
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", _RecordingBackend)

    async def _no_watch(*_a, **_k):
        await asyncio.Event().wait()

    monkeypatch.setattr("iron_jarvis.iron_proxy.service.watch", _no_watch)
    with TestClient(create_app(str(tmp_path / "home"))) as c:
        yield c


@pytest.mark.parametrize(
    "cli, name, line",
    [("claude-code-install", "Install Claude Code", CLAUDE_PS), ("codex-install", "Install Codex", CODEX_PS)],
)
def test_install_for_me_opens_a_pane_running_the_vendors_installer(client, monkeypatch, cli, name, line):
    """The acceptance line, through the real route: one press, a NEW pane
    named for the install, the installer typed AND started (Enter), and the
    pane a plain shell — never silently, the terminal shows it."""
    monkeypatch.setattr(ai_clis.shutil, "which", lambda exe: None)
    r = client.post("/terminals/launch", json={"cli": cli})
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["name"] == name
    assert not row.get("agent_cli")
    assert _backend_of(row["id"]).written == [line + "\r"]


def test_an_install_job_never_runs_on_an_iron_proxy_account(client):
    """The door's press sends only the job id (no `account`); a caller that
    adds one is refused in words, as for every set-up job."""
    r = client.post("/terminals/launch", json={"cli": "claude-code-install", "account": "default"})
    assert r.status_code == 400
    assert "does not run on an Iron-Proxy account" in r.json()["detail"]


def test_the_launch_menu_route_never_offers_the_installs(client):
    clis = {c["id"] for c in client.get("/terminals/ai-clis").json()["clis"]}
    assert "claude-code-install" not in clis and "codex-install" not in clis
    assert "claude" in clis and "codex" in clis


# --------------------------------------------------------------------------- #
# the dashboard's twin
# --------------------------------------------------------------------------- #
def test_the_dashboard_asks_for_the_jobs_by_name_and_sends_no_command():
    """lib/cliInstall.ts names the daemon's ids and holds no installer line.
    MUTATION: rename a job on either side -> red."""
    lib = _read(DASH / "lib" / "cliInstall.ts")
    for cli, job in ai_clis.INSTALL_JOBS.items():
        assert f'{cli}: "{job}"' in lib, (cli, job)
    for forbidden in ("irm ", "install.ps1", "install.cmd", "npm install", "iex"):
        assert forbidden not in lib, f"the dashboard must never carry {forbidden!r}"
    doors = _read(DASH / "components" / "onboarding" / "ConnectDoors.tsx")
    assert "useCliInstall" in doors and "INSTALLED_CHECK_LABEL" in doors
    # The installer LINES never appear in the door (its history comment may
    # still name the old "irm | iex" refusal by word).
    for forbidden in ("install.ps1", "install.cmd", "npm install"):
        assert forbidden not in doors
