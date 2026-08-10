"""The REPL's filesystem confinement (v1.160.0).

WHAT THIS CLOSES. Every file tool in this app routes through
``core/fs_policy``; the REPL child routed through nothing, and the gap was
measured, not theorised — on a fresh install, ``read_file`` refused the app's
own Fernet key with "escapes the session workspace" while a REPL cell printed
its contents, and a cell writing to an absolute path outside the workspace
succeeded while the tool reported ``created_paths: []``. The second one is the
worse of the two: ``ReplTool._created`` only ever diffs INSIDE the workspace,
so an escaping write is not merely untidy, it is INVISIBLE — which contradicts
this repo's standing rule that a tool writing a file says where, absolutely.

THE POLICY IS ASYMMETRIC ON PURPOSE, and the asymmetry is the design:

* READS STAY BROAD. The documents this app exists to work on (tax returns,
  K-1s, spreadsheets) live all over the user's disk, not inside a project
  folder. `test_reading_a_file_outside_the_folder_still_works` is therefore as
  load-bearing as any blocking test here — a change that tightens reads breaks
  the product, and this file is where that gets caught.
* WRITES PIN to the session workspace (the grounded project's folder when
  there is one) plus a private scratch dir.
* PROTECTED ROOTS are refused in BOTH directions, matching ``fs_read_ok``.

WHY SUBPROCESS AND ctypes ARE REFUSED. Neither is new policy; each is a direct
way around the rule above. A child process inherits the OS's permissions and
none of this hook, and FFI reaches the filesystem without raising a single
audit event. Refusing them is what keeps the write rule from being advisory.

WHAT THIS IS NOT. A sandbox. The hook runs inside the interpreter it polices;
code written to defeat it has options this file does not pretend to cover. It
stops a careless model, which is the realistic failure mode for a tool that is
already deny-floored and gated behind an explicit approval.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from iron_jarvis.platform import build_platform
from iron_jarvis.repl import worker
from iron_jarvis.repl.session import confinement_env
from iron_jarvis.tools.base import ToolContext


@pytest.fixture()
def platform(tmp_path, monkeypatch):
    # An allowlist left over from the ambient environment would silently change
    # what "reads are unrestricted" means in half these tests.
    monkeypatch.delenv("IRONJARVIS_FS_ALLOWLIST", raising=False)
    return build_platform(str(tmp_path / "home"))


@pytest.fixture()
def workspace(tmp_path):
    ws = tmp_path / "project"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


@pytest.fixture()
def outside(tmp_path):
    """A directory that is genuinely outside every writable root.

    Deliberately NOT ``tempfile.gettempdir()``: an earlier draft of the feature
    allowed the whole system temp root, and this probe passed while proving
    nothing, because pytest's own ``tmp_path`` lives there too.
    """
    d = tmp_path / "elsewhere"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ctx(platform, workspace, session_id="conf"):
    return ToolContext(
        workspace=workspace,
        session_id=session_id,
        agent_run_id="r1",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


def run(platform, body):
    """One event loop per test; dispose the children on that same loop."""

    async def wrapper():
        try:
            return await body()
        finally:
            if platform.repl is not None:
                await platform.repl.dispose_all()

    return asyncio.run(wrapper())


def cell(platform, ctx, code, timeout=30.0):
    return platform.registry.invoke(
        "repl", {"code": code, "timeout_s": timeout}, ctx, platform.permissions,
        session_allow=["repl"],
    )


# --------------------------------------------------------------------------- #
# (1) READS STAY BROAD — the half that must not regress.
# --------------------------------------------------------------------------- #
def test_reading_a_file_outside_the_folder_still_works(platform, workspace, outside):
    """The user's K-1s are not in the project folder. If this ever fails, the
    feature has been tightened into uselessness and the fix is to loosen it."""
    doc = outside / "client-k1.txt"
    doc.write_text("Ordinary business income 12345")

    async def body():
        res = await cell(
            platform, _ctx(platform, workspace),
            f"print(open({str(doc)!r}).read())",
        )
        assert res.ok, res.error
        assert "12345" in res.output

    run(platform, body)


def test_listing_a_directory_outside_the_folder_still_works(
    platform, workspace, outside
):
    (outside / "a.txt").write_text("x")

    async def body():
        res = await cell(
            platform, _ctx(platform, workspace),
            f"import os; print(sorted(os.listdir({str(outside)!r})))",
        )
        assert res.ok, res.error
        assert "a.txt" in res.output

    run(platform, body)


# --------------------------------------------------------------------------- #
# (2) WRITES PIN TO THE FOLDER.
# --------------------------------------------------------------------------- #
def test_writing_inside_the_folder_works_and_is_reported(platform, workspace):
    async def body():
        res = await cell(
            platform, _ctx(platform, workspace),
            "open('report.txt', 'w').write('done')",
        )
        assert res.ok, res.error
        assert (workspace / "report.txt").read_text() == "done"
        created = res.data.get("created") or []
        assert any(c.endswith("report.txt") for c in created), created

    run(platform, body)


def test_writing_outside_the_folder_is_blocked_and_nothing_lands(
    platform, workspace, outside
):
    """The headline. Before this existed the write SUCCEEDED and the receipt
    said nothing, so the user had a file somewhere they were never told about."""
    target = outside / "escaped.txt"

    async def body():
        res = await cell(
            platform, _ctx(platform, workspace),
            f"open({str(target)!r}, 'w').write('x')",
        )
        assert res.ok is False
        assert "PermissionError" in (res.error or "")
        assert not target.exists(), "the write was reported as blocked but happened"

    run(platform, body)


def test_the_refusal_names_the_path_and_the_allowed_folder(
    platform, workspace, outside
):
    """A model that cannot see WHERE it is allowed to write will retry the same
    path. The error is the only channel that can redirect it."""
    target = outside / "nope.txt"

    async def body():
        res = await cell(
            platform, _ctx(platform, workspace),
            f"open({str(target)!r}, 'w').write('x')",
        )
        message = res.error or ""
        assert "nope.txt" in message, "the refusal does not say what was refused"
        assert str(workspace) in message, "the refusal does not say where it MAY write"

    run(platform, body)


@pytest.mark.parametrize(
    "template",
    [
        # Each is a different audit event, and each was a real hole until the
        # event was added to _WRITE_EVENTS — `open` alone covers none of them.
        "import os; os.mkdir({t!r})",
        "import os; os.rename({src!r}, {t!r})",
        "import shutil; shutil.copyfile({src!r}, {t!r})",
        "import shutil; shutil.move({src!r}, {t!r})",
        "open({t!r}, 'a').write('x')",
        "open({t!r}, 'xb').write(b'x')",
        "import os; os.symlink({src!r}, {t!r})",
    ],
)
def test_every_route_out_of_the_folder_is_blocked(
    platform, workspace, outside, template
):
    source = workspace / "seed.txt"
    source.write_text("seed")
    target = outside / "leaked"

    async def body():
        res = await cell(
            platform, _ctx(platform, workspace),
            template.format(t=str(target), src=str(source)),
        )
        # A symlink needs privileges on Windows and may fail for that reason
        # instead; either way nothing may appear outside the folder.
        assert res.ok is False, f"{template} escaped the folder"
        assert not target.exists(), f"{template} created something outside"

    run(platform, body)


def test_deleting_a_file_outside_the_folder_is_blocked(
    platform, workspace, outside
):
    """Confinement that only covers creation still lets a cell destroy the
    user's files, which is the more expensive direction of the same mistake."""
    victim = outside / "important.txt"
    victim.write_text("client data")

    async def body():
        res = await cell(
            platform, _ctx(platform, workspace),
            f"import os; os.remove({str(victim)!r})",
        )
        assert res.ok is False
        assert victim.exists(), "a file outside the folder was deleted"

    run(platform, body)


