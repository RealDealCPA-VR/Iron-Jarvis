"""The generator of the team-audit-events task (v1.400.0): its sixty area
modules, shop/audit/events.py, and the hidden check's answer key.

    uv run python scripts/bench_task_generators/team_audit_events.py [--task DIR]

``--task`` is the task folder to write (default: the shipped one,
src/iron_jarvis/harness_bench/tasks/team-audit-events). It writes
``repo/shop/<area>/*.py`` (eight areas, seven modules each, plus each area's
``__init__.py``) and ``repo/shop/audit/events.py`` from a fixed seed, records
every audit write AS IT WRITES IT (the ground truth: file, function, event),
and puts that list (``EXPECTED``) and the digest of the finished project
(``TREE``) into ``check/test_hidden_audit_events.py``. Everything else in the
task — the audit package's own code (``log.py``, ``helpers.py``,
``__init__.py``), ``shop/metrics.py``, ``shop/__init__.py``, ``README.md``,
``docs/audit.md``, ``task.md``, and the check's own logic — is written by hand
and read from ``--task``: an empty folder gets them copied from the shipped
task first. Python 3.12, standard library only; the same seed gives the same
bytes on every machine (``tests/test_harness_bench_team_generators_v1400.py``
regenerates into a scratch folder and compares).
"""
import argparse
import hashlib
import json
import os
import random
import re
import shutil
import sys
from pathlib import Path

SHIPPED = Path(__file__).resolve().parents[2] / "src" / "iron_jarvis" / "harness_bench" / "tasks" / "team-audit-events"
TASK = SHIPPED
REPO = TASK / "repo"
rng = random.Random(1400)

AREAS = {
    "orders": (["order", "cart", "checkout", "invoice", "return"],
               ["placed", "cancelled", "updated", "split", "merged", "confirmed", "held", "voided"]),
    "payments": (["payment", "card", "payout", "refund", "dispute"],
                 ["captured", "declined", "refunded", "added", "removed", "sent", "retried", "opened"]),
    "inventory": (["stock", "item", "bin", "transfer", "count"],
                  ["adjusted", "counted", "reserved", "released", "moved", "written_off", "approved"]),
    "accounts": (["user", "password", "role", "session", "invite"],
                 ["created", "locked", "unlocked", "reset", "changed", "granted", "revoked", "opened"]),
    "shipping": (["label", "shipment", "carrier", "pickup", "parcel"],
                 ["printed", "voided", "dispatched", "delivered", "returned", "switched", "booked"]),
    "support": (["ticket", "note", "callback", "survey", "macro"],
                ["opened", "closed", "escalated", "reassigned", "added", "booked", "sent"]),
    "catalog": (["product", "category", "price", "image", "listing"],
                ["created", "archived", "repriced", "renamed", "published", "hidden", "imported"]),
    "reports": (["report", "export", "schedule", "dashboard", "digest"],
                ["exported", "scheduled", "deleted", "shared", "generated", "sent"]),
}
MODULES = {
    "orders": ["service", "carts", "checkout", "returns", "splits", "holds", "notes"],
    "payments": ["capture", "cards", "refunds", "payouts", "retries", "ledger", "disputes"],
    "inventory": ["stock", "counts", "reservations", "bins", "writeoffs", "transfers", "alerts"],
    "accounts": ["users", "passwords", "roles", "sessions", "lockout", "profiles", "invites"],
    "shipping": ["labels", "shipments", "carriers", "tracking", "returns", "rates", "pickups"],
    "support": ["tickets", "notes", "escalation", "callbacks", "macros", "sla", "surveys"],
    "catalog": ["products", "categories", "pricing", "publishing", "search", "imports", "media"],
    "reports": ["exports", "schedules", "sharing", "builders", "retention", "dashboards", "digest"],
}
FILLERS = [
    ["if not {p}:", "    raise ValueError(\"{p} is required\")"],
    ["if isinstance({p}, dict):", "    {p} = dict({p}, seen=True)"],
    ["if isinstance({p}, list):", "    {p} = [x for x in {p} if x]"],
    ["if isinstance({p}, str):", "    {p} = {p}.strip()"],
    ["if len(str({p})) > 200:", "    raise ValueError(\"{p} is too long\")"],
    ["if {p} is None:", "    return None"],
]
WRITE_PATTERNS = ["direct", "kwarg", "alias", "pkg", "modalias", "dotted", "events_mod", "events_name",
                  "local_const", "helper_change", "helper_failure", "decorator", "wrapper"]
