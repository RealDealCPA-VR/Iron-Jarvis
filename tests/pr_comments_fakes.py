"""Shared doubles for the 26-a tests (``harness/pr_comments``): a FAKE ``gh``
on PATH that answers ``gh api`` from a state file (never the real GitHub), and
GitHub-shaped items. Not a test module."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from iron_jarvis.harness import branches as B

PR_URL = "https://github.com/acme/proj/pull/7"
PR_API = "repos/acme/proj/pulls/7"
#: A key-shaped value, built at run time (never written as one value).
KEYISH = "ghp_" + "Q" * 36

FAKE_GH = r'''
import json, os, sys
path = os.environ["IJ_FAKE_GH_STATE"]
with open(path, encoding="utf-8") as fh:
    st = json.load(fh)
args = sys.argv[1:]
st.setdefault("calls", []).append(args)
with open(path, "w", encoding="utf-8") as fh:
    json.dump(st, fh)
if args[:1] == ["api"]:
    rest = [a for a in args[1:] if a != "--paginate"]
    if rest[:1] == ["--hostname"]:
        rest = rest[2:]
    route = (rest[0] if rest else "").split("?")[0]
    if st.get("fail"):
        sys.stderr.write("HTTP 502: Bad Gateway")
        sys.exit(1)
    answers = st.get("answers", {})
    if route in answers:
        out = answers[route]
        if st.get("old_pages") and isinstance(out, list):
            # an older gh: one array per page, back to back
            sys.stdout.write("".join(json.dumps(out[i:i + 2]) for i in range(0, len(out), 2)))
        else:
            sys.stdout.write(json.dumps(out))
        sys.exit(0)
    sys.stderr.write("HTTP 404: Not Found")
    sys.exit(1)
sys.exit(2)
'''


class FakeGh:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.state = folder / "state.json"
        self.state.write_text(json.dumps({}), encoding="utf-8")
        script = folder / "fake_gh.py"
        script.write_text(FAKE_GH, encoding="utf-8")
        if sys.platform == "win32":
            (folder / "gh.cmd").write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        else:
            launcher = folder / "gh"
            launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
            launcher.chmod(0o755)

    def set(self, **values: Any) -> None:
        st = json.loads(self.state.read_text(encoding="utf-8"))
        st.update(values)
        self.state.write_text(json.dumps(st), encoding="utf-8")

    def read(self) -> dict:
        return json.loads(self.state.read_text(encoding="utf-8"))

    def calls(self, *head: str) -> list[list[str]]:
        return [c for c in self.read().get("calls", []) if c[: len(head)] == list(head)]

    def answer(self, *, pr: "dict | None" = None, comments: "list | None" = None, reviews: "list | None" = None,
               api: str = PR_API) -> None:
        self.set(answers={api: pr if pr is not None else {"state": "open", "merged_at": None},
                          f"{api}/comments": comments or [], f"{api}/reviews": reviews or []})


def install(tmp_path: Path, monkeypatch) -> FakeGh:
    gh = FakeGh(tmp_path / "fakegh")
    monkeypatch.setenv("IJ_FAKE_GH_STATE", str(gh.state))
    monkeypatch.setenv("PATH", str(gh.folder) + os.pathsep + os.environ["PATH"])
    B.forget_gh_state()
    return gh


def line_comment(cid: int, *, login: str = "sam", assoc: str = "MEMBER", path: str = "app.py", line: "int | None" = 1,
                 body: str = "Rename x.", created: str = "2026-11-20T10:00:00Z", user_type: str = "User",
                 **over: Any) -> dict:
    item = {
        "id": cid, "user": {"login": login, "type": user_type}, "author_association": assoc, "path": path,
        "line": line, "original_line": line or 5, "side": "RIGHT", "body": body,
        "diff_hunk": "@@ -1,2 +1,2 @@\n-x = 1\n+x = 2", "created_at": created,
        "html_url": f"https://github.com/acme/proj/pull/7#discussion_r{cid}",
    }
    item.update(over)
    return item


def review(rid: int, *, login: str = "alex", assoc: str = "OWNER", body: str = "Please add a test.",
           state: str = "CHANGES_REQUESTED", submitted: str = "2026-11-20T11:00:00Z") -> dict:
    return {"id": rid, "user": {"login": login, "type": "User"}, "author_association": assoc, "body": body,
            "state": state, "submitted_at": submitted,
            "html_url": f"https://github.com/acme/proj/pull/7#pullrequestreview-{rid}"}