# --------------------------------------------------------------------------- #
# (3) PROTECTED ROOTS — parity with every other tool.
# --------------------------------------------------------------------------- #
def test_the_secrets_key_is_unreadable_exactly_like_read_file(platform, workspace):
    """This is the measured defect that started the change: `read_file` refused
    this path and the REPL printed the key."""
    secrets = Path(platform.config.home) / "secrets"
    secrets.mkdir(parents=True, exist_ok=True)
    key = secrets / ".secrets.key"
    key.write_text("SUPER-SECRET-FERNET-KEY")

    async def body():
        ctx = _ctx(platform, workspace)
        res = await cell(platform, ctx, f"print(open({str(key)!r}).read())")
        assert res.ok is False
        assert "SUPER-SECRET-FERNET-KEY" not in (res.output or "")
        assert "SUPER-SECRET-FERNET-KEY" not in (res.error or "")
        assert "protected" in (res.error or "").lower()

        sanctioned = await platform.registry.invoke(
            "read_file", {"path": str(key)}, ctx, platform.permissions,
            session_allow=["read_file"],
        )
        assert sanctioned.ok is False, "the sanctioned tool stopped refusing"

    run(platform, body)


def test_any_file_inside_a_protected_root_is_refused_not_just_the_key(
    platform, workspace
):
    """Isolates the ROOT layer from the NAME layer.

    Two independent checks refuse `.secrets.key`: containment in a protected
    root, and the filename backstop. Every test that used that one filename
    therefore stayed green with either check deleted — the mutation sweep is
    what surfaced it. This file has an ordinary name, so only containment can
    save it.
    """
    secrets = Path(platform.config.home) / "secrets"
    secrets.mkdir(parents=True, exist_ok=True)
    (secrets / "notes.txt").write_text("PRIVATE-MATERIAL")

    async def body():
        res = await cell(
            platform, _ctx(platform, workspace),
            f"print(open({str(secrets / 'notes.txt')!r}).read())",
        )
        assert res.ok is False, "a file inside the protected root was readable"
        assert "PRIVATE-MATERIAL" not in (res.output or "")

    run(platform, body)


