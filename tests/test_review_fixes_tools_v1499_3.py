"""v1.499.3 review fixes: tools, grants, auth, protected files, undo, secrets.

1. ``safe_path`` refuses a path on ANOTHER COMPUTER by its text, before
   anything is resolved (resolving opens it, and opening a UNC path makes
   Windows connect to that host and offer it this PC's sign-in).
2. An exact standing grant is keyed on a script's kept value too (its name
   and fingerprint), so it never covers different data written to one path.
3. The remote inbound door is exactly ``POST /agents/remote/<name>/inbound``.
4. The install token file and ``<home>/backups`` are protected from file tools.
5. Undo of a file a tool created checks the file is unchanged first, and moves
   a file it cannot prove unchanged to the trash instead of deleting it.
6. A secret typed into a preference, a memory entry or a memory-base note is
   kept masked, and the tool result says it was left out.

No network: the UNC tests spy on the functions that would open the path.
No value here is shaped like a real credential: fake keys are concatenated.
"""

from __future__ import annotations

import iron_jarvis.workflows.models  # noqa: F401  (register tables before init_db)

import json
import ntpath
import os
from pathlib import Path

import pytest

from iron_jarvis.tools.base import ToolContext

FAKE_KEY = "sk-" + "test-" + "x" * 30
HOST = "attacker.invalid"
BS = "\\"


# --------------------------------------------------------------------------- #
# 1. safe_path: another computer is refused before anything is opened
# --------------------------------------------------------------------------- #

def _spy_network(monkeypatch) -> list[str]:
    """Record (and refuse) every attempt to open a path naming HOST."""
    calls: list[str] = []

    def wrap(fn, label):
        def spy(p, *a, **k):
            if HOST in str(p).lower():
                calls.append(f"{label} {p}")
                raise OSError(53, "spy: network path not opened")
            return fn(p, *a, **k)

        return spy

    for name in ("_getfinalpathname", "_getfinalpathname_nonstrict"):
        real = getattr(ntpath, name, None)
        if real is not None:
            monkeypatch.setattr(ntpath, name, wrap(real, name))
    monkeypatch.setattr(os, "lstat", wrap(os.lstat, "lstat"))
    monkeypatch.setattr(os, "stat", wrap(os.stat, "stat"))
    if hasattr(os, "readlink"):
        monkeypatch.setattr(os, "readlink", wrap(os.readlink, "readlink"))
    return calls


NETWORK_SPELLINGS = [
    BS * 2 + HOST + BS + "share" + BS + "invoice.pdf",
    "//" + HOST + "/share/invoice.pdf",
    BS * 2 + "?" + BS + "UNC" + BS + HOST + BS + "share" + BS + "x.txt",
    "//?/UNC/" + HOST + "/share/x.txt",
    BS * 2 + "." + BS + "UNC" + BS + HOST + BS + "share" + BS + "x.txt",
    BS + "??" + BS + "UNC" + BS + HOST + BS + "share" + BS + "x.txt",
    BS * 2 + HOST + BS + "share",
]


@pytest.mark.skipif(os.name != "nt", reason="UNC paths are a Windows spelling")
@pytest.mark.parametrize("spelled", NETWORK_SPELLINGS)
def test_safe_path_refuses_a_network_path_before_opening_it(tmp_path, monkeypatch, spelled):
    from iron_jarvis.tools.base import safe_path

    ws = tmp_path / "ws"
    ws.mkdir()
    calls = _spy_network(monkeypatch)
    with pytest.raises(PermissionError) as info:
        safe_path(ws, spelled)
    assert calls == [], f"the network path was opened before the refusal: {calls}"
    assert "another computer" in str(info.value)


