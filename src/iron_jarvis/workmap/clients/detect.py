"""Noticing names that look like clients (v1.499.0, builder A).

NO MODEL CALL, no disk read, no file content: a pure pass over the work
record's own folder paths (every segment, not only the last). The owner's
own folder names are the strongest signal and are already local; the
owner's Yes is the judgement.

STUB until builder A lands: nothing is noticed (``candidates`` → ``[]``,
``notice`` → 0, the loop returns at once). The docstrings are the rule.

Input (``notice``, in ``asyncio.to_thread`` after
``work_map(...).refresh(budget=60)``; two bounded reads):

* Read 1, the folders: ``SELECT folder_key, MIN(folder), COUNT(*),
  MAX(updated_at), SUM(local_only OR keep_home), SUM(kind IN ('code',
  'tool_code')) FROM workentry WHERE archived = 0 GROUP BY folder_key LIMIT
  5000``. Each row is re-keyed with ``keys.home_folder`` (a worktree copy
  counts as its repository); ``index.covered`` is checked again.
* Read 2, the file names: ``files_json`` of the newest 3000 non-archived
  rows, for file-extension counts per folder.

Candidacy, per folder row: with a Clients-type segment, the ONLY candidate is
the first name segment after the LAST Clients-type segment (period segments
skipped), ``under_clients``; deeper segments are that client's subfolders.
Otherwise every name segment is a candidate. A candidate's home is the path
prefix through its segment, as spelled. Grouped by ``names.signature``:
homes (outermost kept), work folders, work, last_at, under_clients,
capitalised, all_code, code_share (``py ts tsx js jsx go rs java cs cpp c h
rb php toml lock yml yaml sh ps1``), local per home.

Qualifies: ``(under_clients AND work >= 2) OR (capitalised AND work_folders >=
2 AND work >= 3)``. Refused, in this order: a. code (not under_clients AND
(all_code OR (>= 5 named files AND code_share >= 0.6))); b. a shared subfolder
word (homes directly under >= 2 different parents that are other candidates'
name segments; period parents do not count); c. a container (its homes hold
>= 2 other qualifying candidates' homes); d. everywhere (>= 5 folders and its
work folders > 60% of them); e. nested (every home inside another surviving
candidate's home); f. taken (a home inside, or holding, a confirmed client's
folder); g. known (a row in any status has this signature or alias; a
proposed row is refreshed instead); h. forgotten (every home covered by
``workforgotten``); i. off (``config.notice_clients`` False).

Strength = ``(under_clients ? 5 : 0) + 2·len(homes) + min(work, 10)``, then
``last_at``, newest first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Iterable, Mapping

#: Noticing passes are throttled to one per this many seconds unless forced.
NOTICE_THROTTLE_S = 60.0
#: The background pass: first 120 s after start, then every 30 minutes.
FIRST_PASS_S = 120.0
EVERY_S = 30 * 60.0


@dataclass(frozen=True)
class CandidateHome:
    """One home of a candidate name: an ``evidence_json`` ``homes`` entry."""

    #: The path prefix through the name's segment, as spelled.
    folder: str
    #: Its folder key.
    key: str
    #: Pieces of work in it (and under it).
    work: int = 0
    last_at: datetime | None = None
    #: Every piece of work in it is Local only or kept home.
    local: bool = False


@dataclass(frozen=True)
class Candidate:
    """A name that qualifies and survived every refusal."""

    #: ``names.signature`` of the name: the row's UNIQUE key.
    signature: str
    #: ``names.display_name`` of its spellings.
    name: str
    #: ``"under_clients"`` | ``"several_folders"``.
    rule: str
    #: At most 8, strongest first.
    homes: tuple[CandidateHome, ...] = ()
    #: Pieces of work in its homes.
    work: int = 0
    #: Distinct work folders inside its homes.
    folders: int = 0
    last_at: datetime | None = None
    under_clients: bool = False
    #: The rank score.
    strength: int = 0
    #: Every spelling seen (the other spellings become aliases at the Yes).
    spellings: tuple[str, ...] = ()


def candidates(
    rows: Iterable[tuple[str, str, int, "datetime | None", int, int]],
    files: Iterable[tuple[str, str]],
    *,
    known: Mapping[str, str],
    confirmed_keys: Iterable[str],
    forgotten: Iterable[str],
) -> list[Candidate]:
    """PURE: the names that qualify and survive refusals a–h, strongest
    first.

    ``rows``: Read 1, ``(folder_key, folder, work, last_at, local_count,
    code_count)``. ``files``: Read 2, ``(folder_key, files_json)``.
    ``known``: every signature and alias on record → its row's status (a
    confirmed or declined one refuses the name; a proposed one is returned so
    :func:`notice` refreshes that row). ``confirmed_keys``: the folder keys a
    confirmed client claims. ``forgotten``: the ``workforgotten`` keys."""
    return []


def notice(platform: Any, *, force: bool = False) -> int:
    """One noticing pass (BLOCKING: call it through ``asyncio.to_thread``).

    Under a module lock plus the UNIQUE signature index: mints up to
    ``MAX_WAITING`` proposed rows, strongest first, and publishes
    ``client.suggested {ids}``; refreshes evidence and strength on rows still
    proposed; withdraws (deletes) proposed rows that no longer qualify (never
    answered, so nothing is lost). Throttled to one pass per
    :data:`NOTICE_THROTTLE_S` unless ``force``. Off when
    ``config.notice_clients`` is False. Returns how many rows it minted.
    Raises ``ClientsUnreadable`` when the record cannot be read (``POST
    /clients/notice`` answers 503; the loop and the after-answer pass
    swallow it)."""
    return 0


async def clients_loop(
    platform: Any,
    *,
    clock: Callable[[], float],
    sleep: Callable[[float], Awaitable[Any]],
) -> None:
    """``bg_tasks['work_clients']``: the first pass :data:`FIRST_PASS_S`
    after start, then every :data:`EVERY_S`, each through
    ``asyncio.to_thread(notice, platform)``. Time comes only from ``clock``
    and ``sleep`` (pinned without a real sleep). Never raises."""
    return None


__all__ = [
    "EVERY_S",
    "FIRST_PASS_S",
    "NOTICE_THROTTLE_S",
    "Candidate",
    "CandidateHome",
    "candidates",
    "clients_loop",
    "notice",
]
