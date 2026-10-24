"""Reference solution: resolve every call of audit.log.emit_legacy in src/ by its imports."""
import ast
import json
from pathlib import Path

FUNC = ("audit.log", "emit_legacy")


def module_name(rel):
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def callers(root):
    out = []
    src = root / "src"
    for path in sorted(src.rglob("*.py")):
        rel = path.relative_to(src)
        here = module_name(rel)
        package = here if path.name == "__init__.py" else here.rpartition(".")[0]
        tree = ast.parse(path.read_text(encoding="utf-8"))
        funcs = set()      # names bound to audit.log.emit_legacy
        modules = set()    # names bound to the module audit.log
        packages = set()   # names bound to the package audit (``import audit.log``)
        if here == "audit.log":
            funcs.add("emit_legacy")
        for node in tree.body:
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == "audit.log":
                        (modules if a.asname else packages).add(a.asname or "audit")
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parts = package.split(".")
                    parts = parts[: len(parts) - (node.level - 1)]
                    base = ".".join([*parts, base] if base else parts)
                for a in node.names:
                    if (base, a.name) in (FUNC, ("audit", "emit_legacy")):
                        funcs.add(a.asname or a.name)
                    elif (base, a.name) == ("audit", "log"):
                        modules.add(a.asname or "log")
                    else:
                        funcs.discard(a.asname or a.name)
            elif isinstance(node, (ast.FunctionDef, ast.ClassDef)) and here != "audit.log":
                funcs.discard(node.name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            hit = False
            if isinstance(f, ast.Name) and f.id in funcs:
                hit = True
            elif isinstance(f, ast.Attribute) and f.attr == "emit_legacy":
                v = f.value
                if isinstance(v, ast.Name) and v.id in modules:
                    hit = True
                elif (isinstance(v, ast.Attribute) and v.attr == "log" and isinstance(v.value, ast.Name)
                      and v.value.id in packages):
                    hit = True
            if hit:
                out.append((f"src/{rel.as_posix()}", node.lineno))
    out.sort()
    return [f"{p}:{n}" for p, n in out]


calls = callers(Path("."))
with open("answer.json", "w", encoding="utf-8", newline="\n") as fh:
    json.dump({"calls": calls, "count": len(calls)}, fh, indent=2)
    fh.write("\n")
