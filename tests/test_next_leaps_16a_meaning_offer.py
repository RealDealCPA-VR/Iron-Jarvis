"""Next leaps 16-a (build-out plan R2.11, idea 16): search by meaning, offered
with what it costs here, with a search model the owner already has.

What must hold (docs/next-leaps/04-a-memory-that-compounds.md, phase 16-a):

* the state never reaches ``on`` without a Yes (a press, or chat's card);
* the cost comes from the probe (injected here: the suite has no network),
  and a figure the server did not report is shown as not measured and is
  never negligible (D-16.1);
* a server off this network is never listed, and choosing one is refused;
* choosing switches the live search model with no restart, and the
  persistent cache keeps the two models' vectors apart;
* a Local only turn still refuses a server off this network;
* the re-read pass upgrades the rows the old model wrote, and is bounded;
* Not now is final; the Undo of a change puts it back;
* done: on a PC with Ollama and a search model already pulled, ONE press
  turns it on, and "the angry email about shipping" finds a file that shares
  no word with it.
"""

from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ProjectKnowledge
from iron_jarvis.daemon.app import create_app
from iron_jarvis.memory import meaning as M
from iron_jarvis.memory.embeddings import (
    EmbedderError,
    MockEmbedder,
    OllamaEmbedder,
    OpenAICompatEmbedder,
)
from iron_jarvis.memory.embedding_cache import EmbeddingRecord
from iron_jarvis.memory.models import MemoryRecord
from iron_jarvis.memory.retrieval import decode_embedding, encode_embedding

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(240)]

WHERE = "http://127.0.0.1:11434"
MODEL = "nomic-embed-text:latest"
CHOICE = M.choice_id(M.KIND_OLLAMA, MODEL, WHERE)
MB = 1024 * 1024
#: The real one, before the scene fixture records the looks a page asks for.
REAL_LOOK_SOON = M.look_soon


class Meaningful:
    """A stand-in SEARCH MODEL: it maps words to what they are about, so two
    texts that share no word but mean the same land together. Text only goes
    into ``calls``; nothing leaves the test."""

    CONCEPTS = {
        "angry": 0, "furious": 0, "upset": 0,
        "shipping": 1, "parcel": 1, "delivery": 1, "courier": 1,
        "late": 2, "delayed": 2,
        "bread": 3, "flour": 3, "bake": 3,
        "email": 4, "message": 4,
    }

    def __init__(self, model: str = MODEL, where: str = WHERE) -> None:
        self.model = model
        self.base_url = where
        self.calls: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        v = [0.0] * 8
        for w in re.findall(r"[a-z]+", text.lower()):
            if w in self.CONCEPTS:
                v[self.CONCEPTS[w]] += 1.0
        v[7] = 0.001  # never a zero vector
        return v


class Broken:
    model = MODEL
    base_url = WHERE

    def embed(self, text: str) -> list[float]:
        raise EmbedderError("connection refused")