#: Patterns that call ``record`` by its bare name (a local ``record`` would hide them).
BARE_RECORD = {"direct", "kwarg", "events_mod", "events_name", "local_const"}
DISTRACTORS = ["metrics", "metrics_alias", "logger", "comment", "history", "shadow", "table", "reference", "docstring"]

IMPORTS = {
    "direct": "from shop.audit import record",
    "kwarg": "from shop.audit import record",
    "alias": "from shop.audit.log import record as audit_write",
    "pkg": "from shop import audit",
    "modalias": "import shop.audit.log as alog",
    "dotted": "import shop.audit",
    "events_mod": "from shop.audit import events",
    "helper_change": "from shop.audit.helpers import audit_change",
    "helper_failure": "from shop.audit.helpers import audit_failure",
    "decorator": "from shop.audit.helpers import audited",
    "metrics": "from shop import metrics",
    "metrics_alias": "from shop.metrics import record as record_metric",
    "logger": "import logging",
}

events_constants = {}  # event -> CONSTANT
truth = []  # (file, function, event)
used_events = set()
counts = {p: 0 for p in WRITE_PATTERNS + DISTRACTORS}
# Rarer distractors: odd code is believable a few times, not on every page.
counts.update({"shadow": 9, "reference": 6})


def const_name(ev):
    return ev.upper().replace(".", "_")


def new_event(area):
    entities, actions = AREAS[area]
    for _ in range(200):
        ev = f"{rng.choice(entities)}.{rng.choice(actions)}"
        if ev not in used_events:
            used_events.add(ev)
            return ev
    raise RuntimeError(area)


def fields(p):
    return f"{p}={p}"


def write_lines(pattern, ev, p, module_state):
    """(statement lines, decorator line or None) for one write of ``ev``."""
    imports = module_state["imports"]
    if pattern in IMPORTS:
        imports.add(IMPORTS[pattern])
    if pattern == "direct":
        return [f'record("{ev}", {fields(p)})'], None
    if pattern == "kwarg":
        return [f'record(event="{ev}", {fields(p)})'], None
    if pattern == "alias":
        return [f'audit_write("{ev}", {fields(p)})'], None
    if pattern == "pkg":
        return [f'audit.record("{ev}", {fields(p)})'], None
    if pattern == "modalias":
        return [f'alog.record("{ev}", {fields(p)})'], None
    if pattern == "dotted":
        return [f'shop.audit.record("{ev}", {fields(p)})'], None
    if pattern == "events_mod":
        imports.add("from shop.audit import record")
        events_constants[ev] = const_name(ev)
        return [f"record(events.{const_name(ev)}, {fields(p)})"], None
    if pattern == "events_name":
        events_constants[ev] = const_name(ev)
        imports.add("from shop.audit import record")
        module_state["event_names"].add(const_name(ev))
        return [f"record({const_name(ev)}, {fields(p)})"], None
    if pattern == "local_const":
        imports.add("from shop.audit import record")
        name = "_" + const_name(ev) + "_EVENT"
        module_state["consts"].append(f'{name} = "{ev}"')
        return [f"record({name}, {fields(p)})"], None
    if pattern == "helper_change":
        entity, action = ev.split(".", 1)
        return [f'audit_change("{entity}", "{action}", {fields(p)})'], None
    if pattern == "helper_failure":
        return [f'audit_failure("{ev.split(".", 1)[1]}", {fields(p)})'], None
    if pattern == "decorator":
        return [], f'@audited("{ev}")'
    if pattern == "wrapper":
        imports.add("from shop.audit import record")
        module_state["wrapper"] = True
        return [f'_audit("{ev}", {fields(p)})'], None
    raise KeyError(pattern)


def distractor_lines(kind, area, p, module_state):
    imports = module_state["imports"]
    if kind in IMPORTS:
        imports.add(IMPORTS[kind])
    ev = rng.choice(sorted(used_events)) if used_events and rng.random() < 0.6 else new_unwritten(area)
    if kind == "metrics":
        return [f'metrics.record("{ev}", 1)']
    if kind == "metrics_alias":
        return [f'record_metric("{ev}.count", 1)']
    if kind == "logger":
        module_state["logger"] = True
        return [f'log.info("{ev} for %s", {p})']
    if kind == "comment":
        return [f'# record("{ev}", {fields(p)})  -- moved to the nightly job']
    if kind == "shadow":
        return [f"record = {p}.append if isinstance({p}, list) else print", f'record("{ev}")']
    if kind == "table":
        n = len(module_state["consts"])
        module_state["consts"].append(f'LABELS_{n} = {{"{ev}": "{ev.replace(".", " ").title()}"}}')
        return [f"{p} = LABELS_{n}.get(str({p}), {p})"]
    if kind == "reference":
        imports.add("from shop.audit import record")
        module_state["consts"].append('HOOKS = {"after_save": record}')
        return ["if not HOOKS:", f"    return {p}"]
    raise KeyError(kind)


