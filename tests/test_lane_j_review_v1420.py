"""v1.420.0 — independent review of lane J (C3.2, C3.5, C3.7, C3.8) and the
coding offer's wiring (C6.1).

THE NOTE (C3.2): the "what I did in that reply" note rides the history the
NEXT model reads (possibly another vendor's, v1.368.0), so it follows the
command-line rule the session's Programs list follows (v1.404.0
``harness/process._shown_command``): the word after a secret-named flag is
hidden even when it has no known key shape; a command the saved input CUT
(``step_detail``'s 300 characters) is marked cut, never shown as complete; and
a reply that touched many files is cut at WHOLE names ("… and N more"), never
mid-path.

THE CODING OFFER (C6.1): ``tools.autoselect.coding_offer`` is wired into BOTH
chat lanes beside the Connect door (``chat_turn.with_coding_offer``): a plain
chat turn asking to build software ends with one door into a coding session
(v1.426.0: Chat › Code — the request carried into its box); a
physical thing, a coding session's own turn, a Build pane's chat, the phone
and the browser sidebar get none. Driven through the REAL lanes (the model is
the only double and never calls a tool).
"""

from __future__ import annotations

import asyncio
import json

from iron_jarvis.daemon.chat_turn import run_chat_turn, with_coding_offer
from iron_jarvis.daemon.doors import MAX_DOORS, collect_doors
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.harness import sessions as S
from iron_jarvis.providers.failure_words import phone_surface
from iron_jarvis.tools.autoselect import CODING_OFFER_HREF, CODING_OFFER_LABEL
from tests._fakes.harness_lane import body as harness_body
from tests._fakes.harness_lane import no_finish_check, post, stream, wire
from tests.test_connect_door_v13590 import (  # noqa: F401 — fixtures by name
    _PostModel,
    _StreamModel,
    _body,
    _clean_env,
    _done,
    _stream,
    app,
)

CODING_DOOR = {"href": CODING_OFFER_HREF, "label": CODING_OFFER_LABEL}
SOFTWARE = "build me a todo app in react"
SHED = "build me a shed"


def _shell(command: str, *, cut: bool = False) -> dict:
    raw = json.dumps({"command": command})
    return {"name": "shell", "ok": True, "detail": {"input": raw[:300] if cut else raw}}


# =========================================================================== #
# 1. the note
# =========================================================================== #


def test_a_secret_after_a_token_flag_is_hidden_even_with_no_known_shape():
    """``--token hunter2secretvalue`` has no key SHAPE, so the shared mask
    alone left it readable in the note the next model (maybe another vendor's)
    reads. The Programs list's own rule hides the word after the flag."""
    note = S.last_work_note([_shell("deploy --token hunter2secretvalue --env prod")])
    assert "hunter2secretvalue" not in note, note
    assert "deploy" in note and "--env prod" in note, "the rest of the command stays"
    note = S.last_work_note([_shell("tool --api-key plainsecret123 run")])
    assert "plainsecret123" not in note, note


def test_a_url_password_and_a_bearer_value_are_masked():
    note = S.last_work_note([_shell("git clone https://bob:Sup3rSecret@github.com/acme/x.git")])
    assert "Sup3rSecret" not in note
    note = S.last_work_note([_shell("curl -H 'Authorization: Bearer abcdef0123456789abcdef' https://x")])
    assert "abcdef0123456789abcdef" not in note


def test_a_command_the_saved_input_cut_is_marked_cut():
    """``step_detail`` cuts the saved input at 300 characters. A command that
    begins late in that text (here after a long key that sorts before it)
    came back as a few clean words — a COMPLETE-looking command the model
    never ran. A cut command ends with "…"."""
    raw = json.dumps({"args": "x" * 270, "command": "rm -rf build && make all"})[:300]
    assert not raw.endswith('"}')
    note = S.last_work_note([{"name": "shell", "ok": True, "detail": {"input": raw}}])
    ran = note.split("ran `", 1)[1].split("`", 1)[0]
    assert ran.endswith("…"), f"a cut command reads as complete: {ran!r}"
    # Control: a whole command is never marked.
    whole = S.last_work_note([_shell("python -m pytest -q")])
    assert "ran `python -m pytest -q` (succeeded)" in whole


def test_forty_files_are_cut_at_whole_names_never_mid_path():
    steps = [
        {"name": "write_file", "ok": True, "target": f"src/pkg/module_{i:03d}/very_long_file_name_{i}.py"}
        for i in range(40)
    ]
    note = S.last_work_note(steps)
    assert len(note) <= S.LAST_WORK_MAX_CHARS
    body = note.split(S.LAST_WORK_LEAD, 1)[1].rstrip("].")
    assert "…" not in body, f"a path was cut mid-name: {body[-120:]!r}"
    named = [f"src/pkg/module_{i:03d}/very_long_file_name_{i}.py" for i in range(40)]
    shown = [n for n in named if n in body]
    assert shown and len(shown) < 40
    assert f"and {40 - len(shown)} more" in body, body[-80:]


