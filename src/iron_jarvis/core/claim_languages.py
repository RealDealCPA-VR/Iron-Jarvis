"""The honesty readings in six more languages (v1.498.0).

The check-again loop (``daemon/until_done``, v1.495.0) and the coding loop
(``harness/finish`` + ``harness/give_up``, v1.493.0) judge a reply by four
readings that were English only: a file the reply says it SAVED, a file it says
it CHANGED, a reply that GIVES UP, and two readings of the user's own message
(they ASKED for a file; they said NOT to use tools). A reply in Spanish said
"Guardé el informe en resumen.xlsx" with no file written and nothing noticed.

This module reads the same four things in Spanish, French, German, Italian,
Portuguese and Dutch. Each reading is called BESIDE the English one by the
function that already owned it (``chat_turn._claimed_write_names``,
``chat_turn._asked_for_a_file``, ``give_up.gives_up``, ``until_done.said_no``),
so the notes under a finished reply and the loop that sends a reply back agree
(the v1.186.0 "one predicate, two users" rule).

THE SAME CARE AS THE ENGLISH READINGS:
- a claim is first person or a present-perfect passive ("he guardado", "j'ai
  enregistré", "ich habe … gespeichert", "se ha creado"), never a description
  of a file's history ("fue creado en 2023", "wurde 2023 erstellt");
- the 40 characters before a claim are read for a negation, a modal, a
  condition or a second person ("no he podido guardar", "puedo crear",
  "si guardas", "el archivo que has creado"): any of them and it is not a claim;
- a give-up is read in the LAST paragraph only (the English reading also reads
  the first sentence; with no success words in every language that would send
  back a reply that opened "No pude ejecutar los tests al principio" and ended
  "Ahora pasan todos");
- every pattern is bounded and linear (a child-process pin feeds hostile text).

A LANGUAGE THESE READINGS DO NOT KNOW (:func:`covered`) is said so, never
guessed: the loop then asks the model ONE side question instead
(``until_done.UntilDone.side_note``), which changes the reply only when the
model answers with a tool call.
"""

from __future__ import annotations

import re

#: The filename part of a claim. MUST equal ``chat_turn._CLAIMED_NAME`` (a
#: test pins the two): the same files are claimed in every language.
FILE_NAME = (
    r"([\w.$~()\[\]-]+\.(?:pdf|docx|doc|xlsx|xls|pptx|ppt|csv|txt|md|json|"
    r"html|rtf|odt|png|jpg|jpeg|zip))"
)
#: What may stand between a verb and the name: quotes, bold, a folder path.
_LEAD = r"[`\"'*“‘«»„]{0,3}(?:[^\s`\"'*<>|]*[\\/])?"
#: How far a claim's verb may be from the file it names.
_GAP = r"[^.\n!?]{0,90}?"
_A = r"(?:'|’)"

#: The text read for a give-up, a request, a "do not".
MAX_TEXT = 6_000

# --------------------------------------------------------------------------- #
# SAVED / CREATED
# --------------------------------------------------------------------------- #

_SAVED_ES = (
    r"(?:guard[ée]|cre[ée]|gener[ée]|escrib[íi]|export[ée]|"
    r"(?:he|hemos|se\s+ha|se\s+han|ha\s+quedado|qued[óo])\s+"
    r"(?:guardad[oa]s?|cread[oa]s?|generad[oa]s?|escrit[oa]s?|exportad[oa]s?))"
)
_SAVED_FR = (
    r"(?:(?:j" + _A + r"ai|(?:je|nous)\s+l" + _A + r"\s*(?:ai|avons)|nous\s+avons|a\s+été|ont\s+été)"
    r"\s+(?:\w+\s+){0,2}?"
    r"(?:enregistr[ée]e?s?|sauvegard[ée]e?s?|cr[ée][ée]e?s?|g[ée]n[ée]r[ée]e?s?|[ée]crite?s?|export[ée]e?s?))"
)
_SAVED_IT = (
    r"(?:(?:ho|abbiamo|[èe]\s+stat[oa]|sono\s+stat[ie])\s+(?:\w+\s+){0,2}?"
    r"(?:salvat[oaie]|creat[oaie]|generat[oaie]|scritt[oaie]|esportat[oaie]))"
)
_SAVED_PT = (
    r"(?:salvei|guardei|criei|gerei|escrevi|exportei|"
    r"(?:foi|foram|tenho|temos)\s+(?:\w+\s+){0,1}?"
    r"(?:salv[oa]s?|guardad[oa]s?|criad[oa]s?|gerad[oa]s?|escrit[oa]s?|exportad[oa]s?))"
)
#: German and Dutch: the participle comes AFTER the file ("Ich habe die Datei
#: bericht.xlsx gespeichert"), so the first person opens the claim.
_PART_DE = r"(?:gespeichert|erstellt|angelegt|geschrieben|exportiert|generiert|erzeugt|abgelegt)"
_PART_NL = r"(?:opgeslagen|bewaard|gemaakt|aangemaakt|gegenereerd|geschreven|ge[eë]xporteerd)"

