"""The dashboard's twins of three daemon facts, and the add-on's consent page
(v1.449.0, readiness Wave C items 22, 23 and 31).

* ``dashboard/lib/oauthApp.ts`` saves a drive's app id and secret under the
  vault names ``platform._oauth_app`` READS, and shows the redirect address
  the daemon defaults to — pinned here against the daemon's own code, so a
  rename on one side fails before a user's sign-in does.
* ``dashboard/lib/directoryLinks.ts`` opens the Handbook section the chat's
  page steps open (``page_doors.M365_ADMIN_SECTION``), and the Node.js link
  is the daemon's own ``runtime_url``.
* ``extensions/chrome/src/setup/setup.html`` says what site access really
  allows (read, act, each action shown first) and names no browser in its
  static words; ``setup.ts`` fills the browser in from the page's own
  navigator. ``browser.hello`` carries the add-on's version and
  ``GET /browser/status`` forwards it, which is what the card compares.
"""

from __future__ import annotations

import re
from pathlib import Path

from iron_jarvis.browser import protocol as P
from iron_jarvis.connectors import service
from iron_jarvis.daemon import page_doors

REPO = Path(__file__).resolve().parents[1]
DASH = REPO / "dashboard"
ADDON = REPO / "extensions" / "chrome" / "src"


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _ts_template(ts: str, name: str) -> str:
    """The template literal a one-line TS function returns."""
    m = re.search(rf"export function {name}\([^)]*\): string \{{\s*return `([^`]*)`;", ts)
    assert m, f"{name} not found as a one-template function"
    return m.group(1)


# --------------------------------------------------------------------------- #
# item 22: the vault names and the redirect are the daemon's
# --------------------------------------------------------------------------- #


def test_the_app_id_and_secret_boxes_save_under_the_names_the_daemon_reads():
    ts = _src(DASH / "lib" / "oauthApp.ts")
    platform_py = _src(REPO / "src" / "iron_jarvis" / "platform.py")
    for ts_fn, py_literal in (
        ("oauthClientIdName", 'secrets.get(f"{provider}_oauth_client_id")'),
        ("oauthClientSecretName", 'secrets.get(f"{provider}_oauth_client_secret")'),
    ):
        template = _ts_template(ts, ts_fn)
        expected = re.search(r'f"\{provider\}(_oauth_client_[a-z]+)"', py_literal).group(1)
        assert template == "${provider}" + expected, (ts_fn, template, expected)
        assert py_literal in platform_py, f"platform._oauth_app no longer reads {py_literal}"


def test_the_redirect_address_shown_is_the_one_the_daemon_defaults_to():
    ts = _src(DASH / "lib" / "oauthApp.ts")
    platform_py = _src(REPO / "src" / "iron_jarvis" / "platform.py")
    shown = _ts_template(ts, "oauthRedirectAddress")
    assert shown == "http://localhost:8787/oauth/${provider}/callback"
    assert 'f"http://localhost:8787/oauth/{provider}/callback"' in platform_py


def test_the_form_is_on_both_cards_and_nobody_types_an_internal_name():
    directory = _src(DASH / "components" / "settings" / "pages" / "DirectoryPage.tsx")
    connections = _src(DASH / "components" / "settings" / "pages" / "ConnectionsPage.tsx")
    for page in (directory, connections):
        assert "<OAuthAppForm" in page
    # The old instruction ("save it in Secrets under the name …") is gone
    # from both pages' prose.
    assert "save the" not in connections.lower().split("oauthappform")[0][-800:]
    assert "Set it up on the Connections page" in directory  # the no-provider fallback stays


# --------------------------------------------------------------------------- #
# item 23: the Handbook door and the Node.js link
# --------------------------------------------------------------------------- #


def test_the_admin_guide_opens_the_handbook_section_the_chat_opens():
    ts = _src(DASH / "lib" / "directoryLinks.ts")
    m = re.search(r'export const M365_ADMIN_SECTION = "([^"]+)";', ts)
    assert m and m.group(1) == page_doors.M365_ADMIN_SECTION
    assert f"/help?guide=handbook&section=${{M365_ADMIN_SECTION}}" in ts
    # And that section really exists in the Handbook.
    handbook = _src(REPO / "docs" / "HANDBOOK.md")
    headings = [page_doors.heading_slug(h) for h in re.findall(r"^#{1,3} (.+)$", handbook, flags=re.M)]
    assert any(h == page_doors.M365_ADMIN_SECTION or h.startswith(page_doors.M365_ADMIN_SECTION + "-") for h in headings)