unwritten = set()


def new_unwritten(area):
    entities, _ = AREAS[area]
    for _ in range(200):
        ev = f"{rng.choice(entities)}.{rng.choice(['flagged', 'purged', 'migrated', 'snapshotted', 'audited'])}"
        if ev not in used_events:
            unwritten.add(ev)
            return ev
    return f"{area}.legacy"


def body_for(area, p, writes, distractors, module_state, indent):
    lines = []
    for chunk in rng.sample(FILLERS, 2):
        lines += [c.format(p=p, v=f"{p}_info") for c in chunk]
    for kind in distractors:
        lines += distractor_lines(kind, area, p, module_state)
    decorators = []
    for pattern, ev in writes:
        stmts, deco = write_lines(pattern, ev, p, module_state)
        if deco:
            decorators.append(deco)
        if stmts and rng.random() < 0.3:
            lines += [f"if {p}:"] + ["    " + s for s in stmts]
        else:
            lines += stmts
    lines.append(f"return {p}")
    pad = " " * indent
    return decorators, [pad + line for line in lines]


def module_source(area, mod, rel):
    state = {"imports": set(), "consts": [], "event_names": set(), "wrapper": False, "logger": False}
    out_funcs = []
    as_class = rng.random() < 0.45
    n_funcs = rng.randint(2, 3)
    cls = mod.rstrip("s").title().replace("_", "") + "Service"
    verbs = ["handle", "apply", "start", "finish", "check", "sync", "process", "record_", "update", "close"]
    rng.shuffle(verbs)
    history_method = as_class and rng.random() < 0.3
    for i in range(n_funcs):
        name = f"{verbs[i].rstrip('_')}_{mod.rstrip('s')}"
        p = rng.choice(["item", "payload", "ref", "target", "entry"])
        n_dis = rng.choice([0, 0, 0, 1, 1, 2])
        dis = []
        for _ in range(n_dis):
            choices = [d for d in DISTRACTORS if d not in ("docstring", "history") and d not in dis]
            kind = min(choices, key=lambda x: (counts[x], rng.random()))
            counts[kind] += 1
            dis.append(kind)
        n_writes = rng.choice([0, 0, 1, 1, 1, 2])
        writes = []
        for _ in range(n_writes):
            allowed = [w for w in WRITE_PATTERNS if not ("shadow" in dis and w in BARE_RECORD)]
            if any(w[0] == "decorator" for w in writes):
                allowed.remove("decorator")
            pattern = min(allowed, key=lambda x: (counts[x], rng.random()))
            counts[pattern] += 1
            ev = new_event(area)
            if pattern == "helper_failure":
                ev = f"failure.{area}_{ev.split('.', 1)[1]}"
                used_events.add(ev)
            writes.append((pattern, ev))
        doc = f'"""{name.replace("_", " ").capitalize()}."""'
        if rng.random() < 0.15 or (counts["docstring"] < 3 and rng.random() < 0.4):
            counts["docstring"] += 1
            old = new_unwritten(area)
            doc = f'"""{name.replace("_", " ").capitalize()}. It used to call record("{old}") itself."""'
        indent = 8 if as_class else 4
        decorators, body = body_for(area, p, writes, dis, state, indent)
        if history_method and i == 0:
            hist_ev = rng.choice(sorted(used_events))
            body.insert(0, " " * indent + f'self.record("{hist_ev}")')
            counts["history"] += 1
        func_rel_name = f"{cls}.{name}" if as_class else name
        for pattern, ev in writes:
            truth.append((rel, func_rel_name, ev))
        sig = f"(self, {p})" if as_class else f"({p})"
        lead = "    " if as_class else ""
        func = [lead + d for d in decorators] + [f"{lead}def {name}{sig}:", " " * indent + doc] + body
        out_funcs.append(func)
    lines = [f'"""{area.title()}: {mod.replace("_", " ")}."""', ""]
    imports = sorted(state["imports"])
    if state["event_names"]:
        imports.append("from shop.audit.events import " + ", ".join(sorted(state["event_names"])))
    imports = sorted(set(imports), key=lambda s: (not s.startswith("import"), s))
    lines += imports + [""]
    if state["logger"]:
        lines += ["log = logging.getLogger(__name__)", ""]
    if state["consts"]:
        lines += sorted(set(state["consts"])) + [""]
    if state["wrapper"]:
        lines += ["", "def _audit(event, **fields):",
                  f'    """Writes ``event`` to the audit log, marked as coming from {area}."""',
                  f'    record(event, area="{area}", **fields)', ""]
    lines.append("")
    if as_class:
        lines += [f"class {cls}:", f'    """The {mod.replace("_", " ")} of {area}."""', "",
                  "    def __init__(self, store=None):", "        self.store = store if store is not None else {}",
                  "        self.history = []", ""]
        if history_method:
            lines += ["    def record(self, what):", '        """Keeps a line of this service\'s own history."""',
                      "        self.history.append(what)", ""]
        for func in out_funcs:
            lines += func + [""]
    else:
        for func in out_funcs:
            lines += func + ["", ""]
    text = "\n".join(lines).rstrip() + "\n"
    return text