@pytest.mark.skipif(os.name != "nt", reason="UNC paths are a Windows spelling")
def test_safe_path_keeps_local_paths_working(tmp_path):
    from iron_jarvis.tools.base import safe_path

    ws = tmp_path / "ws"
    (ws / "sub").mkdir(parents=True)
    assert safe_path(ws, "sub/a.txt") == (ws / "sub" / "a.txt").resolve()
    # An absolute path inside the workspace is still the workspace.
    assert safe_path(ws, str(ws / "sub" / "a.txt")) == (ws / "sub" / "a.txt").resolve()
    with pytest.raises(PermissionError, match="escapes the session workspace"):
        safe_path(ws, str(tmp_path / "outside.txt"))


def test_network_spelling_reads_text_only():
    from iron_jarvis.tools.base import _network_spelling

    for s in NETWORK_SPELLINGS:
        assert _network_spelling(s), s
    for local in ("C:\\x\\y.txt", "\\\\?\\C:\\x", "\\\\.\\C:\\x", "\\??\\C:\\x", "a/b.txt", "\\rooted"):
        assert not _network_spelling(local), local


def test_a_workspace_on_a_share_keeps_its_own_paths():
    """A firm whose folders live on a share works there: a path INSIDE that
    workspace is not refused, a path beside it is (judged as text)."""
    from iron_jarvis.tools.base import _inside_share_workspace

    ws = Path(BS * 2 + "fileserver" + BS + "clients" + BS + "acme")
    inside = BS * 2 + "FileServer" + BS + "clients" + BS + "acme" + BS + "q3.xlsx"
    beside = BS * 2 + "fileserver" + BS + "clients" + BS + "other" + BS + "q3.xlsx"
    climbs = BS * 2 + "fileserver" + BS + "clients" + BS + "acme" + BS + ".." + BS + "other"
    assert _inside_share_workspace(inside, ws, ws)
    assert not _inside_share_workspace(beside, ws, ws)
    assert not _inside_share_workspace(climbs, ws, ws)
    assert not _inside_share_workspace(BS * 2 + HOST + BS + "share", ws, ws)
    local = Path("C:/work")
    assert not _inside_share_workspace(BS * 2 + HOST + BS + "share", local, local)


# --------------------------------------------------------------------------- #
# 2. grants: a kept value is part of the call's identity
# --------------------------------------------------------------------------- #

def _record(name: str, sha: str, what: str = "40 rows") -> dict:
    return {"path": "Q3.xlsx", "_from_script": {"content": {"name": name, "sha": sha, "what": what}}}


def test_args_hash_includes_the_kept_value_identity():
    from iron_jarvis.core.grants import FROM_SCRIPT_ARG, args_hash
    from iron_jarvis.tools.script_values import ARG

    assert FROM_SCRIPT_ARG == ARG
    a = args_hash("write_document", _record("rows", "a" * 64))
    b = args_hash("write_document", _record("rows", "b" * 64))
    other_name = args_hash("write_document", _record("other", "a" * 64))
    bare = args_hash("write_document", {"path": "Q3.xlsx"})
    assert a != b, "different data under one name must be a different call"
    assert a != other_name
    assert a != bare
    # The summary is wording, not identity.
    assert a == args_hash("write_document", _record("rows", "a" * 64, what="forty rows"))
    # The model's own spelling (names only) differs by name too.
    raw1 = {"path": "f.pdf", "_from_script": {"values": "v1"}}
    raw2 = {"path": "f.pdf", "_from_script": {"values": "v2"}}
    assert args_hash("pdf_form_fill", raw1) != args_hash("pdf_form_fill", raw2)


def test_lane_control_keys_stay_out_of_the_hash():
    from iron_jarvis.core.grants import args_hash

    plain = args_hash("write_file", {"path": "a.txt", "content": "x"})
    assert plain == args_hash("write_file", {"path": "a.txt", "content": "x", "_store_as": "k"})
    assert plain == args_hash("write_file", {"path": "a.txt", "content": "x", "_isolate": True})


