"""What each reply used, read from the app's own records.

Read-only: it opens the database in read-only mode, calls no model, changes
nothing and never leaves a file beside the database. Run it on a COPY when
the app is running.

    python -I scripts/measure_tool_definitions.py                  # the dev home
    python -I scripts/measure_tool_definitions.py --root <home>    # <home>/ironjarvis.db
    python -I scripts/measure_tool_definitions.py --db <ironjarvis.db> --days 30
    python -I scripts/measure_tool_definitions.py <tools.json> <ironjarvis.db>
    python -I scripts/measure_tool_definitions.py --db <ironjarvis.db> --days 30 --split 2026-11-27T09:00

THREE REPORTS.

1. What each reply used (the intelligence plan, release 6). Since that
   release every chat reply and every agent run saves, on its own row, the
   size of each part of what it was sent, how much the model reused, and how
   long the model took. This prints, for the last 7 days (``--days``):
   tokens sent per reply by part, and the share reused, by REPLY NUMBER in a
   chat (reply 1, 2, 3 ... averaged over chats), for plain chat, coding
   sessions and agent runs apart, with the time to the first word where a
   reply recorded one. "sent" is the model's own count for the whole reply;
   every size by part is an ESTIMATE (about 3.6 characters a token) of what
   the reply was sent when it began, never the model's count. Something not
   recorded prints as "-" and a share the model did not report as
   "not reported": never 0.

   A REPLY is a chat's usage row with no label. The rows a chat also writes
   for something that is not a reply (a summary of earlier messages, a step's
   own model calls, follow-up suggestions, a coding session's look back for a
   lesson) carry a label and are skipped. Follow-up suggestions and the look
   back were given their label in the version that added this report: rows
   of those two kinds written by an older version have none and still count
   as replies (the report says so).

   A record of a shape this script does not know (a newer version's, or a
   damaged one) is left out and counted, never read as zeros.

   REPLY NUMBERS count from the chat's TRUE first reply. Every usage row of a
   chat is counted, whenever it was written (also before ``--days``). The app
   deletes usage rows older than its history setting (90 days unless
   changed), so an old chat's first rows may be gone: for a saved chat that
   was created before the oldest row this database still holds, the count
   starts from the replies the saved chat itself holds (its messages, counted
   by role; never read as text) that have no row any more. Such a chat's
   replies are never "reply 1", and the report says how many chats were
   numbered that way (their numbers are a lower bound).

3. Do replies start sooner (the intelligence plan, release 7), only when
   ``--split`` gives a moment (ISO, UTC: when the new version was installed).
   For plain chat, the replies before that moment and the replies from it on,
   side by side: the share of what was sent that the model server reused on
   the SECOND AND LATER replies of a chat (the first reply of a chat has
   nothing to reuse), and the time to the first word, by reply number. Read
   the two against each other on the same model server: a share that goes up
   and a first word that comes sooner is the release working.

2. What tool definitions cost per reply (docs/EXTENSIONS-PLAN-2026-11-24.md,
   E0), only when ``tools.json`` (the body of ``GET /tools`` from the running
   app) is given. Definitions are counted the way Iron Jarvis counts them
   (``chat_turn._tool_spec_tokens``): the specs as sorted JSON, at 3.6
   characters a token (``context/budget.estimate_tokens``).
"""

from __future__ import annotations

import argparse
import collections
import itertools
import json
import os
import sqlite3
import statistics as st
import sys
from datetime import datetime, timedelta, timezone

RATIO = 3.6       # context/budget.CHARS_PER_TOKEN
CAP_TOOLS = 24    # chat_turn._MAX_CONNECTOR_TOOLS

