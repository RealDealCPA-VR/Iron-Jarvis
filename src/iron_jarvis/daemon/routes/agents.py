"""Agent/tool routes: registry, skills, custom tools, MCP, dynamic agents.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from ..app import _session_view
from ..schemas import (
    AGENT_APPROVAL_MODES,
    AgentCreate,
    AgentPatch,
    CustomToolCreate,
    McpImportApplyBody,
    McpImportPreviewBody,
    McpServerBody,
    McpServerPatchBody,
    McpSettingsPatch,
    McpSuggestBody,
    PauseBody,
    RemoteAgentCreate,
    RemoteAgentPatch,
    RemoteAgentRun,
    RemoteInboundEnable,
    SkillApplyBody,
    SkillCreate,
    SpawnBody,
    ToolGenerateBody,
)

# Importing this registers the RemoteAgentRecord table on the shared metadata.
from ...agents.remote import RemoteAgentRegistry
from ..chat_append import append_to_chat_thread

# The employee ledger (v1.295.0): what a custom agent spent this month against
# its allowance, and the one sentence a paused/exhausted agent answers with.
from ...agents.allowance import allowance_state, month_spend, refusal_for

# THE FOLDER (v1.297.0): the one slug rule, lifted out of this module so the
# agent's folder, portrait and face key cannot disagree.
from ...agents.files import agent_slug
from ...core.events import EventType
from ...core.models import AgentType
from ...core.logging import get_logger

log = get_logger("daemon.routes.agents")

#: A day off with no stated reason still has one on the record (v1.295.0):
#: this is what every refused spawn says back until the agent is resumed.
PAUSE_DEFAULT_REASON = "paused by the user"

#: How the allowance AUTO-pause words its reason — the literal
#: ``agents.allowance.after_run`` writes (``f"monthly allowance used up (…)"``).
#: A user's own pause never starts like this, which is how the PATCH below
#: tells "lift it because the allowance was raised" from "the user said no".
#: Pinned against that source line in tests/test_agent_employee_routes_v1295.py.
AUTO_PAUSE_PREFIX = "monthly allowance used up"

# Face overrides (v1.180.0). Imported as a MODULE, never as loose names: the
# route looks every helper up on `faces` at call time so a test can monkeypatch
# one (the `_open_native` pattern from routes/documents, and how the
# event-loop-offload test proves the file IO left the loop).
from ...agents import faces


class AgentFilesInstructions(BaseModel):
    """``PUT /agents/{name}/files/instructions`` — the new AGENTS.md text and
    an optional one-line reason kept on the revision of the previous text."""

    text: str
    reason: str = ""


class AgentFilesNotes(BaseModel):
    """``PUT /agents/{name}/files/notes`` — the whole notebook, replaced."""

    text: str


def _open_folder(path: str) -> None:
    """Show *path* in the OS file manager (Explorer / Finder / xdg-open).
    Module-level so tests monkeypatch it instead of really opening a window —
    the ``routes/documents._open_native`` pattern."""
    import subprocess
    import sys

    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 — explicit, user-initiated open
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# --- agent identity: portraits + roster activity (v1.171.0) -----------------
# Storage is BY NAME under <home>/avatars/<slug>.png — the file's existence IS
# the record (no schema change). Module-level pure helpers so tests can drive
# them directly, and so `_generate_avatar_bytes` is monkeypatchable (looked up
# on the module at call time, the `_open_native` pattern from routes/documents).

#: Decoded upload cap — a portrait, not a photo archive.
_AVATAR_MAX_BYTES = 2 * 1024 * 1024
#: Stored portraits are normalized to ≤512px PNG.
_AVATAR_MAX_DIM = 512
#: last_message preview cap (frozen contract: ≤140 chars, plain text).
_PREVIEW_CHARS = 140

_NO_IMAGE_MODEL = (
    "no image model is connected — add a 'pixio' secret (Secrets page) or set "
    "PIXIO_API_KEY to enable portrait generation"
)

_AVATAR_PROMPT = (
    "A friendly square profile avatar portrait for an AI assistant agent "
    "named {name}. {purpose}Minimal flat vector style, bold simple shapes, "
    "dark studio background, centered head-and-shoulders composition, "
    "no text, no watermark."
)

def _avatar_slug(name: str) -> str:
    """One path-safe filename segment that never EATS the identity (v1.153.2).

    v1.297.0: the rule LIVES in ``agents/files.agent_slug`` now — the agent's
    folder, portrait and face key are built from that ONE sanitizer, so the
    three can never disagree. This name is kept for the portrait/face paths
    below and the tests that import it. See ``agent_slug`` for the contract
    (clean lowercase passes verbatim; any touched or case-folded name carries
    a digest of the original; Windows device names get it prefixed).
    """
    return agent_slug(name)


#: Non-whitespace C0/C1 controls (ESC/BEL/NUL survive a whitespace collapse)
#: plus the Unicode bidi controls (U+202E RLO can visually REVERSE a preview,
#: spoofing what the agent appears to have said). Stripped, never rendered.
_PREVIEW_UNSAFE = re.compile(
    "[\x00-\x08\x0b-\x1f\x7f-\x9f"  # C0 (sans \t\n, already collapsed) + DEL + C1
    "\u200e\u200f"  # LRM / RLM
    "\u202a-\u202e"  # LRE / RLE / PDF / LRO / RLO
    "\u2066-\u2069]"  # LRI / RLI / FSI / PDI
)


def _preview_text(text: Any) -> str:
    """Injection-safe one-line preview: whitespace runs (incl. newlines /
    control separators) collapse to single spaces, remaining control and
    bidi-override characters are STRIPPED (a whitespace collapse alone lets
    ESC/BEL/NUL and U+202E through verbatim — measured), clipped to ≤140
    chars. A stored message must never escape into layout or terminal/bidi
    trickery."""
    flat = " ".join(str(text or "").split())
    flat = " ".join(_PREVIEW_UNSAFE.sub("", flat).split())
    if len(flat) <= _PREVIEW_CHARS:
        return flat
    return flat[: _PREVIEW_CHARS - 1].rstrip() + "…"


def _thread_activity(records: Any) -> dict[str, tuple[str, str]]:
    """Newest agent-thread entry per participant key → {key: (iso_at, preview)}.

    ONE pass over already-fetched thread rows (the caller does a single
    ``AgentThreads.list()`` — never N+1 queries). User turns are skipped:
    the roster asks what the AGENT last said, not what was said to it.
    Defensive per-row: one corrupt blob costs that thread's contribution only.
    """
    import json as _json
    from datetime import datetime

    newest: dict[str, tuple[datetime, str, str]] = {}
    for rec in records or []:
        try:
            msgs = _json.loads(getattr(rec, "messages_json", "") or "[]")
        except (TypeError, ValueError):
            continue
        if not isinstance(msgs, list):
            continue
        for m in msgs:
            if not isinstance(m, dict):
                continue
            who = str(m.get("who") or "")
            if not who or who == "user":
                continue
            raw_at = str(m.get("at") or "")
            try:
                at = datetime.fromisoformat(raw_at)
            except ValueError:
                continue
            prev = newest.get(who)
            try:
                is_newer = prev is None or at > prev[0]
            except TypeError:  # naive vs aware timestamps in one store
                is_newer = False
            if is_newer:
                text = m.get("content") or m.get("error") or ""
                newest[who] = (at, raw_at, _preview_text(text))
    return {key: (raw_at, preview) for key, (_at, raw_at, preview) in newest.items()}


def _sniff_image(data: bytes) -> str | None:
    """PNG/JPEG/WebP magic-byte sniff — the CONTENT decides, never the name."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _normalize_avatar_png(data: bytes) -> bytes:
    """Decode + normalize to a ≤512px PNG. Raises on undecodable bytes —
    the caller turns that into an honest 415, never stores garbage."""
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(data)) as im:
        im = im.convert("RGBA")
        im.thumbnail((_AVATAR_MAX_DIM, _AVATAR_MAX_DIM))
        out = BytesIO()
        im.save(out, format="PNG")
    return out.getvalue()


def _generate_avatar_bytes(key: str, prompt: str, *, timeout_seconds: int = 180) -> bytes:
    """Generate ONE portrait through the platform's EXISTING image path —
    the same Pixio API :mod:`iron_jarvis.tools.pixio` speaks (model ids are
    DISCOVERED from ``/api/v1/models``, never invented). SYNC on purpose: the
    avatar route is a sync handler, so this runs in FastAPI's threadpool and
    never blocks the event loop. Raises ``RuntimeError`` with an honest
    message on ANY failure — there is deliberately no placeholder image.
    """
    from ...tools.pixio import _BASE_URL, _default_http, _detail, _http_error, _output_url

    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}

    def _api(method: str, path: str, body: dict | None = None) -> Any:
        resp = _default_http(method, _BASE_URL + path, headers, body)
        status = int(getattr(resp, "status_code", 0) or 0)
        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001 — non-JSON error bodies happen
            payload = {}
        err = _http_error(status, payload if isinstance(payload, dict) else {})
        if err:
            raise RuntimeError(err)
        return payload

    models = _api("GET", "/api/v1/models")
    if isinstance(models, dict):
        models = models.get("models") or models.get("data") or []
    image_models: list[str] = []
    for m in models or []:
        if not isinstance(m, dict):
            continue
        kind = str(m.get("type") or m.get("category") or "").lower()
        model_id = str(m.get("id") or m.get("modelId") or "")
        if model_id and "image" in kind:
            image_models.append(model_id)
    if not image_models:
        raise RuntimeError("no image-capable Pixio model is visible to this account")
    chosen = next(
        (m for m in image_models if "nano-banana" in m),
        next((m for m in image_models if "flux" in m), image_models[0]),
    )

    body = _api(
        "POST",
        "/api/v1/generate",
        {"providerId": "pixio", "modelId": chosen, "params": {"prompt": prompt}},
    )
    body = body if isinstance(body, dict) else {}
    generation_id = str(body.get("contentId") or body.get("id") or "")
    if not generation_id:
        raise RuntimeError("Pixio generate returned no generation id")

    deadline = time.monotonic() + max(1, timeout_seconds)
    while True:
        body = _api("GET", f"/api/v1/generations/{generation_id}")
        body = body if isinstance(body, dict) else {}
        state = str(body.get("status") or "").lower()
        if state == "succeeded":
            url = _output_url(body)
            if not url:
                raise RuntimeError("generation succeeded but returned no output url")
            # Public CDN link — never send the bearer key to a third-party host.
            resp = _default_http("GET", url, {}, None)
            status = int(getattr(resp, "status_code", 0) or 0)
            if not 200 <= status < 300:
                raise RuntimeError(f"portrait download failed ({status})")
            return getattr(resp, "content", b"") or b""
        if state == "failed":
            raise RuntimeError(
                f"generation failed: {_detail(body) or 'no error detail from Pixio'}"
            )
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"generation still '{state or 'pending'}' after {timeout_seconds}s"
            )
        time.sleep(5.0)


def add_mcp_pack(d, body: McpServerBody) -> dict[str, Any]:
    """Register an external MCP server (persisted; loaded live best-effort,
    guaranteed on the next restart).

    The ONE add path (v1.343.0): ``POST /mcp/servers`` delegates here, and
    the import/apply door adds each chosen pack through the same function
    so the secret split, the live load and the add note cannot drift.
    Raises ``HTTPException`` exactly as the route answers.

    v1.355.0 (G44): the body lives in :func:`iron_jarvis.mcp.pack_door.
    add_pack` so the Directory's Connect and the chat's ``app_connect`` go
    through it too (a service must not import a route module); this wrapper
    keeps the route's name, signature and answer byte for byte.
    """
    from ...mcp.pack_door import PackDoorError, add_pack

    try:
        return add_pack(d.platform, body, persist=d._persist_config)
    except PackDoorError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


def remove_mcp_pack(d, name: str) -> dict[str, Any]:
    """Remove a configured pack: its config row, its vault rows
    (``mcp.<name>.*``), its manifest, its live tools and their client.

    The ONE remove path (v1.343.0): ``DELETE /mcp/servers/{name}`` delegates
    here (its answer is unchanged), and the import/apply door's
    ``replace: true`` removes the old pack through the same function before
    re-adding. Raises ``HTTPException(404)`` for an unknown name.

    v1.355.0 (G44): the body is :func:`iron_jarvis.mcp.pack_door.
    remove_pack` — the Directory's Disconnect and the chat's Undo of
    ``app_connect`` remove through it too, so every one of them drops the
    manifest, the live client and the load record (``close_tools_clients``
    and ``forget_load_record`` run there).
    """
    from ...mcp.pack_door import PackDoorError, remove_pack

    try:
        return remove_pack(d.platform, name, persist=d._persist_config)
    except PackDoorError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


def launcher_ready(command: str) -> bool:
    """Would the launcher find this stdio command right now? The catalog's
    ``runtime_ready`` (v1.316.0) and the import preview's (v1.343.0) share
    this ONE answer: the launcher's OWN resolution (``resolve_launcher``:
    real PATH, then the per-user bin dirs a GUI-launched daemon misses) —
    a which-only check would disagree with what actually starts. Any
    fault is False, never a raise."""
    from ...mcp.tools import resolve_launcher

    try:
        resolve_launcher(command)
        return True
    except Exception:  # noqa: BLE001 — not found / unreadable dir = not ready
        return False


#: What a missing launcher is called on a preview row (plain words).
_LAUNCHER_WORDS: dict[str, str] = {
    "npx": "npx (Node.js)",
    "node": "node (Node.js)",
    "npm": "npm (Node.js)",
    "bunx": "bunx (Bun)",
    "uvx": "uvx (uv)",
    "uv": "uv",
    "pipx": "pipx",
    "python": "python",
    "python3": "python3",
    "docker": "docker (Docker Desktop)",
}


def launcher_note(command: str, ready: bool) -> str:
    """``""`` when the launcher resolves; else "needs npx (Node.js)"."""
    if ready:
        return ""
    cmd = str(command or "").strip()
    if not cmd:
        return "needs a command"
    base = cmd.lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    for ext in (".cmd", ".exe"):
        base = base.removesuffix(ext)
    return f"needs {_LAUNCHER_WORDS.get(base, cmd)}"


def _fold_name(name: Any) -> str:
    """A name compared without case, spaces, ``-`` or ``_`` (v1.354.0, G50):
    ``brave_search``, ``brave-search`` and ``Brave Search`` are one server,
    and the catalog's ``sequential_thinking`` is the Tools catalog's
    ``sequentialthinking``."""
    return re.sub(r"[\s_-]+", "", str(name or "").strip().lower())


def known_connections(d) -> dict[str, str]:
    """What is ALREADY connected here, by folded name → how (v1.354.0, G50):
    ``"pack"`` (a pack in ``config.mcp_servers``), ``"app"`` (a Directory
    connection that is live — an OAuth drive, a key, a catalog pack under
    its catalog id) or ``"memory"`` (a memory source, e.g. a brain served by
    a tool). Read through ``connectors.service.list_connectors`` — the
    Directory's own list — ONCE per answer; a list that cannot be read
    leaves the packs alone answering, never an error."""
    known: dict[str, str] = {}
    for s in getattr(d.platform.config, "mcp_servers", None) or []:
        if isinstance(s, dict) and _fold_name(s.get("name")):
            known.setdefault(_fold_name(s.get("name")), "pack")
    try:
        from ...connectors.service import list_connectors

        rows = list_connectors(d.platform)
    except Exception:  # noqa: BLE001 — the preview never fails on the gallery
        rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        via = str(row.get("connect_via") or "")
        if via == "memory":
            for key in (row.get("id"), row.get("name")):
                if _fold_name(key):
                    known.setdefault(_fold_name(key), "memory")
        elif via in ("mcp", "oauth", "api_key") and row.get("connected") is True:
            for key in (row.get("id"), row.get("catalog_id")):
                if _fold_name(key):
                    known.setdefault(_fold_name(key), "app")
    return known


_PKG_PREFIX_RX = re.compile(r"^(?:npm|pip|pypi):", re.IGNORECASE)
#: A description that names the mechanism is not a description (the G42
#: rule: no card says MCP).
_MECHANISM_WORD_RX = re.compile(r"\bMCP\b", re.IGNORECASE)


def _package_key(arg: Any) -> str:
    """A package reference without its version or prefix, lowercased:
    ``@scope/pkg@1.2`` → ``@scope/pkg``, ``pkg@latest`` → ``pkg``,
    ``pip:pkg==1.0`` → ``pkg``. ``""`` for a flag or a ``<placeholder>``."""
    text = _PKG_PREFIX_RX.sub("", str(arg or "").strip()).lower()
    if not text or text.startswith(("-", "<")):
        return ""
    if text.startswith("@"):
        scope, _, rest = text.partition("/")
        if not rest:
            return ""
        text = f"{scope}/{rest.split('@', 1)[0]}"
    else:
        text = text.split("@", 1)[0]
    return re.split(r"[=<>~!]", text, maxsplit=1)[0].strip()


def _first_package(args: Any) -> str:
    for arg in list(args or []):
        key = _package_key(arg)
        if key:
            return key
    return ""


def _first_sentence(text: Any) -> str:
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw:
        return ""
    first = re.split(r"(?<=[.!?])\s", raw, maxsplit=1)[0].strip()
    return first if first.endswith((".", "!", "?")) else first + "."


def catalog_blurbs(d) -> dict[str, str]:
    """Package → one line about what it does (v1.354.0, G49): the
    Directory catalog's ``blurb`` first (``connectors/catalog.CATALOG``),
    then the first sentence of the Tools catalog's description for a
    package only that one lists. A line that names the mechanism is left
    out, so a row then says where it came from instead."""
    index: dict[str, str] = {}
    try:
        from ...connectors.catalog import CATALOG

        for c in CATALOG:
            key = _first_package(c.args) if c.connect_via == "mcp" else ""
            if key and c.blurb and not _MECHANISM_WORD_RX.search(c.blurb):
                index.setdefault(key, c.blurb)
            # v1.355.0 (G44): a card that now reaches a maintained server
            # (GitHub's) still describes the package it used to run — an
            # imported Claude Desktop config naming it is the same app.
            for old in getattr(c, "replaces", None) or []:
                old_key = _package_key(old)
                if old_key and c.blurb and not _MECHANISM_WORD_RX.search(c.blurb):
                    index.setdefault(old_key, c.blurb)
    except Exception:  # noqa: BLE001 — a blurb never breaks the preview
        pass
    for row in getattr(d, "_MCP_CATALOG", None) or []:
        if not isinstance(row, dict):
            continue
        key = _first_package(row.get("args"))
        line = _first_sentence(row.get("description"))
        if key and line and not _MECHANISM_WORD_RX.search(line):
            index.setdefault(key, line)
    return index


def _blurb_for(spec: Any, blurbs: dict[str, str]) -> str:
    """The catalog line for a stdio spec whose arguments name a package the
    catalog knows; ``""`` otherwise (an http pack, an unknown package)."""
    if getattr(spec, "transport", "stdio") != "stdio":
        return ""
    for arg in getattr(spec, "args", None) or []:
        key = _package_key(arg)
        if key and key in blurbs:
            return blurbs[key]
    return ""


