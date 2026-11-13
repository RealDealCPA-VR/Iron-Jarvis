"""Write or check ``src/iron_jarvis/capability_search/phrasings.py``: everyday
phrasings for each entry of the capability index (v1.482.0).

WHY: the index is built from the product's own text (a tool's description, a
setting's help), and people do not use those words ("a helper" for an agent,
"stop asking me" for the approvals setting). For each entry a model writes
eight short ways a non-technical person might ask for it, FROM THAT ENTRY'S
OWN TEXT ALONE. Never from a request set: the measured sets under
``tests/fixtures/capability_*`` must never guide this file, and no phrase is
ever edited by hand because a request misses (regenerate the entry instead).

The phrasings are search words only: they are never shown to the model or the
user (``tool.entry_line`` builds every answer line from the title and the
product's own sentences).

usage:
  uv run python scripts/gen_capability_phrasings.py --check
      fails when an index entry has no phrasings or a phrasing names an entry
      that no longer exists (CI runs this through tests/test_capability_phrasings_v1481.py).
  uv run python scripts/gen_capability_phrasings.py --prompts DIR
      writes one prompt per 25 entries into DIR (p00.txt, p01.txt, ...) for the
      entries that have no phrasings yet (``--all``: every entry).
  uv run python scripts/gen_capability_phrasings.py --collect DIR
      reads DIR/p*.out (each the model's answer to the prompt of the same name,
      e.g. from ``claude -p --model haiku "$(cat p00.txt)" > p00.out`` run in an
      EMPTY folder) and writes the module, keeping existing entries.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

MODULE = ROOT / "src" / "iron_jarvis" / "capability_search" / "phrasings.py"
BATCH = 25
PER_ENTRY = 8
MAX_CHARS = 80

HEAD = (
    "Below is a list of features of a desktop AI assistant app (each with its id and the app's own "
    "short description). For each feature, write 8 different short ways an ordinary, non-technical "
    "person might ask the assistant for it, or describe what they want, in plain everyday English. "
    "3 to 8 words each. Use the words such a person would use, not the app's own technical names. "
    "Make the 8 wordings different from each other. For a line that says the app does NOT do "
    "something, write how a person would ask for that thing.\n"
    "Output exactly one line per feature in the form: ID | phrase; phrase; phrase; phrase; phrase; "
    "phrase; phrase; phrase\n"
    "No other text. Do not use any tools; just answer.\n\nFEATURES:\n"
)

_KIND_WORDS = {"tool": "action", "setting": "setting", "page": "needs a page", "not_offered": "NOT offered"}


def entries() -> list:
    """The index entries of a fresh install (a throwaway home)."""
    from iron_jarvis.capability_search import index as CI
    from iron_jarvis.platform import build_platform

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as home:
        # defer_mcp: no tool pack is started (their tools are never in the index).
        platform = build_platform(home, defer_mcp=True)
        found = list(CI.build(platform, phrasings=False).entries)
        try:
            platform.engine.dispose()
        except Exception:  # noqa: BLE001 — only so the throwaway home can be removed
            pass
        return found


def describe(e) -> str:
    body = e.line if e.kind in ("page", "not_offered") else e.text
    body = " ".join(body.split())[:600]
    return f"{e.id} ({_KIND_WORDS[e.kind]}: {e.title}) | {body}"


def clean(phrase: str) -> str:
    """One phrase as it may be kept: one line, no address, no link, bounded."""
    phrase = " ".join(str(phrase).split()).strip(" .;\"'")
    if not phrase or "@" in phrase or "://" in phrase or len(phrase) > MAX_CHARS:
        return ""
    return phrase


def current() -> dict:
    from iron_jarvis.capability_search.phrasings import PHRASINGS

    return {k: list(v) for k, v in PHRASINGS.items()}


def write(table: dict) -> None:
    lines = [
        '"""Everyday phrasings for the capability index (GENERATED: do not edit).',
        "",
        "Written by ``scripts/gen_capability_phrasings.py``: for each index entry, a",
        "model wrote eight short ways a non-technical person might ask for it, from",
        "that entry's own text only. Search words only, never shown to anyone.",
        '"""',
        "",
        "PHRASINGS: dict[str, tuple[str, ...]] = {",
    ]
    for key in sorted(table):
        phrases = [p for p in (clean(x) for x in table[key]) if p][:PER_ENTRY]
        if not phrases:
            continue
        lines.append(f"    {key!r}: (")
        lines.extend(f"        {p!r}," for p in phrases)
        lines.append("    ),")
    lines.append("}")
    MODULE.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def problems(found: list, table: dict) -> list[str]:
    ids = {e.id for e in found}
    out = [f"no phrasings for {i}" for i in sorted(ids - set(table))]
    out += [f"phrasings for {k}, which is no longer in the index" for k in sorted(set(table) - ids)]
    for key, phrases in table.items():
        if not (1 <= len(phrases) <= PER_ENTRY):
            out.append(f"{key}: {len(phrases)} phrasings")
        out += [f"{key}: phrase not clean: {p!r}" for p in phrases if clean(p) != p]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--prompts")
    ap.add_argument("--collect")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    if a.check:
        bad = problems(entries(), current())
        for line in bad:
            print(line)
        if bad:
            print("regenerate: see the docstring of scripts/gen_capability_phrasings.py")
            return 1
        print("phrasings are current")
        return 0
    if a.prompts:
        out = pathlib.Path(a.prompts)
        out.mkdir(parents=True, exist_ok=True)
        have = set() if a.all else set(current())
        todo = [e for e in entries() if e.id not in have]
        for n in range(0, len(todo), BATCH):
            body = "\n".join(describe(e) for e in todo[n:n + BATCH])
            (out / f"p{n // BATCH:02d}.txt").write_text(HEAD + body + "\n", encoding="utf-8", newline="\n")
        print(len(todo), "entries;", (len(todo) + BATCH - 1) // BATCH, "prompts")
        return 0
    if a.collect:
        ids = {e.id for e in entries()}
        table = {k: v for k, v in current().items() if k in ids}
        for p in sorted(pathlib.Path(a.collect).glob("p*.out")):
            for line in p.read_text(encoding="utf-8").splitlines():
                if "|" not in line:
                    continue
                key, rest = (x.strip() for x in line.split("|", 1))
                key = re.sub(r"\s*\(.*$", "", key)
                if key in ids:
                    table[key] = [x.strip() for x in rest.split(";") if x.strip()]
        write(table)
        print(len(table), "of", len(ids), "entries have phrasings")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
