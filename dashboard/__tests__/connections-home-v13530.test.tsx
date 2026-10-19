/**
 * v1.353.0 lane 3C (product-readiness wave 3): one Connections home.
 *
 * Settings › Connections › Apps & tools (the Directory) as a NEW user meets it:
 *
 * - G42: one line names the tools their other apps already use, with an
 *   Import button that opens the Tools page's SAME "Import from your other
 *   apps" door here (nothing is added without a press); a line says what is
 *   built in; the other doors (another computer, add by config) are named;
 *   no card carries the "MCP / OAUTH / API KEY" tag, and "one tap" is gone.
 * - G43: the browser card opens the add-on's settings (Puppeteer is no
 *   longer offered); a package npm retired says so; the Tools page's npm
 *   example is a package that installs.
 * - G45: before Connect, Gmail says it needs a Google developer setup, a
 *   drive says the same while no OAuth client is registered, a pack whose
 *   helper program is missing says "Needs Node.js"; after a drive sign-in
 *   the card says to choose a folder for memory; Gmail, Slack and Notion
 *   each name the OTHER place people look for them.
 *
 * Driven through the REAL pages and the real useApi; only lib/api is a fake
 * (the house pattern). A waitFor waits for the thing it asserts.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    gets: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    connectors: [] as Record<string, unknown>[],
    categories: [] as string[],
    sources: [] as Record<string, unknown>[] | null,
    sourcesFail: false,
    apply: { added: [], skipped: [] } as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  sseUrl: (p: string) => p,
  wsUrl: (p: string) => p,
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  get: async (path: string) => {
    api.gets.push(path);
    if (path === "/connectors") return { connectors: api.connectors, categories: api.categories };
    if (path === "/mcp/import/sources") {
      if (api.sourcesFail) throw new api.FakeApiError("no such route", 404);
      return { sources: api.sources };
    }
    if (path === "/mcp/servers") return { servers: [] };
    if (path === "/mcp/catalog") return { catalog: [] };
    if (path === "/tools/custom") return { tools: [] };
    return {};
  },
  post: async (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    if (path === "/mcp/import/apply") return api.apply;
    return {};
  },
  put: async () => ({}),
  patch: async () => ({}),
  del: async () => ({}),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: () => {},
  }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/",
}));
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));

const { __resetApiCache } = await import("@/lib/apiCache");
const DirectoryPage = (await import("@/components/settings/pages/DirectoryPage")).default;
const ToolsPage = (await import("@/components/settings/pages/ToolsPage")).default;
const { appName, discover, discoveryLine, listWords } = await import("@/lib/importDiscovery");

/* ------------------------------------------------------------ fixtures --- */

function connector(id: string, name: string, over: Record<string, unknown> = {}) {
  return {
    id,
    name,
    category: "Developer",
    glyph: "",
    blurb: `${name} blurb.`,
    unlocks: `${name} unlocks.`,
    connect_via: "mcp",
    scopes: [],
    docs_url: "",
    fields: [],
    provider: "",
    connected: false,
    status: "disconnected",
    tools_loaded: 0,
    tool_names: [],
    needs: "",
    package_note: "",
    href: "",
    action: "",
    ...over,
  };
}

/** What GET /mcp/import/sources answers on a PC with Claude Desktop, Cursor,
 *  Claude Code and an unreadable Codex. `github` is listed twice (one tool);
 *  `fetch` is already added. */
