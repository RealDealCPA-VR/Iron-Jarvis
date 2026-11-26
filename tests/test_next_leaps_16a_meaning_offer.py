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
    # The GET's own look runs here, never on a stray thread mid-test.
    monkeypatch.setattr(M, "look_soon", lambda _d, every=False: False)
    from iron_jarvis.daemon.routes.settings import config_writer

    config_writer(d)
    return SimpleNamespace(app=app, d=d, p=app.state.platform, client=TestClient(app), targets=targets,
                           models=models, asked=asked, probes=probes, memory=memory, made=made, tmp=tmp_path,
                           monkeypatch=monkeypatch)


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


def _ctx(s, *, cannot_ask: bool = False):
    from iron_jarvis.tools.base import ToolContext

    return ToolContext(workspace=s.tmp, session_id="chat", agent_run_id="chat", config=s.p.config,
                       event_bus=s.p.event_bus, engine=s.p.engine, cannot_ask=cannot_ask)


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
    assert bad["works"] is False and "TimeoutError" in bad["why"]
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
    assert lan["cost_words"].endswith("Already on that computer, so nothing is added here.")

    res = _choose(s, M.choice_id(M.KIND_OPENAI, "text-embedding-3-small", "https://embed.example.com"))
    assert res.status_code == 400 and M.OFF_NETWORK in res.json()["detail"]
    assert M.state(s.p) == M.OFF and s.p.embedder.model == "mock"


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
    assert f"{MODEL} did not answer on this PC" in res.json()["detail"]
    assert M.state(s.p) == M.OFF and s.p.embedder.model == "mock"
    assert "value" not in M.read(s.p.config.home)


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
    # The POST lane draws no card: it never uses the offer up.
    res = await tool.execute({"query": "shipping", "mode": "semantic"}, _ctx(s, cannot_ask=True))
    assert res.ok and res.data["words_only"] is True and res.data["meaning_offer"] is False
    assert M.state(s.p) == M.OFF
    first = await tool.execute({"query": "shipping", "mode": "semantic"}, _ctx(s))
    assert first.data["meaning_offer"] is True and "A card under this reply offers" in first.output
    again = await tool.execute({"query": "shipping", "mode": "semantic"}, _ctx(s))
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
    M.update(home, value=CHOICE)
    on = M.boot_embedder(cfg, reachable=lambda _u: True)
    assert isinstance(on.current(), OllamaEmbedder) and on.model == MODEL and on.held == ""
    down = M.boot_embedder(cfg, reachable=lambda _u: False)
    assert isinstance(down.current(), MockEmbedder) and down.held == CHOICE
    M.update(home, value=M.choice_id(M.KIND_OLLAMA, MODEL, "https://ollama.example.com"))
    far = M.boot_embedder(cfg, reachable=lambda _u: True)
    assert isinstance(far.current(), MockEmbedder)  # a choice off this network never loads


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
