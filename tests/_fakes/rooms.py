"""Drive the backend round-table ROOMS directly (v1.439.0, Wave 6 group E).

The Agents page's round table left the dashboard in v1.308.0, and its HTTP
doors were removed in v1.439.0 (decision D6, docs/ROUTES-WITHOUT-A-SCREEN.md):
``GET``/``POST /agents/threads``, ``PUT …/participants``, ``DELETE
/agents/threads/{id}``, ``POST …/say``, ``POST …/remember`` and ``POST
/projects/{id}/world/room``.

The ROOMS STAY (``agents/threads.py``): chat @-mentions (``POST /chat/panel``),
the phone and remote inbound all run rounds in them, and ``GET
/agents/threads/{id}`` still serves one. Many tests pinned the ROOM's own
behaviour (who speaks, what each speaker is shown, the identity spine, the
memory ladder) by creating a room and saying something through those doors.
``rooms(client)`` gives them a door-shaped handle onto the SAME backend
calls the removed routes made, so those pins keep pinning the backend:

- ``post("/agents/threads", json)`` -> ``AgentThreads.create`` (participants
  through ``clean_participants``; a bad panel answers 400 as before);
- ``get("/agents/threads?project_id=…")`` -> ``AgentThreads.list``;
- ``put(".../participants", json)`` -> ``update_participants``;
- ``delete("/agents/threads/{id}")`` -> ``delete``;
- ``post(".../say", json)`` -> ``run_round`` (KeyError 404, ValueError 400);
- ``post(".../remember", json)`` -> ``remember`` (KeyError 404, ValueError 400,
  RuntimeError 422).

Anything else (``GET /agents/threads/{id}``, every other route) goes to the
real client. What lived ONLY in the removed routes (a project id checked
against the Projects table, the ``{"preview": null}`` parse) is NOT copied
here: a fake that re-implements a removed route would only test itself.
Async backend calls run on the client's own portal (``_portal_factory``), the
same loop a request would have used.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

_ROOM = re.compile(r"^/agents/threads/([^/]+)/(say|remember|participants)$")
_ONE = re.compile(r"^/agents/threads/([^/]+)$")


class RoomAnswer:
    """The two things the old tests read off a response."""

    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload

    @property
    def text(self) -> str:
        import json as _json

        return _json.dumps(self._payload, default=str)


class RoomDoor:
    def __init__(self, client: Any) -> None:
        self._client = client
        self._d = client.app.state.d

    # -- plumbing ------------------------------------------------------------
    def _threads(self):
        from iron_jarvis.agents.threads import AgentThreads

        return AgentThreads(self._d.platform.engine)

    def _run(self, make_coro) -> Any:
        with self._client._portal_factory() as portal:
            return portal.call(make_coro)

    @staticmethod
    def _view(rec) -> dict[str, Any]:
        from iron_jarvis.agents.threads import thread_view

        return thread_view(rec)

    # -- the door shape --------------------------------------------------------
    def post(self, path: str, json: Any = None, **kw: Any) -> Any:
        body = json if isinstance(json, dict) else {}
        if path == "/agents/threads":
            from iron_jarvis.agents.threads import clean_participants

            try:
                participants = clean_participants(body.get("participants"))
            except ValueError as exc:
                return RoomAnswer(400, {"detail": str(exc)})
            rec = self._threads().create(
                str(body.get("title") or ""),
                participants,
                project_id=str(body.get("project_id") or "").strip(),
            )
            return RoomAnswer(200, self._view(rec))
        m = _ROOM.match(path)
        if m and m.group(2) == "say":
            tid = m.group(1)
            threads = self._threads()
            message = str(body.get("message") or "")

            async def _say():
                return await threads.run_round(tid, message, self._d)

            return self._answer(_say)
        if m and m.group(2) == "remember":
            tid = m.group(1)
            threads = self._threads()

            async def _remember():
                return await threads.remember(
                    tid,
                    self._d,
                    mode=str(body.get("mode") or "distill"),
                    source=str(body.get("source") or ""),
                    provider=str(body.get("provider") or ""),
                    model=str(body.get("model") or ""),
                    preview=body.get("preview", True) is not False,
                    approved_content=str(body.get("content") or ""),
                )

            return self._answer(_remember)
        return self._client.post(path, json=json, **kw)

    def _answer(self, make_coro) -> RoomAnswer:
        try:
            return RoomAnswer(200, self._run(make_coro))
        except KeyError:
            return RoomAnswer(404, {"detail": "no such thread"})
        except ValueError as exc:
            return RoomAnswer(400, {"detail": str(exc)})
        except RuntimeError as exc:
            return RoomAnswer(422, {"detail": str(exc)})

    def get(self, path: str, **kw: Any) -> Any:
        parts = urlsplit(path)
        if parts.path == "/agents/threads":
            pid = (parse_qs(parts.query).get("project_id") or [""])[0].strip()
            out = []
            for rec in self._threads().list(project_id=pid):
                view = self._view(rec)
                view.pop("messages", None)
                out.append(view)
            return RoomAnswer(200, {"threads": out})
        return self._client.get(path, **kw)

    def put(self, path: str, json: Any = None, **kw: Any) -> Any:
        m = _ROOM.match(path)
        if m and m.group(2) == "participants":
            from iron_jarvis.agents.threads import clean_participants

            body = json if isinstance(json, dict) else {}
            try:
                participants = clean_participants(body.get("participants"))
            except ValueError as exc:
                return RoomAnswer(400, {"detail": str(exc)})
            rec = self._threads().update_participants(m.group(1), participants)
            if rec is None:
                return RoomAnswer(404, {"detail": "no such thread"})
            return RoomAnswer(200, self._view(rec))
        return self._client.put(path, json=json, **kw)

    def delete(self, path: str, **kw: Any) -> Any:
        m = _ONE.match(path)
        if m:
            if not self._threads().delete(m.group(1)):
                return RoomAnswer(404, {"detail": "no such thread"})
            return RoomAnswer(200, {"deleted": m.group(1)})
        return self._client.delete(path, **kw)


def rooms(client: Any) -> RoomDoor:
    """A door-shaped handle onto the backend rooms behind ``client``'s app."""
    return RoomDoor(client)