const SOURCES = [
  {
    source: "claude-desktop",
    label: "Claude Desktop",
    origin: "C:/Users/me/AppData/Roaming/Claude/claude_desktop_config.json",
    readable: true,
    note: "",
    servers: [
      { name: "github", transport: "stdio", command: "npx", args: ["-y", "gh-server"], exists: false, runtime_ready: true },
      { name: "notion", transport: "stdio", command: "npx", args: ["-y", "notion-server"], exists: false, runtime_ready: true },
    ],
  },
  {
    source: "cursor",
    label: "Cursor",
    origin: "C:/Users/me/.cursor/mcp.json",
    readable: true,
    note: "",
    servers: [
      { name: "github", transport: "stdio", command: "npx", args: ["-y", "gh-server"], exists: false, runtime_ready: true },
      { name: "linear", transport: "http", url: "https://mcp.linear.app/mcp", exists: false },
    ],
  },
  {
    source: "claude-code-settings",
    label: "Claude Code settings",
    origin: "C:/Users/me/.claude/settings.json",
    readable: true,
    note: "",
    servers: [{ name: "fetch", transport: "stdio", command: "uvx", args: ["mcp-server-fetch"], exists: true, runtime_ready: true }],
  },
  { source: "codex", label: "Codex", origin: "", readable: false, note: "", servers: [] },
];

const card = (id: string) => screen.getByTestId(`connector-card-${id}`);

/** Every non-empty text node under `el`, trimmed. */
function textNodes(el: HTMLElement): string[] {
  const out: string[] = [];
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const t = (n.textContent ?? "").trim();
    if (t) out.push(t);
  }
  return out;
}

/** A remount must not paint the previous render's answer from lib/apiCache. */
function remount() {
  cleanup();
  __resetApiCache();
}

beforeEach(() => {
  localStorage.clear();
  __resetApiCache();
  api.gets = [];
  api.posts = [];
  api.connectors = [connector("github", "GitHub")];
  api.categories = ["Developer"];
  api.sources = SOURCES.map((s) => ({ ...s, servers: s.servers.map((r) => ({ ...r })) }));
  api.sourcesFail = false;
  api.apply = { added: [], skipped: [] };
});
afterEach(() => cleanup());

/* ---------------------------------------------------- the discovery words --- */

describe("lib/importDiscovery: one line about what the other apps use", () => {
  it("counts tools by name across apps, names the apps that hold new ones", () => {
    const d = discover(SOURCES as never);
    expect(d.found).toBe(4);
    expect(d.fresh).toBe(3); // github once, notion, linear — fetch is already added
    expect(d.freshApps).toEqual(["Claude Desktop", "Cursor"]);
    expect(d.checkedApps).toEqual(["Claude Desktop", "Cursor", "Claude Code", "Codex"]);
    expect(discoveryLine(d)).toBe("Found 3 tools in Claude Desktop and Cursor.");
  });

  it("says when everything found is already added, and when nothing was found", () => {
    const allAdded = SOURCES.map((s) => ({ ...s, servers: s.servers.map((r) => ({ ...r, exists: true })) }));
    expect(discoveryLine(discover(allAdded as never))).toBe(
      "Everything found in Claude Desktop, Cursor and Claude Code is already added.",
    );
    const empty = SOURCES.map((s) => ({ ...s, servers: [] }));
    expect(discoveryLine(discover(empty as never))).toBe(
      "No tools found in Claude Desktop, Cursor, Claude Code or Codex.",
    );
    expect(discoveryLine(discover([]))).toBe("");
    expect(discoveryLine(discover(null))).toBe("");
  });

  it("names Claude Code's three files as one app, and lists in plain words", () => {
    expect(appName({ source: "claude-code-project", label: "Claude Code (this project)" })).toBe("Claude Code");
    expect(appName({ source: "vscode", label: "VS Code" })).toBe("VS Code");
    expect(listWords(["A"])).toBe("A");
    expect(listWords(["A", "B"])).toBe("A and B");
    expect(listWords(["A", "B", "C"], "or")).toBe("A, B or C");
  });
});

/* ------------------------------------------------- G42: one home --- */