def test_a_stray_key_file_outside_every_protected_root_is_refused_by_name(
    platform, workspace
):
    """Isolates the NAME layer from the ROOT layer.

    A key file copied or rotated somewhere unregistered (`.secrets.key.bak`
    next to a backup) is in no protected root, so only the filename check can
    refuse it. This is the same belt-and-braces `fs_policy` carries, and it is
    the layer that survives a path spelling `realpath` mangles.
    """
    stray = workspace / ".secrets.key.bak"
    stray.write_text("LEAKED-KEY-MATERIAL")

    async def body():
        res = await cell(
            platform, _ctx(platform, workspace), f"print(open({str(stray)!r}).read())"
        )
        assert res.ok is False, "a stray key file was readable by its own name"
        assert "LEAKED-KEY-MATERIAL" not in (res.output or "")

    run(platform, body)


# --------------------------------------------------------------------------- #
# (4) THE WAYS AROUND THE RULE.
# --------------------------------------------------------------------------- #
def test_a_subprocess_cannot_be_used_to_escape(platform, workspace, outside):
    """A child process inherits none of this hook. It also outlives the kill
    that ends a cell — the one lifecycle hole this worker shipped with."""
    target = outside / "via-shell.txt"
    script = (
        "import subprocess, sys; "
        f"subprocess.run([sys.executable, '-c', "
        f"\"open({str(target)!r}, 'w').write('x')\"])"
    )

    async def body():
        res = await cell(platform, _ctx(platform, workspace), script)
        assert res.ok is False
        assert not target.exists(), "a subprocess wrote outside the folder"
        assert "shell" in (res.error or "").lower(), (
            "the refusal should point at the tool that IS allowed to do this"
        )

    run(platform, body)


def test_ffi_cannot_be_used_to_escape(platform, workspace):
    """`ctypes` reaches the filesystem through the C library, raising no audit
    event, so leaving it open would make every test above advisory.

    The library loaded here MUST be one that would really load, and one the
    worker has not already loaded. An earlier version used `ctypes.CDLL(None)`,
    a Unix idiom that fails on Windows regardless — it passed with the whole FFI
    guard deleted, proving nothing. The mutation sweep is what caught that.

    `user32` rather than `kernel32`: importing ctypes on Windows loads kernel32
    and caches it on the `windll` loader, so asking for it again never reaches
    the audit event. That is the documented residual hole, not a test bug.
    """
    load = (
        'ctypes.WinDLL("user32")' if sys.platform == "win32" else "ctypes.CDLL(None)"
    )

    async def body():
        ctx = _ctx(platform, workspace)
        imported = await cell(platform, ctx, "import ctypes; print('imported')")
        assert imported.ok, "importing ctypes should work; only LOADING is refused"

        res = await cell(platform, ctx, f"import ctypes; {load}")
        assert res.ok is False, "a native library loaded despite the guard"
        message = (res.error or "").lower()
        assert "ctypes" in message or "native" in message, res.error

    run(platform, body)


def test_unsetting_the_environment_does_not_lift_the_guard(platform, workspace, outside):
    """The roots are read ONCE at install time into a closure. A cell that
    clears the variables is changing a value nothing reads again."""
    target = outside / "after-unset.txt"

    async def body():
        ctx = _ctx(platform, workspace)
        wiped = await cell(
            platform, ctx,
            "import os; "
            f"[os.environ.pop(k, None) for k in {list(_ENV_NAMES)!r}]; "
            "print('cleared')",
        )
        assert wiped.ok, wiped.error
        res = await cell(platform, ctx, f"open({str(target)!r}, 'w').write('x')")
        assert res.ok is False
        assert not target.exists()

    run(platform, body)


