"""The intelligence plan, release 9: does guiding a small model make it finish
file tasks in fewer model calls? (NOT RUN BY ANYONE YET: it needs the owner's
yes, because it uses their own model server for an hour or so.)

The question the plan asks: on a FRESH set of about 40 file tasks, for a small
model of the user's own, the model calls per FINISHED task with release 9
against without it. Run it twice — once against a scratch daemon of the build
before release 9, once against one of this build — and compare:

    uv run python scripts/measure_guided_rounds.py tasks --seed 2026 --out set.json
    uv run python scripts/measure_guided_rounds.py run --daemon http://127.0.0.1:8899 \\
        --token-file <scratch root>/token.txt --provider ollama --model qwen3:4b \\
        --tasks set.json --work <empty folder> --out before.json --owner-said-yes
    (the same with the new build's scratch daemon)                --out after.json
    uv run python scripts/measure_guided_rounds.py compare before.json after.json

Rules it keeps (CLAUDE.md): it never talks to the owner's live app (port 8787
is refused, and so is 8765); a scratch daemon has its own root, its own port
and no provider or media keys; every task's material is MADE UP (no file of
the owner's); the set is drawn fresh from a seed and must never be tuned to
(a fix found by reading its misses is checked on a new seed). ``run`` refuses
without ``--owner-said-yes``.

What is counted, from the app's own records (``GET /sessions/{id}`` returns
``{session, transcript}``): a task is FINISHED when its own checker passes on
the folder afterwards (never the model's words); model calls are the
assistant turns in the transcript. Only finished tasks count toward "calls per
finished task"; unfinished ones are reported beside it.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

#: Ports that belong to the owner's live services: never measured against.
LIVE_PORTS = (8787, 8765)
TASK_COUNT = 40

_WORDS = (
    "amber", "basalt", "cinder", "delta", "ember", "fjord", "garnet", "harbor", "indigo",
    "juniper", "kelp", "lumen", "marrow", "nimbus", "onyx", "pollen", "quartz", "russet",
    "sorrel", "thistle", "umber", "vellum", "willow", "yarrow", "zephyr",
)


@dataclass
class Task:
    """One made-up file task: the files written before, the words the model
    is given, and what the checker expects afterwards."""

    id: str
    kind: str
    files: dict[str, str]
    prompt: str
    expect: dict[str, Any]


def _lines(rng: random.Random, n: int) -> list[str]:
    return [" ".join(rng.choice(_WORDS) for _ in range(rng.randint(3, 7))) for _ in range(n)]


def make_tasks(seed: int, count: int = TASK_COUNT) -> list[Task]:
    """``count`` made-up tasks of five kinds, drawn from ``seed``."""
    rng = random.Random(seed)
    kinds = ("replace", "count", "list", "join", "first_line")
    out: list[Task] = []
    for i in range(count):
        kind = kinds[i % len(kinds)]
        tid = f"t{i:02d}-{kind}"
        if kind == "replace":
            old, new = rng.sample(_WORDS, 2)
            body = "\n".join(_lines(rng, 6) + [f"the {old} line"]) + "\n"
            out.append(Task(tid, kind, {"notes.txt": body},
                            f"In notes.txt, replace every '{old}' with '{new}'. Change nothing else.",
                            {"file": "notes.txt", "absent": old, "present": new}))
        elif kind == "count":
            n = rng.randint(4, 15)
            out.append(Task(tid, kind, {"data.txt": "\n".join(_lines(rng, n)) + "\n"},
                            "Count the lines in data.txt and write only the number to count.txt.",
                            {"file": "count.txt", "equals": str(n)}))
        elif kind == "list":
            names = sorted({f"{rng.choice(_WORDS)}-{j}.txt" for j in range(rng.randint(3, 6))})
            files = {name: "x\n" for name in names}
            out.append(Task(tid, kind, files,
                            "Write the names of the .txt files in this folder, one per line, "
                            "sorted, into index.md (do not list index.md itself).",
                            {"file": "index.md", "lines": names}))
        elif kind == "join":
            a, b = _lines(rng, 2), _lines(rng, 2)
            out.append(Task(tid, kind, {"a.txt": "\n".join(a) + "\n", "b.txt": "\n".join(b) + "\n"},
                            "Make both.txt holding a.txt's lines followed by b.txt's lines.",
                            {"file": "both.txt", "lines": a + b}))
        else:
            rows = _lines(rng, 5)
            out.append(Task(tid, kind, {"log.txt": "\n".join(rows) + "\n"},
                            "Write the first line of log.txt into first.txt.",
                            {"file": "first.txt", "equals": rows[0]}))
    return out


def prepare(task: Task, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in task.files.items():
        (folder / name).write_text(text, encoding="utf-8")


def check(task: Task, folder: Path) -> bool:
    """Did the folder end up as the task asked? Mechanical, never the model's words."""
    path = folder / task.expect["file"]
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    if "absent" in task.expect:
        return task.expect["absent"] not in text and task.expect["present"] in text
    if "equals" in task.expect:
        return text.strip() == task.expect["equals"]
    got = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    return got == task.expect["lines"]


