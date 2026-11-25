"""What tool definitions cost per reply (docs/EXTENSIONS-PLAN-2026-11-24.md, E0).

Read-only: it opens a saved copy of ``GET /tools`` and the database in
read-only mode, calls no model and changes nothing.

    python -I scripts/measure_tool_definitions.py <tools.json> <ironjarvis.db>

``tools.json`` is the body of ``GET /tools`` from the running app. Definitions
are counted the way Iron Jarvis counts them (``chat_turn._tool_spec_tokens``):
the specs as sorted JSON, at 3.6 characters a token
(``context/budget.estimate_tokens``). That is an estimate, not a tokenizer. The
saved replies' input figures are the providers' own counts.

Since v1.572.0 each chat reply's row also saves what it was really sent (the
provider's cache counts, the tool names, the definitions' estimated size); the
last part of the output reads those, so a change can be compared before and
after on real replies.
"""

from __future__ import annotations

import collections
import itertools
import json
import sqlite3
import statistics as st
import sys

RATIO = 3.6       # context/budget.CHARS_PER_TOKEN
CAP_TOOLS = 24    # chat_turn._MAX_CONNECTOR_TOOLS


def tokens(specs) -> int:
    if not specs:
        return 0
    blob = json.dumps(list(specs), ensure_ascii=False, sort_keys=True, default=str)
    return int(len(blob) / RATIO) + 1


def main(tools_path: str, db_path: str) -> None:
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

    db = sqlite3.connect("file:" + db_path.replace("\\", "/") + "?mode=ro", uri=True, timeout=5)
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


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
