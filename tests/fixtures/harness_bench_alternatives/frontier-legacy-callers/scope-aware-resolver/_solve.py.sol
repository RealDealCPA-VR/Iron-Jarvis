"""Alternative correct: a scope-aware AST visitor. Every name a call goes
through is resolved to a qualified target (imports at any level bind names,
function and class definitions shadow them, attribute chains are followed), and
a call whose target is audit.log.emit_legacy or the re-export
audit.emit_legacy is recorded at the call's first line."""
import ast
import json
from pathlib import Path

TARGETS = {"audit.log.emit_legacy", "audit.emit_legacy"}


class Scan(ast.NodeVisitor):
    def __init__(self, modname, pkg):
        self.scopes = [{}]
        self.modname, self.pkg = modname, pkg
        self.hits = []
        if modname == "audit.log":
            self.scopes[0]["emit_legacy"] = "audit.log.emit_legacy"

    def bind(self, name, target):
        self.scopes[-1][name] = target

    def lookup(self, name):
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None

    def visit_Import(self, node):
        for a in node.names:
            if a.asname:
                self.bind(a.asname, a.name)
            else:
                top = a.name.split(".")[0]
                self.bind(top, top)

    def visit_ImportFrom(self, node):
        base = node.module or ""
        if node.level:
            parts = self.pkg.split(".")
            parts = parts[: len(parts) - node.level + 1]
            base = ".".join(p for p in [*parts, base] if p)
        for a in node.names:
            self.bind(a.asname or a.name, f"{base}.{a.name}")

    def _def(self, node):
        if not (self.modname == "audit.log" and node.name == "emit_legacy"):
            self.bind(node.name, f"{self.modname}.<local>.{node.name}")
        self.scopes.append({})
        self.generic_visit(node)
        self.scopes.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _def

    def qual(self, expr):
        if isinstance(expr, ast.Name):
            return self.lookup(expr.id)
        if isinstance(expr, ast.Attribute):
            left = self.qual(expr.value)
            return f"{left}.{expr.attr}" if left else None
        return None

    def visit_Call(self, node):
        if self.qual(node.func) in TARGETS:
            self.hits.append(node.lineno)
        self.generic_visit(node)


found = []
for path in sorted(Path("src").rglob("*.py")):
    parts = list(path.relative_to("src").with_suffix("").parts)
    is_init = parts[-1] == "__init__"
    if is_init:
        parts = parts[:-1]
    modname = ".".join(parts)
    pkg = modname if is_init else ".".join(parts[:-1])
    s = Scan(modname, pkg)
    s.visit(ast.parse(path.read_text(encoding="utf-8")))
    found += [(path.as_posix(), n) for n in s.hits]
found.sort()
calls = [f"{p}:{n}" for p, n in found]
Path("answer.json").write_text(json.dumps({"count": len(calls), "calls": calls}) + "\n", encoding="utf-8")