def solve(task: Task, folder: Path) -> None:
    """The reference answer (the checker's own test; never shown to a model)."""
    e = task.expect
    path = folder / e["file"]
    if "absent" in e:
        path.write_text(path.read_text(encoding="utf-8").replace(e["absent"], e["present"]), encoding="utf-8")
    elif "equals" in e:
        path.write_text(e["equals"] + "\n", encoding="utf-8")
    else:
        path.write_text("\n".join(e["lines"]) + "\n", encoding="utf-8")


def summarize(rows: list[dict]) -> dict[str, Any]:
    finished = [r for r in rows if r.get("finished")]
    calls = sum(int(r.get("model_calls") or 0) for r in finished)
    return {
        "tasks": len(rows),
        "finished": len(finished),
        "calls_per_finished_task": round(calls / len(finished), 2) if finished else None,
        "unfinished": [r["id"] for r in rows if not r.get("finished")],
    }


def refuse_live(daemon: str) -> str:
    """'' when ``daemon`` is a scratch daemon on this PC; else why not."""
    parts = urlsplit(daemon)
    if parts.hostname not in ("127.0.0.1", "localhost"):
        return "the scratch daemon must run on this PC"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if port in LIVE_PORTS:
        return f"port {port} is the owner's live app; start a scratch daemon on its own port"
    return ""


def _http(method: str, url: str, token: str, body: Any = None, timeout: float = 900.0) -> Any:
    import urllib.request

    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — this PC only
        return json.loads(resp.read() or b"null")


def run(args: argparse.Namespace) -> int:
    why = refuse_live(args.daemon)
    if why:
        print(f"refused: {why}", file=sys.stderr)
        return 2
    if not args.owner_said_yes:
        print("refused: this uses the owner's own model server; pass --owner-said-yes only after they said yes",
              file=sys.stderr)
        return 2
    token = Path(args.token_file).read_text(encoding="utf-8").strip()
    tasks = [Task(**t) for t in json.loads(Path(args.tasks).read_text(encoding="utf-8"))]
    work = Path(args.work)
    rows: list[dict] = []
    for task in tasks:
        folder = work / task.id
        prepare(task, folder)
        started = time.monotonic()
        sess = _http("POST", f"{args.daemon}/sessions", token, {
            "task": task.prompt, "provider": args.provider, "model": args.model, "wait": True,
            "workspace_root": str(folder), "approval_mode": "approve_for_me",
        })
        sid = str((sess or {}).get("id") or "")
        got = _http("GET", f"{args.daemon}/sessions/{sid}", token) if sid else {}
        transcript = (got or {}).get("transcript") or []
        calls = sum(1 for m in transcript if isinstance(m, dict) and m.get("role") == "assistant")
        rows.append({
            "id": task.id, "kind": task.kind, "session": sid, "finished": check(task, folder),
            "model_calls": calls, "seconds": round(time.monotonic() - started, 1),
        })
        print(json.dumps(rows[-1]))
    Path(args.out).write_text(json.dumps({"rows": rows, "summary": summarize(rows)}, indent=1), encoding="utf-8")
    print(json.dumps(summarize(rows), indent=1))
    return 0


def compare(a: dict, b: dict) -> str:
    sa, sb = summarize(a["rows"]), summarize(b["rows"])
    return (
        f"before: {sa['finished']}/{sa['tasks']} finished, {sa['calls_per_finished_task']} model calls per finished task\n"
        f"after:  {sb['finished']}/{sb['tasks']} finished, {sb['calls_per_finished_task']} model calls per finished task"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tasks")
    t.add_argument("--seed", type=int, required=True)
    t.add_argument("--out", required=True)
    r = sub.add_parser("run")
    for name in ("--daemon", "--token-file", "--provider", "--model", "--tasks", "--work", "--out"):
        r.add_argument(name, required=True)
    r.add_argument("--owner-said-yes", action="store_true")
    c = sub.add_parser("compare")
    c.add_argument("before")
    c.add_argument("after")
    args = p.parse_args(argv)
    if args.cmd == "tasks":
        Path(args.out).write_text(json.dumps([asdict(x) for x in make_tasks(args.seed)], indent=1), encoding="utf-8")
        return 0
    if args.cmd == "run":
        return run(args)
    print(compare(*(json.loads(Path(x).read_text(encoding="utf-8")) for x in (args.before, args.after))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
