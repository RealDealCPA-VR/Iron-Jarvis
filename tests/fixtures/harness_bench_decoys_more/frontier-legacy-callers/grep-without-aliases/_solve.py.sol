"""Decoy 3: decoy 2, and a hit counts only in a file that imports from the
audit package (drops the metrics look-alikes and local functions). Still a grep
for the NAME, so calls made through an import alias are never seen."""
import json
import re
from pathlib import Path

out = []
for path in sorted(Path("src").rglob("*.py")):
    rel = path.as_posix()
    text = path.read_text(encoding="utf-8")
    own = rel == "src/audit/log.py"
    if not re.search(r"^(from audit|import audit)", text, re.M) and not own:
        continue
    if re.search(r"^def emit_legacy\(", text, re.M) and not own:
        continue
    for n, line in enumerate(text.splitlines(), start=1):
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
