"""v1.497.0 — the check-again loop's gaps closed (the owner: "address those
risks and be sure none are left without regression and the same or better end
user experience").

What v1.495.0 left open, each pinned here:

1. ENGLISH ONLY. The readings (a file claimed saved or changed, a give-up, the
   user asking for a file, the user saying "no tools") now also read Spanish,
   French, German, Italian, Portuguese and Dutch (``core/claim_languages``),
   and a reply in a language none of them knows gets ONE side question that
   changes the reply only when the model answers with a call of its own tools.
2. THE BROWSER SIDEBAR had no loop. It has one now; its turn holds only
   browser tools, so only a give-up after real work is ever put to it.
3. A CLAIMED CHANGE with no tool that could make it was pushed anyway, and
   ended on a "Blocked:" line two rounds later. It is put only beside such a
   tool now (the note under the reply is unchanged).
4. A CODING SESSION WITH A TEAM had no check-again loop. It has one once the
   team's reviews are done; a lead's note says to hand each fix to a worker.

Driven through the REAL lanes and a real agent run where it matters; only the
model is scripted (the harness of ``tests/test_script_power_lanes_v1473``).
"""

# ruff: noqa: F811 — `desk` is a fixture imported from another test file
from __future__ import annotations

import asyncio
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from iron_jarvis.core import claim_languages as L
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon import until_done as U
from iron_jarvis.harness import give_up as G
from tests.test_script_power_lanes_v1473 import (  # noqa: F401 — `desk` is the fixture
    Model,
    _agent,
    _body,
    _call,
    _drive_stream,
    _post,
    _text_of,
    desk,
)

pytestmark = pytest.mark.timeout(300)

LANES = pytest.mark.parametrize("lane", ["stream", "post"])


# --------------------------------------------------------------------------- #
# 1. the readings, both ways
# --------------------------------------------------------------------------- #

SAVED_YES = [
    "Guardé el informe en resumen.xlsx.",
    "He guardado los datos en resumen.xlsx.",
    "Se ha creado el archivo resumen.xlsx con los totales.",
    "J'ai enregistré le rapport dans rapport.pdf.",
    "Je l'ai enregistré sous rapport.pdf.",
    "Ich habe die Datei bericht.xlsx gespeichert.",
    "bericht.xlsx wurde soeben gespeichert.",
    "Ho salvato il file in riepilogo.xlsx.",
    "Salvei o arquivo em resumo.xlsx.",
    "Ik heb het bestand rapport.xlsx opgeslagen.",
    "Gespeichert unter bericht.xlsx",
    "- Guardado: resumen.xlsx",
]
SAVED_NO = [
    "No he podido guardar resumen.xlsx.",
    "Puedo crear resumen.xlsx si quieres.",
    "El archivo que has creado, resumen.xlsx, tiene errores.",
    "Je ne peux pas enregistrer rapport.pdf.",
    "Ich habe bericht.xlsx nicht gespeichert.",
    "Ich kann bericht.xlsx erstellen, wenn du willst.",
    "Non ho salvato riepilogo.xlsx.",
    "Não salvei resumo.xlsx.",
    "Ik heb rapport.xlsx niet opgeslagen.",
    "bericht.xlsx wurde 2023 erstellt.",
    "El archivo resumen.xlsx fue creado en 2023.",
    "I saved it to report.pdf.",
    "Here is the summary of report.pdf.",
]
CHANGED_YES = [
    "Actualicé resumen.xlsx con los totales nuevos.",
    "He modificado el archivo resumen.xlsx.",
    "J'ai modifié rapport.docx.",
    "Ich habe bericht.xlsx aktualisiert.",
    "Ho aggiornato riepilogo.xlsx.",
    "Atualizei resumo.xlsx.",
    "Ik heb rapport.xlsx bijgewerkt.",
]
CHANGED_NO = [
    "No actualicé resumen.xlsx.",
    "Ich habe bericht.xlsx nicht aktualisiert.",
    "Puedo modificar resumen.xlsx si quieres.",
]
GIVES_YES = [
    "No he podido ejecutar los tests porque falta una dependencia.",
    "Je n'ai pas pu terminer la tâche.",
    "Ich konnte die Datei leider nicht öffnen.",
    "Non sono riuscito a completare il lavoro.",
    "Não consegui corrigir o erro.",
    "Ik kon het bestand niet openen.",
    "Es ist leider nicht möglich.",
]
GIVES_NO = [
    "No pude ejecutar los tests al principio, pero ahora pasan todos.",
    "No puedo creer lo rápido que fue.",
    "Ich konnte den Fehler beheben.",
    "Todo listo: los tests pasan.",
    "Je n'ai pas pu terminer au début, mais maintenant tout marche.",
    "I fixed it and the tests pass.",
]
NO_YES = [
    "Explícame esto, no uses herramientas.",
    "Sans créer de fichier, dis-moi.",
    "Bitte keine Dateien erstellen, nur erklären.",
    "Non usare strumenti, rispondi e basta.",
    "Não crie arquivos.",
    "Gebruik geen tools.",
]
NO_NO = [
    "Maak je geen zorgen, maak een rapport.",
    "Es gibt keine Datei namens x, erstelle eine.",
    "No te preocupes por el formato.",
    "Write a summary and save it as summary.docx.",
]
ASKS_YES = [
    "Crea una hoja de cálculo con los totales.",
    "Erstelle eine Excel-Datei mit den Zahlen.",
    "Bitte eine PDF-Datei erstellen.",
    "Fais-moi un fichier PDF du rapport.",
    "Crea un file Excel con i dati.",
    "Crie uma planilha com os totais.",
    "Maak een Excel-bestand met de cijfers.",
]
ASKS_NO = [
    "¿Cómo creo una hoja de cálculo?",
    "Wie erstelle ich eine Excel-Datei?",
    "Comment créer un fichier PDF ?",
    "Explícame qué es un PDF.",
]


