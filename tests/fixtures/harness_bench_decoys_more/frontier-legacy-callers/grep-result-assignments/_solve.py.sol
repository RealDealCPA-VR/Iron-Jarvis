"""Decoy 4: the generated tree's TELL as first shipped. Every real call site in an emitting
module is written as `    result = <callee>(...)`; nothing else in src/ assigns
to `result`. `grep -rn "    result = " src/` + the one call inside audit/log.py
(found by `grep -n "emit_legacy(" src/audit/log.py`) -- no import is resolved,
no alias is looked for."""
import json
from pathlib import Path

out = []
for path in sorted(Path("src").rglob("*.py")):
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.startswith("    result = "):
            out.append((path.as_posix(), n))
for n, line in enumerate(Path("src/audit/log.py").read_text(encoding="utf-8").splitlines(), start=1):
    if "emit_legacy(" in line and not line.lstrip().startswith("def "):
        out.append(("src/audit/log.py", n))
out.sort()
calls = [f"{p}:{n}" for p, n in out]
with open("answer.json", "w", encoding="utf-8") as fh:
    json.dump({"calls": calls, "count": len(calls)}, fh, indent=2)