#: The parts a reply's record names (``context/breakdown.RECORD_PARTS``), with
#: the column each prints under.
PARTS = (
    ("messages", "messages"), ("attachments", "files"), ("memory", "remembered"),
    ("project", "folder"), ("rules", "rules"), ("skills", "skills"),
    ("summary", "summary"), ("system", "instructions"),
)
NOTHING_YET = (
    "Nothing yet: no reply in the last {days} days has a record of what it used. "
    "Replies written before this version have none."
)
OLD_DATABASE = (
    "Nothing yet: this database was written by an older version, which did not save what a reply used."
)
NOT_REPORTED = "not reported"
#: The record's shape this script reads (``context/breakdown.RECORD_VERSION``):
#: a record of any other version is skipped and counted, as the app's own
#: reader skips it (``decode_record``).
RECORD_VERSION = 1
OLDER_LABELS = (
    "A chat's rows for follow-up suggestions and for a coding session's look back for a lesson are "
    "skipped by their label; rows of those two kinds written before this version have no label and "
    "still count as replies."
)
OLDER_CHATS = (
    "{n} chat{s} older than the oldest usage row this database still holds (the app deletes "
    "older rows): {their} reply numbers start from the replies the saved chat itself still holds, "
    "so they are a lower bound, and never \"reply 1\"."
)
LEFT_BEHIND = (
    "This database's app stopped before folding its newest changes into the main file (there is a "
    "-wal file beside it and nothing has it open). Those changes are not counted here; nothing is "
    "written beside the database."
)
NOT_A_DATABASE = "What each reply used: {path} could not be read as an Iron Jarvis database."
BAD_NOW = "--now must be a time like 2026-11-26T12:00:00, and {value!r} is not one."
BAD_SPLIT = "--split must be a time like 2026-11-26T12:00:00, and {value!r} is not one."


def tokens(specs) -> int:
    if not specs:
        return 0
    blob = json.dumps(list(specs), ensure_ascii=False, sort_keys=True, default=str)
    return int(len(blob) / RATIO) + 1


def wal_state(db_path: str) -> str:
    """How the database's write-ahead file stands: ``closed`` (none, or an
    empty one: a copy, or an app that closed), ``in_use`` (a ``-wal`` WITH its
    ``-shm``: an app has it open) or ``left_behind`` (a non-empty ``-wal`` and
    NO ``-shm``: its app stopped before folding its newest changes in)."""
    path = os.path.abspath(db_path)
    wal = path + "-wal"
    if not (os.path.exists(wal) and os.path.getsize(wal) > 0):
        return "closed"
    return "in_use" if os.path.exists(path + "-shm") else "left_behind"


def open_read_only(db_path: str) -> sqlite3.Connection:
    """The database, READ-ONLY: nothing here can write to it, and NO file is
    ever created beside it.

    A copy, or the database of an app that is closed, has no write-ahead file
    beside it: it is opened ``immutable``, which creates no side file (a
    plain read-only open of this kind of database leaves ``-wal`` and
    ``-shm`` files next to it). A database the app has open has both files
    already, and its newest rows are in them: it is opened read-only the
    plain way, so they are read too. A database whose app died
    (:func:`wal_state` ``left_behind``) would get a ``-shm`` file from a plain
    open: it is opened ``immutable`` too, which reads the main file only
    (the caller says the newest changes are not counted). Never a copy: the
    database may be large."""
    path = os.path.abspath(db_path)
    in_use = wal_state(path) == "in_use"
    uri = "file:" + path.replace("\\", "/") + ("?mode=ro" if in_use else "?mode=ro&immutable=1")
    return sqlite3.connect(uri, uri=True, timeout=5)


# --------------------------------------------------------------------------- #
# 1. What each reply used
# --------------------------------------------------------------------------- #
def _record(raw) -> dict:
    try:
        r = json.loads(raw) if raw else {}
        return r if isinstance(r, dict) else {}
    except (TypeError, ValueError):
        return {}


def _tool_size(record: dict):
    tools = record.get("tools")
    if not isinstance(tools, dict):
        return None
    try:
        total = int((tools.get("builtin") or [0, 0])[1])
        total += sum(int(v[1]) for v in (tools.get("extensions") or {}).values())
        return total
    except (TypeError, ValueError, IndexError):
        return None