@pytest.mark.parametrize("text", SAVED_YES)
def test_a_saved_claim_is_read_in_the_six_languages(text):
    assert L.saved_names(text), text


@pytest.mark.parametrize("text", SAVED_NO)
def test_an_offer_a_denial_a_users_file_or_a_files_history_is_no_claim(text):
    assert L.saved_names(text) == [], text


@pytest.mark.parametrize("text", CHANGED_YES)
def test_a_change_claim_is_read_in_the_six_languages(text):
    assert L.changed_names(text), text


@pytest.mark.parametrize("text", CHANGED_NO)
def test_a_change_denial_or_offer_is_no_claim(text):
    assert L.changed_names(text) == [], text


@pytest.mark.parametrize("text", GIVES_YES)
def test_a_give_up_is_read_in_the_six_languages(text):
    assert G.gives_up(text), text


@pytest.mark.parametrize("text", GIVES_NO)
def test_a_reply_that_worked_after_all_is_no_give_up(text):
    assert not G.gives_up(text), text


@pytest.mark.parametrize("text", NO_YES)
def test_the_users_no_is_read_in_the_six_languages(text):
    assert U.said_no(text), text


@pytest.mark.parametrize("text", NO_NO)
def test_an_ordinary_request_is_no_no(text):
    assert not U.said_no(text), text


def _asked(text: str) -> bool:
    from types import SimpleNamespace

    return CT._asked_for_a_file(SimpleNamespace(messages=[SimpleNamespace(role="user", content=text)]))


@pytest.mark.parametrize("text", ASKS_YES)
def test_a_request_for_a_file_is_read_in_the_six_languages(text):
    assert _asked(text), text


@pytest.mark.parametrize("text", ASKS_NO)
def test_a_question_about_making_a_file_is_advice(text):
    assert not _asked(text), text


@pytest.mark.parametrize("text", [
    "How do I prepare a pdf?", "What is a pdf?", "Can you explain excel formulas?",
])
def test_english_advice_is_still_advice(text):
    """The French "prépare" also matches English "prepare": the English
    question check runs first, so a how-to question is still advice."""
    assert not _asked(text), text


def test_the_file_names_read_are_the_english_readings_own():
    assert L.FILE_NAME == CT._CLAIMED_NAME


def test_a_spanish_claim_reaches_the_note_under_the_reply():
    named, _changed = CT._claimed_write_names("Guardé el resumen en resumen.docx.", [])
    assert named == ["resumen.docx"]
    assert CT._claimed_write_names("Guardé el resumen en resumen.docx.", ["write_document"]) == ([], [])
    assert CT._claimed_write_names("Actualicé resumen.xlsx con los totales.", []) == ([], ["resumen.xlsx"])


@pytest.mark.parametrize("text", [
    "Bloqueado: necesito su inicio de sesión.",
    "**Blockiert:** die Freigabe braucht Ihre Anmeldung.",
    "- Bloqué : il faut votre mot de passe.",
])
def test_a_blocked_line_in_another_language_ends_the_loop(text, monkeypatch):
    monkeypatch.setattr(U, "find_problems", lambda f: [U.Problem("k", "fix it")])
    until = U.UntilDone()
    assert asyncio.run(until.check(_facts(), round_index=0, rounds=10))
    assert asyncio.run(until.check(_facts(reply=text), round_index=1, rounds=10)) == ""
    assert until.ended == U.BLOCKED
    assert not G.gives_up("No pude abrirlo.\n\n" + text)


