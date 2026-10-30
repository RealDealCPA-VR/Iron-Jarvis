"""v1.408.0 review — the memory-profile SEAM: the real daemon's answers, fed
through the screen.

The dashboard half (Set up tools on an MCP memory base, Check and Try a test
save on a memory profile) was built against stand-ins of the contract. This
file asks the REAL routes — a real ``McpBrainConnector`` over a fake MCP
client with the user's wiki tools — and writes what they answer to
``dashboard/__tests__/fixtures/memory-profile-seams-v1408.json``;
``dashboard/__tests__/memory-profile-seams-v1408.test.tsx`` feeds that file
through the real components. A shape change on either side fails one of the
two. Regenerate with ``IJ_REGEN_MEMORY_PROFILE_SEAMS=1`` after an intended
change.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from iron_jarvis.ltm.sources import CustomSourceStore
from tests.test_memory_profile_fixes_v1408 import CONFIRMED, _app_with_wiki

FIXTURE = (
    Path(__file__).resolve().parents[1] / "dashboard" / "__tests__" / "fixtures" / "memory-profile-seams-v1408.json"
)
REGEN = os.environ.get("IJ_REGEN_MEMORY_PROFILE_SEAMS") == "1"
BASE = "hermes-brain"
ROLES = f"/ltm/sources/{BASE}/roles"


def _stable(value):
    """Drop what changes run to run (ids, times, a record's folder). A tool's
    argument may itself be called ``path`` — only a RECORD's path (a dict with
    a ``kind``) is dropped."""
    if isinstance(value, dict):
        record = "kind" in value
        return {k: _stable(v) for k, v in value.items()
                if k not in ("id", "created_at", "updated_at") and not (record and k == "path")}
    if isinstance(value, list):
        return [_stable(v) for v in value]
    return value


def _answer(resp) -> dict:
    return {"status": resp.status_code, "body": _stable(resp.json())}


def test_the_real_answers_are_what_the_screen_reads(tmp_path):
    app, client, platform, conn, fake = _app_with_wiki(tmp_path)
    CustomSourceStore(platform.engine).add(BASE, "mcp", endpoint_url="http://wiki.invalid/mcp")
    CustomSourceStore(platform.engine).add("notes", "markdown", path=str(tmp_path / "notes"))
    pid = client.get("/brain").json()["profiles"][0]["id"]
    out: dict[str, dict] = {}

    out["listing_unconfirmed"] = _answer(client.get("/ltm/sources"))
    out["roles_unconfirmed"] = _answer(client.get(ROLES))
    out["check_unconfirmed"] = _answer(client.post(f"/brain/profiles/{pid}/check"))
    # The proposal as it stands misses the wiki's required type and tags.
    proposed = out["roles_unconfirmed"]["body"]["proposed"]
    out["put_refused"] = _answer(client.put(ROLES, json={"roles": {"create": proposed["create"]}}))
    out["put_confirmed"] = _answer(client.put(ROLES, json={"roles": CONFIRMED}))
    out["listing_confirmed"] = _answer(client.get("/ltm/sources"))
    out["check_confirmed"] = _answer(client.post(f"/brain/profiles/{pid}/check"))
    out["test_save_ok"] = _answer(client.post(f"/brain/profiles/{pid}/test-save"))
    out["check_gone_profile"] = _answer(client.post("/brain/profiles/nope/check"))
    out["roles_not_mcp"] = _answer(client.get("/ltm/sources/notes/roles"))

    # The server stops answering after the tools were confirmed.
    real_list = fake.list_tools

    def boom():
        raise ConnectionError("the wiki did not answer")

    fake.list_tools = boom  # type: ignore[method-assign]
    out["roles_unreachable"] = _answer(client.get(ROLES))
    fake.list_tools = real_list  # type: ignore[method-assign]
    out["put_nothing"] = _answer(client.put(ROLES, json={"roles": {r: None for r in CONFIRMED}}))

    # The contract the screen relies on.
    assert out["listing_unconfirmed"]["body"]["bases"]
    hermes = [b for b in out["listing_unconfirmed"]["body"]["bases"] if b["name"] == BASE][0]
    assert hermes["kind"] == "mcp" and hermes["tools_set_up"] is False
    assert [b for b in out["listing_confirmed"]["body"]["bases"] if b["name"] == BASE][0]["tools_set_up"] is True
    assert out["roles_unconfirmed"]["body"]["saved"] is None
    assert out["put_refused"]["status"] == 400
    assert out["put_confirmed"]["status"] == 200 and out["put_confirmed"]["body"]["saved"]
    assert out["check_gone_profile"]["status"] == 404
    assert out["check_gone_profile"]["body"]["detail"] != "Not Found"
    assert out["roles_not_mcp"]["status"] == 404
    assert out["put_nothing"]["body"]["saved"] is None
    assert out["roles_unreachable"]["body"].get("error") and out["roles_unreachable"]["body"]["saved"]
    assert out["test_save_ok"]["body"]["ok"] is True
    assert out["put_confirmed"]["body"]["saved"]["read"] == CONFIRMED["read"], "a param called path must survive"

    text = json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if REGEN or not FIXTURE.exists():
        FIXTURE.write_bytes(text.encode("utf-8"))
    assert json.loads(FIXTURE.read_bytes().decode("utf-8")) == json.loads(text), (
        "the daemon's answers changed; regenerate with IJ_REGEN_MEMORY_PROFILE_SEAMS=1 "
        "and check the dashboard seam test"
    )
