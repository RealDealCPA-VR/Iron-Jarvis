"""Decoy 2: `grep -rn "emit_legacy(" src/` plus light filtering (what a quick
agent does): drop the definitions, method calls, comment lines, mentions inside
a string (a quote before the name), emit_legacy_v2 (grep already skips it) and
src/metrics/ (the README says that one is not ours)."""
import json
from pathlib import Path

out = []
for path in sorted(Path("src").rglob("*.py")):
    rel = path.as_posix()
    if rel.startswith("src/metrics/"):
        continue
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        i = line.find("emit_legacy(")
        if i < 0:
            continue
        s = line.strip()
        if s.startswith("#") or s.startswith("def ") or "self.emit_legacy" in line:
            continue
        if '"' in line[:i] or "'" in line[:i]:
            continue
        out.append((rel, n))
out.sort()
calls = [f"{p}:{n}" for p, n in out]
with open("answer.json", "w", encoding="utf-8") as fh:
    json.dump({"calls": calls, "count": len(calls)}, fh, indent=2)