@pytest.mark.parametrize("text,want", [
    ("The report is in the folder and you can open it now.", True),
    ("El informe está en la carpeta y es para ti, ábrelo.", True),
    ("Der Bericht ist fertig und die Datei liegt im Ordner.", True),
    ("ok", True),
    ("Here is config.json:\n```json\n{\"a\": 1, \"b\": 2, \"c\": 3, \"d\": 4}\n```", True),
    ("报告已保存到 report.xlsx 文件中，请您打开查看一下。", False),
    ("Отчёт сохранён в файле report.xlsx, проверьте его пожалуйста.", False),
    ("Raport został zapisany w pliku report.xlsx, sprawdź go proszę teraz.", False),
])
def test_which_language_is_known(text, want):
    assert L.covered(text) is want


def test_every_reading_is_linear_on_hostile_text():
    """A reading runs on the event loop's thread pool for every reply: run each
    one on 60 kB of hostile text in a child process, with a deadline."""
    code = textwrap.dedent(
        """
        import sys, time
        sys.path.insert(0, %r)
        from iron_jarvis.core import claim_languages as L
        texts = [
            "Guardé " + "a" * 60000,
            "ich habe " + "x " * 30000 + "gespeichert",
            "no he podido " + "hacer " * 20000,
            ("a." * 30000) + "xlsx",
            "sin usar " + "y " * 30000,
            "crea " + "z" * 60000,
            "Ich konnte " + "a " * 30000 + "nicht",
            "bericht.xlsx " * 5000,
            "j'ai " + "é " * 30000,
        ]
        t = time.perf_counter()
        for s in texts:
            L.saved_names(s); L.changed_names(s); L.says_gives_up(s)
            L.said_no(s); L.asks_for_file(s); L.covered(s)
        print(round(time.perf_counter() - t, 2))
        """
    ) % (str(Path(__file__).resolve().parents[1] / "src"),)
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert float(done.stdout.strip()) < 20, done.stdout


# --------------------------------------------------------------------------- #
# 3. a claimed change is put only beside a tool that can make it
# --------------------------------------------------------------------------- #


def _facts(**kw) -> U.Facts:
    base = dict(reply="ok", tools_used=["read_file"], armed={"write_document"}, chat=True)
    base.update(kw)
    return U.Facts(**base)


CLAIM = "I've turned on Read replies aloud for you."


def _change(found):
    return [p for p in found if p.key.startswith("claimed_change:")]


def test_a_claimed_change_with_no_tool_to_make_it_is_only_asked_to_be_said_plainly():
    """v1.495.0 told the model to "make the change now" with no tool that could
    make it; it could only end on a "Blocked:" line. Now it is asked only to
    stop claiming it, which an honest answer settles."""
    found = _change(U.find_problems(_facts(reply=CLAIM, tools_used=[], armed={"read_file", "write_document"})))
    assert len(found) == 1, found
    assert "No tool you hold in this reply can make that change" in found[0].words
    assert "Make the change now" not in found[0].words
    honest = "I can't change that setting from here; turn on Read replies aloud in Settings."
    assert _change(U.find_problems(_facts(reply=honest, tools_used=[], armed={"read_file"}))) == []


def test_a_claimed_change_beside_a_settings_tool_is_asked_to_be_made():
    found = _change(U.find_problems(_facts(reply=CLAIM, tools_used=[], armed={"read_file", "config_set"})))
    assert len(found) == 1 and "Make the change now with the right tool" in found[0].words


def test_the_change_tools_include_the_settings_records_and_apps():
    tools = U.change_fixers()
    for name in ("config_set", "config_change", "config_undo", "app_connect", "chat_local_only",
                 "schedule_update", "schedule_create", "remember_preference"):
        assert name in tools, name
    assert "read_file" not in tools and "browser_click" not in tools


# --------------------------------------------------------------------------- #
# 1b. the side question, for a language no reading knows
# --------------------------------------------------------------------------- #

ZH_CLAIM = "我已经把总结保存到 summary.docx 文件里了，请您打开查看。"


