/**
 * v1.356.0, lane 6A (product-readiness wave 6): an extension can be switched
 * OFF without losing it (G78), and the Tools / Directory / Enable / Edit
 * surfaces speak without protocol words (G74), and a broken pack says what
 * it costs (G77).
 *
 * Driven through the REAL ToolsPage, DirectoryPage, EnableDialog, EditPack,
 * FilterBar and lib/extensionWords; only the data seams (lib/api) are mocked,
 * the way tools-pack-rows-v1342 does it. Pinned by what a new user sees:
 *  - each Tools row has an On/Off switch (role="switch"); pressing it PATCHes
 *    `{enabled}`; an off row reads "Off", says what off means, offers no
 *    Retry and no "didn't start"; on again says how many tools came back;
 *  - a Directory card reads the row's `enabled` (absent = on): off = "Off"
 *    badge + line + an unchecked switch, never "Needs attention" or Retry;
 *  - "Ask before each use" is a labelled switch with a visible "after a
 *    restart" note; settings are counted as "settings", never "env"; the
 *    launch line is folded under "Show command"; removal says Remove;
 *  - a failing row adds "Chats and agents can't use its tools until it works.";
 *  - the Enable dialog labels fields in words, raw key in a title;
 *  - ACCEPTANCE SWEEP: the text a person can read on the Tools page, the
 *    Directory and an open editor (closed folds and tooltips aside) holds
 *    none of MCP, stdio, SSE, env, runtime, Docker.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    posts: [] as string[],
    patches: [] as { path: string; body: unknown }[],
    dels: [] as string[],
    servers: [] as Record<string, unknown>[],
    catalog: [] as Record<string, unknown>[],
    connectors: [] as Record<string, unknown>[],
    /** What a switch press answers, by the `enabled` it was sent. */
    switchAnswer: null as null | ((name: string, on: boolean) => Record<string, unknown>),
    /** What POST /mcp/import/preview answers (the paste door). */
    preview: null as null | Record<string, unknown>,
    /** What GET /mcp/import/sources answers (review: the sources door). */
    sources: [] as Record<string, unknown>[],
    /** What POST /mcp/import/apply answers (review). */
    apply: null as null | Record<string, unknown>,
    /** GET /mcp/servers' auto_approve_effective (ship fix: the shared rule). */
    effective: false,
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
    if (path === "/mcp/servers") return { servers: api.servers, auto_approve_effective: api.effective };
    if (path === "/mcp/catalog") return { catalog: api.catalog };
    if (path === "/tools/custom") return { tools: [] };
    if (path === "/connectors") return { connectors: api.connectors, categories: ["Developer"] };
    if (path === "/mcp/import/sources") return { sources: api.sources };
    return {};
  },
  post: async (path: string) => {
    api.posts.push(path);
    if (path === "/mcp/import/preview" && api.preview) return api.preview;
    if (path === "/mcp/import/apply" && api.apply) return api.apply;
    return {};
  },
  put: async () => ({}),
  patch: async (path: string, body: unknown) => {
    api.patches.push({ path, body });
    const name = decodeURIComponent(path.split("/").pop() ?? "");
    const b = (body ?? {}) as { enabled?: boolean };
    if (typeof b.enabled === "boolean") {
      const answer = api.switchAnswer ? api.switchAnswer(name, b.enabled) : { name, enabled: b.enabled };
      // The daemon's re-read row, as it really answers it: a switched-off
      // pack holds NO tools (mcp_server_row: state "off", tools_loaded 0 —
      // review: a row that kept its 3 tools hid a Retry on an off row);
      // switched on, the tools the switch answer says came back.
      api.servers = api.servers.map((s) => {
        if (s.name !== name) return s;
        if (!b.enabled) return { ...s, enabled: false, state: "off", tools_loaded: 0, tool_names: [] };
        const a = answer as { tools_loaded?: unknown; tool_names?: unknown };
        return {
          ...s,
          enabled: true,
          state: "ok",
          tools_loaded: typeof a.tools_loaded === "number" ? a.tools_loaded : s.tools_loaded,
          tool_names: Array.isArray(a.tool_names) ? a.tool_names : s.tool_names,
        };
      });
      api.connectors = api.connectors.map((c) => (c.id === name ? { ...c, enabled: b.enabled } : c));
      return answer;
    }
    return {};
  },
  del: async (path: string) => {
    api.dels.push(path);
    return {};
  },
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
const ToolsPage = (await import("@/components/settings/pages/ToolsPage")).default;
const DirectoryPage = (await import("@/components/settings/pages/DirectoryPage")).default;
const { EnableDialog } = await import("@/components/tools/EnableDialog");
const { FilterBar, EMPTY_FILTERS } = await import("@/components/tools/FilterBar");
const words = await import("@/lib/extensionWords");

