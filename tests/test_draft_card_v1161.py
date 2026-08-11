"""The draft fence reaches the model, and both sides name the same word (v1.161.0).

THE FEATURE IS A THREE-PARTY AGREEMENT and every party can fail silently:

1. The daemon tells the model to fence drafts as ```email (``DRAFT_BLOCK``).
2. The model emits that fence.
3. The dashboard turns that fence into a copyable card (``DRAFT_LANGS`` in
   ``components/chat/DraftCard.tsx``).

If (1) reaches only one of the two chat lanes, the card appears in one and not
the other and it reads as flakiness. If (1) and (3) drift apart — someone
renames the fence on either side — the model emits a word nobody renders, and
the user sees a grey code block instead of a card. Neither failure raises
anything, which is why they are asserted here rather than left to be noticed.

The lock-step risk is not hypothetical in this file's neighbourhood: chat has
TWO lanes (``chat_turn.run_chat_turn`` and the streaming mirror in
``routes/chat.py``), the streaming one is what a user actually watches write an
email, and this repo already carries a rule about the two copies drifting.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from iron_jarvis.daemon.chat_turn import DRAFT_BLOCK, run_chat_turn

_DASHBOARD = Path(__file__).resolve().parents[1] / "dashboard"
_CARD = _DASHBOARD / "components" / "chat" / "DraftCard.tsx"


def _dashboard_langs() -> set[str]:
    """The fence words the dashboard will actually render as a card."""
    source = _CARD.read_text(encoding="utf-8")
    match = re.search(r"DRAFT_LANGS\s*=\s*new Set\(\[(.*?)\]\)", source, re.S)
    assert match, "DRAFT_LANGS is no longer a literal Set — update this test"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _fence_words(block: str) -> set[str]:
    """Fence words the instruction actually names, e.g. ```email."""
    return set(re.findall(r"```([a-z]+)", block))


def test_the_instruction_names_a_fence_the_dashboard_renders():
    """The silent-failure case: rename the fence on either side and the model
    emits a word nobody turns into a card."""
    named = _fence_words(DRAFT_BLOCK)
    assert named, "DRAFT_BLOCK names no fence at all, so nothing can render"
    unknown = named - _dashboard_langs()
    assert not unknown, (
        f"chat_turn tells the model to use {sorted(unknown)}, which "
        f"DraftCard.tsx does not render — the reply would show a code block"
    )


def test_the_instruction_reaches_BOTH_chat_lanes():
    """Chat has two lanes and the streaming one is the one users watch."""
    # The ADDITION, not the mere name: `DRAFT_BLOCK` also appears in the import
    # line, so `"DRAFT_BLOCK" in source` stays true with the injection deleted.
    # Caught by the mutation sweep — the first version of this test passed with
    # the streaming lane's injection removed, i.e. it asserted nothing.
    turn = inspect.getsource(run_chat_turn)
    assert re.search(r"system\s*\+=\s*DRAFT_BLOCK", turn), (
        "the non-streaming lane never adds the instruction"
    )

    stream = (
        Path(__file__).resolve().parents[1]
        / "src" / "iron_jarvis" / "daemon" / "routes" / "chat.py"
    ).read_text(encoding="utf-8")
    assert re.search(r"system\s*\+=\s*DRAFT_BLOCK", stream), (
        "the STREAMING lane never adds the instruction — drafts would only be "
        "boxed on non-streamed turns, which reads as the feature being flaky"
    )


def test_the_instruction_is_added_before_the_budget_planner():
    """This repo's rule: anything added to the system prompt after the planner
    runs is a cost the budget cannot see."""
    turn = inspect.getsource(run_chat_turn)
    assert turn.index("DRAFT_BLOCK") < turn.index("plan = _plan_context"), (
        "DRAFT_BLOCK is added after the planner, so its tokens are invisible "
        "to the context budget"
    )


def test_the_instruction_stays_short():
    """Charged on EVERY chat request. A section that grows costs more than the
    feature is worth; the cap is deliberately tight."""
    assert len(DRAFT_BLOCK) < 700, f"{len(DRAFT_BLOCK)} chars is an essay, not a rule"


def test_the_instruction_says_what_NOT_to_fence():
    """Without the negative case the fence gets used for anything email-shaped,
    including messages the assistant is only describing — and a card offering
    to copy something the user is not sending is noise."""
    assert "never" in DRAFT_BLOCK.lower()


def test_the_instruction_asks_for_a_subject_line():
    """The subject is the half the user cannot retype from memory, and
    `splitSubject` only lifts it when it is the FIRST line."""
    assert "subject:" in DRAFT_BLOCK.lower()


def test_the_electron_bridge_exposes_the_rich_copy():
    """The desktop app is where this is used daily, and `navigator.clipboard`
    can be permission-gated inside Electron — the native path is the reliable
    one. A card calling a bridge method that does not exist falls back to plain
    text and silently loses the formatting."""
    preload = (_DASHBOARD.parent / "desktop" / "preload.js").read_text(encoding="utf-8")
    main = (_DASHBOARD.parent / "desktop" / "main.js").read_text(encoding="utf-8")
    assert "clipboardWriteHtml" in preload, "the renderer cannot reach a rich copy"
    assert 'ipcMain.handle("clipboard:writeHtml"' in main, "no handler for the channel"
    # ONE write carrying BOTH flavours. Two calls would clobber each other and
    # leave whichever ran last, losing the other.
    assert re.search(r"clipboard\.write\(\{[^}]*text:[^}]*html:", main, re.S), (
        "the handler must write text and html in a single clipboard.write"
    )