def _part(record: dict, key: str):
    """One part's size in a record: 0 for a part a MEASURED record does not
    name (it held nothing), None when the record measured no parts at all."""
    parts = record.get("parts")
    if not isinstance(parts, dict) or not parts:
        return None
    try:
        return int(parts.get(key) or 0)
    except (TypeError, ValueError):
        return None


def _avg(values) -> str:
    vals = [v for v in values if v is not None]
    return f"{sum(vals) / len(vals):,.0f}" if vals else "-"


def _secs(values) -> str:
    vals = [v for v in values if v is not None]
    return f"{sum(vals) / len(vals) / 1000:.1f}" if vals else "-"


def _reused(rows) -> str:
    """The share of what was sent that the model reused, over the replies
    whose provider reported a figure (read against the input of the calls
    that reported one)."""
    read = against = 0
    for r in rows:
        if r["cache_read"] is None:
            continue
        read += int(r["cache_read"])
        against += int(r["record"].get("cache_input") or r["input"] or 0)
    return f"{100 * read / against:.1f}%" if against > 0 else NOT_REPORTED


def _table(title: str, unit: str, rows: list, max_number: int, out) -> None:
    groups = len({r["group"] for r in rows})
    units = f"repl{'y' if len(rows) == 1 else 'ies'}" if unit == "reply" else f"run{'' if len(rows) == 1 else 's'}"
    where = ("chat" if unit == "reply" else "session") + ("" if groups == 1 else "s")
    print(f"\n{title}: {len(rows)} {units} in {groups} {where}", file=out)
    head = [unit, "n", "sent", "calls"] + [label for _, label in PARTS] + ["tools", "reused", "model s", "first word s"]
    lines = [head]
    by_number: dict = collections.defaultdict(list)
    for r in rows:
        by_number[min(r["number"], max_number + 1)].append(r)
    for number in sorted(by_number):
        part = by_number[number]
        label = str(number) if number <= max_number else f"{max_number + 1}+"
        lines.append(
            [label, str(len(part)), _avg(r["input"] for r in part), _avg(r["calls"] for r in part)]
            + [_avg(_part(r["record"], key) for r in part) for key, _ in PARTS]
            + [_avg(_tool_size(r["record"]) for r in part), _reused(part),
               _secs(r["model_ms"] for r in part), _secs(r["first_word_ms"] for r in part)]
        )
    widths = [max(len(row[i]) for row in lines) for i in range(len(head))]
    for row in lines:
        print("  " + "  ".join(cell.rjust(w) for cell, w in zip(row, widths)), file=out)
    left_out = sum(1 for r in rows if r["record"].get("left_out"))
    if left_out:
        print(f"  In {left_out} of these, earlier messages were left out so the reply fits: there "
              f"\"messages\" is what the chat held, not what was sent.", file=out)


def _replies_before_the_rows(db: sqlite3.Connection, all_rows: list) -> dict:
    """``{chat id: n}``: for a saved chat created BEFORE the oldest usage row
    this database still holds, how many of its replies have no row any more
    (at least 1: its first reply's row is gone). The saved chat's own
    messages are counted by role inside SQLite; their text is never read
    here. A chat created after the oldest row has every row: it is not in
    the answer. Never raises: a database with no chat table, or one this
    SQLite cannot read that way, numbers by the rows alone, as before."""
    oldest = min((str(r[2]).replace("T", " ") for r in all_rows), default="")
    if not oldest:
        return {}
    rows_of: dict = collections.Counter(
        r[1] for r in all_rows if r[0] == "chat" and r[1] and not r[10]
    )
    try:
        chats = db.execute(
            "select t.id, t.created_at, case when json_valid(t.messages_json) then (select count(*) from "
            "json_each(t.messages_json) e where json_extract(e.value, '$.role') = 'assistant') else 0 end "
            "from chatthreadrecord t"
        ).fetchall()
    except sqlite3.Error:
        return {}
    out: dict = {}
    for chat_id, created, replies in chats:
        if chat_id in rows_of and created and str(created).replace("T", " ") < oldest:
            out[chat_id] = max(1, int(replies or 0) - rows_of[chat_id])
    return out


