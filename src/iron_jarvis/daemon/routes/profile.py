"""User-profile routes (v1.144.0) — the /you page's backend.

``GET  /profile``               — the stored profile + the rendered PREVIEW.
``PUT  /profile``               — partial update ({"values": {...}}).
``POST /profile/accessibility`` — turn a mode on/off and seed its companion fields.
``GET  /profile/options``       — the preset vocabularies + language list.

Train Jarvis on me (v1.145.0):

``GET    /profile/samples``     — the stored writing samples (metadata only).
``POST   /profile/samples``     — add one (pasted text, or a document).
``DELETE /profile/samples/{id}``— remove one.
``POST   /profile/voice/derive``— PROPOSE a voice card from the samples. Never
                                  stores it: the user saves it through
                                  ``PUT /profile`` like any other field.
``GET    /profile/training``    — the /train wizard's status board: what is
                                  already connected, counted from the EXISTING
                                  subsystems (LTM bases, memory, search roots)
                                  rather than a new store of its own.

The PREVIEW is the point of this surface, not a nicety: this profile is
injected into every system prompt, and a preferences page whose effect you
cannot see is a page people fill in once and then distrust. ``preview`` is the
EXACT string the seams append — rendered by the same ``render()`` — so what the
user reads on /you is what the model reads.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from ...profile import ProfileStore, as_dict, render
from ...profile import training as _training
from ...profile.block import MAX_BLOCK_CHARS
from ...profile.language import options as language_options
from ...profile.presets import (
    FORMATTING,
    READING_LEVELS,
    RESPONSE_LENGTHS,
    TONES,
    WRITING_STYLES,
    accessibility_options,
    options as preset_options,
)
from ..schemas import ProfileAccessibilityBody, ProfileBody, WritingSampleBody


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""

    def _payload(record) -> dict[str, Any]:
        block = render(record)
        return {
            "profile": as_dict(record),
            # What the model actually receives, and how much of the budget it
            # uses — the honesty surface for "is my profile too long?".
            "preview": block,
            "preview_chars": len(block),
            "preview_limit": MAX_BLOCK_CHARS,
        }

    @app.get("/profile")
    def get_profile() -> dict[str, Any]:
        return _payload(ProfileStore(d.platform.engine).get())

    @app.put("/profile")
    def put_profile(body: ProfileBody) -> dict[str, Any]:
        return _payload(ProfileStore(d.platform.engine).save(body.values or {}))

    @app.post("/profile/accessibility")
    def post_accessibility(body: ProfileAccessibilityBody) -> dict[str, Any]:
        store = ProfileStore(d.platform.engine)
        return _payload(store.apply_accessibility(body.mode or ""))

    # --- Train Jarvis on me (v1.145.0) ------------------------------------- #

    def _samples():
        return _training.SampleStore(d.platform.engine)

    @app.get("/profile/samples")
    def list_samples() -> dict[str, Any]:
        rows = _samples().list()
        return {
            "samples": [_training.as_dict(r) for r in rows],
            "total_chars": sum(len(r.text or "") for r in rows),
            "max_samples": _training.MAX_SAMPLES,
            "min_chars_to_derive": _training.MIN_DERIVE_CHARS,
        }

    @app.post("/profile/samples")
    def add_sample(body: WritingSampleBody) -> dict[str, Any]:
        text = (body.text or "").strip()
        origin = "pasted"
        if not text and body.content_b64:
            # Documents go through the SAME converter /ltm/ingest-document uses
            # — a second extraction path would drift from it the first time one
            # of them learned a new format.
            import base64
            import re as _re
            import tempfile
            from pathlib import Path as _Path

            from ...documents import document_to_markdown

            safe = _re.sub(r"[^A-Za-z0-9._-]", "_", body.filename).strip("._") or "sample"
            try:
                data = base64.b64decode(body.content_b64, validate=False)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=400, detail=f"invalid base64: {exc}")
            tmpdir = tempfile.mkdtemp(prefix="ij-sample-")
            tmp = _Path(tmpdir) / safe
            try:
                tmp.write_bytes(data)
                text = document_to_markdown(tmp).strip()
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=400, detail=f"could not read that document: {exc}"
                )
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                    _Path(tmpdir).rmdir()
                except OSError:
                    pass
            origin = f"document:{safe}"
            if not text:
                raise HTTPException(
                    status_code=422,
                    detail="no readable text in that document (a scanned image?)",
                )
        try:
            rec = _samples().add(
                label=body.label or (body.filename or ""), text=text, origin=origin
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"sample": _training.as_dict(rec)}

    @app.delete("/profile/samples/{sample_id}")
    def delete_sample(sample_id: str) -> dict[str, Any]:
        if not _samples().delete(sample_id):
            raise HTTPException(status_code=404, detail="no such sample")
        return {"deleted": sample_id}

    @app.post("/profile/voice/derive")
    async def derive_voice() -> dict[str, Any]:
        """PROPOSE a voice card. Suggest-don't-act: the response is a draft the
        user edits and saves through PUT /profile — nothing is stored here."""
        samples = _samples().list()
        # "Not enough writing yet" is answerable WITHOUT a model, and answering
        # it first stops the honest-mock refusal below from sending someone off
        # to connect a provider when the real problem is two pasted sentences.
        thin = _training.too_thin(samples)
        if thin:
            return {
                "card": "",
                "reason": thin,
                "samples_used": len(samples),
                "source": "",
            }
        complete = d._skill_distill_complete()
        if complete is None:
            # The honest-mock rule. A fabricated voice card would then be
            # injected into every prompt, which is the worst possible thing in
            # this app to invent.
            raise HTTPException(
                status_code=400,
                detail=(
                    "reading your writing needs a real model — connect one on "
                    "Connections (the offline mock would only invent a result)"
                ),
            )
        card, reason = await _training.derive(complete, samples)
        return {
            "card": card,
            "reason": reason,
            "samples_used": len(samples),
            "source": f"{len(samples)} writing sample{'s' if len(samples) != 1 else ''}",
        }

    @app.get("/profile/training")
    def training_status() -> dict[str, Any]:
        """What Iron Jarvis has to work with, counted from the subsystems that
        ALREADY own each kind of input. This endpoint deliberately stores
        nothing: /train is an on-ramp to the existing doorways, not a sixth
        place your knowledge can live."""
        cfg = d.platform.config
        rows = _samples().list()
        prof = ProfileStore(d.platform.engine).get()

        def _count(fn, default=0):
            try:
                return fn()
            except Exception:  # noqa: BLE001 — one dead subsystem, one 0
                return default

        return {
            "about": bool((prof.about or "").strip()),
            "voice_card": bool((prof.voice_card or "").strip()),
            "samples": len(rows),
            "sample_chars": sum(len(r.text or "") for r in rows),
            # Names only — LISTING a base's items would hit an MCP brain or a
            # network vault on every page load, and a status board is not worth
            # a round trip to someone's Notion.
            "memory_bases": _count(lambda: len(d.platform.ltm.sources())),
            "memory_items": _count(lambda: _row_count("MemoryRecord")),
            "search_roots": len(getattr(cfg, "search_roots", []) or []),
            "projects": _count(lambda: _row_count("Project")),
        }

    def _row_count(model_name: str) -> int:
        from sqlmodel import select as _select

        from ...core.db import session_scope
        from ...core.models import Project
        from ...memory.models import MemoryRecord

        model = {"Project": Project, "MemoryRecord": MemoryRecord}[model_name]
        with session_scope(d.platform.engine) as db:
            return len(list(db.exec(_select(model))))

    @app.get("/profile/options")
    def get_options() -> dict[str, Any]:
        return {
            "tone": preset_options(TONES),
            "writing_style": preset_options(WRITING_STYLES),
            "formatting": preset_options(FORMATTING),
            "reading_level": preset_options(READING_LEVELS),
            "response_length": preset_options(RESPONSE_LENGTHS),
            "accessibility": accessibility_options(),
            "language": language_options(),
        }