describe("G42: the Directory is one Connections home", () => {
  it("names what the other apps use, with Import — and adds nothing until a press", async () => {
    render(<DirectoryPage />);
    const line = await screen.findByTestId("directory-import-line");
    expect(line.textContent).toBe("Found 3 tools in Claude Desktop and Cursor.");
    const open = screen.getByTestId("directory-import-open");
    expect(open.textContent).toBe("Import");
    expect(open.getAttribute("aria-expanded")).toBe("false");
    expect(open.className).not.toMatch(/\bbtn-accent\b/);
    // Nothing added, nothing listed, before the press.
    expect(api.posts).toEqual([]);
    expect(screen.queryByTestId("import-preview")).toBeNull();
  });

  it("Import opens the SAME door here, and Add selected imports through the one apply route", async () => {
    api.apply = { added: [{ name: "github", tools_loaded: 3 }, { name: "notion", tools_loaded: 2 }], skipped: [] };
    render(<DirectoryPage />);
    fireEvent.click(await screen.findByTestId("directory-import-open"));
    expect(screen.getByTestId("directory-import-open").getAttribute("aria-expanded")).toBe("true");
    // The door's own groups, fed the list the page already read (one read).
    const group = await screen.findByTestId("import-source-claude-desktop");
    expect(within(group).getByTestId("import-row-github")).toBeTruthy();
    expect(screen.getByTestId("import-source-cursor")).toBeTruthy();
    expect(api.gets.filter((p) => p === "/mcp/import/sources")).toHaveLength(1);
    // What the re-read after the import answers: the two are added now.
    api.sources = SOURCES.map((s) => ({
      ...s,
      servers: s.servers.map((r) => ({ ...r, exists: r.exists || ["github", "notion"].includes(r.name) })),
    }));
    fireEvent.click(within(group).getByTestId("import-source-apply-claude-desktop"));
    await waitFor(() =>
      expect(api.posts).toContainEqual({
        path: "/mcp/import/apply",
        body: { source: "claude-desktop", names: ["github", "notion"], overrides: {}, auto_approve: false, replace: false },
      }),
    );
    expect((await screen.findByTestId("import-result-github")).textContent).toContain("3 tools ready");
    // The page re-reads the cards and the list (the new packs show at once).
    await waitFor(() => expect(api.gets.filter((p) => p === "/mcp/import/sources")).toHaveLength(2));
    expect(api.gets.filter((p) => p === "/connectors").length).toBeGreaterThanOrEqual(2);
    // Rows the re-read calls "already added" leave the selection, so the
    // button never offers to add them a second time.
    await waitFor(() =>
      expect(screen.getByTestId("import-source-apply-claude-desktop").textContent).toContain("Add selected (0)"),
    );
    expect(screen.getByTestId("import-source-apply-claude-desktop").hasAttribute("disabled")).toBe(true);
  });

  it("everything already added: says so, and the button only shows them", async () => {
    api.sources = SOURCES.map((s) => ({ ...s, servers: s.servers.map((r) => ({ ...r, exists: true })) }));
    render(<DirectoryPage />);
    const line = await screen.findByTestId("directory-import-line");
    expect(line.textContent).toBe("Everything found in Claude Desktop, Cursor and Claude Code is already added.");
    expect(screen.getByTestId("directory-import-open").textContent).toBe("See them");
  });

  it("nothing found: one quiet line, no button; a failed read: nothing at all", async () => {
    api.sources = SOURCES.map((s) => ({ ...s, servers: [] }));
    render(<DirectoryPage />);
    expect((await screen.findByTestId("directory-import-line")).textContent).toBe(
      "No tools found in Claude Desktop, Cursor, Claude Code or Codex.",
    );
    expect(screen.queryByTestId("directory-import-open")).toBeNull();
    remount();
    api.sourcesFail = true;
    render(<DirectoryPage />);
    await screen.findByTestId("connector-card-github");
    await waitFor(() => expect(api.gets).toContain("/mcp/import/sources"));
    await waitFor(() => expect(screen.queryByTestId("directory-import")).toBeNull());
  });

  it("says what is built in, and names the doors that live elsewhere", async () => {
    render(<DirectoryPage />);
    const built = await screen.findByTestId("directory-built-in");
    expect(built.textContent).toBe(
      "Built in, nothing to connect: Files & documents, Web, Images, Spreadsheets, Memory.",
    );
    const more = screen.getByTestId("directory-more-ways");
    const hrefs = within(more)
      .getAllByRole("link")
      .map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(["/agents?view=team", "/fleet", "/tools?focus=packs"]);
    expect(within(more).getByRole("link", { name: /Add a tool by config \(advanced\)/ })).toBeTruthy();
  });

  it("v1.353.0 integrate: a group with no card gets no chip (the daemon names 'Added by you' on every install)", async () => {
    api.connectors = [connector("github", "GitHub"), connector("pixio", "Pixio", { category: "AI & Media" })];
    api.categories = ["Developer", "Storage", "AI & Media", "Added by you", "Memory"];
    render(<DirectoryPage />);
    await screen.findByTestId("connector-card-github");
    const chips = screen.getByRole("group", { name: /Filter by category/ });
    const names = within(chips)
      .getAllByRole("button")
      .map((b) => b.textContent?.trim());
    expect(names).toEqual(["All", "Developer", "AI & Media"]);
    // The summary counts the same groups.
    expect((screen.getByTestId("directory-summary").textContent ?? "").replace(/\s+/g, " ")).toMatch(
      /\b2 categories\b/,
    );
  });

  it("no card says MCP: the mechanism tag is gone (a tooltip on the category line), and 'one tap' too", async () => {
    api.connectors = [
      connector("github", "GitHub"),
      connector("google_drive", "Google Drive", { category: "Storage", connect_via: "oauth", provider: "google_drive" }),
      connector("pixio", "Pixio", { category: "AI & Media", connect_via: "api_key", provider: "pixio" }),
      connector("notes_pack", "notes_pack", {
        category: "Added by you",
        blurb: "Read one note by its title. 3 tools.",
        connected: true,
        status: "connected",
        tools_loaded: 3,
      }),
    ];
    api.categories = ["Developer", "Storage", "AI & Media"];
    render(<DirectoryPage />);
    for (const id of ["github", "google_drive", "pixio", "notes_pack"]) {
      const el = await screen.findByTestId(`connector-card-${id}`);
      // Each text node on its own: a card's whole textContent runs "GitHub"
      // and a tag's "MCP" together, where no word boundary can see it.
      for (const text of textNodes(el)) {
        expect(text, id).not.toMatch(/\bMCP\b|\bOAuth\b|\bAPI key\b/i);
      }
      expect(textNodes(el).length, `${id}: the walker reads the card`).toBeGreaterThan(3);
    }
    expect(within(card("github")).getByText("Developer").getAttribute("title")).toBe(
      "Runs a small helper program on this PC",
    );
    expect(within(card("google_drive")).getByText("Storage").getAttribute("title")).toBe(
      "Connects by signing in to your account",
    );
    expect(document.body.textContent).not.toMatch(/one tap/i);
    // The user's own group has its chip even before the daemon's list names it.
    const chips = screen.getByRole("group", { name: /Filter by category/ });
    fireEvent.click(within(chips).getByRole("button", { name: "Added by you" }));
    expect(screen.getByTestId("connector-card-notes_pack")).toBeTruthy();
    expect(screen.queryByTestId("connector-card-github")).toBeNull();
  });
});