_SAVED_VERB_FIRST_RX = re.compile(
    r"\b(?:" + _SAVED_ES + "|" + _SAVED_FR + "|" + _SAVED_IT + "|" + _SAVED_PT + r")\b"
    + _GAP + FILE_NAME,
    re.IGNORECASE,
)
#: Words that start a second clause. The participle after one of them belongs
#: to that clause, not to the file: "Ich habe bericht.xlsx gelesen UND eine
#: Zusammenfassung erstellt" saved nothing called bericht.xlsx.
_NEXT_CLAUSE = (
    r"(?:und|oder|aber|sowie|dann|danach|anschlie(?:ß|ss)end|"
    r"en|of|maar|daarna|toen|vervolgens)"
)
_SAVED_SUBJECT_FIRST_RX = re.compile(
    r"\b(?:(?:ich|wir)\s+(?:habe|haben|hab)|(?:ik|we|wij)\s+(?:heb|hebben))\b"
    r"[^.\n!?]{0,80}?" + FILE_NAME
    + r"(?:(?!\b" + _NEXT_CLAUSE + r"\b)[^.\n!?;]){0,40}?"
    r"\b(?:" + _PART_DE + "|" + _PART_NL + r")\b",
    re.IGNORECASE,
)
#: "bericht.xlsx wurde soeben gespeichert", "rapport.xlsx is nu opgeslagen".
_SAVED_NAME_FIRST_RX = re.compile(
    r"(?<![\w.$~()\[\]-])" + FILE_NAME
    + r"\s+(?:(?:wurde|ist|wurden|sind)\s+(?:jetzt|nun|soeben|gerade|erfolgreich)"
    r"|(?:is|zijn|werd)\s+(?:nu|zojuist|net|succesvol))\s+(?:" + _PART_DE + "|" + _PART_NL + r")\b",
    re.IGNORECASE,
)
#: "Guardado: resumen.xlsx", "Gespeichert unter bericht.xlsx", "✅ Enregistré
#: sous rapport.pdf" — a line that opens with the participle.
_SAVED_LINE_RX = re.compile(
    r"^[^\S\n]*(?:[-*•]\s*)?(?:[✅✔]\s*)?(?:\*\*)?"
    r"(?:guardad[oa]|cread[oa]|generad[oa]|exportad[oa]|enregistr[ée]e?|sauvegard[ée]e?|cr[ée][ée]e?|"
    r"salvat[oa]|creat[oa]|salv[oa]|criad[oa]|" + _PART_DE + "|" + _PART_NL + r")\b"
    r"(?:\*\*)?[^.\n]{0,60}?" + FILE_NAME,
    re.IGNORECASE | re.MULTILINE,
)

# --------------------------------------------------------------------------- #
# CHANGED
# --------------------------------------------------------------------------- #