def test_an_exact_grant_covers_only_the_value_it_was_minted_for(tmp_path):
    from iron_jarvis.core.db import init_db, make_engine
    from iron_jarvis.core.grants import GrantStore, args_hash

    engine = make_engine(tmp_path / "g.db")
    init_db(engine)
    store = GrantStore(engine)
    scopes = [("chat", "thread-1")]
    seen = _record("rows", "a" * 64)
    store.create("chat", "thread-1", "write_document", args_hash("write_document", seen), "w")
    # The registry checks the FILLED record: the same value is covered...
    assert store.match(scopes, "write_document", seen, touch=False) is not None
    # ...a different value kept under the same name, written to the same path, is not.
    assert store.match(scopes, "write_document", _record("rows", "b" * 64), touch=False) is None
    # The card's peek at the model's own spelling (no fingerprint yet) never
    # claims an exact grant: the registry's check on the record decides.
    raw = {"path": "Q3.xlsx", "_from_script": {"content": "rows"}}
    assert store.match(scopes, "write_document", raw, touch=False) is None
    # Even a grant minted on that spelling: it names no data, so it covers none.
    store.create("chat", "thread-1", "write_document", args_hash("write_document", raw), "w")
    assert store.match(scopes, "write_document", raw, touch=False) is None
    # A grant for the bare path does not cover a call that carries kept data.
    store.create("chat", "thread-1", "write_document", args_hash("write_document", {"path": "Q3.xlsx"}), "w")
    assert store.match(scopes, "write_document", _record("rows", "c" * 64), touch=False) is None


# --------------------------------------------------------------------------- #
# 3. auth: the remote inbound door is exactly POST /agents/remote/<name>/inbound
# --------------------------------------------------------------------------- #

def _request(method: str, path: str):
    from starlette.requests import Request

    return Request({
        "type": "http", "method": method, "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": [], "client": ("127.0.0.1", 5000),
        "server": ("127.0.0.1", 8787), "scheme": "http",
    })


def test_remote_inbound_exemption_is_exact():
    from iron_jarvis.daemon.auth import _exempt_for, _is_exempt

    assert _is_exempt("/agents/remote/desk/inbound")
    assert _is_exempt("/agents/remote/inbound/inbound")  # the door of a remote named "inbound"
    assert not _is_exempt("/agents/remote/inbound")  # PATCH/DELETE of a remote named "inbound"
    assert not _is_exempt("/agents/remote//inbound")
    assert not _is_exempt("/agents/remote/a/b/inbound")
    assert not _is_exempt("/agents/remote/desk/inbound/enable")
    assert _exempt_for(_request("POST", "/agents/remote/desk/inbound"))
    for method in ("GET", "PATCH", "DELETE", "PUT"):
        assert not _exempt_for(_request(method, "/agents/remote/desk/inbound")), method
    assert not _exempt_for(_request("PATCH", "/agents/remote/inbound"))
    assert not _exempt_for(_request("DELETE", "/agents/remote/inbound"))


# --------------------------------------------------------------------------- #
# 4. protected files: the install token and the backups
# --------------------------------------------------------------------------- #

def test_install_token_and_backups_are_protected(tmp_path):
    from iron_jarvis.core.fs_policy import fs_read_ok, is_protected_path
    from iron_jarvis.platform import build_platform

    platform = build_platform(str(tmp_path))
    home = Path(platform.config.home)
    (home / "backups").mkdir(parents=True, exist_ok=True)
    backup = home / "backups" / "ironjarvis-backup-1.tar.gz"
    backup.write_bytes(b"db")
    token = home.parent / "token.txt"
    token.write_text("f" * 64, encoding="utf-8")
    beside = home.parent / "notes.txt"
    beside.write_text("mine", encoding="utf-8")
    assert is_protected_path(backup)
    assert is_protected_path(token)
    assert fs_read_ok(str(token))[0] is False
    assert not is_protected_path(beside), "only the token file itself is protected"
    appdata = os.environ.get("APPDATA")
    if appdata:
        assert is_protected_path(Path(appdata) / "Iron Jarvis" / "token.txt")


