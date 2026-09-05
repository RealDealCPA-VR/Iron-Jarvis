"""Agent/tool routes: registry, skills, custom tools, MCP, dynamic agents.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, HTTPException

from ..app import _session_view
from ..schemas import (
    AgentCreate,
    AgentPatch,
    CustomToolCreate,
    McpServerBody,
    McpServerPatch,
    McpSettingsPatch,
    McpSuggestBody,
    RemoteAgentCreate,
    RemoteAgentPatch,
    RemoteAgentRun,
    SkillApplyBody,
    SkillCreate,
    SpawnBody,
    ToolGenerateBody,
)

# Importing this registers the RemoteAgentRecord table on the shared metadata.
from ...agents.remote import RemoteAgentRegistry

# Face overrides (v1.180.0). Imported as a MODULE, never as loose names: the
# route looks every helper up on `faces` at call time so a test can monkeypatch
# one (the `_open_native` pattern from routes/documents, and how the
# event-loop-offload test proves the file IO left the loop).
from ...agents import faces

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

_AVATAR_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

#: DOS device names: opening ``<dir>/nul.png`` opens the DEVICE, not a file —
#: Windows matches the segment before the FIRST dot, case-insensitively.
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in "123456789"}
    | {f"lpt{i}" for i in "123456789"}
)


def _avatar_slug(name: str) -> str:
    """One path-safe filename segment that never EATS the identity (v1.153.2).

    A clean LOWERCASE name passes through verbatim. Any name the sanitizer had
    to touch gets a short digest of the ORIGINAL appended, so ``a/b`` and
    ``a_b`` can never collide on one file — lossy sanitization without the
    digest would silently merge two agents' portraits.

    CASE-FOLDING IS LOSSY TOO: the shipping filesystems (NTFS, APFS) are
    case-insensitive, so ``Analyst`` and ``analyst`` as distinct slugs would
    still resolve to ONE file. The stored segment is therefore lowercase, and
    a name the fold changed is treated exactly like any other sanitizer touch.
    Windows reserved device names (nul, con, com1…) get the digest PREFIXED —
    the device match keys on the segment before the first dot, so an appended
    digest would not break ``nul.txt``.
    """
    raw = str(name or "").strip()
    slug = _AVATAR_UNSAFE.sub("_", raw).strip("._")
    lowered = slug.lower()
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    if lowered.split(".", 1)[0] in _WINDOWS_RESERVED:
        return f"{digest}-{lowered}"
    if not lowered or lowered != raw:
        return f"{lowered or 'agent'}-{digest}"
    return lowered


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
        items = [
            {"name": s.name, "description": s.description, "source": s.source}
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

    @app.get("/agents")
    def list_agents() -> dict[str, Any]:
        import json as _json

        from ...agents.types import _DEFINITIONS

        return {
            "builtin": [t.value for t in _DEFINITIONS],
            "dynamic": [
                {
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
                    "tools": _json.loads(r.tools_json or "[]"),
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
                }
                for r in d.platform.agents_registry.list()
            ],
        }

    @app.post("/agents")
    def create_agent(body: AgentCreate) -> dict[str, Any]:
        name = (body.name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        rec = d.platform.agents_registry.register(
            name,
            body.system_prompt,
            body.tools,
            description=body.description,
            provider=body.provider,
            model=body.model,
        )
        return {"name": rec.name, "provider": rec.provider, "model": rec.model}

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
        """
        from ...agents.roster import resolve_target
        from ...agents.threads import AgentThreads, clean_participants, parse_mentions

        message = str(body.get("message") or "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="message is required")
        chat_thread_id = str(body.get("chat_thread_id") or "").strip()

        mentions = parse_mentions(message)
        if not mentions:
            raise HTTPException(
                status_code=400, detail="no @mentions in this message"
            )
        resolved: list[dict[str, str]] = []
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
        if not resolved:
            raise HTTPException(
                status_code=404,
                detail=f"no agent matched {', '.join('@' + u for u in unknown)}",
            )

        threads = AgentThreads(d.platform.engine)
        rec = threads.for_chat(chat_thread_id, title=message[:60])
        try:
            threads.add_participants(rec.id, clean_participants(resolved))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        try:
            round_out = await threads.run_round(rec.id, message, d)
        except KeyError:
            raise HTTPException(status_code=404, detail="panel thread vanished")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {
            "thread_id": rec.id,
            "unknown_mentions": unknown,
            **round_out,
        }

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
            timeout_s=int(body.timeout_s or 120),
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

    # --- Dynamic-agent edit / delete (catch-all {name} — keep AFTER remote) ---

    @app.patch("/agents/{name}")
    def patch_agent(name: str, body: AgentPatch) -> dict[str, Any]:
        rec = d.platform.agents_registry.get(name)
        if rec is None:
            raise HTTPException(status_code=404, detail="unknown agent")
        import json as _json

        try:
            tools = _json.loads(rec.tools_json or "[]")
        except (TypeError, ValueError):
            tools = []
        updated = d.platform.agents_registry.register(
            name,
            body.system_prompt if body.system_prompt is not None else rec.system_prompt,
            [str(t) for t in body.tools] if body.tools is not None else tools,
            base_type=rec.base_type,
            description=body.description if body.description is not None else rec.description,
            provider=rec.provider,
            model=rec.model,
        )
        return {"name": updated.name, "description": updated.description}

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
        import json as _json

        msgs = _json.loads(rec.messages_json or "[]")
        return {
            "id": rec.id,
            "title": rec.title,
            "participants": _json.loads(rec.participants_json or "[]"),
            "messages": msgs,
            "message_count": len(msgs),
            "updated_at": rec.updated_at.isoformat(),
        }

    @app.get("/agents/threads")
    def list_agent_threads() -> dict[str, Any]:
        out = []
        for rec in _threads().list():
            view = _thread_view(rec)
            view.pop("messages")  # list rows stay light; GET one for the transcript
            out.append(view)
        return {"threads": out}

    @app.post("/agents/threads")
    def create_agent_thread(body: dict) -> dict[str, Any]:
        from ...agents.threads import clean_participants

        try:
            participants = clean_participants(body.get("participants"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        rec = _threads().create(str(body.get("title") or ""), participants)
        return _thread_view(rec)

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
    def mcp_catalog() -> dict[str, Any]:
        return {"catalog": d._MCP_CATALOG}

    @app.get("/mcp/servers")
    def mcp_servers() -> dict[str, Any]:
        """Configured MCP servers, each annotated with how many of its tools are
        currently LIVE in the registry (0 = configured but not loaded — usually a
        server that failed to connect, or was added and needs a restart)."""
        servers = []
        for s in list(getattr(d.platform.config, "mcp_servers", None) or []):
            name = s.get("name") or ""
            loaded = d.platform.registry.mcp_names(name) if name else []
            servers.append(
                {
                    **s,
                    # Rows saved by the marketplace connect flow OMIT env/args
                    # when empty — normalize so clients can rely on the keys
                    # (a missing env crashed the Tools page on real installs).
                    "args": list(s.get("args") or []),
                    "env": dict(s.get("env") or {}),
                    "tools_loaded": len(loaded),
                    "tool_names": [n.split("__", 2)[-1] for n in loaded],
                }
            )
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
        changed_keys = ["mcp_auto_approve"]
        d.platform.config.mcp_auto_approve = bool(body.auto_approve)
        if not body.auto_approve:
            cleared = False
            for s in servers:
                if "auto_approve" in s:
                    s.pop("auto_approve", None)  # absent == off, matching POST/PATCH
                    cleared = True
            if cleared:
                d.platform.config.mcp_servers = servers
                changed_keys.append("mcp_servers")
        d._persist_config(changed_keys)
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
        the command/URL + auth work without waiting for a restart. Read-only."""
        servers = list(getattr(d.platform.config, "mcp_servers", None) or [])
        cfg = next((s for s in servers if s.get("name") == name), None)
        if cfg is None:
            raise HTTPException(status_code=404, detail="no such server")
        from ...mcp.tools import mcp_tools as _mcp_tools

        try:
            tools = _mcp_tools([cfg], secret_resolver=d.platform.secrets.get)
        except Exception as exc:  # noqa: BLE001 — report, never crash
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "tools": []}
        names = [t.name.split("__", 2)[-1] for t in tools]
        return {
            "ok": bool(tools),
            "count": len(tools),
            "tools": names,
            "error": None if tools else "connected but the server advertised no tools",
        }

    @app.post("/mcp/servers")
    def add_mcp_server(body: McpServerBody) -> dict[str, Any]:
        """Register an external MCP server (persisted; loaded live best-effort,
        guaranteed on the next restart)."""
        import re as _re

        name = (body.name or "").strip()
        if not _re.match(r"^[a-zA-Z][a-zA-Z0-9_-]{0,39}$", name):
            raise HTTPException(status_code=400, detail="invalid server name")
        if not (body.command or "").strip():
            raise HTTPException(status_code=400, detail="command is required")
        servers = list(getattr(d.platform.config, "mcp_servers", None) or [])
        if any(s.get("name") == name for s in servers):
            raise HTTPException(status_code=400, detail=f"server '{name}' already exists")
        cfg = {
            "name": name,
            "command": body.command.strip(),
            "args": [str(a) for a in body.args],
            "env": dict(body.env or {}),
            **({"cwd": body.cwd} if body.cwd else {}),
            **({"auto_approve": True} if body.auto_approve else {}),
        }
        servers.append(cfg)
        d.platform.config.mcp_servers = servers
        d._persist_config(["mcp_servers"])
        # Best-effort LIVE load so its tools appear without a restart. Registered
        # with mcp=True so the ``mcp:*`` sentinel reaches them from the agent loop
        # and they survive the next restart (boot re-registers the same way).
        loaded = 0
        try:
            from ...mcp.tools import mcp_tools as _mcp_tools

            for tool in _mcp_tools([cfg], secret_resolver=d.platform.secrets.get):
                d.platform.registry.register(tool, mcp=True)
                loaded += 1
        except Exception:  # noqa: BLE001 — persisted config still loads on restart
            loaded = 0
        # auto_approve is applied when the resolver is built at boot, so a server
        # added live with auto_approve won't be trusted by headless agents until
        # the next restart — be honest about that.
        note = None
        if not loaded:
            note = "saved — restart the daemon to load its tools"
        elif body.auto_approve:
            note = "loaded — restart the daemon so autonomous agents may use it without asking"
        return {
            "name": name,
            "added": True,
            "tools_loaded": loaded,
            "auto_approve": bool(body.auto_approve),
            "note": note,
        }

    @app.patch("/mcp/servers/{name}")
    def patch_mcp_server(name: str, body: McpServerPatch) -> dict[str, Any]:
        """Change a connected pack's auto-approve (v1.103.0).

        It could only ever be set at CONNECT time — changing your mind meant
        deleting the pack and re-adding it, and the Tools page offered a
        checkbox that looked like a setting but was really a form field for the
        next connect, so it never reflected or changed what was stored.

        Takes effect for autonomous agents at the next restart (the ask-resolver
        is built once at boot), and the response says so rather than implying it
        is live. Turning it OFF is honest immediately in the sense that it stops
        being re-applied on the next boot.
        """
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
        servers = list(getattr(d.platform.config, "mcp_servers", None) or [])
        kept = [s for s in servers if s.get("name") != name]
        if len(kept) == len(servers):
            raise HTTPException(status_code=404, detail="no such server")
        d.platform.config.mcp_servers = kept
        d._persist_config(["mcp_servers"])
        # Unregister its live tools NOW so they disappear from every agent's
        # loadout immediately (they won't come back on restart either — the
        # config no longer lists the server).
        unloaded = 0
        for tool_name in d.platform.registry.mcp_names(name):
            if d.platform.registry.unregister(tool_name):
                unloaded += 1
        return {"removed": name, "tools_unloaded": unloaded}

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
        # Parity with POST /sessions (v1.166.0): an explicit body.provider/model
        # wins; the dynamic record's pinned pair is the fallback.
        provider = body.provider or (rec.provider if (rec and rec.provider) else None)
        model = body.model or (rec.model if (rec and rec.model) else None)
        # The folder rides the spawn too (v1.189.0) — same contract, same
        # honest 400, same single predicate as POST /sessions.
        workspace_root = (body.workspace_root or "").strip() or None
        if workspace_root:
            from ...core.fs_policy import usable_workspace_root

            if not usable_workspace_root(workspace_root):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "workspace_root must be an existing, absolute, "
                        "non-protected folder this app may write in"
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