def preview_rows(
    d,
    specs: list[Any],
    *,
    known: dict[str, str] | None = None,
    blurbs: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """The import preview's rows (v1.343.0): each spec's ``preview_row``
    (names only — ``mcp/import_config.PackSpec.preview_row`` never carries
    a value) plus ``exists`` (the name is already in ``config.mcp_servers``),
    ``runtime_ready`` (:func:`launcher_ready`, cached per command; always
    True for an http/sse pack — nothing to launch) and ``launcher_note``.

    v1.354.0: ``exists`` also covers a server connected ANOTHER way — a live
    Directory connection or a memory source (:func:`known_connections`,
    names compared folded) — and ``exists_as`` says which (``"pack"`` /
    ``"app"`` / ``"memory"``, ``""`` when new); ``blurb`` is the catalog's
    one line about the package (:func:`catalog_blurbs`, ``""`` when the
    catalog does not know it); ``disabled`` (from the spec) says the app it
    came from has it switched off. ``known`` / ``blurbs`` let a caller that
    previews many lists (the sources route) read them once."""
    if known is None:
        known = known_connections(d)
    if blurbs is None:
        blurbs = catalog_blurbs(d)
    cache: dict[str, bool] = {}
    rows: list[dict[str, Any]] = []
    for spec in specs:
        row = spec.preview_row()
        if spec.transport == "stdio":
            cmd = spec.command
            if cmd not in cache:
                cache[cmd] = launcher_ready(cmd)
            ready = cache[cmd]
            note = launcher_note(cmd, ready)
        else:
            ready, note = True, ""
        exists_as = known.get(_fold_name(spec.name), "") if spec.name else ""
        row["exists"] = bool(exists_as)
        row["runtime_ready"] = ready
        row["launcher_note"] = note
        row["exists_as"] = exists_as
        row["blurb"] = _blurb_for(spec, blurbs)
        rows.append(row)
    return rows


def _body_from_spec(spec: Any, overrides: dict[str, str], auto_approve: bool) -> McpServerBody:
    """A ``McpServerBody`` for the one add door from a spec plus the values
    the user typed (``overrides``: env keys for a stdio pack, header names
    — or ``auth`` — for an http one). Raises ``ValueError`` when the shape
    does not validate (the caller reports it as a skip)."""
    from ..schemas import McpAuthBody

    env = dict(spec.env)
    headers = dict(spec.headers)
    auth = dict(spec.auth) if spec.auth else None
    for key, value in dict(overrides or {}).items():
        k = str(key)
        v = "" if value is None else str(value)
        if spec.transport == "stdio":
            env[k] = v
        elif k.lower() in ("auth", "auth.value", "token"):
            auth = {**(auth or {"header": "Authorization", "format": "Bearer {value}"}), "value": v}
        else:
            headers[k] = v
    kwargs: dict[str, Any] = {
        "name": spec.name,
        "transport": spec.transport,
        "auto_approve": bool(auto_approve),
    }
    if spec.transport == "stdio":
        kwargs.update(command=spec.command, args=list(spec.args), env=env)
        if spec.cwd:
            kwargs["cwd"] = spec.cwd
    else:
        kwargs.update(url=spec.url, headers=headers)
        if auth and auth.get("value"):
            kwargs["auth"] = McpAuthBody(
                header=str(auth.get("header") or "Authorization"),
                format=str(auth.get("format") or "Bearer {value}"),
                value=str(auth.get("value") or ""),
            )
    return McpServerBody(**kwargs)


def _apply_specs(
    d,
    specs: list[Any],
    names: list[str],
    overrides: dict[str, dict[str, str]],
    auto_approve: bool,
    replace: bool,
) -> dict[str, Any]:
    """Add the chosen specs through :func:`add_mcp_pack` — the SAME door
    ``POST /mcp/servers`` uses, so the secret split, the live load and the
    add note cannot drift — and answer ``{added, skipped}``.

    A name not in ``names`` is left alone; a name in ``names`` that no spec
    carries is skipped ("not in the paste"); an existing pack is skipped
    ("already added") unless ``replace`` removes it first
    (:func:`remove_mcp_pack`); a spec with no usable name is skipped; a
    refusal from the add door (an invalid shape, the vault refusing) is
    the skip's ``why`` — key names only, never a value.

    ORDER MATTERS for ``replace``: the body is BUILT (and so validated —
    a parsed entry can carry a url the add door refuses) before anything
    is removed, so a skip for a bad shape leaves the original pack and its
    vault rows untouched. The one refusal that cannot be checked ahead is
    the vault refusing the NEW values (503) after the old pack is gone: in
    that case :func:`_put_back_pack` restores the old config row and
    re-stores its vault values best-effort, and the skip says what could
    not be put back (key names only).
    """
    wanted = [str(n) for n in (names or [])]
    by_name: dict[str, Any] = {}
    for spec in specs:
        if spec.name and spec.name not in by_name:
            by_name[spec.name] = spec
    nameless = [s for s in specs if not s.name]
    added: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for name in wanted:
        if not name:
            if nameless and not any(s["name"] == "" for s in skipped):
                skipped.append({"name": "", "why": "no usable name"})
            continue
        spec = by_name.get(name)
        if spec is None:
            skipped.append({"name": name, "why": "not in the paste"})
            continue
        # The body first: a shape the add door refuses must skip BEFORE
        # any removal, or a replace would destroy the pack it could not
        # replace.
        try:
            body = _body_from_spec(spec, dict((overrides or {}).get(name) or {}), auto_approve)
        except (ValueError, TypeError) as exc:
            skipped.append({"name": name, "why": _plain_validation_words(exc)})
            continue
        old_row = _pack_row(d, name)
        old_values: dict[str, str] = {}
        if old_row is not None:
            if not replace:
                skipped.append({"name": name, "why": "already added"})
                continue
            old_values = _pack_vault_values(d, name, old_row)
            try:
                remove_mcp_pack(d, name)
            except HTTPException as exc:
                skipped.append({"name": name, "why": f"could not replace: {exc.detail}"})
                continue
        try:
            result = add_mcp_pack(d, body)
        except HTTPException as exc:
            why = str(exc.detail)
            if old_row is not None:
                why = _put_back_pack(d, old_row, old_values, why)
            skipped.append({"name": name, "why": why})
            continue
        from ...mcp.tools import load_status as _mcp_load_status

        status = _mcp_load_status(name) or {}
        tools_loaded = int(result.get("tools_loaded") or 0)
        added.append(
            {
                "name": name,
                "tools_loaded": tools_loaded,
                "note": result.get("note"),
                "state": status.get("state") or ("ok" if tools_loaded else "failed"),
            }
        )
    return {"added": added, "skipped": skipped}


def _pack_row(d, name: str) -> dict[str, Any] | None:
    """A COPY of the stored config row named *name*, or None."""
    for s in getattr(d.platform.config, "mcp_servers", None) or []:
        if isinstance(s, dict) and str(s.get("name") or "") == name:
            return json.loads(json.dumps(s))
    return None


def _pack_vault_values(d, name: str, row: dict[str, Any]) -> dict[str, str]:
    """The pack's vault rows (``mcp.<name>.*``) read into memory so a failed
    replace can put them back. Values stay in this process: nothing here is
    logged or returned. A row that cannot be read is left out."""
    from ...mcp.secrets import pack_vault_names as _pack_vault_names

    secrets = getattr(d.platform, "secrets", None)
    out: dict[str, str] = {}
    if secrets is None:
        return out
    try:
        names = _pack_vault_names(secrets, name, row)
    except Exception:  # noqa: BLE001 — best effort
        return out
    for vault_name in names:
        try:
            value = secrets.get(vault_name)
        except Exception:  # noqa: BLE001
            value = None
        if value:
            out[vault_name] = str(value)
    return out


def _put_back_pack(d, row: dict[str, Any], values: dict[str, str], why: str) -> str:
    """After ``replace`` removed a pack and the add door then refused the
    new one (the vault refusing its values), restore the OLD config row
    and re-store its vault values best-effort. Returns the skip's ``why``
    extended with what happened — vault names cut to their KEY, never a
    value. The old pack's tools come back on the next load (Retry or a
    restart); the row is what must not be lost."""
    name = str(row.get("name") or "")
    servers = list(getattr(d.platform.config, "mcp_servers", None) or [])
    if name and not any(isinstance(s, dict) and s.get("name") == name for s in servers):
        servers.append(row)
        d.platform.config.mcp_servers = servers
        try:
            d._persist_config(["mcp_servers"])
        except Exception as exc:  # noqa: BLE001 — the in-memory row still stands
            log.warning("MCP pack %r: put-back could not be persisted: %s", name, type(exc).__name__)
    lost: list[str] = []
    secrets = getattr(d.platform, "secrets", None)
    for vault_name, value in values.items():
        key = vault_name[len(f"mcp.{name}."):] if vault_name.startswith(f"mcp.{name}.") else vault_name
        try:
            if secrets is None:
                raise RuntimeError("no vault")
            secrets.set(vault_name, value, kind="api_key", description=f"MCP pack {name}: {key}")
        except Exception:  # noqa: BLE001 — names only
            lost.append(key)
    note = f"{why}; the earlier pack was put back"
    if lost:
        note += f", but its stored {', '.join(sorted(lost))} could not be; enter it again in Settings"
    return note


def _plain_validation_words(exc: BaseException) -> str:
    """Pydantic's ``ValidationError`` text, cut to its one sentence."""
    text = str(exc)
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Value error, "):
            return line[len("Value error, "):].split(" [type=", 1)[0]
    return text.splitlines()[0][:200] if text else "the pack could not be read"


def mcp_server_row(d, s: dict[str, Any]) -> dict[str, Any]:
    """ONE configured pack as ``GET /mcp/servers`` lists it (v1.343.0, lane
    J: lifted out of the route so the PATCH answer is the same row). Key and
    header NAMES only, never a value; annotated with the live tool count and
    the load record."""
    from ...mcp.tools import load_status as _mcp_load_status

    name = s.get("name") or ""
    loaded = d.platform.registry.mcp_names(name) if name else []
    # v1.299.0: the per-tool view, read off the LIVE Tool objects so the
    # row and the resolver cannot disagree about what is held back.
    tool_rows = []
    for full in loaded:
        live = d.platform.registry.get(full)
        tool_rows.append(
            {
                "name": full.split("__", 2)[-1],
                "write_like": bool(getattr(live, "write_like", True)),
                "quarantined": bool(getattr(live, "quarantined", False)),
            }
        )
    # v1.229.0 (audit U4): WHY a server holds 0 tools. The load record
    # keeps the skip reason (e.g. "FileNotFoundError: npx not found")
    # that used to live only in a daemon.log warning; `null` means it
    # loaded (or was never attempted in this process).
    status = _mcp_load_status(name) if name else None
    # v1.342.0 (lane C): a row carries env KEY NAMES, never a value.
    # `env` (plain entries) and `env_secrets` (vault names) stay out
    # of the answer; `env_keys` + `secret_keys` say what is set. A
    # pre-migration row that still holds a plain token is masked the
    # same way — the key is listed, the value never leaves.
    _plain_env = dict(s.get("env") or {})
    _env_secrets = dict(s.get("env_secrets") or {})
    # v1.343.0 (lane H): an http pack's `headers` are VALUES and its
    # `header_secrets` are vault names — neither rides the row; the
    # row lists header NAMES and says whether an auth value is set.
    _plain_headers = dict(s.get("headers") or {})
    _header_secrets = dict(s.get("header_secrets") or {})
    # v1.351.0 (G12): `arg_secrets` / `url_secret` are vault NAMES (the
    # argv / url twins of `env_secrets`) — they stay out of the row like the
    # others. And the command, args and url are shown MASKED, as the import
    # preview's rows have been since v1.343.0 (`PackSpec.preview_row`): a
    # stored placeholder is already masked, and a row from before the
    # migration (or a Directory connect that has not been migrated yet) must
    # still never put a token in front of the browser.
    from ...detections.redact import mask as _mask

    _row = {
        k: v
        for k, v in s.items()
        if k not in ("env", "env_secrets", "headers", "header_secrets", "arg_secrets", "url_secret")
    }
    if isinstance(_row.get("command"), str):
        _row["command"] = _mask(_row["command"])
    if isinstance(_row.get("url"), str):
        _row["url"] = _mask(_row["url"])
    # v1.342.0 (integration): an HTTP pack's header token (`auth.value`,
    # the non-env secret shape _build_transport reads) is a VALUE too.
    # The row keeps the auth shape and says a value is set, never what.
    # v1.343.0: the same for `auth.secret` (the vault name the POST
    # door stores) — the row says `value_set`, not where.
    _auth_set = False
    if isinstance(_row.get("auth"), dict):
        _auth_set = bool(_row["auth"].get("value") or _row["auth"].get("secret"))
        _row["auth"] = {
            k: v for k, v in _row["auth"].items() if k not in ("value", "secret")
        } | {"value_set": _auth_set}
    return {
        **_row,
        # Rows saved by the marketplace connect flow OMIT env/args
        # when empty — normalize so clients can rely on the keys
        # (a missing env crashed the Tools page on real installs).
        # v1.351.0 (G12): each argument masked (see above).
        "args": [_mask(str(a)) for a in (s.get("args") or [])],
        "env_keys": sorted(str(k) for k in _plain_env),
        "secret_keys": sorted(str(k) for k in _env_secrets),
        # v1.343.0: the http shape — transport ("stdio" when the
        # row predates it), header NAMES, whether auth is set.
        "transport": (
            s["transport"]
            if isinstance(s.get("transport"), str) and s.get("transport")
            else "stdio"
        ),
        "header_names": sorted(
            {str(k) for k in _plain_headers} | {str(k) for k in _header_secrets}
        ),
        "auth_set": _auth_set,
        "tools_loaded": len(loaded),
        "tool_names": [n.split("__", 2)[-1] for n in loaded],
        # v1.299.0 (additive): write-like tools that APPEARED or
        # CHANGED since the pack was first trusted; they ask until
        # `POST /mcp/servers/{name}/tools/{tool}/trust`.
        "tools": tool_rows,
        "quarantined": [t["name"] for t in tool_rows if t["quarantined"]],
        "last_error": status.get("last_error") if status else None,
        # v1.256.0 (R-02): the same raw text, plus what it MEANS and
        # what to do. Classified once on the load record so this row
        # and the Overview hero cannot form two opinions about it.
        "reason": status.get("reason") if status else None,
        "fix": status.get("fix") if status else None,
        "last_attempt_at": status.get("at") if status else None,
        # v1.311.0: "starting" while the daemon's background load
        # has not answered yet (packs load after boot now) — a row
        # must not read as "0 tools, broken" in that window.
        # v1.342.0: "ok" / "failed" / "failing" (loaded, but its
        # calls fail) — the row says it in words.
        "state": status.get("state") if status else None,
        # v1.342.0: call-time failures since the last success or
        # Retry, and the child's last output (masked, bounded) —
        # the one clue a crashed pack leaves behind.
        "failures": int(status.get("failures") or 0) if status else 0,
        "stderr_tail": str(status.get("stderr_tail") or "") if status else "",
    }


#: The keys that make up a pack row's SHAPE (stdio or http). Anything else a
#: stored row carries (``auto_approve``, a Directory mark) is kept as is by
#: an edit.
_PACK_SHAPE_KEYS: frozenset[str] = frozenset(
    {
        "name",
        "transport",
        "command",
        "args",
        "env",
        "env_secrets",
        "cwd",
        "url",
        "headers",
        "header_secrets",
        "auth",
        # v1.351.0 (G12): rebuilt by the edit, never copied blindly.
        "arg_secrets",
        "url_secret",
    }
)


def update_mcp_pack(d, name: str, body: McpServerPatchBody) -> dict[str, Any]:
    """Edit a configured pack PARTIALLY and reload it (v1.343.0, lane J).

    The row that exists is the base; every field the body carries replaces
    or merges into it (``env`` / ``headers`` key by key, a secret-named
    key's value into the vault under the same names the add door uses;
    ``clear_secrets`` / ``clear_headers`` drop a key's vault row AND its row
    entry; ``auth: null`` drops the token). Omitted = keep; ``""`` is a
    value; ``cwd: ""`` stores no folder, as the add door does. The result
    is validated like a POST (an http pack needs a url, a stdio pack a
    command), every vault write lands BEFORE the row is persisted (a
    refusal changes nothing and names the KEYS), then the pack's live tools
    are unloaded (clients closed) and loaded again through the one loader,
    manifest re-diffed. Answers the ``GET /mcp/servers`` row plus
    ``{reloaded, note}``. A rename is a 422 (the name is the identity).
    Raises ``HTTPException`` exactly as the route answers.
    """
    from ...mcp.secrets import is_secret_key as _is_secret_key
    from ...mcp.secrets import split_env as _split_env
    from ...mcp.secrets import vault_name as _vault_name

    servers = list(getattr(d.platform.config, "mcp_servers", None) or [])
    idx = next(
        (i for i, s in enumerate(servers) if isinstance(s, dict) and s.get("name") == name),
        None,
    )
    if idx is None:
        raise HTTPException(status_code=404, detail="no such server")
    if body.name is not None and body.name.strip() != name:
        raise HTTPException(
            status_code=422,
            detail="the name cannot change; remove the pack and add it again under the new name",
        )
    old = servers[idx]
    row = json.loads(json.dumps(old))
    old_transport = (
        row["transport"] if isinstance(row.get("transport"), str) and row.get("transport") else "stdio"
    )
    transport = body.transport if body.transport is not None else old_transport
    is_http = transport in ("http", "sse")

    # Vault work is COLLECTED first and applied in order: writes before the
    # row is persisted (a refusal leaves everything as it was), deletes
    # after (an orphaned vault row is harmless; a lost one is not).
    writes: list[tuple[str, str, str]] = []  # (vault name, value, the KEY for the words)
    deletes: list[str] = []

    # -- the stdio half ---------------------------------------------------- #
    env: dict[str, str] = {str(k): ("" if v is None else str(v)) for k, v in dict(row.get("env") or {}).items()}
    env_secrets: dict[str, str] = {str(k): str(v) for k, v in dict(row.get("env_secrets") or {}).items()}
    if body.env is not None:
        plain, secret_values = _split_env(name, body.env)
        for key, value in plain.items():
            env[key] = value
        for key, value in secret_values.items():
            sname = _vault_name(name, key)
            writes.append((sname, value, key))
            env_secrets[key] = sname
            # A pre-migration plain value under that key is superseded.
            env.pop(key, None)
    for key in body.cleared_secrets():
        sname = env_secrets.pop(key, None)
        if sname:
            deletes.append(sname)
        env.pop(key, None)
    command = body.command if body.command is not None else str(row.get("command") or "")
    cwd = body.cwd if body.cwd is not None else str(row.get("cwd") or "")
    # v1.351.0 (G12): the args. The row SHOWED them masked, and the editor
    # sends that masked line back whenever anything in it changed (often
    # split on whitespace). So: args sent back as shown = unchanged (the
    # stored args and their vault rows stay exactly as they are); otherwise
    # every `***` is put back to the real text it stood for
    # (`carry_masked_args` — one it cannot place is a 422, never a literal
    # `***` over a working secret), then split again like the add door.
    from ...detections.redact import mask as _mask
    from ...mcp.secrets import MaskedValueLeft as _MaskedValueLeft
    from ...mcp.secrets import arg_key as _arg_key
    from ...mcp.secrets import args_unchanged as _args_unchanged
    from ...mcp.secrets import carry_masked_args as _carry_masked_args
    from ...mcp.secrets import resolve_args as _resolve_args
    from ...mcp.secrets import split_args as _split_args
    from ...mcp.secrets import split_url as _split_url

    _vault_get = getattr(getattr(d.platform, "secrets", None), "get", None)
    stored_args = [str(a) for a in (row.get("args") or [])]
    old_arg_secrets = {str(k): str(v) for k, v in dict(row.get("arg_secrets") or {}).items()}
    args: list[str] = stored_args
    arg_secrets: dict[str, str] = dict(old_arg_secrets)
    if body.args is not None:
        shown_before = [_mask(a) for a in stored_args]
        if not _args_unchanged(body.args, shown_before):
            try:
                real_args, carried = _carry_masked_args(
                    body.args, shown_before, _resolve_args(stored_args, old_arg_secrets, _vault_get)
                )
            except _MaskedValueLeft as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            args, arg_values = _split_args(real_args)
            # A value put back for a `***` stays a secret even when, split off
            # its `Authorization:`, it no longer looks like one.
            for i in carried:
                if i not in arg_values:
                    arg_values[i] = real_args[i]
                    args[i] = str(body.args[i])
            arg_secrets = {}
            for i, value in sorted(arg_values.items()):
                sname = _vault_name(name, _arg_key(i))
                writes.append((sname, value, f"argument {i + 1}"))
                arg_secrets[str(i)] = sname
            deletes.extend(n for n in old_arg_secrets.values() if n not in arg_secrets.values())

    # -- the http half ----------------------------------------------------- #
    # v1.351.0 (G12): the url, the same way — sent back as the row showed it
    # = unchanged; a NEW url that carries a secret is vaulted; a `***` that
    # does not match the stored url is a 422.
    stored_url = str(row.get("url") or "")
    url = stored_url
    url_secret = str(row.get("url_secret") or "")
    if body.url is not None and body.url != _mask(stored_url):
        if "***" in body.url:
            raise HTTPException(
                status_code=422,
                detail="the url still shows *** where a hidden value was; type the whole url again",
            )
        url, url_value = _split_url(body.url)
        if url_value:
            sname = _vault_name(name, "url")
            writes.append((sname, url_value, "the url"))
            url_secret = sname
        elif url_secret:
            deletes.append(url_secret)
            url_secret = ""
    headers: dict[str, str] = {
        str(k): ("" if v is None else str(v)) for k, v in dict(row.get("headers") or {}).items()
    }
    header_secrets: dict[str, str] = {
        str(k): str(v) for k, v in dict(row.get("header_secrets") or {}).items()
    }
    if body.headers is not None:
        for header_name, value in dict(body.headers).items():
            h = str(header_name).strip()
            v = "" if value is None else str(value)
            if not h:
                continue
            if _is_secret_key(h):
                if v:
                    sname = _vault_name(name, f"header.{h}")
                    writes.append((sname, v, f"header {h}"))
                    header_secrets[h] = sname
                    headers.pop(h, None)
            else:
                headers[h] = v
    for h in body.cleared_headers():
        sname = header_secrets.pop(h, None)
        if sname:
            deletes.append(sname)
        headers.pop(h, None)
    auth: dict[str, Any] | None = dict(row["auth"]) if isinstance(row.get("auth"), dict) else None
    if body.clears_auth():
        if auth and auth.get("secret"):
            deletes.append(str(auth["secret"]))
        auth = None
    elif body.auth is not None:
        new_auth: dict[str, Any] = {
            "header": body.auth.header.strip(),
            "format": body.auth.format,
        }
        if body.auth.value:
            sname = _vault_name(name, "auth.value")
            writes.append((sname, body.auth.value, "the auth value"))
            new_auth["secret"] = sname
        elif auth and auth.get("secret"):
            new_auth["secret"] = auth["secret"]
        elif auth and auth.get("value"):
            # A pre-migration plain value: kept where it was, still masked
            # by the row (`value_set`), until a new value replaces it.
            new_auth["value"] = auth["value"]
        else:
            new_auth = {}
        auth = new_auth or None

    # -- validate the RESULT like the add door ----------------------------- #
    if is_http:
        url = (url or "").strip()
        if not url:
            raise HTTPException(status_code=422, detail=f"an {transport} pack needs a url")
        if not url.lower().startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="the url must start with http:// or https://")
    else:
        if not (command or "").strip():
            raise HTTPException(status_code=422, detail="a stdio pack needs a command")

    # -- the new row: today's stored shapes, byte for byte ----------------- #
    if is_http:
        new: dict[str, Any] = {"name": name, "transport": transport, "url": url}
        if url_secret:
            new["url_secret"] = url_secret
        if headers:
            new["headers"] = headers
        if header_secrets:
            new["header_secrets"] = header_secrets
        if auth:
            new["auth"] = auth
        # A pack that moved off stdio leaves its env (and argument) secrets
        # behind.
        deletes.extend(env_secrets.values())
        deletes.extend(arg_secrets.values())
    else:
        new = {
            "name": name,
            "command": command.strip(),
            "args": [str(a) for a in args],
            "env": env,
        }
        if arg_secrets:
            new["arg_secrets"] = arg_secrets
        if env_secrets:
            new["env_secrets"] = env_secrets
        if cwd:
            new["cwd"] = cwd
        # A pack that moved off http leaves its header / auth (and url)
        # secrets behind.
        deletes.extend(header_secrets.values())
        if auth and auth.get("secret"):
            deletes.append(str(auth["secret"]))
        if url_secret:
            deletes.append(url_secret)
    for key, value in row.items():
        if key not in _PACK_SHAPE_KEYS:
            new[key] = value
    if body.auto_approve is True:
        new["auto_approve"] = True
    elif body.auto_approve is False:
        new.pop("auto_approve", None)  # absent == off, matching POST

    # -- the vault, then the row, then the vault's leftovers --------------- #
    secrets = getattr(d.platform, "secrets", None)
    try:
        for sname, value, key in writes:
            if secrets is None:
                raise RuntimeError("no vault")
            secrets.set(sname, value, kind="api_key", description=f"MCP pack {name}: {key}")
    except Exception as exc:  # noqa: BLE001 — names only, never the value
        refused = sorted(key for _s, _v, key in writes)
        raise HTTPException(
            status_code=503,
            detail=(
                f"the vault could not store {', '.join(refused)} "
                f"({type(exc).__name__}); the pack was not changed"
            ),
        )
    servers[idx] = new
    d.platform.config.mcp_servers = servers
    d._persist_config(["mcp_servers"])
    for sname in dict.fromkeys(deletes):
        try:
            if secrets is not None:
                secrets.delete(sname)
        except Exception as exc:  # noqa: BLE001 — the row no longer names it
            log.warning("MCP pack %r: vault row %r could not be deleted: %s", name, sname, type(exc).__name__)

    # -- unload, then load again through the one loader -------------------- #
    from ...mcp.tools import mcp_tools as _mcp_tools
    from ...mcp.tools import unload_pack_tools as _unload_pack_tools

    _unload_pack_tools(d.platform.registry, name)
    loaded = 0
    errors: dict[str, dict[str, Any]] = {}
    try:
        # `home`: the reload re-diffs the pack against its manifest, as a
        # Retry does, so a tool that arrived with the edit is held back
        # exactly as it would be at the next boot.
        for tool in _mcp_tools(
            [new],
            secret_resolver=d.platform.secrets.get,
            home=d.platform.config.home,
            errors=errors,
        ):
            d.platform.registry.register(tool, mcp=True)
            loaded += 1
    except Exception as exc:  # noqa: BLE001 — the row is saved; say why it did not start
        errors.setdefault(name, {"reason": f"{type(exc).__name__}: {exc}"})
        loaded = 0
    notes: list[str] = []
    if not loaded:
        failure = errors.get(name)
        if failure is not None:
            reason = failure.get("reason") or failure.get("error") or "it did not start"
            notes.append(f"saved, but it did not start: {reason}; see its row for the fix")
        else:
            notes.append("saved; it connected but advertised no tools, see its row")
    if body.auto_approve is not None and bool(body.auto_approve) != bool(old.get("auto_approve")):
        notes.append("restart Iron Jarvis for autonomous agents to pick up the auto-approve change")
    return {
        **mcp_server_row(d, new),
        "reloaded": loaded > 0,
        "note": "; ".join(notes) if notes else None,
    }