def test_the_side_question_is_asked_for_a_missing_named_file(tmp_path):
    until = U.UntilDone()
    note = asyncio.run(until.side_note(
        _facts(reply=ZH_CLAIM, tools_used=["read_file"], request="请总结这些笔记"),
        workspace=tmp_path, failed_calls=0, round_index=0, rounds=10,
    ))
    assert note.startswith(U.SIDE_HEAD) and "`summary.docx`" in note and "KEEP" in note
    assert until.side_asked
    again = asyncio.run(until.side_note(
        _facts(reply=ZH_CLAIM), workspace=tmp_path, failed_calls=0, round_index=1, rounds=10,
    ))
    assert again == "", "asked once per reply"


def test_no_side_question_for_a_file_that_is_there_a_known_language_or_no_writer(tmp_path):
    (tmp_path / "summary.docx").write_bytes(b"x")
    kw = dict(workspace=tmp_path, failed_calls=0, round_index=0, rounds=10)
    assert asyncio.run(U.UntilDone().side_note(_facts(reply=ZH_CLAIM), **kw)) == ""
    (tmp_path / "summary.docx").unlink()
    english = "I wrote the summary below; you can paste it into summary.docx if you like."
    assert asyncio.run(U.UntilDone().side_note(_facts(reply=english), **kw)) == ""
    assert asyncio.run(U.UntilDone().side_note(_facts(reply=ZH_CLAIM, armed={"read_file"}), **kw)) == ""
    assert asyncio.run(U.UntilDone().side_note(
        _facts(reply=ZH_CLAIM, tools_used=["write_document"]), **kw)) == ""
    assert asyncio.run(U.UntilDone().side_note(
        _facts(reply=ZH_CLAIM, request="写 summary.docx"), **kw)) == "", "the user named the file"
    assert asyncio.run(U.UntilDone().side_note(_facts(reply=ZH_CLAIM), **{**kw, "round_index": 8})) == ""
    assert asyncio.run(U.UntilDone().side_note(_facts(reply=ZH_CLAIM), low_trust=True, **kw)) == ""
    assert asyncio.run(U.UntilDone(declined=True).side_note(_facts(reply=ZH_CLAIM), **kw)) == ""


def test_a_failed_call_after_work_asks_the_side_question(tmp_path):
    reply = "这个任务我做不到，因为文件打不开，抱歉。"
    note = asyncio.run(U.UntilDone().side_note(
        _facts(reply=reply, tools_used=["read_file"]), workspace=tmp_path, failed_calls=1,
        round_index=0, rounds=10,
    ))
    assert U.SIDE_FAILED in note
    assert asyncio.run(U.UntilDone().side_note(
        _facts(reply=reply, tools_used=["find_capability"]), workspace=tmp_path, failed_calls=1,
        round_index=0, rounds=10,
    )) == "", "a lookup is not work"


def test_a_folder_on_another_computer_is_never_looked_in(monkeypatch):
    looked: list[str] = []

    def record(self, *a, **k):  # noqa: ANN001
        looked.append(str(self))
        return False

    monkeypatch.setattr(Path, "exists", record)
    for share in ("\\\\fileserver\\share\\work", "//fileserver/share/work"):
        note = asyncio.run(U.UntilDone().side_note(
            _facts(reply=ZH_CLAIM), workspace=share, failed_calls=0, round_index=0, rounds=10,
        ))
        assert note == ""
    assert looked == [], looked


def _scripted(app, model: Model) -> None:
    app.state.platform.router.stream = model.stream
    app.state.platform.router.complete = model.complete


async def _run(lane, app, body, model):
    _scripted(app, model)
    if lane == "stream":
        from tests.test_script_power_lanes_v1473 import _answer

        frames = await _drive_stream(app, body, _answer(app, []))
        done = [d for ev, d in frames if ev == "done"]
        assert done, [ev for ev, _ in frames]
        return done[-1], frames
    return await _post(app, body, model), []


@LANES
async def test_a_side_question_answered_with_a_call_makes_the_file(desk, lane):
    app, ws = desk
    model = Model([ZH_CLAIM,
                   _call(1, "write_document", path="summary.docx", content="# 总结\n\n很短。"),
                   "已保存 summary.docx。"])
    done, frames = await _run(lane, app, _body(ws, "请把这些笔记总结一下，写成文件。", tools=["write_document"]), model)
    assert (ws / "summary.docx").is_file()
    assert done["reply"].endswith("已保存 summary.docx。")
    if lane == "stream":
        assert ("reset", {"reason": "check_again"}) in frames