@pytest.fixture()
def scene(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    d = app.state.d
    made: list[Meaningful] = []

    def build(kind, model, where, **kw):
        base = Meaningful(model, where)
        made.append(base)
        return base

    monkeypatch.setattr(M, "build_base", build)
    targets = [SimpleNamespace(id="ollama", label="Ollama on this PC", kind="ollama", base_url=WHERE,
                               this_pc=True, listed=())]
    models = {WHERE: [{"id": MODEL, "size_bytes": 274_000_000}, {"id": "llama3.2:3b", "size_bytes": 2_000_000_000}]}
    asked: list[str] = []

    def models_on(_d, t):
        asked.append(t.base_url)
        return list(models.get(t.base_url, [])), ""

    monkeypatch.setattr(M, "TARGETS", lambda _d: list(targets))
    monkeypatch.setattr(M, "MODELS_ON", models_on)
    probes: list[str] = []
    memory = {"bytes": 300 * MB}

    def probe(cand, headers, budget):
        probes.append(cand["choice"])
        return {"works": True, "why": "", "memory_bytes": memory["bytes"]}

    monkeypatch.setattr(M, "PROBE", probe)
    # The GET's own look runs here, never on a stray thread mid-test: the
    # looks a page asked for are recorded.
    looks: list[dict] = []
    monkeypatch.setattr(M, "look_soon", lambda _d, **kw: looks.append(kw) or False)
    from iron_jarvis.daemon.routes.settings import config_writer

    config_writer(d)
    return SimpleNamespace(app=app, d=d, p=app.state.platform, client=TestClient(app), targets=targets,
                           models=models, asked=asked, probes=probes, memory=memory, made=made, tmp=tmp_path,
                           monkeypatch=monkeypatch, looks=looks)


def _view(s) -> dict:
    res = s.client.get("/memory/meaning")
    assert res.status_code == 200, res.text
    return res.json()


def _inbox_meaning(s) -> list[dict]:
    res = s.client.get("/improvements")
    assert res.status_code == 200, res.text
    return [i for i in res.json()["items"] if i["kind"] == "meaning_search"]


def _choose(s, choice: str = CHOICE):
    return s.client.post("/memory/meaning/choose", json={"choice": choice})


def _files(s):
    folder = s.tmp / "mail"
    folder.mkdir()
    (folder / "complaint.txt").write_text("Customer furious: parcel delivery late again.\n", encoding="utf-8")
    (folder / "recipe.txt").write_text("Bake bread with flour and water.\n", encoding="utf-8")
    s.p.config.search_roots = [str(folder)]
    return folder


def _semantic(s, query: str):
    from iron_jarvis.filesearch.service import SearchNotes

    notes = SearchNotes()
    hits = s.p.filesearch.search(query, mode="semantic", notes=notes)
    return hits, notes


def _ctx(s, *, cannot_ask: bool = False, chat_page: bool = False, session: str = "chat"):
    from iron_jarvis.tools.base import ToolContext

    return ToolContext(workspace=s.tmp, session_id=session, agent_run_id=session, config=s.p.config,
                       event_bus=s.p.event_bus, engine=s.p.engine, cannot_ask=cannot_ask, chat_page=chat_page)


# ---- never on without a Yes -------------------------------------------------
def test_nothing_turns_it_on_without_a_yes(scene):
    s = scene
    assert M.state(s.p) == M.OFF and M.setting_value(s.p) is None
    M.look(s.d)
    view = _view(s)
    assert view["state"] == "off" and view["offer"] is True and view["on"] is False
    assert view["words"] == M.OFF_WORDS
    assert [c["model"] for c in view["candidates"]] == [MODEL]  # the chat model is not a search model
    # The daemon's own loop looks, measures and offers; it never turns it on.
    for _ in range(3):
        M.one_pass(s.d)
    assert M.mark_offered(s.p) is True
    assert M.mark_offered(s.p) is False  # the reply card is drawn once, ever
    assert M.state(s.p) == M.OFFERED
    assert s.p.embedder.model == "mock" and s.p.filesearch.words_only is True
    assert M.setting_value(s.p) is None
    values = s.client.get("/settings/values").json()["values"]
    assert values["meaning_search"] is None
    # One suggestion on Improvements, with its cost, its own routes and the
    # setting as "Use another model".
    (it,) = _inbox_meaning(s)
    assert it["title"] == M.OFF_WORDS
    assert it["evidence"][0] == "Uses about 300 MB of memory while it works. Already on your disk."
    assert (it["accept"]["label"], it["accept"]["path"], it["accept"]["body"]) == (
        "Turn it on", "/memory/meaning/choose", {"choice": CHOICE})
    assert (it["decline"]["label"], it["decline"]["path"]) == ("Not now", "/memory/meaning/decline")
    assert it["home"] == {"href": "/settings?focus=meaning_search", "label": "Use another model"}


# ---- the cost ---------------------------------------------------------------
def test_the_cost_is_measured_and_a_missing_figure_is_never_negligible(scene):
    s = scene
    M.look(s.d)
    (cand,) = _view(s)["candidates"]
    assert cand["cost_words"] == "Uses about 300 MB of memory while it works. Already on your disk."
    assert cand["cost"]["memory_measured"] is True and cand["cost"]["disk_added_bytes"] == 0
    assert s.probes == [CHOICE]  # measured once, kept per model
    M.look(s.d)
    assert s.probes == [CHOICE]

    # A server that reports no memory figure: said, never guessed.
    s.memory["bytes"] = None
    cost = M.measure({"choice": "x", "kind": "openai", "model": "m", "where": WHERE, "size_bytes": 274_000_000})
    assert cost["memory_measured"] is False and cost["memory_bytes"] is None
    words = M.cost_words(cost, {"size_bytes": 274_000_000, "this_pc": True})
    assert words.startswith("Not measured: models like this usually need about their file size in memory")
    assert M.negligible(cost, 64 * 1024 * MB) is False

    # A probe that fails is a cost that says so; measuring never raises.
    def boom(*_a):
        raise TimeoutError()

    bad = M.measure({"choice": "x", "kind": "ollama", "model": "m", "where": WHERE}, probe=boom)
    assert bad["works"] is False and bad["why"] == M.WHY_NO_ANSWER  # plain words, never "TimeoutError"
    assert M.cost_words(bad) == "It did not answer here."
    assert M.negligible(bad, 64 * 1024 * MB) is False


@pytest.mark.parametrize("mem,ram,disk,unloads,want", [
    (150 * MB, 16 * 1024 * MB, 0, True, True),          # at the limit, on 16 GB
    (150 * MB + 1, 16 * 1024 * MB, 0, True, False),     # over 150 MB
    (100 * MB, 4 * 1024 * MB, 0, True, False),          # 2% of 4 GB is about 82 MB
    (80 * MB, 4 * 1024 * MB, 0, True, True),
    (150 * MB, 8 * 1024 * MB, 0, True, True),           # 2% of 8 GB is about 164 MB
    (150 * MB, 64 * 1024 * MB, 100 * MB + 1, True, False),  # adds more than 100 MB of disk
    (150 * MB, 64 * 1024 * MB, 0, False, False),        # stays loaded when idle
    (150 * MB, None, 0, True, False),                   # this PC's memory not known
])
def test_negligible_at_its_edges(mem, ram, disk, unloads, want):
    cost = {"works": True, "memory_bytes": mem, "memory_measured": True, "disk_added_bytes": disk, "unloads": unloads}
    assert M.negligible(cost, ram) is want


# ---- this network only ------------------------------------------------------
def test_a_server_off_this_network_is_never_offered_or_used(scene):
    s = scene
    s.targets.append(SimpleNamespace(id="cloud", label="Cloud box", kind="openai",
                                     base_url="https://embed.example.com", this_pc=False, listed=()))
    s.targets.append(SimpleNamespace(id="lan", label="Studio PC", kind="lmstudio",
                                     base_url="http://192.168.1.40:1234", this_pc=False, listed=()))
    s.models["https://embed.example.com"] = [{"id": "text-embedding-3-small"}]
    s.models["http://192.168.1.40:1234"] = [{"id": "text-embedding-nomic-embed-text-v1.5"}]
    M.look(s.d, every=True)
    view = _view(s)
    assert {c["server"] for c in view["candidates"]} == {"Ollama on this PC", "Studio PC"}
    assert "https://embed.example.com" not in s.asked  # not even asked what it has
    lan = next(c for c in view["candidates"] if c["server"] == "Studio PC")
    assert lan["where_words"] == "on Studio PC (your network)"
    # Its memory figure is that computer's, said so, and never weighed
    # against this PC's memory (D-16.1).
    assert lan["cost_words"] == ("Uses about 300 MB of memory on that computer while it works. "
                                 "Already on that computer, so nothing is added here.")
    assert lan["negligible"] is False
    small = {"works": True, "memory_bytes": 50 * MB, "memory_measured": True, "disk_added_bytes": 0, "unloads": True}
    M.update(s.p.config.home, costs={**M.read(s.p.config.home)["costs"], lan["choice"]: small})
    assert next(c for c in _view(s)["candidates"] if c["server"] == "Studio PC")["negligible"] is False

    res = _choose(s, M.choice_id(M.KIND_OPENAI, "text-embedding-3-small", "https://embed.example.com"))
    assert res.status_code == 400 and M.OFF_NETWORK in res.json()["detail"]
    assert M.state(s.p) == M.OFF and s.p.embedder.model == "mock"
    # An address on this network that is none of the owner's model servers
    # (one named in a document, say) is refused too, before any probe.
    res = _choose(s, M.choice_id(M.KIND_OLLAMA, MODEL, "http://192.168.1.99:11434"))
    assert res.status_code == 400 and M.NOT_A_SERVER in res.json()["detail"]
    res = _choose(s, M.choice_id(M.KIND_OLLAMA, MODEL, "http://localhost:9999"))
    assert res.status_code == 400 and M.NOT_A_SERVER in res.json()["detail"]
    assert M.state(s.p) == M.OFF and s.p.embedder.model == "mock" and len(s.made) == 0
    # Ollama on this PC is one server however its address is written (a
    # choice set up before may say "localhost").
    res = _choose(s, M.choice_id(M.KIND_OLLAMA, MODEL, "http://localhost:11434"))
    assert res.status_code == 200, res.text
    assert M.state(s.p) == M.ON and s.p.embedder.model == MODEL


def test_a_local_only_turn_refuses_a_server_off_this_network():
    from iron_jarvis.providers.egress import local_only_scope

    class Recorder:
        def __init__(self):
            self.calls = []

        def post(self, url, **kw):
            self.calls.append(url)
            return SimpleNamespace(status_code=200, json=lambda: {"embedding": [1.0], "data": [{"embedding": [1.0]}]})

    far, near = Recorder(), Recorder()
    with local_only_scope(True):
        for emb in (OllamaEmbedder("https://embed.example.com", http=far),
                    OpenAICompatEmbedder("https://embed.example.com/v1", "m", http=far)):
            with pytest.raises(EmbedderError, match="local only"):
                emb.embed("the client's question")
        assert far.calls == []
        assert OpenAICompatEmbedder("http://192.168.1.40:1234/v1", "m", http=near).embed("q") == [1.0]
    assert near.calls == ["http://192.168.1.40:1234/v1/embeddings"]


# ---- one press, at once -----------------------------------------------------
def test_one_press_turns_it_on_at_once_and_finds_by_meaning(scene):
    """The phase's done line."""
    s = scene
    _files(s)
    query = "the angry email about shipping"
    _hits, notes = _semantic(s, query)
    assert notes.words_only is True
    M.look(s.d)
    res = _choose(s)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["view"]["on"] is True and body["note"] == f"Search by meaning: on, with {MODEL} on this PC."
    assert body["action_id"], "the change is in the ledger, with its Undo"
    # No restart: the same embedder every consumer holds answers with it now.
    assert s.p.embedder.model == MODEL and s.p.filesearch.words_only is False
    hits, notes = _semantic(s, query)
    assert notes.words_only is False and notes.indexing is False
    assert hits and hits[0]["path"].endswith("complaint.txt"), hits
    words = set(re.findall(r"[a-z]+", query))
    assert not words & set(re.findall(r"[a-z]+", "customer furious: parcel delivery late again."))
    # The cache keeps the two models apart.
    with session_scope(s.p.engine) as db:
        seen = {r.model for r in db.exec(select(EmbeddingRecord))}
    assert {"mock", MODEL} <= seen


def test_a_model_that_does_not_answer_is_refused_in_words_and_nothing_changes(scene):
    s = scene
    M.look(s.d)
    s.monkeypatch.setattr(M, "build_base", lambda *a, **k: Broken())
    res = _choose(s)
    assert res.status_code == 400
    assert f"{MODEL} did not answer on this PC, so search by meaning was not changed." in res.json()["detail"]
    assert M.state(s.p) == M.OFF and s.p.embedder.model == "mock"
    assert "value" not in M.read(s.p.config.home)
    # Review note: plain words on screen, never a transport error or a code.
    for detail, said in (
        ("ollama embeddings request failed: [WinError 10061] No connection could be made", "did not answer"),
        ("ollama embeddings returned HTTP 500", "could not be used on this PC (it refused the request)"),
        ("ollama embeddings: missing/empty 'embedding'", "could not be used on this PC (its answer could not be used)"),
    ):
        class Says(Broken):
            def embed(self, text, _detail=detail):
                raise EmbedderError(_detail)

        s.monkeypatch.setattr(M, "build_base", lambda *a, **k: Says())
        words = _choose(s).json()["detail"]
        assert said in words, words
        assert not re.search(r"WinError|HTTP|500|embedding'|ollama embeddings", words), words


def test_undo_puts_it_back_and_not_now_is_final(scene):
    s = scene
    M.look(s.d)
    assert _choose(s).status_code == 200
    w = s.p.config_writer
    w.restore({"meaning_search": None})
    assert M.state(s.p) in (M.OFF, M.OFFERED) and s.p.embedder.model == "mock"
    w.restore({"meaning_search": CHOICE})
    assert M.state(s.p) == M.ON and s.p.embedder.model == MODEL

    res = s.client.post("/memory/meaning/off")
    assert res.status_code == 200 and res.json()["view"]["state"] == "declined"
    assert s.p.embedder.model == "mock"

    # Not now: answered, never offered again (Improvements, the reply card).
    w.restore({"meaning_search": None})
    assert _inbox_meaning(s)
    assert s.client.post("/memory/meaning/decline").status_code == 200
    assert _view(s)["offer"] is False and _inbox_meaning(s) == []
    assert M.mark_offered(s.p) is False
    assert s.client.get("/settings/values").json()["values"]["meaning_search"] == ""
    # The setting itself can still turn it on (opening the setting).
    assert _choose(s).status_code == 200 and M.state(s.p) == M.ON


# ---- the re-read pass -------------------------------------------------------
def test_the_reread_pass_upgrades_rows_and_is_bounded(scene):
    s = scene
    mock = MockEmbedder()
    with session_scope(s.p.engine) as db:
        for n in range(250):
            text = f"memory number {n} about a late parcel"
            db.add(MemoryRecord(layer="user", key=f"k{n}", text=text,
                                embedding_json=encode_embedding(mock.embed(text), "mock")))
        for n in range(3):
            db.add(ProjectKnowledge(project_id="p1", name=f"n{n}", text=f"courier note {n}",
                                    embedding_json=json.dumps(mock.embed(f"courier note {n}"))))
        db.commit()
    assert M.reread_pass(s.p)["skipped"] == "off"  # off: nothing is read again
    M.look(s.d)
    assert _choose(s).status_code == 200
    first = M.reread_pass(s.p)
    assert first == {"read": 200, "more": True}
    second = M.reread_pass(s.p)
    assert second == {"read": 53, "more": False}  # the last 50, then the 3 knowledge rows
    assert M.reread_pass(s.p) == {"read": 0, "more": False}
    with session_scope(s.p.engine) as db:
        models = {decode_embedding(r.embedding_json)[1] for r in db.exec(select(MemoryRecord))}
        knowledge = [json.loads(r.embedding_json) for r in db.exec(select(ProjectKnowledge))]
    assert models == {MODEL}
    assert all(len(v) == 8 for v in knowledge)  # the new model's vectors
    # Recall now ranks by meaning.
    hits = s.p.memory.retriever.search("a delayed courier", k=3)
    assert hits and hits[0][1] > 0.5
    # Only with a model on this network: a base that would send text out
    # reads nothing again (it cannot be chosen; this is the second line).
    with session_scope(s.p.engine) as db:
        db.add(MemoryRecord(layer="user", key="late", text="one more late parcel",
                            embedding_json=encode_embedding(mock.embed("x"), "mock")))
        db.commit()
    M.switch_of(s.p).switch(Meaningful(MODEL, "https://far.example.com"))
    assert M.reread_pass(s.p) == {"skipped": "not on this network", "read": 0, "more": False}


# ---- the card under a reply -------------------------------------------------
async def test_a_by_meaning_search_that_matched_words_carries_the_offer_once(scene):
    from iron_jarvis.daemon.chat_turn import tool_frame_data
    from iron_jarvis.daemon.durable_turns import reply_line
    from iron_jarvis.filesearch.tools import FileSearchTool

    s = scene
    _files(s)
    tool = FileSearchTool(s.p.filesearch)
    # The POST lane, a Build session and a Build pane draw no card: they
    # never use the offer up (each lane's own flag: the lane tests below).
    for ctx in (_ctx(s, cannot_ask=True), _ctx(s, session="build:chat_x"), _ctx(s)):
        res = await tool.execute({"query": "shipping", "mode": "semantic"}, ctx)
        assert res.ok and res.data["words_only"] is True and res.data["meaning_offer"] is False
        assert "A card under this reply" not in res.output
    assert M.state(s.p) == M.OFF
    first = await tool.execute({"query": "shipping", "mode": "semantic"}, _ctx(s, chat_page=True))
    assert first.data["meaning_offer"] is True and "A card under this reply offers" in first.output
    again = await tool.execute({"query": "shipping", "mode": "semantic"}, _ctx(s, chat_page=True))
    assert again.data["meaning_offer"] is False and "A card under this reply" not in again.output
    # The frame carries it from the step's own result; every other frame is as before.
    assert tool_frame_data("file_search", first, True) == {"data": {"meaning_offer": True}}
    assert tool_frame_data("file_search", again, True) == {}
    assert tool_frame_data("file_search", first, False) == {}
    assert tool_frame_data("read_file", first, True) == {}
    # A reply written home keeps it too.
    frames = [
        ("tool_call", json.dumps({"name": "file_search", "status": "finished", "ok": True,
                                  "data": {"meaning_offer": True}})),
        ("token", json.dumps({"text": "Here is what I found."})),
        ("done", json.dumps({"reply": "Here is what I found."})),
    ]
    assert reply_line("t1", frames, "done")["meaningOffer"] is True
    assert "meaningOffer" not in reply_line("t2", frames[1:], "done")


# ---- chat reaches it --------------------------------------------------------
async def test_chat_turns_it_on_through_the_setting_with_the_cost_on_the_card(scene):
    from iron_jarvis.settings.tools import ConfigChangeTool, ConfigSetTool

    s = scene
    M.look(s.d)
    tool = ConfigChangeTool(s.p)
    check = await tool.spend_check({"key": "meaning_search", "value": "on"}, _ctx(s))
    assert check.ask is True
    assert check.words == (f"Turn on search by meaning with {MODEL} on this PC. "
                           "Uses about 300 MB of memory while it works. Already on your disk.")
    off = await tool.spend_check({"key": "meaning_search", "value": False}, _ctx(s))
    assert off.ask is False  # turning it off needs no card (it has an Undo)
    # The allow-tier tool is refused and names the right one.
    wrong = await ConfigSetTool(s.p).execute({"key": "meaning_search", "value": "on"}, _ctx(s))
    assert not wrong.ok and "config_change" in wrong.error
    done = await tool.execute({"key": "meaning_search", "value": "nomic-embed-text"}, _ctx(s))
    assert done.ok, done.error
    card = done.data["config_change"]
    assert card["new"] == CHOICE and card["new_label"] == f"{MODEL} on this PC" and card["old_label"] == "Not set"
    assert s.p.embedder.model == MODEL
    # A name it did not find is refused in words.
    nope = await tool.execute({"key": "meaning_search", "value": "made-up-embed"}, _ctx(s))
    assert not nope.ok and "did not find a search model called made-up-embed" in nope.error


# ---- start-up ---------------------------------------------------------------
def test_start_up_uses_the_choice_only_when_it_answers(tmp_path):
    home = tmp_path / "h"
    cfg = SimpleNamespace(home=home, embedder_provider="auto", embedder_model="nomic-embed-text",
                          ollama_base_url="https://ollama.example.com")
    # Never answered, "auto": a server off this network is never used.
    assert M.boot_embedder(cfg, reachable=lambda _u: True).current().__class__ is MockEmbedder
    cfg.ollama_base_url = "http://127.0.0.1:11434"
    legacy = M.boot_embedder(cfg, reachable=lambda _u: True)
    assert isinstance(legacy.current(), OllamaEmbedder)  # set up before: kept
    # Answered: the file decides, and "auto" no longer turns anything on.
    M.update(home, value="")
    assert isinstance(M.boot_embedder(cfg, reachable=lambda _u: True).current(), MockEmbedder)
    M.update(home, value=CHOICE, knowledge_read={MODEL: "pk_ffffffffffff"})
    on = M.boot_embedder(cfg, reachable=lambda _u: True)
    assert isinstance(on.current(), OllamaEmbedder) and on.model == MODEL and on.held == ""
    assert M.read(home)["knowledge_read"] == {MODEL: "pk_ffffffffffff"}  # its own walk goes on
    down = M.boot_embedder(cfg, reachable=lambda _u: False)
    assert isinstance(down.current(), MockEmbedder) and down.held == CHOICE
    # The stand-in serves this session and writes knowledge rows a later
    # session's model must read again: the walk starts over.
    assert M.read(home)["knowledge_read"] == {}
    M.update(home, value=M.choice_id(M.KIND_OLLAMA, MODEL, "https://ollama.example.com"))
    far = M.boot_embedder(cfg, reachable=lambda _u: True)
    assert isinstance(far.current(), MockEmbedder)  # a choice off this network never loads
    # Review note: a model server that is not Ollama needs its key, which
    # start-up never reads: it is HELD (reattach tries it), never loaded bare.
    lan = M.choice_id(M.KIND_OPENAI, "text-embedding-nomic-embed-text-v1.5", "http://192.168.1.40:1234")
    M.update(home, value=lan)
    keyed = M.boot_embedder(cfg, reachable=lambda _u: True)
    assert isinstance(keyed.current(), MockEmbedder) and keyed.held == lan


def _boot_platform(cfg, reachable: bool):
    emb = M.boot_embedder(cfg, reachable=lambda _u: reachable)
    return SimpleNamespace(config=cfg, embedder=emb)


def test_set_up_before_survives_a_slow_start_and_the_offer(tmp_path):
    """Review 2, finding 1: an install whose Ollama search model worked
    before this version. One start, Ollama has not answered the 2 s probe
    yet, and a by-meaning search shows the offer (``asked_at``). Only an
    ANSWER ends the earlier set-up: the next start that Ollama answers is on
    again, as it was, for someone who did nothing differently."""
    home = tmp_path / "h"
    cfg = SimpleNamespace(home=home, embedder_provider="auto", embedder_model="nomic-embed-text",
                          ollama_base_url="http://127.0.0.1:11434")
    legacy = M.choice_id(M.KIND_OLLAMA, "nomic-embed-text", "http://127.0.0.1:11434")
    up = _boot_platform(cfg, True)
    assert M.state(up) == M.ON and M.setting_value(up) == legacy
    down = _boot_platform(cfg, False)
    assert M.state(down) == M.OFF
    assert M.mark_offered(down) is True and M.state(down) == M.OFFERED
    again = _boot_platform(cfg, True)
    assert isinstance(again.embedder.current(), OllamaEmbedder)
    assert M.state(again) == M.ON and M.setting_value(again) == legacy
    view = M.view(SimpleNamespace(platform=again))
    assert view["how"] == "set up before" and view["offer"] is False
    assert M.mark_offered(again) is False  # the card is drawn once, ever
    # An Undo of a first answer puts "never answered" back: still set up before.
    M.update(home, value=None)
    assert isinstance(_boot_platform(cfg, True).embedder.current(), OllamaEmbedder)
    # An ANSWER ends it: Not now (or turned off) stays off at every start.
    M.update(home, value="")
    declined = _boot_platform(cfg, True)
    assert isinstance(declined.embedder.current(), MockEmbedder) and M.state(declined) == M.DECLINED


# ---- the loop's pacing ------------------------------------------------------
async def test_the_loop_is_paced_and_waits_while_a_reply_is_written(scene, monkeypatch):
    s = scene
    passes: list[int] = []
    naps: list[float] = []
    busy = iter([True, False, False])
    monkeypatch.setattr(M, "replying", lambda: next(busy, False))
    monkeypatch.setattr(M, "one_pass", lambda _d: passes.append(1) or {"more": len(passes) == 1})

    async def nap(sec):
        naps.append(sec)
        if len(naps) > 40:
            raise asyncio.CancelledError()

    ticks: list[bool] = []
    with pytest.raises(asyncio.CancelledError):
        await M.meaning_loop(s.d, lambda ok, exc: ticks.append(ok), sleep=nap, first_s=120.0, pace_s=30.0,
                             idle_s=3600.0, step_s=60.0)
    assert naps[0] == 120.0
    assert naps[1] == 30.0 and passes  # a reply was being written: no pass, a short wait
    # After the first pass (more rows waiting) the wait is short; then up to an hour, in steps.
    assert 30.0 in naps[2:3] and naps.count(60.0) >= 30
    assert ticks and all(ticks)


# ---- review round 1 ---------------------------------------------------------
def _live_calls(s, text: str) -> int:
    """How many times the search model in use was asked to read *text*."""
    base = M.switch_of(s.p).current()
    return sum(1 for t in getattr(base, "calls", []) if t == text)


def test_every_switch_starts_the_knowledge_walk_over(scene):
    """Review 1: project knowledge vectors name no model, so the walk's
    cursor holds only while ONE model was live. A note the stand-in wrote
    while search by meaning was off (an id that sorts BEFORE the cursor) is
    read again when it is turned on again; so is one read while a held model
    was standing in."""
    s = scene
    mock = MockEmbedder()
    home = s.p.config.home
    with session_scope(s.p.engine) as db:
        for n in range(3):
            db.add(ProjectKnowledge(project_id="p1", name=f"n{n}", text=f"courier note {n}",
                                    embedding_json=json.dumps(mock.embed(f"courier note {n}"))))
        db.commit()
    M.look(s.d)
    assert _choose(s).status_code == 200
    assert M.reread_pass(s.p) == {"read": 3, "more": False}
    assert M.read(home)["knowledge_read"], "the walk keeps its cursor"
    assert M.reread_pass(s.p) == {"read": 0, "more": False}  # walked once per model
    # Off: the stand-in writes a note whose id sorts before every other.
    assert s.client.post("/memory/meaning/off").status_code == 200
    with session_scope(s.p.engine) as db:
        db.add(ProjectKnowledge(id="pk_000000000000a", project_id="p1", name="new", text="parcel late",
                                embedding_json=json.dumps(mock.embed("parcel late"))))
        db.commit()
    assert len(mock.embed("x")) != 8
    assert _choose(s).status_code == 200  # on again, the same model
    assert M.reread_pass(s.p) == {"read": 4, "more": False}
    with session_scope(s.p.engine) as db:
        vec = json.loads(db.get(ProjectKnowledge, "pk_000000000000a").embedding_json)
    assert len(vec) == 8, "the stand-in's vector is still there"
    # A model held at start: the stand-in served until it answers again.
    M.update(home, knowledge_read={MODEL: "pk_ffffffffffff"})
    M.switch_of(s.p).switch(MockEmbedder(), held=CHOICE)
    assert M.reattach(s.d) is True
    assert M.read(home)["knowledge_read"] == {}


def test_the_offer_names_only_a_model_that_answered_here(scene):
    """Review 2: the first model found does not answer its probe. One look
    goes on to the next, so the offer names a model that answered, with its
    measured cost; a cost is "being measured" only while a look runs."""
    s = scene
    small = "all-minilm:latest"
    small_choice = M.choice_id(M.KIND_OLLAMA, small, WHERE)
    s.models[WHERE].insert(0, {"id": small, "size_bytes": 46_000_000})
    answers = {small: {"works": False, "why": M.WHY_REFUSED, "memory_bytes": None}}

    def probe(cand, headers, budget):
        s.probes.append(cand["choice"])
        return answers.get(cand["model"], {"works": True, "why": "", "memory_bytes": 300 * MB})

    s.monkeypatch.setattr(M, "PROBE", probe)
    # Bounded: one look measures at most LOOK_MEASURE_MAX models.
    s.monkeypatch.setattr(M, "LOOK_MEASURE_MAX", 1)
    M.look(s.d)
    assert s.probes == [small_choice]
    view = _view(s)
    assert view["offer"] is False and view["best"] is None and _inbox_meaning(s) == []
    assert [c["cost_words"] for c in view["candidates"]] == [
        "It could not be used here: it refused the request.", M.COST_NOT_YET]
    # The next look goes past the one that did not answer.
    s.monkeypatch.setattr(M, "LOOK_MEASURE_MAX", 5)
    M.look(s.d)
    assert s.probes == [small_choice, CHOICE]
    view = _view(s)
    assert [c["model"] for c in view["candidates"]] == [small, MODEL]
    assert view["offer"] is True and view["best"]["model"] == MODEL
    measured = "Uses about 300 MB of memory while it works. Already on your disk."
    assert view["best"]["cost_words"] == measured
    (it,) = _inbox_meaning(s)
    assert it["accept"]["body"] == {"choice": CHOICE} and it["evidence"][0] == measured
    # One look with nothing known measures until one answers, in one go.
    M.update(s.p.config.home, costs={})
    s.probes.clear()
    M.look(s.d)
    assert s.probes == [small_choice, CHOICE] and _view(s)["best"]["model"] == MODEL
    # A model found but not measured: never "being measured" unless a look runs.
    M.update(s.p.config.home, costs={})
    view = _view(s)
    assert view["looking"] is False and view["offer"] is False
    assert {c["cost_words"] for c in view["candidates"]} == {M.COST_NOT_YET}
    M._LOOKING.set()
    try:
        assert {c["cost_words"] for c in _view(s)["candidates"]} == {M.COST_MEASURING}
    finally:
        M._LOOKING.clear()
    # While a reply is written, the loop's look measures nothing.
    s.probes.clear()
    M.look(s.d, pause=lambda: True)
    assert s.probes == []
    s.monkeypatch.setattr(M, "replying", lambda: True)
    M.update(s.p.config.home, looked_at_s=0)  # a daily look is due
    assert M.one_pass(s.d)["looked"] is True and s.probes == []
    s.monkeypatch.setattr(M, "replying", lambda: False)
    # None answers: no offer.
    answers[MODEL] = {"works": False, "why": M.WHY_REFUSED, "memory_bytes": None}
    M.look(s.d)
    view = _view(s)
    assert view["offer"] is False and view["best"] is None and _inbox_meaning(s) == []


async def test_the_card_measures_a_model_the_look_did_not_list(scene):
    """Review 2: chat names a model on the owner's server that the last look
    did not list: the card still carries its MEASURED cost (never "being
    measured" with no look running). One on none of the owner's servers gets
    no cost card and is refused in words."""
    from iron_jarvis.settings.tools import ConfigChangeTool

    s = scene
    M.look(s.d)
    other = M.choice_id(M.KIND_OLLAMA, "mxbai-embed-large:latest", WHERE)
    tool = ConfigChangeTool(s.p)
    check = await tool.spend_check({"key": "meaning_search", "value": other}, _ctx(s))
    assert check.ask is True
    assert check.words == ("Turn on search by meaning with mxbai-embed-large:latest on this PC. "
                           "Uses about 300 MB of memory while it works. Already on your disk.")
    assert other in s.probes
    stray = M.choice_id(M.KIND_OLLAMA, MODEL, "http://192.168.1.99:11434")
    assert await tool.spend_check({"key": "meaning_search", "value": stray}, _ctx(s)) is None
    nope = await tool.execute({"key": "meaning_search", "value": stray}, _ctx(s))
    assert not nope.ok and M.NOT_A_SERVER in nope.error
    assert M.state(s.p) == M.OFF and s.p.embedder.model == "mock"


def test_the_reread_pass_stops_the_moment_a_reply_starts(scene, monkeypatch):
    """Review 3: the pass is paused before EVERY row while a chat reply is
    written (memory rows, then knowledge rows), says so, and goes on later
    with nothing read twice."""
    s = scene
    mock = MockEmbedder()
    texts = [f"memory {n} about a late parcel" for n in range(3)]
    notes = [f"courier note {n}" for n in range(3)]
    with session_scope(s.p.engine) as db:
        for n, text in enumerate(texts):
            db.add(MemoryRecord(layer="user", key=f"k{n}", text=text,
                                embedding_json=encode_embedding(mock.embed(text), "mock")))
        for n, text in enumerate(notes):
            db.add(ProjectKnowledge(project_id="p1", name=f"n{n}", text=text,
                                    embedding_json=json.dumps(mock.embed(text))))
        db.commit()
    M.look(s.d)
    assert _choose(s).status_code == 200
    checks = {"n": 0, "at": 0}

    def replying() -> bool:
        checks["n"] += 1
        return checks["n"] == checks["at"]

    monkeypatch.setattr(M, "replying", replying)
    checks.update(n=0, at=2)  # a reply starts after the first memory row
    assert M.reread_pass(s.p) == {"skipped": M.REPLY_PAUSE, "read": 1, "more": True}
    checks.update(n=0, at=4)  # ... and after the first knowledge row
    assert M.reread_pass(s.p) == {"skipped": M.REPLY_PAUSE, "read": 3, "more": True}
    checks.update(n=0, at=0)  # no reply: the rest
    assert M.reread_pass(s.p) == {"read": 2, "more": False}
    assert [_live_calls(s, t) for t in texts + notes] == [1] * 6
    with session_scope(s.p.engine) as db:
        assert {decode_embedding(r.embedding_json)[1] for r in db.exec(select(MemoryRecord))} == {MODEL}
        assert all(len(json.loads(r.embedding_json)) == 8 for r in db.exec(select(ProjectKnowledge)))


class Picky(Meaningful):
    """A search model that refuses some texts (an error, as a server that
    refuses over-long input does) while it still answers others."""

    def __init__(self, *a, refuse=lambda _t: False, **kw):
        super().__init__(*a, **kw)
        self.refuse = refuse

    def embed(self, text: str) -> list[float]:
        if self.refuse(text):
            self.calls.append(text)
            raise EmbedderError("the search model's server answered HTTP 500")
        return super().embed(text)


def test_a_row_the_model_refuses_is_stepped_past_never_blocking_the_rest(scene):
    """Review 2, finding 2: the newest memory row is one the model refuses.
    The pass steps past it (the model still answers its probe), reads every
    other row, then the project knowledge rows, and no later pass starts on
    it again. Text is clipped as knowledge text always was, so an over-long
    row is read. A model that does not answer at all ends the pass with no
    ``more`` (the loop's idle look tries again: never a request every 30 s)."""
    s = scene
    mock = MockEmbedder()
    from datetime import timedelta

    from iron_jarvis.core.ids import utcnow

    now = utcnow()
    long_text = "a late parcel " + "x" * 5000  # over 4000 characters: clipped, then read
    rows = [(f"short {n} about a late parcel", now - timedelta(minutes=10 + n)) for n in range(5)]
    rows += [("poison: refused", now), (long_text, now - timedelta(minutes=1))]
    with session_scope(s.p.engine) as db:
        for n, (text, at) in enumerate(rows):
            db.add(MemoryRecord(layer="user", key=f"k{n}", text=text, created_at=at,
                                embedding_json=encode_embedding(mock.embed(text), "mock")))
        for n in range(2):
            db.add(ProjectKnowledge(id=f"pk_00000000000{n}", project_id="p1", name=f"n{n}",
                                    text=("poison note" if n == 0 else "courier note"),
                                    embedding_json=json.dumps(mock.embed("courier note"))))
        db.commit()
    picky = Picky(refuse=lambda t: "poison" in t or len(t) > M.REREAD_TEXT_MAX)
    s.monkeypatch.setattr(M, "build_base", lambda *a, **k: picky)
    M.look(s.d)
    assert _choose(s).status_code == 200
    first = M.reread_pass(s.p)
    assert first == {"read": 7, "more": False, "failed": 2}, first  # 6 memory rows + 1 note; 2 stepped past
    assert sum(1 for t in picky.calls if t.startswith("poison")) == 2
    assert M.read(s.p.config.home)["knowledge_read"][MODEL] == "pk_000000000001"
    picky.calls.clear()
    assert M.reread_pass(s.p) == {"read": 0, "more": False}  # never starts on them again
    assert not any(t.startswith("poison") for t in picky.calls)
    with session_scope(s.p.engine) as db:
        made = {r.text[:20]: decode_embedding(r.embedding_json)[1] for r in db.exec(select(MemoryRecord))}
    assert made.pop("poison: refused") == "mock" and set(made.values()) == {MODEL}
    # A choice made again tries every row once more.
    assert M.read(s.p.config.home)["reread_failed"][MODEL]
    assert s.client.post("/memory/meaning/off").status_code == 200
    assert M.read(s.p.config.home)["reread_failed"] == {}
    # The model does not answer at all (its probe neither): the pass ends,
    # with nothing recorded as refused and no ``more``.
    assert _choose(s).status_code == 200
    picky.refuse = lambda _t: True
    assert M.reread_pass(s.p) == {"skipped": M.NO_ANSWER, "read": 0, "more": False}
    assert M.read(s.p.config.home)["reread_failed"] == {}


def _search_rounds():
    from iron_jarvis.providers.adapters.base import ToolCall

    return [[ToolCall(id="c1", name="file_search", arguments={"query": "shipping", "mode": "semantic"})]]


def _search_body(**extra) -> dict:
    return {"messages": [{"role": "user", "content": "find the shipping complaint by meaning"}],
            "tools": ["file_search"], "approval_mode": "yolo", **extra}


def _offer_frames(frames) -> list[dict]:
    return [d for e, d in frames if e == "tool_call" and d.get("name") == "file_search"
            and d.get("status") == "finished" and (d.get("data") or {}).get("meaning_offer") is True]


def _told(seen: dict) -> str:
    """Everything the model was sent, as one string."""
    return json.dumps([[str(getattr(m, "content", "")) for m in ms] for ms in seen["messages"]])


def test_only_the_chat_page_uses_the_offer_up_on_the_real_lanes(scene):
    """Review 4, through the real lanes: a Build pane's turn and the POST
    lane draw no card, so a words-only search there never uses the offer up
    and never tells the model a card is on screen; the main chat's does."""
    from tests.test_wave3_chat_lanes_v1311 import _post_turn, _scripted_router

    s = scene
    _files(s)
    for lane, extra in (("post", {}), ("stream", {"pane_id": "term_abc"})):
        seen = _scripted_router(s.p, s.monkeypatch, _search_rounds(), "Here is what I found.")
        _body, frames = _post_turn(s.client, lane, _search_body(**extra))
        assert seen["n"] == 2, lane  # it really searched (two model rounds)
        assert M.state(s.p) == M.OFF, lane
        assert _offer_frames(frames) == [], lane
        assert "matched words" in _told(seen) and "A card under this reply" not in _told(seen), lane
    # The main chat page: the offer is used up once, on that step's frame.
    main = _scripted_router(s.p, s.monkeypatch, _search_rounds(), "Here is what I found.")
    _body, frames = _post_turn(s.client, "stream", _search_body())
    assert M.state(s.p) == M.OFFERED and len(_offer_frames(frames)) == 1
    assert "A card under this reply" in _told(main)


async def test_a_build_session_turn_never_uses_the_offer_up(scene):
    """Review 4: a Build session's turn runs in the stream lane under its own
    session id and asks for the suggestion line, but draws no search-by-
    meaning card: the offer stays unused."""
    from iron_jarvis.daemon.chat_stream import stream_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody
    from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
    from tests.test_auto_approve_lanes_v14060 import _frames
    from tests.test_wave3_chat_lanes_v1311 import _scripted_router

    s = scene
    _files(s)

    async def turn(**kw) -> tuple[list, dict]:
        seen = _scripted_router(s.p, s.monkeypatch, _search_rounds(), "Here is what I found.")
        gen = await stream_chat_turn(s.p, BUILTIN_PERSONAS, ChatBody(**_search_body()), suggest_preferences=True,
                                     **kw)
        out = []
        async for chunk in gen:
            out.extend(_frames([chunk]))
        return out, seen

    frames, seen = await turn(session_id="build:chat_d0000000beef")
    assert seen["n"] == 2 and "matched words" in _told(seen), frames[-4:]
    assert M.state(s.p) == M.OFF and _offer_frames(frames) == []
    # The same turn in the chat itself does use it (the flag is the lane's).
    frames, seen = await turn()
    assert M.state(s.p) == M.OFFERED and len(_offer_frames(frames)) == 1


def test_a_held_model_is_never_called_off_in_the_search_note(scene):
    """Review note: the chosen model did not answer at start (held), so the
    stand-in serves. The step's note says search by meaning is ON and its
    model did not answer, never that it is off."""
    from iron_jarvis.filesearch.service import HELD_NOTE, WORD_ONLY_NOTE

    s = scene
    _files(s)
    M.update(s.p.config.home, value=CHOICE)
    M.switch_of(s.p).switch(MockEmbedder(), held=CHOICE)
    _hits, notes = _semantic(s, "shipping")
    assert notes.words_only is True and notes.held is True
    assert HELD_NOTE in notes.note() and WORD_ONLY_NOTE not in notes.note()
    assert "did not answer when Iron Jarvis started" in M.status_words(s.p)
    M.switch_of(s.p).switch(MockEmbedder())
    M.update(s.p.config.home, value="")
    _hits, notes = _semantic(s, "shipping")
    assert notes.held is False and WORD_ONLY_NOTE in notes.note()


# ---- review round 2 ---------------------------------------------------------
BIG = "mxbai-embed-large:latest"
BIG_CHOICE = M.choice_id(M.KIND_OLLAMA, BIG, WHERE)


def test_the_open_list_measures_every_model_and_the_row_stays_light(scene):
    """Review 2, finding 3 and note 7: a model the list shows with no cost
    yet is measured by the open list's own read (the page's re-read starts
    that look), a few a look, and a "did not answer" is tried again later.
    The row of someone who said Not now, or whose search by meaning is on,
    only finds: it never loads a model to measure it."""
    s = scene
    snow = M.choice_id(M.KIND_OLLAMA, "snowflake-arctic-embed:latest", WHERE)
    s.models[WHERE].append({"id": BIG, "size_bytes": 670_000_000})
    s.models[WHERE].append({"id": "snowflake-arctic-embed:latest", "size_bytes": 800_000_000})
    M.look(s.d)
    assert s.probes == [CHOICE]  # the first that answers ends the row's look
    view = _view(s)
    assert [c["works"] for c in view["candidates"]] == [True, None, None]
    assert s.looks == []  # looked just now: the row asks for nothing
    # The list is open (?all=1) and a cost is missing: its read starts a look
    # that measures every model, a few a look.
    assert s.client.get("/memory/meaning?all=1").status_code == 200
    assert s.looks == [{"every": True}]
    s.monkeypatch.setattr(M, "LOOK_EVERY_MAX", 1)
    s.probes.clear()
    M.look(s.d, every=True)
    assert s.probes == [BIG_CHOICE]
    s.looks.clear()
    s.client.get("/memory/meaning?all=1")
    assert s.looks == [{"every": True}]  # one still has no cost: the next read measures it
    M.look(s.d, every=True)
    assert s.probes == [BIG_CHOICE, snow]
    s.looks.clear()
    s.client.get("/memory/meaning?all=1")
    assert s.looks == []  # every cost measured, looked just now: no look after look
    # A model that did not answer is measured again after a while.
    costs = M.read(s.p.config.home)["costs"]
    costs[BIG_CHOICE] = {**costs[BIG_CHOICE], "works": False, "why": M.WHY_NO_ANSWER,
                         "measured_s": 0}
    M.update(s.p.config.home, costs=costs)
    s.client.get("/memory/meaning?all=1")
    assert s.looks == [{"every": True}]
    s.probes.clear()
    M.look(s.d, every=True)
    assert s.probes == [BIG_CHOICE] and _view(s)["candidates"][1]["works"] is True
    # The row's look when it is due: it measures while the offer is open...
    s.looks.clear()
    M.update(s.p.config.home, looked_at_s=0)
    _view(s)
    assert s.looks == [{"measure_any": True}]
    # ...and only finds after Not now (or while it is on).
    assert s.client.post("/memory/meaning/decline").status_code == 200
    M.update(s.p.config.home, looked_at_s=0, costs={})
    s.looks.clear()
    _view(s)
    assert s.looks == [{"measure_any": False}]
    s.probes.clear()
    M.look(s.d, measure_any=False)
    assert s.probes == [] and len(M.read(s.p.config.home)["found"]) == 3


def test_a_look_started_from_a_page_is_running_before_the_page_reads(scene):
    """Review 2, finding 3: the look a read starts is marked running BEFORE
    the read answers, so the page knows to read again; a second read while
    it runs starts none; it waits while a reply is written."""
    s = scene
    gate = __import__("threading").Event()
    seen: list[dict] = []

    def slow_look(_d, **kw):
        seen.append(kw)
        gate.wait(10)
        return {}

    s.monkeypatch.setattr(M, "_look", slow_look)
    try:
        assert REAL_LOOK_SOON(s.d, every=True) is True
        assert M._LOOKING.is_set()
        assert REAL_LOOK_SOON(s.d) is False
    finally:
        gate.set()
    # The look's thread lets go of the lock only after it clears the flag.
    assert M._LOOK_LOCK.acquire(timeout=10)
    M._LOOK_LOCK.release()
    assert not M._LOOKING.is_set()
    assert seen == [{"every": True, "measure_any": True, "pause": M.replying}]


def test_a_scratch_e2e_daemon_never_reaches_this_pcs_ollama():
    """Review 2, finding 4: the e2e scratch daemon's look for search models
    must never ask the developer's own Ollama (CLAUDE.md: tests never reach
    the owner's live services): its OLLAMA_HOST is a closed port."""
    from pathlib import Path

    config = (Path(__file__).resolve().parents[1] / "dashboard" / "playwright.config.ts").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    serve = config[config.index("ironjarvis serve"):]
    serve = serve[: serve.index("next dev")]
    assert 'OLLAMA_HOST: "http://127.0.0.1:1"' in serve


async def test_chat_lists_and_prices_the_search_models_found(scene):
    """Review 2, finding 5 (G9): "switch the search model" in chat. The
    settings list names every search model found, what each costs here and
    the value that chooses it; config_change with no value puts them on a
    pick card (never one that did not answer), with Off."""
    from iron_jarvis.settings.tools import ConfigChangeTool, ConfigListTool

    s = scene
    s.models[WHERE].append({"id": BIG, "size_bytes": 670_000_000})
    s.models[WHERE].append({"id": "all-minilm:latest", "size_bytes": 900_000_000})
    tool = ConfigChangeTool(s.p)
    # Before any look: no card, and the list says how to go on.
    assert tool.missing_details({"key": "meaning_search"}) == {}
    listed = await ConfigListTool(s.p).execute({"query": "search model"}, _ctx(s))
    assert "meaning_search" in listed.output and M.NOT_LOOKED_YET in listed.output
    M.look(s.d)
    costs = M.read(s.p.config.home)["costs"]
    minilm = M.choice_id(M.KIND_OLLAMA, "all-minilm:latest", WHERE)
    costs[minilm] = {"works": False, "why": M.WHY_NO_ANSWER, "measured_s": 9e12}
    M.update(s.p.config.home, costs=costs)
    listed = await ConfigListTool(s.p).execute({"query": "search model"}, _ctx(s))
    line = next(x for x in listed.output.splitlines() if "(meaning_search)" in x)
    assert (f"{MODEL} on this PC: Uses about 300 MB of memory while it works. Already on your disk. "
            f"(value: {CHOICE})") in line
    assert f"{BIG} on this PC: {M.COST_NOT_YET} (value: {BIG_CHOICE})" in line
    assert "all-minilm:latest on this PC: It did not answer here." in line
    card = tool.missing_details({"key": "meaning_search"})["value"]
    assert card["enum"] == [CHOICE, BIG_CHOICE, "off"]
    assert card["enumNames"][0] == f"{MODEL} on this PC: Uses about 300 MB of memory while it works. Already on your disk."
    assert card["enumNames"][-1] == M.CHAT_OFF[1]
    # The pick is a value the setting's own card then measures and asks for.
    check = await tool.spend_check({"key": "meaning_search", "value": BIG_CHOICE}, _ctx(s))
    assert check.ask is True and BIG_CHOICE in s.probes
