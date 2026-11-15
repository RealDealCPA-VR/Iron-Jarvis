"""Clients from chat (v1.499.0, vision plan leap 3 Phase 3, builder C).

Six tools (``settings/records_clients``): ``client_list`` (allow, walled),
``client_answer``, ``client_update``, ``client_preference_add``,
``client_preference_change``, ``client_preference_forget`` (ask, each with
a card that names the client and an Undo that puts back exactly what that
change did). The name a tool acts on must be in the OWNER's own message,
never only the model's argument; in a client's folder a change to ANOTHER
client is refused. Plus the guards on ``remember_preference``, the client
door, the Clients page door, the "client" claim family, the inbox kind's
setting row and the permission tiers.

Most tests drive the tools' own logic over an in-memory record that keeps
builder A's contract (``FakeClients`` below: it records every change made,
so a refusal is proven to write nothing); its map is A's real
``store.ClientMap`` and its names A's real ``names.signature`` /
``names.named_in``. The section "On builder A's REAL record" at the end runs
the same changes and their Undo on A's real store (``store.unconfirm``
included), with each turn's wall from the real resolver. The walls are set
through the real ContextVar (``wall.turn_scope``) and the owner's words
through ``records_clients.owner_words_scope`` — the same seams the lanes use.
All names and paths are made up.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import textwrap
from datetime import datetime
from types import SimpleNamespace

import pytest

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.trust import LOW_TRUST_DENY, low_trust_overrides
from iron_jarvis.daemon import doors as D
from iron_jarvis.daemon import page_doors as PD
from iron_jarvis.daemon.app import create_app
from iron_jarvis.learning.models import LessonRecord
from iron_jarvis.providers import egress
from iron_jarvis.settings import records as R
from iron_jarvis.settings import records_clients as RC
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.workmap import clients as C
from iron_jarvis.workmap import keys as K
from iron_jarvis.workmap.clients import block as CB
from iron_jarvis.workmap.clients import names as N
from iron_jarvis.workmap.clients import page as PG
from iron_jarvis.workmap.clients import store as S
from iron_jarvis.workmap.clients import wall as W
from iron_jarvis.workmap.models import WorkClient

WRITERS = frozenset(RC.CLIENT_WRITERS)
HARTLEYS = "client_" + "a1" * 6
SMITH = "client_" + "b2" * 6
LEE = "client_" + "c3" * 6
WAITING = "client_" + "d4" * 6
DECLINED = "client_" + "e5" * 6


# --------------------------------------------------------------------------- #
# the record, faked to its contract (builder A's store / names / page)
# --------------------------------------------------------------------------- #
def fake_sig(s) -> str:
    """The record's signature: builder A's real (pure) ``names.signature``."""
    return N.signature(s)


def _fk(folder: str) -> str:
    """A folder's key, as the record keys it."""
    return K.folder_key(K.home_folder(folder))


