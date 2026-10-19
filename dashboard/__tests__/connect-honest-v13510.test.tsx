/**
 * v1.351.0, wave 1 lane D (G08): connect and add doors look green only when
 * the pack works.
 *
 * - Directory: a connector whose pack did not start (status "failed") or
 *   loaded nothing ("no_tools") kept the green "Connected" badge and ring
 *   ABOVE its amber "Didn't start". Its badge, ring and glyph now read the
 *   backend's `status`: green "Connected" only for "connected", an amber
 *   "Needs attention" otherwise, with the reason and Retry beside it. A
 *   working pack says where it is used: "Turn it on in a chat with + ›
 *   Connections" (it was only a tooltip).
 * - Tools add doors (paste, HTTP server, the npm/catalog add) announced a
 *   pack with ZERO tools in the green success box. They now say "Added, but
 *   it didn't start: <why>. Open its row below to retry." in the warn tone,
 *   with the warning triangle, never the green check.
 *
 * Driven through the REAL pages; only lib/api is a fake (the house pattern).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    posts: [] as { path: string; body: unknown }[],
    servers: [] as Record<string, unknown>[],
    connectors: [] as Record<string, unknown>[],
    connect: { ok: true, connector: "", tools_loaded: 0, note: null } as Record<string, unknown>,
    preview: { servers: [] as Record<string, unknown>[], unrecognised: false } as Record<string, unknown>,
    apply: { added: [], skipped: [] } as Record<string, unknown>,
    addResult: { name: "", added: true, tools_loaded: 0, auto_approve: false, note: null } as Record<string, unknown>,
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
    if (path === "/mcp/servers") return { servers: api.servers };
    if (path === "/mcp/catalog") return { catalog: [] };
    if (path === "/tools/custom") return { tools: [] };
    if (path === "/mcp/import/sources") return { sources: [] };
    if (path === "/connectors") return { connectors: api.connectors, categories: ["Communication"] };
    return {};
  },
  post: async (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    if (path.endsWith("/connect")) return api.connect;
    if (path === "/mcp/import/preview") return api.preview;
    if (path === "/mcp/import/apply") return api.apply;
    if (path === "/mcp/servers") return { ...api.addResult, name: (body as { name: string }).name };
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

const ToolsPage = (await import("@/components/settings/pages/ToolsPage")).default;
const DirectoryPage = (await import("@/components/settings/pages/DirectoryPage")).default;
const { addedButNotStarted, packStartReason, packDidNotStart } = await import("@/lib/packStartWords");

/** The daemon's own add note for a pack whose launcher is missing. */
const NO_NODE_NOTE =
  "saved, but it did not start: npx isn't installed, or isn't on the PATH this app can see — see its row for the fix";
const NO_NODE_WORDS =
  "Added, but it didn't start: npx isn't installed, or isn't on the PATH this app can see. Open its row below to retry.";

function slack(over: Record<string, unknown> = {}) {
  return {
    id: "slack",
    name: "Slack",
    category: "Communication",
    glyph: "",
    blurb: "Read channels and post messages.",
    unlocks: "Search your Slack workspace.",
    connect_via: "mcp",
    scopes: [],
    docs_url: "",
    fields: [
      { name: "SLACK_BOT_TOKEN", label: "Slack bot token", help: "", kind: "secret", optional: false },
    ],
    provider: "",
    connected: false,
    status: "disconnected",
    tools_loaded: 0,
    tool_names: [],
    ...over,
  };
}

/** What GET /connectors says for Slack on a PC without Node. */
const SLACK_FAILED = slack({
  connected: true,
  status: "failed",
  detail: "FileNotFoundError: launcher 'npx' was not found",
  reason: "npx isn't installed, or isn't on the PATH this app can see",
  fix: "Install Node.js LTS from nodejs.org, then press Retry.",
  stderr_tail: "",
});

const card = (id: string) => screen.getByTestId(`connector-card-${id}`);

beforeEach(() => {
  localStorage.clear();
  api.posts = [];
  api.servers = [];
  api.connectors = [];
  api.connect = { ok: true, connector: "", tools_loaded: 0, note: null };
  api.preview = { servers: [], unrecognised: false };
  api.apply = { added: [], skipped: [] };
  api.addResult = { name: "", added: true, tools_loaded: 0, auto_approve: false, note: null };
});
afterEach(() => cleanup());

/* ------------------------------------------------------------ the words --- */

describe("the add-door words (lib/packStartWords)", () => {
  it("take the daemon's reason out of its wrapper, once", () => {
    expect(packStartReason(NO_NODE_NOTE)).toBe("npx isn't installed, or isn't on the PATH this app can see");
    expect(packStartReason("saved — it connected but advertised no tools; see its row")).toBe(
      "it connected but advertised no tools",
    );
    expect(packStartReason(null)).toBe("");
    expect(addedButNotStarted(NO_NODE_NOTE)).toBe(NO_NODE_WORDS);
    expect(addedButNotStarted(null)).toBe("Added, but it didn't start. Open its row below to retry.");
    expect(addedButNotStarted("it did not answer", '"api"')).toBe(
      '"api" added, but it didn\'t start: it did not answer. Open its row below to retry.',
    );
  });

  it("zero (or no) tools is 'did not start'; any tool is working", () => {
    expect(packDidNotStart({ tools_loaded: 0 })).toBe(true);
    expect(packDidNotStart({ tools_loaded: null })).toBe(true);
    expect(packDidNotStart({})).toBe(true);
    expect(packDidNotStart({ tools_loaded: 1 })).toBe(false);
  });
});

