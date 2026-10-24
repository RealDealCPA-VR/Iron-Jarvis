# IJ-BENCH-HIDDEN-CHECK frontier-legacy-callers (setup step: hidden, never copied to the agent)
"""Generate the task's repository: about 2,000 files, the same tree for the same seed.

    python make_monorepo.py <folder> <seed>            write the tree into <folder>
    python make_monorepo.py <folder> <seed> --truth    also print the true call list (JSON) on stdout

Every file is written with LF line endings and UTF-8, from a ``random.Random(seed)``
stream only (no clock, no environment), so the tree is identical on every run and
every machine. The true answer is tracked while writing: the places in ``src/``
that call ``audit.log.emit_legacy`` (by name, through an import alias, through the
module or through the package's re-export), and none of the look-alikes (a
same-named function elsewhere, a method of that name, comments, strings,
``emit_legacy_v2``, a reference that is not a call, the vendored copy).
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

PACKAGES = [
    "accounts", "admin", "analytics", "billing", "catalog", "checkout", "cms", "coupons",
    "crm", "exports", "fulfillment", "gateway", "imports", "inventory", "invoicing", "ledger",
    "loyalty", "mailer", "media", "notifications", "orders", "payments", "pricing", "promotions",
    "quotes", "refunds", "reports", "returns", "reviews", "scheduler", "search", "sessions",
    "shipping", "subscriptions", "support", "tax", "users", "warehouse", "webhooks", "wishlist",
]
MODULES_PER_PACKAGE = 39
VERBS = ["load", "save", "build", "check", "apply", "merge", "split", "render", "fetch", "close",
         "open", "sync", "score", "match", "count", "clean", "plan", "route", "price", "queue"]
NOUNS = ["order", "item", "batch", "record", "entry", "report", "account", "line", "event", "rule",
         "token", "draft", "label", "invoice", "quota", "window", "bucket", "slot", "note", "rate"]
EVENTS = ["created", "updated", "closed", "failed", "retried", "skipped", "merged", "exported"]

#: How an emitting module reaches audit's emit_legacy (each a REAL call).
REAL_STYLES = ["name", "alias", "module_alias", "from_package_module", "dotted", "package_reexport",
               "multiline"]
#: Look-alikes that are NOT calls of audit.log.emit_legacy.
FAKE_STYLES = ["comment", "docstring", "string", "same_name_other_module", "method", "local_def",
               "v2", "reference"]


def ident(rng: random.Random) -> str:
    return f"{rng.choice(VERBS)}_{rng.choice(NOUNS)}"


class Writer:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.count = 0

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        self.count += 1


def filler_function(rng: random.Random, name: str) -> list[str]:
    a, b = rng.randint(2, 9), rng.randint(10, 99)
    return [
        f"def {name}(values, factor={a}):",
        f'    """{name.replace("_", " ").capitalize()} for a list of numbers."""',
        "    total = 0",
        "    for value in values:",
        f"        if value > {b}:",
        "            total += value * factor",
        "        else:",
        "            total += value",
        "    return total",
        "",
        "",
    ]


AUDIT_LOG = '''"""The audit trail.

``emit`` is the API every module should use. ``emit_legacy`` is the old one: it
writes the same event in the old flat format, and is being removed.
"""

import json

_SINK = []


def emit(event, **fields):
    """Record one audit event (the current format)."""
    record = {"event": event, "fields": dict(sorted(fields.items()))}
    _SINK.append(json.dumps(record, sort_keys=True))
    return record


def emit_legacy(event, **fields):
    """Record one audit event in the OLD flat format. Deprecated: use emit()."""
    flat = {"event": event, **fields}
    _SINK.append(json.dumps(flat, sort_keys=True))
    return flat


def replay(events):
    """Re-emit stored events; old ones go through the old format."""
    out = []
    for event in events:
        if event.get("legacy"):
            out.append(emit_legacy(event["event"], replayed=True))
        else:
            out.append(emit(event["event"], replayed=True))
    return out


def drain():
    items = list(_SINK)
    _SINK.clear()
    return items
'''

AUDIT_INIT = '''"""The audit package. ``emit_legacy`` is re-exported here for old callers."""

from .log import emit, emit_legacy

__all__ = ["emit", "emit_legacy"]
'''

METRICS_COMPAT = '''"""Metrics compatibility shims (NOT the audit trail).