def main():
    shop = REPO / "shop"
    (shop / "audit").mkdir(parents=True, exist_ok=True)
    for area, mods in AREAS.items():
        pkg = shop / area
        if pkg.exists():
            shutil.rmtree(pkg)
        pkg.mkdir()
        (pkg / "__init__.py").write_text(f'"""{area.title()}."""\n', encoding="utf-8", newline="\n")
        for mod in MODULES[area]:
            rel = f"shop/{area}/{mod}.py"
            (REPO / rel).write_text(module_source(area, mod, rel), encoding="utf-8", newline="\n")
    # the audit package itself
    truth.append(("shop/audit/log.py", "rotate", "audit.rotated"))
    consts = dict(sorted((v, k) for k, v in events_constants.items()))
    spare = ["order.archived", "payment.voided", "user.deleted"]
    const_lines = [f'{c} = "{e}"' for c, e in sorted(consts.items())]
    const_lines += [f'{e.upper().replace(".", "_")} = "{e}"  # not written any more' for e in spare]
    (shop / "audit" / "events.py").write_text(
        '"""The names of the audit events that have a constant."""\n\n' + "\n".join(const_lines) + "\n",
        encoding="utf-8", newline="\n")
    return sorted(truth)


#: The files of the task written by hand (copied from the shipped task into
#: an empty --task folder before the generated ones are written).
HAND_WRITTEN = (
    "task.md", "task.toml", "check/test_hidden_audit_events.py", "repo/README.md", "repo/docs/audit.md",
    "repo/shop/__init__.py", "repo/shop/metrics.py", "repo/shop/audit/__init__.py", "repo/shop/audit/log.py",
    "repo/shop/audit/helpers.py",
)
SCOPE = ("README.md", "docs", "shop")


def digest(root):
    """The hidden check's ``_digest``: what the agent must leave unchanged."""
    h = hashlib.sha256()
    for top in SCOPE:
        base = os.path.join(root, top)
        if os.path.isfile(base):
            paths = [base]
        else:
            paths = []
            for folder, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                paths += [os.path.join(folder, f) for f in files]
            paths.sort(key=lambda p: os.path.relpath(p, root).replace(os.sep, "/"))
        for p in paths:
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            with open(p, "rb") as fh:
                data = fh.read().replace(b"\r\n", b"\n")
            h.update(rel.encode() + b"\0")
            h.update(hashlib.sha256(data).digest())
    return h.hexdigest()


def write_answer_key(task, rows):
    """EXPECTED and TREE in the hidden check, replaced in place (the rest of
    the check is hand-written)."""
    check = task / "check" / "test_hidden_audit_events.py"
    text = check.read_text(encoding="utf-8")
    listed = "\n".join(f"    ({json.dumps(f)}, {json.dumps(fn)}, {json.dumps(ev)})," for f, fn, ev in rows)
    text, n = re.subn(r"(?ms)^EXPECTED = \[\n.*?^\]\n", lambda _m: f"EXPECTED = [\n{listed}\n]\n", text, count=1)
    assert n == 1, "EXPECTED not found in the check"
    text, n = re.subn(r'(?m)^TREE = "[0-9a-f]*"$', lambda _m: f'TREE = "{digest(task / "repo")}"', text, count=1)
    assert n == 1, "TREE not found in the check"
    check.write_text(text, encoding="utf-8", newline="\n")


def run(task):
    global TASK, REPO
    TASK, REPO = Path(task), Path(task) / "repo"
    for rel in HAND_WRITTEN:
        target = TASK / rel
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SHIPPED / rel, target)
    rows = main()
    write_answer_key(TASK, rows)
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--task", default=str(SHIPPED), help="the task folder to write (default: the shipped one)")
    rows = run(parser.parse_args().task)
    print(f"{len(rows)} audit writes; answer key and tree digest written", file=sys.stderr)