def test_the_note_rides_the_newest_reply_only_and_never_a_covered_line():
    steps = [{"name": "read_file", "ok": True, "target": "old.py"}]
    newest = [{"name": "read_file", "ok": True, "target": "new.py"}]
    msgs = [
        {"role": "user", "content": "q1"}, {"role": "assistant", "content": "A1", "steps": steps},
        {"role": "user", "content": "q2"}, {"role": "assistant", "content": "A2", "steps": newest},
        {"role": "user", "content": "q3"},
    ]
    out = S.history_of(msgs, last_work=True)
    assert out[1]["content"] == "A1" and "new.py" in out[3]["content"]
    # The plain rule (every other reader) never carries it.
    assert all(S.LAST_WORK_LEAD not in m["content"] for m in S.history_of(msgs))


# =========================================================================== #
# 2. the coding offer, through both real lanes
# =========================================================================== #


def _stream_done(app, text: str, **kw):  # noqa: F811 — the fixture is the app
    model = _StreamModel(text="Sure, here is how.")
    app.state.platform.router.stream = model
    done = _done(_stream(app, _body(text), **kw))
    assert model.called == []
    return done


def _post_res(app, text: str, **extra):  # noqa: F811
    platform = app.state.platform
    platform.router = _PostModel(text="Sure, here is how.")
    b = ChatBody(messages=[{"role": "user", "content": text}], auto_tools=True, **extra)
    return asyncio.run(run_chat_turn(platform, {}, b))


def test_a_request_to_build_software_ends_with_the_coding_door_in_the_stream_lane(app):  # noqa: F811
    done = _stream_done(app, SOFTWARE)
    assert CODING_DOOR in done["doors"], done["doors"]
    assert done["reply"] == "Sure, here is how.", "only a link: the chat still answers"


def test_a_request_to_build_software_ends_with_the_coding_door_in_the_post_lane(app):  # noqa: F811
    res = _post_res(app, SOFTWARE)
    assert CODING_DOOR in res["doors"], res["doors"]


def test_a_shed_is_no_software_in_either_lane(app):  # noqa: F811
    assert CODING_DOOR not in _stream_done(app, SHED)["doors"]
    assert CODING_DOOR not in _post_res(app, SHED)["doors"]


def test_a_build_panes_chat_gets_no_coding_door(app):  # noqa: F811
    assert CODING_DOOR not in _post_res(app, SOFTWARE, pane_id="pane_1")["doors"]


def test_the_phone_and_the_sidebar_get_no_coding_door(app):  # noqa: F811
    with phone_surface():
        assert CODING_DOOR not in _post_res(app, SOFTWARE)["doors"]
    done = _stream_done(app, SOFTWARE, arm_family=frozenset())
    assert CODING_DOOR not in done["doors"]


async def test_a_coding_sessions_own_turn_gets_no_coding_door_in_either_lane(tmp_path, monkeypatch):
    from iron_jarvis.platform import build_platform

    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    no_finish_check(ws)
    wire(platform, monkeypatch, ["Done."])
    out = await stream(platform, harness_body(ws, SOFTWARE))
    assert out["done"] is not None and CODING_DOOR not in out["done"]["doors"]
    wire(platform, monkeypatch, ["Done."])
    res = await post(platform, harness_body(ws, SOFTWARE))
    assert CODING_DOOR not in res["doors"]


def test_the_helper_keeps_the_door_cap_and_never_doubles_the_door():
    b = ChatBody(messages=[{"role": "user", "content": SOFTWARE}])
    full = [{"href": f"/x{i}", "label": str(i)} for i in range(MAX_DOORS)]
    assert len(collect_doors(with_coding_offer(full, b))) == MAX_DOORS
    once = with_coding_offer(with_coding_offer([], b), b)
    assert once == [CODING_DOOR]
    assert with_coding_offer([], b, escalate=True) == [], "an escalated turn offers nothing"
    assert with_coding_offer([], b, harness=object()) == []


def test_the_door_opens_chat_code_whose_page_reads_view_code():
    """v1.426.0 (Wave 6, E2), moved on purpose: the door opens Chat › Code
    (the coding session the owner uses; Build › Code opened EMPTY and the
    request was retyped). The label says so, and the chat page reads
    `?view=code` (``lib/chatSurface.viewFromSearch``)."""
    from pathlib import Path

    assert CODING_OFFER_HREF == "/chat?view=code"
    assert "Chat › Code" in CODING_OFFER_LABEL
    src = (Path(__file__).resolve().parents[1] / "dashboard" / "lib" / "chatSurface.ts").read_text(
        encoding="utf-8"
    )
    assert 'q.get("view")' in src and 'CODE_VIEW_HREF = "/chat?view=code"' in src
