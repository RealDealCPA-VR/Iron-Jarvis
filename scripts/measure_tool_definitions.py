"""What each reply used, read from the app's own records.

Read-only: it opens the database in read-only mode, calls no model and changes
nothing. Run it on a COPY when the app is running.

    python -I scripts/measure_tool_definitions.py                  # the dev home
    python -I scripts/measure_tool_definitions.py --root <home>    # <home>/ironjarvis.db
    python -I scripts/measure_tool_definitions.py --db <ironjarvis.db> --days 30
    python -I scripts/measure_tool_definitions.py <tools.json> <ironjarvis.db>

TWO REPORTS.

1. What each reply used (the intelligence plan, release 6). Since that
   release every chat reply and every agent run saves, on its own row, the
   size of each part of what it was sent, how much the model server reused,
   and how long the model took. This prints, for the last 7 days (``--days``):
   tokens sent per reply by part, and the share reused, by REPLY NUMBER in a
   chat (reply 1, 2, 3 ... averaged over chats), for plain chat, coding
   sessions and agent runs apart, with the time to the first word where a
   reply recorded one. "sent" is the provider's own count for the whole reply;
   every size by part is an ESTIMATE (about 3.6 characters a token) of what
   the reply was sent when it began, never a provider's count. Something not
   recorded prints as "-" and a share the provider did not report as
   "not reported": never 0.

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


def tokens(specs) -> int:
    if not specs:
        return 0
    blob = json.dumps(list(specs), ensure_ascii=False, sort_keys=True, default=str)
    return int(len(blob) / RATIO) + 1


def open_read_only(db_path: str) -> sqlite3.Connection:
    """The database, READ-ONLY: nothing here can write to it.

    A copy, or the database of an app that is closed, has no write-ahead file
    beside it: it is opened ``immutable``, which also creates NO side file
    (a plain read-only open of this kind of database leaves ``-wal`` and
    ``-shm`` files next to it). A database the app has open has those files
    already, and its newest rows are in them: it is opened read-only the
    plain way, so they are read too."""
    path = os.path.abspath(db_path)
    wal = path + "-wal"
    in_use = os.path.exists(wal) and os.path.getsize(wal) > 0
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
    """The share of what was sent that the model server reused, over the
    replies whose provider reported a figure (read against the input of the
    calls that reported one)."""
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


def used(db: sqlite3.Connection, days: int = 7, now: "datetime | None" = None, max_number: int = 12,
         out=None) -> int:
    """Print report 1 from *db*. Returns how many records it read."""
    out = out or sys.stdout
    cols = {r[1] for r in db.execute("pragma table_info(agentrun)")}
    print(f"What each reply used (Iron Jarvis's own records, last {days} days)", file=out)
    if "parts_json" not in cols:
        print(OLD_DATABASE, file=out)
        return 0
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    until = now.strftime("%Y-%m-%d %H:%M:%S.999999")
    # Every reply of a chat is numbered in order, whenever it was written, so
    # "reply 9" is the ninth reply of its chat even when the first eight are
    # older than the window. A chat reply's own row has no label
    # (``result``); a summary's row and a step's own row (a picture looked at
    # for a step) have one, and are not replies.
    all_rows = db.execute(
        "select session_id, thread_id, created_at, input_tokens, steps, cache_read_input_tokens, "
        "parts_json, model_ms, model_calls, first_word_ms, coalesce(result, '') from agentrun "
        "order by created_at, id"
    ).fetchall()
    numbers: dict = collections.Counter()
    chat, code, runs, unsaved = [], [], [], 0
    for sid, thread, created, inp, steps, cache_read, parts_json, model_ms, calls, first_ms, label in all_rows:
        is_chat = sid == "chat"
        inside = cutoff <= str(created).replace("T", " ") <= until
        if is_chat and label:
            continue  # a summary's row: not a reply
        if is_chat and not thread:
            if parts_json and inside:
                unsaved += 1
            continue
        group = ("chat", thread) if is_chat else ("run", sid)
        numbers[group] += 1
        if not parts_json or not inside:
            continue
        record = _record(parts_json)
        row = {
            "group": group, "number": numbers[group], "input": int(inp or 0),
            "calls": calls if calls is not None else (int(steps or 0) or None),
            "cache_read": cache_read, "record": record, "model_ms": model_ms, "first_word_ms": first_ms,
        }
        (runs if not is_chat else code if record.get("kind") == "code" else chat).append(row)
    total = len(chat) + len(code) + len(runs)
    if not total:
        print(NOTHING_YET.format(days=days), file=out)
        return 0
    print("\"sent\" is the provider's own count of what was sent for the whole reply (all its model calls). "
          "The columns by part are ESTIMATES of what the reply was sent when it began; \"tools\" is one "
          "call's tool definitions. \"-\" = not recorded.", file=out)
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
    return total


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
    args = ap.parse_args(argv)
    tools_json, db_path = args.tools_json, args.db or args.db_path
    if tools_json and not db_path and not tools_json.lower().endswith(".json"):
        tools_json, db_path = None, tools_json  # one positional that is the database
    db_path = db_path or default_db(args.root)
    if not os.path.isfile(db_path):
        print(f"What each reply used: there is no database at {db_path}. Nothing yet.")
        return 0
    now = datetime.fromisoformat(args.now) if args.now else None
    if now is not None and now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    db = open_read_only(db_path)
    try:
        if tools_json:
            definitions(tools_json, db)
            print()
        used(db, days=max(1, args.days), now=now, max_number=max(1, args.max_replies))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