_ENV_NAMES = (worker.ENV_WRITE_ROOTS, worker.ENV_READ_ROOTS, worker.ENV_DENY_ROOTS)


# --------------------------------------------------------------------------- #
# (5) IT MUST NOT BREAK ORDINARY WORK.
# --------------------------------------------------------------------------- #
def test_tempfile_works_and_lands_in_the_private_scratch_dir(platform, workspace):
    """Libraries write through `tempfile` constantly, so it has to work — but
    WHERE it works is the point, and asserting only that it succeeded proved
    nothing.

    `tempfile` PROBES its candidate directories by trying to create a file in
    each. With the redirect removed the system temp candidate is refused by the
    hook, tempfile silently falls through its list, and lands on `os.getcwd()`
    — the user's project folder, quietly filling up with `tmpXXXX` files. That
    is the exact littering this feature exists to stop, and it is invisible
    unless the test checks the path. Measured, not reasoned about.
    """

    async def body():
        res = await cell(
            platform, _ctx(platform, workspace),
            "import tempfile, os\n"
            "with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as f:\n"
            "    f.write('scratch'); p = f.name\n"
            "print(open(p).read()); print('LANDED:', p)",
        )
        assert res.ok, res.error
        assert "scratch" in res.output
        # A MARKER, not `splitlines()[-1]`: the tool appends its own "Files
        # created in the workspace" block, so the last line is "- <path>" and
        # `Path("- C:\\...")` compares unequal to everything. That parse made
        # this test pass with the redirect deleted.
        marked = [
            ln for ln in res.output.splitlines() if ln.startswith("LANDED:")
        ]
        assert marked, res.output
        landed = Path(marked[0].split("LANDED:", 1)[1].strip())
        assert workspace not in landed.parents, (
            f"a temp file landed in the user's project folder: {landed}"
        )
        # Same fact from the other side: anything landing in the workspace is
        # reported as created, so a temp file must never appear in that list.
        created = res.data.get("created") or []
        assert not [c for c in created if Path(c).name.startswith("tmp")], created

    run(platform, body)


def test_importing_a_module_for_the_first_time_still_works(
    platform, workspace, outside
):
    """Python writes a .pyc NEXT TO the source on first import — for a library
    that means inside site-packages, i.e. outside the folder. Without
    PYTHONDONTWRITEBYTECODE the guard turns `import x` into a PermissionError
    about a path the model never named, which is close to undebuggable.

    The module imported here is written FRESH into a directory outside the
    writable roots, because that is the only way to reach the .pyc path at all.
    Importing stdlib modules instead proved nothing: their bytecode is already
    cached, so the test passed with the fix removed.
    """
    module = outside / "freshly_written_module.py"
    module.write_text("VALUE = 'imported fine'\n")

    async def body():
        res = await cell(
            platform, _ctx(platform, workspace),
            f"import sys; sys.path.insert(0, {str(outside)!r})\n"
            "import freshly_written_module as m\n"
            "print(m.VALUE)",
        )
        assert res.ok, res.error
        assert "imported fine" in res.output

    run(platform, body)


def test_importing_does_not_litter_the_project_folder_with_pycache(
    platform, workspace
):
    """Why PYTHONDONTWRITEBYTECODE is set.

    NOT because imports would otherwise fail — `importlib` catches the
    `PermissionError` from a refused `.pyc` write and carries on, which is why
    removing the flag left the import test green. The real cost is here: the
    workspace IS writable, so importing anything from the user's project folder
    drops `__pycache__` directories into it. The user asked for their work not
    to be scattered; the app's own machinery scattering build artifacts through
    their project is the same complaint from the other side.
    """
    (workspace / "local_helper.py").write_text("HELPER = 1\n")

    async def body():
        res = await cell(
            platform, _ctx(platform, workspace),
            "import sys; sys.path.insert(0, '.')\n"
            "import local_helper; print('used', local_helper.HELPER)",
        )
        assert res.ok, res.error
        assert "used 1" in res.output
        assert not (workspace / "__pycache__").exists(), (
            "importing dropped a __pycache__ into the user's project folder"
        )

    run(platform, body)