_DET = r"(?:(?:el|la|los|las|le|les|l" + _A + r"|il|lo|o|a|os|as|de|het|die|das|den)\s*)?"
_FILE_NOUN = (
    r"(?:(?:archivo|fichero|documento|libro|hoja|fichier|document|classeur|datei|dokument|"
    r"arbeitsmappe|tabelle|file|cartella|arquivo|ficheiro|planilha|bestand|werkmap)\s+)?"
)
_CHANGED_VERB_FIRST_RX = re.compile(
    r"\b(?:actualic[ée]|modifiqu[ée]|edit[ée]|cambi[ée]|"
    r"(?:he|hemos)\s+(?:actualizado|modificado|editado|cambiado)|"
    r"(?:j" + _A + r"ai|nous\s+avons)\s+(?:mis\s+[àa]\s+jour|modifi[ée]|[ée]dit[ée]|chang[ée])|"
    r"(?:ho|abbiamo)\s+(?:aggiornato|modificato|cambiato)|"
    r"atualizei|modifiquei|editei|alterei|mudei)\s+"
    + _DET + _FILE_NOUN + _LEAD + FILE_NAME,
    re.IGNORECASE,
)
_CHANGED_SUBJECT_FIRST_RX = re.compile(
    r"\b(?:(?:ich|wir)\s+(?:habe|haben|hab)|(?:ik|we|wij)\s+(?:heb|hebben))\s+"
    + _DET + _FILE_NOUN + _LEAD + FILE_NAME
    + r"\s+(?:\w+\s+){0,2}?(?:aktualisiert|ge[äa]ndert|bearbeitet|angepasst|bijgewerkt|gewijzigd|"
    r"aangepast|veranderd)\b",
    re.IGNORECASE,
)

#: The run-up that makes a match an offer, a denial, a condition or the USER's
#: own act — the English ``_CLAIM_NEGATION_RX``, in each language.
_NOT_A_CLAIM_RX = re.compile(
    r"\b(?:"
    # Spanish
    r"no|nunca|ning[úu]n|ninguna|nada|puedo|podr[íi]a|podr[íi]as|puedes|puede|voy\s+a|vas\s+a|"
    r"va\s+a|quieres|si|cuando|antes\s+de|para|has|hayas|usted|tú|"
    # French
    r"ne|n" + _A + r"|pas|jamais|aucune?|rien|peux|pourrais|pourriez|pouvez|vais|veux|voulez|"
    r"quand|avant\s+de|pour|tu|vous|"
    # German
    r"nicht|kein|keine|keinen|nie|nichts|kann|kannst|k[öo]nnte|k[öo]nnen|werde|wirst|w[üu]rde|"
    r"soll|wenn|falls|sobald|bevor|um|du|ihr|"
    # Italian
    r"non|mai|nessuno|nessuna|niente|posso|potrei|puoi|pu[òo]|vuoi|se|prima\s+di|per|hai|avete|"
    # Portuguese
    r"n[ãa]o|nenhum|nenhuma|poderia|podes|vou|vai|quer|quando|antes\s+de|voc[êe]s?|"
    # Dutch
    r"niet|geen|nooit|niets|kun|kunt|zou|zal|ga|gaat|wil|als|wanneer|voordat|om|jij|u"
    r")\b[^.\n]{0,24}$",
    re.IGNORECASE,
)
#: A negation INSIDE a subject-first claim ("ich habe x.xlsx nicht gespeichert").
_NOT_INSIDE_RX = re.compile(r"\b(?:nicht|kein\w*|nie|niet|geen|nooit)\b", re.IGNORECASE)


def _claim_ok(text: str, m: "re.Match[str]", *, inside: bool = False) -> bool:
    lead = text[max(0, m.start() - 40): m.start()]
    if _NOT_A_CLAIM_RX.search(lead):
        return False
    if inside and _NOT_INSIDE_RX.search(m.group(0)):
        return False
    return True


def saved_names(text: str) -> list[str]:
    """The files ``text`` says were saved or created, in the six languages.
    Never raises."""
    try:
        text = str(text or "")[:40_000]
        out: list[str] = []
        for rx, inside in ((_SAVED_VERB_FIRST_RX, False), (_SAVED_SUBJECT_FIRST_RX, True),
                           (_SAVED_NAME_FIRST_RX, True), (_SAVED_LINE_RX, False)):
            for m in rx.finditer(text):
                if _claim_ok(text, m, inside=inside):
                    name = m.group(1)
                    if name not in out:
                        out.append(name)
        return out
    except Exception:  # noqa: BLE001 — a reading never breaks a reply
        return []