function row(name: string, over: Record<string, unknown> = {}) {
  return {
    name,
    command: "npx",
    args: ["-y", `@example/${name}`],
    tools_loaded: 0,
    tool_names: [],
    last_error: null,
    state: "ok",
    enabled: true,
    failures: 0,
    reason: null,
    fix: null,
    stderr_tail: "",
    env_keys: [],
    secret_keys: [],
    transport: "stdio",
    ...over,
  };
}

const SLACK = row("slack", {
  tools_loaded: 3,
  tool_names: ["post_message", "list_channels", "read_thread"],
  env_keys: ["SLACK_BOT_TOKEN", "SLACK_TEAM_ID"],
  secret_keys: ["SLACK_BOT_TOKEN"],
});
const SLACK_OFF = { ...SLACK, enabled: false, state: "off", tools_loaded: 0, tool_names: [] };
const BROKEN = row("brave", {
  state: "failed",
  last_error: "MCPError: the server closed the connection (exit code 1)",
  reason: "It needs a Brave key and none was given",
  fix: "Add the key, then Retry",
});

function connector(id: string, name: string, over: Record<string, unknown> = {}) {
  return {
    id,
    name,
    category: "Developer",
    glyph: "",
    blurb: `${name} blurb.`,
    unlocks: `${name} unlocks.`,
    connect_via: "mcp",
    scopes: ["Messages"],
    docs_url: "",
    fields: [{ name: "SLACK_BOT_TOKEN", label: "Bot token", help: "", kind: "secret", optional: false }],
    provider: "",
    connected: true,
    status: "connected",
    tools_loaded: 3,
    tool_names: ["post_message"],
    needs: "",
    package_note: "",
    href: "",
    action: "",
    ...over,
  };
}

const rowOf = (name: string) => document.querySelector(`[data-pack-row="${name}"]`) as HTMLElement;

/** The text a person can read: closed folds keep only their summary, and
 *  nothing in an attribute (titles are tooltips, by design). */
function visibleText(root: HTMLElement): string {
  const clone = root.cloneNode(true) as HTMLElement;
  for (const d of Array.from(clone.querySelectorAll("details"))) {
    if ((d as HTMLDetailsElement).open) continue;
    for (const child of Array.from(d.children)) {
      if (child.tagName.toLowerCase() !== "summary") child.remove();
    }
  }
  for (const s of Array.from(clone.querySelectorAll("script,style"))) s.remove();
  return clone.textContent ?? "";
}

const ENGINE_WORDS: [string, RegExp][] = [
  ["MCP", /\bMCP\b/],
  ["stdio", /\bstdio\b/i],
  ["SSE", /\bSSE\b/],
  ["env", /\benv\b/i],
  ["environment variable", /environment variable/i],
  ["runtime", /\bruntime\b/i],
  ["Docker", /\bDocker\b/],
];

function engineWordsIn(text: string): string[] {
  return ENGINE_WORDS.filter(([, rx]) => rx.test(text)).map(([w]) => w);
}

beforeEach(() => {
  localStorage.clear();
  __resetApiCache();
  api.gets = [];
  api.posts = [];
  api.patches = [];
  api.dels = [];
  api.servers = [SLACK];
  api.catalog = [];
  api.connectors = [];
  api.switchAnswer = null;
  api.preview = null;
  api.sources = [];
  api.apply = null;
  api.effective = false;
});
afterEach(() => cleanup());

/* ------------------------------------------------------------- G78 Tools --- */

