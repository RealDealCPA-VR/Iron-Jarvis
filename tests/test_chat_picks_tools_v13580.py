"""v1.358.0 lane 8B — chat picks the right tools and names a failure.

The post-build re-audit (v1.357.0) confirmed three regressions and three
partly-met gaps in this lane. Each block below pins one, through the REAL
code (the real selector, the real registry, the real app factory's two chat
lanes, the real Claude transport's error builders, the real records tool);
only the model is scripted.

(1) REGRESSION: a web question that names an app ("look up the Stripe API
    docs for refunds", "search for Notion templates for a CRM") armed no web
    tool since v1.354.0, and the apps section told the model "Never search
    the web in its place". Research ABOUT an app reaches the web again; an
    app takes a search over only for the user's OWN data in it (a possessive
    "my Drive" always; "search GitHub for …" only when GitHub is connected).
(2) G15/G39: chat armed the first 24 of an app's tools in NAME order, so
    GitHub's pull-request tools were cut. The 24 are now the ones most
    relevant to the message (read-only first on a tie), in both lanes.
(3) REGRESSION: an unclassified provider failure read an anonymous "Iron
    Jarvis couldn't finish this answer". The Claude transport's timeout and
    cut-off answers are typed (timeout / interrupted), the generic headline
    names the provider it knows, and "claude-cli:" is never doubled.
(4) G41: a refused BUILT-IN tool reads in tool words with where to turn it
    on, never "_Note: write_file could not run (…)._", in both lanes.
(5) A pack that loaded no tools after app_connect is not "Connected …
    (saved — loads at the next start)"; it says it did not start and why.
(6) A rejected Pixio key says where to fix it (Settings › Connections),
    never a vault name or an environment variable.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.types import AgentType
from iron_jarvis.connectors.app_names import apps_section, connected_ids
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import refusal_footnotes
from iron_jarvis.mcp.tools import MCPRemoteTool
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ProviderError, ToolCall
from iron_jarvis.providers.adapters.claude_native import transport
from iron_jarvis.providers.failure_words import (
    ACCOUNTS_PLACE,
    GENERIC_TITLE,
    friendly_error,
    is_generic_title,
    plain_reason,
)
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.autoselect import select_auto_tools
from tests.test_chat_recovery_v1344 import (
    LOOKUP,
    _app,
    _body,
    _FakeTool,
    _run_post,
    _run_stream,
)

SECTION = "# Apps named in this message"


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #
def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        event, data = None, None
        for line in block.strip().splitlines():
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:"):].strip())
        if event is not None:
            out.append((event, data if isinstance(data, dict) else {}))
    return out


def _tool_names(tools) -> list[str]:
    return [getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None) for t in tools or []]


def _scripted(platform, monkeypatch, final: str = "Done."):
    """One fake model for BOTH lanes: answers ``final`` at once, recording the
    system prompt and the tools it was shown."""
    seen: dict = {"systems": [], "tools": []}

    async def fake_complete(*, system, messages, tools, **kw):
        seen["systems"].append(system)
        seen["tools"].append(_tool_names(tools))
        return RouteResult(LLMResponse(text=final, tool_calls=[]), "mock", "mock")

    async def fake_stream(*, system, messages, tools, **kw):
        seen["systems"].append(system)
        seen["tools"].append(_tool_names(tools))
        yield {"type": "text", "text": final}
        yield {"type": "final", "response": LLMResponse(text=final, tool_calls=[]),
               "provider": "mock", "model": "mock"}

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.router, "stream", fake_stream)
    return seen


def _send(client, lane: str, text: str, **extra):
    body = {"messages": [{"role": "user", "content": text}], "auto_tools": True, **extra}
    r = client.post("/chat" if lane == "post" else "/chat/stream", json=body)
    assert r.status_code == 200, r.text
    return r


def _section(system: str) -> str:
    at = system.find(SECTION)
    assert at >= 0, "the named-apps section never reached the prompt"
    end = system.find("\n\n", at + len(SECTION))
    return system[at:] if end < 0 else system[at:end]


#: GitHub's own server's default toolsets (context, repos, issues,
#: pull_requests, users) as its README lists them — the re-audit's evidence.
GITHUB_TOOLS = (
    "add_comment_to_pending_review add_issue_comment assign_copilot_to_issue create_branch "
    "create_issue create_or_update_file create_pull_request create_repository delete_file "
    "fork_repository get_commit get_file_contents get_issue get_issue_comments "
    "get_latest_release get_me get_release_by_tag get_tag get_team_members get_teams "
    "list_branches list_commits list_issue_types list_issues list_pull_requests list_releases "
    "list_sub_issues list_tags merge_pull_request pull_request_read pull_request_review_write "
    "push_files remove_sub_issue reprioritize_sub_issue request_copilot_review search_code "
    "search_issues search_pull_requests search_repositories search_users sub_issue_write "
    "update_issue update_pull_request update_pull_request_branch"
).split()
_READ_FIRST = ("get", "list", "search")
PR_LIST = "mcp__github__list_pull_requests"


def _reads(name: str) -> bool:
    return name.split("_", 1)[0] in _READ_FIRST or name.endswith("_read")


def _connect_github(platform, names=GITHUB_TOOLS, *, quarantined=()):
    """GitHub as a CONNECTED app: its saved pack row (the catalog id) and its
    loaded tools in the real registry (real ``MCPRemoteTool`` objects with
    the server's read-only hints; never called)."""
    platform.config.mcp_servers.append(
        {"name": "github", "transport": "http", "url": "https://example.invalid/mcp"}
    )
    for n in names:
        tool = MCPRemoteTool(
            None, "github", n, f"{n.replace('_', ' ').capitalize()} in a GitHub repository.",
            None, {"readOnlyHint": _reads(n)},
        )
        tool.quarantined = n in quarantined
        platform.registry.register(tool, mcp=True)
    return sorted(f"mcp__github__{n}" for n in names)


# =========================================================================== #
# (1) research ABOUT an app reaches the web; the user's OWN data does not
# =========================================================================== #
#: The re-audit's probe phrasings (probe_compare.py), both ways, plus the
#: v1.354.0 pins. ``(text, web when nothing is connected, web when the named
#: app IS connected)``.
PROBE_TABLE: list[tuple[str, bool, bool]] = [
    # research about an app — the confirmed regression: web, whatever is connected
    ("look up the stripe api docs for refunds", True, True),
    ("search for notion templates for a CRM", True, True),
    ("look up how to use github actions", True, True),
    ("search for the dropbox desktop app download", True, True),
    ("look up google maps api pricing", True, True),
    ("search for brave search api pricing", True, True),
    # a real web word: web, as before
    ("what's the latest news about github copilot", True, True),
    ("find a good postgres tutorial online", True, True),
    ("research stripe vs square fees", True, True),
    ("search the notion docs for databases", True, True),
    # the user's OWN data in an app: never the web (G39's Drive acceptance)
    ("search my google drive for the lease", False, False),
    ("search my gmail for the invoice from acme", False, False),
    ("look up the invoice in my Dropbox", False, False),
    ("search for the lease in my Google Drive", False, False),
    # a search IN an app, no possessive: the app takes it only when CONNECTED
    ("search github for issues about login", True, False),
    ("look up the refund in stripe", True, False),
]
ALL_ON = ("github", "notion", "stripe", "google_drive", "gmail", "dropbox", "google_maps", "brave_search")


@pytest.mark.parametrize("text, web_off, web_on", PROBE_TABLE)
def test_the_probe_table_side_by_side(text, web_off, web_on):
    off = select_auto_tools(text)
    on = select_auto_tools(text, apps_on=ALL_ON)
    assert ("web_search" in off) is web_off, (text, off)
    assert ("web_search" in on) is web_on, (text, on)
    for got, want in ((off, web_off), (on, web_on)):
        assert ("web_fetch" in got) is want, (text, got)


def test_only_the_named_apps_own_connection_counts():
    """Another app being connected does not take GitHub's search over."""
    text = "search github for issues about login"
    assert "web_search" in select_auto_tools(text, apps_on=("notion", "slack"))
    assert "web_search" not in select_auto_tools(text, apps_on=("github",))


def test_the_apps_section_no_longer_forbids_web_research_about_an_app():
    rows = [{"id": "stripe", "name": "Stripe", "state": "not_connected", "tools": 0, "needs": "", "note": ""}]
    section = apps_section(rows)
    assert "Never search the web in its place." not in section
    assert "A question about Stripe itself (its docs, pricing or how to use it) may be answered from the web." in section
    # The user's own data still never goes to the web, and the offer stays.
    assert "Never search the web for what is in it." in section
    assert "offer to connect it" in section
    assert "If answering needs what is in the user's own Stripe" in section


def test_connected_ids_reads_only_connected_apps_never_a_cli():
    rows = [
        {"id": "github", "state": "connected"},
        {"id": "notion", "state": "not_connected"},
        {"id": "slack", "state": "off"},
        {"id": "claude-cli", "state": "signed_in"},
    ]
    assert connected_ids(rows) == ["github"]


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_the_stripe_api_docs_reach_the_web_in_both_lanes(tmp_path, monkeypatch, lane):
    client = TestClient(create_app(str(tmp_path)))
    seen = _scripted(client.app.state.platform, monkeypatch)
    _send(client, lane, "look up the stripe api docs for refunds")
    tools = seen["tools"][0]
    assert "web_search" in tools and "web_fetch" in tools, tools
    assert "may be answered from the web" in _section(seen["systems"][0])


@pytest.mark.parametrize("lane", ["post", "stream"])
@pytest.mark.parametrize("connected", [False, True])
def test_a_connected_github_takes_over_its_own_search_in_both_lanes(tmp_path, monkeypatch, lane, connected):
    """The grounding reads GitHub as connected and hands it to the arming
    pass (``_Grounding.apps_on`` -> ``arming_kw`` -> ``apps_on``) in BOTH
    lanes; with GitHub not connected the same sentence keeps the web."""
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    if connected:
        _connect_github(platform, ["search_issues", "list_issues"])
    seen = _scripted(platform, monkeypatch)
    _send(client, lane, "search github for issues about login")
    tools = seen["tools"][0]
    section = _section(seen["systems"][0])
    if connected:
        assert "- GitHub: connected (2 tools)." in section
        assert "web_search" not in tools and "web_fetch" not in tools, tools
    else:
        assert "- GitHub: not connected." in section
        assert "web_search" in tools, tools


def test_the_arming_pass_passes_apps_on_only_when_set():
    """Additive knob, passed only when set: a turn naming no connected app
    calls the arming pass exactly as before (fixed-signature doubles keep
    working)."""
    assert chat_turn.arming_kw(SimpleNamespace(apps_on=[])) == {}
    assert chat_turn.arming_kw(SimpleNamespace()) == {}
    assert chat_turn.arming_kw(SimpleNamespace(apps_on=["github"])) == {"apps_on": ["github"]}


# =========================================================================== #
# (2) WHICH of an app's tools are armed: the ones the message asks about
# =========================================================================== #
def test_name_order_would_cut_the_pull_request_list_tool(tmp_path):
    """Anti-vacuity: the cap in NAME order (the old rule) loses it."""
    platform = create_app(str(tmp_path)).state.platform
    names = _connect_github(platform)
    assert len(names) == 44
    assert PR_LIST not in names[: chat_turn._MAX_CONNECTOR_TOOLS]


@pytest.mark.parametrize("text", ["list my open pull requests", "list my open PRs on GitHub"])
def test_list_my_open_pull_requests_picks_the_pull_request_list_tool(tmp_path, text):
    platform = create_app(str(tmp_path)).state.platform
    names = _connect_github(platform)
    got = platform.registry.pick_for_message(names, text, chat_turn._MAX_CONNECTOR_TOOLS)
    assert PR_LIST in got
    assert len(got) == chat_turn._MAX_CONNECTOR_TOOLS
    assert got == [n for n in names if n in got], "kept in the order given"


def test_an_app_that_fits_arms_every_tool_in_its_order(tmp_path):
    platform = create_app(str(tmp_path)).state.platform
    names = _connect_github(platform)[:10]
    assert platform.registry.pick_for_message(names, "create an issue", 24) == names
    assert platform.registry.pick_for_message(names, "", 24) == names


def test_read_only_tools_first_on_a_tie(tmp_path):
    platform = create_app(str(tmp_path)).state.platform
    names = _connect_github(platform)
    reads = [n for n in names if _reads(n.rsplit("__", 1)[-1])]
    assert len(reads) >= 24, "the fixture has enough read-only tools for the tie to decide"
    got = platform.registry.pick_for_message(names, "hello there", 24)
    assert set(got) <= set(reads), sorted(set(got) - set(reads))


def test_a_write_tool_the_message_asks_for_wins_over_a_read_only_one(tmp_path):
    platform = create_app(str(tmp_path)).state.platform
    names = _connect_github(platform)
    got = platform.registry.pick_for_message(names, "create a pull request for my branch", 24)
    assert "mcp__github__create_pull_request" in got


def test_a_tool_not_yet_seen_on_the_card_is_picked_last(tmp_path):
    """A tool that arrived since the user last looked (quarantined) is armed
    only after every tool they did see — and still when the app fits."""
    platform = create_app(str(tmp_path)).state.platform
    names = _connect_github(platform, quarantined={"list_pull_requests"})
    got = platform.registry.pick_for_message(names, "list my open pull requests", 24)
    assert PR_LIST not in got
    assert PR_LIST in platform.registry.pick_for_message(names, "list my open pull requests", 44)


def test_the_stream_lane_arms_the_pull_request_list_tool_for_a_named_github(tmp_path, monkeypatch):
    """GitHub connected, NOT toggled: the named-app path (stream lane only,
    ask-armed) hands the model GitHub's pull-request list tool, under the cap."""
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    _connect_github(platform)
    seen = _scripted(platform, monkeypatch)
    _send(client, "stream", "list my open pull requests on GitHub")
    tools = seen["tools"][0]
    github = [t for t in tools if t.startswith("mcp__github__")]
    assert PR_LIST in github, github
    assert len(github) == chat_turn._MAX_CONNECTOR_TOOLS


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_the_toggle_arms_the_pull_request_list_tool_in_both_lanes(tmp_path, monkeypatch, lane):
    """The "+" toggle (``connectors``), resolved once for both lanes in the
    shared grounding: lock-step by construction."""
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    _connect_github(platform)
    seen = _scripted(platform, monkeypatch)
    _send(client, lane, "list my open pull requests", connectors=["github"])
    github = [t for t in seen["tools"][0] if t.startswith("mcp__github__")]
    assert PR_LIST in github, github
    assert len(github) == chat_turn._MAX_CONNECTOR_TOOLS


# =========================================================================== #
# (3) a provider failure names the provider; the transport's errors are typed
# =========================================================================== #
def _timeouts():
    return transport._Timeouts(SimpleNamespace(idle_timeout_s=300, total_timeout_s=3600), 0.0)


def _admission(status=None, *, failure="", network=None, said=""):
    return SimpleNamespace(
        status=status, capture=SimpleNamespace(complete=False), error_text=lambda: said,
        failure=failure, denied=1, failure_network=network, retry_after=None,
    )


def _no_engine_words(words: dict) -> None:
    shown = words["title"] + " " + words["fix"]
    for raw in ("claude-cli", "CLI timed out", "upstream", "Iron Jarvis couldn't"):
        assert raw not in shown, (raw, shown)


@pytest.mark.parametrize("total", [False, True])
def test_a_claude_code_timeout_reads_as_a_timeout_naming_claude_code(total):
    exc = _timeouts().error(total)
    assert str(exc).startswith(transport.TIMEOUT_PREFIX), "the words are unchanged"
    assert getattr(exc, "kind", "") == "timeout" and exc.transient is True
    w = friendly_error(exc, provider="claude-cli")
    assert w["title"] == "Claude Code didn't answer in time"
    assert w["fix"] == "Press Retry, or pick another model for this chat."
    assert w["technical"].startswith(transport.TIMEOUT_PREFIX), "the technical text is kept, folded"
    _no_engine_words(w)


def test_an_answer_cut_off_mid_way_reads_as_a_dropped_connection():
    got = SimpleNamespace(emitted=True, native_error="")
    exc = transport.upstream_failure(got, _admission(200), probe=False)
    assert str(exc).startswith(transport.INCOMPLETE_PREFIX), "the words are unchanged"
    assert getattr(exc, "kind", "") == "interrupted" and getattr(exc, "partial", False) is True
    assert getattr(exc, "failed_provider", "") == "claude-cli"
    w = friendly_error(exc)
    assert w["title"] == "The connection to Claude Code dropped mid-answer"
    assert w["fix"] == "The reply above is incomplete. Press Retry."
    _no_engine_words(w)


def test_a_drop_before_any_answer_reads_as_a_dropped_connection():
    got = SimpleNamespace(emitted=False, native_error="")
    exc = transport.upstream_failure(got, _admission(None, failure="ConnectionResetError"), probe=False)
    assert str(exc) == transport.DROPPED and exc.transient is True
    assert getattr(exc, "kind", "") == "interrupted" and not getattr(exc, "partial", False)
    w = friendly_error(exc)
    assert w["title"] == "The connection to Claude Code dropped"
    _no_engine_words(w)


def test_the_reaudits_own_probe_no_longer_reads_anonymous():
    """friendly_error(ProviderError('claude-cli: CLI timed out ...'),
    provider='claude-cli') — built by hand, no typed kind: the transport's
    own constant still names it."""
    exc = ProviderError("claude-cli: CLI timed out - no output for 300s")
    exc.failed_provider = "claude-cli"
    w = friendly_error(exc, provider="claude-cli")
    assert w["title"] != GENERIC_TITLE
    assert w["title"] == "Claude Code didn't answer in time"


def test_an_unclassified_provider_failure_names_the_provider_it_knows():
    exc = ProviderError(
        "claude-cli: Iron Jarvis could not pass the request on to Anthropic"
        " (an internal relay error, not your connection)"
    )
    w = friendly_error(exc, provider="mock")
    assert w["title"] == "Claude Code couldn't finish this answer"
    assert is_generic_title(w["title"])
    assert "relay error" in w["technical"] and "relay" not in w["title"] + w["fix"]


def test_a_lower_case_brand_keeps_its_case_at_the_start_of_the_headline():
    exc = ProviderError("llamacpp: the server closed the stream")
    exc.failed_provider = "llamacpp"
    assert friendly_error(exc)["title"] == "llama.cpp couldn't finish this answer"


def test_iron_jarvis_own_fault_keeps_the_iron_jarvis_headline():
    """Not a provider's failure: the headline does not blame the model."""
    assert friendly_error(RuntimeError("boom"), provider="claude-cli")["title"] == GENERIC_TITLE
    assert friendly_error(ProviderError("boom"), provider="auto")["title"] == GENERIC_TITLE
    assert is_generic_title(GENERIC_TITLE) and not is_generic_title("Claude Code didn't answer in time")


def _router_wrapped(inner: BaseException) -> RuntimeError:
    """``router.stream``'s "busy" sentence around the primary's failure,
    built the way the router builds it (``f"({provider}: {exc})"``)."""
    try:
        try:
            raise inner
        except BaseException as e:  # noqa: BLE001
            raise RuntimeError(
                "every connected model is rate-limited or unavailable right now. Wait a minute and"
                f" try again (claude-cli: {e})"
            ) from e
    except RuntimeError as outer:
        return outer


def test_claude_cli_is_never_doubled_and_a_run_summary_says_the_headline():
    outer = _router_wrapped(_timeouts().error(False))
    assert "claude-cli: claude-cli:" in str(outer), "anti-vacuity: the router doubles it"
    w = friendly_error(outer)
    assert w["title"] == "Claude Code didn't answer in time"
    assert "claude-cli: claude-cli:" not in w["technical"]
    reason = plain_reason(outer)
    assert reason == "Claude Code didn't answer in time. Press Retry, or pick another model for this chat."
    # An unclassified wrapped failure keeps its own words, said once.
    odd = _router_wrapped(ProviderError("claude-cli: something new went wrong"))
    assert "claude-cli: claude-cli:" not in plain_reason(odd)


async def test_a_failed_run_summary_names_claude_code_not_the_doubled_raw_text(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    s = await orch.create_session("go", AgentType.BUILDER)
    await orch._finalize_failed(s, _router_wrapped(_timeouts().error(False)))
    with session_scope(p.engine) as db:
        summary = db.get(Session, s.id).summary or ""
    assert summary == (
        "The run failed: Claude Code didn't answer in time. Press Retry, or pick another model for this chat."
    )
    assert "claude-cli" not in summary


def _failing_router(platform, monkeypatch, exc):
    async def fake_complete(**kw):
        raise exc

    async def fake_stream(**kw):
        raise exc
        yield  # pragma: no cover

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.router, "stream", fake_stream)


def test_both_lanes_say_claude_code_timed_out(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    exc = _router_wrapped(_timeouts().error(False))
    _failing_router(client.app.state.platform, monkeypatch, exc)
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]})
    [err] = [d for ev, d in _parse_sse(r.text) if ev == "error"]
    assert err["title"] == "Claude Code didn't answer in time"
    assert "claude-cli: claude-cli:" not in err["technical"]
    _no_engine_words(err)
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 502
    body = r.json()
    assert body["title"] == err["title"] and body["fix"] == err["fix"], "lock-step"