def changed_names(text: str) -> list[str]:
    """The files ``text`` says were changed, in the six languages. Never
    raises."""
    try:
        text = str(text or "")[:40_000]
        out: list[str] = []
        for rx, inside in ((_CHANGED_VERB_FIRST_RX, False), (_CHANGED_SUBJECT_FIRST_RX, True)):
            for m in rx.finditer(text):
                if _claim_ok(text, m, inside=inside):
                    name = m.group(1)
                    if name not in out:
                        out.append(name)
        return out
    except Exception:  # noqa: BLE001
        return []


# --------------------------------------------------------------------------- #
# GIVES UP
# --------------------------------------------------------------------------- #

_WORK_ES = (
    r"(?:completar|terminar|arreglar|corregir|hacer|crear|ejecutar|abrir|leer|acceder|instalar|"
    r"resolver|encontrar|guardar|escribir|modificar|cambiar|conectar|continuar|generar|exportar|"
    r"actualizar|conseguir|lograr)\w*"
)
_WORK_FR = (
    r"(?:terminer|compl[ée]ter|corriger|r[ée]parer|faire|cr[ée]er|ex[ée]cuter|ouvrir|lire|"
    r"acc[ée]der|installer|r[ée]soudre|trouver|enregistrer|[ée]crire|modifier|changer|connecter|"
    r"continuer|g[ée]n[ée]rer|exporter|mettre\s+[àa]\s+jour)"
)
_WORK_DE = (
    r"(?:abschlie[ßs]en|beenden|fertigstellen|beheben|reparieren|machen|erstellen|ausf[üu]hren|"
    r"[öo]ffnen|lesen|zugreifen|installieren|l[öo]sen|finden|speichern|schreiben|[äa]ndern|"
    r"verbinden|fortfahren|erzeugen|exportieren|aktualisieren|durchf[üu]hren|erledigen)"
)
_WORK_IT = (
    r"(?:completare|finire|correggere|sistemare|fare|creare|eseguire|aprire|leggere|accedere|"
    r"installare|risolvere|trovare|salvare|scrivere|modificare|cambiare|collegare|continuare|"
    r"generare|esportare|aggiornare)\w*"
)
_WORK_PT = (
    r"(?:concluir|terminar|corrigir|consertar|fazer|criar|executar|abrir|ler|acessar|aceder|"
    r"instalar|resolver|encontrar|salvar|guardar|escrever|modificar|alterar|mudar|conectar|"
    r"continuar|gerar|exportar|atualizar)\w*"
)
_WORK_NL = (
    r"(?:voltooien|afmaken|oplossen|repareren|maken|aanmaken|uitvoeren|openen|lezen|installeren|"
    r"vinden|opslaan|schrijven|wijzigen|veranderen|verbinden|doorgaan|genereren|exporteren|"
    r"bijwerken|afronden)"
)

