"""The Connector Marketplace catalog (CX-01).

A single, curated gallery of everything Iron Jarvis can connect to — MCP servers
(Slack/GitHub/Postgres/…), OAuth apps (Drive/Dropbox/…), and API-key services
(Pixio) — each with a glyph, a plain-English *what this unlocks*, the permissions
it wants, and how it connects. The catalog carries NO secrets: a token the user
supplies is stored in the encrypted vault and injected at launch.

This module is pure data + light helpers (no I/O), so it imports cheaply and is
trivially testable. :mod:`iron_jarvis.connectors.service` turns a catalog entry
into a live connection and reports its status.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: How a connector is wired up.
CONNECT_VIA = ("mcp", "oauth", "api_key")

#: What a single input field targets when connecting an MCP connector:
#: ``secret`` — stored in the vault, injected as an env var of the same name;
#: ``env``    — a non-secret env var set inline (e.g. a team id);
#: ``arg``    — substituted for the ``<name>`` placeholder in ``args`` (e.g. a
#:              folder path or database URL).
FIELD_KINDS = ("secret", "env", "arg")


@dataclass(frozen=True)
class Field:
    """One value the user supplies to connect an MCP connector."""

    name: str          # env var name (secret/env) OR the <name> arg placeholder
    label: str
    help: str = ""
    kind: str = "secret"
    optional: bool = False


@dataclass(frozen=True)
class Connector:
    """One entry in the marketplace gallery."""

    id: str
    name: str
    category: str
    glyph: str          # an emoji shown on the card
    blurb: str          # one line: what it is
    unlocks: str        # plain-English: what you can do once connected
    connect_via: str    # "mcp" | "oauth" | "api_key"
    scopes: list[str] = field(default_factory=list)  # permissions, plain-ish
    docs_url: str = ""
    # -- mcp --
    command: str = ""
    args: list[str] = field(default_factory=list)
    fields: list[Field] = field(default_factory=list)
    # -- oauth / api_key (routes through the ConnectionRegistry) --
    provider: str = ""  # a ConnectionSpec provider id
    # v1.353.0 (G45): a one-time setup this app needs OUTSIDE Iron Jarvis,
    # said on the card BEFORE Connect (Gmail's package reads a Google OAuth
    # client the user registers themselves; it used to be one clause of the
    # blurb, and Drive's said nothing until the press failed). For an OAuth
    # app it is met once an OAuth client is registered, so the gallery shows
    # it only while `oauth_client_configured` is False (connectors.service).
    needs: str = ""
    # v1.353.0 (G43): an honest word about the PACKAGE itself — npm marks
    # it "no longer supported" and no maintained replacement with the same
    # settings has been verified yet. Shown on the card; the pack still
    # installs, so it stays connectable.
    package_note: str = ""
    # -- link (a card that points at the page that sets it up; LINK_CARDS) --
    href: str = ""
    action: str = ""
    # -- a server reached over the network instead of a program run here --
    # v1.355.0 (G44): ``transport`` "http" (or "sse") with ``url`` is a REMOTE
    # server — GitHub's own is one — and ``command``/``args`` stay empty. Its
    # token is the field named ``auth_field``, sent as ``Authorization:
    # Bearer <token>`` (vaulted as ``mcp.<id>.auth.value`` by the one add door,
    # mcp/pack_door.py). "stdio" (the default) keeps every other card exactly
    # as it was.
    transport: str = "stdio"
    url: str = ""
    auth_field: str = ""
    # v1.355.0 (G44): packages this card USED to run. An imported copy of one
    # (a Claude Desktop config still naming @modelcontextprotocol/server-github)
    # is described by this card's blurb (routes/agents.catalog_blurbs).
    replaces: list[str] = field(default_factory=list)


def is_remote(connector: "Connector") -> bool:
    """True for a card whose server is reached over the network (G44)."""
    return str(connector.transport or "").lower() in ("http", "sse") and bool(connector.url)


#: How a LINK card is wired: it is not connected here at all — its one action
#: opens the page that sets it up. Not in :data:`CONNECT_VIA` and never in
#: :data:`CATALOG`, so every connect / test / disconnect door (and the chat's
#: app_connect, which reads :func:`get_connector`) still sees only real
#: connectors.
LINK = "link"

#: v1.353.0 (G43): the words for a package npm marks "no longer supported".
OLDER_PACKAGE_NOTE = "Uses an older package its makers no longer update."


# --------------------------------------------------------------------------- #
# The catalog. Grouped by category (order preserved for the gallery).
# --------------------------------------------------------------------------- #
_GH = "https://github.com/settings/tokens"

#: v1.355.0 (G44): GitHub's own maintained server, hosted by GitHub and
#: reached over Streamable HTTP. Read off github/github-mcp-server's README
#: (2026-10-19): `"type": "http", "url": "https://api.githubcopilot.com/mcp/"`
#: with `"Authorization": "Bearer <personal access token>"`. The reference
#: @modelcontextprotocol/server-github it replaces is "no longer supported" on
#: npm. Nothing to install on this PC. The Tools page's catalog
#: (daemon/app.py ``_MCP_CATALOG``) names the same address.
GITHUB_MCP_URL = "https://api.githubcopilot.com/mcp/"
#: The reference package the GitHub card ran before (G43's "older package").
GITHUB_OLD_PACKAGE = "@modelcontextprotocol/server-github"

CATALOG: list[Connector] = [
    # ---------------- Developer ----------------
    Connector(
        id="github",
        name="GitHub",
        category="Developer",
        glyph="🐙",
        blurb="Repos, issues, and pull requests.",
        unlocks="Search repositories, read and open issues & PRs, review diffs, and "
        "manage your GitHub — all from chat and agents, through GitHub's own server.",
        connect_via="mcp",
        scopes=["Repositories", "Issues & pull requests", "Code search"],
        docs_url=_GH,
        fields=[
            Field(
                "GITHUB_PERSONAL_ACCESS_TOKEN",
                "GitHub personal access token",
                help=f"Create a token (repo scope) at {_GH}.",
            )
        ],
        # v1.355.0 (G44): GitHub's maintained server, reached over the network
        # (nothing to install), so no "older package" note any more.
        transport="http",
        url=GITHUB_MCP_URL,
        auth_field="GITHUB_PERSONAL_ACCESS_TOKEN",
        replaces=[GITHUB_OLD_PACKAGE],
    ),
    Connector(
        id="postgres",
        name="Postgres",
        category="Developer",
        glyph="🐘",
        blurb="Query your database in plain English.",
        unlocks="Explore schemas and run read-only SQL against a Postgres database "
        "by describing what you want — no hand-written queries.",
        connect_via="mcp",
        scopes=["Read-only SQL", "Schema inspection"],
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/postgres",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-postgres", "<connection>"],
        fields=[
            Field(
                "connection",
                "Connection string",
                help="e.g. postgresql://user:pass@host:5432/dbname",
                kind="arg",
            )
        ],
        package_note=OLDER_PACKAGE_NOTE,
    ),
    Connector(
        id="filesystem",
        name="Filesystem",
        category="Developer",
        glyph="📁",
        blurb="Read & write files in a folder you choose.",
        unlocks="Give agents scoped access to a specific folder — read, write, and "
        "organize files there (nothing outside it).",
        connect_via="mcp",
        scopes=["Read files", "Write files (chosen folder only)"],
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "<folder>"],
        fields=[Field("folder", "Folder path", help="Absolute path to allow", kind="arg")],
    ),
    Connector(
        id="sentry",
        name="Sentry",
        category="Developer",
        glyph="🛡️",
        blurb="Triage errors and issues.",
        unlocks="List and inspect Sentry issues, see stack traces, and understand "
        "what's breaking — straight from chat.",
        connect_via="mcp",
        scopes=["Read issues & events"],
        docs_url="https://docs.sentry.io/product/sentry-mcp/",
        command="npx",
        args=["-y", "@sentry/mcp-server"],
        fields=[
            Field("SENTRY_AUTH_TOKEN", "Sentry auth token", help="An org auth token from Sentry settings.")
        ],
    ),
    # v1.353.0 (G43): "Browser (Puppeteer)" is gone from here. npm marks
    # @modelcontextprotocol/server-puppeteer "no longer supported", and the
    # app already drives the user's OWN browser through its add-on, so the
    # Directory offers that instead: the "browser_addon" LINK card below,
    # whose action opens Settings › Connections › Browser. A pack the user
    # already added as "puppeteer" keeps working and shows as "Added by you".
    # ---------------- Communication ----------------
    Connector(
        id="slack",
        name="Slack",
        category="Communication",
        glyph="💬",
        blurb="Read channels and post messages.",
        unlocks="Search your Slack workspace, read channel history, and post or "
        "reply to messages on your behalf.",
        connect_via="mcp",
        scopes=["Read channels & messages", "Post messages"],
        # v1.353.0 (G43): the maintained server. The reference
        # @modelcontextprotocol/server-slack is "no longer supported" on npm;
        # the official servers README names Zencoder's as its maintainer, and
        # `npm view` (2026-10-18) confirmed it reads the SAME two settings,
        # SLACK_BOT_TOKEN and SLACK_TEAM_ID, speaks stdio by default and has
        # one bin, so `npx -y` runs it with the same arguments.
        docs_url="https://github.com/zencoderai/slack-mcp-server",
        command="npx",
        args=["-y", "@zencoderai/slack-mcp-server"],
        fields=[
            Field("SLACK_BOT_TOKEN", "Slack bot token", help="A xoxb-… bot token from your Slack app."),
            Field("SLACK_TEAM_ID", "Slack team id", help="Your workspace/team id (T…).", kind="env"),
        ],
    ),
    Connector(
        id="gmail",
        name="Gmail",
        category="Communication",
        glyph="✉️",
        blurb="Read, search, and send email.",
        unlocks="Search your inbox, read and summarize threads, draft and send email.",
        connect_via="mcp",
        scopes=["Read mail", "Send mail"],
        docs_url="https://github.com/GongRzhe/Gmail-MCP-Server",
        command="npx",
        args=["-y", "@gongrzhe/server-gmail-autoauth-mcp"],
        # v1.353.0 (G45): said BEFORE Connect (it was the blurb's last clause).
        needs="Needs a one-time Google developer setup",
    ),
    # ---------------- Productivity ----------------
    Connector(
        id="notion",
        name="Notion",
        category="Productivity",
        glyph="📝",
        blurb="Search and edit your Notion.",
        unlocks="Search, read, create, and update Notion pages and databases from "
        "chat — turn conversations into docs.",
        connect_via="mcp",
        scopes=["Read pages & databases", "Create & update pages"],
        docs_url="https://developers.notion.com/docs/mcp",
        command="npx",
        args=["-y", "@notionhq/notion-mcp-server"],
        fields=[
            Field("NOTION_TOKEN", "Notion integration token", help="An internal integration token (ntn_…) shared with your pages.")
        ],
    ),
    Connector(
        id="google_maps",
        name="Google Maps",
        category="Productivity",
        glyph="🗺️",
        blurb="Places, directions, geocoding.",
        unlocks="Look up places, get directions and travel times, and geocode "
        "addresses via the Google Maps APIs.",
        connect_via="mcp",
        scopes=["Places", "Directions", "Geocoding"],
        docs_url="https://console.cloud.google.com/google/maps-apis/credentials",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-google-maps"],
        fields=[Field("GOOGLE_MAPS_API_KEY", "Google Maps API key", help="A Maps Platform API key.")],
        package_note=OLDER_PACKAGE_NOTE,
    ),
    # ---------------- Storage ----------------
    Connector(
        id="google_drive",
        name="Google Drive",
        category="Storage",
        glyph="📂",
        blurb="Search & ingest Drive files.",
        unlocks="Connect a Google Drive so its files can be searched and pulled "
        "into long-term memory and chat.",
        connect_via="oauth",
        scopes=["Read Drive files"],
        provider="google_drive",
        docs_url="https://console.cloud.google.com/apis/credentials",
        # v1.353.0 (G45): shown only while no OAuth client is registered.
        needs="Needs a one-time Google developer setup",
    ),
    Connector(
        id="onedrive",
        name="OneDrive",
        category="Storage",
        glyph="☁️",
        blurb="Search & ingest OneDrive files.",
        unlocks="Connect OneDrive so its files can be searched and ingested into "
        "memory.",
        connect_via="oauth",
        scopes=["Read/write files"],
        provider="onedrive",
        docs_url="https://portal.azure.com",
        needs="Needs a one-time Microsoft developer setup",
    ),
    Connector(
        id="dropbox",
        name="Dropbox",
        category="Storage",
        glyph="🗄️",
        blurb="Search & ingest Dropbox files.",
        unlocks="Connect Dropbox so its files can be searched and ingested into "
        "memory.",
        connect_via="oauth",
        scopes=["Read files"],
        provider="dropbox",
        docs_url="https://www.dropbox.com/developers/apps",
        needs="Needs a one-time Dropbox developer setup",
    ),
    Connector(
        id="box",
        name="Box",
        category="Storage",
        glyph="📦",
        blurb="Manage your Box files.",
        # v1.353.0 (G42/G45): no mechanism word on the card, and the helper
        # program it needs (uv) is the card's own "Needs uv" line, said only
        # when this PC lacks it.
        unlocks="Search, read, and manage files in Box, through Box's own server.",
        connect_via="mcp",
        scopes=["Read & manage files"],
        docs_url="https://developer.box.com",
        command="uvx",
        args=["mcp-server-box"],
        fields=[
            Field("BOX_CLIENT_ID", "Box client id", help="From a Box custom app."),
            Field("BOX_CLIENT_SECRET", "Box client secret", help="From a Box custom app."),
        ],
    ),
    # ---------------- Data & Web ----------------
    Connector(
        id="fetch",
        name="Web Fetch",
        category="Data & Web",
        glyph="🌐",
        blurb="Fetch & clean web pages.",
        unlocks="Fetch any URL and return clean, readable content for the agent — "
        "no token required.",
        connect_via="mcp",
        scopes=["Fetch web pages"],
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/fetch",
        command="uvx",
        args=["mcp-server-fetch"],
    ),
    Connector(
        id="brave_search",
        name="Brave Search",
        category="Data & Web",
        glyph="🔎",
        blurb="Private web search.",
        unlocks="Search the web with Brave for fresh, cited results the agent can "
        "read and summarize.",
        connect_via="mcp",
        scopes=["Web search"],
        docs_url="https://brave.com/search/api/",
        command="npx",
        # Brave's OWN server — the upstream @modelcontextprotocol/server-brave-
        # search was deprecated (archived; connects but advertises no tools).
        args=["-y", "@brave/brave-search-mcp-server"],
        fields=[Field("BRAVE_API_KEY", "Brave Search API key", help="From the Brave Search API dashboard.")],
    ),
    Connector(
        id="memory",
        name="Knowledge Graph Memory",
        category="Data & Web",
        glyph="🧠",
        blurb="A persistent knowledge graph.",
        unlocks="Give the agent a durable knowledge-graph memory it can grow and "
        "query across sessions — no token required.",
        connect_via="mcp",
        scopes=["Read/write graph memory"],
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/memory",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-memory"],
    ),
    Connector(
        id="sequential_thinking",
        name="Sequential Thinking",
        category="Data & Web",
        glyph="🪜",
        blurb="Structured step-by-step reasoning.",
        unlocks="A scratchpad tool that lets the agent reason through hard problems "
        "one deliberate step at a time — no token required.",
        connect_via="mcp",
        scopes=["Reasoning scratchpad"],
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-sequential-thinking"],
    ),
    # ---------------- Payments ----------------
    Connector(
        id="stripe",
        name="Stripe",
        category="Payments",
        glyph="💳",
        blurb="Look up customers & payments.",
        unlocks="Query customers, payments, invoices, and subscriptions in Stripe "
        "in plain English (use a restricted key).",
        connect_via="mcp",
        scopes=["Read customers, payments, invoices"],
        docs_url="https://docs.stripe.com/mcp",
        command="npx",
        args=["-y", "@stripe/mcp", "--tools=all"],
        fields=[
            Field("STRIPE_SECRET_KEY", "Stripe secret key", help="Prefer a RESTRICTED key (rk_…) from the Stripe dashboard.")
        ],
    ),
    # ---------------- AI & Media ----------------
    Connector(
        id="pixio",
        name="Pixio",
        category="AI & Media",
        glyph="🎨",
        blurb="Generate images, video, and audio.",
        unlocks="Power the Creative gallery and the pixio_* tools — generate and "
        "edit images, video, and music.",
        connect_via="api_key",
        scopes=["Generate media"],
        provider="pixio",
        docs_url="https://beta.pixio.myapps.ai",
    ),
]

#: v1.353.0 (G43): cards that point somewhere instead of connecting here. The
#: gallery lists them after the catalog (``connectors.service.list_connectors``);
#: their one action opens ``href``. Kept OUT of :data:`CATALOG` on purpose:
#: :func:`get_connector` answers only connectable entries, so a link card can
#: never reach a connect / test / disconnect door or the chat's app_connect.
LINK_CARDS: list[Connector] = [
    Connector(
        id="browser_addon",
        name="Your browser",
        category="Data & Web",
        glyph="🧭",
        blurb="Let Jarvis read and act in the browser you already use.",
        unlocks="Open pages, read them, click and fill in forms in your own "
        "browser, through the Iron Jarvis browser add-on.",
        connect_via=LINK,
        scopes=["Read pages", "Click and type"],
        # Settings › Connections › Browser through its old address, which
        # redirects there. The card lives INSIDE Settings
        # (/settings?section=connections-apps), and a link to
        # /settings?section=… only changes the address bar: SettingsHome
        # reads its section once, on mount, and the App Router keeps a page
        # mounted across a search-param change (measured on the running
        # dashboard). The old address goes through another route, so the
        # page mounts again on the Browser section.
        href="/computeruse",
        action="Set up the browser add-on",
    ),
]

#: v1.353.0 integrate: names a model (or an old Handbook) may still use for
#: the browser — "puppeteer" was a catalog entry until G43 — mapped to the
#: link card that now stands for it. Read by :func:`get_link_card` only:
#: ``get_connector`` still answers real connectors alone.
LINK_ALIASES: dict[str, str] = {
    "puppeteer": "browser_addon",
    "browser": "browser_addon",
}


def get_link_card(connector_id: str) -> Connector | None:
    """The LINK card *connector_id* names (its id or an alias), or None."""
    key = (connector_id or "").strip().lower()
    key = LINK_ALIASES.get(key, key)
    return next((c for c in LINK_CARDS if c.id == key), None)

#: Category display order for the gallery.
CATEGORY_ORDER = [
    "Developer",
    "Communication",
    "Productivity",
    "Storage",
    "Data & Web",
    "Payments",
    "AI & Media",
]


def get_connector(connector_id: str) -> Connector | None:
    return next((c for c in CATALOG if c.id == connector_id), None)


def field_dict(f: Field) -> dict:
    return {"name": f.name, "label": f.label, "help": f.help, "kind": f.kind, "optional": f.optional}


def connector_dict(c: Connector) -> dict:
    """The catalog half of a connector as JSON (status is merged in by the
    service). Never includes secrets."""
    return {
        "id": c.id,
        "name": c.name,
        "category": c.category,
        "glyph": c.glyph,
        "blurb": c.blurb,
        "unlocks": c.unlocks,
        "connect_via": c.connect_via,
        "scopes": list(c.scopes),
        "docs_url": c.docs_url,
        "fields": [field_dict(f) for f in c.fields],
        "provider": c.provider,
        # v1.353.0 (G45/G43): the setup it needs and a word about its package
        # ("" when none). For an OAuth app the service blanks `needs` once a
        # client is registered.
        "needs": c.needs,
        "package_note": c.package_note,
        # A LINK card's one action (both "" for every other card).
        "href": c.href,
        "action": c.action,
    }
