"""Reviewer's alternative (v1.400.0 review): the audit writes of shop/, found
by a resolver written without looking at the reference solution.

Each module's imports give names a MEANING (the audit record, the counters'
record, a helper, a module); constants are folded from shop/audit/events.py
and the module's own top level; a name a function assigns is that function's
own (it hides the import everywhere in the function, as Python scopes it); a
module function whose body passes one of its OWN parameters on as the event
is a wrapper, and its callers' literal events are theirs. Run in the task's
folder (cwd); writes answer.json only."""

import ast
import json
import os

ROOT = "shop"
AUDIT_RECORD = "shop.audit.log.record"
CHANGE = "shop.audit.helpers.audit_change"
FAILURE = "shop.audit.helpers.audit_failure"
AUDITED = "shop.audit.helpers.audited"
# What a dotted import path means as a module (attribute access continues it).
REEXPORTS = {"shop.audit.record": AUDIT_RECORD, "shop.audit.helpers.record": AUDIT_RECORD}


def module_name(path):
    rel = os.path.relpath(path, ".").replace(os.sep, "/")[:-3]
    parts = rel.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def canonical(dotted):
    return REEXPORTS.get(dotted, dotted)


def load_constants(path):
    out = {}
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = node.value.value
    return out


EVENTS = load_constants(os.path.join(ROOT, "audit", "events.py"))


class Module:
    def __init__(self, path):
        self.path = path
        self.name = module_name(path)
        with open(path, encoding="utf-8") as fh:
            self.tree = ast.parse(fh.read())
        self.names = {}  # local name -> dotted meaning
        self.constants = load_constants(path)
        package = self.name if path.endswith("__init__.py") else self.name.rpartition(".")[0]
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # A module's own function: shop/audit/log.py's record is THE record.
                self.names[node.name] = canonical(f"{self.name}.{node.name}")
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in self.names:
                        del self.names[target.id]  # rebound at the top level: not the import any more
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        self.names[alias.asname] = alias.name
                    else:
                        top = alias.name.split(".")[0]
                        self.names[top] = top
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parent = package.split(".")
                    if node.level > 1:
                        parent = parent[: len(parent) - node.level + 1]
                    base = ".".join(parent + ([base] if base else []))
                for alias in node.names:
                    self.names[alias.asname or alias.name] = canonical(f"{base}.{alias.name}")

    def meaning(self, expr, local):
        """The dotted thing an expression names, or None."""
        if isinstance(expr, ast.Name):
            if expr.id in local:
                return None
            return self.names.get(expr.id)
        if isinstance(expr, ast.Attribute):
            base = self.meaning(expr.value, local)
            return canonical(f"{base}.{expr.attr}") if base else None
        return None

    def text(self, expr, local):
        """The event text an argument is: a literal, an events constant or a module constant."""
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            return expr.value
        if isinstance(expr, ast.Name) and expr.id not in local:
            if expr.id in self.constants:
                return self.constants[expr.id]
            dotted = self.names.get(expr.id, "")
            if dotted.startswith("shop.audit.events."):
                return EVENTS.get(dotted.rpartition(".")[2])
        if isinstance(expr, ast.Attribute):
            dotted = self.meaning(expr, local) or ""
            if dotted.startswith("shop.audit.events."):
                return EVENTS.get(dotted.rpartition(".")[2])
        return None


def locals_of(fn):
    names = {a.arg for a in (*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs)}
    if fn.args.vararg:
        names.add(fn.args.vararg.arg)
    if fn.args.kwarg:
        names.add(fn.args.kwarg.arg)
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node is not fn:
            names.add(node.name)
    return names


def event_argument(call, param="event"):
    if call.args:
        return call.args[0]
    for kw in call.keywords:
        if kw.arg == param:
            return kw.value
    return None


def functions(module):
    """(qualname, node) of every module function and method."""
    for node in module.tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield f"{node.name}.{item.name}", item


def wrappers(module):
    """Module functions that pass one of their own parameters on as the event."""
    out = {}
    for qualname, fn in functions(module):
        if "." in qualname:
            continue
        params = [a.arg for a in fn.args.args]
        local = locals_of(fn) - set(params)
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and module.meaning(node.func, local) == AUDIT_RECORD:
                arg = event_argument(node)
                if isinstance(arg, ast.Name) and arg.id in params:
                    out[qualname] = params.index(arg.id), arg.id
    return out


def writes(module):
    found = set()
    wrapped = wrappers(module)
    for qualname, fn in functions(module):
        local = locals_of(fn)
        for deco in fn.decorator_list:
            if isinstance(deco, ast.Call) and module.meaning(deco.func, set()) == AUDITED and deco.args:
                event = module.text(deco.args[0], set())
                if event:
                    found.add((qualname, event))
        if qualname in wrapped:
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            target = module.meaning(node.func, local)
            event = None
            if target == AUDIT_RECORD:
                event = module.text(event_argument(node), local) if event_argument(node) is not None else None
            elif target in (CHANGE, FAILURE):
                args = list(node.args)
                kw = {k.arg: k.value for k in node.keywords}
                if target == CHANGE:
                    entity = module.text(args[0] if args else kw.get("entity"), local)
                    action = module.text(args[1] if len(args) > 1 else kw.get("action"), local)
                    event = f"{entity}.{action}" if entity and action else None
                else:
                    area = module.text(args[0] if args else kw.get("area"), local)
                    event = f"failure.{area}" if area else None
            elif isinstance(node.func, ast.Name) and node.func.id not in local and node.func.id in wrapped:
                position, param = wrapped[node.func.id]
                arg = node.args[position] if len(node.args) > position else None
                if arg is None:
                    arg = next((k.value for k in node.keywords if k.arg == param), None)
                event = module.text(arg, local) if arg is not None else None
            if event:
                found.add((qualname, event))
    return found


def main():
    answer = []
    for folder, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            module = Module(os.path.join(folder, name))
            rel = os.path.relpath(module.path, ".").replace(os.sep, "/")
            for qualname, event in sorted(writes(module)):
                answer.append({"file": rel, "function": qualname, "event": event})
    # What Windows PowerShell 5.1 writes for `ConvertTo-Json | Out-File answer.json`
    # (or `> answer.json`): UTF-16 LE with a byte-order mark, CRLF line ends.
    text = json.dumps({"writes": answer}, indent=4)
    with open("answer.json", "w", encoding="utf-16", newline="\r\n") as fh:
        fh.write(text + "\n")


if __name__ == "__main__":
    main()
