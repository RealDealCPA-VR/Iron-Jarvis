"""Decoy: a careful-LOOKING scan that matches calls by NAME only.

It skips vendor/, comments and strings (it parses the code), but it counts any
call spelled ``emit_legacy(...)`` or ``<something>.emit_legacy(...)`` — the
metrics function of the same name, local functions and methods included — and
it never sees a call made through an import alias (``_audit(...)``).
"""
import ast
import json
from pathlib import Path

out = []
for path in sorted(Path("src").rglob("*.py")):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            if name == "emit_legacy":
                out.append((path.as_posix(), node.lineno))
out.sort()
calls = [f"{p}:{n}" for p, n in out]
with open("answer.json", "w", encoding="utf-8", newline="\n") as fh:
    json.dump({"calls": calls, "count": len(calls)}, fh, indent=2)
    fh.write("\n")
