"""The USE CASE shown on each gallery tile (v1.96.0).

``run_code`` names an unnamed script ``run_<epoch>``, so a tile with no purpose
reads "run_1753459200" and the gallery is useless. Purpose comes from what the
agent STATED, else from the code's own header — and never from invention.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.codelab.purpose import MAX_PURPOSE, derive_purpose, purpose_for
from iron_jarvis.daemon.app import create_app


def test_stated_purpose_always_wins():
    assert (
        purpose_for("# something else", "python", "Rename 400 invoices")
        == "Rename 400 invoices"
    )


def test_derives_from_a_python_docstring():
    src = '"""Merge the quarterly CSVs into one workbook."""\nimport csv\n'
    assert derive_purpose(src) == "Merge the quarterly CSVs into one workbook."


def test_derives_from_a_leading_comment_skipping_shebang_and_rules():
    src = "#!/usr/bin/env python\n# ----------------\n# Strip PII from the export\nimport re\n"
    assert derive_purpose(src) == "Strip PII from the export"


def test_ignores_comments_that_come_after_real_code():
    """A comment further down describes a STEP, not the script — putting it on
    the tile would mislead about what the script is for."""
    src = "import os\n# loop over the files\nfor f in os.listdir('.'):\n    pass\n"
    assert derive_purpose(src) == ""


def test_strips_a_redundant_label():
    assert derive_purpose("# Purpose: tidy the downloads folder\nimport os\n") == (
        "tidy the downloads folder"
    )


def test_powershell_block_comment():
    src = "<#\nResize every PNG in the folder to 1080p\n#>\nGet-ChildItem\n"
    assert derive_purpose(src, "powershell") == "Resize every PNG in the folder to 1080p"


def test_never_invents_a_purpose():
    """Blank beats a fabricated summary of code we did not understand — the
    tile says 'No stated purpose' instead."""
    assert derive_purpose("import os\nprint(os.getcwd())\n") == ""
    assert purpose_for("x = 1", "python", "   ") == ""


def test_purpose_is_capped_for_a_one_line_tile():
    long_state = "z" * (MAX_PURPOSE + 200)
    assert len(purpose_for("x=1", "python", long_state)) == MAX_PURPOSE
    assert len(derive_purpose('"""' + "y" * (MAX_PURPOSE + 200) + '"""')) == MAX_PURPOSE


def test_empty_source_is_safe():
    assert derive_purpose("") == ""
    assert derive_purpose("   \n\n") == ""


# --- the whole way through --------------------------------------------------


def test_a_saved_script_carries_its_use_case_to_the_gallery(tmp_path):
    """List rows must include the description — it is the tile's headline."""
    client = TestClient(create_app(str(tmp_path)))
    client.post(
        "/code-artifacts",
        json={
            "name": "run_1753459200",  # the useless auto-name
            "language": "python",
            "source": '"""Rename the scanned invoices to INV-<date>.pdf."""\n',
        },
    )
    row = client.get("/code-artifacts").json()["artifacts"][0]
    assert row["description"] == "Rename the scanned invoices to INV-<date>.pdf."


def test_run_code_passes_the_agent_s_stated_purpose_to_the_sink(tmp_path):
    import asyncio

    from iron_jarvis.tools.base import ToolContext
    from iron_jarvis.tools.runcode import RunCodeTool

    seen: list[str] = []

    def sink(name, language, code, session_id, exit_code, output, purpose=""):
        seen.append(purpose)

    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(
        workspace=ws, session_id="s", agent_run_id="r",
        config=None, event_bus=None, engine=None,
    )
    asyncio.run(
        RunCodeTool(sink=sink).execute(
            {
                "language": "python",
                "code": "print(1)",
                "purpose": "Count the rows in the ledger export",
            },
            ctx,
        )
    )
    assert seen == ["Count the rows in the ledger export"]


def test_the_REAL_platform_wiring_records_purpose(tmp_path):
    """End-to-end through build_platform, not a hand-rolled sink.

    ``RunCodeTool._record`` swallows sink exceptions so bookkeeping can never
    break an agent's task — which also means a signature mismatch between the
    tool and the platform's sink fails SILENTLY, recording nothing. (That is
    exactly what happened while developing this: adding ``purpose`` broke a
    6-arg sink and the only symptom was an empty store.) This test exercises the
    real wiring so that drift is a red test instead of silence.
    """
    import asyncio

    from iron_jarvis.platform import build_platform
    from iron_jarvis.tools.base import ToolContext

    platform = build_platform(str(tmp_path))
    tool = platform.registry.get("run_code")
    assert tool is not None

    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(
        workspace=ws, session_id=None, agent_run_id="r",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
    )
    result = asyncio.run(
        tool.execute(
            {
                "language": "python",
                "code": "print('real wiring')",
                "purpose": "Prove the sink is actually connected",
            },
            ctx,
        )
    )
    assert result.ok, result.error
    saved = platform.code_artifacts.list()
    assert len(saved) == 1, "the platform sink recorded nothing — wiring drifted"
    assert saved[0].description == "Prove the sink is actually connected"
    assert saved[0].last_exit_code == 0
