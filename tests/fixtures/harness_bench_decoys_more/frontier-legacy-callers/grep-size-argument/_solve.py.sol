"""Decoy: the generated tree's second TELL. Every real call passes
`size=len(payload)`; take each line that does (one line up the call when the
argument sits on its own line inside a multi-line call), plus the one call in
src/audit/log.py. No import is resolved, no alias is looked for."""
import json
import re
from pathlib import Path

out = []
for path in sorted(Path("src").rglob("*.py")):
    lines = path.read_text(encoding="utf-8").splitlines()
    for n, line in enumerate(lines, start=1):
        if "size=len(payload)" not in line:
            continue
        if line.strip().startswith("size=len(payload)"):
            # the call opened on an earlier line: walk up to the line with "("
            k = n - 1
            while k > 0 and not lines[k - 1].rstrip().endswith("("):
                k -= 1
            out.append((path.as_posix(), k))
        else:
            out.append((path.as_posix(), n))
for n, line in enumerate(Path("src/audit/log.py").read_text(encoding="utf-8").splitlines(), start=1):
    if "emit_legacy(" in line and not line.lstrip().startswith("def "):
        out.append(("src/audit/log.py", n))
out = sorted(set(out))
calls = [f"{p}:{n}" for p, n in out]
with open("answer.json", "w", encoding="utf-8") as fh:
    json.dump({"calls": calls, "count": len(calls)}, fh, indent=2)