class FakeClients:
    """An in-memory client record with the contract's reads and changes."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.prefs: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.version = 0
        self.n = 0

    # -- seeding
    def add(self, cid, name, status="confirmed", folders=(), *, was_client=None, aliases=()):
        self.rows[cid] = {
            "id": cid, "name": name, "status": status, "folders": list(folders),
            "aliases": [fake_sig(a) for a in aliases],
            "was_client": status == "confirmed" if was_client is None else was_client,
            "homes": list(folders),
        }
        return cid

    def keep(self, cid, text, local_only=False, thread_id=""):
        self.n += 1
        pid = f"cpref_{self.n:012x}"
        self.prefs[pid] = {"id": pid, "client_id": cid, "text": text, "local_only": local_only,
                           "origin": "typed", "evidence": [{"thread_id": thread_id}] if thread_id else []}
        return pid

    # -- reads
    def load_map(self, engine):
        self.calls.append(("load_map",))
        conf = tuple(
            C.ClientRoot(cid, r["name"], _fk(f), f)
            for cid, r in self.rows.items() if r["status"] == "confirmed" for f in r["folders"]
        )
        waiting = tuple(
            C.Waiting(cid, r["name"], tuple(_fk(f) for f in r["homes"]))
            for cid, r in self.rows.items() if r["status"] == "proposed"
        )
        table = lambda st: {  # noqa: E731
            cid: (r["name"], fake_sig(r["name"]), tuple(r["aliases"])) for cid, r in self.rows.items()
            if r["status"] == st
        }
        return S.ClientMap(version=self.version, confirmed=conf, waiting=waiting, names=table("confirmed"),
                       waiting_names=table("proposed"))

    def preferences(self, engine, client_id):
        return [{"id": p["id"], "text": p["text"], "local_only": p["local_only"], "thread_id": ""}
                for p in self.prefs.values() if p["client_id"] == client_id]

    def clients_list(self, platform):
        rows = self.rows.values()
        return {
            "clients": [{"id": r["id"], "name": r["name"], "how": "noticed",
                         "folders": [{"folder": f, "name": f.replace("\\", "/").rsplit("/", 1)[-1]}
                                     for f in r["folders"]],
                         "work_count": 3, "last_at": "", "preferences_count": len(self.preferences(None, r["id"]))}
                        for r in rows if r["status"] == "confirmed"],
            "waiting": [{"id": r["id"], "name": r["name"], "rule": "under_clients", "about": "2 folders, 9 pieces of work",
                         "evidence": [], "folders": 2, "work": 9, "pre_ticked": True, "created_at": ""}
                        for r in rows if r["status"] == "proposed"],
            "declined": [{"id": r["id"], "name": r["name"], "decided_at": "", "was_client": r["was_client"]}
                         for r in rows if r["status"] == "declined"],
            "total": len(self.rows), "noticing": True,
        }

    # -- changes (each one the contract names)
    def _row(self, cid):
        if cid not in self.rows:
            raise C.ClientNotFound()
        return self.rows[cid]

    def _view(self, cid):
        self.version += 1
        return {"ok": True, "client": {"id": cid, "name": self.rows[cid]["name"]}, "note": ""}

    def confirm(self, platform, cid, *, name=None):
        self.calls.append(("confirm", cid))
        r = self._row(cid)
        if r["status"] != "proposed":
            raise C.ClientConflict(f"{r['name']} was already answered.")
        r.update(status="confirmed", was_client=True, folders=list(r["homes"]))
        return self._view(cid)

    def decline(self, platform, cid):
        self.calls.append(("decline", cid))
        r = self._row(cid)
        if r["status"] == "declined":
            raise C.ClientConflict(f"{r['name']} was already answered.")
        r["status"] = "declined"
        return self._view(cid)

    def ask_again(self, platform, cid):
        self.calls.append(("ask_again", cid))
        r = self._row(cid)
        if r["status"] != "declined" or r["was_client"]:
            raise C.ClientRefused("Only a name that was never a client is asked again.")
        r["status"] = "proposed"
        return self._view(cid)

    def restore(self, platform, cid):
        self.calls.append(("restore", cid))
        r = self._row(cid)
        if r["status"] != "declined" or not r["was_client"]:
            raise C.ClientRefused("That client cannot be made a client again.")
        r["status"] = "confirmed"
        return self._view(cid)

    def create(self, platform, *, folder="", name="", **_kw):
        self.calls.append(("create", folder, name))
        sig = fake_sig(name)
        for cid, r in self.rows.items():
            if fake_sig(r["name"]) == sig and r["status"] in ("proposed", "declined"):
                r.update(status="confirmed", was_client=True, folders=[folder])
                return self._view(cid)
        self.n += 1
        cid = f"client_{self.n:012x}"
        self.add(cid, name, "confirmed", [folder])
        return self._view(cid)

    def add_folder(self, platform, cid, folder, **_kw):
        self.calls.append(("add_folder", cid, folder))
        self._row(cid)["folders"].append(folder)
        return self._view(cid)

    def remove_folder(self, platform, cid, folder):
        self.calls.append(("remove_folder", cid, folder))
        self._row(cid)["folders"].remove(folder)
        return self._view(cid)

    def rename(self, platform, cid, name):
        self.calls.append(("rename", cid, name))
        r = self._row(cid)
        r["aliases"].append(fake_sig(r["name"]))
        r["name"] = name
        return self._view(cid)

    def set_aliases(self, platform, cid, aliases):
        self.calls.append(("set_aliases", cid, tuple(aliases)))
        self._row(cid)["aliases"] = [fake_sig(a) for a in aliases]
        return self._view(cid)

    def keep_preference(self, platform, cid, text, *, origin, local_only=False, evidence=()):
        self.calls.append(("keep_preference", cid, text, origin, local_only, tuple(json.dumps(e) for e in evidence)))
        if self._row(cid)["status"] != "confirmed":
            raise C.ClientRefused("Only a client's preference is kept.")
        pid = self.keep(cid, text, local_only)
        self.prefs[pid].update(origin=origin, evidence=list(evidence))
        self.version += 1
        return {"ok": True, "preference": dict(self.prefs[pid])}

    def change_preference(self, platform, cid, pid, text, **_kw):
        self.calls.append(("change_preference", cid, pid, text))
        self.prefs[pid]["text"] = text
        self.version += 1
        return {"ok": True, "preference": dict(self.prefs[pid])}

    def forget_preference(self, platform, cid, pid):
        self.calls.append(("forget_preference", cid, pid))
        view = self.prefs.pop(pid)
        self.version += 1
        return {"ok": True, "preference": view}

    def unconfirm(self, platform, cid, *, back_to):
        self.calls.append(("unconfirm", cid, back_to))
        if back_to == "gone":
            del self.rows[cid]
            return {"ok": True}
        r = self._row(cid)
        r.update(status=back_to, folders=[] if back_to != "confirmed" else r["folders"])
        if back_to == "proposed":
            r["was_client"] = False
        return self._view(cid)

    def writes(self):
        return [c for c in self.calls if c[0] not in ("load_map",)]


_MUTATIONS = ("confirm", "decline", "ask_again", "restore", "create", "add_folder", "remove_folder", "rename",
              "set_aliases", "keep_preference", "change_preference", "forget_preference")


@pytest.fixture()
def rec(monkeypatch):
    f = FakeClients()
    monkeypatch.setattr(S, "load_map", f.load_map)
    monkeypatch.setattr(S, "preferences", f.preferences)
    monkeypatch.setattr(PG, "clients_list", f.clients_list)
    for name in _MUTATIONS:
        monkeypatch.setattr(S, name, getattr(f, name))
    monkeypatch.setattr(S, "unconfirm", f.unconfirm, raising=False)
    return f


@pytest.fixture(scope="module")
def platform(tmp_path_factory):
    return create_app(str(tmp_path_factory.mktemp("clients-chat") / "home")).state.platform


def _ctx(p, thread_id="chat_t1"):
    return ToolContext(workspace=p.config.home, session_id="chat", agent_run_id="chat", config=p.config,
                       event_bus=p.event_bus, engine=p.engine, thread_id=thread_id)


def client_turn(cid, name, folder):
    return C.TurnWall(owner=f"client:{cid}", client=C.ClientRoot(cid, name, _fk(folder), folder),
                      folder_key=_fk(folder))


def call(p, tool_name, args, *, turn=C.NO_CLIENT, words=""):
    """capture_undo then execute — the registry's order — inside the turn's
    wall and the owner's words. Returns ``(spend_check, undo descriptor,
    result)``."""
    tool = p.registry.get(tool_name)
    ctx = _ctx(p)

    async def go():
        with W.turn_scope(turn), RC.owner_words_scope(words):
            check = await tool.spend_check(args, ctx)
            desc = await tool.capture_undo(args, ctx)
            res = await tool.execute(args, ctx)
            return check, desc, res

    return asyncio.run(go())


def undo(p, desc):
    return R.restore(p, json.loads(desc["pre_inline"]))


def seed_three(rec):
    rec.add(HARTLEYS, "Hartleys", "confirmed", [r"C:\Clients\Hartleys"])
    rec.add(SMITH, "Smith", "confirmed", [r"C:\Clients\Smith"])
    rec.add(WAITING, "Lee Dental", "proposed", [r"C:\Clients\Lee Dental"])


# --------------------------------------------------------------------------- #
# arming, both ways
# --------------------------------------------------------------------------- #
NAMES = {HARTLEYS: ("Hartleys", "hartley", ()), SMITH: ("Smith", "smith", ())}
ARMS = [
    ("Hartley's wants cash basis", {"client_preference_add"}),
    ("yes, Hartleys is a client", {"client_answer"}),
    ("Smith isn't a client", {"client_answer"}),
    ("Smith Dental is a client, its folder is D:\\Clients\\Smith Dental", {"client_answer"}),
    ("make Pereira Farms a client", {"client_answer"}),
    ("show me my clients", {"client_list"}),
    ("who are my clients?", {"client_list"}),
    ("add D:\\Scans\\Hartleys to Hartleys", {"client_update"}),
    ("Hartleys is also called HRT", {"client_update"}),
    ("change Hartleys' preference to accrual", {"client_preference_change"}),
    ("forget that Hartleys uses cash basis", {"client_preference_forget"}),
]
ARMS_NO_WRITER = [
    "my client called this morning",
    "the client portal is down",
    "Mrs. Price wants coffee",
    "what is a client?",
    "This is a client email, please answer it",
    "is Hartleys a client?",
    "what does Hartleys want for the return?",
    "draft a letter to Smith about the extension",
]


@pytest.mark.parametrize("text,want", ARMS, ids=[t for t, _ in ARMS])
def test_arming_both_ways(rec, text, want):
    got = set(RC.clients_tools_for(text, client_names=NAMES))
    assert got == want, (text, got)
    # Through the one function both lanes call.
    assert want <= set(R.record_tools_for(text, client_names=NAMES)), text


@pytest.mark.parametrize("text", ARMS_NO_WRITER)
def test_arming_no_writer(rec, text):
    got = set(RC.clients_tools_for(text, client_names=NAMES))
    assert not (got & WRITERS), (text, got)
    assert not (set(R.record_tools_for(text, client_names=NAMES)) & WRITERS), text


def test_in_a_clients_folder_the_turns_own_client_is_the_subject(rec):
    """"They want cash basis" in Hartleys' folder is Hartleys' preference; the
    same words in no client's folder arm nothing."""
    assert RC.clients_tools_for("they want cash basis", client_names=NAMES, in_client="Hartleys") == [
        "client_preference_add"]
    assert RC.clients_tools_for("for this client, always use accrual", in_client="Hartleys") == [
        "client_preference_add"]
    assert RC.clients_tools_for("they want cash basis", client_names=NAMES) == []
    # A question keeps only the reader, in a client's folder too.
    assert RC.clients_tools_for("do they want cash basis?", in_client="Hartleys") == []