def test_the_node_link_is_the_daemons_own_address():
    ts = _src(DASH / "lib" / "directoryLinks.ts")
    name, url = service._RUNTIMES["node"]  # noqa: SLF001
    assert name == "Node.js"
    assert f'"node.js": {{ name: "Node.js", url: "{url}" }}' in ts
    assert f'node: {{ name: "Node.js", url: "{url}" }}' in ts


def test_the_microsoft_365_card_links_its_admin_steps_and_names_its_github_link():
    directory = _src(DASH / "components" / "settings" / "pages" / "DirectoryPage.tsx")
    # v1.455.0 (Wave H item 27, moved on purpose): one table holds the admin
    # steps AND the four own-app guides; ADMIN_GUIDES is spread into it.
    assert "SETUP_GUIDES[c.id]" in directory
    assert "...ADMIN_GUIDES," in _src(DASH / "lib" / "directoryLinks.ts")
    assert "docsLinkLabel(c.docs_url)" in directory
    ts = _src(DASH / "lib" / "directoryLinks.ts")
    assert "microsoft_365:" in ts and '"How your admin sets this up"' in ts


# --------------------------------------------------------------------------- #
# item 31: the consent page tells the truth and names no browser
# --------------------------------------------------------------------------- #


def _visible(html: str) -> str:
    text = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    text = re.sub(r"<style.*?</style>", "", text, flags=re.S)
    text = re.sub(r"<script.*?</script>", "", text, flags=re.S)
    return re.sub(r"<[^>]+>", " ", text)


def test_the_consent_page_says_what_site_access_really_allows():
    visible = _visible(_src(ADDON / "setup" / "setup.html")).lower()
    for needle in ("read", "click", "type", "fill a form", "screenshot", "shown to you first"):
        assert needle in visible, f"setup.html does not say the add-on can {needle!r}"
    for untrue in ("sees only", "later versions", "tab titles"):
        assert untrue not in visible, f"setup.html still says {untrue!r}"


def test_the_consent_page_names_no_browser_in_its_static_words():
    visible = _visible(_src(ADDON / "setup" / "setup.html"))
    assert not re.search(r"\bChrome\b|\bEdge\b|chrome://|edge://", visible), (
        "setup.html names a browser; setup.ts fills the browser in from the page's own navigator"
    )
    assert 'data-browser="name"' in _src(ADDON / "setup" / "setup.html")
    assert 'data-browser="addons-page"' in _src(ADDON / "setup" / "setup.html")


def test_the_consent_script_fills_the_browser_in_and_keeps_the_gesture():
    ts = _src(ADDON / "setup" / "setup.ts")
    assert "edg/" in ts and "edge://extensions" in ts and "chrome://extensions" in ts
    assert "fillBrowserWords(browser)" in ts
    # The gesture rule (v1.235.0): nothing awaited before the request.
    click = ts[ts.index('addEventListener("click"'):]
    before_request = click[: click.index("requestHostPermission()")]
    code_only = re.sub(r"//[^\n]*", "", before_request)  # the comment SAYS "no await"
    assert not re.search(r"\bawait\b", code_only), "an await before the request loses the user gesture"
    assert "Waiting for Chrome" not in ts, "the page still names one browser for everybody"


def test_the_add_on_reports_its_version_on_hello_and_the_status_route_forwards_it():
    frame = P.hello_frame("ext-id", "1.448.1", True)
    assert frame["extension_version"] == "1.448.1"
    assert "extension_version" in P.HelloFrame.__annotations__
    route = _src(REPO / "src" / "iron_jarvis" / "daemon" / "routes" / "browser.py")
    assert '"extension_version": str(view.get("extension_version") or "")' in route
    card = _src(DASH / "components" / "browser" / "YourBrowserCard.tsx")
    assert "staleAddonLine(addonVersion, appVersion, browser)" in card
