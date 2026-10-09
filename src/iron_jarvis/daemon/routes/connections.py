"""Provider connection routes: keys, OAuth, models, rescan.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

import asyncio
import html as _html
import json
import time
from urllib.parse import urlsplit

from ...providers.reasoning import reasoning_levels

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Any

from ..schemas import ConnectionKeyBody, EndpointModelsBody, OAuthCompleteBody


def selectable_models(d) -> list[dict[str, Any]]:
    """Every {provider, model} the pickers offer, with availability flags.

    Extracted from GET /models (v1.128.0) so the templates requirements
    engine checks pinned models against EXACTLY what the pickers show —
    a narrower list would raise false “not connected” warnings."""
    from ...agents.dynamic import available_models

    # Hide the internal offline 'mock' model — not a selectable option in
    # the pickers (it stays the engine's silent fallback).
    models = [m for m in available_models() if m.get("provider") != "mock"]
    # Config-driven entries LIGHT UP once configured: the local model and
    # the custom endpoint appear in every picker (topbar switcher, New
    # Session, per-terminal AI) without hardcoding dead options.
    cfg = d.platform.config
    if cfg.ollama_base_url:
        models.append({"provider": "ollama", "model": cfg.ollama_model})
    if cfg.custom_base_url:
        models.append(
            {"provider": "custom", "model": cfg.custom_model or "default"}
        )
    # OpenCode CLI: ONLY the models that genuinely run on the user's own
    # hardware. Its hosted tier and any paid passthrough alias are excluded
    # upstream, so a picker can never offer a model this provider refuses.
    try:
        if d.platform.providers.available("opencode-cli"):
            for entry in d.platform.providers._opencode_allowed():  # noqa: SLF001
                models.append({"provider": "opencode-cli", "model": entry})
    except Exception:  # noqa: BLE001 — a picker never breaks on detection
        pass
    # LIVE DISCOVERY: ask each CONNECTED provider what it actually serves
    # (cached ~10 min). Discovered ids are ADDED; curated ids drop only when
    # the live list is non-empty (a failed probe — e.g. an OAuth token that
    # can't list models — degrades safely to the curated set).
    from ...providers.discovery import discover_models

    for prov in ("anthropic", "openai", "openrouter", "ollama", "custom"):
        try:
            if not d.platform.providers.available(prov):
                continue
            live = discover_models(
                prov,
                lambda p=prov: d.platform.providers._cred(p),  # noqa: SLF001
                base_url=(
                    cfg.ollama_base_url
                    if prov == "ollama"
                    else cfg.custom_base_url
                    if prov == "custom"
                    else ""
                ),
            )
            if not live:
                continue
            live_set = set(live)
            models = [
                m for m in models
                if m["provider"] != prov or m["model"] in live_set
            ]
            known = {m["model"] for m in models if m["provider"] == prov}
            for mid in live:
                if mid not in known:
                    models.append({"provider": prov, "model": mid})
        except Exception:  # noqa: BLE001 — discovery must never break the picker
            continue
    # CUSTOM ENDPOINTS (fleet nodes marked routable): each one is its own
    # provider ("fleet-<id>"), so EVERY endpoint the user added shows in
    # every picker — not just the single legacy custom slot. `name` carries
    # the user's label so pickers can render it instead of the raw id.
    # Per-endpoint live discovery reuses the same URL-keyed cache.
    try:
        for node in d.fleet.routable_nodes():
            prov = f"fleet-{node.id}"
            label = node.label or node.id
            ids: list[str] = []
            try:
                ids = discover_models(
                    prov,
                    lambda n=node: (
                        d.platform.secrets.get(n.api_key_name)
                        if n.api_key_name
                        else None
                    ),
                    base_url=node.base_url,
                )
            except Exception:  # noqa: BLE001 — discovery never breaks the picker
                ids = []
            if not ids:
                ids = [node.default_model or "default"]
            for mid in ids:
                models.append(
                    {
                        "provider": prov,
                        "model": mid,
                        "name": label,
                        "source": "endpoint",
                    }
                )
    except Exception:  # noqa: BLE001 — a fleet fault never breaks the picker
        pass
    # THE CLAUDE SUBSCRIPTION'S OWN PICKER (v1.300.0): claude-cli lists the
    # models the logged-in account offers (the CLI's `initialize` handshake,
    # cached; the pinned table when it fails) after its "subscription" row
    # (= the CLI's default, no --model). A keyless `anthropic` is SERVED by
    # claude-cli (manager.inherited_from), so it lists the very same rows
    # FIRST and keeps every curated id the catalog does not cover after them
    # (claude-sonnet-4-6 still runs through the CLI, and templates pinned to it
    # are checked against this list). Never blocks: claude_catalog() serves
    # the cache and refreshes behind it.
    try:
        models = _with_claude_catalog(d, models)
    except Exception:  # noqa: BLE001 — the picker never breaks on the catalog
        pass
    # Honesty flag: which entries the user can ACTUALLY run right now
    # (provider connected/configured). Pickers show available ones first
    # and grey/hide the rest — no more dead options that silently fail.
    for m in models:
        try:
            m["available"] = bool(d.platform.providers.available(m["provider"]))
        except Exception:  # noqa: BLE001
            m["available"] = False
    # Locally-installed CLI providers (e.g. the `grok` CLI) are DETECTED on
    # disk, not configured — so a CLI a user just installed surfaces in every
    # picker automatically, no restart. Detection is live + cheap and never
    # raises; each entry carries its own freshly-computed `available` flag.
    try:
        from ...providers.cli_detect import detect_cli_providers

        for dm in detect_cli_providers():
            models.append(
                {
                    "provider": dm.provider,
                    "model": dm.model,
                    "name": dm.name,
                    "available": bool(dm.available),
                    "source": "cli",
                }
            )
    except Exception:  # noqa: BLE001 — detection must never break the picker
        pass
    # WHERE DOES THIS RUN? (v1.148.0) Every picker had to guess, and none of
    # them did — so a local 14B and a metered frontier model looked identical
    # in the list. `kind` is the same classification /health serves
    # ("local" | "cli" | "api"), from the one definition in providers.local, and
    # `size_b` is the parameter count parsed out of the id so a local fleet can
    # be ordered smallest-first the way the router now orders it.
    from ...providers.local import is_local_provider, model_size_b

    for m in models:
        prov = str(m.get("provider") or "")
        m["kind"] = (
            "local"
            if is_local_provider(prov)
            else "cli"
            if prov in ("claude-cli", "codex-cli", "grok-cli")
            else "api"
        )
        m["size_b"] = model_size_b(str(m.get("model") or ""))
        # v1.230.0 (U5): a keyless API provider served through the logged-in
        # CLI is flat-rate — the picker says "included", not "metered". Same
        # answer /health and /connections give (manager.inherited_from).
        try:
            m["inherited_from"] = d.platform.providers.inherited_from(prov)
        except Exception:  # noqa: BLE001 — never breaks the picker
            m["inherited_from"] = None
        # v1.263.0: the reasoning levels this model offers (empty = none), so
        # the chat composer shows the control only where it does something.
        m["reasoning"] = list(reasoning_levels(prov, str(m.get("model") or "")))
    return models


def _claude_rows(provider: str, cat: dict) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for r in cat.get("models") or []:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        rows.append(
            {
                "provider": provider,
                "model": r["id"],
                "id": r["id"],
                "label": r.get("label") or r["id"],
                "description": r.get("description") or "",
                "native": r.get("native"),
                "context_window": r.get("context_window"),
                "usage_credits": bool(r.get("usage_credits")),
                "pinned": bool(r.get("pinned")),
                "cli_default": bool(r.get("default")),
            }
        )
    return rows


def _claude_bare(model: Any) -> str:
    """A Claude id lower-cased with any ``[1m]`` suffix stripped ("" = none)."""
    from ...providers.claude_models import _strip_1m

    return _strip_1m(str(model or "").strip()).lower()


def _claude_block(
    provider: str, cat: dict, curated: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The catalog rows FIRST (live picker order, their label/1M/credits),
    then every curated row the catalog does not already cover — deduplicated
    by canonical id, so a ``[1m]`` variant or a picker alias never doubles a
    model. Dropping the curated rows instead (the first v1.300.0 cut) took
    ``claude-sonnet-4-6`` & co. out of the list templates are checked against
    (``analyze_requirements``), though those ids run live through claude-cli."""
    from ...providers.claude_models import PINNED_ALIASES

    block = _claude_rows(provider, cat)
    by_id = {row["id"]: row for row in block}
    #: key -> the row that answers for it (a catalog row, or a curated row kept)
    owner: dict[str, dict[str, Any]] = {}
    for r in cat.get("models") or []:
        if isinstance(r, dict) and r.get("id") in by_id:
            # its id, and the picker's own value ("sonnet", "…[1m]") RAW — the
            # live picker, not the pinned alias table, decides what "sonnet" is.
            for key in (_claude_bare(r.get("id")), _claude_bare(r.get("value"))):
                if key:
                    owner.setdefault(key, by_id[r["id"]])
    for m in curated:
        bare = _claude_bare(m.get("model"))
        keys = [k for k in dict.fromkeys((bare, PINNED_ALIASES.get(bare, bare))) if k]
        if not keys:
            continue
        cover = next((owner[k] for k in keys if k in owner), None)
        if cover is not None:
            # Folded into the row that covers it, but still ANSWERED for: a
            # template pinned to the curated id (``claude-haiku-4-5``, covered by
            # the dated catalog row) must not read "isn't connected"
            # (``templates.analyze_requirements`` accepts ``aliases``).
            name = str(m.get("model") or "")
            if name and name != cover.get("model") and name not in cover.setdefault("aliases", []):
                cover["aliases"].append(name)
            continue
        for k in keys:
            owner[k] = m
        block.append(m)
    return block


