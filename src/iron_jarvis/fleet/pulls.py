"""Model downloads: one at a time per server, with progress and Cancel (next
leaps 43-a; idea 16 and idea 37 use the same call, ``model_download``).

* **Before it starts** (the route and the chat tool, ``fleet/library.preview``):
  the exact size is read from the registry, and a download that would leave
  less than a tenth of the drive free is refused in words (D-43.2). Nothing
  reaches the model server until that check passed.
* **While it runs**: ONE worker thread per download streams Ollama's
  ``POST /api/pull`` answer (NDJSON, one line per layer's progress). Every read
  is bounded (:data:`READ_STALL_S` without a byte fails it; a line longer than
  :data:`MAX_LINE` is dropped), never on the event loop. Progress is published
  as ``model.download.progress`` at most once per whole percent and once per
  :data:`PROGRESS_EVERY_S` (so at most about 100 events and 100 row writes a
  download), and the end as ``model.download.ended``.
* **Cancel** shuts the connection down and closes its socket (so a read that
  is waiting wakes at once on Windows and Linux); Ollama stops the pull and
  keeps the part already downloaded (said on the card: Download again carries
  on).
* **A restart**: a row an earlier daemon left ``downloading`` is settled to
  ``stopped`` with words at the next start (:meth:`PullManager.settle`).

Everything here is BLOCKING (database writes, the stream): the routes call it
with ``asyncio.to_thread``. A time is read through an injected clock.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import threading
import time
from typing import Any, Callable

from ..core.events import EventType
from ..core.ids import utcnow
from . import library as L
from .pull_models import ModelDownload

log = logging.getLogger("ironjarvis.fleet")

DOWNLOADING = "downloading"
DONE = "done"
STOPPED = "stopped"
FAILED = "failed"
STATES = (DOWNLOADING, DONE, STOPPED, FAILED)

#: A pull that sends nothing for this long has stopped answering (verifying
#: a very large model's checksum can take minutes with no line).
READ_STALL_S = 300.0
CONNECT_S = 10.0
#: The longest line read from the stream; a longer one is dropped.
MAX_LINE = 64 * 1024
#: Progress is published at most this often (and only when the whole
#: percent moved).
PROGRESS_EVERY_S = 1.0
#: How long the worker waits for one event to reach the loop.
PUBLISH_WAIT_S = 5.0
#: Rows the list keeps showing.
LIST_LIMIT = 20

PROGRESS_EVENT = EventType.MODEL_DOWNLOAD_PROGRESS
ENDED_EVENT = EventType.MODEL_DOWNLOAD_ENDED

CANCELLED_WORDS = "Stopped: you pressed Cancel. " + L.PARTIAL_KEPT_WORDS
APP_CLOSED_WORDS = "Stopped when Iron Jarvis closed. " + L.PARTIAL_KEPT_WORDS
STALLED_WORDS = "Stopped: the model server sent nothing for 5 minutes. " + L.PARTIAL_KEPT_WORDS
UNREACHABLE_WORDS = "The model server could not be reached, so nothing was downloaded."
OLLAMA_NOT_ANSWERING = "Ollama is not answering on this PC. Start Ollama, then press Download again."
BUSY_WORDS = (
    "{label} is already downloading {other}. One download at a time per server: wait for it to finish, "
    "or press Cancel on it first."
)


class PullRefused(Exception):
    def __init__(self, words: str, status: int = 409) -> None:
        super().__init__(words)
        self.words = words
        self.status = status


class _Live:
    """A download this daemon is running right now."""

    def __init__(self, row_id: str, node_id: str, total: int | None) -> None:
        self.id = row_id
        self.node_id = node_id
        self.total = total
        self.done = 0
        self.layers: dict[str, tuple[int, int]] = {}
        self.cancel = threading.Event()
        self.sock: socket.socket | None = None
        self.thread: threading.Thread | None = None
        self.status = ""
        self.unreachable = UNREACHABLE_WORDS


def _mask(text: str) -> str:
    try:
        from ..providers.failure_words import mask_failure_text

        return mask_failure_text(text)
    except Exception:  # noqa: BLE001
        return text


def server_words(raw: Any) -> str:
    """What the server said went wrong, as data: masked, one line, bounded."""
    text = " ".join(str(raw or "").split())[:240]
    return _mask(text)


def view(row: ModelDownload, live: "_Live | None" = None) -> dict[str, Any]:
    total = live.total if live is not None and live.total else row.bytes_total
    done = live.done if live is not None else row.bytes_done
    percent = None
    if isinstance(total, int) and total > 0:
        percent = max(0, min(100, int(done * 100 / total)))
    if row.state == DONE:
        percent = 100
    return {
        "id": row.id,
        "node_id": row.node_id,
        "model": row.model,
        "name": row.name or row.model,
        "bytes_total": total,
        "size_from": row.size_from,
        "bytes_done": done,
        "percent": percent,
        "state": row.state,
        "started_by": row.started_by,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "error": row.error,
        "size_words": L.fmt_bytes(total) if total else "",
        "done_words": L.fmt_bytes(done) if done else "0 MB",
    }


class PullManager:
    """The downloads of one daemon. ``loop`` is where events are published
    (the routes pass theirs); with none, no event is sent (the rows still
    say everything)."""

    def __init__(
        self,
        engine: Any,
        bus: Any = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        on_done: "Callable[[str, str], None] | None" = None,
    ) -> None:
        self.engine = engine
        self.bus = bus
        self.clock = clock
        self.on_done = on_done
        self._lock = threading.Lock()
        self._live: dict[str, _Live] = {}
        self._by_node: dict[str, str] = {}
        ModelDownload.__table__.create(engine, checkfirst=True)  # type: ignore[attr-defined]

    # --- rows ---------------------------------------------------------------
    def _session(self):
        from ..core.db import session_scope

        return session_scope(self.engine)

    def _get_row(self, row_id: str) -> ModelDownload | None:
        with self._session() as db:
            row = db.get(ModelDownload, row_id)
            if row is not None:
                db.expunge(row)
            return row

    def _update(self, row_id: str, **fields: Any) -> ModelDownload | None:
        with self._session() as db:
            row = db.get(ModelDownload, row_id)
            if row is None:
                return None
            for k, v in fields.items():
                setattr(row, k, v)
            db.add(row)
            db.commit()
            db.refresh(row)
            db.expunge(row)
            return row

    def get(self, row_id: str) -> dict[str, Any] | None:
        row = self._get_row(row_id)
        if row is None:
            return None
        return view(row, self._live.get(row_id))

    def list(self, limit: int = LIST_LIMIT) -> list[dict[str, Any]]:
        from sqlmodel import select

        with self._session() as db:
            rows = list(
                db.exec(select(ModelDownload).order_by(ModelDownload.started_at.desc()).limit(limit))  # type: ignore[attr-defined]
            )
            for r in rows:
                db.expunge(r)
        return [view(r, self._live.get(r.id)) for r in rows]

    def running_on(self, node_id: str) -> dict[str, Any] | None:
        with self._lock:
            rid = self._by_node.get(node_id)
        return self.get(rid) if rid else None

    def settle(self) -> int:
        """Rows an earlier daemon left ``downloading`` end as ``stopped``,
        with words. Idempotent; never touches a download running now."""
        from sqlmodel import select

        n = 0
        with self._session() as db:
            rows = list(db.exec(select(ModelDownload).where(ModelDownload.state == DOWNLOADING)))
            for row in rows:
                if row.id in self._live:
                    continue
                row.state = STOPPED
                row.error = APP_CLOSED_WORDS
                row.finished_at = utcnow()
                db.add(row)
                n += 1
            if n:
                db.commit()
        return n

    # --- start / cancel -------------------------------------------------------
    def start(
        self,
        target: "L.Target",
        *,
        model: str,
        name: str,
        size: int | None,
        size_from: str,
        headers: "dict[str, str] | None" = None,
        loop: "asyncio.AbstractEventLoop | None" = None,
        started_by: str = "page",
    ) -> dict[str, Any]:
        """Start downloading *model* into *target* (its size checked by the
        caller). Refused in words when the server already downloads one."""
        clean = L.clean_model_name(model)
        if not clean:
            raise PullRefused(L.BAD_NAME_WORDS, status=400)
        if not target.downloads:
            raise PullRefused(L.download_why(target))
        root = L.server_root(target.base_url)
        if not root:
            raise PullRefused("That model server has no address saved.")
        with self._lock:
            other = self._by_node.get(target.id)
            if other:
                busy = self._get_row(other)
                raise PullRefused(
                    BUSY_WORDS.format(label=target.label, other=(busy.name or busy.model) if busy else "a model")
                )
            row = ModelDownload(
                node_id=target.id,
                model=clean,
                name=str(name or clean)[:120],
                bytes_total=size if isinstance(size, int) and size > 0 else None,
                size_from=size_from if size_from in ("measured", "about") else "",
                started_by="chat" if started_by == "chat" else "page",
            )
            with self._session() as db:
                db.add(row)
                db.commit()
                db.refresh(row)
                db.expunge(row)
            live = _Live(row.id, target.id, row.bytes_total)
            if target.kind == "ollama" and target.this_pc:
                live.unreachable = OLLAMA_NOT_ANSWERING
            self._live[row.id] = live
            self._by_node[target.id] = row.id
        worker = threading.Thread(
            target=self._run,
            args=(live, root, clean, dict(headers or {}), loop),
            name=f"ij-model-download-{row.id}",
            daemon=True,
        )
        live.thread = worker
        worker.start()
        return view(row, live)

    def cancel(self, row_id: str) -> dict[str, Any]:
        """Stop a download. A running one stops at once (its connection is
        shut down); one an earlier daemon left is settled; an ended one is
        returned as it is."""
        live = self._live.get(row_id)
        if live is not None:
            live.cancel.set()
            sock = live.sock
            if sock is not None:
                # Both, measured on Windows 11: a shutdown alone left the
                # waiting read blocked until the server sent again (8 s in the
                # probe); closing the socket woke it at once (0.0 s). On Linux
                # the shutdown is what wakes it.
                for stop in (lambda: sock.shutdown(socket.SHUT_RDWR), sock.close):
                    try:
                        stop()
                    except OSError:
                        pass
            if live.thread is not None:
                live.thread.join(timeout=10.0)
            got = self.get(row_id)
            if got is None:
                raise PullRefused("There is no such download.", status=404)
            return got
        row = self._get_row(row_id)
        if row is None:
            raise PullRefused("There is no such download.", status=404)
        if row.state == DOWNLOADING:
            row = self._update(row_id, state=STOPPED, error=CANCELLED_WORDS, finished_at=utcnow()) or row
        return view(row)

    def wait(self, row_id: str, timeout: float = 30.0) -> dict[str, Any] | None:
        """Wait for a download's worker to end (tests and the chat card)."""
        live = self._live.get(row_id)
        if live is not None and live.thread is not None:
            live.thread.join(timeout=timeout)
        return self.get(row_id)

    # --- events -------------------------------------------------------------
    def _publish(self, loop: Any, kind: str, payload: dict[str, Any]) -> None:
        if self.bus is None or loop is None:
            return
        try:
            if loop.is_closed():
                return
            fut = asyncio.run_coroutine_threadsafe(self.bus.publish(kind, payload), loop)
            fut.result(timeout=PUBLISH_WAIT_S)
        except Exception:  # noqa: BLE001 — the row says it either way
            log.debug("could not publish %s", kind, exc_info=True)

    # --- the worker ---------------------------------------------------------
    def _lines(self, response: Any, live: _Live):
        """The stream's lines, each at most :data:`MAX_LINE` (a longer one is
        dropped whole)."""
        buf = b""
        dropping = False
        for chunk in response.iter_bytes():
            if live.cancel.is_set():
                return
            buf += chunk
            while True:
                cut = buf.find(b"\n")
                if cut < 0:
                    break
                line, buf = buf[:cut], buf[cut + 1:]
                if dropping:
                    dropping = False
                    continue
                yield line
            if len(buf) > MAX_LINE:
                buf = b""
                dropping = True
        if buf.strip() and not dropping:
            yield buf

    def _run(self, live: _Live, root: str, model: str, headers: dict[str, str], loop: Any) -> None:
        import httpx

        from ..core import http as _http

        state, error = FAILED, ""
        last_pub = -1e18
        last_pct = -1
        success = False
        try:
            timeout = httpx.Timeout(connect=CONNECT_S, read=READ_STALL_S, write=30.0, pool=CONNECT_S)
            with _http.client(timeout=timeout, follow_redirects=False, trust_env=False) as c:
                body = {"model": model, "name": model, "stream": True}
                with c.stream("POST", f"{root}/api/pull", json=body, headers=headers) as r:
                    try:
                        ns = r.extensions.get("network_stream")
                        live.sock = ns.get_extra_info("socket") if ns is not None else None
                    except Exception:  # noqa: BLE001 — Cancel then waits for the next line
                        live.sock = None
                    if live.cancel.is_set():
                        raise _Cancelled
                    if r.status_code != 200:
                        said = ""
                        try:
                            head = b""
                            for chunk in r.iter_bytes():
                                head += chunk
                                if len(head) >= 4096:
                                    break
                            said = server_words((json.loads(head[:4096] or b"{}") or {}).get("error"))
                        except Exception:  # noqa: BLE001 — the status alone is said
                            pass
                        raise _Failed(
                            f"The model server answered {r.status_code}"
                            + (f": {said}" if said else ".")
                        )
                    for raw in self._lines(r, live):
                        if live.cancel.is_set():
                            break
                        try:
                            obj = json.loads(raw)
                        except ValueError:
                            continue
                        if not isinstance(obj, dict):
                            continue
                        if obj.get("error"):
                            raise _Failed("The model server said: " + server_words(obj.get("error")))
                        live.status = server_words(obj.get("status"))[:80]
                        digest = str(obj.get("digest") or "")[:100]
                        total, completed = obj.get("total"), obj.get("completed")
                        if digest and isinstance(total, int) and total > 0:
                            done_here = completed if isinstance(completed, int) and completed >= 0 else 0
                            live.layers[digest] = (total, min(done_here, total))
                            live.done = sum(c for _t, c in live.layers.values())
                            seen_total = sum(t for t, _c in live.layers.values())
                            if not live.total or seen_total > live.total:
                                live.total = seen_total
                        if live.status == "success":
                            success = True
                            if live.total:
                                live.done = live.total
                        pct = int(live.done * 100 / live.total) if live.total else -1
                        now = float(self.clock())
                        if pct != last_pct and (now - last_pub >= PROGRESS_EVERY_S or pct >= 100):
                            last_pct, last_pub = pct, now
                            self._update(live.id, bytes_done=live.done, bytes_total=live.total)
                            self._publish(loop, PROGRESS_EVENT, {
                                "id": live.id, "node_id": live.node_id, "model": model,
                                "bytes_done": live.done, "bytes_total": live.total,
                                "percent": pct if pct >= 0 else None,
                            })
            if live.cancel.is_set():
                state, error = STOPPED, CANCELLED_WORDS
            elif success:
                state, error = DONE, ""
            else:
                state, error = FAILED, "The model server ended the download without saying it was finished."
        except _Cancelled:
            state, error = STOPPED, CANCELLED_WORDS
        except _Failed as exc:
            state, error = FAILED, str(exc)
        except httpx.ReadTimeout:
            state, error = (STOPPED, CANCELLED_WORDS) if live.cancel.is_set() else (FAILED, STALLED_WORDS)
        except httpx.ConnectError:
            state, error = (STOPPED, CANCELLED_WORDS) if live.cancel.is_set() else (FAILED, live.unreachable)
        except Exception as exc:  # noqa: BLE001 — a download that breaks is a failed row, never a crash
            if live.cancel.is_set():
                state, error = STOPPED, CANCELLED_WORDS
            else:
                log.info("model download %s broke: %s", live.id, _mask(f"{type(exc).__name__}: {exc}")[:300])
                state, error = FAILED, "The download broke off. " + L.PARTIAL_KEPT_WORDS
        finally:
            fields: dict[str, Any] = {
                "state": state, "error": error, "finished_at": utcnow(), "bytes_done": live.done,
            }
            if live.total:
                fields["bytes_total"] = live.total
            row = None
            try:
                row = self._update(live.id, **fields)
            except Exception:  # noqa: BLE001
                log.warning("could not save the end of download %s", live.id, exc_info=True)
            with self._lock:
                self._live.pop(live.id, None)
                if self._by_node.get(live.node_id) == live.id:
                    self._by_node.pop(live.node_id, None)
            if state == DONE and self.on_done is not None:
                try:
                    self.on_done(live.node_id, model)
                except Exception:  # noqa: BLE001
                    log.debug("after-download step failed", exc_info=True)
            self._publish(loop, ENDED_EVENT, {
                "id": live.id, "node_id": live.node_id, "model": model, "state": state,
                "error": error, "bytes_done": live.done, "bytes_total": live.total,
            } if row is None else {**view(row), "id": live.id})


