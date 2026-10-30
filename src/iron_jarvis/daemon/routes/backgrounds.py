"""Animated backgrounds (v1.410.0): download on pick, list, delete, and serve
the saved file SEALED OFF.

* ``GET /backgrounds`` — every catalog background with its state on this PC
  (``absent`` / ``ready`` / ``outdated``) and size; ``total_bytes`` on disk.
* ``POST /backgrounds/{id}/download`` — fetch that one file (backgrounds/store);
  a file already saved and matching is never fetched again.
* ``DELETE /backgrounds/{id}`` — remove the saved file (``freed_bytes``).
* ``GET /backgrounds/{id}/view`` — the saved file, ONLY while it still hashes
  to the catalog, under :data:`VIEW_CSP`: no network of any kind (``default-src
  'none'``, so no fetch, socket, image, font or frame), inline script and style
  only, and the CSP ``sandbox`` directive so even a page that opened the
  address directly runs it in an opaque origin. It is token-EXEMPT
  (``auth._is_exempt``): an ``<iframe>`` cannot send the bearer header, and the
  token must never ride a URL a sandboxed page can read (``location.href``).
  The dashboard draws it in ``<iframe sandbox="allow-scripts">`` — never
  ``allow-same-origin`` — so it cannot read the dashboard's storage or call
  the daemon.

Every route is a sync ``def``: FastAPI runs it on its threadpool, so the disk
and the network stay off the event loop.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

from ...backgrounds import catalog, store

#: The policy the saved background is served under. Pinned by test.
VIEW_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data: blob:; connect-src 'none'; font-src 'none'; frame-src 'none'; "
    "worker-src 'none'; media-src 'none'; object-src 'none'; base-uri 'none'; "
    "form-action 'none'; sandbox allow-scripts"
)

VIEW_HEADERS = {
    "Content-Security-Policy": VIEW_CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "cross-origin",
}

#: The exact leaf ``auth._is_exempt`` lets through without the token.
VIEW_PREFIX = "/backgrounds/"
VIEW_SUFFIX = "/view"


def is_view_path(path: str) -> bool:
    if not (path.startswith(VIEW_PREFIX) and path.endswith(VIEW_SUFFIX)):
        return False
    middle = path[len(VIEW_PREFIX) : -len(VIEW_SUFFIX)]
    return catalog.get(middle) is not None


def _home(d: Any) -> Any:
    return d.platform.config.home


def _raise(exc: store.BackgroundError) -> None:
    raise HTTPException(status_code=exc.status, detail=exc.words) from exc


def register(app: FastAPI, d: Any) -> None:
    @app.get("/backgrounds")
    def backgrounds_list() -> dict[str, Any]:
        return store.list_state(_home(d))

    @app.post("/backgrounds/{bg_id}/download")
    def backgrounds_download(bg_id: str) -> dict[str, Any]:
        try:
            return store.download(_home(d), bg_id)
        except store.BackgroundError as exc:
            _raise(exc)
            raise  # unreachable

    @app.delete("/backgrounds/{bg_id}")
    def backgrounds_delete(bg_id: str) -> dict[str, Any]:
        try:
            return store.delete(_home(d), bg_id)
        except store.BackgroundError as exc:
            _raise(exc)
            raise

    @app.get("/backgrounds/{bg_id}/view")
    def backgrounds_view(bg_id: str) -> Response:
        body = store.read_verified(_home(d), bg_id)
        if body is None:
            # Missing, deleted, or no longer the catalog's file: the page falls
            # back to the plain theme.
            return Response(status_code=404, content=b"", headers=VIEW_HEADERS)
        return Response(content=body, media_type="text/html; charset=utf-8", headers=VIEW_HEADERS)