def panel_reply_lines(round_out: dict[str, Any], room_id: str) -> list[dict[str, Any]]:
    """The round's replies as the EXACT chat lines the page's ``sendPanel``
    composes (v1.345.0; read ``dashboard/app/chat/page.tsx`` sendPanel):
    the user's own turn left out, ``content`` ``"(no reply)"`` when empty,
    ``panelWho`` the participant key, ``panelThreadId`` the room, a failed
    seat ``panelError: true``, the context-window drop as ``panelNote``
    on the FIRST reply, a remote that took the task and will report back
    (``pending``) as a quiet ``panelKind: "progress"`` line, and any
    ``documents`` the entry carried (absolute paths)."""
    replies = [
        e
        for e in (round_out.get("entries") or [])
        if isinstance(e, dict) and e.get("who") and e.get("who") != "user"
    ]
    dropped = int((round_out.get("context") or {}).get("chat_dropped") or 0)
    note = (
        f"{dropped} earlier message{'' if dropped == 1 else 's'} did not fit "
        "the agent's context window"
        if dropped > 0
        else ""
    )
    lines: list[dict[str, Any]] = []
    for idx, e in enumerate(replies):
        line: dict[str, Any] = {
            "role": "assistant",
            "content": str(e.get("content") or "") or "(no reply)",
            "panelWho": str(e.get("who")),
            "panelThreadId": room_id,
        }
        if e.get("error"):
            line["panelError"] = True
        if idx == 0 and note:
            line["panelNote"] = note
        if e.get("pending"):
            line["panelKind"] = "progress"
        docs = e.get("documents")
        if isinstance(docs, list) and docs:
            line["documents"] = [str(p) for p in docs]
        lines.append(line)
    return lines


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""
    @app.get("/tools")
    def tools() -> dict[str, Any]:
        return {"tools": d.platform.registry.specs()}

    @app.post("/skills/{name}/apply")
    async def apply_skill(name: str, body: SkillApplyBody) -> dict[str, Any]:
        """USE a skill right here: the skill's full instructions + the user's
        request go to the model in one shot (retry/failover included) and the
        result comes straight back — no session plumbing."""
        sk = d.platform.skills.get(name)
        if sk is None:
            raise HTTPException(status_code=404, detail="no such skill")
        if not (body.request or "").strip():
            raise HTTPException(status_code=400, detail="request is required")
        provider = body.provider or d.platform.config.default_provider
        model = body.model or d.platform.config.default_model
        try:
            adapter = d.platform.providers.get(provider, model)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"provider unavailable: {exc}")
        from ...providers.adapters.base import LLMMessage

        system = (
            "Fulfil the user's request by FOLLOWING this skill playbook exactly.\n\n"
            f"# Skill: {sk.name}\n{sk.instructions[:8000]}"
        )
        resp, used_provider, used_model = await d._one_shot_complete(
            provider,
            adapter,
            system=system,
            messages=[LLMMessage(role="user", content=body.request.strip()[:6000])],
        )
        return {
            "reply": resp.text or "(no reply)",
            "skill": sk.name,
            "provider": used_provider,
            "model": used_model,
        }

    @app.get("/skills")
    def skills() -> dict[str, Any]:
        # v1.297.0 (the curator): `created_by` ("user" | "agent" | "proposal")
        # and `pinned` ride every row, additive — a Skill from before the
        # provenance fields reads as a user-authored, unpinned skill.
        items = [
            {
                "name": s.name,
                "description": s.description,
                "source": s.source,
                "created_by": str(getattr(s, "created_by", "") or "user"),
                "pinned": bool(getattr(s, "pinned", False)),
            }
            for s in d.platform.skills.list()
        ]
        # A per-source tally so the dashboard can show "12 Claude · 8 Codex · …".
        counts: dict[str, int] = {}
        for it in items:
            counts[it["source"]] = counts.get(it["source"], 0) + 1
        return {"skills": items, "counts": counts}

    @app.get("/skills/{name}")
    def skill(name: str) -> dict[str, Any]:
        sk = d.platform.skills.get(name)
        if sk is None:
            raise HTTPException(status_code=404, detail="no such skill")
        return {
            "name": sk.name,
            "description": sk.description,
            "instructions": sk.instructions,
            "source": sk.source,
            "created_by": str(getattr(sk, "created_by", "") or "user"),
            "pinned": bool(getattr(sk, "pinned", False)),
        }

    @app.post("/skills/rescan")
    def rescan_skills() -> dict[str, Any]:
        """Re-scan every source (builtin + user + Claude + Codex + extra paths)
        so newly-added external skills show up without restarting the daemon."""
        counts = d._rescan_skills()
        return {"total": sum(counts.values()), "counts": counts}

    @app.post("/skills")
    def create_skill(body: SkillCreate) -> dict[str, Any]:
        """Author a new skill (name + description + instructions).

        Persists ``<home>/skills/<slug>/SKILL.md`` and re-scans so it shows up
        immediately — user skills sit alongside the built-ins and the pulled-in
        Claude/Codex skills, searchable/injectable by agents the same way.
        """
        from ...skills import save_skill as _save_skill

        try:
            _save_skill(
                d.platform.config.home / "skills",
                body.name,
                body.description,
                body.instructions,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        # Re-scan so the new skill (and any external ones) are live without a restart.
        d._rescan_skills()
        sk = d.platform.skills.get(body.name.strip())
        return {"name": sk.name if sk else body.name, "created": True}

    # --- portrait storage (v1.171.0): <home>/avatars/<slug>.png -------------

    def _avatar_path(name: str) -> Path:
        return d.platform.config.home / "avatars" / f"{_avatar_slug(name)}.png"

    def _effective_tools(name: str) -> list[str] | None:
        """What this agent ACTUALLY holds, inheritance resolved (v1.178.0).

        Never raises: a display field must not be able to break the agents list.

        NONE MEANS UNKNOWN, `[]` MEANS GENUINELY NONE (v1.185.0). It used to
        return `[]` from both the failure branch and the real-empty case, so the
        card could not tell "the registry would not answer" from "this agent
        holds nothing" — and it renders those two as opposite sentences: the
        first is "this daemon does not report it, fall back to the stored list",
        the second is a claim about the agent. Collapsing them meant a registry
        hiccup displayed as a confident, wrong roster — the same shape as the
        v1.178.0 bug this field was ADDED to fix, one level up.

        The clients already speak this dialect: `effectiveOrNull` in SetupCard
        maps a non-array to `null` and its `ToolOrigin` carries "unreported",
        because the field is absent on a pre-v1.178.0 daemon. An unknown is
        indistinguishable from that older daemon, which is exactly right.
        """
        try:
            definition = d.platform.agents_registry.definition(name)
        except Exception:  # noqa: BLE001 — a display field never breaks the list
            return None
        return list(definition.tools) if definition is not None else None

    def _avatar_url(name: str) -> str | None:
        """The serve URL — ONLY when a stored portrait actually exists.
        None otherwise, so no client ever renders a broken 404 image."""
        try:
            if name and _avatar_path(name).is_file():
                return f"/agents/{quote(name, safe='')}/avatar"
        except OSError:
            pass
        return None

    # --- face overrides (v1.180.0): <home>/faces/<slug>.json ----------------
    # THE SAME SLUG as the portrait above, by construction — an agent's
    # portrait and its face key can never disagree because there is exactly one
    # `_avatar_slug` and both paths are built from it here.

    def _face_slug(name: str) -> str:
        return _avatar_slug(name)

    def _face_override(name: str) -> dict[str, str] | None:
        """This agent's stored override, or None when it derives from its name.

        None (not ``{}``) so the wire field reads exactly like ``avatar``: a
        null means "no override — derive", which is what every surface did
        before this feature existed. Never raises: a display field must not be
        able to break the agents list or the roster.
        """
        try:
            if not name:
                return None
            return faces.read_face(d.platform.config.home, _face_slug(name)) or None
        except Exception:  # noqa: BLE001 — a display field never breaks the list
            return None

    def _image_key() -> str | None:
        """Same key resolution as the creative routes: vault first, env second."""
        try:
            key = d.platform.secrets.get("pixio")
        except Exception:  # noqa: BLE001 — vault miss = not configured
            key = None
        return key or os.environ.get("PIXIO_API_KEY") or None

    def _json_list(text: Any) -> list[Any]:
        import json as _json

        try:
            value = _json.loads(text or "[]")
        except (TypeError, ValueError):
            return []
        return list(value) if isinstance(value, list) else []

    def _dynamic_row(r) -> dict[str, Any]:
        """ONE row shape for a custom agent — ``GET /agents`` and the
        pause/resume doors all answer with it (v1.295.0), so the Agents page
        never has to re-fetch the list to learn what it just changed.

        Every pre-v1.295.0 key is kept verbatim; the employee fields are
        additive. ``allowance`` is one ledger read per agent, guarded: a
        broken ledger costs that field (null), never the list.
        """
        paused_reason = str(getattr(r, "paused_reason", "") or "")
        paused_at = getattr(r, "paused_at", None)
        paused = None
        if paused_reason:
            paused = {
                "reason": paused_reason,
                "at": paused_at.isoformat() if paused_at is not None else None,
            }
        try:
            allowance = allowance_state(
                r, month_spend(d.platform.engine, f"custom:{r.name}")
            )
        except Exception:  # noqa: BLE001 — the allowance is a bonus, the row is the job
            allowance = None
        return {
            "name": r.name,
            "description": r.description,
            "provider": r.provider,
            "model": r.model,
            # Editable fields so the Agents page can PATCH them without a
            # separate detail fetch.
            "system_prompt": r.system_prompt,
            # `tools` stays the STORED list — the Agents page PATCHes
            # this field back, so echoing an inherited roster here would
            # freeze the inheritance into an explicit allowlist on the
            # first save the user makes for an unrelated reason.
            "tools": _json_list(r.tools_json),
            # ...and `effective_tools` is what the agent ACTUALLY holds
            # (v1.178.0): an empty stored list inherits the base type's
            # roster, so a card rendering only `tools` would tell the
            # user "no tools" about an agent that works. Read-only,
            # additive, and never PATCHed back.
            "effective_tools": _effective_tools(r.name),
            # v1.171.0 additive: the portrait URL when one is stored
            # (None otherwise) — the Setup card's avatar row reads it.
            "avatar": _avatar_url(r.name),
            # v1.180.0 additive, exactly the same contract: the chosen
            # face when one is stored, null when the face derives from
            # the name. A client older than this field, or a daemon
            # older than it, both land on the derived face.
            "face": _face_override(r.name),
            # --- the job card (v1.295.0) ---------------------------------
            "base_type": str(getattr(r, "base_type", "") or "builder"),
            "approval_mode": str(getattr(r, "approval_mode", "") or ""),
            "max_steps": getattr(r, "max_steps", None),
            "reports_to": str(getattr(r, "reports_to", "") or ""),
            "skills": _json_list(getattr(r, "skills_json", "[]")),
            "deny_tools": _json_list(getattr(r, "deny_tools_json", "[]")),
            # The day off: {reason, at} while paused, null otherwise.
            "paused": paused,
            # The monthly allowance against this month's ledger, or null.
            "allowance": allowance,
        }

    def _employee_fields(body: Any, *, name: str, creating: bool) -> dict[str, Any]:
        """Validate a hire/edit body's employee fields and return ONLY the
        provided ones as ``register()`` kwargs (v1.295.0).

        Plain-words 422s, never silent coercion: a value the registry or the
        runtime would quietly reshape ("yolo" → approve_for_me, an unknown
        base type → builder) is refused HERE with the reason, because the job
        card the user wrote and the one on file must say the same thing. On a
        PATCH ``None`` means "keep" and is simply not passed; on a create every
        field is present (its default when unstated) and all are passed.
        """

        def refuse(sentence: str) -> None:
            raise HTTPException(status_code=422, detail=sentence)

        builtins = ", ".join(t.value for t in AgentType if t is not AgentType.SUPERVISOR)
        out: dict[str, Any] = {}

        base_type = getattr(body, "base_type", None)
        if base_type is not None:
            base = str(base_type).strip()
            try:
                AgentType(base)
            except ValueError:
                refuse(
                    f"base_type '{base_type}' is not a builtin agent type — "
                    f"pick one of: {builtins}"
                )
            if base == AgentType.SUPERVISOR.value:
                refuse(
                    "base_type 'supervisor' is refused: the builtin supervisor "
                    "would run and silently discard this agent's custom system "
                    f"prompt — pick one of: {builtins}"
                )
            out["base_type"] = base

        mode = getattr(body, "approval_mode", None)
        if mode is not None:
            posture = str(mode).strip()
            if posture == "yolo":
                refuse(
                    "approval_mode 'yolo' is refused for an agent: a chat's "
                    "auto-approve is consented to one turn at a time with you "
                    "watching, and a background run of many tool calls is a "
                    "different blast radius — use 'approve_for_me' or "
                    "'always_ask', or leave it blank for the default"
                )
            if posture not in AGENT_APPROVAL_MODES:
                refuse(
                    f"approval_mode '{mode}' is not one of: approve_for_me, "
                    "always_ask (or blank for the default)"
                )
            out["approval_mode"] = posture

        # Bounds already enforced by the schema's own validator (the session
        # door's `_clean_max_steps`); None on a create = the configured default.
        # On a PATCH, None = keep, and `clear_max_steps` passes None EXPLICITLY
        # (the registry only keeps an OMITTED kwarg) to go back to the default.
        steps = getattr(body, "max_steps", None)
        if getattr(body, "clear_max_steps", False):
            if steps is not None:
                refuse(
                    f"clear_max_steps and max_steps={steps} say opposite things "
                    "— send one or the other"
                )
            out["max_steps"] = None
        elif creating or steps is not None:
            out["max_steps"] = steps

        for field, unit in (("allowance_tokens", "tokens"), ("allowance_usd", "dollars")):
            amount = getattr(body, field, None)
            if amount is None:
                continue
            if isinstance(amount, bool) or amount < 0:
                refuse(
                    f"{field} must be 0 (unlimited) or a positive number of "
                    f"{unit} for the month, not {amount!r}"
                )
            out[field] = amount

        manager = getattr(body, "reports_to", None)
        if manager is not None:
            target = str(manager).strip()
            if target in (name, f"custom:{name}"):
                refuse(
                    "reports_to cannot be the agent itself — an agent is not its "
                    "own manager; leave it blank to report to you"
                )
            if target.startswith("custom:"):
                bare = target[len("custom:"):]
                if not bare or d.platform.agents_registry.get(bare) is None:
                    refuse(
                        f"reports_to '{target}' names a custom agent that does "
                        "not exist — create it first, or leave reports_to blank "
                        "to report to you"
                    )
            elif target:
                try:
                    AgentType(target)
                except ValueError:
                    refuse(
                        f"reports_to '{target}' is neither a builtin agent "
                        f"({builtins}, supervisor) nor 'custom:<name>' of an "
                        "existing custom agent; leave it blank to report to you"
                    )
            out["reports_to"] = target

        for field in ("skills", "deny_tools"):
            items = getattr(body, field, None)
            if items is None:
                continue
            cleaned: list[str] = []
            for index, raw in enumerate(items, start=1):
                text = str(raw).strip()
                if not text:
                    refuse(
                        f"{field} must be a list of names — entry {index} is "
                        "blank"
                    )
                if text not in cleaned:
                    cleaned.append(text)
            out[field] = cleaned
        return out

    @app.get("/agents")
    def list_agents() -> dict[str, Any]:
        from ...agents.types import _DEFINITIONS

        return {
            "builtin": [t.value for t in _DEFINITIONS],
            "dynamic": [_dynamic_row(r) for r in d.platform.agents_registry.list()],
        }

    @app.post("/agents")
    def create_agent(body: AgentCreate) -> dict[str, Any]:
        name = (body.name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        # A hire is a CREATE (v1.295.0). The registry upserts by name, so a
        # second POST used to overwrite the whole job card (allowance, manager,
        # skills, pause) with whatever the new body carried — a re-create that
        # cost an employee its terms. Editing is the PATCH on its row.
        if d.platform.agents_registry.get(name) is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{name} already exists — edit it on its row (PATCH) "
                    "instead of re-creating it"
                ),
            )
        fields = _employee_fields(body, name=name, creating=True)
        rec = d.platform.agents_registry.register(
            name,
            body.system_prompt,
            body.tools,
            description=body.description,
            provider=body.provider,
            model=body.model,
            **fields,
        )
        return {
            "name": rec.name,
            "provider": rec.provider,
            "model": rec.model,
            "base_type": rec.base_type,
        }

    @app.get("/agents/roster")
    def agents_roster() -> dict[str, Any]:
        """The capability roster (v1.139.0): every agent that could take work
        — builtin specialists, dynamic ("custom:<slug>") and remote
        ("remote:<name>") agents — with delegability, live health, and honest
        measured stats (sample counts always included; None when unmeasured).
        Read-only composition over existing data; the dashboard renders each
        entry's ``line`` verbatim."""
        try:
            from ...agents.roster import build_roster

            entries = build_roster(d.platform)
        except Exception:  # noqa: BLE001 — an empty roster beats a 500
            entries = []
        # v1.171.0: join the roster against agent-thread activity in ONE query
        # pass (a single AgentThreads.list(), processed in memory — never N+1).
        # A failed join costs the new fields only, never the roster itself.
        activity: dict[str, tuple[str, str]] = {}
        try:
            from ...agents.threads import AgentThreads

            activity = _thread_activity(AgentThreads(d.platform.engine).list())
        except Exception:  # noqa: BLE001 — activity is a bonus, roster is the job
            activity = {}
        # Roster kind → thread-participant source (the one bridge, both ways).
        source_by_kind = {"builtin": "builtin", "dynamic": "dynamic", "remote": "remote"}
        roster = []
        for e in entries:
            try:
                bare = e.name.partition(":")[2] if ":" in e.name else e.name
                last = activity.get(f"{source_by_kind.get(e.kind, 'builtin')}:{bare}")
                roster.append(
                    {
                        "name": e.name,
                        "kind": e.kind,
                        "description": e.description,
                        "delegable": e.delegable,
                        "healthy": e.healthy,
                        "stats": e.stats,
                        "line": e.line(),
                        # v1.193.0 additive: liveness as its own field, so the
                        # rail reads a value instead of string-parsing `line`.
                        # "busy" | "queued" | "idle" | "unknown" — and "unknown"
                        # is honest: delegated children never enter the
                        # orchestrator registries, so a quiet agent is not a
                        # promise that it is free (see roster.py LIVENESS).
                        # getattr, not e.activity: the loop below turns ANY
                        # attribute error into `continue`, so an entry missing
                        # one field would drop that entry from the rail
                        # entirely — and an older or duck-typed entry missing
                        # this one would empty the WHOLE roster. "unknown" is
                        # already the honest value for "we cannot tell".
                        "activity": getattr(e, "activity", "unknown"),
                        # v1.171.0 additive (frozen contract): real activity or
                        # honest nulls — never an invented "just now".
                        "last_active": last[0] if last else None,
                        "last_message": last[1] if last else None,
                        "avatar": _avatar_url(bare),
                        # v1.180.0 additive: the chosen face, or null to derive.
                        # Keyed on the BARE name like the portrait, so a roster
                        # entry ("custom:remy") and a thread seat ("dynamic:remy")
                        # resolve to the same stored face.
                        "face": _face_override(bare),
                        # v1.295.0 additive: the day off and the allowance.
                        # getattr with defaults, same reason as `activity`
                        # above — an older or duck-typed entry missing one of
                        # these must not drop from the rail.
                        "paused": bool(getattr(e, "paused", False)),
                        "pause_reason": str(getattr(e, "pause_reason", "") or ""),
                        "allowance": getattr(e, "allowance", None),
                        "reports_to": str(getattr(e, "reports_to", "") or ""),
                        # v1.296.0 additive: the assignment-driven health
                        # dict (last run, last outcome, queued/running/
                        # blocked counts) — None when the entry has none.
                        "health": getattr(e, "health", None),
                    }
                )
            except Exception:  # noqa: BLE001 — one bad entry must not drop the rest
                continue
        return {"roster": roster}

    # --- @mention in chat (v1.150.0) --------------------------------------

    def _roster_to_participant(entry) -> dict[str, str]:
        """A roster entry as a THREAD participant.

        The two vocabularies differ by design and have to be bridged in exactly
        one place: the roster names things ``builder`` / ``custom:<slug>`` /
        ``remote:<name>`` (what a delegating model reads), while a thread stores
        ``{source, name}`` with source ``builtin|dynamic|remote``.
        """
        kind = str(getattr(entry, "kind", "") or "")
        name = str(getattr(entry, "name", "") or "")
        source = {"builtin": "builtin", "dynamic": "dynamic", "remote": "remote"}.get(
            kind, "builtin"
        )
        bare = name.split(":", 1)[1] if ":" in name else name
        return {"source": source, "name": bare, "role": "participant"}

    @app.get("/agents/mentionable")
    def agents_mentionable() -> dict[str, Any]:
        """Everyone reachable with ``@`` from chat — the picker's catalog.

        Built from the SAME roster the delegation prompt reads, so what you can
        mention and what a model can hand work to never drift apart. Offline
        remotes are INCLUDED and flagged rather than hidden: "my agent isn't in
        the list" is a worse failure than "my agent is listed as offline".
        """
        from ...agents.roster import build_roster

        try:
            entries = build_roster(d.platform)
        except Exception:  # noqa: BLE001 — an empty picker beats a 500
            entries = []
        out = []
        for e in entries:
            try:
                p = _roster_to_participant(e)
                out.append(
                    {
                        "mention": p["name"],       # what the user types after @
                        "name": e.name,             # the roster's own id
                        "kind": e.kind,
                        "source": p["source"],
                        "description": e.description,
                        "healthy": bool(e.healthy),
                        "delegable": bool(e.delegable),
                    }
                )
            except Exception:  # noqa: BLE001
                continue
        return {"agents": out}

    #: Chat-history rows the panel route accepts per call (v1.284.0). The
    #: speaker's transcript is BUDGETED downstream; this is only a transport
    #: bound so a runaway client cannot post megabytes of rows per round.
    _PANEL_HISTORY_ROWS = 600
    #: A participant key — "<source>:<name>" as the room stores it. Anything
    #: that is not this, "user" or "jarvis" is attributed to the USER: a row
    #: must not be able to spell "Iron Jarvis" (or any free label) and put
    #: words in the assistant's mouth inside the speaker's prompt.
    _PANEL_HISTORY_KEY = re.compile(r"^(builtin|dynamic|remote):[A-Za-z0-9._\- ]{1,120}$")

    def _clean_panel_history(raw: Any) -> list[dict[str, str]] | None:
        """``history`` off the wire → ``[{who, content}]``, or None when the
        client sent none (an older page / the Agents page): None keeps the
        room's own transcript, ``[]`` means "a chat with nothing before this"."""
        if raw is None:
            return None
        if not isinstance(raw, list):
            return []
        out: list[dict[str, str]] = []
        for item in raw[-_PANEL_HISTORY_ROWS:]:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            who = str(item.get("who") or "user").strip()
            if who not in ("user", "jarvis") and not _PANEL_HISTORY_KEY.match(who):
                who = "user"
            out.append({"who": who, "content": content})
        return out

    @app.post("/chat/panel")
    async def chat_panel(body: dict) -> dict[str, Any]:
        """Run one panel round for an @-mentioned chat message (v1.150.0).

        The whole feature is a CONNECTION, not new machinery: chat resolves the
        mentions against the roster, the mentioned agents join the panel bound to
        this chat thread, and ``run_round`` — which has directed rounds,
        sequential turn-taking, honest per-agent errors and live persistence
        already — does the work. Because the panel IS an ordinary agent thread,
        the conversation appears on the Agents page with no extra plumbing, which
        is the point: the inter-agent transcript lives where agents live.

        v1.284.0 — THE AGENT REMEMBERS, STAYS ADDRESSED, AND CAN ACT. The body
        also takes ``history`` (the chat so far — the speaker's transcript,
        budgeted; see ``AgentThreads.run_round``), ``panel_thread_id`` (the room
        an earlier round answered with — adopted when this chat has no room
        yet, so a new chat's first two rounds share one room), ``mentions``
        (addressees for a follow-up whose text carries no "@" — chat's sticky
        "still talking to builder"), and ``hands`` (``true`` = do it in a real
        session; ``false`` = always a round; absent = decide from the ask).
        When ONE local agent is addressed and the ask would arm a file-changing
        tool (``threads.needs_hands``), the answer is ``mode: "session"`` with
        the roster ``target`` — the page then opens the SAME tooled session a
        chat escalation does, and the result (files included) lands in chat.
        A panelist cannot make a PDF, and a round that says so is not the
        answer the user asked for. Rounds answer ``mode: "panel"``.
        """
        from ...agents.roster import resolve_target
        from ...agents.threads import (
            AgentThreads,
            clean_participants,
            needs_hands,
            parse_mentions,
        )

        message = str(body.get("message") or "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="message is required")
        chat_thread_id = str(body.get("chat_thread_id") or "").strip()
        panel_thread_id = str(body.get("panel_thread_id") or "").strip()
        history = _clean_panel_history(body.get("history"))
        hands = body.get("hands")
        hands = hands if isinstance(hands, bool) else None

        mentions = parse_mentions(message)
        if not mentions:
            # A follow-up addressed outside the text (v1.284.0). Same token
            # rule as the text form: lower-cased, "@" and quotes shed.
            raw = body.get("mentions")
            if isinstance(raw, list):
                seen: set[str] = set()
                for item in raw:
                    tok = str(item or "").strip().lstrip("@").strip().strip('"').lower()
                    if tok and tok not in seen:
                        seen.add(tok)
                        mentions.append(tok)
        if not mentions:
            raise HTTPException(
                status_code=400, detail="no @mentions in this message"
            )
        resolved: list[dict[str, str]] = []
        entries_found: list[Any] = []
        unknown: list[str] = []
        for token in mentions:
            # Conversation, not delegation: a mention of a coordinator
            # (planner/supervisor) joins the panel even though delegated WORK
            # to it is refused (v1.166.0 — planner carries `delegate` now).
            entry = resolve_target(d.platform, token, require_delegable=False)
            if entry is None:
                # Named somebody who isn't reachable. Reported, never silently
                # dropped — a mention that quietly does nothing is how a user
                # concludes the feature is broken.
                unknown.append(token)
                continue
            resolved.append(_roster_to_participant(entry))
            entries_found.append(entry)
        if not resolved:
            raise HTTPException(
                status_code=404,
                detail=f"no agent matched {', '.join('@' + u for u in unknown)}",
            )

        # WORK GOES TO A SESSION (v1.284.0). One LOCAL addressee only: a remote
        # has no session-shaped run here, and a multi-agent mention is a
        # conversation by construction. `hands` is the page's override (the
        # "Have builder do this" chip sends true; the Agents page never sends).
        if len(resolved) == 1 and resolved[0]["source"] in ("builtin", "dynamic"):
            if hands is None:
                tools = await asyncio.to_thread(needs_hands, message)
            else:
                tools = ["requested"] if hands else []
            if tools:
                entry = entries_found[0]
                bare = resolved[0]["name"]
                who = f"{resolved[0]['source']}:{bare}"
                return {
                    "mode": "session",
                    "target": entry.name,
                    "who": who,
                    "tools": tools,
                    "reason": (
                        f"this is work, not a question — {bare} is doing it in a "
                        "real session, with tools"
                    ),
                    "unknown_mentions": unknown,
                }

        threads = AgentThreads(d.platform.engine)
        # THE REPLY COMES HOME (v1.345.0): ``thread_id`` is the SAVED chat the
        # page asked from (the page saves a new chat first, so it is always
        # sent). The room is bound to THAT chat — ``for_chat`` is the one
        # binding door — and the round's replies are appended to it below
        # BEFORE the answer, so a reply that lands while the user is in
        # another chat is still in the chat it was asked in. No ``thread_id``
        # = byte-identical to v1.344.0 (pinned).
        saved_thread_id = str(body.get("thread_id") or "").strip()
        rec = threads.for_chat(
            saved_thread_id or chat_thread_id, title=message[:60], adopt=panel_thread_id
        )
        try:
            threads.add_participants(rec.id, clean_participants(resolved))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        # LOCAL ONLY (v1.332.0): the round runs under the chat's scope — the
        # body's ``local_only``, or the saved chat (``thread_id``, else
        # ``chat_thread_id``) says so. A seat whose model is off this network
        # sits the round out with one plain line (``AgentThreads.run_round``).
        from ...providers import egress as _egress

        local_only = await _egress.effective_local_only(
            d.platform.engine,
            body.get("local_only") is True,
            str(body.get("thread_id") or "").strip() or chat_thread_id,
        )
        try:
            with _egress.local_only_scope(local_only):
                round_out = await threads.run_round(
                    rec.id,
                    message,
                    d,
                    directed=[p["name"] for p in resolved],
                    chat_history=history,
                )
        except KeyError:
            raise HTTPException(status_code=404, detail="panel thread vanished")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        answer: dict[str, Any] = {
            "mode": "panel",
            "thread_id": rec.id,
            "unknown_mentions": unknown,
            **round_out,
        }
        if saved_thread_id:
            lines = panel_reply_lines(round_out, rec.id)
            appended = await asyncio.to_thread(
                append_to_chat_thread,
                d.platform.engine,
                saved_thread_id,
                lines,
                event_bus=d.platform.event_bus,
            )
            answer["appended"] = bool(appended.get("ok")) and int(appended.get("appended") or 0) > 0
            answer["updated_at"] = appended.get("updated_at")
        return answer

    # --- Remote agents (run elsewhere) ------------------------------------
    # Registered BEFORE the /agents/{name} routes below so the literal
    # /agents/remote path is never swallowed by the {name} param match.

    def _remote_reg() -> RemoteAgentRegistry:
        return RemoteAgentRegistry(d.platform.engine)

    def _remote_view(r) -> dict[str, Any]:
        # STATUS only — never the token / secret value.
        return {
            "name": r.name,
            "base_url": r.base_url,
            "kind": r.kind,
            "model": r.model or "",
            "enabled": r.enabled,
            "timeout_s": r.timeout_s,
            "has_credential": bool(r.secret_name),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            # v1.285.0: whether the remote may message back, and where it posts.
            # The inbound TOKEN is never here — it was shown once, at enable.
            "inbound_enabled": bool(getattr(r, "inbound_enabled", False)),
            "inbound_url": str(getattr(r, "inbound_url", "") or ""),
        }

    @app.get("/agents/remote")
    def list_remote_agents() -> dict[str, Any]:
        return {"agents": [_remote_view(r) for r in _remote_reg().list()]}

    @app.post("/agents/remote")
    def add_remote_agent(body: RemoteAgentCreate) -> dict[str, Any]:
        import re as _re

        from ...agents.remote import KINDS

        name = (body.name or "").strip()
        if not _re.match(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$", name):
            raise HTTPException(status_code=400, detail="invalid remote agent name")
        if not (body.base_url or "").strip():
            raise HTTPException(status_code=400, detail="base_url is required")
        if body.kind not in KINDS:
            raise HTTPException(
                status_code=400, detail=f"kind must be one of {', '.join(KINDS)}"
            )
        secret_name: str | None = None
        if (body.token or "").strip():
            secret_name = "remote_agent_" + name
            d.platform.secrets.set(secret_name, body.token.strip(), kind="token")
        rec = _remote_reg().upsert(
            name,
            body.base_url.strip(),
            body.kind,
            secret_name=secret_name,
            model=(body.model or "").strip() or None,
            enabled=body.enabled,
            # max(1, …) as PATCH does (v1.288.0): the value is now a TOTAL
            # deadline (asyncio.timeout), and a stored negative expires at once.
            timeout_s=max(1, int(body.timeout_s or 120)),
        )
        return _remote_view(rec)

    @app.patch("/agents/remote/{name}")
    def edit_remote_agent(name: str, body: RemoteAgentPatch) -> dict[str, Any]:
        """Fix one thing about a registered remote agent (v1.164.0).

        Before this the only options were Test and Delete, so a mistyped base
        URL meant deleting and re-entering the whole record — including a
        bearer token the user may no longer have to hand.

        Deliberately NOT a re-POST of the create body: that path assigns every
        column, and the UI cannot prefill the token (stored encrypted, never
        returned), so an edit would send an empty token and silently drop a
        working credential. Here an omitted ``token`` keeps the stored one and
        only ``clear_token`` removes it.
        """
        from ...agents.remote import KINDS

        reg = _remote_reg()
        rec = reg.get(name)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such remote agent")

        fields: dict[str, Any] = {}
        if body.base_url is not None:
            base_url = body.base_url.strip()
            if not base_url:
                raise HTTPException(status_code=400, detail="base_url cannot be empty")
            fields["base_url"] = base_url
        if body.kind is not None:
            if body.kind not in KINDS:
                raise HTTPException(
                    status_code=400, detail=f"kind must be one of {', '.join(KINDS)}"
                )
            fields["kind"] = body.kind
        if body.model is not None:
            fields["model"] = body.model.strip() or None
        if body.enabled is not None:
            fields["enabled"] = bool(body.enabled)
        if body.timeout_s is not None:
            fields["timeout_s"] = max(1, int(body.timeout_s))
        if body.inbound_url is not None:
            inbound_url = body.inbound_url.strip()
            if not inbound_url.lower().startswith(("http://", "https://")):
                raise HTTPException(
                    status_code=400, detail="inbound_url must start with http:// or https://"
                )
            fields["inbound_url"] = inbound_url

        # CREDENTIAL: three distinct intents, and conflating any two of them
        # loses a secret the user cannot retype.
        if body.clear_token:
            if rec.secret_name:
                try:
                    d.platform.secrets.delete(rec.secret_name)
                except Exception:  # noqa: BLE001 — an absent secret is fine
                    pass
            fields["secret_name"] = None
        elif (body.token or "").strip():
            secret_name = "remote_agent_" + name
            d.platform.secrets.set(secret_name, body.token.strip(), kind="token")
            fields["secret_name"] = secret_name
        # else: untouched. This is the whole reason the endpoint exists.

        if not fields:
            return _remote_view(rec)  # nothing asked for; not an error
        updated = reg.update(name, **fields)
        if updated is None:  # raced with a delete
            raise HTTPException(status_code=404, detail="no such remote agent")
        return _remote_view(updated)

    @app.delete("/agents/remote/{name}")
    def delete_remote_agent(name: str) -> dict[str, Any]:
        reg = _remote_reg()
        rec = reg.get(name)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such remote agent")
        # Drop its vault secret too (best-effort — an absent secret is fine).
        if rec.secret_name:
            try:
                d.platform.secrets.delete(rec.secret_name)
            except Exception:  # noqa: BLE001
                pass
        reg.remove(name)
        return {"removed": name}

    @app.post("/agents/remote/{name}/test")
    async def test_remote_agent(name: str) -> dict[str, Any]:
        reg = _remote_reg()
        rec = reg.get(name)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such remote agent")
        return await reg.test(rec, d.platform.secrets.get)

    @app.post("/agents/remote/{name}/run")
    async def run_remote_agent(name: str, body: RemoteAgentRun) -> dict[str, Any]:
        reg = _remote_reg()
        rec = reg.get(name)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such remote agent")
        if not rec.enabled:
            raise HTTPException(status_code=400, detail="remote agent is disabled")
        res = await reg.run(rec, body.task or "", d.platform.secrets.get)
        if not res.get("ok"):
            # 424 Failed Dependency — the remote agent itself failed to answer.
            raise HTTPException(status_code=424, detail=res.get("detail") or "remote call failed")
        return {"result": res.get("result") or "", "agent": name, "kind": rec.kind}

    # --- A remote agent messages back (v1.285.0) ----------------------------
    # The user's question: "what is stopping me from interacting with my
    # remote agents the way I can with Slack?" — nothing could come BACK. These
    # three routes are the way in: the user enables it (a token minted once,
    # shown once), the remote posts into the room it sits in with that token,
    # and the line lands where the user is — the room, the desktop chat that
    # mirrors it, and the phone when the room belongs to a phone conversation.

    #: Inbound posts per remote per minute before a 429 — a chatty remote is
    #: fine, a runaway one is not.
    _INBOUND_RATE_MAX = 60
    _INBOUND_RATE_WINDOW_S = 60.0
    _INBOUND_MAX_BODY_BYTES = 96 * 1024 * 1024
    #: Posts that CARRY FILES are rarer and heavier: a tighter per-minute cap and
    #: a per-agent, per-day byte budget for the inbox — nothing prunes it, so
    #: "60 posts × 60 MB a minute" must not be within the rules.
    _INBOUND_FILE_POSTS_MAX = 10
    _INBOUND_INBOX_DAILY_BYTES = 512 * 1024 * 1024
    #: Phone pushes per agent per 10 minutes — the room keeps every line; the
    #: phone does not have to buzz for each one.
    _INBOUND_PHONE_MAX = 20
    _INBOUND_PHONE_WINDOW_S = 600.0
    _inbound_hits: dict[str, deque] = {}
    _inbound_file_hits: dict[str, deque] = {}
    _inbound_phone_hits: dict[str, deque] = {}
    _inbound_inbox_bytes: dict[str, tuple[str, int]] = {}  # name -> (day, bytes)

    def _windowed_ok(table: dict[str, deque], name: str, limit: int, window_s: float) -> bool:
        now = time.monotonic()
        q = table.setdefault(name, deque())
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True

    def _inbound_rate_ok(name: str) -> bool:
        return _windowed_ok(_inbound_hits, name, _INBOUND_RATE_MAX, _INBOUND_RATE_WINDOW_S)

    def _inbound_inbox_budget_ok(name: str, more: int) -> bool:
        """Charge ``more`` bytes to today's inbox budget for ``name``; False
        when it would overflow (nothing is charged then)."""
        day = time.strftime("%Y-%m-%d", time.gmtime())
        prev_day, used = _inbound_inbox_bytes.get(name, (day, 0))
        if prev_day != day:
            used = 0
        if used + more > _INBOUND_INBOX_DAILY_BYTES:
            return False
        _inbound_inbox_bytes[name] = (day, used + more)
        return True

    def _lan_address() -> str:
        """This machine's LAN IPv4, best-effort (a UDP socket 'connect' picks
        the route; nothing is sent). "" when there is none to find."""
        import socket

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("10.255.255.255", 1))
                return str(s.getsockname()[0])
            finally:
                s.close()
        except Exception:  # noqa: BLE001 — offline / no route
            return ""

    def _inbound_url_for(request: Request, name: str) -> str:
        """The address a remote is told to post to. The DASHBOARD reaches this
        daemon on 127.0.0.1 — an address no other machine can use — so a
        loopback request derives the LAN address of this machine instead, on
        the same port; a request that already came over a real host keeps it.
        Editable afterwards (PATCH inbound_url) for tunnels and proxies."""
        from ..auth import _host_label

        host = (request.headers.get("host") or "").strip()
        scheme = request.url.scheme or "http"
        if not host:
            return ""
        label = _host_label(host).lower()
        if label in ("127.0.0.1", "localhost", "::1", "[::1]", "testserver"):
            lan = _lan_address()
            if lan:
                port = host.rsplit(":", 1)[1] if host.count(":") == 1 else ""
                host = f"{lan}:{port}" if port else lan
        return f"{scheme}://{host}/agents/remote/{quote(name, safe='')}/inbound"

    def _inbound_reachability(url: str) -> dict[str, Any]:
        """Whether THIS daemon would even accept a request addressed to
        ``url``'s host, and what it takes if not. The packaged app binds the
        daemon to 127.0.0.1 and the Host guard admits loopback only, so a
        remote on another machine needs two things the user must do on purpose
        — said here, in the same answer as the token, never discovered as a
        bare 403 in the remote's logs."""
        from urllib.parse import urlparse

        from ..auth import _host_ok

        try:
            host = urlparse(url).netloc or ""
        except Exception:  # noqa: BLE001
            host = ""
        allowed = bool(host) and _host_ok(host)
        note = ""
        if not allowed:
            label = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
            note = (
                f"This daemon currently accepts requests addressed to 127.0.0.1 or "
                f"localhost only, so a remote on another machine cannot reach it yet. "
                f"To allow it: run the daemon listening on your LAN address "
                f"(ironjarvis serve --host {label or '<your LAN IP>'}) and set "
                f"IRONJARVIS_HOST_ALLOWLIST={label or '<your LAN IP>'} before starting it "
                f"— or point a tunnel or reverse proxy at 127.0.0.1:8787 and paste its "
                f"address here (Edit → inbound URL). A remote running on THIS machine "
                f"can post to http://127.0.0.1:8787 as is."
            )
        return {"host_allowed": allowed, "note": note}

    def _bearer_of(request: Request) -> str:
        header = request.headers.get("authorization") or ""
        return header[7:].strip() if header.lower().startswith("bearer ") else ""

    def _last_inbound_at(threads, room_id: str, who: str) -> str:
        """The ``at`` of the room's newest inbound entry by ``who`` — the key
        the page's ``mirrorRoomInbound`` dedupes on (v1.345.0). "" when the
        room cannot be read; never raises (a mirror must not fail the post)."""
        try:
            rec = threads.get(room_id)
            msgs = json.loads(rec.messages_json or "[]") if rec is not None else []
            for e in reversed(msgs if isinstance(msgs, list) else []):
                if isinstance(e, dict) and e.get("inbound") and e.get("who") == who:
                    return str(e.get("at") or "")
        except Exception:  # noqa: BLE001
            log.warning("remote inbound: could not read the room's last entry", exc_info=True)
        return ""

    @app.post("/agents/remote/{name}/inbound/enable")
    def enable_remote_inbound(
        name: str, request: Request, body: RemoteInboundEnable | None = None
    ) -> dict[str, Any]:
        """Turn inbound ON for a remote: mint (or ROTATE) its inbound token and
        return it ONCE — it lives in the vault afterwards and no listing ever
        carries it again. The remote sends it as ``Authorization: Bearer``."""
        reg = _remote_reg()
        rec = reg.get(name)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such remote agent")
        url = ((body.url if body else "") or "").strip() or _inbound_url_for(request, name)
        if url and not url.lower().startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="url must start with http:// or https://")
        out = reg.enable_inbound(name, d.platform.secrets.set, url=url)
        if out is None:
            raise HTTPException(status_code=404, detail="no such remote agent")
        row, token = out
        return {
            **_remote_view(row),
            "token": token,
            "url": row.inbound_url,
            "reachable": _inbound_reachability(row.inbound_url),
        }

    @app.post("/agents/remote/{name}/inbound/disable")
    def disable_remote_inbound(name: str) -> dict[str, Any]:
        reg = _remote_reg()
        row = reg.disable_inbound(name, d.platform.secrets.delete)
        if row is None:
            raise HTTPException(status_code=404, detail="no such remote agent")
        return _remote_view(row)

    @app.post("/agents/remote/{name}/inbound")
    async def remote_inbound(name: str, request: Request) -> dict[str, Any]:
        """A remote agent posts INTO a conversation (v1.285.0).

        Token-exempt in the middleware (``auth._is_exempt``) because the remote
        holds its OWN inbound token, never the install bearer — verified here
        FAIL-CLOSED: inbound off / no vault entry → 403, wrong or missing
        token → 401. The remote may only post into a room it is a participant
        of (403 otherwise), so a token for one agent cannot write as another.
        Body: ``{conversation_id, message, kind?, files?}`` — ``kind`` one of
        message / progress / question / done; ``files`` in the v1.157.0 shape,
        landed under ``<home>/remote-inbox/<agent>/`` through the same trust
        boundary the delegate tool uses (names sanitised, sizes capped, URLs on
        the remote's own host only). The line lands in the room (an
        ``AGENT_THREAD_UPDATED`` like any round entry, plus ``REMOTE_MESSAGE``)
        and — when the room belongs to a PHONE conversation — on that thread
        and that phone, named.
        """
        from ...agents import remote_files as rf
        from ...agents.remote import INBOUND_KINDS, INBOUND_MESSAGE_CHARS
        from ...agents.threads import AgentThreads

        reg = _remote_reg()
        # DB + vault reads off the loop: a third party drives this route.
        rec = await asyncio.to_thread(reg.get, name)
        # A DISABLED remote is disabled in both directions: the user's one
        # switch must silence it, not leave it writing into conversations.
        if (
            rec is None
            or not bool(getattr(rec, "enabled", True))
            or not bool(getattr(rec, "inbound_enabled", False))
        ):
            raise HTTPException(
                status_code=403, detail="inbound is not enabled for this remote agent"
            )
        ok = await asyncio.to_thread(
            reg.verify_inbound, rec, _bearer_of(request), d.platform.secrets.get
        )
        if not ok:
            raise HTTPException(status_code=401, detail="invalid inbound token")
        if not _inbound_rate_ok(name):
            raise HTTPException(status_code=429, detail="too many inbound messages — slow down")
        # Refuse an oversized body BEFORE parsing it: 20 files × 25 MB in base64
        # is the legitimate ceiling (~80 MB); anything past that is not a message.
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            declared = 0
        if declared > _INBOUND_MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="inbound body too large")
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 — not JSON
            raise HTTPException(status_code=400, detail="body must be JSON")
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="body must be a JSON object")
        conversation_id = str(body.get("conversation_id") or "").strip()
        message = str(body.get("message") or "").strip()
        kind = str(body.get("kind") or "message").strip().lower()
        if kind not in INBOUND_KINDS:
            raise HTTPException(
                status_code=400, detail=f"kind must be one of {', '.join(INBOUND_KINDS)}"
            )
        if not conversation_id:
            raise HTTPException(status_code=400, detail="conversation_id is required")
        raw_files = body.get("files")
        if not message and not raw_files:
            raise HTTPException(status_code=400, detail="message (or files) is required")
        if len(message) > INBOUND_MESSAGE_CHARS:
            raise HTTPException(
                status_code=413, detail=f"message longer than {INBOUND_MESSAGE_CHARS} characters"
            )
        threads = AgentThreads(d.platform.engine)
        room = await asyncio.to_thread(threads.get, conversation_id)
        who = f"remote:{name}"
        try:
            participants = json.loads(room.participants_json or "[]") if room is not None else []
        except Exception:  # noqa: BLE001
            participants = []
        # ONE answer for "no such room" and "not your room": a token holder
        # must not be able to enumerate which conversation ids exist.
        if room is None or not any(
            str(p.get("key") or "") == who for p in participants if isinstance(p, dict)
        ):
            raise HTTPException(
                status_code=403, detail="this remote agent is not a participant of that conversation"
            )

        # FILES — the same trust boundary the delegate tool uses; landed in a
        # per-agent inbox, never in a workspace the remote did not earn.
        documents: list[str] = []
        notes: list[str] = []
        entries = rf.parse_files({"files": raw_files}) if raw_files else []
        if entries:
            if not _windowed_ok(
                _inbound_file_hits, name, _INBOUND_FILE_POSTS_MAX, _INBOUND_RATE_WINDOW_S
            ):
                raise HTTPException(
                    status_code=429, detail="too many file deliveries — slow down"
                )
            import httpx

            from ...tools.base import safe_path

            async def _fetch(url: str) -> bytes:
                # STREAMED, aborted past the cap — never buffer a body whose
                # size the other machine chose.
                async with httpx.AsyncClient(timeout=min(rec.timeout_s or 120, 60)) as c:
                    async with c.stream("GET", url) as r:
                        r.raise_for_status()
                        got = bytearray()
                        async for chunk in r.aiter_bytes():
                            got.extend(chunk)
                            if len(got) > rf.MAX_FILE_BYTES:
                                raise ValueError("too large")
                        return bytes(got)

            files, notes = await rf.collect(entries, base_url=rec.base_url or "", fetch=_fetch)
            total = sum(len(b) for _n, b in files)
            if files and not _inbound_inbox_budget_ok(name, total):
                notes.append(
                    f"{len(files)} file(s) not saved — {name} has used today's inbox "
                    f"budget ({_INBOUND_INBOX_DAILY_BYTES // (1024 * 1024)} MB)"
                )
                files = []
            inbox = d.platform.config.home / "remote-inbox" / rf.safe_filename(name, fallback="remote")

            def _land() -> list[str]:
                inbox.mkdir(parents=True, exist_ok=True)
                saved: list[str] = []
                for fname, blob in files:
                    try:
                        target = rf.unique_path(inbox, fname)
                        # The SECOND lock (v1.157.0's invariant): even a name
                        # that survived sanitising cannot resolve outside the
                        # inbox.
                        checked = safe_path(inbox, target.name)
                        checked.write_bytes(blob)
                        saved.append(str(checked.resolve()))
                    except Exception as exc:  # noqa: BLE001
                        notes.append(f"{fname}: could not be written ({type(exc).__name__})")
                return saved

            documents = await asyncio.to_thread(_land)

        text = message
        if documents:
            listed = "\n".join(f"- {p}" for p in documents)
            text = (text + "\n\n" if text else "") + f"Files from {name}:\n{listed}"
        if notes:
            text += "\n\nNot saved:\n" + "\n".join(f"- {n}" for n in notes)

        try:
            count = await threads.add_inbound_entry(
                room.id, who, text, d, kind=kind, documents=documents or None
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="no such conversation")
        try:
            await d.platform.event_bus.publish(
                EventType.REMOTE_MESSAGE,
                {"agent": name, "thread_id": room.id, "kind": kind, "documents": documents},
            )
        except Exception:  # noqa: BLE001 — an event must never fail the post
            pass

        # A PHONE conversation hears it too — on the thread and on the phone.
        phoned = False
        phone_rate_limited = False
        chat_thread_id = str(getattr(room, "chat_thread_id", "") or "")
        store = getattr(d, "comm_thread_store", None)
        poller = getattr(d, "inbound_poller", None)
        # Which kind of chat the room belongs to — NEVER both paths (v1.345.0):
        # a daemon-owned (phone) thread keeps the comm mirror below; any other
        # ChatThreadRecord is a chat the user asked from, and the line lands
        # there through the one chat writer (a remote that reports back later
        # comes home to the chat it was asked in).
        daemon_owned = bool(
            chat_thread_id
            and store is not None
            and await asyncio.to_thread(store.is_daemon_owned, chat_thread_id)
        )
        homed = False
        if chat_thread_id and not daemon_owned:
            # The room entry's ``at`` rides as ``panelAt`` — the page's
            # ``mirrorRoomInbound`` dedupes on it, so a line the daemon
            # appended and the page's live mirror never show twice.
            panel_at = await asyncio.to_thread(_last_inbound_at, threads, room.id, who)
            line: dict[str, Any] = {
                "role": "assistant",
                "content": text,
                "panelWho": who,
                "panelThreadId": room.id,
                "panelKind": kind,
            }
            if panel_at:
                line["panelAt"] = panel_at
            if documents:
                line["documents"] = list(documents)
            homed_out = await asyncio.to_thread(
                append_to_chat_thread,
                d.platform.engine,
                chat_thread_id,
                [line],
                event_bus=d.platform.event_bus,
                who=who,
            )
            homed = bool(homed_out.get("ok")) and int(homed_out.get("appended") or 0) > 0
        if daemon_owned:
            try:
                await asyncio.to_thread(
                    store.append,
                    chat_thread_id,
                    "assistant",
                    text,
                    extra={"panelWho": who, "panelKind": kind, "documents": documents or None},
                )
            except Exception:  # noqa: BLE001 — the room already has it
                log.warning("remote inbound: comm thread append failed", exc_info=True)
            pair = await asyncio.to_thread(store.thread_channel, chat_thread_id)
            if pair and poller is not None:
                channel_name, sender_id = pair
                ch = next(
                    (c for n, c in poller.inbound_channels() if n == channel_name), None
                )
                if ch is not None and not _windowed_ok(
                    _inbound_phone_hits, name, _INBOUND_PHONE_MAX, _INBOUND_PHONE_WINDOW_S
                ):
                    # The thread keeps the line; the phone stops buzzing.
                    phone_rate_limited = True
                elif ch is not None:
                    try:
                        phoned = bool(
                            await poller.send_chunked(
                                ch, text, chat_id=sender_id, prefix=f"{name}: "
                            )
                        )
                    except Exception:  # noqa: BLE001
                        log.warning("remote inbound: phone send failed", exc_info=True)
        return {
            "ok": True,
            "thread_id": room.id,
            "entries": count,
            "kind": kind,
            # Names only — the other machine has no business learning where
            # this user's home folder is.
            "documents": [Path(p).name for p in documents],
            "skipped": notes,
            "phoned": phoned,
            "phone_rate_limited": phone_rate_limited,
            # v1.345.0: the line also landed in the chat the room was asked from.
            "homed": homed,
        }

    # --- The folder (v1.297.0) ----------------------------------------------
    # `<home>/agents/<slug>/`: AGENTS.md (the instructions — the SAME text as
    # the row's system_prompt; an edit here goes THROUGH the registry so the
    # DB mirror and the revision both land), NOTES.md (the notebook) and
    # revisions/. Every disk read is in a sync `def` (threadpool). After the
    # /agents/remote/* block, BEFORE the /agents/{name} catch-alls.

    def _agent_files():
        files = getattr(d.platform.agents_registry, "files", None)
        if files is None:
            raise HTTPException(status_code=503, detail="agent folders are not available")
        return files

    def _folder_record(name: str):
        """The custom agent's record, or the right 404: a builtin type has no
        folder of its own, an unknown name has nothing."""
        rec = d.platform.agents_registry.get(name)
        if rec is not None:
            return rec
        try:
            AgentType(name)
        except ValueError:
            raise HTTPException(status_code=404, detail="unknown agent") from None
        raise HTTPException(
            status_code=404, detail="only your own agents have a folder"
        )

    def _files_view(rec) -> dict[str, Any]:
        files = _agent_files()
        instructions = files.read_instructions(rec.name)
        if instructions is None:
            # A folder that has not been written yet still SHOWS the row's
            # text — the DB is the truth; the file is the copy.
            instructions = rec.system_prompt or ""
        return {
            "name": rec.name,
            "instructions": instructions,
            "notes": files.read_notes(rec.name),
            "revisions": files.list_revisions(rec.name),
            "folder": str(files.folder(rec.name)),
        }

    @app.get("/agents/{name}/files")
    def agent_files(name: str) -> dict[str, Any]:
        """``{name, instructions, notes, revisions: [{id, at, reason, bytes}],
        folder}`` for a custom agent; 404 for a builtin or an unknown name."""
        return _files_view(_folder_record(name))

    @app.put("/agents/{name}/files/instructions")
    def put_agent_instructions(name: str, body: AgentFilesInstructions) -> dict[str, Any]:
        """The same effect as ``PATCH {system_prompt}``, THROUGH the registry:
        the row changes, AGENTS.md follows, and the previous text is kept as
        a revision tagged ``reason``. Answers the files view."""
        rec = _folder_record(name)
        _agent_files()
        reason = " ".join(str(body.reason or "").split()) or "edit"
        updated = d.platform.agents_registry.register(
            rec.name,
            body.text,
            _json_list(rec.tools_json),
            base_type=rec.base_type,
            description=rec.description,
            provider=rec.provider,
            model=rec.model,
            prompt_reason=reason,
        )
        return _files_view(updated)

    @app.put("/agents/{name}/files/notes")
    def put_agent_notes(name: str, body: AgentFilesNotes) -> dict[str, Any]:
        rec = _folder_record(name)
        _agent_files().write_notes(rec.name, body.text)
        return _files_view(rec)

    @app.get("/agents/{name}/files/revisions/{revision_id}")
    def agent_files_revision(name: str, revision_id: str) -> dict[str, Any]:
        rec = _folder_record(name)
        found = _agent_files().read_revision(rec.name, revision_id)
        if found is None:
            raise HTTPException(status_code=404, detail="unknown revision")
        return found

    @app.post("/agents/{name}/files/revisions/{revision_id}/restore")
    def restore_agent_files_revision(name: str, revision_id: str) -> dict[str, Any]:
        """Put a revision's text back — through the registry, so the row and
        the file agree and the text being replaced is kept as a revision."""
        rec = _folder_record(name)
        found = _agent_files().read_revision(rec.name, revision_id)
        if found is None:
            raise HTTPException(status_code=404, detail="unknown revision")
        updated = d.platform.agents_registry.register(
            rec.name,
            found["text"],
            _json_list(rec.tools_json),
            base_type=rec.base_type,
            description=rec.description,
            provider=rec.provider,
            model=rec.model,
            prompt_reason=f"restore {found['id']}",
        )
        return _files_view(updated)

    @app.post("/agents/{name}/files/open")
    def open_agent_folder(name: str) -> dict[str, Any]:
        """Show the folder in the OS file manager (it is created if missing,
        so the user never lands on a 'path not found')."""
        rec = _folder_record(name)
        folder = _agent_files().folder(rec.name)
        folder.mkdir(parents=True, exist_ok=True)
        try:
            _open_folder(str(folder))
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"could not open the folder: {exc}") from exc
        return {"opened": str(folder)}

    # --- The inbox (v1.296.0) -----------------------------------------------
    # What is waiting for ONE agent: its assignment queue and health. After
    # the /agents/remote/* block (same reason as the portraits below) and
    # BEFORE the /agents/{name} catch-alls, or PATCH/DELETE would swallow it.

    @app.get("/agents/{name}/inbox")
    def agent_inbox(name: str) -> dict[str, Any]:
        """``{assignee, inbox: {queued, claimed, running, blocked, recent},
        health}`` for a builtin type or a custom agent (bare name or
        ``custom:<name>``); 404 for a name nobody answers to."""
        from .assignments import resolve_assignee

        try:
            assignee = resolve_assignee(d, name)
        except HTTPException as exc:
            raise HTTPException(status_code=404, detail=str(exc.detail))
        store = getattr(d.platform, "assignments", None)
        if store is None:
            raise HTTPException(
                status_code=503,
                detail="assignments are not available on this daemon — restart it",
            )
        raw = store.inbox(assignee) or {}
        inbox = {
            key: [store.as_dict(r) for r in (raw.get(key) or [])]
            for key in ("queued", "claimed", "running", "blocked", "recent")
        }
        try:
            from ...assignments.health import agent_health

            health = agent_health(d.platform.engine, store, assignee)
        except Exception:  # noqa: BLE001 — the inbox is the job; health is a bonus
            log.debug("agent_health failed for %s", assignee, exc_info=True)
            health = None
        return {"assignee": assignee, "inbox": inbox, "health": health}

    # --- Agent portraits (v1.171.0) -----------------------------------------
    # Registered AFTER the /agents/remote/* block on purpose: /agents/remote/…
    # paths keep their priority for the (pathological) agent name "remote".
    # Works for BUILTIN names and dynamic slugs alike — storage is by name and
    # the file's existence is the whole record.

    @app.get("/agents/{name}/avatar")
    def get_agent_avatar(name: str):
        """The stored portrait's bytes — INLINE disposition (the v1.166 lesson:
        an <img> must render it, not trigger a download). 404 when none."""
        from fastapi.responses import FileResponse

        name = (name or "").strip()
        p = _avatar_path(name)
        if not name or not p.is_file():
            raise HTTPException(status_code=404, detail="no stored portrait for this agent")
        return FileResponse(
            str(p),
            media_type="image/png",
            filename=p.name,
            content_disposition_type="inline",
        )

    @app.post("/agents/{name}/avatar")
    def set_agent_avatar(name: str, body: dict) -> dict[str, Any]:
        """Store a portrait: upload (``image_b64``) or generate (``generate``).

        Upload: ≤2MB decoded, PNG/JPEG/WebP sniffed by CONTENT, normalized to
        a ≤512px PNG. Generate: through the platform's existing Pixio image
        path — and when no image model is configured, an HONEST 409 naming
        what's missing. There is deliberately NO placeholder image: a face
        that pretends a portrait exists is the dishonest kind of warmth.

        Sync handler on purpose — the (possibly minutes-long) generation runs
        in FastAPI's threadpool, never on the event loop.
        """
        import base64

        name = (name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="agent name is required")
        image_b64 = str(body.get("image_b64") or "")
        generate = bool(body.get("generate"))
        if bool(image_b64) == generate:
            raise HTTPException(
                status_code=400, detail="give exactly one of image_b64 or generate"
            )

        if image_b64:
            # Reject on the base64 length BEFORE decoding (4/3 expansion) so an
            # oversized body never gets buffered — the uploads-route pattern.
            approx = (len(image_b64) * 3) // 4
            if approx > _AVATAR_MAX_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"portrait too large (~{approx // (1024 * 1024)} MB); "
                        "limit is 2 MB"
                    ),
                )
            try:
                data = base64.b64decode(image_b64, validate=False)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=400, detail=f"invalid base64: {exc}")
            if len(data) > _AVATAR_MAX_BYTES:
                raise HTTPException(
                    status_code=413, detail="portrait too large; limit is 2 MB"
                )
            source = "upload"
        else:
            key = _image_key()
            if not key:
                # The honest degradation the wave exists for: say what's
                # missing, never hand back a placeholder.
                raise HTTPException(status_code=409, detail=_NO_IMAGE_MODEL)
            purpose = ""
            try:
                rec = d.platform.agents_registry.get(name)
                if rec is not None and (rec.description or "").strip():
                    purpose = " ".join(str(rec.description).split())[:300]
            except Exception:  # noqa: BLE001 — a purposeless prompt still works
                purpose = ""
            prompt = _AVATAR_PROMPT.format(
                name=name, purpose=f"Its purpose: {purpose}. " if purpose else ""
            )
            try:
                data = _generate_avatar_bytes(key, prompt)
            except RuntimeError as exc:
                # Configured but failing is a dependency failure, not a
                # conflict — mirrors /creative/publish's 424.
                raise HTTPException(status_code=424, detail=str(exc))
            source = "generated"

        if _sniff_image(data) is None:
            raise HTTPException(
                status_code=415,
                detail=(
                    "the image model returned data that is not PNG/JPEG/WebP"
                    if source == "generated"
                    else "not a PNG/JPEG/WebP image"
                ),
            )
        try:
            png = _normalize_avatar_png(data)
        except Exception as exc:  # noqa: BLE001 — decode failure, honest 415
            raise HTTPException(
                status_code=415, detail=f"could not decode the image: {exc}"
            )
        p = _avatar_path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Atomic publish (the creative-thumbs/backup convention): write a
        # unique temp file beside the target, then os.replace. A concurrent
        # GET (the roster's <img> refetch) must never be served a half-written
        # PNG, and a crash mid-write must not leave a corrupt file that then
        # serves as a "valid" portrait forever.
        import uuid

        tmp = p.parent / f"{p.name}.{uuid.uuid4().hex}.tmp"
        try:
            tmp.write_bytes(png)
            os.replace(tmp, p)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        return {
            "name": name,
            "avatar": f"/agents/{quote(name, safe='')}/avatar",
            "source": source,
            "bytes": len(png),
        }

    @app.delete("/agents/{name}/avatar")
    def delete_agent_avatar(name: str) -> dict[str, Any]:
        name = (name or "").strip()
        p = _avatar_path(name)
        if not name or not p.is_file():
            raise HTTPException(status_code=404, detail="no stored portrait for this agent")
        p.unlink()
        return {"removed": name}

    # --- Agent face overrides (v1.180.0) ------------------------------------
    # The drawn face is seeded from the agent's NAME; these routes let the user
    # choose the shape, the eyes and the colour instead. Each field is
    # INDEPENDENT — one set field overrides that one aspect and the rest keep
    # deriving — and an ABSENT field means "derive", never "empty".
    #
    # A stored PORTRAIT still wins over both (the client's precedence, unchanged
    # since v1.171.0): a real picture is a stronger identity than a chosen
    # geometry, and swapping that order would make an upload look like it failed.
    #
    # `async def` + `asyncio.to_thread` for every filesystem step: the daemon is
    # ONE loop and a face read is real blocking IO (v1.153.1).

    @app.get("/agents/faces")
    async def list_agent_faces() -> dict[str, Any]:
        """Every stored override at once, keyed by agent name, plus the allowed
        sets a picker may offer.

        ONE call instead of one per agent: the Setup card renders a face for
        every built-in, dynamic and remote agent it lists, and per-row fetches
        would be N round-trips for a card that is collapsed by default.
        `options` is served rather than hardcoded client-side so the picker can
        never offer a value this daemon would 400.
        """
        try:
            stored = await asyncio.to_thread(faces.list_faces, d.platform.config.home)
        except Exception:  # noqa: BLE001 — an empty map beats a 500; faces derive
            stored = {}
        return {"faces": stored, "options": faces.face_options()}

    @app.get("/agents/{name}/face")
    async def get_agent_face(name: str) -> dict[str, Any]:
        """This agent's override, or ``face: null`` when it derives from its
        name. 200 either way — "no override" is a normal state, not a 404."""
        name = (name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="agent name is required")
        stored = await asyncio.to_thread(
            faces.read_face, d.platform.config.home, _face_slug(name)
        )
        return {"name": name, "face": stored or None, "options": faces.face_options()}

    @app.put("/agents/{name}/face")
    async def set_agent_face(name: str, body: dict) -> dict[str, Any]:
        """Set a partial override. Every field is validated against the
        daemon's allowed set and an invalid value is an HONEST 400 naming the
        field and what it accepts — never a silent default, which would tell
        the user they picked something they did not.

        The write REPLACES the record, so a field left out of the body goes
        back to deriving. That is the whole vocabulary of this endpoint: send
        what should be pinned, DELETE to pin nothing.
        """
        name = (name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="agent name is required")
        try:
            override = faces.normalize_override(body or {})
        except faces.FaceValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if not override:
            raise HTTPException(
                status_code=400,
                detail=(
                    "give at least one of shape, color or eyes — "
                    f"DELETE /agents/{name}/face resets to the derived face"
                ),
            )
        stored = await asyncio.to_thread(
            faces.write_face,
            d.platform.config.home,
            _face_slug(name),
            override,
            name=name,
        )
        return {"name": name, "face": stored}

    @app.delete("/agents/{name}/face")
    async def delete_agent_face(name: str) -> dict[str, Any]:
        """Back to the face the NAME draws.

        Idempotent 200 (unlike the portrait's DELETE, which 404s): "reset" is a
        state the user asked for, and a Reset button that errors on an
        already-derived face reports a failure where nothing failed. `removed`
        says whether a record actually existed, so the caller still knows.
        """
        name = (name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="agent name is required")
        removed = await asyncio.to_thread(
            faces.delete_face, d.platform.config.home, _face_slug(name)
        )
        return {"name": name, "removed": removed, "face": None}

    # --- A day off (v1.295.0) — AFTER the remote block (so "/agents/remote/…"
    # keeps its priority), BEFORE the {name} edit/delete catch-alls below. ---

    @app.post("/agents/{name}/pause")
    def pause_agent(name: str, body: PauseBody) -> dict[str, Any]:
        """Give a custom agent a day off: every spawn answers 409 with the
        reason until ``/resume``. Answers with the same row ``GET /agents``
        shows, so the page can render the change without a second fetch."""
        reason = (body.reason or "").strip() or PAUSE_DEFAULT_REASON
        rec = d.platform.agents_registry.set_paused(name, reason)
        if rec is None:
            raise HTTPException(status_code=404, detail="unknown agent")
        return _dynamic_row(rec)

    @app.post("/agents/{name}/resume")
    def resume_agent(name: str) -> dict[str, Any]:
        rec = d.platform.agents_registry.resume(name)
        if rec is None:
            raise HTTPException(status_code=404, detail="unknown agent")
        return _dynamic_row(rec)

    # --- Dynamic-agent edit / delete (catch-all {name} — keep AFTER remote) ---

    @app.patch("/agents/{name}")
    def patch_agent(name: str, body: AgentPatch) -> dict[str, Any]:
        rec = d.platform.agents_registry.get(name)
        if rec is None:
            raise HTTPException(status_code=404, detail="unknown agent")
        # PARTIAL on purpose (v1.295.0): the identity columns (`register`'s
        # positional parameters, always written) travel body-or-record, and
        # the job-card kwargs are passed ONLY when the body carries them (the
        # registry keeps an omitted one). The old route re-passed
        # `rec.provider`/`rec.model`/`rec.base_type` unconditionally, so a
        # PATCH that changed the model was silently a no-op.
        fields = _employee_fields(body, name=rec.name, creating=False)
        updated = d.platform.agents_registry.register(
            name,
            body.system_prompt if body.system_prompt is not None else rec.system_prompt,
            [str(t) for t in body.tools] if body.tools is not None else _json_list(rec.tools_json),
            base_type=fields.pop("base_type", rec.base_type),
            description=body.description if body.description is not None else rec.description,
            provider=body.provider if body.provider is not None else rec.provider,
            model=body.model if body.model is not None else rec.model,
            **fields,
        )
        # Raising the allowance LIFTS an allowance auto-pause (reviewer
        # finding): the refusal says "raise it on the Agents page", so the
        # raise must be enough on its own. Only when THIS body changed a bound,
        # only for the AUTO reason (a user's day off is never lifted here),
        # and only if the month's ledger is no longer exhausted against the
        # new bound; the 80% warning dedupe is reset so the new bound warns
        # afresh.
        allowance_changed = (
            updated.allowance_tokens != rec.allowance_tokens
            or updated.allowance_usd != rec.allowance_usd
        )
        auto_paused = str(getattr(updated, "paused_reason", "") or "").startswith(
            AUTO_PAUSE_PREFIX
        )
        if allowance_changed and auto_paused:
            state = allowance_state(
                updated, month_spend(d.platform.engine, f"custom:{updated.name}")
            )
            if state.get("status") != "exhausted":
                resumed = d.platform.agents_registry.resume(name)
                cleared = d.platform.agents_registry.set_warned_month(name, "")
                updated = cleared or resumed or updated
        return _dynamic_row(updated)

    @app.delete("/agents/{name}")
    def delete_agent(name: str) -> dict[str, Any]:
        if not d.platform.agents_registry.remove(name):
            raise HTTPException(status_code=404, detail="unknown agent")
        return {"removed": name}

    # --- Agent threads: cross-source panels with roles (the Agents page) -----
    # IMPORTANT: registered BEFORE the {name} catch-alls above would matter,
    # but "/agents/threads/..." never collides because those routes were
    # declared first in this file; keep new thread routes below explicit paths.

    def _threads():
        from ...agents.threads import AgentThreads

        return AgentThreads(d.platform.engine)

    def _thread_view(rec) -> dict[str, Any]:
        # ONE shape, shared with the project world's room route (v1.304.0:
        # + ``project_id``, "" for a general room).
        from ...agents.threads import thread_view

        return thread_view(rec)

    @app.get("/agents/threads")
    def list_agent_threads(project_id: str = "") -> dict[str, Any]:
        """The rooms of ONE world (v1.304.0). No ``project_id`` (or an empty
        one) = the GENERAL world: rooms bound to no project — the Agents page
        exactly as it was, chat-bound panels included. ``?project_id=<id>`` =
        that project's rooms. A project room never shows up in General: its
        world is where it lives."""
        out = []
        for rec in _threads().list(project_id=(project_id or "").strip()):
            view = _thread_view(rec)
            view.pop("messages")  # list rows stay light; GET one for the transcript
            out.append(view)
        return {"threads": out}

    def _room_project(raw: Any) -> str:
        """``project_id`` on a new room: "" (general) or an existing ACTIVE
        project's id — else a 400 in plain words (v1.304.0)."""
        pid = str(raw or "").strip()
        if not pid:
            return ""
        from ...core.db import session_scope
        from ...core.models import Project

        with session_scope(d.platform.engine) as db:
            project = db.get(Project, pid)
        if project is None:
            raise HTTPException(
                status_code=400,
                detail=f"there is no project with id {pid} — pick one from the Projects page",
            )
        if (project.status or "active") != "active":
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{project.name} is archived — unarchive it before starting a "
                    "round table there"
                ),
            )
        return pid

    @app.post("/agents/threads")
    def create_agent_thread(body: dict) -> dict[str, Any]:
        """A new room. ``project_id`` (v1.304.0, optional) binds it to that
        project's world — its rounds are grounded in the project and its
        jobs carry the project; absent = a general room, as before."""
        from ...agents.threads import clean_participants

        try:
            participants = clean_participants(body.get("participants"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        project_id = _room_project(body.get("project_id"))
        rec = _threads().create(str(body.get("title") or ""), participants, project_id=project_id)
        return _thread_view(rec)

    @app.get("/agents/worlds")
    async def agent_worlds() -> dict[str, Any]:
        """The Agents page's landing (v1.304.0): one WORLD per active project
        — ``{project: {id, name, status, brief, root, created_at}, team:
        [{name, kind, label, avatar, face}], thread_id (its round table, or
        null), counts: {waiting, running, queued, done_7d}, last_activity}``
        — newest activity first, plus ``general: {thread_count}`` (the rooms
        bound to no project). A few grouped queries off the loop; the asks
        are read from the approvals registry ON the loop (in-memory)."""
        from ...projects import world as _world
        from .projects import _review_ids, _world_asks

        reviews = _review_ids(d)
        data = await asyncio.to_thread(_world.gather_worlds, d.platform, reviews)
        out = []
        for w in data["worlds"]:
            snap = w.pop("snap")
            asks = _world_asks(d, [s["id"] for s in snap["active"]])
            last = w.pop("last_activity", None)
            out.append(
                {
                    **w,
                    "counts": _world.counts_for(snap, asks),
                    "last_activity": _world._iso(last),
                }
            )
        return {"worlds": out, "general": data["general"]}

    @app.get("/agents/threads/{thread_id}")
    def get_agent_thread(thread_id: str) -> dict[str, Any]:
        rec = _threads().get(thread_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such thread")
        return _thread_view(rec)

    @app.put("/agents/threads/{thread_id}/participants")
    def set_agent_thread_participants(thread_id: str, body: dict) -> dict[str, Any]:
        from ...agents.threads import clean_participants

        try:
            participants = clean_participants(body.get("participants"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        rec = _threads().update_participants(thread_id, participants)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such thread")
        return _thread_view(rec)

    @app.delete("/agents/threads/{thread_id}")
    def delete_agent_thread(thread_id: str) -> dict[str, Any]:
        if not _threads().delete(thread_id):
            raise HTTPException(status_code=404, detail="no such thread")
        return {"deleted": thread_id}

    @app.post("/agents/threads/{thread_id}/say")
    async def agent_thread_say(thread_id: str, body: dict) -> dict[str, Any]:
        """One speaking round: the user's message (optional — empty continues
        the panel), then every participant answers in order, each seeing the
        replies before it. Failures are honest per-participant entries.

        LIVE + DIRECTED (v1.140.0): entries persist one-by-one and each
        publishes AGENT_THREAD_UPDATED {thread_id, who, entries} so the UI can
        follow along; @-mentions in the message (name / role / key's name
        part, case-insensitive) restrict who speaks this round — see
        AgentThreads.run_round for the exact rule. Response is additive:
        {"entries": [the full round], "spoke": [keys that answered, honest
        errors included], "skipped": [remote keys skipped as offline]}."""
        try:
            return await _threads().run_round(
                thread_id, str(body.get("message") or ""), d
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="no such thread")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/agents/threads/{thread_id}/remember")
    async def remember_agent_thread(thread_id: str, body: dict) -> dict[str, Any]:
        """Commit a round table to LONG-TERM MEMORY (v1.178.0) — the agent-side
        twin of ``POST /chat/threads/{id}/remember``. Without it a decision the
        panel reached dies with the thread.

        Body (all optional): ``mode`` distill|full, ``source`` (an LTM store,
        "" = the default brain), ``provider``/``model`` (distill override), and
        ``preview``.

        ``preview`` DEFAULTS TO TRUE and that is the point: the default call
        writes nothing and returns ``items`` (the extracted claims) plus the
        exact ``content`` that would land, so the user reviews agent-written
        text before the app can quote it back as fact. Send ``preview: false``
        to commit — the explicit call. With no real model connected, distill
        degrades to a verbatim excerpt and says so (``distilled: false`` +
        ``note``); a mock must never fabricate a memory of a real conversation.
        The ladder lives in ``AgentThreads.remember``; errors map the same way
        ``/say`` maps them."""
        body = body or {}
        # ONLY AN EXPLICIT FALSE MAY DEFEAT THE PREVIEW. ``bool(body.get(
        # "preview", True))`` reads right and is not: every falsy JSON value
        # resolves to False, so ``{"preview": null}`` — what a client sends for
        # a field it has not decided yet — COMMITTED. Measured: that exact body
        # wrote the panel into the brain and answered ``"preview": false``,
        # which is the suggest-don't-act default failing silently in the one
        # direction that cannot be undone. Anything not recognisable as a
        # "false" previews; the caller that means to write says so.
        _pv = body.get("preview", True)
        try:
            return await _threads().remember(
                thread_id,
                d,
                mode=str(body.get("mode") or "distill"),
                source=str(body.get("source") or ""),
                provider=str(body.get("provider") or ""),
                model=str(body.get("model") or ""),
                preview=_pv is None
                or str(_pv).strip().lower() not in ("false", "0", "no"),
                # The body the preview returned, sent back on the commit so what
                # the user approved is what lands (v1.178.0 review finding).
                # Optional: a caller that never previewed omits it and the
                # ladder runs exactly as before.
                approved_content=str(body.get("content") or ""),
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="no such thread")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except RuntimeError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    # Custom (agent/user-authored) reusable tools.
    @app.get("/tools/custom")
    def list_custom_tools() -> dict[str, Any]:
        import json as _json

        def _load(s: str):
            try:
                return _json.loads(s or "[]")
            except (TypeError, ValueError):
                return []

        return {
            "tools": [
                {
                    "name": r.name,
                    "description": r.description,
                    "parameters": _load(r.params_json),
                    "command": _load(r.argv_json),
                    "timeout_seconds": r.timeout_seconds,
                    "created_by": r.created_by,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in d.platform.tools_registry.list()
            ]
        }

    @app.post("/tools/custom")
    def create_custom_tool(body: CustomToolCreate) -> dict[str, Any]:
        import re as _re

        name = (body.name or "").strip()
        if not _re.match(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$", name):
            raise HTTPException(status_code=400, detail="invalid tool name")
        if d.platform.registry.get(name) is not None and name not in set(
            d.platform.registry.custom_names()
        ):
            raise HTTPException(status_code=400, detail=f"'{name}' is a built-in tool")
        if not body.command:
            raise HTTPException(status_code=400, detail="command (argv) is required")
        # THE PROGRAM MUST EXIST (v1.205.0) — the same refusal, same wording,
        # as the `tool_create` agent tool: both doors go through
        # `missing_program_error`, so a dead tool (POSIX `mv` on a Windows
        # install — 22/22 live failures) is never persisted from either side.
        from ...tools.dynamic import missing_program_error

        not_installed = missing_program_error(
            [str(c) for c in body.command if str(c).strip()]
        )
        if not_installed:
            raise HTTPException(status_code=400, detail=not_installed)
        try:
            rec = d.platform.tools_registry.register(
                name, body.description, body.parameters, body.command, body.timeout_seconds
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        d.platform.registry.register(d.platform.tools_registry.build_tool(rec), custom=True)
        return {"name": rec.name}

    @app.post("/tools/custom/generate")
    async def generate_custom_tool(body: ToolGenerateBody) -> dict[str, Any]:
        """Describe the tool you want in plain language — an LLM designs the
        command-line tool (name, typed parameters, argv template) and it is
        registered immediately, usable by every agent."""
        import json as _json

        from ...providers.adapters.base import LLMMessage

        provider = body.provider or d.platform.config.default_provider
        model = body.model or d.platform.config.default_model
        try:
            adapter = d.platform.providers.get(provider, model)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"provider unavailable: {exc}")

        system = (
            "You design COMMAND-LINE tools for an agent platform running on "
            f"{'Windows' if os.name == 'nt' else 'POSIX'}. A tool runs an argv "
            "command in a workspace, with {param} placeholders filled from typed "
            "parameters. Respond with ONLY a JSON object (no prose, no fence): "
            '{"name": "snake_case_name", "description": "one line: what it does '
            'and when an agent should use it", "parameters": [{"name": "...", '
            '"type": "string|number|boolean", "required": true, "description": '
            '"..."}], "command": ["program", "arg", "{param}"], '
            '"timeout_seconds": 60}. Prefer python -c or powershell -Command for '
            "portability; keep it safe (no destructive defaults); every {param} "
            "in command MUST exist in parameters."
        )
        resp, _p, _m = await d._one_shot_complete(
            provider,
            adapter,
            system=system,
            messages=[
                LLMMessage(role="user", content=f"Design a tool for: {body.description}")
            ],
        )
        text = resp.text or ""
        start, depth, obj = text.find("{"), 0, ""
        if start >= 0:
            for i in range(start, len(text)):
                depth += (text[i] == "{") - (text[i] == "}")
                if depth == 0:
                    obj = text[start : i + 1]
                    break
        try:
            spec = _json.loads(obj)
        except Exception:  # noqa: BLE001
            raise HTTPException(
                status_code=422,
                detail="the model did not return a valid tool spec — try rephrasing",
            )
        # Register through the SAME validated path as a hand-made tool.
        create = CustomToolCreate(
            name=str(spec.get("name") or ""),
            description=str(spec.get("description") or body.description)[:300],
            parameters=[p for p in (spec.get("parameters") or []) if isinstance(p, dict)],
            command=[str(c) for c in (spec.get("command") or [])],
            timeout_seconds=int(spec.get("timeout_seconds") or 60),
        )
        result = create_custom_tool(create)
        return {
            **result,
            "spec": create.model_dump(),
            "reply": (
                f"Built the `{create.name}` tool — it's live for every agent now. "
                "Try it, and delete/regenerate if it's not quite right."
            ),
        }

    @app.get("/mcp/catalog")
    async def mcp_catalog() -> dict[str, Any]:
        """The add-a-pack catalog, each row carrying ``runtime_ready``.

        v1.316.0 (UX wave 4): the Tools page painted EVERY pack with an amber
        "Needs Node" on a machine that runs Node, because ``needs`` is a static
        field. ``runtime_ready`` answers "would the launcher find this pack's
        command right now?" with the launcher's OWN resolution
        (``resolve_launcher``: real PATH, then the per-user bin dirs a
        GUI-launched daemon misses) — a which-only check would disagree with
        what actually starts. Computed per request (a user who installs uv
        sees it on reload), on COPIES (never written into the shared list),
        off the loop (PATH probes touch the disk), and any fault is False,
        never a 500. v1.343.0: the resolution is :func:`launcher_ready`,
        shared with the import preview.
        """

        def _rows() -> list[dict[str, Any]]:
            cache: dict[str, bool] = {}
            out: list[dict[str, Any]] = []
            for row in d._MCP_CATALOG:
                # v1.355.0 (G44): a server reached over the network (GitHub's
                # own) launches nothing here, so nothing is missing — the
                # import preview's rule for an http/sse pack.
                if str(row.get("transport") or "stdio").lower() in ("http", "sse"):
                    out.append({**row, "runtime_ready": True})
                    continue
                cmd = str(row.get("command") or "")
                if cmd not in cache:
                    cache[cmd] = launcher_ready(cmd)
                out.append({**row, "runtime_ready": cache[cmd]})
            return out

        return {"catalog": await asyncio.to_thread(_rows)}

    @app.post("/mcp/import/preview")
    def mcp_import_preview(body: McpImportPreviewBody) -> dict[str, Any]:
        """What a paste would add (v1.343.0, "add a pack in one paste").

        ``mcp/import_config.parse_any`` reads a Claude Desktop / Claude Code
        / VS Code / Cursor JSON block, a Codex TOML, a single server object,
        a command line, a URL or an ``npx mcp-remote`` line into specs; the
        rows carry NAMES ONLY (env keys, secret keys, header names) plus
        ``exists`` / ``runtime_ready`` / ``launcher_note``. A value the
        paste carried never comes back — the apply route re-parses the
        text server-side. ``unrecognised`` is True when the text said
        something and none of it was a pack.
        """
        from ...mcp.import_config import parse_any

        text = body.text or ""
        specs = parse_any(text)
        return {
            "servers": preview_rows(d, specs),
            "unrecognised": bool(text.strip()) and not specs,
        }

    @app.post("/mcp/import/apply")
    def mcp_import_apply(body: McpImportApplyBody) -> dict[str, Any]:
        """Add the chosen packs out of a paste (v1.343.0).

        The TEXT is parsed again here (values never round-trip through the
        browser; ``overrides`` are the values the user typed for keys the
        paste did not carry) and every chosen pack goes through the one add
        door (:func:`add_mcp_pack`) — secrets split into the vault, the live
        load run, the note saying why a pack did not start. Answers
        ``{added: [{name, tools_loaded, note, state}], skipped: [{name,
        why}]}``; an existing name is skipped unless ``replace`` is true.
        ``source`` (one of the scanned config files) is the sources door's
        branch of this same route.
        """
        from ...mcp.import_config import parse_any

        text = body.text or ""
        source = (body.source or "").strip()
        if text.strip():
            specs = parse_any(text)
        elif source:
            # v1.343.0 (lane I): the sources door. The file is RE-READ here by
            # its scan key — the browser never sends a path, and a key the
            # scan does not know is refused — so values come off the disk
            # and go straight into the vault, never through the browser.
            from ...mcp.import_sources import read_source

            scanned = read_source(d.platform, source)
            if scanned is None:
                raise HTTPException(status_code=404, detail="no such config file source")
            if not scanned.readable:
                raise HTTPException(
                    status_code=409,
                    detail=scanned.note or f"{scanned.label}'s config file was not found",
                )
            specs = scanned.specs
        else:
            raise HTTPException(status_code=400, detail="paste a config block, a command line or a URL")
        if not body.names:
            raise HTTPException(status_code=400, detail="choose at least one pack to add")
        return _apply_specs(
            d, specs, list(body.names), dict(body.overrides or {}), bool(body.auto_approve), bool(body.replace)
        )

    @app.get("/mcp/import/sources")
    async def mcp_import_sources() -> dict[str, Any]:
        """The MCP servers the user's other apps already have (v1.343.0).

        ``mcp/import_sources.scan`` reads, READ-ONLY and off the loop,
        ``~/.claude.json`` (``CLAUDE_CONFIG_DIR`` honoured), Claude Code's
        ``settings.json``, the active project's ``.mcp.json``, Claude
        Desktop's config, Codex's ``config.toml`` (``CODEX_HOME`` honoured),
        Cursor's ``mcp.json`` and VS Code's user ``settings.json``. Each
        row is ``{source, label, origin, readable, note, servers}`` with the
        same preview rows the paste door answers — names only, never a
        value. A missing file is ``readable: false`` with an empty note; a
        malformed one names the file in one line, never its contents. Iron
        Jarvis's own server entries are not listed.

        v1.354.0 (G50): also VS Code's ``User/mcp.json``, a project's
        ``.vscode/mcp.json`` and ``.cursor/mcp.json`` (only while a project
        is active), Windsurf, Gemini CLI and the installed Claude Code
        plugins; rows carry ``disabled`` / ``exists_as`` / ``blurb`` (see
        :func:`preview_rows`).
        """
        from ...mcp.import_sources import scan

        def _rows() -> list[dict[str, Any]]:
            # v1.354.0: what is already connected and the catalog's lines
            # are read ONCE for every file, not once per file.
            try:
                known = known_connections(d)
            except Exception:  # noqa: BLE001
                known = {}
            try:
                blurbs = catalog_blurbs(d)
            except Exception:  # noqa: BLE001
                blurbs = {}
            out: list[dict[str, Any]] = []
            for item in scan(d.platform):
                try:
                    servers = (
                        preview_rows(d, item.specs, known=known, blurbs=blurbs) if item.readable else []
                    )
                except Exception:  # noqa: BLE001 — one bad file never hides the rest
                    servers = []
                out.append(item.row(servers))
            return out

        return {"sources": await asyncio.to_thread(_rows)}

    @app.get("/mcp/servers")
    def mcp_servers() -> dict[str, Any]:
        """Configured MCP servers, each annotated with how many of its tools are
        currently LIVE in the registry (0 = configured but not loaded — usually a
        server that failed to connect, or was added and needs a restart)."""
        # v1.343.0 (lane J): the row is built by `mcp_server_row` so the
        # PATCH answer and this list cannot disagree.
        servers = [
            mcp_server_row(d, s)
            for s in list(getattr(d.platform.config, "mcp_servers", None) or [])
        ]
        # The Tools page checkbox binds to EFFECTIVE (what the boot-time
        # resolver actually grants: the global switch OR any per-server flag) —
        # a checkbox showing "off" while agents run tools unprompted would be
        # the dishonest kind of simple.
        _global = bool(getattr(d.platform.config, "mcp_auto_approve", False))
        _effective = _global or any(bool(s.get("auto_approve")) for s in servers)
        return {
            "servers": servers,
            "auto_approve_global": _global,
            "auto_approve_effective": _effective,
        }

    @app.patch("/mcp/settings")
    def patch_mcp_settings(body: McpSettingsPatch) -> dict[str, Any]:
        """The global "Let agents use plug-in tools without asking" switch
        (v1.127.0). Twice reported as "the checkbox doesn't stick": it used to
        be an unsaved form field consumed only by the next connect. Now it IS
        the setting.

        ON sets the persisted global flag. OFF turns the global flag off AND
        clears every per-server auto_approve — an unchecked box must mean
        "agents ask", not "agents ask unless some older per-plug-in flag still
        trusts everything" (mcp_call is one shared permission key, so any
        surviving flag would keep the blanket grant alive). ``None`` reads
        without changing. Applied by the ask-resolver at boot, so the response
        says restart rather than pretending it is live.
        """
        servers = list(getattr(d.platform.config, "mcp_servers", None) or [])
        if body.auto_approve is None:
            _global = bool(getattr(d.platform.config, "mcp_auto_approve", False))
            return {
                "auto_approve_global": _global,
                "auto_approve_effective": _global
                or any(bool(s.get("auto_approve")) for s in servers),
                "note": None,
            }
        # Redesign S2: the global flag through the one writer (ledger + Undo);
        # the per-server flags it clears are pack records, persisted as before.
        from .settings import config_writer

        config_writer(d).apply({"mcp_auto_approve": bool(body.auto_approve)}, actor="tools_page")
        if not body.auto_approve:
            cleared = False
            for s in servers:
                if "auto_approve" in s:
                    s.pop("auto_approve", None)  # absent == off, matching POST/PATCH
                    cleared = True
            if cleared:
                d.platform.config.mcp_servers = servers
                d._persist_config(["mcp_servers"])
        return {
            "auto_approve_global": bool(body.auto_approve),
            "auto_approve_effective": bool(body.auto_approve),
            "note": (
                "saved — restart Iron Jarvis so autonomous agents may use plug-in tools without asking"
                if body.auto_approve
                else "saved — every plug-in will ask before use again after the next restart"
            ),
        }

    @app.post("/mcp/servers/{name}/test")
    def test_mcp_server(name: str) -> dict[str, Any]:
        """Connect to a configured server RIGHT NOW and list its tools — proves
        the command/URL + auth work without waiting for a restart. Read-only —
        it registers nothing, so it must not touch the load record either
        (``record=False``): a green Test over a pack that failed at boot would
        otherwise clear ``last_error``, hide the Tools row's Retry and let the
        Overview say nominal while agents still hold none of its tools."""
        servers = list(getattr(d.platform.config, "mcp_servers", None) or [])
        cfg = next((s for s in servers if s.get("name") == name), None)
        if cfg is None:
            raise HTTPException(status_code=404, detail="no such server")
        from ...mcp.tools import mcp_tools as _mcp_tools

        # v1.342.0: the loader hands a probe's failure back through `errors`,
        # so the answer is the REAL load error with its reason, fix and the
        # child's last output — "advertised no tools" is said only when the
        # pack really connected and listed none.
        errors: dict[str, dict[str, Any]] = {}
        try:
            tools = _mcp_tools(
                [cfg], secret_resolver=d.platform.secrets.get, record=False, errors=errors
            )
        except Exception as exc:  # noqa: BLE001 — report, never crash
            return {
                "ok": False,
                "count": 0,
                "tools": [],
                "error": f"{type(exc).__name__}: {exc}",
                "reason": "",
                "fix": "",
                "stderr_tail": "",
            }
        failure = errors.get(name)
        if failure is not None:
            return {
                "ok": False,
                "count": 0,
                "tools": [],
                "error": failure.get("error") or "the pack did not start",
                "reason": failure.get("reason") or "",
                "fix": failure.get("fix") or "",
                "stderr_tail": failure.get("stderr_tail") or "",
            }
        names = [t.name.split("__", 2)[-1] for t in tools]
        return {
            "ok": bool(tools),
            "count": len(tools),
            "tools": names,
            "error": None if tools else "connected but the server advertised no tools",
            "reason": "",
            "fix": "",
            "stderr_tail": "",
        }

    @app.post("/mcp/servers/{name}/reload")
    def reload_mcp_server(name: str) -> dict[str, Any]:
        """Retry a server that did not start (v1.229.0, audit U4): unload its
        live tools, connect again through the same loader boot uses, register
        what it advertises. The load record is refreshed either way, so the row
        on the Tools page shows the NEW reason (or none) after a Retry — the
        read-only ``/test`` route proves a config but never changes what agents
        hold."""
        # v1.344.0: the reload itself is ``mcp.tools.reload_pack`` — the
        # ``pack_restart`` tool calls the same function, so a Retry from the
        # Tools page and one asked for in chat are one reload.
        from ...mcp.tools import reload_pack

        try:
            r = reload_pack(d.platform, name, home=d.platform.config.home)
        except KeyError:
            raise HTTPException(status_code=404, detail="no such server")
        # The response shape is pinned byte-for-byte by v1.229.0's tests; what
        # the reload held back is read off the row (`GET /mcp/servers`).
        return {"ok": r["ok"], "tools_loaded": r["tools_loaded"], "last_error": r["last_error"]}

    @app.post("/mcp/servers/{name}/tools/{tool}/trust")
    def trust_mcp_tool(name: str, tool: str) -> dict[str, Any]:
        """The user trusts ONE quarantined tool by name (v1.299.0).

        A write-like tool that appeared (or changed shape) after its pack was
        connected asks regardless of the pack's auto-approve. This clears that:
        the manifest marks it trusted (so the next boot/reload keeps it) and the
        live Tool's flag drops, so the pack's permission applies to it from the
        next call. Per TOOL on purpose — trusting the pack again wholesale is
        what the quarantine exists to stop. 404 when the pack or the tool is
        unknown to both the manifest and the registry.
        """
        servers = list(getattr(d.platform.config, "mcp_servers", None) or [])
        if not any(s.get("name") == name for s in servers):
            raise HTTPException(status_code=404, detail="no such server")
        from ...mcp.tools import clear_quarantine as _clear_quarantine

        if not _clear_quarantine(d.platform.config.home, d.platform.registry, name, tool):
            raise HTTPException(status_code=404, detail="no such tool")
        return {"ok": True, "server": name, "tool": tool}

    @app.post("/mcp/servers")
    def add_mcp_server(body: McpServerBody) -> dict[str, Any]:
        """Register an external MCP server — see :func:`add_mcp_pack`."""
        return add_mcp_pack(d, body)

    @app.patch("/mcp/servers/{name}")
    def patch_mcp_server(name: str, body: McpServerPatchBody) -> dict[str, Any]:
        """Edit a connected pack (v1.343.0, lane J: PARTIAL — see
        :func:`update_mcp_pack`), or just flip its auto-approve (v1.103.0).

        The auto-approve flip could only ever be set at CONNECT time —
        changing your mind meant deleting the pack and re-adding it, and the
        Tools page offered a checkbox that looked like a setting but was really
        a form field for the next connect, so it never reflected or changed
        what was stored. A body carrying nothing but ``auto_approve`` still
        takes this path, byte for byte: no reload, the same answer.

        Takes effect for autonomous agents at the next restart (the ask-resolver
        is built once at boot), and the response says so rather than implying it
        is live. Turning it OFF is honest immediately in the sense that it stops
        being re-applied on the next boot.
        """
        if not body.only_auto_approve():
            return update_mcp_pack(d, name, body)
        servers = list(getattr(d.platform.config, "mcp_servers", None) or [])
        target = next((s for s in servers if s.get("name") == name), None)
        if target is None:
            raise HTTPException(status_code=404, detail="no such server")
        if body.auto_approve is None:
            return {"name": name, "auto_approve": bool(target.get("auto_approve"))}

        if body.auto_approve:
            target["auto_approve"] = True
        else:
            target.pop("auto_approve", None)  # absent == off, matching POST
        d.platform.config.mcp_servers = servers
        d._persist_config(["mcp_servers"])
        return {
            "name": name,
            "auto_approve": bool(body.auto_approve),
            "note": (
                "saved — restart Iron Jarvis for autonomous agents to pick this up"
            ),
        }

    @app.delete("/mcp/servers/{name}")
    def delete_mcp_server(name: str) -> dict[str, Any]:
        """Remove a pack — see :func:`remove_mcp_pack` (the one remove path;
        the import door's ``replace`` uses it too)."""
        return remove_mcp_pack(d, name)

    @app.post("/mcp/suggest")
    async def suggest_mcp_server(body: McpSuggestBody) -> dict[str, Any]:
        """Describe what you want to connect — an LLM proposes the MCP server
        config (returned for review; nothing is added until you confirm)."""
        import json as _json

        from ...providers.adapters.base import LLMMessage

        provider = body.provider or d.platform.config.default_provider
        model = body.model or d.platform.config.default_model
        try:
            adapter = d.platform.providers.get(provider, model)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"provider unavailable: {exc}")
        system = (
            "You configure MCP (Model Context Protocol) stdio servers. Respond "
            "with ONLY a JSON object: {\"name\": \"kebab-name\", \"command\": "
            "\"npx\", \"args\": [\"-y\", \"<package>\", ...], \"env\": "
            "{\"KEY\": \"<what to put here>\"}, \"reply\": \"one short line: "
            "what this connects and any credential the user must supply\"}. "
            "Prefer well-known official/community MCP packages; if none fits, "
            "say so in reply and return an empty command."
        )
        resp, _p, _m = await d._one_shot_complete(
            provider,
            adapter,
            system=system,
            messages=[LLMMessage(role="user", content=body.description)],
        )
        text = resp.text or ""
        start, depth, obj = text.find("{"), 0, ""
        if start >= 0:
            for i in range(start, len(text)):
                depth += (text[i] == "{") - (text[i] == "}")
                if depth == 0:
                    obj = text[start : i + 1]
                    break
        try:
            spec = _json.loads(obj)
        except Exception:  # noqa: BLE001
            raise HTTPException(
                status_code=422, detail="no valid suggestion — try rephrasing"
            )
        return {"suggestion": spec}

    @app.delete("/tools/custom/{name}")
    def delete_custom_tool(name: str) -> dict[str, Any]:
        removed = d.platform.tools_registry.remove(name)
        d.platform.registry.unregister(name)
        return {"removed": removed}

    @app.post("/agents/{name}/spawn")
    async def spawn_agent_ep(name: str, body: SpawnBody) -> dict[str, Any]:
        from ...agents.types import get_agent_definition
        from ...core.models import AgentType

        definition = d.platform.agents_registry.definition(name)
        rec = d.platform.agents_registry.get(name)
        # WHO RAN (v1.193.0). THIS IS THE MOST COMMON WAY A USER RUNS THEIR OWN
        # AGENT (the Agents page's Run button) and it published no
        # ``delegation.started``, so the ledger had nothing to read back and the
        # run was credited to ``definition.type`` — i.e. a user running their own
        # tax-reader left ``custom:tax-reader`` reading "(no runs yet)" forever
        # while ``builder`` absorbed the history. The name is taken from the
        # RECORD (``rec.name``, the registry key's own casing), never from the
        # URL segment, which ``definition()``/``get()`` may have matched
        # differently.
        agent_name = ""
        if definition is not None and rec is not None:
            agent_name = f"custom:{rec.name}"
        if definition is None:
            try:
                definition = get_agent_definition(AgentType(name))
                agent_name = definition.type.value  # a builtin IS its roster name
            except ValueError:
                raise HTTPException(status_code=404, detail="unknown agent")
        elif definition.type is AgentType.SUPERVISOR:
            # Honest refusal, not silent substitution (v1.166.0): run_session
            # reroutes SUPERVISOR-typed sessions to the builtin run_supervised,
            # which cannot honor this record's custom system prompt — spawning
            # would silently discard a user-authored prompt. Unreachable via
            # POST /agents today (base_type is hardcoded "builder"), but a
            # directly registered record must not slip through the seam.
            raise HTTPException(
                status_code=409,
                detail=(
                    f"dynamic agent '{name}' is based on 'supervisor': the "
                    "builtin supervisor would run and silently discard the "
                    "record's custom system prompt. Re-create it with a "
                    "non-supervisor base type, or POST /sessions with "
                    "agent_type 'supervisor' to use the builtin."
                ),
            )
        # A day off or a spent allowance refuses the run HERE, before a session
        # row exists (v1.295.0): `refusal_for` reads the month's ledger, so it
        # leaves the loop like every other DB read on this route.
        if rec is not None:
            reason = await asyncio.to_thread(refusal_for, rec, d.platform.engine)
            if reason:
                raise HTTPException(status_code=409, detail=reason)
        # Parity with POST /sessions (v1.166.0): an explicit body.provider/model
        # wins; the dynamic record's pinned pair is the fallback.
        provider = body.provider or (rec.provider if (rec and rec.provider) else None)
        model = body.model or (rec.model if (rec and rec.model) else None)
        # The folder rides the spawn too (v1.189.0) — same contract, same
        # honest 400, same single predicate as POST /sessions.
        workspace_root = (body.workspace_root or "").strip() or None
        if workspace_root:
            from ...core.fs_policy import usable_workspace_root

            # v1.228.0: writability is PROBED now — off the loop, as /sessions.
            if not await asyncio.to_thread(usable_workspace_root, workspace_root):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "workspace_root must be an existing, absolute, "
                        "non-protected folder this app may write in "
                        "(missing, protected, not a directory, or not "
                        f"writable): {workspace_root} — pick a folder you can "
                        "save files in"
                    ),
                )
        session = await d.orchestrator.create_session(
            body.task,
            definition.type,
            provider=provider,
            model=model,
            project_id=body.project_id or None,
            allow_tools=body.allow_tools or None,
            workspace_root=workspace_root,
            origin=body.origin,
            agent_name=agent_name,
            # The chat posture rides a custom-agent escalation too (v1.232.0);
            # when the spawn states none, the job card's posture applies
            # (v1.295.0), and so does its per-run step budget.
            approval_mode=body.approval_mode
            or (str(getattr(rec, "approval_mode", "") or "") if rec is not None else ""),
            max_steps=getattr(rec, "max_steps", None) if rec is not None else None,
            # LOCAL ONLY RIDES THE ESCALATION (v1.332.0): stored on the row; a
            # provider off this network is refused inside create_session (409).
            options={"local_only": True} if getattr(body, "local_only", False) else None,
            # v1.344.0: what the chat already ran this turn rides the
            # custom-agent escalation too (SpawnBody.prior_tool_results).
            prior_tool_results=getattr(body, "prior_tool_results", None),
            # v1.345.0: the chat the custom-agent escalation was asked in —
            # the daemon appends the run's final line to it.
            chat_thread_id=getattr(body, "chat_thread_id", ""),
        )
        # Run through the orchestrator (with the dynamic definition override) so
        # a crashed run is finalized FAILED instead of stranded ACTIVE, and
        # post-run learning + git review fire for spawned agents too — the old
        # hand-rolled inline runner here had none of that (v1.166.0).
        if body.wait:
            session = await d.orchestrator.run_session(session.id, definition=definition)
        else:
            # Non-blocking spawn: the UI jumps straight to the live session view
            # (parity with POST /sessions wait:false). A parked spawn returns
            # None (v1.167.0) — re-read the row so the response says QUEUED,
            # not a stale "active" for work that never started.
            parked = d._spawn_bg(
                session.id, d.orchestrator.run_session(session.id, definition=definition)
            )
            if parked is None:
                session = d.orchestrator.get_session(session.id) or session
        return _session_view(session, d)