_GIVES_UP_RX = re.compile(
    r"(?:"
    # Spanish
    r"\bno\s+(?:he\s+podido|pude|puedo|logr[ée]|consegu[íi]|consigo|fui\s+capaz\s+de|soy\s+capaz\s+de)"
    r"\s+(?:\w+\s+){0,2}?" + _WORK_ES + r"\b"
    r"|\bno\s+(?:es|fue|ha\s+sido)\s+posible\s+" + _WORK_ES + r"\b"
    r"|\bno\s+se\s+puede\s+" + _WORK_ES + r"\b"
    r"|\b(?:tendr[áa]s|tendr[áa]|deber[áa]s|deber[áa])\s+que\s+hacerlo\s+(?:t[úu]|usted|manualmente|a\s+mano)\b"
    # French
    r"|\bje\s+(?:n" + _A + r"ai\s+pas\s+pu|ne\s+peux\s+pas|ne\s+pouvais\s+pas|n" + _A + r"arrive\s+pas\s+[àa]"
    r"|ne\s+suis\s+pas\s+parvenue?\s+[àa]|n" + _A + r"ai\s+pas\s+r[ée]ussi\s+[àa])"
    r"\s+(?:\w+\s+){0,2}?" + _WORK_FR + r"\b"
    r"|\b(?:il\s+n" + _A + r"est\s+pas\s+possible\s+de|impossible\s+de)\s+" + _WORK_FR + r"\b"
    r"|\bvous\s+(?:devrez|devez)\s+le\s+faire\s+(?:vous-m[êe]me|manuellement|[àa]\s+la\s+main)\b"
    # German
    r"|\bich\s+(?:konnte|kann|k[öo]nnte)\b[^.!?\n]{0,60}?\bnicht\b[^.!?\n]{0,40}?\b" + _WORK_DE + r"\b"
    r"|\bich\s+war\s+nicht\s+in\s+der\s+lage\b"
    r"|\bes\s+ist\s+(?:leider\s+)?nicht\s+m[öo]glich\b"
    r"|\bm[üu]ssen\s+sie\s+(?:das|dies)\s+(?:selbst|manuell|von\s+hand)\b"
    # Italian
    r"|\bnon\s+(?:sono\s+riuscit[oa]|riesco|posso|ho\s+potuto|sono\s+in\s+grado\s+di)\s+(?:a\s+)?"
    r"(?:\w+\s+){0,2}?" + _WORK_IT + r"\b"
    r"|\bnon\s+[èe]\s+(?:stato\s+)?possibile\s+" + _WORK_IT + r"\b"
    r"|\bdovrai\s+farlo\s+(?:tu|manualmente|a\s+mano)\b"
    # Portuguese
    r"|\bn[ãa]o\s+(?:consegui|consigo|pude|posso|fui\s+capaz\s+de)\s+(?:\w+\s+){0,2}?" + _WORK_PT + r"\b"
    r"|\bn[ãa]o\s+(?:[ée]|foi)\s+poss[íi]vel\s+" + _WORK_PT + r"\b"
    r"|\bvoc[êe]\s+(?:ter[áa]|precisar[áa])\s+(?:que|de)\s+fazer\s+isso\s+(?:manualmente|sozinh[oa])\b"
    # Dutch
    r"|\bik\s+(?:kon|kan)\b[^.!?\n]{0,60}?\bniet\b[^.!?\n]{0,40}?\b" + _WORK_NL + r"\b"
    r"|\bik\s+ben\s+er\s+niet\s+in\s+geslaagd\b"
    r"|\bhet\s+is\s+(?:helaas\s+)?niet\s+mogelijk\b"
    r")",
    re.IGNORECASE,
)


#: "… al principio, pero ahora pasan": the paragraph says it worked after all.
_THEN_IT_WORKED_RX = re.compile(
    r"\b(?:pero|aunque|sin\s+embargo|mais|cependant|aber|jedoch|doch|ma|per[òo]|mas|por[ée]m|maar)\b"
    r"[^.!?\n]{0,80}?\b(?:ahora|ya|maintenant|jetzt|nun|ora|adesso|agora|j[áa]|nu|inmiddels)\b",
    re.IGNORECASE,
)


def says_gives_up(text: str) -> bool:
    """Does ``text`` (a reply's LAST paragraph) say the work cannot be done,
    in the six languages? Not when the paragraph goes on to say it worked
    after all. Never raises."""
    try:
        text = str(text or "")[-MAX_TEXT:]
        m = _GIVES_UP_RX.search(text)
        if not m:
            return False
        return not _THEN_IT_WORKED_RX.search(text[m.end():])
    except Exception:  # noqa: BLE001
        return False


#: A line that says what blocks the work, in each language ("Blocked:" is what
#: the notes ask for; a model answering in Spanish may write "Bloqueado:").
BLOCKED_WORDS = r"(?:blocked|bloquead[oa]|bloqu[ée]e?|blockiert|bloccat[oa]|geblokkeerd)"


# --------------------------------------------------------------------------- #
# THE USER'S OWN WORDS
# --------------------------------------------------------------------------- #

