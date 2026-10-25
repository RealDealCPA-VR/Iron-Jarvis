"""Decoy 5: grep for the WORD (emit_legacy or an alias of it) in files that
import audit, minus import lines, defs, comments and strings. Handles aliases.
Careless part: it lists every USE of the name that is not an import, so a
reference such as HANDLERS = {'legacy': emit_legacy} counts as a call."""
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
    aliases = re.findall(r"^from audit(?:\.log)? import emit_legacy as (\w+)", text, re.M)
    names = ["emit_legacy", *aliases]
    pat = re.compile(r"\b(" + "|".join(map(re.escape, names)) + r")\b(?!_)")
    for n, line in enumerate(text.splitlines(), start=1):
        s = line.strip()
        if s.startswith(("#", "def ", "from ", "import ", '"', "'")) or "self." in line:
            continue
        m = pat.search(line)
        if not m:
            continue
        if '"' in line[:m.start()] or "'" in line[:m.start()].replace("'legacy'", ""):
            continue
        out.append((rel, n))
out.sort()
calls = [f"{p}:{n}" for p, n in out]
with open("answer.json", "w", encoding="utf-8") as fh:
    json.dump({"calls": calls, "count": len(calls)}, fh, indent=2)
