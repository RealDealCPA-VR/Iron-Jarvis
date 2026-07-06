"""Provider connection routes: keys, OAuth, models, rescan.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

import asyncio
import html as _html
import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from typing import Any

from ..schemas import ConnectionKeyBody, EndpointModelsBody, OAuthCompleteBody


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
        cfg.default_provider = provider
        cfg.default_model = d._PROMOTE_DEFAULT_MODEL.get(provider, cfg.default_model)
        d._persist_config(["default_provider", "default_model"])
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
        return {"models": models}

    @app.post("/providers/rescan")
    def rescan_cli_providers() -> dict[str, Any]:
        """Re-scan for locally-installed CLI inference providers (Grok, etc.).

        Idempotent and on-demand: a CLI a user installs mid-session shows up the
        next time any picker fetches ``/models``, but this lets the dashboard
        force an immediate refresh (and is what the periodic boot loop calls).
        """
        from ...providers.cli_detect import detect_cli_providers

        detected = detect_cli_providers()
        return {"detected": [dm.as_dict() for dm in detected]}

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