describe("a Tools row switches its extension off and on without losing it", () => {
  it("off: PATCH {enabled:false}, the row reads Off, says what off means, no Retry, no failure", async () => {
    api.switchAnswer = (name, on) =>
      on
        ? { ...SLACK, reloaded: true, tools_loaded: 3, note: null, changed: true }
        : { ...SLACK_OFF, reloaded: false, tools_unloaded: 3, note: null, changed: true };
    render(<ToolsPage />);
    const sw = await screen.findByTestId("mcp-switch-slack");
    expect(sw.getAttribute("role")).toBe("switch");
    expect(sw.getAttribute("aria-checked")).toBe("true");
    expect(sw.getAttribute("aria-label")).toBe("Use slack");

    await act(async () => {
      fireEvent.click(sw);
    });
    expect(api.patches).toEqual([{ path: "/mcp/servers/slack", body: { enabled: false } }]);
    // The note says it is paused and nothing is lost.
    await screen.findByText(/"slack" is off\. Its tools are paused for chats and agents; its settings and keys are kept\./);
    // The re-read row: Off, the off line, no Retry, no failure line.
    await waitFor(() => expect(screen.getByTestId("mcp-state-slack").textContent).toBe("Off"));
    const r = rowOf("slack");
    expect(within(r).getByTestId("mcp-off-slack").textContent).toBe(words.PACK_OFF_LINE);
    expect(within(r).queryByTestId("mcp-retry-slack")).toBeNull();
    expect(within(r).queryByTestId("mcp-last-error-slack")).toBeNull();
    expect(within(r).queryByTestId("mcp-no-tools-slack")).toBeNull();
    expect(screen.getByTestId("mcp-switch-slack").getAttribute("aria-checked")).toBe("false");
    // The summary does not count a switched-off extension as enabled (the
    // "Enabled:" line itself — the box holds other counts too).
    expect(screen.getByTestId("tools-summary").textContent).toMatch(/built-in · 0 extensions ·/);

    // On again: one press, nothing typed; the note counts the tools.
    await act(async () => {
      fireEvent.click(screen.getByTestId("mcp-switch-slack"));
    });
    expect(api.patches[1]).toEqual({ path: "/mcp/servers/slack", body: { enabled: true } });
    await screen.findByText(/"slack" is on\. 3 tools ready to use\./);
    await waitFor(() => expect(screen.getByTestId("mcp-switch-slack").getAttribute("aria-checked")).toBe("true"));
    expect(screen.queryByTestId("mcp-off-slack")).toBeNull();
  });

  it("on, but it did not start: the warn note carries the daemon's reason and the row's way back", async () => {
    api.servers = [SLACK_OFF];
    api.switchAnswer = () => ({
      ...SLACK,
      reloaded: false,
      tools_loaded: 0,
      note: "switched on, but it did not start: the Slack token was refused; see its row for the fix",
    });
    render(<ToolsPage />);
    const sw = await screen.findByTestId("mcp-switch-slack");
    await act(async () => {
      fireEvent.click(sw);
    });
    const warn = await screen.findByTestId("mcp-add-warn");
    expect(warn.textContent).toContain(`"slack" is on, but it didn't start: the Slack token was refused. Open its row to retry.`);
  });

  it("a catalog card for an added-but-off extension reads Off, never the green Enabled", async () => {
    api.servers = [SLACK_OFF];
    api.catalog = [
      { id: "slack", name: "Slack", description: "Read and post messages.", command: "npx", args: [], category: "integration" },
    ];
    render(<ToolsPage />);
    await screen.findByTestId("packs-enabled");
    const cardEl = document.querySelector('[data-pack="slack"]') as HTMLElement;
    expect(within(cardEl).getByTestId("status-off").textContent).toBe("Off");
    expect(within(cardEl).queryByTestId("status-added")).toBeNull();
    expect(cardEl.className).not.toMatch(/emerald/);
    // Removal says Remove on the card too.
    expect(within(cardEl).getByTestId("pack-remove-slack").textContent).toContain("Remove");
  });

  it("a row the daemon reads as off (no tools) offers no Retry and no 'No tools loaded' (review)", async () => {
    api.servers = [SLACK_OFF];
    render(<ToolsPage />);
    await waitFor(() => expect(screen.getByTestId("mcp-state-slack").textContent).toBe("Off"));
    const r = rowOf("slack");
    expect(within(r).getByTestId("mcp-off-slack").textContent).toBe(words.PACK_OFF_LINE);
    expect(within(r).queryByTestId("mcp-retry-slack")).toBeNull();
    expect(within(r).queryByTestId("mcp-no-tools-slack")).toBeNull();
    expect(within(r).queryByTestId("mcp-last-error-slack")).toBeNull();
    expect(within(r).getByTestId("mcp-switch-slack").getAttribute("aria-checked")).toBe("false");
  });

  it("an edit saved while it is off says so, never 'didn't start'", async () => {
    api.servers = [SLACK_OFF];
    const { savedWhileOff } = words;
    expect(savedWhileOff("slack")).toBe(`"slack" saved. It's off, so it starts when you turn it on.`);
  });

  it("a server its app had switched off, ticked anyway, is ADDED SWITCHED OFF: no warn triangle, no chat line, its press lands on the row's switch (review)", async () => {
    const scrolled: Element[] = [];
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (this: Element) {
      scrolled.push(this);
    };
    try {
      // The row the daemon lists once it is saved: off, no tools.
      api.servers = [{ ...SLACK_OFF, name: "parked" }];
      api.sources = [
        {
          source: "claude-desktop",
          label: "Claude Desktop",
          origin: "C:\\AppData\\Claude\\claude_desktop_config.json",
          readable: true,
          note: "",
          servers: [
            {
              name: "parked",
              transport: "stdio",
              command: "npx",
              args: ["-y", "@example/parked"],
              url: "",
              header_names: [],
              env_keys: [],
              secret_keys: [],
              exists: false,
              runtime_ready: true,
              launcher_note: "",
              source: "claude-desktop",
              origin: "C:\\AppData\\Claude\\claude_desktop_config.json",
              disabled: true,
              exists_as: "",
              blurb: "",
            },
          ],
        },
      ];
      // The apply answer for a ticked disabled row (routes/agents._apply_specs).
      api.apply = {
        added: [
          {
            name: "parked",
            tools_loaded: 0,
            note: "saved switched off, as it was in the app it came from; turn it on to use it",
            state: "off",
          },
        ],
        skipped: [],
      };
      render(<ToolsPage />);
      const group = await screen.findByTestId("import-source-claude-desktop");
      const parked = await within(group).findByTestId("import-row-parked");
      fireEvent.click(within(parked).getByTestId("import-pick-parked"));
      fireEvent.click(within(group).getByTestId("import-source-apply-claude-desktop"));
      const result = await within(group).findByTestId("import-result-parked");
      expect(result.getAttribute("data-outcome")).toBe("off");
      expect(within(result).getByTestId("import-off-added-parked").textContent).toBe(
        "Added switched off, as it was in its app.",
      );
      // Saved, not started on purpose: never the warn triangle or the
      // "didn't start" words, and no "Use it in a chat" line for it.
      expect(within(result).queryByTestId("import-warn-parked")).toBeNull();
      expect(result.textContent).not.toMatch(/didn.t start/i);
      expect(within(group).queryByTestId("import-use-in-chat")).toBeNull();
      // Its press goes to the row and puts the focus on the row's switch
      // (an off row has no Retry).
      const jump = within(result).getByTestId("import-open-row-parked");
      expect(jump.textContent).toBe("Turn it on in its row below.");
      await screen.findByTestId("mcp-switch-parked");
      fireEvent.click(jump);
      expect(document.activeElement).toBe(screen.getByTestId("mcp-switch-parked"));
      expect(scrolled.length).toBeGreaterThan(0);
    } finally {
      Element.prototype.scrollIntoView = original;
    }
  });
});

