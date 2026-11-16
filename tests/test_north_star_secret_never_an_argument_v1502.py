"""v1.502.0 (north-star plan 2.11): a secret is never a tool argument.

THE DEFECT. ``secrets/tools.SecretSetTool`` took the secret VALUE as an
argument the MODEL writes, in agent runs (chat already swaps the tool for
``config_secret``'s secure card, ``daemon/chat_turn.py``). The root rule says
a secret is typed on the secure card and kept in the vault, never a tool
argument: a value in a model's call has already passed through the model and
its provider, and a model that "saves" a key it read in a document stores
someone else's words as the user's credential.

THE RULE. ``secret_set`` is name-only. Its schema offers no value; a call
that carries one anyway stores NOTHING, keeps nothing of it (the ledger row
is redacted and the refusal never repeats it) and says where the owner saves
the key. An agent run has no secure card to raise (a card needs a chat page
to draw it), so every agent-run call is that honest refusal. No builtin agent
roster holds ``secret_set`` (only a roster a user writes, or a one-run grant,
can), so it stays registered, refusing, rather than vanishing from rosters
and grants that name it. Proved through the real registry's invoke.
"""

from __future__ import annotations

from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext

VALUE = "not-a-real-credential-for-this-test"


def _world(tmp_path):
    p = build_platform(str(tmp_path))
    ctx = ToolContext(workspace=tmp_path, session_id="s1", agent_run_id="r1",
                      config=p.config, event_bus=p.event_bus, engine=p.engine)
    return p, ctx


def test_the_tool_offers_no_value_argument(tmp_path):
    p, _ctx = _world(tmp_path)
    spec = p.registry.get("secret_set").spec()
    assert "value" not in spec["input_schema"]["properties"]
    assert "value" not in spec["input_schema"].get("required", [])


async def test_a_value_in_the_call_is_never_stored_kept_or_repeated(tmp_path):
    p, ctx = _world(tmp_path)
    res = await p.registry.invoke(
        "secret_set", {"name": "notion", "value": VALUE, "kind": "token"},
        ctx, p.permissions, agent_overrides={"secret_set": "allow"},
    )
    assert res.ok is False
    assert p.secrets.get("notion") is None, "a model-written value reached the vault"
    words = f"{res.error} {res.output}"
    assert VALUE not in words
    assert "Settings" in words and "Secrets" in words, words
    with session_scope(p.engine) as db:
        row = db.exec(select(ToolInvocation).where(ToolInvocation.tool == "secret_set")).first()
    assert row is not None and VALUE not in (row.args_json or "") and VALUE not in (row.output or "")


async def test_a_name_only_call_says_where_the_owner_saves_it(tmp_path):
    p, ctx = _world(tmp_path)
    res = await p.registry.invoke(
        "secret_set", {"name": "notion", "kind": "token"},
        ctx, p.permissions, agent_overrides={"secret_set": "allow"},
    )
    assert res.ok is False
    assert "notion" in res.error and "Secrets" in res.error, res.error
    assert p.secrets.get("notion") is None
