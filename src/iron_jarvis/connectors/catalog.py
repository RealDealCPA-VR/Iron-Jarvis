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


# --------------------------------------------------------------------------- #
# The catalog. Grouped by category (order preserved for the gallery).
# --------------------------------------------------------------------------- #
_GH = "https://github.com/settings/tokens"

CATALOG: list[Connector] = [
    # ---------------- Developer ----------------
    Connector(
        id="github",
        name="GitHub",
        category="Developer",
        glyph="🐙",
        blurb="Repos, issues, and pull requests.",
        unlocks="Search repositories, read and open issues & PRs, review diffs, and "
        "manage your GitHub — all from chat and agents.",
        connect_via="mcp",
        scopes=["Repositories", "Issues & pull requests", "Code search"],
        docs_url=_GH,
        command="npx",
        args=["-y", "@modelcontextprotocol/server-github"],
        fields=[
            Field(
                "GITHUB_PERSONAL_ACCESS_TOKEN",
                "GitHub personal access token",
                help=f"Create a token (repo scope) at {_GH}.",
            )
        ],
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
    Connector(
        id="puppeteer",
        name="Browser (Puppeteer)",
        category="Developer",
        glyph="🎭",
        blurb="Automate a real browser.",
        unlocks="Navigate pages, click, fill forms, and capture screenshots via a "
        "headless Chrome — no token required.",
        connect_via="mcp",
        scopes=["Browser automation"],
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/puppeteer",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-puppeteer"],
    ),
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
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/slack",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-slack"],
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
        unlocks="Search your inbox, read and summarize threads, draft and send "
        "email. Needs Google OAuth credentials (see docs) on first run.",
        connect_via="mcp",
        scopes=["Read mail", "Send mail"],
        docs_url="https://github.com/GongRzhe/Gmail-MCP-Server",
        command="npx",
        args=["-y", "@gongrzhe/server-gmail-autoauth-mcp"],
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
    ),
    Connector(
        id="box",
        name="Box",
        category="Storage",
        glyph="📦",
        blurb="Manage your Box files.",
        unlocks="Search, read, and manage files in Box (Box's own MCP server; needs "
        "uv installed).",
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
    }