# --------------------------------------------------------------------------- #
# 5. undo of a file a tool created
# --------------------------------------------------------------------------- #

def _platform_ctx(tmp_path):
    from iron_jarvis.platform import build_platform

    platform = build_platform(str(tmp_path))
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    ctx = ToolContext(
        workspace=ws, session_id="s_undo14993", agent_run_id="r_undo14993",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
    )
    return platform, ctx


def _journal(platform):
    from sqlmodel import select

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import UndoJournal

    with session_scope(platform.engine) as db:
        rows = list(db.exec(select(UndoJournal)).all())
    return [
        {"kind": r.kind, "pre_ref": r.pre_ref, "pre_inline": r.pre_inline,
         "pre_sha256": r.pre_sha256, "post_sha256": r.post_sha256, "reversible": r.reversible}
        for r in rows
    ]


async def test_post_hoc_created_file_is_hashed_and_an_edit_since_refuses_undo(tmp_path):
    from iron_jarvis.tools.undo import RevertConflict, revert_workspace_file, sha256_target

    platform, ctx = _platform_ctx(tmp_path)
    (ctx.workspace / "notes.txt").write_text("plain text", encoding="utf-8")
    res = await platform.registry.invoke(
        "convert_document", {"source": "notes.txt", "target": "out/notes.md"},
        ctx, platform.permissions, session_allow={"convert_document"},
    )
    assert res.ok is True, res.error
    made = ctx.workspace / "out" / "notes.md"
    rows = _journal(platform)
    assert len(rows) == 1 and rows[0]["kind"] == "file_delete"
    assert rows[0]["post_sha256"] == sha256_target(made, "raw") is not None
    made.write_text("the user's own edit since", encoding="utf-8")
    with pytest.raises(RevertConflict):
        await revert_workspace_file(rows[0], ctx)
    assert made.read_text(encoding="utf-8") == "the user's own edit since"


