"""Decoy: the list a text search gives. Every `record("a.b")` or
`audit_write("a.b")` call written with a literal name, credited to the
function it sits in. It misses the writes through constants, the helpers, the
decorator and the module wrappers, and it lists the dashboard counters, the
comments, the docstrings, a method called record and a local called record."""

import json
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))
CALL = re.compile(r"""(?:record|audit_write)\(\s*(?:event=)?["']([a-z_]+\.[a-z_]+)["']""")
DEF = re.compile(r"^(\s*)def (\w+)\(")
CLASS = re.compile(r"^class (\w+)")

writes = set()
for folder, dirs, files in os.walk(os.path.join(ROOT, "shop")):
    dirs[:] = [d for d in dirs if d != "__pycache__"]
    for name in sorted(files):
        if not name.endswith(".py"):
            continue
        path = os.path.join(folder, name)
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        cls = None
        func = None
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                m = CLASS.match(line)
                if m:
                    cls = m.group(1)
                    continue
                m = DEF.match(line)
                if m:
                    if not m.group(1):
                        cls = None
                    func = f"{cls}.{m.group(2)}" if cls and m.group(1) else m.group(2)
                    continue
                for event in CALL.findall(line):
                    if func:
                        writes.add((rel, func, event))

with open(os.path.join(ROOT, "answer.json"), "w", encoding="utf-8") as fh:
    json.dump({"writes": [{"file": f, "function": fn, "event": ev} for f, fn, ev in sorted(writes)]}, fh, indent=1)
