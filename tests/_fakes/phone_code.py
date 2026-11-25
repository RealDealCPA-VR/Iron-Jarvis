"""Shared rig for the 12-a tests (start coding work from the phone): folders
the user has used (real git repositories under the test's own folder, each
with a coding session), a Telegram that answers from memory (10-b's
``FakeTelegramBack``) and a poller wired the way the daemon wires it.
Nothing here touches a network or the owner's folders."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from iron_jarvis.comm.models import PendingPromptRecord
from iron_jarvis.core.db import session_scope
from iron_jarvis.harness import night as N
from iron_jarvis.harness import sessions as S
from iron_jarvis.harness.night import NightTaskRecord
from sqlmodel import select
from tests._fakes.phone_back import FakeTelegramBack, telegram_back
from tests._fakes.phone_files import ME, message, poller
from tests.test_build_git_v1375 import make_repo


def used_repo(platform: Any, path: Path, *, name: str = "") -> Path:
    """A git repository the user has USED: a coding session works in it
    (and ``name``, when given, is its display name)."""
    repo = make_repo(path)
    S.create(platform.engine, str(repo), "A session", {})
    if name:
        S.rename_folder(platform.engine, str(repo), name)
    return repo


def rig(platform: Any, **over: Any) -> tuple[Any, FakeTelegramBack]:
    fake = FakeTelegramBack([])
    poll = poller(platform, telegram_back(fake, chat_id=ME, **over))
    # The identity exists once the sender has talked to Jarvis here.
    poll.thread_store.resolve("tg", str(ME), "Val")
    return poll, fake


def text(update_id: int, words: str, sender: int = ME, **kw: Any) -> dict:
    return message(update_id, sender, text=words, **kw)


def prompts(platform: Any, kind: str = "") -> list[PendingPromptRecord]:
    with session_scope(platform.engine) as db:
        rows = list(db.exec(select(PendingPromptRecord)).all())
    return [r for r in rows if not kind or r.kind == kind]


def tasks(platform: Any) -> list[dict]:
    with session_scope(platform.engine) as db:
        return [N.view(r) for r in db.exec(select(NightTaskRecord)).all()]


def report_json_of(platform: Any, task_id: str) -> str:
    with session_scope(platform.engine) as db:
        return str(db.get(NightTaskRecord, task_id).report_json or "")


__all__ = ["ME", "prompts", "report_json_of", "rig", "tasks", "text", "used_repo"]