/* ------------------------------------------------------------ Directory --- */

describe("Directory: green only when the pack works", () => {
  it("Slack that did not start: an amber 'Needs attention' card with the reason and Retry, never 'Connected'", async () => {
    api.connectors = [SLACK_FAILED];
    render(<DirectoryPage />);
    const badge = await screen.findByTestId("connector-attention-slack");
    expect(badge.textContent).toBe("Needs attention");
    expect(badge.className).toContain("text-tone-warn");
    const c = card("slack");
    expect(c.getAttribute("data-health")).toBe("attention");
    expect(c.className).toContain("ring-tone-warn/30");
    expect(c.className).not.toContain("emerald");
    expect(within(c).queryByText("Connected")).toBeNull();
    // The reason and Retry, right on the card.
    const line = within(c).getByTestId("connector-status-slack");
    expect(line.textContent).toContain("npx isn't installed, or isn't on the PATH this app can see");
    expect(within(c).getByTestId("connector-retry-slack").textContent).toBe("Retry");
    expect(within(c).getByTestId("connector-fix-slack").textContent).toContain("Install Node.js LTS");
    // No "use it in a chat" hint for a pack that does not work.
    expect(within(c).queryByTestId("connector-chat-hint-slack")).toBeNull();
  });

  it("a saved pack that loaded nothing (no_tools) also needs attention", async () => {
    api.connectors = [
      slack({
        connected: true,
        status: "no_tools",
        detail: "Saved, but it isn't answering yet. Press Test, then Retry.",
      }),
    ];
    render(<DirectoryPage />);
    expect((await screen.findByTestId("connector-attention-slack")).textContent).toBe("Needs attention");
    expect(within(card("slack")).queryByText("Connected")).toBeNull();
    expect(within(card("slack")).getByTestId("connector-status-slack").textContent).toContain(
      "Saved, but it isn't answering yet. Press Test, then Retry.",
    );
  });

  it("control: a working pack is green 'Connected' and says where to turn it on", async () => {
    api.connectors = [slack({ connected: true, status: "connected", tools_loaded: 8 })];
    render(<DirectoryPage />);
    const hint = await screen.findByTestId("connector-chat-hint-slack");
    expect(hint.textContent).toBe("Turn it on in a chat with + › Connections");
    expect(hint.className).toContain("text-zinc-500");
    const c = card("slack");
    expect(c.getAttribute("data-health")).toBe("connected");
    expect(within(c).getByText("Connected")).toBeTruthy();
    expect(within(c).queryByTestId("connector-attention-slack")).toBeNull();
    expect(c.className).not.toContain("ring-tone-warn");
  });

  it("control: a pack that is not connected wears neither badge", async () => {
    api.connectors = [slack()];
    render(<DirectoryPage />);
    const c = await screen.findByTestId("connector-card-slack");
    expect(c.getAttribute("data-health")).toBe("off");
    expect(within(c).queryByTestId("connector-attention-slack")).toBeNull();
    expect(within(c).queryByText("Connected")).toBeNull();
  });

  it("connecting a pack that does not start says Retry, never restart", async () => {
    api.connectors = [slack()];
    api.connect = { ok: true, connector: "slack", tools_loaded: 0, note: null };
    render(<DirectoryPage />);
    const c = await screen.findByTestId("connector-card-slack");
    fireEvent.click(within(c).getByRole("button", { name: /Connect/ }));
    fireEvent.change(within(c).getByLabelText("Slack bot token"), { target: { value: "xoxb-1" } });
    fireEvent.click(within(c).getAllByRole("button", { name: /Connect/ }).find((b) => b.getAttribute("type") === "submit")!);
    const note = await within(c).findByText("Saved, but it didn't start. Press Retry.");
    expect(note).toBeTruthy();
    expect(c.textContent ?? "").not.toMatch(/restart/i);
  });
});

/* --------------------------------------------------------- Tools doors --- */

async function openTools() {
  render(<ToolsPage />);
  return await screen.findByTestId("add-pack");
}

/** v1.354.0 (G49, updated on purpose): the paste box is the advanced door,
 *  no longer the one the area opens on. */
async function openToolsPaste() {
  const area = await openTools();
  fireEvent.click(within(area).getByTestId("add-pack-door-paste"));
  await within(area).findByTestId("add-pack-paste");
  return area;
}