/* ------------------------------------------------- G43: entries that install --- */

describe("G43: catalog entries that still install", () => {
  it("the browser card opens the add-on's settings and offers no Connect", async () => {
    api.connectors = [
      connector("browser_addon", "Your browser", {
        category: "Data & Web",
        connect_via: "link",
        href: "/computeruse",
        action: "Set up the browser add-on",
      }),
    ];
    api.categories = ["Data & Web"];
    render(<DirectoryPage />);
    const link = await screen.findByTestId("connector-link-browser_addon");
    expect(link.getAttribute("href")).toBe("/computeruse");
    expect(link.textContent).toContain("Set up the browser add-on");
    expect(within(card("browser_addon")).queryByRole("button", { name: /Connect/ })).toBeNull();
    expect(document.body.textContent).not.toMatch(/Puppeteer/);
  });

  it("a package npm retired says so on its card", async () => {
    api.connectors = [connector("postgres", "Postgres", { package_note: "Uses an older package its makers no longer update." })];
    render(<DirectoryPage />);
    expect((await screen.findByTestId("connector-package-note-postgres")).textContent).toBe(
      "Uses an older package its makers no longer update.",
    );
  });

  it("the Tools page's npm example installs, and its blurb says what a helper program is", async () => {
    render(<ToolsPage />);
    const example = await screen.findByText("@modelcontextprotocol/server-filesystem");
    expect(example.tagName).toBe("CODE");
    expect(document.body.textContent).not.toContain("@modelcontextprotocol/server-slack");
    expect(document.body.textContent).toContain(
      "Some need a free helper program (Node.js). We'll tell you if one is missing.",
    );
    expect(document.body.textContent).not.toContain("(MCP)");
  });
});