def test_the_stdlib_modules_a_cell_actually_uses_still_import(platform, workspace):
    """`uuid` and `platform` reach for ctypes on some paths, and the FFI refusal
    must not make ordinary stdlib unusable."""

    async def body():
        res = await cell(
            platform, _ctx(platform, workspace),
            "import csv, json, sqlite3, zipfile, base64, statistics, uuid, platform\n"
            "print('ok', statistics.mean([1, 2, 3]), len(str(uuid.uuid4())))",
        )
        assert res.ok, res.error
        assert "ok 2 36" in res.output

    run(platform, body)


def test_a_writable_scratch_dir_is_removed_when_the_session_dies(
    platform, workspace
):
    async def body():
        ctx = _ctx(platform, workspace)
        res = await cell(platform, ctx, "import tempfile; print(tempfile.gettempdir())")
        assert res.ok, res.error
        scratch = Path(res.output.strip().splitlines()[-1])
        assert scratch.is_dir()
        await platform.repl.dispose(ctx.session_id)
        assert not scratch.exists(), "the child's scratch dir outlived the child"

    run(platform, body)


def test_reading_many_files_is_not_pathologically_slow(platform, workspace):
    """An audit hook runs on EVERY audited operation, and this one canonicalises
    paths. Bounded here so a future 'just resolve it twice' cannot quietly turn
    a data-processing cell into a timeout."""
    for i in range(200):
        (workspace / f"f{i}.txt").write_text("x" * 64)

    async def body():
        res = await cell(
            platform, _ctx(platform, workspace),
            "import glob, time\n"
            "t = time.monotonic()\n"
            "n = sum(len(open(p).read()) for p in glob.glob('f*.txt'))\n"
            "print(n, round(time.monotonic() - t, 3))",
        )
        assert res.ok, res.error
        total, elapsed = res.output.strip().split()
        assert int(total) == 200 * 64
        assert float(elapsed) < 5.0, f"200 reads took {elapsed}s under the hook"

    run(platform, body)


# --------------------------------------------------------------------------- #
# (5b) SWITCHING PROJECTS MID-CHAT.
# --------------------------------------------------------------------------- #
def test_switching_folders_moves_the_write_root_with_the_user(
    platform, tmp_path
):
    """Chat runs EVERY turn under the literal session id "chat" while its tool
    workspace follows the grounded project, so keying namespaces on the session
    id alone pinned the folder to whichever project happened to be open first.

    Before confinement that was merely untidy — a relative write landed in the
    old project. With the workspace doubling as the write root it becomes a
    refusal naming a folder the user has already navigated away from, which
    reads as "the tool is broken" rather than "you moved".
    """
    first = tmp_path / "project-a"
    second = tmp_path / "project-b"
    first.mkdir()
    second.mkdir()

    async def body():
        a = await cell(platform, _ctx(platform, first, "chat"),
                       "open('a.txt', 'w').write('from A')")
        assert a.ok, a.error
        b = await cell(platform, _ctx(platform, second, "chat"),
                       "open('b.txt', 'w').write('from B')")
        assert b.ok, f"the second project's folder was refused: {b.error}"
        assert (first / "a.txt").read_text() == "from A"
        assert (second / "b.txt").read_text() == "from B", (
            "the write followed the old project instead of the current one"
        )

    run(platform, body)


def test_two_projects_do_not_share_a_namespace(platform, tmp_path):
    """The other half of the same key: variables built up while working on one
    client's files must not be visible while working on another's."""
    first = tmp_path / "client-a"
    second = tmp_path / "client-b"
    first.mkdir()
    second.mkdir()

    async def body():
        await cell(platform, _ctx(platform, first, "chat"), "ssn = '123-45-6789'")
        res = await cell(platform, _ctx(platform, second, "chat"),
                         "print('ssn' in dir())")
        assert res.ok, res.error
        assert "False" in res.output, "one project could see another's variables"

    run(platform, body)


def test_disposing_a_session_kills_every_folder_it_worked_in(platform, tmp_path):
    """A namespace left behind at session end is an interpreter nobody can
    reach — the same leak the `_closed` guard prevents, by a different road."""
    first = tmp_path / "p1"
    second = tmp_path / "p2"
    first.mkdir()
    second.mkdir()

    async def body():
        await cell(platform, _ctx(platform, first, "chat"), "x = 1")
        await cell(platform, _ctx(platform, second, "chat"), "y = 2")
        assert len(platform.repl) == 2, platform.repl.describe()
        assert await platform.repl.dispose("chat") is True
        assert len(platform.repl) == 0, platform.repl.describe()

    run(platform, body)