async def test_post_hoc_files_delete_checks_every_file_before_removing_any(tmp_path, monkeypatch):
    import iron_jarvis.documents.batch as _batch_mod
    from iron_jarvis.tools.undo import RevertConflict, revert_workspace_file

    platform, ctx = _platform_ctx(tmp_path)
    folder = ctx.workspace / "docs"
    folder.mkdir()
    (folder / "a.txt").write_text("doc", encoding="utf-8")

    async def fake_run_batch(src, out_dir, router, *, instructions, output, max_files, config, on_file=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        outs = []
        for name in ("summary.docx", "summary.xlsx"):
            p = out_dir / name
            p.write_bytes(b"fake " + name.encode())
            outs.append(str(p))
        return {"processed": 1, "cached": 0, "failed": [], "skipped": [], "deliverables": outs,
                "synthesis_errors": [], "qa": {}}

    monkeypatch.setattr(_batch_mod, "run_batch", fake_run_batch)
    res = await platform.registry.invoke(
        "batch_documents", {"folder": "docs"}, ctx, platform.permissions,
        session_allow={"batch_documents"},
    )
    assert res.ok is True, res.error
    rows = _journal(platform)
    assert len(rows) == 1 and rows[0]["kind"] == "files_delete"
    env = json.loads(rows[0]["pre_inline"])
    assert sorted(env["sha256"]) == sorted(env["paths"])
    assert all(env["sha256"].values())
    docx, xlsx = (Path(p) for p in sorted(res.created_paths))
    xlsx.write_bytes(b"edited by the user")
    with pytest.raises(RevertConflict):
        await revert_workspace_file(rows[0], ctx)
    assert docx.exists() and xlsx.read_bytes() == b"edited by the user"
    # Unchanged files are removed as before.
    xlsx.write_bytes(b"fake summary.xlsx")
    ok = await revert_workspace_file(rows[0], ctx)
    assert ok.ok is True, ok.error
    assert not docx.exists() and not xlsx.exists()


async def test_an_unhashed_created_file_goes_to_the_trash_not_deleted(tmp_path):
    """A row from before the hashes proves nothing: the file is MOVED to
    ``<home>/trash`` (recoverable), for one file and for many."""
    from iron_jarvis.tools.undo import revert_workspace_file

    _platform, ctx = _platform_ctx(tmp_path)
    home = Path(ctx.config.home)
    one = ctx.workspace / "made.txt"
    one.write_text("since edited, maybe", encoding="utf-8")
    row = {"kind": "file_delete", "reversible": True, "pre_ref": None,
           "pre_inline": json.dumps({"path": "made.txt", "mode": "raw", "data": None}),
           "pre_sha256": None, "post_sha256": None}
    res = await revert_workspace_file(row, ctx)
    assert res.ok is True, res.error
    assert not one.exists()
    kept = list((home / "trash").rglob("made.txt"))
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == "since edited, maybe"
    assert "trash" in res.output

    a = ctx.workspace / "a.txt"
    b = ctx.workspace / "sub" / "b.txt"
    b.parent.mkdir()
    a.write_text("A", encoding="utf-8")
    b.write_text("B", encoding="utf-8")
    many = {"kind": "files_delete", "reversible": True, "pre_ref": None,
            "pre_inline": json.dumps({"paths": ["a.txt", "sub/b.txt"], "mode": "raw"}),
            "pre_sha256": None, "post_sha256": None}
    res = await revert_workspace_file(many, ctx)
    assert res.ok is True, res.error
    assert not a.exists() and not b.exists()
    assert {p.read_text(encoding="utf-8") for p in (home / "trash").rglob("*.txt") if p.name != "made.txt"} == {"A", "B"}
    assert "trash" in res.output


# --------------------------------------------------------------------------- #
# 6. secrets never reach memory
# --------------------------------------------------------------------------- #

class _Bus:
    def __init__(self):
        self.events = []

    async def publish(self, *a, **k):
        self.events.append((a, k))


def _engine(tmp_path):
    from iron_jarvis.core.db import init_db, make_engine

    engine = make_engine(tmp_path / "t.db")
    init_db(engine)
    return engine


def test_a_preference_never_keeps_a_secret(tmp_path):
    from iron_jarvis.learning.engine import LearningEngine

    le = LearningEngine(_engine(tmp_path))
    row = le.note_preference(f"always use my api key {FAKE_KEY} when calling the API")
    assert FAKE_KEY not in row.text and "sk-***" in row.text
    assert FAKE_KEY not in le.apply_to_prompt("SYSTEM")
    fb = le.record_feedback("s1", "down", f"the password=hunter{'2' * 3} was wrong")
    assert fb is not None
    assert all("hunter" not in lesson.text for lesson in le.lessons(scope="user", limit=50))


async def test_remember_preference_says_the_secret_was_left_out(tmp_path, monkeypatch):
    import iron_jarvis.learning.tools as LT
    from iron_jarvis.learning.engine import LearningEngine

    monkeypatch.setattr(LT, "client_guard", lambda engine, text: ("", ""))
    tool = LT.RememberPreferenceTool(LearningEngine(_engine(tmp_path)))
    ctx = ToolContext(workspace=tmp_path, session_id="s", agent_run_id="r", config=None,
                      event_bus=_Bus(), engine=None)
    res = await tool.execute({"text": f"use {FAKE_KEY} for the API"}, ctx)
    assert res.ok is True
    assert FAKE_KEY not in res.output and FAKE_KEY not in res.data["text"]
    assert LT.SECRET_LEFT_OUT in res.output
    plain = await tool.execute({"text": "prefers short bullet summaries"}, ctx)
    assert LT.SECRET_LEFT_OUT not in plain.output


def test_memory_write_stores_a_secret_masked(tmp_path):
    from iron_jarvis.memory.layers import MemoryLayers

    layers = MemoryLayers(_engine(tmp_path))
    rec = layers.write("user", "api", f"the key is {FAKE_KEY}")
    assert FAKE_KEY not in rec.text
    assert FAKE_KEY not in (layers.read("user", "api") or "")
    layers.write("user", "api", f"Authorization: Bearer {'q' * 24}")  # the update path too
    assert "q" * 24 not in (layers.read("user", "api") or "")


async def test_memory_write_says_the_secret_was_left_out(tmp_path):
    """The tool result says so, like remember_preference and ltm_append."""
    from types import SimpleNamespace

    from iron_jarvis.ltm.tools import SECRET_LEFT_OUT
    from iron_jarvis.memory.layers import MemoryLayers
    from iron_jarvis.memory.tools import MemoryWriteTool

    class _Bus:
        async def publish(self, *a, **kw):
            return None

    tool = MemoryWriteTool(MemoryLayers(_engine(tmp_path)))
    ctx = SimpleNamespace(event_bus=_Bus(), session_id="s1")
    said = await tool.execute({"layer": "user", "key": "api", "text": f"the key is {FAKE_KEY}"}, ctx)
    assert said.ok and SECRET_LEFT_OUT in said.output and FAKE_KEY not in said.output
    plain = await tool.execute({"layer": "user", "key": "tone", "text": "short answers please"}, ctx)
    assert plain.ok and SECRET_LEFT_OUT not in plain.output


async def test_ltm_append_masks_and_its_undo_still_matches(tmp_path):
    from iron_jarvis.ltm.base import MarkdownDirConnector
    from iron_jarvis.ltm.manager import LongTermMemory
    from iron_jarvis.ltm.tools import SECRET_LEFT_OUT, LTMAppendTool
    from iron_jarvis.tools.undo import sha256_target

    class _Cfg:
        home = str(tmp_path / "home")

    manager = LongTermMemory()
    conn = MarkdownDirConnector(tmp_path / "brain", create=True)
    conn.name = "brain"
    manager.register(conn)
    tool = LTMAppendTool(manager)
    ctx = ToolContext(workspace=tmp_path, session_id="s", agent_run_id="r", config=_Cfg(),
                      event_bus=_Bus(), engine=None)
    args = {"title": "API notes", "content": f"token: {FAKE_KEY}\nuse the EU endpoint"}
    undo = await tool.capture_undo(args, ctx)
    res = await tool.execute(args, ctx)
    assert res.ok is True, res.error
    assert SECRET_LEFT_OUT in res.output
    note = tmp_path / "brain" / "api-notes.md"
    text = note.read_text(encoding="utf-8")
    assert FAKE_KEY not in text and "use the EU endpoint" in text
    assert undo["post_sha256"] == sha256_target(note, "text"), "Undo must still recognise the note"


async def test_brain_save_keeps_a_secret_out_and_says_so(tmp_path):
    """brain_save goes through LongTermMemory.append (and Retry through
    write_note): a secret is kept as ***, the save still reads back as
    confirmed, and the result says a secret was left out (v1.499.3)."""
    from pathlib import Path

    from iron_jarvis.brain.tools import BrainSaveTool
    from iron_jarvis.ltm.tools import SECRET_LEFT_OUT
    from tests.test_brain_profiles_v1406 import _app, _choose, _ctx, _profile

    app, client, platform = _app(tmp_path)
    _choose(client, _profile(client, guide_ref="")["id"])
    res = await BrainSaveTool(platform).execute(
        {"title": "api access", "content": f"the key is {FAKE_KEY}"}, _ctx(platform))
    assert res.ok and res.data["brain"]["verified"] is True, res
    assert SECRET_LEFT_OUT in res.output
    kept = "".join(p.read_text(encoding="utf-8") for p in Path(platform.config.home, "brain").glob("*.md"))
    assert FAKE_KEY not in kept and "***" in kept