def _older_note(rows: list) -> str:
    """The one line for chats numbered from their saved messages ("" = none)."""
    n = len({r["group"] for r in rows if r.get("older")})
    return OLDER_CHATS.format(n=n, s=" is" if n == 1 else "s are", their="its" if n == 1 else "their") if n else ""


def _collect(db: sqlite3.Connection, days: int, now: "datetime | None"):
    """Every reply and run with a record inside the window, numbered in its
    chat: ``(chat, code, runs, unsaved, unknown)`` (*unknown*: records of a
    shape this script does not know, left out and counted). Shared by reports
    1 and 3: ONE reader, so a labelled row is never a reply in either."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    until = now.strftime("%Y-%m-%d %H:%M:%S.999999")
    # Every reply of a chat is numbered in order, whenever it was written, so
    # "reply 9" is the ninth reply of its chat even when the first eight are
    # older than the window. A chat reply's own row has no label
    # (``result``); a summary's row, a step's own row (a picture looked at
    # for a step), follow-up suggestions and a coding session's look back
    # for a lesson have one, and are not replies.
    all_rows = db.execute(
        "select session_id, thread_id, created_at, input_tokens, steps, cache_read_input_tokens, "
        "parts_json, model_ms, model_calls, first_word_ms, coalesce(result, '') from agentrun "
        "order by created_at, id"
    ).fetchall()
    numbers: dict = collections.Counter()
    # A chat older than the oldest row still held starts past the replies
    # whose rows the app has deleted (see the module's note on reply numbers).
    before_rows = _replies_before_the_rows(db, all_rows)
    for chat_id, gone in before_rows.items():
        numbers[("chat", chat_id)] = gone
    chat, code, runs, unsaved, unknown = [], [], [], 0, 0
    for sid, thread, created, inp, steps, cache_read, parts_json, model_ms, calls, first_ms, label in all_rows:
        is_chat = sid == "chat"
        inside = cutoff <= str(created).replace("T", " ") <= until
        if is_chat and label:
            continue  # a labelled row (a summary, follow-up suggestions ...): not a reply
        if is_chat and not thread:
            if parts_json and inside:
                unsaved += 1
            continue
        group = ("chat", thread) if is_chat else ("run", sid)
        numbers[group] += 1
        if not parts_json or not inside:
            continue
        record = _record(parts_json)
        if record.get("v") != RECORD_VERSION:
            unknown += 1  # a shape this script does not know: never read as zeros
            continue
        row = {
            "group": group, "number": numbers[group], "input": int(inp or 0),
            "calls": calls if calls is not None else (int(steps or 0) or None),
            "cache_read": cache_read, "record": record, "model_ms": model_ms, "first_word_ms": first_ms,
            "created": str(created).replace("T", " "),
            "older": bool(is_chat and thread in before_rows),
        }
        (runs if not is_chat else code if record.get("kind") == "code" else chat).append(row)
    return chat, code, runs, unsaved, unknown


def _skipped(unknown: int) -> str:
    """The one line for records of a shape this script does not know."""
    return (
        f"{unknown} record{'' if unknown == 1 else 's'} of a shape this script does not know (a newer "
        f"version's, or damaged) {'is' if unknown == 1 else 'are'} left out."
    ) if unknown else ""


def used(db: sqlite3.Connection, days: int = 7, now: "datetime | None" = None, max_number: int = 12,
         out=None) -> int:
    """Print report 1 from *db*. Returns how many records it read."""
    out = out or sys.stdout
    cols = {r[1] for r in db.execute("pragma table_info(agentrun)")}
    print(f"What each reply used (Iron Jarvis's own records, last {days} days)", file=out)
    if "parts_json" not in cols:
        print(OLD_DATABASE, file=out)
        return 0
    chat, code, runs, unsaved, unknown = _collect(db, days, now)
    total = len(chat) + len(code) + len(runs)
    skipped = _skipped(unknown)
    if not total:
        print(NOTHING_YET.format(days=days), file=out)
        if skipped:
            print(skipped, file=out)
        return 0
    print("\"sent\" is the model's own count of what was sent for the whole reply (all its model calls). "
          "The columns by part are ESTIMATES of what the reply was sent when it began; \"tools\" is one "
          "call's tool definitions. \"-\" = not recorded.", file=out)
    if chat or code:
        print(OLDER_LABELS, file=out)
    if chat:
        _table("Plain chat", "reply", chat, max_number, out)
    if code:
        _table("Coding sessions", "reply", code, max_number, out)
    if runs:
        _table("Agent runs", "run", runs, max_number, out)
        steps_sent = [r["record"]["sent"] for r in runs if isinstance(r["record"].get("sent"), dict)
                      and r["record"]["sent"].get("steps")]
        if steps_sent:
            n = sum(int(s["steps"]) for s in steps_sent)
            print(f"  Per step, over {n} steps (estimates): instructions "
                  f"{sum(int(s.get('system') or 0) for s in steps_sent) / n:,.0f}, messages "
                  f"{sum(int(s.get('messages') or 0) for s in steps_sent) / n:,.0f}, tools "
                  f"{sum(int(s.get('tools') or 0) for s in steps_sent) / n:,.0f}", file=out)
    if unsaved:
        print(f"\n{unsaved} repl{'y' if unsaved == 1 else 'ies'} of chats that were never saved "
              f"{'is' if unsaved == 1 else 'are'} left out (no chat to number them in).", file=out)
    if skipped:
        print(("" if unsaved else "\n") + skipped, file=out)
    older = _older_note(chat + code)
    if older:
        print(("" if (unsaved or skipped) else "\n") + older, file=out)
    return total


# --------------------------------------------------------------------------- #
# 3. Do replies start sooner (release 7)
# --------------------------------------------------------------------------- #
def sooner(db: sqlite3.Connection, split: datetime, days: int = 7, now: "datetime | None" = None,
           max_number: int = 12, out=None) -> int:
    """Print report 3 from *db*: plain chat's replies before *split* and from
    it on. Returns how many replies it read."""
    out = out or sys.stdout
    at = split.strftime("%Y-%m-%d %H:%M:%S")
    print(f"\nDo replies start sooner (plain chat, last {days} days, split at {at} UTC)", file=out)
    cols = {r[1] for r in db.execute("pragma table_info(agentrun)")}
    if "parts_json" not in cols:
        print(OLD_DATABASE, file=out)
        return 0
    chat, _code, _runs, _unsaved, unknown = _collect(db, days, now)
    if not chat:
        print(NOTHING_YET.format(days=days), file=out)
        if unknown:
            print(_skipped(unknown), file=out)
        return 0
    print("\"reused\" is the share of what was sent that the model server read from its saved copy, over "
          "the replies whose model server reported one. The first reply of a chat has nothing to reuse: "
          "read the line for the second and later replies.", file=out)
    if unknown:
        print(_skipped(unknown), file=out)
    older = _older_note(chat)
    if older:
        print(older, file=out)
    for label, part in (("before", [r for r in chat if r["created"] < at]),
                        ("from it on", [r for r in chat if r["created"] >= at])):
        later = [r for r in part if r["number"] >= 2]
        print(f"\n  {label}: {len(part)} repl{'y' if len(part) == 1 else 'ies'} in "
              f"{len({r['group'] for r in part})} chats", file=out)
        if not part:
            continue
        print(f"    second and later replies ({len(later)}): reused {_reused(later)}, "
              f"first word after {_secs(r['first_word_ms'] for r in later)} s", file=out)
        head = ["reply", "n", "sent", "reused", "first word s"]
        lines = [head]
        by_number: dict = collections.defaultdict(list)
        for r in part:
            by_number[min(r["number"], max_number + 1)].append(r)
        for number in sorted(by_number):
            rows = by_number[number]
            lines.append([str(number) if number <= max_number else f"{max_number + 1}+", str(len(rows)),
                          _avg(r["input"] for r in rows), _reused(rows), _secs(r["first_word_ms"] for r in rows)])
        widths = [max(len(row[i]) for row in lines) for i in range(len(head))]
        for row in lines:
            print("    " + "  ".join(cell.rjust(w) for cell, w in zip(row, widths)), file=out)
    return len(chat)


# --------------------------------------------------------------------------- #
# 2. What tool definitions cost (E0)
# --------------------------------------------------------------------------- #
def definitions(tools_path: str, db: sqlite3.Connection) -> None:
    with open(tools_path, encoding="utf-8") as fh:
        tools = json.load(fh)["tools"]
    ext: dict[str, list] = collections.defaultdict(list)
    builtin = []
    for t in tools:
        name = t["name"]
        (ext[name.split("__")[1]] if name.startswith("mcp__") else builtin).append(t)

    print(f"built-in tools: {len(builtin)}, all together {tokens(builtin):,} tokens, "
          f"median one tool {int(st.median(tokens([t]) for t in builtin))}")
    print("each extension:")
    for key, specs in sorted(ext.items(), key=lambda kv: tokens(kv[1])):
        per = sorted(tokens([t]) for t in specs)
        print(f"  {key:20s} {len(specs):2d} tools {tokens(specs):6,} tokens "
              f"(one tool: {per[0]}-{per[-1]})")

    one = sorted(tokens(v) for v in ext.values())
    print(f"1 ticked: min {one[0]:,} median {int(st.median(one)):,} max {one[-1]:,}")
    three = sorted(
        tokens([t for k in combo for t in ext[k]])
        for combo in itertools.combinations(ext, 3)
    )
    if three:
        print(f"3 ticked ({len(three)} ways): min {three[0]:,} "
              f"median {int(st.median(three)):,} max {three[-1]:,}")
    every = [t for v in ext.values() for t in v]
    by_size = sorted(every, key=lambda t: tokens([t]))
    print(f"all {len(ext)} ticked: {len(every)} tools, uncapped {tokens(every):,}; "
          f"capped at {CAP_TOOLS}: {tokens(by_size[:CAP_TOOLS]):,} to "
          f"{tokens(by_size[-CAP_TOOLS:]):,}")

    rows = db.execute(
        "select input_tokens, steps, provider from agentrun "
        "where session_id='chat' and input_tokens>0 and steps>0"
    ).fetchall()
    own = [i / s for i, s, p in rows if not p.startswith("claude")]
    claude = [i / s for i, s, p in rows if p.startswith("claude")]
    print(f"saved chat replies: {len(rows)}")
    for label, vals in (("own models", own), ("Claude", claude)):
        if not vals:
            continue
        base = st.median(vals)
        print(f"  {label}: n={len(vals)}, median input per model call {int(base):,}")
        for name, cost in (("smallest extension", one[0]), ("median extension", st.median(one)),
                           ("largest extension", one[-1]),
                           ("all, capped, largest", tokens(by_size[-CAP_TOOLS:]))):
            print(f"    + {name:22s} {cost:7,.0f} tokens = "
                  f"{100 * cost / (base + cost):4.1f}% of the call")
    sent(db)


def sent(db) -> None:
    """What saved replies were really sent (the columns v1.572.0 added).
    Measured from each reply's own row; says so when there is nothing yet."""
    cols = {r[1] for r in db.execute("pragma table_info(agentrun)")}
    if "tools_sent_json" not in cols:
        print("what replies were sent: not saved by this version (needs v1.572.0)")
        return
    rows = db.execute(
        "select provider, input_tokens, cache_read_input_tokens, tools_sent_json, "
        "tool_spec_tokens from agentrun where session_id='chat' and input_tokens>0"
    ).fetchall()
    cached = [(i, c) for _, i, c, _, _ in rows if c is not None]
    tools = [(p, i, json.loads(j), t or 0) for p, i, _, j, t in rows if j is not None]
    print(f"replies with a cache figure: {len(cached)}; with their tools saved: {len(tools)}")
    if cached:
        total = sum(i for i, _ in cached)
        print(f"  served from the provider's cache: "
              f"{100 * sum(c for _, c in cached) / total:4.1f}% of what was sent")
    for label, own in (("own models", True), ("Claude", False)):
        part = [(i, names, t) for p, i, names, t in tools if p.startswith("claude") != own]
        if not part:
            continue
        total = sum(i for i, _, _ in part)
        ext = sum(1 for _, names, _ in part if any(n.startswith("mcp__") for n in names))
        print(f"  {label}: n={len(part)}, tool definitions are "
              f"{100 * sum(t for _, _, t in part) / total:4.1f}% of what was sent "
              f"(an estimate); median {int(st.median(len(n) for _, n, _ in part))} tools a reply; "
              f"{ext} replies had an extension's tool")