# --------------------------------------------------------------------------- #
# (6) THE WIRING, AND THE DELIBERATE OFF SWITCH.
# --------------------------------------------------------------------------- #
def test_no_roots_configured_means_no_restriction(monkeypatch):
    """A worker started by hand (`python -m iron_jarvis.repl.worker`) has no
    policy to enforce. Inventing a default would give it a boundary nobody
    chose and nobody could predict."""
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    assert worker.install_confinement() is False


def test_the_parent_hands_over_the_workspace_and_the_protected_roots(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("IRONJARVIS_FS_ALLOWLIST", raising=False)
    from iron_jarvis.core import fs_policy

    secret = tmp_path / "secrets"
    secret.mkdir()
    fs_policy.register_protected_root(secret)

    ws = tmp_path / "ws"
    ws.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    env = confinement_env(ws, scratch)

    assert str(ws.resolve()) in env[worker.ENV_WRITE_ROOTS]
    assert str(scratch) in env[worker.ENV_WRITE_ROOTS]
    assert os.path.normcase(str(secret.resolve())) in os.path.normcase(
        env[worker.ENV_DENY_ROOTS]
    )
    # No allowlist set → reads unrestricted, so the variable is absent entirely
    # rather than present-and-empty (which would read as "allow nothing").
    assert worker.ENV_READ_ROOTS not in env
    assert env["TMPDIR"] == str(scratch)


def test_the_allowlist_is_carried_into_the_child(tmp_path, monkeypatch):
    """`IRONJARVIS_FS_ALLOWLIST` is the multi-user deployment switch. If the
    child ignored it, an agent could read past it via the REPL — the same class
    of bypass `fs_policy` exists to prevent."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("IRONJARVIS_FS_ALLOWLIST", str(allowed))
    env = confinement_env(tmp_path / "ws")
    assert os.path.normcase(str(allowed.resolve())) in os.path.normcase(
        env[worker.ENV_READ_ROOTS]
    )


def test_the_worker_is_still_stdlib_only():
    """The confinement code lives INSIDE worker.py rather than in a sibling
    module for one reason: this must keep holding. The worker is spawned from a
    frozen binary and importing the app here would be circular and heavy."""
    import ast

    tree = ast.parse(Path(worker.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # a relative import IS an iron_jarvis import
                imported.add("iron_jarvis")
            elif node.module:
                imported.add(node.module.split(".")[0])
    assert "iron_jarvis" not in imported, sorted(imported)
    assert imported <= set(sys.stdlib_module_names), sorted(
        imported - set(sys.stdlib_module_names)
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path spellings")
def test_a_windows_device_path_cannot_reach_a_protected_root(platform, workspace):
    r"""The READ direction is where the device prefix actually bites.

    `\\?\C:\x` opens identically to `C:\x`, but `os.path.realpath` returns
    `C:x` for it — drive-RELATIVE and mangled. For a write that is harmless:
    the mangled path matches no writable root, so it is refused anyway
    (fail-closed). For a DENY root it is the opposite — a mangled path matches
    no protected root either, and the refusal silently stops applying. So
    `_canonical` strips the prefix before resolving, and the name check runs on
    the raw spelling. Mutation-proven via both layers.
    """
    secrets = Path(platform.config.home) / "secrets"
    secrets.mkdir(parents=True, exist_ok=True)
    # An ordinary NAME, so the filename backstop cannot mask a broken root
    # check — this test is about `_canonical`, and nothing else may save it.
    victim = secrets / "notes.txt"
    victim.write_text("PRIVATE-MATERIAL")
    device = "\\\\?\\" + str(victim)

    async def body():
        res = await cell(
            platform, _ctx(platform, workspace), f"print(open({device!r}).read())"
        )
        assert res.ok is False, "a device path read straight past the deny root"
        assert "PRIVATE-MATERIAL" not in (res.output or "")
        assert "PRIVATE-MATERIAL" not in (res.error or "")

    run(platform, body)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path spellings")
def test_a_windows_device_path_cannot_sidestep_the_write_check(
    platform, workspace, outside
):
    target = outside / "device.txt"

    async def body():
        res = await cell(
            platform, _ctx(platform, workspace),
            f"open({('\\\\\\\\?\\\\' + str(target))!r}, 'w').write('x')",
        )
        assert res.ok is False
        assert not target.exists()

    run(platform, body)