def test_a_failure_before_the_answer_began_still_says_it_never_started(tmp_path, monkeypatch):
    """The stream lane's "couldn't get ready" headline replaces the GENERIC
    one — and the generic one may now name a provider; it is still generic
    (``is_generic_title``), so a preparation failure keeps its own words."""
    from iron_jarvis.daemon.routes import chat as chat_routes

    async def broken(*a, **k):
        raise ProviderError("claude-cli: something unexpected while preparing")

    client = TestClient(create_app(str(tmp_path)))
    monkeypatch.setattr(chat_routes, "_gather_grounding", broken)
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]})
    [err] = [d for ev, d in _parse_sse(r.text) if ev == "error"]
    assert err["title"] == chat_routes._PREP_FAILED_TITLE, err
    assert err["fix"] == "Send your message again to retry."


# =========================================================================== #
# (4) a refused BUILT-IN tool reads in tool words, with where to turn it on
# =========================================================================== #
def test_a_built_in_tools_refusal_reads_in_tool_words_with_its_place():
    assert refusal_footnotes([("write_file", "not armed")]) == (
        "\n\n_Note: Jarvis couldn't save a file (not turned on for this chat) — turn on Auto"
        " tools in the chat's Tools menu, then ask for it in your message._"
    )
    assert refusal_footnotes([("web_search", "not armed")]) == (
        "\n\n_Note: Jarvis couldn't search the web (not turned on for this chat) — turn on Web"
        " & research in the chat's Tools menu._"
    )
    # Permission denied / low trust: the tool's words, no false place.
    assert refusal_footnotes([("image_info", "permission denied")]) == (
        "\n\n_Note: Jarvis couldn't use image info (permission denied)._"
    )
    assert refusal_footnotes([("shell", "not armed")]) == (
        "\n\n_Note: Jarvis couldn't run a command (not turned on for this chat)._"
    ), "no chat switch arms shell: no place is named"
    # Never the raw id for a built-in.
    for name in ("write_file", "web_search", "image_info", "shell"):
        assert f"{name} could not run" not in refusal_footnotes([(name, "not armed")])
    # An app's tool and a malformed id are unchanged.
    assert refusal_footnotes([("mcp__github__create_issue", "not armed")]).startswith(
        "\n\n_Note: GitHub › create issue could not run"
    )
    assert refusal_footnotes([("mcp__", "not armed")]) == (
        "\n\n_Note: mcp__ could not run (not turned on for this chat)._"
    )