class _Cancelled(Exception):
    pass


class _Failed(Exception):
    pass


def _after_download(d: Any) -> Callable[[str, str], None]:
    """What changes once a model is in: the model menu reads the server's
    list again, this PC's drive is read again, and the Model servers page's
    next check sees it."""

    def _done(node_id: str, _model: str) -> None:
        from . import hardware as HW

        HW.forget()
        try:
            from ..daemon.routes.connections import invalidate_model_catalog

            invalidate_model_catalog(
                discovery=True, provider="ollama" if node_id == L.THIS_PC_OLLAMA else f"fleet-{node_id}"
            )
        except Exception:  # noqa: BLE001
            pass
        try:
            from ..providers import cli_detect, ollama_seen

            cli_detect.forget_detection_memos()
            ollama_seen.forget()
        except Exception:  # noqa: BLE001
            pass
        sampler = getattr(d, "fleet_sampler", None)
        if sampler is not None:
            try:
                sampler.sample_once(force=True)
            except Exception:  # noqa: BLE001
                pass

    return _done


def manager_for(d: Any) -> PullManager:
    """The daemon's one :class:`PullManager` (made on first use, rows an
    earlier daemon left are settled then). BLOCKING the first time."""
    if not hasattr(d, "platform"):
        # A chat tool holds the platform; the daemon's deps hang off its
        # settings writer (``settings.records._d``).
        d = getattr(getattr(d, "config_writer", None), "d", None) or d
    platform = getattr(d, "platform", d)
    with _MANAGER_LOCK:
        mgr = getattr(platform, "_model_pulls", None)
        if isinstance(mgr, PullManager) and mgr.engine is getattr(platform, "engine", None):
            return mgr
        mgr = PullManager(platform.engine, getattr(platform, "event_bus", None), on_done=_after_download(d))
        try:
            mgr.settle()
        except Exception:  # noqa: BLE001
            log.warning("could not settle earlier downloads", exc_info=True)
        try:
            setattr(platform, "_model_pulls", mgr)
        except Exception:  # noqa: BLE001
            pass
        return mgr


_MANAGER_LOCK = threading.Lock()


def start_download(
    d: Any,
    node_id: str,
    model: str,
    *,
    loop: "asyncio.AbstractEventLoop | None" = None,
    started_by: str = "page",
) -> dict[str, Any]:
    """THE download call (the Download press, chat's ``model_download``, and
    the later phases that need a model: idea 16's search model, idea 37's
    voice). Reads the exact size first (``library.preview``), refuses in
    words before any request reaches the model server when the drive would
    keep less than a tenth free (D-43.2), then starts the one download this
    server may run. Returns ``{"download": row, "preview": what was shown}``.
    Raises :class:`PullRefused` (or ``library.LibraryProblem``) in words.
    BLOCKING."""
    preview = L.preview(d, node_id, model)
    if preview.get("refusal"):
        raise PullRefused(str(preview["refusal"]))
    target = L.target_for(d, node_id)
    mgr = manager_for(d)
    row = mgr.start(
        target,
        model=str(preview["model"]),
        name=str(preview.get("name") or preview["model"]),
        size=preview.get("size_bytes"),
        size_from=str(preview.get("size_from") or ""),
        headers=L.auth_headers(d, target),
        loop=loop,
        started_by=started_by,
    )
    return {"download": row, "preview": preview}