_THING_NO = (
    r"(?:herramientas?|archivos?|ficheros?|documentos?|nada|outils?|fichiers?|documents?|rien|"
    r"werkzeuge|tools?|dateien?|dokumente?|nichts|strumenti|strumento|file|niente|nulla|"
    r"ferramentas?|arquivos?|bestanden|bestand|documenten|niets)"
)
_SAID_NO_RX = re.compile(
    r"(?:"
    r"\bno\s+(?:uses|utilices|llames|ejecutes|crees|guardes|escribas|toques)\b(?:\s+\w+){0,3}?\s+" + _THING_NO
    + r"|\bsin\s+(?:usar|utilizar|crear|guardar|escribir|tocar)\b(?:\s+\w+){0,2}?\s+" + _THING_NO
    + r"|\bne\s+(?:cr[ée]e|cr[ée]ez|enregistre|enregistrez|[ée]cris|[ée]crivez|touche|touchez|utilise|"
    r"utilisez|lance|lancez)\s+(?:pas|aucun|aucune)\b(?:\s+\w+){0,3}?\s+" + _THING_NO
    + r"|\bn" + _A + r"utilise(?:z)?\s+(?:pas|aucun)\b(?:\s+\w+){0,3}?\s+" + _THING_NO
    + r"|\bsans\s+(?:utiliser|cr[ée]er|enregistrer|[ée]crire|toucher)\b(?:\s+\w+){0,2}?\s+" + _THING_NO
    + r"|\b(?:keine|kein)\s+(?:werkzeuge|tools?|datei|dateien|dokumente?)\s+(?:\w+\s+){0,2}?"
    r"(?:verwenden|benutzen|nutzen|erstellen|anlegen|speichern|schreiben|aufrufen)\b"
    r"|\bohne\b(?:\s+\w+){0,2}?\s+(?:werkzeuge|tools?|dateien?|dokumente?)\b"
    r"|\b(?:verwende|benutze|nutze|erstelle|speichere|schreibe|verwenden\s+sie|erstellen\s+sie)\s+"
    r"(?:\w+\s+){0,2}?(?:(?:keine|kein)\s+(?:werkzeuge|tools?|datei|dateien|dokumente?)|nichts)\b"
    r"|\bnon\s+(?:usare|utilizzare|chiamare|eseguire|creare|salvare|scrivere|toccare)\b(?:\s+\w+){0,3}?\s+"
    + _THING_NO
    + r"|\bsenza\s+(?:usare|utilizzare|creare|salvare|scrivere|toccare)\b(?:\s+\w+){0,2}?\s+" + _THING_NO
    + r"|\bn[ãa]o\s+(?:use|usa|utilize|chame|execute|crie|cria|salve|salva|grave|escreva|toque)\b"
    r"(?:\s+\w+){0,3}?\s+" + _THING_NO
    + r"|\bsem\s+(?:usar|utilizar|criar|salvar|gravar|escrever|tocar)\b(?:\s+\w+){0,2}?\s+" + _THING_NO
    + r"|\b(?:gebruik|maak|sla|schrijf)\s+(?:\w+\s+){0,1}?"
    r"(?:geen\s+(?:tools?|bestanden|bestand|documenten)|niets\s+op)\b"
    r"|\bzonder\b(?:\s+\w+){0,2}?\s+(?:tools?|bestanden|bestand|documenten)\b"
    r")",
    re.IGNORECASE,
)


def said_no(request: str) -> bool:
    """Does the user's message say not to use tools or write anything, in the
    six languages? Never raises."""
    try:
        return bool(_SAID_NO_RX.search(str(request or "")[:MAX_TEXT]))
    except Exception:  # noqa: BLE001
        return False