/* ------------------------------------------------------- G74 Tools words --- */

describe("a Tools row in plain words", () => {
  it("counts settings (never 'env'), folds the launch line under Show command, and removes with Remove", async () => {
    render(<ToolsPage />);
    const env = await screen.findByTestId("mcp-env-keys-slack");
    expect(env.textContent).toBe("2 settings");
    expect(env.getAttribute("title")).toBe("SLACK_BOT_TOKEN, SLACK_TEAM_ID");
    const launch = screen.getByTestId("mcp-launch-slack");
    const fold = launch.closest("details") as HTMLDetailsElement;
    expect(fold).not.toBeNull();
    expect(fold.open).toBe(false);
    expect(fold.querySelector("summary")?.textContent).toContain("Show command");
    expect(within(rowOf("slack")).getByRole("button", { name: /Remove/ })).toBeTruthy();
    expect(within(rowOf("slack")).queryByRole("button", { name: /^Delete/ })).toBeNull();
  });

  it("'Ask before each use' is a labelled switch with a visible 'after a restart' note", async () => {
    render(<ToolsPage />);
    const ask = await screen.findByTestId("mcp-ask-slack");
    expect(ask.getAttribute("role")).toBe("switch");
    expect(ask.getAttribute("aria-label")).toBe("Ask before each use of slack");
    // auto_approve absent = agents ask = the switch is ON.
    expect(ask.getAttribute("aria-checked")).toBe("true");
    const r = rowOf("slack");
    expect(visibleText(r)).toContain("Ask before each use");
    expect(within(r).getByTestId("mcp-ask-note-slack").textContent).toMatch(/after a restart/);
    expect(visibleText(r)).not.toMatch(/auto-approve/i);
    await act(async () => {
      fireEvent.click(ask);
    });
    expect(api.patches).toEqual([{ path: "/mcp/servers/slack", body: { auto_approve: true } }]);
  });

  it("ship fix: an On switch says it is not in effect while app tools run without asking", async () => {
    // "Run without asking" is ONE shared rule (the global setting, or any
    // switched-on extension set to run unasked): a row whose switch reads On
    // must not promise agents will ask.
    api.effective = true;
    render(<ToolsPage />);
    const ask = await screen.findByTestId("mcp-ask-slack");
    expect(ask.getAttribute("aria-checked")).toBe("true");
    const note = within(rowOf("slack")).getByTestId("mcp-ask-note-slack");
    expect(note.textContent).toMatch(/not in effect right now: app tools are set to run without asking/);
    expect(note.getAttribute("title") ?? "").toMatch(/Run app tools without asking/);
    expect(note.textContent).not.toMatch(/after a restart/);
  });

  it("ship fix: the row that IS the reason keeps the plain restart note", async () => {
    api.effective = true;
    api.servers = [{ ...SLACK, auto_approve: true }];
    render(<ToolsPage />);
    await screen.findByTestId("mcp-ask-slack");
    const note = within(rowOf("slack")).getByTestId("mcp-ask-note-slack");
    await waitFor(() => expect(note.textContent).toMatch(/after a restart/));
    expect(note.getAttribute("title")).toBeNull();
  });

  it("an extension that runs without asking shows the switch OFF", async () => {
    api.servers = [{ ...SLACK, auto_approve: true }];
    render(<ToolsPage />);
    expect((await screen.findByTestId("mcp-ask-slack")).getAttribute("aria-checked")).toBe("false");
  });

  it("an http extension's fold says Show address", async () => {
    api.servers = [row("remote", { transport: "http", url: "https://host/mcp", command: "" })];
    render(<ToolsPage />);
    const launch = await screen.findByTestId("mcp-launch-remote");
    expect(launch.closest("details")?.querySelector("summary")?.textContent).toContain("Show address");
  });
});

