"""A refused turn tells the truth, calmly (v1.329.0, calm chat J1).

THE AUDIT. Steering a real /chat/stream to a dead Anthropic-compatible fleet
node showed the chat this, verbatim from the router:

    fleet-fa2-anth isn't connected right now, so this turn was not answered.
    No substitute was used on purpose — a stand-in answer would look like real
    work that never happened. ...

Two problems in one sentence: a dash aside (the calm copy rule is plain
sentences) and the RAW provider id where the user had given the endpoint a
label. The receipt names models by what the user knows them as; the refusal
now names an endpoint the same way, and the id stays on the event payload
(``requested``) for the logs and the mission's route note.

WORDING ONLY. Every assertion here also checks that the turn still REFUSES and
that nothing answered in its place (the v1.162.0 rule: never auto-switch).
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from iron_jarvis.core.events import EventType
from iron_jarvis.fleet.models import FleetNode
from iron_jarvis.platform import build_platform
from iron_jarvis.providers import router as R
from iron_jarvis.providers.adapters.base import LLMMessage, ProviderError

DASH = "—"
LABEL = "Office Spark"
NODE = "fa2-anth"
PROVIDER = f"fleet-{NODE}"


class _Spy:
    """Records what the router publishes, then hands it to the real bus."""

    def __init__(self, bus):
        self._bus = bus
        self.seen: list[tuple[str, dict, object]] = []

    async def publish(self, type, payload=None, session_id=None):
        self.seen.append((type, dict(payload or {}), session_id))
        return await self._bus.publish(type, payload, session_id=session_id)

    def downgraded(self):
        return [p for t, p, _ in self.seen if t == EventType.PROVIDER_DOWNGRADED]


def _platform_with_dead_node(tmp_path, label: str = LABEL):
    """The audit's shape on a REAL platform: a routable Anthropic-protocol
    fleet node, the default provider, which the sampler last saw unreachable."""
    platform = build_platform(str(tmp_path))
    platform.fleet.add(
        FleetNode(
            id=NODE,
            label=label,
            base_url="http://127.0.0.1:9/v1",
            routable=True,
            protocol="anthropic",
        )
    )
    platform.fleet.register_providers(platform.providers)
    platform.fleet.set_reachable(NODE, False)
    platform.config.default_provider = PROVIDER
    spy = _Spy(platform.router.event_bus)
    platform.router.event_bus = spy
    return platform, spy


def _msgs():
    return [LLMMessage(role="user", content="Say hello.")]


@pytest.mark.asyncio
async def test_complete_refusal_names_the_label_in_plain_sentences(tmp_path):
    platform, spy = _platform_with_dead_node(tmp_path)
    assert platform.providers.available(PROVIDER) is False

    with pytest.raises(ProviderError) as ei:
        await platform.router.complete(system="", messages=_msgs(), tools=[])
    text = str(ei.value)

    assert text.startswith(f"{LABEL} isn't connected right now, so this turn was not answered.")
    assert PROVIDER not in text, "the raw provider id reached the user's words"
    assert DASH not in text, text
    assert (
        "No substitute was used on purpose. A stand-in answer would look like"
        " real work that never happened." in text
    )
    assert "Bring that endpoint back up, or pick another model for this chat, and retry." in text

    [event] = spy.downgraded()
    # The id stays for the logs; the label rides LAST beside it.
    assert event["requested"] == PROVIDER
    assert event["used"] == "none"
    assert event["reason"] == "not connected"
    assert event["label"] == LABEL
    assert list(event)[-1] == "label"
    # Nothing answered and nothing stood in: no route was published.
    assert not [t for t, _, _ in spy.seen if t == EventType.PROVIDER_ROUTED]


@pytest.mark.asyncio
async def test_stream_refusal_says_the_same_words(tmp_path):
    platform, spy = _platform_with_dead_node(tmp_path)
    frames = []
    with pytest.raises(ProviderError) as ei:
        async for frame in platform.router.stream(system="", messages=_msgs(), tools=[]):
            frames.append(frame)
    assert frames == [], "a refused stream must not yield a single frame"
    text = str(ei.value)
    assert text.startswith(f"{LABEL} isn't connected right now")
    assert PROVIDER not in text and DASH not in text
    [event] = spy.downgraded()
    assert event["label"] == LABEL and event["requested"] == PROVIDER


@pytest.mark.asyncio
async def test_an_unlabelled_node_keeps_its_id(tmp_path):
    platform, spy = _platform_with_dead_node(tmp_path, label="")
    with pytest.raises(ProviderError) as ei:
        await platform.router.complete(system="", messages=_msgs(), tools=[])
    assert str(ei.value).startswith(f"{PROVIDER} isn't connected right now")
    assert spy.downgraded()[0]["label"] == PROVIDER


# --------------------------------------------------------------------------- #
# Every refusal kind, with a fake manager (no fleet, no network).
# --------------------------------------------------------------------------- #
class _Node:
    label = "  Office   Spark  "


class _Adapter:
    provider = PROVIDER
    node = _Node()


class _Manager:
    def __init__(self, broken: bool = False):
        self.broken = broken

    def get(self, name, model=None):
        if self.broken:
            raise RuntimeError("factory fault")
        if name == PROVIDER:
            return _Adapter()
        raise KeyError(name)

    def available(self, name):
        return False


class _Bus:
    def __init__(self):
        self.seen = []

    async def publish(self, type, payload=None, session_id=None):
        self.seen.append((type, dict(payload or {})))


def _router(manager=None):
    return R.ModelRouter(manager or _Manager(), default_provider="mock", event_bus=_Bus())


KINDS = [
    {"kind": "unreachable"},
    {"kind": "timeout"},
    {"kind": "interrupted"},
    {"kind": "interrupted", "partial": True},
    {"kind": "cooldown", "retry_in": 12.2},
    {"kind": "answered_error", "exc": ProviderError("upstream GPU box unreachable.", status_code=500)},
]


@pytest.mark.parametrize("pinned", [False, True], ids=["default", "pinned"])
@pytest.mark.parametrize("kw", KINDS, ids=lambda k: k["kind"] + ("-partial" if k.get("partial") else ""))
def test_every_refusal_kind_is_plain_and_names_the_label(kw, pinned):
    text = str(_router()._unavailable_error(PROVIDER, pinned, **kw))
    assert DASH not in text, text
    assert "Office Spark" in text, text  # whitespace in the label is collapsed
    assert PROVIDER not in text, text
    if not pinned and kw["kind"] != "answered_error":
        assert "No substitute was used on purpose. A stand-in answer" in text


def test_answered_error_keeps_the_endpoints_own_words_and_the_policy():
    exc = ProviderError("model 'llama3.1' not found.", status_code=404)
    text = str(_router()._unavailable_error(PROVIDER, False, kind="answered_error", exc=exc))
    assert text.startswith("Office Spark answered HTTP 404: model 'llama3.1' not found.")
    assert "No substitute was used on purpose (local_primary_policy=refuse)." in text
    assert ".." not in text


def test_cooldown_keeps_the_lead_the_retry_countdown_reads():
    text = str(_router()._unavailable_error(PROVIDER, False, kind="cooldown", retry_in=44.1))
    assert text.startswith("Office Spark is in cooldown, retry in 45 s. It failed")


def test_a_cloud_drop_mid_answer_says_press_retry_in_plain_sentences():
    text = str(_router()._unavailable_error("openai", False, kind="interrupted", partial=True))
    assert "Press Retry. If it keeps happening" in text
    assert DASH not in text


def test_a_broken_manager_never_turns_wording_into_a_failure():
    r = _router(_Manager(broken=True))
    assert r._endpoint_label(PROVIDER) == PROVIDER
    text = str(r._unavailable_error(PROVIDER, False))
    assert text.startswith(f"{PROVIDER} isn't connected right now")


def test_a_non_fleet_provider_is_never_looked_up():
    class _Strict(_Manager):
        def get(self, name, model=None):  # pragma: no cover - must not run
            raise AssertionError("looked up a non-fleet provider")

    assert _router(_Strict())._endpoint_label("anthropic") == "anthropic"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["unreachable", "timeout", "interrupted", "answered_error", "cooldown"])
async def test_banner_reasons_are_plain_clauses_with_the_label_last(kind):
    r = _router()
    await r._publish_not_connected(PROVIDER, "s1", kind=kind, retry_in=3)
    [(etype, payload)] = r.event_bus.seen
    assert etype == EventType.PROVIDER_DOWNGRADED
    assert payload["used"] == "none" and payload["requested"] == PROVIDER
    assert payload["label"] == "Office Spark" and list(payload)[-1] == "label"
    assert DASH not in payload["reason"], payload["reason"]
    if kind == "unreachable":
        assert payload["reason"] == "not connected"
    else:
        assert "not connected" not in payload["reason"]


def test_no_user_facing_router_string_carries_a_dash_aside():
    """Source pin: every string literal in the refusal builders (and the
    mock-trap banner reason in BOTH lanes) is free of the em-dash."""
    for fn in (
        R.ModelRouter._unavailable_error,
        R.ModelRouter._publish_not_connected,
        R.ModelRouter._endpoint_label,
    ):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        body = tree.body[0].body[1:]  # skip the docstring
        for node in ast.walk(ast.Module(body=body, type_ignores=[])):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert DASH not in node.value, (fn.__name__, node.value)
    assert DASH not in R._NO_STAND_IN
    src = inspect.getsource(R)
    assert src.count("connected. Make it your default on the Connections page.") == 2
    assert "every connected model is rate-limited or unavailable" in src
    assert f"right now {DASH} wait a minute" not in src