_FILE_KIND = (
    r"(?:excel|xlsx|docx|word|pdf|csv|pptx|"
    r"hoja\s+de\s+c[áa]lculo|libro\s+de\s+excel|presentaci[óo]n|documento|archivo|fichero|"
    r"tableur|classeur|pr[ée]sentation|document|fichier|"
    r"tabelle|arbeitsmappe|pr[äa]sentation|dokument|datei|"
    r"foglio\s+di\s+calcolo|cartella\s+di\s+lavoro|presentazione|"
    r"planilha|folha\s+de\s+c[áa]lculo|apresenta[çc][ãa]o|arquivo|ficheiro|"
    r"spreadsheet|werkmap|presentatie|bestand)"
)
_MAKE = (
    r"(?:crea|cr[ée]ame|crear|haz|hazme|genera|gen[ée]rame|prepara|prep[áa]rame|escribe|guarda|"
    r"exporta|redacta|gu[áa]rdal[oa]s?|cr[ée]al[oa]s?|hazl[oa]s?|esc[ríi]+bel[oa]s?|exp[óo]rtal[oa]s?|"
    r"salvalo|salvala|crealo|creala|scrivilo|esportalo|salve-o|salva-o|guarde-o|crie-o|exporte-o|"
    # Dutch "sla ... op" only WITH its "op": a bare "sla" is English "SLA".
    r"sauvegarde|sauvegardez|sla(?=\s+(?:\S+\s+){0,4}?op\b)|"
    r"cr[ée]e|cr[ée]ez|cr[ée]er|fais|faites|g[ée]n[èe]re|g[ée]n[ée]rez|pr[ée]pare|pr[ée]parez|[ée]cris|"
    r"[ée]crivez|enregistre|exporte|r[ée]dige|"
    r"erstelle|erzeuge|mach|mache|schreib|schreibe|speichere|exportiere|generiere|"
    r"creami|fai|fammi|preparami|scrivi|salva|esporta|redigi|"
    r"crie|cria|criar|fa[çc]a|faz|gere|gera|escreva|escreve|salve|exporte|redija|"
    r"maak|cre[eë]er|genereer|schrijf|exporteer)"
)
_ASKS_FILE_RX = re.compile(
    r"\b" + _MAKE + r"\b.{0,60}?\b" + _FILE_KIND + r"\b"
    r"|\b" + _FILE_KIND + r"\b.{0,60}?\b(?:erstellen|anlegen|speichern|exportieren|erzeugen|schreiben|"
    r"maken|aanmaken|opslaan|exporteren|genereren)\b",
    re.IGNORECASE,
)
#: A QUESTION about making something is advice ("¿Cómo creo una hoja?"), as
#: the English ``_ADVICE_RX`` says for "how do I create …".
_ADVICE_RX = re.compile(
    r"\s*[¿¡]?\s*(?:c[óo]mo|qu[ée]|por\s+qu[ée]|cu[áa]ndo|d[óo]nde|puedo|puedes|podr[íi]as|"
    r"se\s+puede|es\s+posible|debo|deber[íi]a|"
    r"comment|que|qu" + _A + r"|pourquoi|quand|o[ùu]|est-ce|puis-je|peux-tu|pouvez-vous|faut-il|"
    r"wie|was|warum|wann|wo|kann|kannst|k[öo]nnen|k[öo]nnte|soll|sollte|ist|gibt|"
    r"come|cosa|che|perch[ée]|quando|dove|posso|puoi|potresti|si\s+pu[òo]|devo|"
    r"como|o\s+que|por\s+que|porque|onde|pode|podes|poderia|[ée]\s+poss[íi]vel|"
    r"hoe|wat|waarom|wanneer|waar|kun|kunt|moet|zou)\b",
    re.IGNORECASE,
)


def asks_for_file(request: str) -> bool:
    """Does the user's message ask for a file to be made, in the six
    languages? Never raises."""
    try:
        text = str(request or "")[:MAX_TEXT]
        return bool(_ASKS_FILE_RX.search(text)) and not _ADVICE_RX.match(text)
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# WHICH LANGUAGE
# --------------------------------------------------------------------------- #