def test_arming_patterns_are_linear():
    """A child process with a deadline: every rule is anchored or bounded, so
    near-matches of 2,000 characters return at once."""
    code = textwrap.dedent(
        """
        import time
        from iron_jarvis.settings.records_clients import clients_tools_for
        bad = [
            "A " * 1000 + "is a client",
            ("Hartleys " * 300) + "is also called",
            "make " + "Big " * 600 + "a client",
            "show me " + "my " * 900 + "clients",
            "forget " + "x " * 990 + "preference",
            "Smith" + " " * 1990 + "isn't a client",
            "they " + "want " * 390,
            "add D:" + "\\\\x" * 900 + " to Hartleys",
        ]
        names = {"client_" + "a1" * 6: ("Hartleys", "hartley", ())}
        t = time.perf_counter()
        for b in bad:
            clients_tools_for(b, client_names=names, in_client="Hartleys")
        print(time.perf_counter() - t)
        """
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert float(out.stdout.strip().splitlines()[-1]) < 3.0, out.stdout


# --------------------------------------------------------------------------- #
# tiers, registration, low trust
# --------------------------------------------------------------------------- #
def test_tiers_and_registration(platform):
    for name, tier in RC.ALL_CLIENT_TOOL_TIERS.items():
        assert platform.registry.get(name) is not None, name
        assert platform.permissions.authorize(name, {}).allowed is (tier == "allow"), name
    for name in WRITERS:
        assert R.RECORD_TOOL_TIERS[name] == "ask" and name in R.RECORD_CARD_TOOLS
    assert RC.CLIENT_READER not in R.RECORD_TOOL_TIERS


def test_low_trust_denies_every_client_writer(platform, rec):
    """Through the REAL registry, the layer that refuses: every writer is
    denied at low trust and the record is never touched; the control (full
    trust, the card answered) reaches the record."""
    seed_three(rec)
    assert WRITERS <= LOW_TRUST_DENY and RC.CLIENT_READER not in LOW_TRUST_DENY
    args = {
        "client_answer": {"name": "Lee Dental", "answer": "yes"},
        "client_update": {"client": "Hartleys", "add_folder": r"D:\Scans\Hartleys"},
        "client_preference_add": {"text": "Uses cash basis.", "client": "Hartleys"},
        "client_preference_change": {"text": "cash", "new_text": "Uses accrual.", "client": "Hartleys"},
        "client_preference_forget": {"text": "cash", "client": "Hartleys"},
    }
    rec.keep(HARTLEYS, "Uses cash basis.")
    words = "Lee Dental is a client; add D:\\Scans\\Hartleys to Hartleys; Hartleys uses cash basis"

    async def go(name, overrides):
        with W.turn_scope(C.NO_CLIENT), RC.owner_words_scope(words):
            return await platform.registry.invoke(
                name, args[name], _ctx(platform), platform.permissions, agent_overrides=overrides,
                session_allow={name},
            )

    for name in sorted(WRITERS):
        before = len(rec.writes())
        res = asyncio.run(go(name, low_trust_overrides({name: "allow"})))
        assert res.ok is False and res.error.startswith("permission denied"), (name, res.error)
        assert len(rec.writes()) == before, f"{name} reached the record under low trust"
    control = asyncio.run(go("client_answer", {"client_answer": "allow"}))
    assert control.ok is True, control.error
    assert ("confirm", WAITING) in rec.writes()


# --------------------------------------------------------------------------- #
# client_list: walled
# --------------------------------------------------------------------------- #
def _add_row(p, cid, name, local):
    with session_scope(p.engine) as db:
        if db.get(WorkClient, cid) is None:
            db.add(WorkClient(id=cid, name=name, signature=fake_sig(name) + cid, status="confirmed",
                              evidence_json=json.dumps({"rule": "under_clients", "homes": [
                                  {"folder": f"C:/Clients/{name}", "key": f"c:/clients/{name.lower()}",
                                   "work": 3, "last_at": "", "local": local}], "work": 3, "folders": 1})))
            db.commit()


def test_client_list_is_walled_and_withholds_local_only_clients_in_a_cloud_turn(platform, rec):
    from iron_jarvis.providers import egress

    seed_three(rec)
    rec.add(LEE, "Leeward", "confirmed", [r"C:\Clients\Leeward"])
    _add_row(platform, HARTLEYS, "Hartleys", False)
    _add_row(platform, SMITH, "Smith", False)
    _add_row(platform, LEE, "Leeward", True)  # known only from Local only work
    # A chat in Hartleys' folder: only Hartleys, and how many others there are.
    _c, _d, res = call(platform, "client_list", {}, turn=client_turn(HARTLEYS, "Hartleys", r"C:\Clients\Hartleys"),
                       words="who are my clients?")
    assert res.ok, res.error
    assert "Hartleys" in res.output and "Smith" not in res.output and "Leeward" not in res.output
    assert "Lee Dental" not in res.output
    assert "2 other clients are on the Clients page" in res.output
    assert [c["id"] for c in res.data["clients"]] == [HARTLEYS]
    # No client: every confirmed name, but a cloud turn leaves out the client
    # known only from Local only work, and names that wait are a count.
    _c, _d, res = call(platform, "client_list", {}, words="who are my clients?")
    assert "Hartleys" in res.output and "Smith" in res.output and "Leeward" not in res.output
    assert "1 more client is kept on this computer" in res.output
    assert "Lee Dental" not in res.output and "1 more name Iron Jarvis noticed" in res.output
    # A Local only turn may be told it (the whole prompt stays on this network).

    async def local():
        with egress.local_only_scope(True), W.turn_scope(C.NO_CLIENT), RC.owner_words_scope("who are my clients?"):
            return await platform.registry.get("client_list").execute({}, _ctx(platform))

    res = asyncio.run(local())
    assert "Leeward" in res.output and "kept on this computer" not in res.output
    # A waiting name the OWNER named is said, with its question.
    _c, _d, res = call(platform, "client_list", {}, words="who are my clients, and what about Lee Dental?")
    assert "Is Lee Dental a client?".lower() in res.output.lower()
    # The map cannot be read: the reader says so, never "no clients".
    _c, _d, res = call(platform, "client_list", {}, turn=C.TurnWall(ok=False), words="who are my clients?")
    assert res.ok is False and res.error == C.ClientsUnreadable.default


# --------------------------------------------------------------------------- #
# client_answer
# --------------------------------------------------------------------------- #
def test_client_answer_refuses_a_name_not_in_the_owners_message(platform, rec):
    seed_three(rec)
    # The model names Lee Dental; the owner said only "yes".
    check, desc, res = call(platform, "client_answer", {"name": "Lee Dental", "answer": "yes"}, words="yes")
    assert res.ok is False and "is not in it" in res.error
    assert check.ask is False and desc is None  # no card for a call that changes nothing
    assert rec.writes() == []
    # No owner words at all (a lane that set none): refused, never acted on.
    _c, _d, res = call(platform, "client_answer", {"name": "Lee Dental", "answer": "yes"})
    assert res.ok is False and res.error == RC.NO_OWNER_WORDS and rec.writes() == []
    # The owner's own words name it: the card says what it does, then it runs.
    check, desc, res = call(platform, "client_answer", {"name": "Lee Dental", "answer": "yes"},
                            words="yes, Lee Dental is a client")
    assert check.ask is True and "Makes Lee Dental a client" in check.words
    assert res.ok, res.error
    assert rec.rows[WAITING]["status"] == "confirmed"
    assert res.data["client_id"] == WAITING and res.output.endswith(f"/clients/{WAITING}")
    # Undo: back to waiting.
    assert desc is not None
    undo(platform, desc)
    assert rec.rows[WAITING]["status"] == "proposed"


def test_client_answer_no_folder_asks_for_one_and_a_new_client_is_made_from_its_folder(platform, rec):
    _c, desc, res = call(platform, "client_answer", {"name": "Pereira Farms", "answer": "yes"},
                         words="Pereira Farms is a client")
    assert res.ok is False and res.error == "Pereira Farms has no folder yet, so no chat is told about it. Tell me its folder."
    check, desc, res = call(platform, "client_answer",
                            {"name": "Pereira Farms", "answer": "yes", "folder": r"D:\Clients\Pereira Farms"},
                            words=r"Pereira Farms is a client, its folder is D:\Clients\Pereira Farms")
    assert res.ok, res.error
    cid = res.data["client_id"]
    assert rec.rows[cid]["status"] == "confirmed" and rec.rows[cid]["folders"] == [r"D:\Clients\Pereira Farms"]
    assert r"D:\Clients\Pereira Farms" in check.words
    undo(platform, desc)
    assert cid not in rec.rows  # the client the change made is gone again


def test_not_a_client_and_undo_restores_it(platform, rec):
    seed_three(rec)
    check, desc, res = call(platform, "client_answer", {"name": "Smith", "answer": "no"}, words="Smith isn't a client")
    assert res.ok and rec.rows[SMITH]["status"] == "declined"
    assert "Marks Smith as not a client" in check.words
    assert res.data["record_change"]["title"] == "Not a client"
    undo(platform, desc)
    assert rec.rows[SMITH]["status"] == "confirmed"


def test_no_undo_is_promised_when_the_record_cannot_put_a_yes_back(platform, rec, monkeypatch):
    """A Yes is undone by the record's ``unconfirm``; without it no Undo is
    journaled and no card offers one (never an Undo the journal cannot do)."""
    seed_three(rec)
    monkeypatch.delattr(S, "unconfirm")
    _c, desc, res = call(platform, "client_answer", {"name": "Lee Dental", "answer": "yes"},
                         words="Lee Dental is a client")
    assert res.ok and desc is None and "record_change" not in res.data
    # A No needs no new function: its Undo is the record's own Ask again.
    _c, desc, res = call(platform, "client_answer", {"name": "Smith", "answer": "no"}, words="Smith isn't a client")
    assert res.ok and desc is not None and res.data["record_change"]["change_id"]


# --------------------------------------------------------------------------- #
# client preferences
# --------------------------------------------------------------------------- #
def test_client_preference_add_defaults_to_the_turns_client_and_refuses_another_clients(platform, rec):
    seed_three(rec)
    hartleys = client_turn(HARTLEYS, "Hartleys", r"C:\Clients\Hartleys")
    smith = client_turn(SMITH, "Smith", r"C:\Clients\Smith")
    # In Hartleys' folder, "they want cash basis" is kept for Hartleys.
    check, desc, res = call(platform, "client_preference_add", {"text": "Uses cash basis."}, turn=hartleys,
                            words="they want cash basis from now on")
    assert res.ok, res.error
    assert check.words == "Keep for Hartleys only (told in Hartleys' folders): “Uses cash basis.”"
    kept = [c for c in rec.writes() if c[0] == "keep_preference"]
    assert kept and kept[-1][1] == HARTLEYS and kept[-1][3] == "said"
    evidence = json.loads(kept[-1][5][0])
    # The evidence is the OWNER's message, never the model's argument.
    assert evidence["quote"] == "they want cash basis from now on" and evidence["where"] == "Hartleys"
    # In Smith's folder, a preference for Hartleys is refused — whether the
    # model names Hartleys or the owner's message does.
    n = len(rec.writes())
    for args, words in (({"text": "Uses cash basis.", "client": "Hartleys"}, "Hartleys wants cash basis"),
                        ({"text": "Uses cash basis."}, "Hartleys wants cash basis"),
                        # Only the MODEL named Hartleys; the owner's words name nobody.
                        ({"text": "Uses cash basis.", "client": "Hartleys"}, "keep this: cash basis"),
                        # The owner named both; the model picked the other one.
                        ({"text": "Uses cash basis.", "client": "Hartleys"}, "Smith and Hartleys want cash basis")):
        check, desc, res = call(platform, "client_preference_add", args, turn=smith, words=words)
        assert res.ok is False, args
        assert res.error == ("This chat is in Smith's folder. A preference for another client is kept from that "
                             "client's page or a chat in its folders.")
        assert check.ask is False and desc is None
    assert len(rec.writes()) == n
    # In no client's folder the client must be named by the OWNER.
    _c, _d, res = call(platform, "client_preference_add", {"text": "Uses cash basis.", "client": "Hartleys"},
                       words="keep this: cash basis")
    assert res.ok is False and "is not in it" in res.error
    _c, _d, res = call(platform, "client_preference_add", {"text": "Uses cash basis."},
                       words="Hartley's wants cash basis")
    assert res.ok and [c for c in rec.writes() if c[0] == "keep_preference"][-1][1] == HARTLEYS


def test_a_local_only_turn_keeps_a_local_only_preference(platform, rec):
    from iron_jarvis.providers import egress

    seed_three(rec)

    async def go():
        tool = platform.registry.get("client_preference_add")
        with egress.local_only_scope(True), W.turn_scope(C.NO_CLIENT), RC.owner_words_scope("Hartleys wants cash basis"):
            return await tool.execute({"text": "Uses cash basis."}, _ctx(platform))

    assert asyncio.run(go()).ok
    assert [c for c in rec.writes() if c[0] == "keep_preference"][-1][4] is True


def test_waiting_name_card_confirms_and_keeps_in_one_press_and_undo_reverts_both(platform, rec):
    seed_three(rec)
    check, desc, res = call(platform, "client_preference_add", {"text": "Wants paper statements."},
                            words="Lee Dental wants paper statements")
    assert check.ask is True
    assert check.words == "Make Lee Dental a client and keep for Lee Dental: “Wants paper statements.”"
    assert res.ok, res.error
    assert rec.rows[WAITING]["status"] == "confirmed"
    assert [p["text"] for p in rec.preferences(None, WAITING)] == ["Wants paper statements."]
    assert res.output.startswith("Lee Dental is a client now; kept for Lee Dental only")
    undo(platform, desc)
    assert rec.rows[WAITING]["status"] == "proposed" and rec.preferences(None, WAITING) == []


def test_cards_name_the_client_and_undo_restores_exactly(platform, rec):
    seed_three(rec)
    pid = rec.keep(HARTLEYS, "Uses cash basis.")
    hartleys = client_turn(HARTLEYS, "Hartleys", r"C:\Clients\Hartleys")
    # Change, then Undo puts the old words back.
    check, desc, res = call(platform, "client_preference_change", {"text": "cash basis", "new_text": "Uses accrual."},
                            turn=hartleys, words="change their preference to accrual")
    assert check.words == "Change what is kept for Hartleys: “Uses cash basis.” becomes “Uses accrual.”"
    card = res.data["record_change"]
    assert card["key"] == f"client.{HARTLEYS}" and card["label"] == "Client “Hartleys”"
    assert (card["old"], card["new"]) == ("Uses cash basis.", "Uses accrual.")
    undo(platform, desc)
    assert rec.prefs[pid]["text"] == "Uses cash basis."
    # Forget, then Undo keeps it again with its own words, origin and evidence.
    rec.prefs[pid]["origin"] = "said"
    check, desc, res = call(platform, "client_preference_forget", {"text": "cash basis"}, turn=hartleys,
                            words="forget that they use cash basis")
    assert check.words == "Forget what is kept for Hartleys: “Uses cash basis.”" and pid not in rec.prefs
    undo(platform, desc)
    again = rec.preferences(None, HARTLEYS)
    assert [p["text"] for p in again] == ["Uses cash basis."]
    assert rec.calls[-1][0] == "keep_preference" and rec.calls[-1][3] == "said"
    # An update with several changes is undone in reverse, exactly.
    check, desc, res = call(platform, "client_update", {"add_folder": r"D:\Scans\Hartleys", "aliases": ["HRT"]},
                            turn=hartleys, words=r"add D:\Scans\Hartleys to Hartleys, it is also called HRT")
    assert "For Hartleys, Iron Jarvis adds the folder D:\\Scans\\Hartleys; also knows it as HRT." == check.words
    assert rec.rows[HARTLEYS]["folders"] == [r"C:\Clients\Hartleys", r"D:\Scans\Hartleys"]
    undo(platform, desc)
    assert rec.rows[HARTLEYS]["folders"] == [r"C:\Clients\Hartleys"] and rec.rows[HARTLEYS]["aliases"] == []


def test_undo_refuses_when_the_client_was_changed_since(platform, rec):
    seed_three(rec)
    pid = rec.keep(HARTLEYS, "Uses cash basis.")
    hartleys = client_turn(HARTLEYS, "Hartleys", r"C:\Clients\Hartleys")
    _c, desc, res = call(platform, "client_preference_change", {"text": "cash", "new_text": "Uses accrual."},
                         turn=hartleys, words="change their preference to accrual")
    assert res.ok
    rec.prefs[pid]["text"] = "Uses the modified cash basis."  # changed on the page since
    with pytest.raises(R.RecordError, match="was changed again after this change"):
        undo(platform, desc)
    assert rec.prefs[pid]["text"] == "Uses the modified cash basis."


def test_client_update_refuses_another_client_in_a_clients_folder(platform, rec):
    seed_three(rec)
    smith = client_turn(SMITH, "Smith", r"C:\Clients\Smith")
    for words in (r"add D:\Scans\Hartleys to Hartleys", r"add D:\Scans\Hartleys here, Smith and Hartleys share it"):
        _c, desc, res = call(platform, "client_update", {"client": "Hartleys", "add_folder": r"D:\Scans\Hartleys"},
                             turn=smith, words=words)
        assert res.ok is False and res.error.startswith("This chat is in Smith's folder, so it changes only Smith.")
        assert "C:\\Clients\\Hartleys" not in res.error and rec.writes() == []
    # The same change in Hartleys' own folder needs no name at all.
    hartleys = client_turn(HARTLEYS, "Hartleys", r"C:\Clients\Hartleys")
    _c, desc, res = call(platform, "client_update", {"add_folder": r"D:\Scans\Hartleys"}, turn=hartleys,
                         words=r"add D:\Scans\Hartleys to this client")
    assert res.ok and ("add_folder", HARTLEYS, r"D:\Scans\Hartleys") in rec.writes()


# --------------------------------------------------------------------------- #
# remember_preference never keeps a client's fact for every chat
# --------------------------------------------------------------------------- #
def _global_prefs(p) -> list[str]:
    with session_scope(p.engine) as db:
        from sqlmodel import select

        return sorted(r.text for r in db.exec(select(LessonRecord).where(LessonRecord.source == "preference")))


def test_remember_preference_refuses_client_sentences_and_writes_nothing_global(platform, rec):
    seed_three(rec)
    tool = platform.registry.get("remember_preference")
    hartleys = client_turn(HARTLEYS, "Hartleys", r"C:\Clients\Hartleys")

    async def keep(text, turn=C.NO_CLIENT):
        with W.turn_scope(turn):
            return await tool.execute({"text": text}, _ctx(platform))

    before = _global_prefs(platform)
    # 1. A sentence naming a confirmed OR a waiting client.
    for text in ("Hartleys wants cash basis", "Prefers paper statements for Lee Dental"):
        res = asyncio.run(keep(text))
        assert res.ok is False and "use client_preference_add" in res.error, text
    # 2. In a client's folder, a sentence whose subject is the client.
    for text in ("They want cash basis", "The client prefers paper statements", "Send drafts by mail for them"):
        res = asyncio.run(keep(text, hartleys))
        assert res.ok is False and res.error.startswith("This chat is in Hartleys' folder"), text
    assert _global_prefs(platform) == before
    # 3. Anything else is still kept, with the note in a client's folder.
    res = asyncio.run(keep("Prefers shorter answers", hartleys))
    assert res.ok and res.output.endswith("Kept for every chat. If this was only for Hartleys, say so.")
    res = asyncio.run(keep("Prefers bullet points"))
    assert res.ok and res.output == "remembered preference: Prefers bullet points"
    assert _global_prefs(platform) == sorted([*before, "Prefers shorter answers", "Prefers bullet points"])
    # 4. The client record cannot be read: fail closed.
    res = asyncio.run(keep("Prefers tables", C.TurnWall(ok=False)))
    assert res.ok is False and "could not be read" in res.error


# --------------------------------------------------------------------------- #
# doors
# --------------------------------------------------------------------------- #
def test_client_door_href_is_built_only_from_a_valid_id(rec):
    ok = D.with_client_door([], {HARTLEYS: ("Hartleys", "hartley", ())})
    assert ok == [{"href": f"/clients/{HARTLEYS}", "label": "Open Hartleys' page"}]
    for bad in ("Hartley's", "client_ABCDEF123456", "client_a1a1a1a1a1a1\n", "../confirm", "client_a1a1",
                "/clients/x/delete", "client_a1a1a1a1a1a1/../../x"):
        got = D.with_client_door([], [{"id": bad, "name": "Hartleys"}])
        assert got == [{"href": "/clients", "label": "Open your clients"}], bad
    # At most one door, several named → the Clients page.
    two = D.with_client_door([], {HARTLEYS: "Hartleys", SMITH: "Smith"})
    assert two == [{"href": "/clients", "label": "Open your clients"}]
    # Never beside a door that already leads there, never past the cap.
    assert D.with_client_door([{"href": f"/clients/{HARTLEYS}", "label": "x"}], {SMITH: "Smith"}) == [
        {"href": f"/clients/{HARTLEYS}", "label": "x"}]
    full = [{"href": f"/p{i}", "label": "x"} for i in range(D.MAX_DOORS)]
    assert D.with_client_door(full, {SMITH: "Smith"}) == full
    assert D.with_client_door([], {}) == []
    # A tool's success door: from the result's id, never its words.
    res = SimpleNamespace(ok=True, data={"client_id": "Hartleys' folder", "client": "Hartleys"})
    assert D.door_for("client_answer", res) == {"href": "/clients", "label": "Open your clients"}
    res = SimpleNamespace(ok=True, data={"client_id": SMITH, "client": "Smith"})
    assert D.door_for("client_preference_add", res) == {"href": f"/clients/{SMITH}", "label": "Open Smith's page"}


def test_the_clients_page_door_and_the_bulk_line():
    assert PD.with_page_door([], "show my clients") == [{"href": "/clients", "label": "Open your clients"}]
    assert PD.with_page_door([], "open the clients page") == [{"href": "/clients", "label": "Open your clients"}]
    bulk = PD.with_page_door([], "make all of those clients")
    assert bulk == [{"href": "/clients#waiting", "label": "Answer the names on the Clients page"}]
    assert PD.CLIENTS_BULK_LINE in PD.prompt_block("make all of those clients")
    for everyday in ("the client portal is down", "open the client portal", "my client called"):
        assert PD.with_page_door([], everyday) == [], everyday
        assert PD.prompt_block(everyday) == "", everyday
    # A named client's own door already there: no second one to the Clients page.
    named = [{"href": f"/clients/{HARTLEYS}", "label": "Open Hartleys' page"}]
    assert PD.with_page_door(named, "show my clients") == named


# --------------------------------------------------------------------------- #
# the rest of chat's reach
# --------------------------------------------------------------------------- #
def test_a_claimed_client_change_that_did_not_run_gets_a_note():
    from iron_jarvis.daemon.change_claims import claimed_change_note

    assert "No client was created in this reply" in claimed_change_note("I made Hartleys a client.", [], [])
    assert claimed_change_note("I made Hartleys a client.", ["client_answer"], []) == ""
    for other in ("I set up the email client.", "I added a new client in QuickBooks.",
                  "I updated the calendar with your client meetings."):
        assert claimed_change_note(other, [], []) == "", other
    # A preference kept for one client backs "I saved that preference".
    assert claimed_change_note("I saved that preference for Hartleys.", ["client_preference_add"], []) == ""


def test_the_feature_lookup_finds_clients(platform):
    from iron_jarvis.capability_search import index as CI
    from iron_jarvis.capability_search import join as J

    for query, tool in (("who are my clients", "client_list"),
                        ("keep a preference for one client", "client_preference_add"),
                        ("say whether a name is a client", "client_answer")):
        found = [e.tool or e.id for e in CI.search(platform, query, limit=5)]
        assert tool in found, (query, found)
    for writer in WRITERS:
        assert J.READER_FOR[writer] == "client_list"
    assert WRITERS | {"client_list"} <= J.joinable()


def test_the_notice_clients_row_is_under_memory_and_you():
    from iron_jarvis.core.config import Config
    from iron_jarvis.settings import schema

    row = schema.get("notice_clients")
    assert row.label == "Notice clients in your folders" and row.group == "memory" and row.tier == "allow"
    assert Config.model_fields["notice_clients"].default is True
    assert "no model" in row.help and "nothing is sent anywhere" in row.help


def test_the_card_words_are_plain():
    from iron_jarvis.agents import mission

    for name in RC.ALL_CLIENT_TOOL_TIERS:
        words = mission.card_words(name)
        assert "_" not in words and not words.startswith("use "), (name, words)
    assert mission.card_words("client_preference_add") == "keep a preference for one client"


def test_the_owner_words_scope_resets(rec):
    with RC.owner_words_scope("Hartleys wants cash basis"):
        assert RC.owner_words() == "Hartleys wants cash basis"
    assert RC.owner_words() == ""

    async def gen():
        yield RC.owner_words()

    async def drain():
        return [x async for x in RC.scoped_owner_words(gen(), "Smith isn't a client")]

    assert asyncio.run(drain()) == ["Smith isn't a client"] and RC.owner_words() == ""


def test_possessive_follows_the_one_table():
    assert RC.possessive("Hartley's") == "Hartley's"
    assert RC.possessive("Hartleys") == "Hartleys'"
    assert RC.possessive("Smith") == "Smith's"
    # The ONE table, read from the dashboard's own copy
    # (dashboard/lib/clients.ts POSSESSIVE_TABLE): the daemon's sentences and
    # the page headings say the same.
    from pathlib import Path

    ts = (Path(__file__).resolve().parents[1] / "dashboard" / "lib" / "clients.ts").read_text(encoding="utf-8")
    ts = ts.replace("\r\n", "\n")
    body = ts[ts.index("export const POSSESSIVE_TABLE"):]
    body = body[: body.index("];")]
    rows = re.findall(r'\[\s*"([^"]*)"\s*,\s*"([^"]*)"\s*\]', body)
    assert len(rows) >= 5, rows
    for name, want in rows:
        assert N.possessive(name) == want, (name, N.possessive(name), want)
        assert RC.possessive(name) == want, (name, RC.possessive(name), want)


def test_live_names_never_raises(platform, monkeypatch):
    def broken(engine):
        raise C.ClientsUnreadable()

    monkeypatch.setattr(S, "load_map", broken)
    assert RC.live_names(platform) == {}
    assert datetime  # imported for the evidence's timestamp shape


# --------------------------------------------------------------------------- #
# On builder A's REAL record: no fake, the turn's wall from the real
# resolver. Proves the shapes the tools read (a Yes, its Undo through
# ``store.unconfirm``, ``forget_preference``'s answer, ``roots_of`` keys,
# ``clients_list`` rows) are the record's own.
# --------------------------------------------------------------------------- #
@pytest.fixture()
def real(tmp_path):
    p = create_app(str(tmp_path / "home")).state.platform
    yield p
    p.engine.dispose()


def _waiting_row(p, name, homes) -> str:
    """A name the noticing pass would have left waiting (the row as
    ``detect.notice`` writes it); the map's write version is moved, as for
    any row written outside the store's functions."""
    from iron_jarvis.core.ids import new_id

    cid = new_id("client")
    with session_scope(p.engine) as db:
        db.add(WorkClient(
            id=cid, name=name, name_key=N.name_key(name), signature=N.signature(name), status="proposed",
            how="noticed", strength=9, evidence_json=json.dumps({
                "rule": "under_clients", "work": 4, "folders": len(homes),
                "homes": [{"folder": h, "key": _fk(h), "work": 2, "last_at": None, "local": False} for h in homes],
            }),
        ))
        db.commit()
    S.bump_version()
    return cid


def _status(p, cid):
    with session_scope(p.engine) as db:
        row = db.get(WorkClient, cid)
        return None if row is None else row.status


def test_on_the_real_record_a_yes_from_chat_and_its_undo(real):
    wid = _waiting_row(real, "Lee Dental", [r"C:\Clients\Lee Dental"])
    check, desc, res = call(real, "client_answer", {"name": "Lee Dental", "answer": "yes"},
                            words="yes, Lee Dental is a client")
    assert check.ask is True and res.ok, res.error
    assert desc is not None, "a Yes has an Undo on the real record (store.unconfirm)"
    assert _status(real, wid) == "confirmed"
    cmap = S.load_map(real.engine)
    assert cmap.client_of(r"C:\Clients\Lee Dental\2026").client_id == wid
    undo(real, desc)
    assert _status(real, wid) == "proposed"
    assert S.load_map(real.engine).client_of(r"C:\Clients\Lee Dental\2026") is None
    # A name not on record, made a client from its folder; Undo: gone again.
    check, desc, res = call(real, "client_answer",
                            {"name": "Pereira Farms", "answer": "yes", "folder": r"D:\Clients\Pereira Farms"},
                            words=r"Pereira Farms is a client, its folder is D:\Clients\Pereira Farms")
    assert res.ok, res.error
    made = res.data["client_id"]
    assert C.CLIENT_ID_RE.fullmatch(made) and _status(real, made) == "confirmed"
    undo(real, desc)
    assert _status(real, made) is None


def test_on_the_real_record_preferences_and_folders_undo_exactly(real):
    hid = S.create(real, folder=r"C:\Clients\Hartleys", name="Hartleys")["client"]["id"]
    S.create(real, folder=r"C:\Clients\Smith", name="Smith")
    turn = W.turn_wall(real.engine, r"C:\Clients\Hartleys\2026", "they want cash basis from now on")
    assert turn.client is not None and turn.client.client_id == hid
    _c, _d, res = call(real, "client_preference_add", {"text": "Uses cash basis."}, turn=turn,
                       words="they want cash basis from now on")
    assert res.ok, res.error
    kept = S.preferences(real.engine, hid)
    assert [p["text"] for p in kept] == ["Uses cash basis."] and kept[0]["origin"] == "said"
    assert kept[0]["evidence"][0]["quote"] == "they want cash basis from now on"
    # Change, then Undo puts the old words back.
    words = "change their preference to accrual"
    _c, desc, res = call(real, "client_preference_change", {"text": "cash basis", "new_text": "Uses accrual."},
                         turn=W.turn_wall(real.engine, r"C:\Clients\Hartleys", words), words=words)
    assert res.ok, res.error
    assert [p["text"] for p in S.preferences(real.engine, hid)] == ["Uses accrual."]
    undo(real, desc)
    assert [p["text"] for p in S.preferences(real.engine, hid)] == ["Uses cash basis."]
    # Forget, then Undo keeps it again with its own words, origin and evidence.
    words = "forget that they use cash basis"
    _c, desc, res = call(real, "client_preference_forget", {"text": "cash basis"},
                         turn=W.turn_wall(real.engine, r"C:\Clients\Hartleys", words), words=words)
    assert res.ok and S.preferences(real.engine, hid) == []
    undo(real, desc)
    again = S.preferences(real.engine, hid)
    assert [(p["text"], p["origin"]) for p in again] == [("Uses cash basis.", "said")]
    assert again[0]["evidence"][0]["quote"] == "they want cash basis from now on"
    # A folder and a spelling added, then both put back in reverse.
    words = r"add D:\Scans\Hartleys to Hartleys, it is also called HRT"
    _c, desc, res = call(real, "client_update", {"add_folder": r"D:\Scans\Hartleys", "aliases": ["HRT"]},
                         turn=W.turn_wall(real.engine, r"C:\Clients\Hartleys", words), words=words)
    assert res.ok, res.error
    cmap = S.load_map(real.engine)
    assert {r.key for r in cmap.roots_of(hid)} == {_fk(r"C:\Clients\Hartleys"), _fk(r"D:\Scans\Hartleys")}
    assert cmap.names[hid][2] == (N.signature("HRT"),)
    undo(real, desc)
    cmap = S.load_map(real.engine)
    assert [r.key for r in cmap.roots_of(hid)] == [_fk(r"C:\Clients\Hartleys")] and cmap.names[hid][2] == ()


def test_on_the_real_record_a_waiting_names_card_confirms_keeps_and_undoes_both(real):
    wid = _waiting_row(real, "Lee Dental", [r"C:\Clients\Lee Dental"])
    check, desc, res = call(real, "client_preference_add", {"text": "Wants paper statements."},
                            words="Lee Dental wants paper statements")
    assert check.words == "Make Lee Dental a client and keep for Lee Dental: “Wants paper statements.”"
    assert res.ok, res.error
    assert _status(real, wid) == "confirmed"
    assert [p["text"] for p in S.preferences(real.engine, wid)] == ["Wants paper statements."]
    undo(real, desc)
    assert _status(real, wid) == "proposed" and S.preferences(real.engine, wid) == []


def test_on_the_real_record_client_list_is_walled(real):
    hid = S.create(real, folder=r"C:\Clients\Hartleys", name="Hartleys")["client"]["id"]
    S.create(real, folder=r"C:\Clients\Smith", name="Smith")
    _waiting_row(real, "Lee Dental", [r"C:\Clients\Lee Dental"])
    words = "who are my clients?"
    _c, _d, res = call(real, "client_list", {}, turn=W.turn_wall(real.engine, r"C:\Clients\Hartleys", words),
                       words=words)
    assert res.ok, res.error
    assert "Hartleys" in res.output and "Smith" not in res.output and "Lee Dental" not in res.output
    assert [c["id"] for c in res.data["clients"]] == [hid]
    _c, _d, res = call(real, "client_list", {}, turn=W.turn_wall(real.engine, r"D:\Work\Misc", words),
                       words=words)
    assert "Hartleys" in res.output and "Smith" in res.output and "Lee Dental" not in res.output


def test_on_the_real_record_a_provisional_client_folder_is_walled_too(real):
    """The independent review: a folder under Clients that is not a client
    yet (owner ``folder:``) is walled like a waiting home: client_list names
    no other client, and the writers refuse a change to another client."""
    hid = S.create(real, folder=r"C:\Clients\Hartleys", name="Hartleys")["client"]["id"]
    S.create(real, folder=r"C:\Clients\Smith", name="Smith")
    words = "who are my clients?"
    turn = W.turn_wall(real.engine, r"C:\Clients\Jones\2025", words)
    assert turn.ok and turn.owner == "folder:" + _fk(r"C:\Clients\Jones") and turn.client is None
    _c, _d, res = call(real, "client_list", {}, turn=turn, words=words)
    assert res.ok, res.error
    assert "Hartleys" not in res.output and "Smith" not in res.output, res.output
    assert "kept apart for one client" in res.output and "2 other clients" in res.output
    assert res.data["clients"] == []
    # A writer may not change another client from here, even when named.
    words = "Hartleys wants cash basis"
    turn = W.turn_wall(real.engine, r"C:\Clients\Jones\2025", words)
    _c, _d, res = call(real, "client_preference_add", {"client": "Hartleys", "text": "Uses cash basis."},
                       turn=turn, words=words)
    assert not res.ok and "Hartleys" not in (res.error or ""), res.error
    assert S.preferences(real.engine, hid) == []
    words = "Smith isn't a client"
    turn = W.turn_wall(real.engine, r"C:\Clients\Jones\2025", words)
    _c, _d, res = call(real, "client_answer", {"name": "Smith", "answer": "no"}, turn=turn, words=words)
    assert not res.ok, res.output
    # The folder's own name may still be made a client from inside it.
    words = r"Jones is a client, its folder is C:\Clients\Jones"
    turn = W.turn_wall(real.engine, r"C:\Clients\Jones\2025", words)
    _c, _d, res = call(real, "client_answer", {"name": "Jones", "answer": "yes", "folder": r"C:\Clients\Jones"},
                       turn=turn, words=words)
    assert res.ok, res.error


def test_on_the_real_record_a_preference_changed_in_a_local_only_chat_stays_off_the_cloud(real, tmp_path):
    """The independent review: the changed words are the owner's, said in a
    Local only chat. The row becomes Local only and its evidence is this
    message; the Undo puts the old flag, origin and evidence back."""
    folder = tmp_path / "Clients" / "Hartleys"
    folder.mkdir(parents=True)
    hid = S.create(real, folder=str(folder), name="Hartleys")["client"]["id"]
    S.keep_preference(real, hid, "Uses cash basis.", origin="typed")
    new = "Uses accrual; the divorce settlement is in the 2025 binder."
    words = "change their preference to: " + new
    with egress.local_only_scope(True):
        _c, desc, res = call(real, "client_preference_change", {"text": "cash basis", "new_text": new},
                             turn=W.turn_wall(real.engine, str(folder), words), words=words)
    assert res.ok, res.error
    pref = S.preferences(real.engine, hid)[0]
    assert pref["text"] == new and pref["local_only"] is True and pref["origin"] == "said"
    assert pref["evidence"][0]["quote"] == words[:160]
    cloud = CB.client_block(real, folder=str(folder), local=False)
    assert "divorce" not in cloud, cloud
    assert "divorce" in CB.client_block(real, folder=str(folder), local=True)
    undo(real, desc)
    pref = S.preferences(real.engine, hid)[0]
    assert (pref["text"], pref["local_only"], pref["origin"], pref["evidence"]) == (
        "Uses cash basis.", False, "typed", [])


def test_on_the_real_record_undo_of_a_folder_removal_puts_a_found_folder_back_as_found(real):
    """The independent review: Undo of "remove this folder" puts the folder
    back as it WAS (found stays found), so its Local only check still holds
    and the page never says "Added by you" for a folder the owner never
    added."""
    a, b = r"D:\Tax\2024\Hartleys", r"D:\Tax\2025\Hartleys"
    hid = _waiting_row(real, "Hartleys", [a, b])
    S.confirm(real, hid)
    assert {r["folder"]: r["how"] for r in S.folder_rows(real.engine, hid)} == {a: "found", b: "found"}
    words = "remove " + a + " from Hartleys"
    _c, desc, res = call(real, "client_update", {"remove_folder": a}, turn=W.turn_wall(real.engine, b, words),
                         words=words)
    assert res.ok, res.error
    assert [r["folder"] for r in S.folder_rows(real.engine, hid)] == [b]
    undo(real, desc)
    assert {r["folder"]: r["how"] for r in S.folder_rows(real.engine, hid)} == {a: "found", b: "found"}


def test_on_the_real_record_a_refusal_in_a_clients_chat_never_names_another_client(real):
    """The independent review: a folder or name rule that names ANOTHER
    client is told in a client's chat without that client's name or folder;
    the owner's own page route keeps the named sentence."""
    hid = S.create(real, folder=r"C:\Clients\Hartleys", name="Hartleys")["client"]["id"]
    S.create(real, folder=r"D:\Scans\Smith", name="Smith")
    words = r"add D:\Scans to Hartleys' folders"
    turn = W.turn_wall(real.engine, r"C:\Clients\Hartleys", words)
    _c, _d, res = call(real, "client_update", {"add_folder": r"D:\Scans"}, turn=turn, words=words)
    assert not res.ok and res.error == RC.OVERLAPS_ANOTHER, res.error
    assert "Smith" not in res.error
    words = "rename Hartleys to Smith"
    turn = W.turn_wall(real.engine, r"C:\Clients\Hartleys", words)
    _c, _d, res = call(real, "client_update", {"rename": "Smith"}, turn=turn, words=words)
    assert not res.ok and res.error == RC.OVERLAPS_ANOTHER, res.error
    # A chat with no client still hears which rule (it may be told names).
    with pytest.raises(C.ClientRefused) as got:
        S.add_folder(real, hid, r"D:\Scans")
    assert "Smith" in got.value.sentence


def test_on_the_real_record_a_declined_name_is_found_by_its_other_spellings(real):
    """The independent review: chat finds a name marked Not a client by any
    of its spellings, as the record does, so the card says what really
    happens and the Undo it promises can run (and never loses the No)."""
    hid = S.create(real, folder=r"C:\Clients\Hartleys", name="Hartleys")["client"]["id"]
    S.rename(real, hid, "Hartley Family Trust")
    S.keep_preference(real, hid, "Uses cash basis.", origin="typed")
    S.decline(real, hid)
    words = r"Hartleys is a client, its folder is D:\Clients\Hartleys 2027"
    check, desc, res = call(real, "client_answer",
                            {"name": "Hartleys", "answer": "yes", "folder": r"D:\Clients\Hartleys 2027"},
                            words=words)
    assert res.ok, res.error
    assert "again" in check.words and "Hartley Family Trust" in check.words, check.words
    assert res.data["client_id"] == hid and _status(real, hid) == "confirmed"
    assert desc is not None
    undo(real, desc)
    assert _status(real, hid) == "declined"
    # A never-confirmed name, renamed then declined: its Undo keeps the No.
    wid = _waiting_row(real, "Lee Dental", [r"C:\Clients\Lee Dental"])
    S.rename(real, wid, "Lee Family Dental")
    S.decline(real, wid)
    words = r"Lee Dental is a client, its folder is D:\Clients\Lee Dental"
    _c, desc, res = call(real, "client_answer",
                         {"name": "Lee Dental", "answer": "yes", "folder": r"D:\Clients\Lee Dental"}, words=words)
    assert res.ok, res.error
    assert res.data["client_id"] == wid and _status(real, wid) == "confirmed"
    undo(real, desc)
    assert _status(real, wid) == "declined"


def test_on_the_real_record_undo_of_not_a_client_says_what_it_could_not_put_back(real):
    """The independent review: Undo never says "as it was" over a folder the
    record dropped, and a Yes that took over a Not-a-client client's folder
    gives it back on its Undo instead of deleting it."""
    a, b = r"C:\Clients\Hartleys", r"D:\Scans\Hartleys"
    hid = S.create(real, folder=a, name="Hartleys")["client"]["id"]
    S.add_folder(real, hid, b)
    words = "Hartleys isn't a client"
    _c, desc, res = call(real, "client_answer", {"name": "Hartleys", "answer": "no"}, words=words)
    assert res.ok, res.error
    S.create(real, folder=b + r"\Trust", name="Hartley Trust")
    told = undo(real, desc)
    assert [r["folder"] for r in S.folder_rows(real.engine, hid)] == [a]
    assert "as it was" not in told and "not exactly" in told and b in told, told
    # A takeover: declined Hartleys' folder is made another client's, then
    # that Yes is undone: the folder is Hartleys' again, kept, unused.
    lee = r"C:\Clients\Lee"
    lid = S.create(real, folder=lee, name="Lee")["client"]["id"]
    S.decline(real, lid)
    words = r"HRT Holdings is a client, its folder is C:\Clients\Lee"
    _c, desc, res = call(real, "client_answer", {"name": "HRT Holdings", "answer": "yes", "folder": lee},
                         words=words)
    assert res.ok, res.error
    assert S.folder_rows(real.engine, lid) == []
    undo(real, desc)
    assert [(r["folder"], r["how"]) for r in S.folder_rows(real.engine, lid)] == [(lee, "added")]
    assert _status(real, lid) == "declined"


def test_on_the_real_record_a_cloud_client_list_asks_local_only_again(real):
    """The independent review: a client made in a Local only chat, or whose
    work is all Local only NOW, is not named to a cloud turn."""
    from iron_jarvis.workmap.models import WorkEntry

    words = r"Pereira Farms is a client, its folder is D:\Clients\Pereira Farms"
    with egress.local_only_scope(True):
        _c, _d, res = call(real, "client_answer",
                           {"name": "Pereira Farms", "answer": "yes", "folder": r"D:\Clients\Pereira Farms"},
                           words=words)
    assert res.ok, res.error
    home = r"C:\Clients\Hartleys"
    hid = _waiting_row(real, "Hartleys", [home])
    S.confirm(real, hid)
    with session_scope(real.engine) as db:
        for i in range(2):
            db.add(WorkEntry(id=f"chat:t{i}", kind="chat", ref=f"t{i}", title="x", folder=home,
                             folder_key=_fk(home), local_only=True))
        db.commit()
    S.create(real, folder=r"C:\Clients\Smith", name="Smith")
    words = "who are my clients?"
    _c, _d, res = call(real, "client_list", {}, words=words)
    assert res.ok, res.error
    assert "Pereira" not in res.output and "Hartleys" not in res.output, res.output
    assert "Smith" in res.output and "2 more clients are kept on this computer" in res.output
    # The work in Hartleys' folder is shared again: named again.
    with session_scope(real.engine) as db:
        row = db.get(WorkEntry, "chat:t0")
        row.local_only = False
        db.add(row)
        db.commit()
    _c, _d, res = call(real, "client_list", {}, words=words)
    assert "Hartleys" in res.output and "Pereira" not in res.output, res.output


def test_on_the_real_record_a_yes_after_a_no_that_fails_halfway_says_so(real, monkeypatch):
    """The independent review: "actually Lee Dental is a client" undoes the
    No and then says Yes, as two recorded steps. When the Yes cannot land,
    the reply says the No was undone (never "Nothing was changed") and its
    Undo puts the No back."""
    wid = _waiting_row(real, "Lee Dental", [r"C:\Clients\Lee Dental"])
    S.decline(real, wid)

    def locked(*a, **k):
        raise C.ClientsUnreadable()

    monkeypatch.setattr(S, "confirm", locked)
    words = "actually Lee Dental is a client"
    _c, desc, res = call(real, "client_answer", {"name": "Lee Dental", "answer": "yes"}, words=words)
    assert "Nothing was changed." not in (res.output or "") + (res.error or ""), (res.output, res.error)
    assert "waits for your answer again" in (res.output or ""), res.output
    assert _status(real, wid) == "proposed"
    monkeypatch.undo()
    assert desc is not None
    undo(real, desc)
    assert _status(real, wid) == "declined"


def test_on_the_real_record_a_yes_that_links_no_folder_never_says_chats_are_told(real):
    """The independent review: a Yes from chat whose every home is another
    client's says the client has no folder yet, never "chats in its folders
    are told it"."""
    home = r"C:\Work\Hartleys"
    wid = _waiting_row(real, "Hartleys", [home])
    S.create(real, folder=home + "/Trust", name="Hartleys Trust")  # inside the home: it is skipped
    assert _status(real, wid) == "proposed"
    words = "Hartleys wants cash basis"
    _c, _d, res = call(real, "client_preference_add", {"client": "Hartleys", "text": "Uses cash basis."},
                       words=words)
    assert res.ok, res.error
    assert "are told it" not in res.output and "no folder yet" in res.output, res.output
