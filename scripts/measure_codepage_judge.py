"""Measure the OEM/ANSI code-page judge (sandbox/native._plausibility).

Runs ``_as_text`` over client names and memo/dir/tree lines written in EACH
page of a pair, on BOTH pairs a Windows box can have (US: cp437 + cp1252;
Western-European: cp850 + cp1252), with ``_fallback_codecs`` pointed at the
pair. Prints every loss and the count per pair — the numbers quoted in
``_decode_fallback``'s docstring come from here. Re-run it whenever a rule in
``_plausibility`` changes:

    uv run python scripts/measure_codepage_judge.py            # the current judge
    uv run python scripts/measure_codepage_judge.py old.py     # another native.py, for a BEFORE run

Not a test: the judge is pinned by tests/test_shell_output_encoding_v1288.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import iron_jarvis.sandbox  # noqa: E402,F401  (the package, so a relative import resolves)

if len(sys.argv) > 1:  # a path to another native.py (e.g. the committed one) for a BEFORE run
    import importlib.util

    spec = importlib.util.spec_from_file_location("iron_jarvis.sandbox.native_other", sys.argv[1])
    native = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = native
    spec.loader.exec_module(native)
else:
    from iron_jarvis.sandbox import native  # noqa: E402

BASE = [
    "JOSÉ", "GARCÍA", "MUÑOZ", "NÚÑEZ", "Müller", "José", "García", "Muñoz",
    "Núñez", "María", "Pérez", "Sánchez", "Peña", "Jiménez", "Hernández",
    "Rodríguez", "López", "Ramírez", "Gómez", "Díaz", "Vázquez", "Ángel",
    "Iñaki", "Andrés", "Inés", "Raúl", "Sofía", "Björn", "François", "Zoë",
    # doer extras: all-caps exports, French/German/Nordic/Catalan clients
    "Óscar", "Geneviève", "Renée", "Chloé", "Noël", "Françoise", "Ödön",
    "Straße", "Solà", "Vilà", "Ångström", "Søren", "Çelik", "Ñoño",
]
NAMES = sorted({*BASE, *(n.upper() for n in BASE)})
CONTEXTS = [
    "{n}",                       # a lone word (e.g. one-line dir /b)
    "{n}.pdf",                   # dir /b entry
    "{n},1200.00",               # csv row
    "name,amount\n{n},1200.00\nJohn Smith,300.00\n",  # csv with ASCII rows
    "{n} 1040 2025.pdf\nplain.pdf\n",
    "09/22/2026  02:10 PM            12,345 {n} 1040.pdf\r\n",   # dir line
    "Get-Item : Cannot find path 'C:\\Clients\\{n}' because it does not exist.\r\n",
    "Client,Note\r\n{n},paid\u2014see memo\r\n",                 # ANSI-only (em dash)
    "{n} \u2013 \u201cextension filed\u201d\r\n",                # ANSI-only (typography)
    "C:.\r\n\u251c\u2500\u2500\u2500{n}\r\n\u2502   \u2514\u2500\u2500\u25001040.pdf\r\n",  # OEM-only (tree)
]

def run(pair):
    native._fallback_codecs = lambda: pair
    losses, total = 0, 0
    seen = set()
    for origin in pair:
        for n in NAMES:
            for c in CONTEXTS:
                text = c.format(n=n)
                try:
                    raw = text.encode(origin)
                except UnicodeEncodeError:
                    continue
                try:
                    raw.decode("utf-8")
                    continue  # never reaches the judge
                except UnicodeDecodeError:
                    pass
                total += 1
                got = native._as_text(raw)
                if got != text:
                    losses += 1
                    key = (origin, n)
                    if key not in seen:
                        seen.add(key)
                        print(f"  LOSS origin={origin:<6} want={text!r:<60} got={got!r}")
    print(f"pair={pair}: {losses} losses / {total} judged ({len(seen)} distinct name+origin)\n")
    return losses, total

for pair in (("cp437", "cp1252"), ("cp850", "cp1252")):
    print(f"=== {pair} ===")
    run(pair)