/* ------------------------------------------------- G45: needs before Connect --- */

describe("G45: setup needs are shown before Connect", () => {
  it("Gmail says it needs a Google developer setup BEFORE the press, and not once it is connected", async () => {
    api.connectors = [
      connector("gmail", "Gmail", { category: "Communication", needs: "Needs a one-time Google developer setup" }),
    ];
    api.categories = ["Communication"];
    render(<DirectoryPage />);
    const need = await screen.findByTestId("connector-needs-setup-gmail");
    expect(need.textContent).toBe("Needs a one-time Google developer setup");
    // Words, not colour alone: the calm amber dot beside them.
    expect(need.getAttribute("data-tone")).toBe("amber");
    expect(within(card("gmail")).getByRole("button", { name: /Connect/ })).toBeTruthy();
    remount();
    api.connectors = [
      connector("gmail", "Gmail", {
        category: "Communication",
        needs: "Needs a one-time Google developer setup",
        connected: true,
        status: "connected",
        tools_loaded: 4,
      }),
    ];
    render(<DirectoryPage />);
    await screen.findByTestId("connector-card-gmail");
    expect(screen.queryByTestId("connector-needs-gmail")).toBeNull();
  });

  it("Drive says the same while no OAuth client is registered, and nothing once one is", async () => {
    const drive = (needs: string) =>
      connector("google_drive", "Google Drive", {
        category: "Storage",
        connect_via: "oauth",
        provider: "google_drive",
        needs,
        oauth_client_configured: needs === "",
      });
    api.connectors = [drive("Needs a one-time Google developer setup")];
    api.categories = ["Storage"];
    render(<DirectoryPage />);
    expect((await screen.findByTestId("connector-needs-setup-google_drive")).textContent).toBe(
      "Needs a one-time Google developer setup",
    );
    // Where it is done: the same place the after-the-press note points.
    expect(screen.getByTestId("connector-needs-howto-google_drive").getAttribute("href")).toBe("/connections");
    remount();
    api.connectors = [drive("")];
    render(<DirectoryPage />);
    await screen.findByTestId("connector-card-google_drive");
    expect(screen.queryByTestId("connector-needs-google_drive")).toBeNull();
  });

  it("after a Drive sign-in the card says to choose a folder for memory, and links there", async () => {
    const signedIn = (memory_source: boolean) =>
      connector("google_drive", "Google Drive", {
        category: "Storage",
        connect_via: "oauth",
        provider: "google_drive",
        connected: true,
        status: "connected",
        account: "me@example.com",
        memory_source,
      });
    api.connectors = [signedIn(false)];
    api.categories = ["Storage"];
    render(<DirectoryPage />);
    const next = await screen.findByTestId("connector-memory-next-google_drive");
    expect(next.textContent).toContain("Signed in as me@example.com. Now choose a folder for memory.");
    expect(within(next).getByRole("link", { name: /Choose a folder/ }).getAttribute("href")).toBe(
      "/memory?scope=longterm&focus=add-base",
    );
    expect(within(card("google_drive")).queryByText("Ready to use")).toBeNull();
    remount();
    api.connectors = [signedIn(true)];
    render(<DirectoryPage />);
    expect(await screen.findByText("Signed in as me@example.com")).toBeTruthy();
    expect(screen.queryByTestId("connector-memory-next-google_drive")).toBeNull();
  });

  it("a pack whose helper program is missing says 'Needs Node.js' before Connect; a found one says nothing", async () => {
    api.connectors = [
      connector("github", "GitHub", { runtime_ready: false, runtime: "Node.js", runtime_url: "https://nodejs.org" }),
      connector("fetch", "Web Fetch", { runtime_ready: true, runtime: "uv" }),
    ];
    render(<DirectoryPage />);
    const chip = await screen.findByTestId("connector-needs-runtime-github");
    expect(chip.textContent).toBe("Needs Node.js");
    expect(chip.getAttribute("title")).toContain("https://nodejs.org");
    expect(screen.queryByTestId("connector-needs-fetch")).toBeNull();
  });

  it("Gmail, Slack and Notion each name the OTHER place people look for them", async () => {
    api.connectors = [
      connector("gmail", "Gmail", { category: "Communication" }),
      connector("slack", "Slack", { category: "Communication" }),
      connector("notion", "Notion", { category: "Productivity" }),
      connector("github", "GitHub"),
    ];
    api.categories = ["Developer", "Communication", "Productivity"];
    render(<DirectoryPage />);
    const gmail = await screen.findByTestId("connector-also-gmail");
    expect(gmail.textContent).toBe("To have Jarvis email you, use Notifications › Email.");
    // The old address (it redirects to Settings › Connections › Notifications):
    // a /settings?section=… link from inside Settings only changes the URL.
    expect(within(gmail).getByRole("link").getAttribute("href")).toBe("/channels");
    const slack = screen.getByTestId("connector-also-slack");
    expect(slack.textContent).toBe("To have Jarvis message you in Slack, use Notifications › Slack.");
    expect(within(slack).getByRole("link").getAttribute("href")).toBe("/channels");
    const notion = screen.getByTestId("connector-also-notion");
    expect(notion.textContent).toBe("To make your Notion pages part of memory, add a memory base.");
    expect(within(notion).getByRole("link").getAttribute("href")).toBe("/memory?scope=longterm&focus=add-base");
    expect(screen.queryByTestId("connector-also-github")).toBeNull();
  });
});

