"""What the two client pages read (v1.499.0, builder A).

Plain, BLOCKING reads (the routes are plain ``def``). They WRITE NOTHING and
never mint a suggestion. Every claim names the record it came from. Nothing
here is told to a model, and nothing is read from disk.

STUB until builder A lands: the list is empty and every client is "not on
record". The docstrings are the rule to build.
"""

from __future__ import annotations

from typing import Any

from . import ClientNotFound


def clients_list(platform: Any) -> dict[str, Any]:
    """``GET /clients``::

        {clients:[{id,name,how,folders:[{folder,name}],work_count,last_at,
                   preferences_count}],
         waiting:[{id,name,rule,about,evidence:[str],folders,work,pre_ticked,
                   created_at}],
         declined:[{id,name,decided_at,was_client}], total, noticing}

    ``pre_ticked`` is true only for a name found under a Clients-type folder.
    Evidence names folders as ``parent › name``, never a full path.
    ``noticing`` is ``config.notice_clients``. Raises ``ClientsUnreadable``
    (the route answers 503, never an empty list)."""
    config = getattr(platform, "config", None)
    return {
        "clients": [],
        "waiting": [],
        "declined": [],
        "total": 0,
        "noticing": bool(getattr(config, "notice_clients", True)),
    }


def client_page(platform: Any, client_id: str) -> dict[str, Any]:
    """``GET /clients/{id}``::

        {client:{id,name,status,how,aliases,created_at,confirmed_at,why:[str]},
         folders:[{folder,name,how,count,last_at,forgotten}],
         entries:[public row], entries_total,
         files:[{name,path,how,folder_name,entry_id,title,link,at}], files_total,
         decisions:[{text,label:"decided"|"open",entry_id,title,link,written_at,
                     current}],
         people:[{name,how:"title"|"file"|"email",seen_in:[{entry_id,title,link}]}],
         preferences:[{id,text,origin,local_only,evidence,created_at}],
         named_elsewhere:[{entry_id,title,link,folder_name}],
         projects:[{id,name,root}], told: str}

    Work: ``WorkMap.for_folder(root, under=True, as_spelled=True)`` per folder
    (≤ 8), deduplicated, newest first, 100 shown. Files: the union of that
    work's ``files_json`` (≤ 100). Decisions: the ``Decided:`` / ``Open:``
    lines of kept recaps of that work (≤ 40, a stale one marked). People:
    computed at read time from the owner's own titles and file stems plus
    e-mail addresses in checked recap lines (≤ 20; never stored, never told).
    Named elsewhere: ≤ 10 titles outside its folders naming it. Projects:
    active Projects whose root is inside one of its folders. ``told``:
    ``block.client_block(platform, folder=<its first folder>, local=False)``.
    A forgotten folder shows no work. Raises ``ClientNotFound`` (404) and
    ``ClientsUnreadable`` (503)."""
    raise ClientNotFound()


__all__ = ["client_page", "clients_list"]