def _with_claude_catalog(d, models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    providers = d.platform.providers
    catalog_fn = getattr(providers, "claude_catalog", None)
    if not callable(catalog_fn):
        return models
    inherited = providers.inherited_from("anthropic") == "claude-cli"
    if not (providers.available("claude-cli") or inherited):
        return models  # nothing to serve them: keep today's rows untouched
    cat = catalog_fn()
    if not _claude_rows("claude-cli", cat):
        return models
    served = ["claude-cli"] + (["anthropic"] if inherited else [])
    curated: dict[str, list[dict[str, Any]]] = {p: [] for p in served}
    out: list[dict[str, Any]] = []
    slot: dict[str, int] = {}  # where each provider's first curated row stood
    for m in models:
        prov = m.get("provider")
        keep_in_place = prov == "claude-cli" and m.get("model") in ("subscription", "default")
        if prov in curated and not keep_in_place:
            curated[prov].append(m)
            slot.setdefault(prov, len(out))
            continue
        out.append(m)
    # claude-cli's block goes after its subscription row (the CLI's default);
    # anthropic's where its curated rows stood (else at the end).
    at = {
        "claude-cli": next(
            (i + 1 for i, m in enumerate(out) if m.get("provider") == "claude-cli"),
            slot.get("claude-cli", len(out)),
        )
    }
    if inherited:
        at["anthropic"] = slot.get("anthropic", len(out))
    # the LATER slot first, so the earlier index is still right afterwards
    for prov in sorted(at, key=lambda p: at[p], reverse=True):
        out[at[prov]:at[prov]] = _claude_block(prov, cat, curated[prov])
    return out


# --------------------------------------------------------------------------- #
#  "Fetch available models" for a custom endpoint (v1.328.0)
# --------------------------------------------------------------------------- #

#: The whole probe's bound, every path it tries included. A model list is one
#: small GET; a server that needs longer than this is not one to wait on.
_ENDPOINT_PROBE_TIMEOUT_S = 5.0
#: Most ids kept from one listing (a gateway can list thousands).
_ENDPOINT_PROBE_MAX_MODELS = 1000
#: Protocols a SAVED endpoint can actually speak. A saved endpoint is a fleet
#: node, and a fleet node is an OpenAI-compatible adapter (fleet/adapter.py),
#: so listing an Anthropic-style server would offer models it cannot call.
_ENDPOINT_PROTOCOLS = ("openai",)


class EndpointModelsProbeBody(BaseModel):
    """``POST /connections/endpoints/models``. Every field has a default and is
    checked in the handler: a pydantic 422 echoes the offending input, and for
    a missing field that input is the whole body, key included."""

    base_url: str = ""
    api_key: str = ""
    protocol: str = "openai"


def _probe_words(kind: str) -> str:
    """The one sentence the form shows for a failed probe. Never the URL or
    the key, never an exception's text (it can carry either)."""
    secs = max(1, round(_ENDPOINT_PROBE_TIMEOUT_S))
    return {
        "unreachable": "Could not reach that address. Check the URL and that the server is running.",
        "timeout": f"The server did not answer within {secs} second{'' if secs == 1 else 's'}.",
        "refused_key": "The server refused this key. Check the key and try again.",
        "needs_key": "The server asks for a key. Paste it in the API key box and try again.",
        "not_model_server": (
            "That address answered, but it does not look like a model server. "
            "Check the URL (it often ends in /v1)."
        ),
        "no_models": "The server answered but lists no models. Type the model id yourself.",
        "server_error": (
            "The server answered with an error. Try again in a moment, "
            "or type the model id yourself."
        ),
        "bad_address": "That is not a web address this app can open.",
    }.get(kind, "Could not ask that server for its models. Type the model id yourself.")


def _probe_fail(kind: str) -> dict[str, Any]:
    return {"models": [], "error": _probe_words(kind), "reason": kind}


def _model_list_urls(base_url: str) -> list[str]:
    """Where a server keeps its model list, most likely first: the
    OpenAI-compatible ``<base>/v1/models`` (or ``<base>/models`` when the base
    already ends in /v1), then ``<base>/models``, then Ollama's own
    ``/api/tags`` for a host that predates its /v1 shim. A pasted chat or
    models URL is cut back to its base first."""
    u = base_url.strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions", "/models"):
        if u.endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
            break
    if u.endswith("/v1"):
        host = u[: -len("/v1")].rstrip("/")
        urls = [f"{u}/models"]
    else:
        host = u
        urls = [f"{u}/v1/models", f"{u}/models"]
    urls.append(f"{host}/api/tags")
    return list(dict.fromkeys(urls))


def _model_ids(payload: Any) -> list[str] | None:
    """The model ids in one listing, or None when *payload* is not a listing.
    OpenAI shape ``{"data": [{"id"}]}``, Ollama ``{"models": [{"name"}]}``,
    or a bare list of ids / rows."""
    rows: Any = None
    if isinstance(payload, dict):
        for k in ("data", "models"):
            if isinstance(payload.get(k), list):
                rows = payload[k]
                break
    elif isinstance(payload, list):
        rows = payload
    if rows is None:
        return None
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        mid: Any = row
        if isinstance(row, dict):
            mid = next(
                (row[k] for k in ("id", "name", "model") if isinstance(row.get(k), str) and row[k].strip()),
                "",
            )
        if not isinstance(mid, str):
            continue
        mid = mid.strip()
        if not mid or len(mid) > 256 or any(ord(c) < 32 for c in mid) or mid in seen:
            continue
        seen.add(mid)
        out.append(mid)
        if len(out) >= _ENDPOINT_PROBE_MAX_MODELS:
            break
    if rows and not out:
        return None  # a list, but of nothing that names a model
    return out


def _probe_endpoint_models(base_url: str, key: str, *, timeout_s: float) -> dict[str, Any]:
    """BLOCKING: ask one server for its model list (the route runs this in a
    worker thread). Stops at the first decisive answer: unreachable, timed
    out, or a refused key end the probe; a 404 / not-JSON / not-a-listing
    moves on to the next likely path. Redirects are not followed, so the key
    never travels to a host the user did not type."""
    import httpx

    deadline = time.monotonic() + timeout_s
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    saw: set[str] = set()
    with httpx.Client(follow_redirects=False) as client:
        for url in _model_list_urls(base_url):
            left = deadline - time.monotonic()
            if left <= 0:
                return _probe_fail("timeout")
            try:
                resp = client.get(url, headers=headers, timeout=left)
            except httpx.TimeoutException:
                return _probe_fail("timeout")
            except httpx.ConnectError:
                return _probe_fail("unreachable")
            except (httpx.InvalidURL, httpx.UnsupportedProtocol):
                return _probe_fail("bad_address")
            except httpx.HTTPError:
                saw.add("not_model_server")  # dropped mid-answer, odd protocol
                continue
            if resp.status_code in (401, 403):
                return _probe_fail("refused_key" if key else "needs_key")
            if resp.status_code >= 500:
                saw.add("server_error")
                continue
            if resp.status_code != 200:
                saw.add("not_model_server")
                continue
            try:
                payload = resp.json()
            except ValueError:
                saw.add("not_model_server")
                continue
            ids = _model_ids(payload)
            if ids is None:
                saw.add("not_model_server")
            elif not ids:
                saw.add("no_models")
            else:
                return {"models": ids, "error": None, "reason": None}
    for kind in ("no_models", "server_error", "not_model_server"):
        if kind in saw:
            return _probe_fail(kind)
    return _probe_fail("not_model_server")


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""
    @app.get("/providers")
    def providers() -> dict[str, Any]:
        return {"providers": d._visible_providers()}

    @app.get("/connections")
    def connections() -> dict[str, Any]:
        # Hide the internal offline 'mock' provider — it's an engine fallback,
        # not something the user connects/manages.
        return {
            "connections": [
                c for c in d.platform.connections.status() if c.get("provider") != "mock"
            ]
        }

    @app.post("/connections/{provider}/default")
    def set_default_provider(provider: str) -> dict[str, Any]:
        """Make a CONNECTED provider the active default (+ a sensible model).

        One-click from the Connections page so a user with several accounts
        chooses which one runs their sessions — instead of the confusing
        auto-promote (which just picked whichever connected first)."""
        if d.platform.connections.get_spec(provider) is None:
            raise HTTPException(status_code=404, detail="unknown provider")
        if not d.platform.providers.available(provider):
            raise HTTPException(
                status_code=400, detail=f"connect {provider} before making it the default"
            )
        cfg = d.platform.config
        # Redesign S2: the one writer (ledger + Undo).
        from .settings import config_writer

        config_writer(d).apply(
            {
                "default_provider": provider,
                "default_model": d._PROMOTE_DEFAULT_MODEL.get(provider, cfg.default_model),
            },
            actor="connections",
        )
        return {"default_provider": provider, "default_model": cfg.default_model}

    @app.post("/connections/{provider}/key")
    def connect_key(provider: str, body: ConnectionKeyBody) -> dict[str, Any]:
        try:
            rec = d.platform.connections.set_api_key(provider, body.key)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        promoted = d._maybe_autopromote_default(rec.provider)
        return {"provider": rec.provider, "status": rec.status, "promoted_default": promoted}

    @app.post("/connections/{provider}/test")
    async def connect_test(provider: str) -> dict[str, Any]:
        # test() may do a real network probe (when wired) → run it off the event
        # loop so a slow provider can't stall the daemon.
        return await asyncio.to_thread(d.platform.connections.test, provider)

    @app.delete("/connections/{provider}")
    def connect_disconnect(provider: str) -> dict[str, Any]:
        d.platform.connections.disconnect(provider)
        return {"provider": provider, "status": "disconnected"}

    @app.get("/oauth/{provider}/start")
    def oauth_start(provider: str) -> dict[str, Any]:
        try:
            out = d.platform.connections.start_oauth(provider)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        # RFC 8252 loopback: embedded public clients registered against a FIXED
        # localhost port (OpenAI's :1455) need a one-shot listener to catch the
        # redirect — it completes the flow server-side, then shuts down.
        loop = d.platform.connections.loopback_redirect(provider)
        if loop:
            from ...connections.loopback import OAuthLoopbackServer

            port, cb_path = loop
            old = d._loopback_servers.pop(provider, None)
            if old:
                old.stop()

            def _complete(code: str, state: str, _p: str = provider) -> None:
                d.platform.connections.complete_oauth(_p, code=code, state=state)
                d._maybe_autopromote_default(_p)

            srv = OAuthLoopbackServer(
                port=port, path=cb_path, provider=provider, on_code=_complete
            )
            try:
                srv.start()
            except OSError:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"port {port} is busy (another app — e.g. Codex CLI — is "
                        "using it). Close it and try again."
                    ),
                )
            d._loopback_servers[provider] = srv
        return out

    @app.post("/oauth/{provider}/complete")
    def oauth_complete(provider: str, body: OAuthCompleteBody) -> dict[str, Any]:
        """Manual-code OAuth completion (e.g. Anthropic's paste-the-code flow).

        The provider showed the user an authorization code (``code#state``);
        the Connections page posts it here instead of a browser redirect ever
        reaching the daemon.
        """
        try:
            rec = d.platform.connections.complete_oauth(
                provider, code=body.code, state=body.state
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        promoted = d._maybe_autopromote_default(provider)
        return {
            "provider": rec.provider,
            "status": rec.status,
            "promoted_default": promoted,
        }

    @app.get("/oauth/{provider}/callback")
    def oauth_callback(provider: str, code: str = "", state: str = "") -> HTMLResponse:
        try:
            d.platform.connections.complete_oauth(provider, code=code, state=state)
            d._maybe_autopromote_default(provider)
            msg, ok = f"Connected to {provider}. You can close this window.", True
        except Exception as exc:  # noqa: BLE001
            msg, ok = f"Connection failed: {exc}", False
        color = "#22d3ee" if ok else "#fb7185"
        # SECURITY: this route is auth-exempt and `provider`/exception text are
        # attacker-influenced — a reflected-XSS sink. Escape every interpolated
        # value and build the postMessage payload as a JS-safe string literal.
        safe_msg = _html.escape(msg)
        payload = json.dumps(
            {"type": "ironjarvis-oauth", "provider": provider, "ok": ok}
        ).replace("<", "\\u003c")
        html = (
            "<!doctype html><meta charset=utf-8><title>Iron Jarvis</title>"
            "<body style='background:#0a0a0f;color:#e5e7eb;font-family:system-ui;"
            "display:grid;place-items:center;height:100vh;margin:0'>"
            f"<div style='text-align:center'><div style='font-size:42px;color:{color}'>"
            f"{'✓' if ok else '✕'}</div><p>{safe_msg}</p></div>"
            "<script>try{window.opener&&window.opener.postMessage("
            f"JSON.parse({json.dumps(payload)}),'*');"
            "setTimeout(()=>window.close(),1200)}catch(e){}</script></body>"
        )
        return HTMLResponse(
            html,
            headers={
                "Content-Security-Policy": (
                    "default-src 'none'; script-src 'unsafe-inline'; "
                    "style-src 'unsafe-inline'"
                ),
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/models")
    def list_models() -> dict[str, Any]:
        return {"models": selectable_models(d)}

    @app.post("/providers/rescan")
    def rescan_cli_providers() -> dict[str, Any]:
        """Re-scan for locally-installed CLI inference providers (Grok, etc.).

        Idempotent and on-demand: a CLI a user installs mid-session shows up the
        next time any picker fetches ``/models``, but this lets the dashboard
        force an immediate refresh (and is what the periodic boot loop calls).
        """
        from ...providers.cli_detect import detect_cli_providers
        from ...providers.manager import invalidate_cli_presence

        # v1.311.0 review: the PATH lookup is memoised now (cli-binary-probe-
        # uncached-on-loop), so a CLI installed a moment ago would read "not
        # installed" until its entry expired. Re-detect means LOOK AGAIN: drop
        # the memo first (a sync route on the threadpool, so the scan is fine).
        invalidate_cli_presence()
        detected = detect_cli_providers()
        rows = [dm.as_dict() for dm in detected]
        # v1.234.0: the subscription CLIs are re-PROBED for sign-in here
        # (blocking is fine — this is a sync route on the threadpool), so
        # "Re-detect" after `/login` turns the row green without a restart.
        try:
            from ...providers.cli_auth import CLI_BINARIES, SIGN_IN_FIX, DEFAULT_PROBE

            for prov, binary in CLI_BINARIES.items():
                if not d.platform.providers._cli_binary_present(binary):  # noqa: SLF001
                    continue
                st = DEFAULT_PROBE.refresh(binary)
                usable = st.signed_in is not False
                rows.append(
                    {
                        "provider": prov,
                        "model": "subscription",
                        "name": "Claude Code CLI" if binary == "claude" else "Codex CLI",
                        "available": usable,
                        "source": "cli",
                        "base_url": None,
                        "exec_path": None,
                        "context_window": None,
                        "detail": "" if usable else f"{st.detail}. {SIGN_IN_FIX[binary]}",
                    }
                )
        except Exception:  # noqa: BLE001 — a probe fault never breaks the rescan
            pass
        return {"detected": rows}

    @app.post("/providers/endpoint-models")
    def endpoint_models(body: EndpointModelsBody) -> dict[str, Any]:
        """List the models a user-entered OpenAI-compatible endpoint actually
        serves (``/v1/models``, falling back to Ollama's native ``/api/tags``)
        — so the setup form offers a picker instead of a blank model-id field.
        Probe-only: nothing is saved. Always 200; a failed probe returns an
        honest ``error`` (the form shows it and keeps manual entry)."""
        from ...providers.discovery import list_endpoint_models

        try:
            models = list_endpoint_models(body.base_url, body.api_key.strip())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:  # noqa: BLE001 — unreachable/odd server, be honest
            return {"models": [], "error": f"{type(exc).__name__}: {exc}"[:300]}
        return {"models": models}

    @app.post("/connections/endpoints/models")
    async def endpoint_models_probe(body: EndpointModelsProbeBody) -> dict[str, Any]:
        """The custom-endpoint form's "Fetch available models" (v1.328.0).

        Asks the server the user typed for its own model list, bounded by
        ``_ENDPOINT_PROBE_TIMEOUT_S`` and run in a worker thread (a dead host
        must never park the event loop). Probe-only: nothing is saved. The key
        rides one ``Authorization`` header to that server and is never logged,
        stored or returned. Always 200 for a server's answer, with ``error``
        in plain words and ``reason`` (unreachable / timeout / refused_key /
        needs_key / not_model_server / no_models / server_error /
        bad_address); 400 only for input the form should not have sent."""
        url = (body.base_url or "").strip()
        try:
            parts = urlsplit(url)
            host = parts.hostname
        except ValueError:
            parts, host = None, None
        if parts is None or parts.scheme.lower() not in ("http", "https") or not host:
            raise HTTPException(
                status_code=400,
                detail="Enter the endpoint address first, starting with http:// or https://.",
            )
        protocol = (body.protocol or "openai").strip().lower()
        if protocol not in _ENDPOINT_PROTOCOLS:
            raise HTTPException(
                status_code=400,
                detail="Only OpenAI-compatible endpoints can be added here.",
            )
        key = (body.api_key or "").strip()
        if len(key) > 8192 or any(not 32 <= ord(c) < 127 for c in key):
            raise HTTPException(
                status_code=400,
                detail="That key has characters a web request cannot carry. Paste it again.",
            )
        timeout_s = _ENDPOINT_PROBE_TIMEOUT_S
        try:
            # The thread's own per-request timeouts already sum under the
            # bound; this outer one is the backstop, and only ITS expiry earns
            # the timeout words.
            async with asyncio.timeout(timeout_s + 1.0) as cm:
                return await asyncio.to_thread(
                    _probe_endpoint_models, url, key, timeout_s=timeout_s
                )
        except TimeoutError:
            if cm.expired():
                return _probe_fail("timeout")
            return _probe_fail("failed")
        except Exception:  # noqa: BLE001 — a probe fault is data, never a 500
            return _probe_fail("failed")
