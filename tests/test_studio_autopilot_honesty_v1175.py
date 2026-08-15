"""v1.175.0 — the Autopilot checkbox must describe what actually launches.

THE DEFECT, as an adversarial review found it: the Creative Studio's Autopilot
label said Claude "engages auto-accept via Shift+Tab after boot" and Codex
"launches with --full-auto". Neither had been true for a while —
``routes/creative.py`` replaced the Shift+Tab cycle with
``--dangerously-skip-permissions`` (Shift+Tab could never reach a genuinely
hands-off mode) and codex ≥0.4x REMOVED ``--full-auto``, so it takes
``--dangerously-bypass-approvals-and-sandbox``. The checkbox therefore
described something MILDER than what ran, and named a flag that no longer
exists. That is the app's own "never trade trust for magic" rule, broken in the
UI.

The fix is structural, not editorial: the flag is DATA (``AUTOPILOT_FLAGS``,
served on every CLI record as ``autopilot_flag``) and the label renders it, so
prose cannot drift from behaviour again. These tests pin the seam from Python
because a frontend test stays green when the render is deleted — the same
lesson as ``test_draft_spacing_v1163.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.terminals.ai_clis import AUTOPILOT_FLAGS, detect_ai_clis

_PAGE = (
    Path(__file__).resolve().parents[1] / "dashboard" / "app" / "creative" / "page.tsx"
)
_TYPES = Path(__file__).resolve().parents[1] / "dashboard" / "lib" / "types.ts"


def test_flags_are_the_real_ones_and_have_one_home():
    """The canonical table lives with the CLI catalog; the creative route
    re-exports it. Two copies would be two things to keep in step."""
    from iron_jarvis.daemon.routes.creative import _AUTOPILOT_FLAGS

    assert AUTOPILOT_FLAGS["claude"] == "--dangerously-skip-permissions"
    assert AUTOPILOT_FLAGS["codex"] == "--dangerously-bypass-approvals-and-sandbox"
    assert _AUTOPILOT_FLAGS is AUTOPILOT_FLAGS


def test_detect_tags_every_cli_with_its_autopilot_flag():
    """Every record carries the field — "" for a CLI with no hands-off flag, so
    the UI can say "this one has none" instead of staying silent."""
    clis = detect_ai_clis()
    assert clis, "no CLI catalog"
    for c in clis:
        assert "autopilot_flag" in c, f"{c['id']} has no autopilot_flag"
        assert c["autopilot_flag"] == AUTOPILOT_FLAGS.get(c["id"], "")
    by_id = {c["id"]: c for c in clis}
    assert by_id["claude"]["autopilot_flag"] == "--dangerously-skip-permissions"
    assert by_id["grok"]["autopilot_flag"] == ""


def test_ai_clis_endpoint_serves_the_flag(tmp_path):
    """The dashboard's actual source of truth is the HTTP payload."""
    app = create_app(str(tmp_path))
    with TestClient(app) as client:
        resp = client.get("/terminals/ai-clis")
        assert resp.status_code == 200
        clis = resp.json()["clis"]
        by_id = {c["id"]: c for c in clis}
        assert by_id["claude"]["autopilot_flag"] == "--dangerously-skip-permissions"
        assert (
            by_id["codex"]["autopilot_flag"]
            == "--dangerously-bypass-approvals-and-sandbox"
        )


def test_studio_start_launches_exactly_the_advertised_flag(tmp_path):
    """The command the daemon reports is the command it typed, and it carries
    the SAME flag the UI showed. This is the mismatch the review found."""
    app = create_app(str(tmp_path))
    with TestClient(app) as client:
        clis = {c["id"]: c for c in client.get("/terminals/ai-clis").json()["clis"]}
        if not clis["claude"]["installed"]:
            return  # nothing to launch on this machine; the endpoint tests cover the seam
        resp = client.post(
            "/creative/studio/start",
            json={"cli": "claude", "cwd": str(tmp_path), "autopilot": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert clis["claude"]["autopilot_flag"] in body["command"]
        assert body["autopilot"] is True
        client.post(f"/terminals/{body['terminal_id']}/close")


def _autopilot_label(src: str) -> str:
    """The rendered Autopilot <label> block — the text a user actually reads.

    Scoped deliberately: the file's COMMENTS name the stale flags on purpose (to
    record what went wrong), and a whole-file search would fire on those.
    """
    start = src.index("Autopilot</span>")
    end = src.index("</label>", start)
    return src[start:end]


def test_autopilot_label_names_no_stale_flag():
    """The two stale claims must not come back as hand-written prose."""
    label = _autopilot_label(_PAGE.read_text(encoding="utf-8"))
    assert (
        "--full-auto" not in label
    ), "the removed codex flag is being advertised again"
    # The Shift+Tab MECHANISM still exists as a daemon-side fallback, but the
    # label must not tell the user that is how autopilot engages.
    assert not re.search(
        r"Shift\+Tab", label
    ), "the label describes a mechanism the launch flag replaced"
    # And no hardcoded flag string at all: the flag is rendered from data.
    assert not re.search(
        r"--dangerously-[a-z-]+", label
    ), "the label restates a flag instead of rendering the daemon's"


def test_label_and_header_render_the_real_values():
    """Assert the CALL SITES: a deleted render leaves prose-only tests green."""
    src = _PAGE.read_text(encoding="utf-8")
    # The label reads the daemon's flag rather than restating one.
    assert "selectedCli?.autopilot_flag" in src
    assert "{autopilotFlag}" in src
    # The setup card previews the launch line...
    assert "launchPreview" in src
    # ...and the running session shows what ACTUALLY launched.
    assert "{session.command}" in src
    # The field is typed, or the build would not see it.
    assert "autopilot_flag?: string" in _TYPES.read_text(encoding="utf-8")


def test_label_states_the_real_posture():
    """Autopilot stays ON by default (unattended generation is the Studio's
    whole point — two live failures came from non-hands-off modes), so the label
    is what has to be honest: no approvals, no sandbox, and an opt-out."""
    src = _PAGE.read_text(encoding="utf-8")
    label = _autopilot_label(src)
    assert "no approval prompts and no sandbox" in label
    assert "Leave it" in label and "off" in label  # the opt-out is stated
    # The default is unchanged — this fix is about honesty, not capability.
    assert "initialStore.autopilot ?? true" in src
