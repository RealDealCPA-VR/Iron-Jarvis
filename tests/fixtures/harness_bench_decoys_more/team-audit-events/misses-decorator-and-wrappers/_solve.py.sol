"""Decoy (review): a careful resolver that forgets two rules of the task: the
@audited decorator, and that a call through a module's own wrapper is a write
by its caller. Otherwise it finds every audit write in shop/ by RESOLVING the code
(imports, aliases, module constants, helpers, the decorator, module wrappers,
local names) with the ast module — never by matching text — and writes
answer.json. It knows nothing the generator recorded."""

import ast
import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
RECORD = "shop.audit.log.record"
CHANGE = "shop.audit.helpers.audit_change"
FAILURE = "shop.audit.helpers.audit_failure"
AUDITED = "shop.audit.helpers.audited"
HELPERS_FILE = "shop/audit/helpers.py"


def module_name(rel):
    parts = rel[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def load_modules():
    modules = {}
    for folder, dirs, files in os.walk(os.path.join(ROOT, "shop")):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(folder, f)
                rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
                with open(path, encoding="utf-8") as fh:
                    modules[module_name(rel)] = (rel, ast.parse(fh.read(), path))
    return modules


MODULES = load_modules()


def absolute(mod, level, name):
    if not level:
        return name
    base = mod.split(".")
    if not MODULES[mod][0].endswith("__init__.py"):
        base = base[:-1]
    base = base[: len(base) - (level - 1)]
    return ".".join(base + ([name] if name else []))


def bindings(mod):
    """Module-level names: name -> ("module", dotted) | ("object", dotted) | ("str", value)."""
    out = {}
    for node in MODULES[mod][1].body:
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    out[a.asname] = ("module", a.name)
                else:
                    top = a.name.split(".")[0]
                    out[top] = ("module", top)
        elif isinstance(node, ast.ImportFrom):
            src = absolute(mod, node.level, node.module or "")
            for a in node.names:
                full = f"{src}.{a.name}"
                out[a.asname or a.name] = ("module", full) if full in MODULES else ("object", full)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            out[node.name] = ("object", f"{mod}.{node.name}")
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                out[node.targets[0].id] = ("str", node.value.value)
    return out


BINDINGS = {mod: bindings(mod) for mod in MODULES}


def follow(dotted, seen=()):
    """The final object a dotted name means, through re-exports and modules."""
    if dotted in MODULES:
        return ("module", dotted)
    if dotted in seen:
        return ("object", dotted)
    mod, _, attr = dotted.rpartition(".")
    if mod in MODULES and attr in BINDINGS[mod]:
        kind, target = BINDINGS[mod][attr]
        if kind == "str":
            return ("str", target)
        if kind == "module":
            return ("module", target)
        if target == dotted:  # defined right there
            return ("object", dotted)
        return follow(target, (*seen, dotted))
    return ("object", dotted)


def resolve(expr, mod, local_names):
    """What an expression names: ("module"|"object", dotted) | ("str", value) | None."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return ("str", expr.value)
    if isinstance(expr, ast.Name):
        if expr.id in local_names or expr.id not in BINDINGS[mod]:
            return None
        kind, target = BINDINGS[mod][expr.id]
        if kind == "str":
            return ("str", target)
        if kind == "module":
            return ("module", target)
        return follow(target)
    if isinstance(expr, ast.Attribute):
        base = resolve(expr.value, mod, local_names)
        if base and base[0] == "module":
            return follow(f"{base[1]}.{expr.attr}")
    return None


def wrappers(mod):
    """Module functions that pass their first parameter on to record as the event."""
    out = set()
    for node in MODULES[mod][1].body:
        if isinstance(node, ast.FunctionDef) and node.args.args:
            first = node.args.args[0].arg
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and resolve(call.func, mod, set()) == ("object", RECORD):
                    arg = event_arg(call)
                    if isinstance(arg, ast.Name) and arg.id == first:
                        out.add(node.name)
    return out


def event_arg(call):
    if call.args:
        return call.args[0]
    for kw in call.keywords:
        if kw.arg == "event":
            return kw.value
    return None


def local_names(func):
    names = {a.arg for a in func.args.args + func.args.kwonlyargs}
    for node in ast.walk(func):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        names.add(n.id)
    return names


def functions(tree):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            yield node.name, node
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    yield f"{node.name}.{item.name}", item


def main():
    writes = set()
    for mod, (rel, tree) in MODULES.items():
        if rel == HELPERS_FILE:
            continue
        own_wrappers = wrappers(mod)
        for qualname, func in functions(tree):
            if qualname in own_wrappers:
                continue
            names = local_names(func)
            for call in ast.walk(func):
                if not isinstance(call, ast.Call):
                    continue
                target = resolve(call.func, mod, names)
                if target == ("object", RECORD):
                    value = resolve(event_arg(call), mod, names) if event_arg(call) is not None else None
                    if value and value[0] == "str":
                        writes.add((rel, qualname, value[1]))
                elif target in (("object", CHANGE), ("object", FAILURE)):
                    parts = [resolve(a, mod, names) for a in call.args[: 2 if target[1] == CHANGE else 1]]
                    if all(p and p[0] == "str" for p in parts):
                        event = ".".join(p[1] for p in parts) if target[1] == CHANGE else f"failure.{parts[0][1]}"
                        writes.add((rel, qualname, event))
    answer = {"writes": [{"file": f, "function": fn, "event": ev} for f, fn, ev in sorted(writes)]}
    with open(os.path.join(ROOT, "answer.json"), "w", encoding="utf-8") as fh:
        json.dump(answer, fh, indent=1)


main()
