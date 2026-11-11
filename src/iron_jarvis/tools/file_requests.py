"""Does a chat message ask for the user's FILES to be renamed or moved?
(v1.473.0)

THE GAP. Chat never offered ``rename_file``: nothing armed it, so a request
to rename a folder of statements left the model one way to do it, the shell.
Measured on v1.472.1 with a real model: "Rename the statement files here so
each name starts with its month" ran out of steps trying shell commands and
renamed nothing. And a shell rename has no Undo, where ``rename_file`` is a
journaled change the user can take back.

WHAT THIS IS. One small matcher, used by ``chat_turn._resolve_armed_tools``
to arm ``rename_file`` in its own place (like the calculator: no rule of
``tools/autoselect`` scores it, so that selector's output is unchanged).
With the script tool in the same turn, the stream lane can then offer
``apply_changes``: many renames after ONE approval.

HOW IT READS A MESSAGE (token based, one pass, linear):

* "rename" followed, within a few words and with no preposition between, by
  a FILE word ("the statement files", "every pdf", "these scanned
  invoices") or a file name ("report.docx");
* "move" the same way, and only when the sentence also says where: a folder
  word after "to" / "into", or a path;
* never a question about renaming, never after "don't", never when the
  thing renamed is a sheet, a column, a chat, a project and the like (other
  tools own those).

When unsure it says no: the model can still ask for a hand-off, as before.
"""

from __future__ import annotations

import re

__all__ = ["wants_file_rename"]

_MAX_CHARS = 4000
#: A sentence ends at ! ? ; a newline, or a full stop followed by space (a
#: full stop inside "report.docx" does not end one).
_SENTENCE = re.compile(r"[!?;\n]+|\.(?:\s+|$)")
_WORD = re.compile(r"[a-z0-9][a-z0-9'._-]*")
#: A token that is a file's name: "report.docx", "2026-01-statement.csv".
_FILE_NAME = re.compile(r"^[a-z0-9][a-z0-9._'-]*\.[a-z][a-z0-9]{1,4}$")

#: How far after the verb the file word may sit ("rename all of my scanned
#: client invoices").
_REACH = 6

#: What the user has files OF.
_FILE_WORDS = frozenset({
    "file", "files", "folder", "folders", "pdf", "pdfs", "photo", "photos",
    "picture", "pictures", "image", "images", "document", "documents", "doc",
    "docs", "scan", "scans", "statement", "statements", "invoice", "invoices",
    "receipt", "receipts", "workbook", "workbooks", "spreadsheet",
    "spreadsheets", "csv", "csvs", "attachment", "attachments", "download",
    "downloads", "screenshot", "screenshots", "recording", "recordings",
    "export", "exports", "return", "returns", "w2s", "1099s", "k1s",
})
#: Words that end the look: a preposition (what follows is somewhere else),
#: or a thing another tool renames or moves.
_STOP = frozenset({
    "in", "of", "on", "to", "into", "from", "for", "as", "by", "with", "at",
    "about", "onto", "over", "up", "down", "forward", "ahead", "along",
    "sheet", "sheets", "tab", "tabs", "column", "columns", "row", "rows",
    "cell", "cells", "chat", "chats", "thread", "project", "projects",
    "skill", "skills", "agent", "agents", "workflow", "workflows",
    "schedule", "schedules", "rule", "rules", "terminal", "session",
    "variable", "variables", "function", "functions", "class", "classes",
    "method", "methods", "module", "branch", "branches", "repo", "meeting",
    "call", "appointment", "deadline", "date", "event", "money", "funds",
    "it", "that", "this",
})
#: A file word followed by one of these is naming something IN a file ("the
#: invoice date", "the statement total"), not the file.
_PART_OF = frozenset({
    "date", "dates", "total", "totals", "number", "numbers", "amount",
    "amounts", "line", "lines", "header", "heading", "title", "section",
    "page", "pages", "field", "fields", "column", "columns", "row", "rows",
    "name's", "balance",
})
#: "all of the files", "each of these pdfs": the "of" is not a preposition
#: leading somewhere else.
_SOME_OF = frozenset({"all", "each", "both", "some", "any", "one", "most", "many", "every"})
#: Right before the verb, these make it not a request.
_NOT = frozenset({"don't", "dont", "not", "never", "without", "didn't", "cannot", "can't", "won't"})
#: "…into a folder called archive", "…to the 2025 directory".
_PLACES = frozenset({"folder", "folders", "directory", "subfolder", "archive", "archives"})
_TO = frozenset({"to", "into", "in", "under", "inside"})


def _file_ahead(words: list[str], at: int) -> bool:
    """A file word (or a file's name) within reach after ``words[at]``, with
    nothing between that says the object is something else."""
    for j in range(at + 1, min(len(words), at + 1 + _REACH)):
        word = words[j]
        if _FILE_NAME.match(word):
            return True
        if word in _FILE_WORDS:
            nxt = words[j + 1] if j + 1 < len(words) else ""
            # "the statement files": a file word describing the next file
            # word is fine; "the invoice date" is a part of a file.
            return nxt not in _PART_OF
        if word == "of" and words[j - 1] in _SOME_OF:
            continue  # "all of my scanned invoices"
        if word in _STOP:
            return False
    return False


def _says_where(words: list[str], at: int, raw: str) -> bool:
    """After the verb, the sentence names a place: "into a folder…", or a
    path."""
    if "\\" in raw or "/" in raw:
        return True
    for j in range(at + 1, len(words) - 1):
        if words[j] in _TO and any(w in _PLACES for w in words[j + 1: j + 5]):
            return True
    return False


def wants_file_rename(text: str) -> bool:
    """True when *text* asks for files to be renamed or moved. Never raises;
    a fault says no."""
    try:
        raw = str(text or "")[:_MAX_CHARS]
        low = raw.lower()
        if "rename" not in low and "move" not in low:
            return False
        asked = False
        for sentence in _SENTENCE.split(low):
            words = _WORD.findall(sentence)
            for at, word in enumerate(words):
                if word not in ("rename", "move"):
                    continue
                if any(w in _NOT for w in words[max(0, at - 3): at]):
                    continue
                if not _file_ahead(words, at):
                    continue
                if word == "move" and not _says_where(words, at, sentence):
                    continue
                asked = True
                break
            if asked:
                break
        if not asked:
            return False
        from ..settings.records import is_question

        return not is_question(raw)
    except Exception:  # noqa: BLE001 — a matcher fault arms nothing
        return False