def _calls_write_file() -> list[LLMResponse]:
    return [
        LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="write_file",
                                                  arguments={"path": "x.txt", "content": "hi"})]),
        LLMResponse(text="I could not save it."),
    ]


FOOTNOTE = (
    "\n\n_Note: Jarvis couldn't save a file (not turned on for this chat) — turn on Auto tools"
    " in the chat's Tools menu, then ask for it in your message._"
)


async def test_post_lane_footnotes_an_unarmed_write_file_in_tool_words(tmp_path):
    platform = _app(tmp_path, _FakeTool(LOOKUP))
    out, _ = await _run_post(platform, _body(LOOKUP), _calls_write_file())
    assert out["reply"].endswith(FOOTNOTE), out["reply"]
    assert "write_file could not run" not in out["reply"]


async def test_stream_lane_footnotes_an_unarmed_write_file_in_tool_words(tmp_path):
    platform = _app(tmp_path, _FakeTool(LOOKUP))
    done, _, _ = await _run_stream(platform, _body(LOOKUP), _calls_write_file())
    assert done["reply"].endswith(FOOTNOTE), done["reply"]
    assert "write_file could not run" not in done["reply"]


# =========================================================================== #
# (5) a pack that loaded no tools is not "connected … loads at the next start"
# =========================================================================== #
async def test_a_pack_that_did_not_start_says_so_and_why(tmp_path, monkeypatch):
    from iron_jarvis.mcp import tools as mcp_tools
    from iron_jarvis.settings import records

    p = create_app(str(tmp_path)).state.platform
    # The loader the pack door uses ran and loaded nothing, recording why
    # (a real load record, in the words every surface reads).
    def no_tools(platform, cfg):
        mcp_tools._record_load(cfg["name"], error="FileNotFoundError: [WinError 2] no such file",
                               tools_loaded=0, cfg={"command": "npx", "args": []})
        return 0

    monkeypatch.setattr(records, "load_app", no_tools)
    try:
        res = await records.AppConnectTool(p).execute({"app": "memory"}, None)
    finally:
        mcp_tools.forget_load_record("memory")
    assert res.ok, res.error
    out = res.output
    assert "loads at the next start" not in out
    assert out.startswith("Saved Knowledge graph, but it did not start, so its tools are not in this chat: ")
    assert "isn't installed, or isn't on the PATH this app can see" in out or "on the PATH" in out
    card = res.data["record_change"]
    assert card["title"] == "App saved" and card["new"] == records.NOT_STARTED_STATE
    assert "loads at the next start" not in json.dumps(card)


async def test_a_pack_that_started_still_reads_connected(tmp_path, monkeypatch):
    from iron_jarvis.settings import records

    p = create_app(str(tmp_path)).state.platform
    monkeypatch.setattr(records, "load_app", lambda platform, cfg: 3)
    res = await records.AppConnectTool(p).execute({"app": "memory"}, None)
    assert res.ok and res.output.startswith("Connected Knowledge graph (3 tools).")
    assert res.data["record_change"]["title"] == "App connected"


# =========================================================================== #
# (6) a rejected Pixio key says where to fix it, in plain words
# =========================================================================== #
def test_a_rejected_pixio_key_says_settings_connections():
    from iron_jarvis.media.pixio import map_error

    err = map_error(401, {"error": "invalid key"})
    assert err is not None and err.code == "not_configured"
    words = str(err)
    assert ACCOUNTS_PLACE in words, words
    for leak in ("PIXIO_API_KEY", "'pixio' secret", "secret", "env"):
        assert leak not in words, (leak, words)