#: A few common words of each language these readings know. A reply holding
#: two different ones of a language is in a language they know.
_STOPWORDS: dict[str, frozenset[str]] = {
    "en": frozenset("the and you that this with for have was are not but from what your will "
                    "is it of to in i we".split()),
    "es": frozenset("el los las del que una por con para pero como está esto este más también "
                    "muy hay ya es en un la".split()),
    "fr": frozenset("le les des une est pas pour avec dans que qui sur mais vous nous cette sont "
                    "aussi très il je".split()),
    "de": frozenset("der die das und ist nicht ein eine mit für auf ich sie wir den dem auch "
                    "noch wird zu".split()),
    "it": frozenset("il che non della per una sono con gli anche questo questa più come ma del "
                    "nel ho è".split()),
    "pt": frozenset("os uma não para com que mais como mas está isso este também muito seu "
                    "sua foi ao".split()),
    "nl": frozenset("de het een niet van dat ik je met voor op zijn maar ook nog wel dit naar "
                    "is en".split()),
}
#: Common words of Latin-script languages these readings do NOT know. Only
#: this positive evidence (or :data:`_FOREIGN_LETTER_RX`) makes a Latin-script
#: reply "another language": a reply that is merely list-like ("Claude Code,
#: Codex or Pi.") is judged as it always was.
_FOREIGN_WORDS: dict[str, frozenset[str]] = {
    "nordic": frozenset("jeg jag og och er är det ikke inte har den har jeg til av som på med "
                        "nu klar".split()),
    "fi": frozenset("ja se nyt ei olen että tämä kanssa mutta myös".split()),
    "pl": frozenset("nie jest się że jak ale już tak dla tylko".split()),
    "cs": frozenset("je jsem není že jak ale už pro jsou".split()),
    "tr": frozenset("ve bir bu için ile ama çok daha olarak".split()),
    "id": frozenset("saya sudah dan yang anda untuk dengan ini itu tidak di".split()),
    "tl": frozenset("ang ng mga sa ay ako ko na hindi".split()),
    "ro": frozenset("și este nu cu pentru am fost sunt care".split()),
}
#: Letters only languages these readings do not know use (Polish, Czech,
#: Turkish, Nordic, Romanian, Hungarian...).
#: Case-sensitive on purpose: under IGNORECASE the Turkish dotless "ı"
#: matches a plain "i", and every English reply would look foreign.
_FOREIGN_LETTER_RX = re.compile(
    "[łąęśżźćńőűøåæşğıțșăěřůčšžďťňľĺŕđŁĄĘŚŻŹĆŃŐŰØÅÆŞĞİȚȘĂĚŘŮČŠŽĎŤŇĽĹŔĐ]"
)
_WORD_RX = re.compile(r"[^\W\d_]+", re.UNICODE)
_LATIN_RX = re.compile(r"[A-Za-zÀ-ɏ]")
#: Prose shorter than this is judged as it always was (``covered`` = True).
MIN_WORDS = 6
#: Text with fewer letters than this is judged as it always was.
MIN_LETTERS = 12
_FENCE_LINE_RX = re.compile(r"^\s*(?:```|~~~)")
_INLINE_CODE_RX = re.compile(r"`[^`\n]{0,400}`")


def _prose(text: str) -> str:
    """``text`` without fenced code blocks and inline code (a reply that is
    mostly code says little in any language)."""
    out: list[str] = []
    fenced = False
    for line in text.replace("\r\n", "\n").split("\n"):
        if _FENCE_LINE_RX.match(line):
            fenced = not fenced
            continue
        if not fenced:
            out.append(line)
    return _INLINE_CODE_RX.sub(" ", "\n".join(out))


def covered(text: str) -> bool:
    """Is ``text`` in a language these readings (or the English ones) know?

    True for English and the six languages, and for prose too short to tell
    (fewer than :data:`MIN_WORDS` words outside code: a short reply is judged
    as it always was). False only on positive evidence: a reply mostly in
    another script, or a Latin-script one holding common words
    (:data:`_FOREIGN_WORDS`) or letters (:data:`_FOREIGN_LETTER_RX`) of a
    language these readings do not know. A terse or list-like English reply
    is True (pinned against every English sentence of the Handbook). Never
    raises (cannot tell = True: nothing extra is asked)."""
    try:
        sample = _prose(str(text or "")[: MAX_TEXT * 2])[:MAX_TEXT]
        letters = sum(1 for ch in sample if ch.isalpha())
        latin = len(_LATIN_RX.findall(sample))
        if letters >= MIN_LETTERS and latin / letters < 0.6:
            # Mostly another script (Chinese writes no spaces between words,
            # so its words cannot be counted).
            return False
        words = [w.lower() for w in _WORD_RX.findall(sample)]
        if len(words) < MIN_WORDS:
            return True
        seen = set(words)
        known = max(len(seen & stop) for stop in _STOPWORDS.values())
        foreign = max(len(seen & stop) for stop in _FOREIGN_WORDS.values())
        if foreign >= 2 and foreign > known:
            return False
        if known >= 2:
            return True
        if _FOREIGN_LETTER_RX.search(sample):
            return False
        # No evidence either way: judged as it always was. An English list of
        # names or a terse English fragment is not a foreign reply, and only
        # positive evidence (the words or letters above) says otherwise.
        return True
    except Exception:  # noqa: BLE001
        return True


__all__ = [
    "BLOCKED_WORDS", "FILE_NAME", "asks_for_file", "changed_names", "covered", "said_no", "saved_names",
    "says_gives_up",
]