/* ---------------------------------------------------------------- G77 ----- */

describe("a broken extension says what it costs", () => {
  it("adds 'Chats and agents can't use its tools until it works.' to the failing row", async () => {
    api.servers = [BROKEN];
    render(<ToolsPage />);
    const line = await screen.findByTestId("mcp-last-error-brave");
    expect(within(line).getByTestId("mcp-consequence-brave").textContent).toBe(
      "Chats and agents can't use its tools until it works.",
    );
    expect(line.textContent).toContain("Didn’t start: It needs a Brave key and none was given.");
    expect(within(line).getByTestId("mcp-retry-brave")).toBeTruthy();
  });

  it("a healthy row carries no such sentence", async () => {
    render(<ToolsPage />);
    await screen.findByTestId("mcp-state-slack");
    expect(screen.queryByTestId("mcp-consequence-slack")).toBeNull();
  });
});

/* ---------------------------------------------------------- G78 Directory --- */

describe("a Directory card switches its app off and on", () => {
  it("absent `enabled` reads ON: Connected, the switch checked", async () => {
    api.connectors = [connector("slack", "Slack")];
    render(<DirectoryPage />);
    const sw = await screen.findByTestId("connector-switch-slack");
    expect(sw.getAttribute("role")).toBe("switch");
    expect(sw.getAttribute("aria-checked")).toBe("true");
    expect(screen.getByTestId("connector-card-slack").getAttribute("data-health")).toBe("connected");
  });

  it("enabled:false reads Off with its line, never Needs attention or Retry, even if the status says no_tools", async () => {
    api.connectors = [connector("slack", "Slack", { enabled: false, status: "no_tools", tools_loaded: 0 })];
    render(<DirectoryPage />);
    const c = await screen.findByTestId("connector-card-slack");
    expect(within(c).getByTestId("connector-off-slack").textContent).toBe("Off");
    expect(within(c).getByTestId("connector-off-line-slack").textContent).toBe(words.PACK_OFF_LINE);
    expect(within(c).queryByTestId("connector-attention-slack")).toBeNull();
    expect(within(c).queryByTestId("connector-retry-slack")).toBeNull();
    expect(within(c).queryByTestId("connector-chat-hint-slack")).toBeNull();
    expect(within(c).getByTestId("connector-switch-slack").getAttribute("aria-checked")).toBe("false");
    expect(c.getAttribute("data-switched-off")).toBe("1");
  });

  it("pressing the switch PATCHes the pack by the card's id, and says what happened", async () => {
    api.connectors = [connector("slack", "Slack")];
    api.switchAnswer = (name, on) => ({ name, enabled: on, reloaded: false, tools_unloaded: 3 });
    render(<DirectoryPage />);
    const sw = await screen.findByTestId("connector-switch-slack");
    await act(async () => {
      fireEvent.click(sw);
    });
    expect(api.patches).toEqual([{ path: "/mcp/servers/slack", body: { enabled: false } }]);
    await screen.findByText(/"Slack" is off\./);
    await waitFor(() =>
      expect(screen.getByTestId("connector-switch-slack").getAttribute("aria-checked")).toBe("false"),
    );
  });

  it("ship fix: a switched-off card offers no Sign in; switched on it does", async () => {
    const signin = { cli: "microsoft-365-signin-work", label: "Sign in to Microsoft" };
    api.connectors = [
      connector("microsoft_365", "Microsoft 365", { enabled: false, status: "no_tools", tools_loaded: 0, signin }),
    ];
    render(<DirectoryPage />);
    const c = await screen.findByTestId("connector-card-microsoft_365");
    expect(within(c).getByTestId("connector-off-microsoft_365").textContent).toBe("Off");
    expect(within(c).queryByTestId("connector-signin-press-microsoft_365")).toBeNull();
    cleanup();
    __resetApiCache();
    api.connectors = [connector("microsoft_365", "Microsoft 365", { signin })];
    render(<DirectoryPage />);
    expect(await screen.findByTestId("connector-signin-press-microsoft_365")).toBeTruthy();
  });

  it("removal says Remove, not Disconnect", async () => {
    api.connectors = [connector("slack", "Slack")];
    render(<DirectoryPage />);
    const c = await screen.findByTestId("connector-card-slack");
    expect(within(c).getByRole("button", { name: /Remove/ })).toBeTruthy();
    expect(within(c).queryByRole("button", { name: /Disconnect/ })).toBeNull();
  });

  it("a card that does not run as a pack (a sign-in app) has no switch", async () => {
    api.connectors = [connector("drive", "Google Drive", { connect_via: "oauth", fields: [], account: "me@example.com" })];
    render(<DirectoryPage />);
    await screen.findByTestId("connector-card-drive");
    expect(screen.queryByTestId("connector-switch-drive")).toBeNull();
  });
});