# --------------------------------------------------------------------------- #
def default_db(root: "str | None") -> str:
    """``<root>/ironjarvis.db``; the root defaults to the DEV home
    (``~/.ironjarvis``), never the installed app's."""
    home = root or os.path.join(os.path.expanduser("~"), ".ironjarvis")
    return os.path.join(home, "ironjarvis.db")


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="What each reply used, from the app's own records (read-only).")
    ap.add_argument("tools_json", nargs="?", help="a saved GET /tools body: also print what tool definitions cost")
    ap.add_argument("db_path", nargs="?", help="the database (the older form: <tools.json> <ironjarvis.db>)")
    ap.add_argument("--db", help="the database file to read (read-only)")
    ap.add_argument("--root", help="the state home holding ironjarvis.db (default: the dev home, ~/.ironjarvis)")
    ap.add_argument("--days", type=int, default=7, help="how many days back to read (default 7)")
    ap.add_argument("--max-replies", type=int, default=12, help="reply numbers past this print as one row")
    ap.add_argument("--now", help="read as if it were this time (ISO, UTC), for a copy taken earlier")
    ap.add_argument("--split", help="a moment (ISO; read as UTC when it names no zone, as --now is): also "
                                    "print plain chat's reuse and time to the first word before it and "
                                    "from it on")
    args = ap.parse_args(argv)
    tools_json, db_path = args.tools_json, args.db or args.db_path
    if tools_json and not db_path and not tools_json.lower().endswith(".json"):
        tools_json, db_path = None, tools_json  # one positional that is the database
    # A bad --split ends here in one sentence, before anything is looked for or opened.
    try:
        split = datetime.fromisoformat(args.split) if args.split else None
    except ValueError:
        print(BAD_SPLIT.format(value=args.split), file=sys.stderr)
        return 2
    if split is not None and split.tzinfo is not None:
        split = split.astimezone(timezone.utc).replace(tzinfo=None)
    db_path = db_path or default_db(args.root)
    if not os.path.isfile(db_path):
        print(f"What each reply used: there is no database at {db_path}. Nothing yet.")
        return 0
    try:
        now = datetime.fromisoformat(args.now) if args.now else None
    except ValueError:
        print(BAD_NOW.format(value=args.now), file=sys.stderr)
        return 2
    if now is not None and now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    left_behind = wal_state(db_path) == "left_behind"
    try:
        db = open_read_only(db_path)
    except sqlite3.Error:
        print(NOT_A_DATABASE.format(path=db_path), file=sys.stderr)
        return 2
    try:
        # The first read: a file that is not a database, or one with no
        # table of replies and runs, ends here in one sentence.
        if not db.execute("pragma table_info(agentrun)").fetchall():
            print(NOT_A_DATABASE.format(path=db_path), file=sys.stderr)
            return 2
        if left_behind:
            print(LEFT_BEHIND)
        if tools_json:
            definitions(tools_json, db)
            print()
        used(db, days=max(1, args.days), now=now, max_number=max(1, args.max_replies))
        if split is not None:
            sooner(db, split, days=max(1, args.days), now=now, max_number=max(1, args.max_replies))
    except sqlite3.DatabaseError:
        print(NOT_A_DATABASE.format(path=db_path), file=sys.stderr)
        return 2
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