@LANES
async def test_a_side_question_answered_keep_leaves_the_reply_as_it_was(desk, lane):
    """The cost of the side question on an honest reply: one more model call,
    nothing on screen changes (no reset frame), the reply is word for word."""
    app, ws = desk
    model = Model([ZH_CLAIM, "KEEP"])
    done, frames = await _run(lane, app, _body(ws, "请把这些笔记总结一下。", tools=["write_document"]), model)
    assert len(model.offered) == 2, "the reply, then one side question"
    assert done["reply"].startswith(ZH_CLAIM)
    assert "KEEP" not in done["reply"]
    if lane == "stream":
        assert not any(ev == "reset" for ev, _ in frames), frames


@LANES
async def test_an_ordinary_reply_in_another_script_costs_nothing(desk, lane):
    app, ws = desk
    model = Model(["你好！今天我能为您做些什么呢？请告诉我吧。"])
    done, _frames = await _run(lane, app, _body(ws, "你好"), model)
    assert len(model.offered) == 1
    assert done["reply"].startswith("你好")


@LANES
async def test_a_spanish_false_claim_is_sent_back_through_the_lane(desk, lane):
    app, ws = desk
    model = Model(["Guardé el resumen en resumen.docx.",
                   _call(1, "write_document", path="resumen.docx", content="# Resumen\n\nCorto."),
                   "Listo: resumen.docx está guardado."])
    done, _frames = await _run(lane, app, _body(ws, "Resume estas notas y guárdalo como resumen.docx.", tools=["write_document"]), model)
    assert (ws / "resumen.docx").is_file()
    assert "Checked by Iron Jarvis" not in done["reply"]


def test_an_agent_run_in_another_script_answered_keep_keeps_its_answer(tmp_path):
    s, offered, _texts, _rows = _agent(
        tmp_path, "请把这些笔记总结一下。",
        [ZH_CLAIM, "KEEP"],
    )
    assert len(offered) == 2, offered
    assert (s.summary or "").startswith(ZH_CLAIM[:10])
    assert "KEEP" not in (s.summary or "")


def test_an_agent_run_in_another_script_answered_with_a_call_makes_the_file(tmp_path):
    s, offered, _texts, rows = _agent(
        tmp_path, "请把这些笔记总结一下，写成 Markdown 文件。",
        [ZH_CLAIM.replace("summary.docx", "summary.md"),
         _call(1, "write_file", path="summary.md", content="# 总结\n\n很短。"),
         "已保存 summary.md。"],
    )
    assert (tmp_path / "work" / "summary.md").is_file()
    assert ("write_file", True) in rows


# --------------------------------------------------------------------------- #
# 2. the browser sidebar
# --------------------------------------------------------------------------- #


def test_the_sidebar_now_has_the_loop():
    from iron_jarvis.providers.failure_words import reset_surface, set_sidebar_surface

    token = set_sidebar_surface()
    try:
        assert CT._in_the_sidebar()
        assert isinstance(CT.until_done_for_turn(None, None), U.UntilDone)
    finally:
        reset_surface(token)
    assert CT.until_done_for_turn(object(), None) is None, "a coding session has its own"


def test_in_the_sidebar_no_file_problem_is_ever_put():
    """Its turn holds only browser tools: no file or figure problem is ever
    put to it (each needs a tool that can fix it); a change it claims is only
    asked to be said plainly."""
    armed = {"browser_read_page", "browser_click", "browser_type"}
    facts = _facts(reply="I saved the page as page.pdf for you.", tools_used=["browser_read_page"],
                   armed=armed, asked_for_file=True)
    assert U.find_problems(facts) == []
    gave_up = _facts(reply="I couldn't open the settings page.", tools_used=["browser_read_page"],
                     armed=armed)
    note = asyncio.run(U.UntilDone().check(gave_up, round_index=0, rounds=24))
    assert note == G.GIVE_UP_NUDGE


# --------------------------------------------------------------------------- #
# 4. a coding session with a team
# --------------------------------------------------------------------------- #


def test_a_team_reply_reaches_the_check_again_loop():
    src = (Path(__file__).resolve().parents[1] / "src/iron_jarvis/harness/finish.py").read_text(encoding="utf-8")
    assert "if second is not None or self._second_note_due:" in src
    assert "if second is not None or self.team is not None:" not in src
    assert re.search(r"if not self\.ran or not self\._note_sent or not self\.config\.again:", src)


def test_a_team_leads_note_says_to_hand_the_fix_to_a_worker():
    from iron_jarvis.harness import finish as F

    assert "hand each fix to a worker" in F.AGAIN_KEEP_GOING_TEAM
    check = F.FinishCheck.__new__(F.FinishCheck)
    check.team = type("T", (), {"unlocked": False})()
    assert check._lead_locked()
    check.team = None
    assert not check._lead_locked()