/* -------------------------------------------------------- G74 Enable ------ */

describe("the Enable dialog labels fields in words, the raw key in a title", () => {
  it("humanFieldLabel", () => {
    expect(words.humanFieldLabel("<folder-path>")).toBe("Folder");
    expect(words.humanFieldLabel("<repo-path>")).toBe("Repository folder");
    expect(words.humanFieldLabel("<folder>")).toBe("Folder");
    expect(words.humanFieldLabel("GITHUB_PERSONAL_ACCESS_TOKEN")).toBe("GitHub personal access token");
    expect(words.humanFieldLabel("BOX_CLIENT_ID")).toBe("Box client ID");
    expect(words.humanFieldLabel("OPENAI_API_KEY")).toBe("OpenAI API key");
    expect(words.humanFieldLabel("")).toBe("");
  });

  it("the Tools catalog's Add opens the dialog with worded labels and the key hint in plain words", async () => {
    api.servers = [];
    api.catalog = [
      {
        id: "filesystem",
        name: "Files & folders",
        description: "Read and write files in one folder.",
        command: "npx",
        args: ["-y", "@modelcontextprotocol/server-filesystem", "<folder-path>"],
        env_keys: ["GITHUB_PERSONAL_ACCESS_TOKEN"],
        category: "reference",
        needs: "Node",
        runtime_ready: true,
      },
    ];
    render(<ToolsPage />);
    fireEvent.click(await screen.findByTestId("pack-add-filesystem"));
    const d = await screen.findByTestId("enable-dialog");
    const folder = within(d).getByText("Folder");
    expect(folder.closest("[title]")?.getAttribute("title")).toBe("<folder-path>");
    const token = within(d).getByText("GitHub personal access token");
    expect(token.closest("[title]")?.getAttribute("title")).toBe("GITHUB_PERSONAL_ACCESS_TOKEN");
    expect(d.textContent).toContain(words.KEYS_KEPT_HINT);
    expect(engineWordsIn(visibleText(d))).toEqual([]);
    expect(visibleText(d)).not.toMatch(/<folder-path>|GITHUB_PERSONAL_ACCESS_TOKEN/);
  });

  it("the dialog component shows a field's title", () => {
    render(
      <EnableDialog
        plan={{
          kind: "extension",
          title: "X",
          id: "x",
          summary: "s",
          caps: [],
          fields: [{ key: "env:A_B", label: "A b", title: "A_B", kind: "text" }],
        }}
        busy={false}
        onCancel={() => {}}
        onEnable={() => {}}
      />,
    );
    expect(screen.getByText("A b").getAttribute("title")).toBe("A_B");
  });
});