/* ------------------------------------------------- house rules --- */

describe("the new copy keeps the house rules", () => {
  const read = (rel: string) => readFileSync(join(process.cwd(), rel), "utf8").replace(/\r\n/g, "\n");

  it("no spaced dash and no half-pixel size in the new Directory lines", () => {
    const src = read("components/settings/pages/DirectoryPage.tsx");
    for (const piece of [
      "Now choose a folder for memory.",
      "Built in, nothing to connect: ",
      "More ways to connect:",
      "To have Jarvis email you, use",
      "Looking in your other apps",
    ]) {
      expect(src, piece).toContain(piece);
      expect(piece).not.toMatch(/\s[—–]\s/);
    }
    const start = src.indexOf("function ImportFromApps");
    const end = src.indexOf("/*  Skeleton card");
    expect(start).toBeGreaterThan(0);
    expect(src.slice(start, end)).not.toMatch(/text-\[\d+\.5px\]/);
  });

  it("a link from this Settings section to another one goes through its old address", async () => {
    // SettingsHome reads ?section= once, on mount, and the App Router keeps a
    // page mounted across a search-param change (measured on the running
    // dashboard: a push to /settings?section=connections-browser changed the
    // URL and left the Directory on screen; /computeruse remounted Settings on
    // the Browser section). So no link here may point at /settings?section=.
    const src = read("components/settings/pages/DirectoryPage.tsx");
    expect(src).not.toMatch(/["'`]\/settings\?section=/);
    const { RELOCATED } = await import("@/lib/relocated");
    expect(RELOCATED["/channels"]).toEqual({ base: "/settings", extra: { section: "connections-notifications" } });
    expect(RELOCATED["/computeruse"]).toEqual({ base: "/settings", extra: { section: "connections-browser" } });
  });
});