This module has its own ``emit_legacy``: it pushes a metric in the old
statsd-like format. It has nothing to do with audit.log.emit_legacy.
"""


def emit_legacy(name, value=1):
    """Push one metric in the old format."""
    return f"{name}:{value}|c"


def emit_v2(name, value=1):
    return {"name": name, "value": value}
'''


def emitting_module(rng: random.Random, pkg: str, mod: str, style: str, fake: str | None,
                    truth: list[tuple[str, int]], rel: str) -> str:
    """One module that calls audit's emit_legacy in ``style`` (and carries
    the look-alike ``fake`` too, when given). Real call lines are added to
    ``truth`` as they are written."""
    lines: list[str] = [f'"""{pkg}.{mod}: {ident(rng).replace("_", " ")} helpers."""', ""]
    imports = {
        "name": "from audit.log import emit_legacy",
        "alias": f"from audit.log import emit_legacy as _{rng.choice(['audit', 'old_audit', 'legacy_event'])}",
        "module_alias": f"import audit.log as {rng.choice(['alog', 'audit_log', 'trail'])}",
        "from_package_module": "from audit import log",
        "dotted": "import audit.log",
        "package_reexport": "from audit import emit_legacy",
        "multiline": "from audit.log import emit_legacy",
    }
    imp = imports[style]
    lines.append("import json")
    lines.append(imp)
    if fake == "same_name_other_module":
        lines.append("from metrics.compat import emit_legacy as metric_legacy")
    lines += ["", ""]
    if style == "alias":
        callee = imp.rsplit(" as ", 1)[1]
    elif style == "module_alias":
        callee = imp.rsplit(" as ", 1)[1] + ".emit_legacy"
    elif style == "from_package_module":
        callee = "log.emit_legacy"
    elif style == "dotted":
        callee = "audit.log.emit_legacy"
    else:
        callee = "emit_legacy"
    for _ in range(rng.randint(1, 3)):
        lines += filler_function(rng, ident(rng))
    n_calls = rng.choice([1, 1, 1, 2])
    for i in range(n_calls):
        fname = f"{ident(rng)}_{i}"
        event = f"{pkg}.{rng.choice(EVENTS)}"
        lines.append(f"def {fname}(record):")
        if fake == "docstring" and i == 0:
            lines.append(f'    """Write the {pkg} record. Old code called emit_legacy("{event}") here."""')
        else:
            lines.append(f'    """Write one {pkg} record."""')
        lines.append("    payload = json.dumps(record, sort_keys=True)")
        if fake == "comment" and i == 0:
            lines.append(f"    # TODO: switch emit_legacy(\"{event}\", ...) to audit.log.emit")
        if style == "multiline":
            lines.append("    result = emit_legacy(")
            truth.append((rel, len(lines)))
            lines.append(f'        "{event}",')
            lines.append("        size=len(payload),")
            lines.append("    )")
        else:
            lines.append(f'    result = {callee}("{event}", size=len(payload))')
            truth.append((rel, len(lines)))
        lines.append("    return result")
        lines += ["", ""]
    if fake == "string":
        lines.append(f'HELP = "Events from {pkg} used to go through emit_legacy(event, **fields)."')
        lines += ["", ""]
    if fake == "same_name_other_module":
        lines.append("def push_metric(name):")
        lines.append("    return metric_legacy(name, 1)")
        lines += ["", ""]
    if fake == "method":
        lines += [
            "class Recorder:",
            "    def emit_legacy(self, event):",
            "        return {'local': event}",
            "",
            "    def run(self, event):",
            "        return self.emit_legacy(event)",
            "",
            "",
        ]
    if fake == "v2":
        lines += [
            "def emit_legacy_v2(event):",
            "    return {'v2': event}",
            "",
            "",
            "def send_v2(event):",
            "    return emit_legacy_v2(event)",
            "",
            "",
        ]
    if fake == "reference" and style in ("name", "multiline", "package_reexport"):
        lines += [
            "HANDLERS = {'legacy': emit_legacy}",
            "",
            "",
        ]
    return "\n".join(lines).rstrip("\n") + "\n"


def lookalike_module(rng: random.Random, pkg: str, mod: str, fake: str) -> str:
    """A module that only LOOKS like a caller (never a real call)."""
    lines = [f'"""{pkg}.{mod}: {ident(rng).replace("_", " ")} helpers."""', "", "import json", ""]
    if fake == "same_name_other_module":
        lines += ["from metrics.compat import emit_legacy", "", "",
                  "def report_count(name, n):", "    return emit_legacy(name, n)", "", ""]
    elif fake == "local_def":
        lines += ["", "def emit_legacy(message):", "    \"\"\"Print a message the old way.\"\"\"",
                  "    print(message)", "    return message", "", "",
                  "def announce(text):", "    return emit_legacy(text.upper())", "", ""]
    elif fake == "method":
        lines += ["", "class Bridge:", "    def emit_legacy(self, event, **fields):",
                  "        return (event, fields)", "", "    def forward(self, event):",
                  "        return self.emit_legacy(event, forwarded=True)", "", ""]
    elif fake == "comment":
        lines += ["", "# emit_legacy(\"x\") was removed from this module in 2023.", ""]
    elif fake == "string":
        lines += ["", "DOC = '''", "    Example (old):", "        emit_legacy(\"orders.created\", id=1)", "'''", "", ""]
    elif fake == "v2":
        lines += ["", "def emit_legacy_v2(event):", "    return event", "", "",
                  "def go(event):", "    return emit_legacy_v2(event)", "", ""]
    elif fake == "docstring":
        lines += ["", "def migrate():", "    \"\"\"Replaces emit_legacy() calls with emit().\"\"\"", "    return None",
                  "", ""]
    else:  # reference: the function object, never called here
        lines += ["from audit.log import emit_legacy", "", "",
                  "LEGACY_SINKS = [emit_legacy]", "", "",
                  "def has_legacy():", "    return bool(LEGACY_SINKS)", "", ""]
    for _ in range(rng.randint(1, 2)):
        lines += filler_function(rng, ident(rng))
    return "\n".join(lines).rstrip("\n") + "\n"


def plain_module(rng: random.Random, pkg: str, mod: str) -> str:
    lines = [f'"""{pkg}.{mod}: {ident(rng).replace("_", " ")} helpers."""', "", "import json"]
    if rng.random() < 0.3:
        lines.append("from audit.log import emit")
    lines += ["", ""]
    for _ in range(rng.randint(2, 4)):
        lines += filler_function(rng, ident(rng))
    if "from audit.log import emit" in lines:
        lines += [f"def record(event):", f'    return emit("{pkg}." + event)', "", ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def generate(root: Path, seed: int) -> tuple[int, list[tuple[str, int]]]:
    rng = random.Random(seed)
    w = Writer(root)
    truth: list[tuple[str, int]] = []
    w.write("src/audit/__init__.py", AUDIT_INIT)
    w.write("src/audit/log.py", AUDIT_LOG)
    # The internal call inside audit/log.py (replay) is a real call.
    for i, line in enumerate(AUDIT_LOG.splitlines(), start=1):
        if "out.append(emit_legacy(" in line:
            truth.append(("src/audit/log.py", i))
    w.write("src/metrics/__init__.py", '"""Metrics."""\n')
    w.write("src/metrics/compat.py", METRICS_COMPAT)
    styles = REAL_STYLES * 7          # 49 emitting modules
    fakes = FAKE_STYLES * 6           # 48 look-alike-only modules
    slots = [(p, f"{n}_{i:02d}") for p in PACKAGES for i, n in
             enumerate(rng.choice(NOUNS) for _ in range(MODULES_PER_PACKAGE))]
    rng.shuffle(slots)
    emitters = slots[: len(styles)]
    lookalikes = slots[len(styles): len(styles) + len(fakes)]
    rng.shuffle(styles)
    rng.shuffle(fakes)
    special = dict(zip(emitters, styles))
    looks = dict(zip(lookalikes, fakes))
    for pkg in PACKAGES:
        w.write(f"src/{pkg}/__init__.py", f'"""The {pkg} package."""\n')
    for pkg, mod in sorted(slots):
        rel = f"src/{pkg}/{mod}.py"
        if (pkg, mod) in special:
            fake = rng.choice([None, None, None, *FAKE_STYLES])
            w.write(rel, emitting_module(rng, pkg, mod, special[(pkg, mod)], fake, truth, rel))
        elif (pkg, mod) in looks:
            w.write(rel, lookalike_module(rng, pkg, mod, looks[(pkg, mod)]))
        else:
            w.write(rel, plain_module(rng, pkg, mod))
    # The vendored copy: an older snapshot of some of our modules (NOT ours).
    vendor_rng = random.Random(seed * 7 + 1)
    for i in range(170):
        pkg = vendor_rng.choice(PACKAGES)
        style = vendor_rng.choice(REAL_STYLES)
        dummy: list[tuple[str, int]] = []
        w.write(f"vendor/acme_legacy/{pkg}_{i:03d}.py",
                emitting_module(vendor_rng, pkg, f"v{i}", style, None, dummy, ""))
    for i, pkg in enumerate(PACKAGES):
        for j in range(3):
            w.write(f"docs/{pkg}/page_{j}.md",
                    f"# {pkg} notes {j}\n\nOld modules call `emit_legacy(event, **fields)`; new code uses "
                    f"`audit.log.emit(event, **fields)`.\n\nSee also `{ident(rng)}`.\n")
    for i in range(60):
        w.write(f"config/{PACKAGES[i % len(PACKAGES)]}_{i:02d}.ini",
                f"[{PACKAGES[i % len(PACKAGES)]}]\naudit = {'legacy' if i % 5 == 0 else 'current'}\nlimit = {i * 3}\n")
    truth.sort(key=lambda t: (t[0], t[1]))
    return w.count, truth



def main(argv: list[str]) -> int:
    folder = Path(argv[1])
    seed = int(argv[2])
    count, truth = generate(folder, seed)
    if "--truth" in argv:
        print(json.dumps({"files": count, "calls": [f"{p}:{n}" for p, n in truth]}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