/* ----------------------------------------------------------- FilterBar ---- */

describe("the filter says what it does", () => {
  it("'Works without installing anything', never 'No runtime needed'", () => {
    render(<FilterBar value={EMPTY_FILTERS} onChange={() => {}} counts={{ builtin: 1, extension: 1 }} />);
    expect(screen.getByTestId("filter-ready").textContent).toContain("Works without installing anything");
    expect(document.body.textContent).not.toMatch(/runtime/i);
  });
});

/* -------------------------------------------- the acceptance sweep -------- */

describe("ACCEPTANCE: add, edit and remove an extension without reading MCP, stdio, SSE, env, runtime or Docker", () => {
  it("the Tools page (a working row, an off row, a broken row, an open editor)", async () => {
    api.servers = [SLACK, { ...SLACK_OFF, name: "notes" }, BROKEN, row("remote", { transport: "sse", url: "https://h/sse", command: "" })];
    api.catalog = [
      { id: "slack", name: "Slack", description: "Read and post messages.", command: "npx", args: [], category: "integration" },
      { id: "github", name: "GitHub", description: "Issues and pull requests.", command: "", args: [], transport: "http", url: "https://api.githubcopilot.com/mcp/", env_keys: ["GITHUB_PERSONAL_ACCESS_TOKEN"], auth_field: "GITHUB_PERSONAL_ACCESS_TOKEN", category: "reference" },
    ];
    render(<ToolsPage />);
    await screen.findByTestId("mcp-state-brave");
    // Open the editor on the stdio row and on the address row: the forms a
    // person edits with.
    fireEvent.click(screen.getByTestId("mcp-edit-slack"));
    await screen.findByTestId("edit-pack-slack");
    const found = engineWordsIn(visibleText(document.body));
    expect(found).toEqual([]);
    cleanup();
    render(<ToolsPage />);
    fireEvent.click(await screen.findByTestId("mcp-edit-remote"));
    await screen.findByTestId("edit-pack-remote");
    expect(engineWordsIn(visibleText(document.body))).toEqual([]);
  });

  it("adding by address, its More options opened, says how it connects in words (review)", async () => {
    api.servers = [];
    render(<ToolsPage />);
    const area = await screen.findByTestId("add-pack");
    fireEvent.click(within(area).getByTestId("add-pack-door-http"));
    const door = await screen.findByTestId("add-pack-http-door");
    const more = within(door).getByTestId("add-pack-http-more") as HTMLDetailsElement;
    more.open = true;
    const select = within(door).getByTestId("add-pack-http-transport") as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual([
      "Automatic (read from the address)",
      "A web address",
      "A web address (older servers)",
    ]);
    expect(select.getAttribute("aria-label")).toBe("How it connects");
    expect(engineWordsIn(visibleText(door))).toEqual([]);
  });

  it("every fold on the Tools page OPENED still says how an address row connects in words (fix round)", async () => {
    api.servers = [
      SLACK,
      row("remote", { transport: "http", url: "https://host/mcp", command: "", args: [] }),
      row("old", { transport: "sse", url: "https://h/sse", command: "", args: [] }),
    ];
    render(<ToolsPage />);
    await screen.findByTestId("mcp-launch-old");
    // A person who opens every fold: the address folds read the address, an
    // older server says so, and the protocol name is only in the tooltip.
    for (const d of Array.from(document.querySelectorAll("details"))) (d as HTMLDetailsElement).open = true;
    const remote = screen.getByTestId("mcp-launch-remote");
    expect(remote.textContent).toBe("https://host/mcp");
    expect(remote.getAttribute("title")).toBe("Streamable HTTP https://host/mcp");
    const old = screen.getByTestId("mcp-launch-old");
    expect(old.textContent).toBe("https://h/sse (older servers)");
    expect(old.getAttribute("title")).toBe("SSE https://h/sse");
    expect(engineWordsIn(visibleText(document.body))).toEqual([]);
  });

  it("a pasted older-server row, its Details opened by the value it asks for, says how it connects in words (fix round)", async () => {
    api.servers = [];
    api.preview = {
      servers: [
        {
          name: "old",
          transport: "sse",
          command: null,
          args: [],
          url: "https://h/sse",
          header_names: ["X-Api-Key"],
          env_keys: [],
          secret_keys: [],
          asks: ["X-Api-Key"],
          exists: false,
          runtime_ready: true,
          launcher_note: null,
          source: "paste",
          origin: "paste",
        },
      ],
      unrecognised: false,
    };
    render(<ToolsPage />);
    const area = await screen.findByTestId("add-pack");
    fireEvent.click(within(area).getByTestId("add-pack-door-paste"));
    const box = await within(area).findByTestId("add-pack-paste");
    fireEvent.change(box, { target: { value: "npx mcp-remote https://h/sse" } });
    fireEvent.blur(box);
    const r = await screen.findByTestId("import-row-old");
    // The fold opens by itself on a row that needs a typed value.
    expect((within(r).getByTestId("import-details-old") as HTMLDetailsElement).open).toBe(true);
    const chip = within(r).getByTestId("import-launcher-old");
    expect(chip.textContent).toBe("A web address (older servers)");
    expect(chip.getAttribute("title")).toBe("SSE");
    expect(engineWordsIn(visibleText(r))).toEqual([]);
  });

  it("webAddressWords / transportTitle / addressLine", () => {
    expect(words.webAddressWords("http")).toBe("A web address");
    expect(words.webAddressWords("sse")).toBe("A web address (older servers)");
    expect(words.transportTitle("http")).toBe("Streamable HTTP");
    expect(words.transportTitle("sse")).toBe("SSE");
    expect(words.addressLine("http", " https://a/mcp ")).toEqual({
      text: "https://a/mcp",
      title: "Streamable HTTP https://a/mcp",
    });
    expect(words.addressLine("sse", "https://a/sse")).toEqual({
      text: "https://a/sse (older servers)",
      title: "SSE https://a/sse",
    });
    expect(words.addressLine("http", null)).toEqual({ text: "", title: "Streamable HTTP" });
  });

  it("the Directory (a connected app, a switched-off app)", async () => {
    api.connectors = [
      connector("slack", "Slack"),
      connector("notes", "Notes", { enabled: false }),
      connector("brave", "Brave Search", { status: "failed", tools_loaded: 0, reason: "It needs a key." }),
    ];
    render(<DirectoryPage />);
    await screen.findByTestId("connector-card-notes");
    expect(engineWordsIn(visibleText(document.body))).toEqual([]);
  });

  it("the sweep is not vacuous: it catches the old words", () => {
    const el = document.createElement("div");
    el.innerHTML = "<p>2 env</p> <p>No runtime needed</p> <p>Streamable HTTP / SSE</p>";
    expect(engineWordsIn(visibleText(el))).toEqual(["SSE", "env", "runtime"]);
    const folded = document.createElement("div");
    folded.innerHTML = "<details><summary>Show command</summary><code>MCP stdio</code></details>";
    expect(engineWordsIn(visibleText(folded))).toEqual([]);
  });
});