describe("Tools add doors: green only when the pack works", () => {
  it("paste: a pack that loaded no tools reads 'Added, but it didn't start' under a warning triangle", async () => {
    api.preview = {
      servers: [
        {
          name: "slack",
          transport: "stdio",
          command: "npx",
          args: ["-y", "@modelcontextprotocol/server-slack"],
          url: null,
          header_names: [],
          env_keys: [],
          secret_keys: [],
          exists: false,
          runtime_ready: false,
          launcher_note: null,
          source: "paste",
          origin: "paste",
        },
      ],
      unrecognised: false,
    };
    api.apply = { added: [{ name: "slack", tools_loaded: 0, note: NO_NODE_NOTE, state: "failed" }], skipped: [] };
    const area = await openToolsPaste();
    const box = within(area).getByTestId("add-pack-paste");
    fireEvent.change(box, { target: { value: "npx -y @modelcontextprotocol/server-slack" } });
    fireEvent.blur(box);
    await screen.findByTestId("import-row-slack");
    fireEvent.click(screen.getByTestId("add-pack-apply"));
    const result = await screen.findByTestId("import-result-slack");
    expect(result.getAttribute("data-outcome")).toBe("not-started");
    expect(result.textContent).toContain(NO_NODE_WORDS);
    expect(within(result).getByTestId("import-warn-slack").getAttribute("class")).toContain("text-tone-warn");
    expect(result.querySelector(".text-tone-success")).toBeNull();
  });

  it("paste control: a pack with tools keeps its green check and count", async () => {
    api.preview = {
      servers: [
        {
          name: "time",
          transport: "stdio",
          command: "uvx",
          args: ["mcp-server-time"],
          url: null,
          header_names: [],
          env_keys: [],
          secret_keys: [],
          exists: false,
          runtime_ready: true,
          launcher_note: null,
          source: "paste",
          origin: "paste",
        },
      ],
      unrecognised: false,
    };
    api.apply = { added: [{ name: "time", tools_loaded: 2, note: null, state: "ok" }], skipped: [] };
    const area = await openToolsPaste();
    const box = within(area).getByTestId("add-pack-paste");
    fireEvent.change(box, { target: { value: "uvx mcp-server-time" } });
    fireEvent.blur(box);
    await screen.findByTestId("import-row-time");
    fireEvent.click(screen.getByTestId("add-pack-apply"));
    const result = await screen.findByTestId("import-result-time");
    expect(result.getAttribute("data-outcome")).toBe("ok");
    expect(result.textContent).toContain("2 tools ready");
    expect(result.querySelector(".text-tone-success")).toBeTruthy();
    expect(screen.queryByTestId("import-warn-time")).toBeNull();
  });

  it("HTTP server: zero tools is the warn note, not the success box", async () => {
    api.addResult = { name: "", added: true, tools_loaded: 0, auto_approve: false, note: NO_NODE_NOTE };
    const area = await openTools();
    fireEvent.click(within(area).getByTestId("add-pack-door-http"));
    const door = await screen.findByTestId("add-pack-http-door");
    fireEvent.change(within(door).getByTestId("add-pack-url"), { target: { value: "https://slack.example.com/mcp" } });
    fireEvent.click(within(door).getByTestId("add-pack-http-add"));
    const warn = await within(door).findByTestId("add-pack-http-warn");
    expect(warn.textContent).toContain("added, but it didn't start: npx isn't installed");
    expect(warn.textContent).toContain("Open its row below to retry.");
    expect(warn.querySelector('[data-tone="warn"]')).toBeTruthy();
    expect(warn.querySelector(".text-tone-success")).toBeNull();
    expect(within(door).queryByTestId("add-pack-http-ok")).toBeNull();
  });

  it("the shared add (npm door, catalog): 'Added, but it didn't start' in the warn tone", async () => {
    api.addResult = { name: "", added: true, tools_loaded: 0, auto_approve: false, note: NO_NODE_NOTE };
    await openTools();
    fireEvent.change(screen.getByTestId("npm-door-input"), {
      target: { value: "@modelcontextprotocol/server-slack" },
    });
    fireEvent.click(await screen.findByText("Connect this pack"));
    const warn = await screen.findByTestId("mcp-add-warn");
    expect(warn.textContent).toContain('Extension "server-slack" added, but it didn\'t start');
    expect(warn.textContent).toContain("npx isn't installed, or isn't on the PATH this app can see");
    expect(warn.textContent).toContain("Open its row below to retry.");
    expect(warn.querySelector('[data-tone="warn"]')).toBeTruthy();
    expect(screen.queryByText(/connected — /)).toBeNull();
  });

  it("the shared add control: a pack with tools keeps its green words", async () => {
    api.addResult = { name: "", added: true, tools_loaded: 3, auto_approve: false, note: null };
    await openTools();
    fireEvent.change(screen.getByTestId("npm-door-input"), {
      target: { value: "@modelcontextprotocol/server-slack" },
    });
    fireEvent.click(await screen.findByText("Connect this pack"));
    await waitFor(() =>
      expect(screen.getByText('Extension "server-slack" connected — 3 tools ready to use.')).toBeTruthy(),
    );
    expect(screen.queryByTestId("mcp-add-warn")).toBeNull();
  });
});
