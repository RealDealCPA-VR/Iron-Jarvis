/**
 * v1.342.0 ("the pack says why"), lane F: the Tools page reads a pack's LOAD
 * RECORD and says it in words.
 *
 * GET /mcp/servers rows carry `state` (starting | ok | failed | failing),
 * `failures`, `reason`, `fix`, `stderr_tail` (masked by the daemon), and
 * the env KEY names only (`env_keys`, `secret_keys`). Pinned here, by what
 * the user sees:
 *  - the state in words: "Starting", "N tools", "Didn't start",
 *    "Failing: N calls" (the calm Badge);
 *  - the amber line = the reason, the fix line, and a folded "Last output"
 *    (monospace, wrapped, its own scroll) only when the tail is non-empty;
 *  - Retry shows for a failed or failing pack AND for a pack holding zero
 *    tools, never for a loaded one or one still starting;
 *  - the catalog card never reads "Enabled" for a pack whose state is failed;
 *  - the env display names KEYS (no value reaches the page);
 *  - the KEY=value box says tokens go to the vault;
 *  - Delete no longer asks for a restart: "Removed";
 *  - a failed Test renders error + reason + fix + last output.
 *
 * Harness mirrors mcp-last-error-v1229 (the page's data seams are mocked).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ASIDE, readSrc } from "./helpers/dashGuard";

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
    dels: [] as string[],
    servers: [] as Record<string, unknown>[],
    catalog: [] as Record<string, unknown>[],
    connectors: [] as Record<string, unknown>[],
    test: {} as Record<string, unknown>,
    reload: { ok: false, tools_loaded: 0, last_error: null as string | null },
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
    if (path === "/mcp/servers") return { servers: api.servers };
    if (path === "/mcp/catalog") return { catalog: api.catalog };
    if (path === "/tools/custom") return { tools: [] };
    if (path === "/connectors") return { connectors: api.connectors, categories: ["Developer"] };
    return {};
  },
  post: async (path: string) => {
    api.posts.push(path);
    if (path.endsWith("/test")) return api.test;
    if (path.endsWith("/reload")) return api.reload;
    return {};
  },
  put: async () => ({}),
  patch: async () => ({}),
  del: async (path: string) => {
    api.dels.push(path);
    api.servers = api.servers.filter((s) => !path.endsWith(`/${s.name}`));
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

const ToolsPage = (await import("@/components/settings/pages/ToolsPage")).default;
const DirectoryPage = (await import("@/components/settings/pages/DirectoryPage")).default;

const TAIL = "npm ERR! 404 Not Found\nnpm ERR! @brave/brave-search-mcp-server@latest\nError: BRAVE_API_KEY is not set";

function row(name: string, over: Record<string, unknown> = {}) {
  return {
    name,
    command: "npx",
    args: ["-y", `@example/${name}`],
    tools_loaded: 0,
    tool_names: [],
    last_error: null,
    state: "ok",
    failures: 0,
    reason: null,
    fix: null,
    stderr_tail: "",
    env_keys: [],
    secret_keys: [],
    ...over,
  };
}

const STARTING = row("slow", { state: "starting" });
const HEALTHY = row("filesystem", { state: "ok", tools_loaded: 3, tool_names: ["a", "b", "c"] });
const EMPTY_OK = row("quiet", { state: "ok", tools_loaded: 0 });
const FAILED = row("brave", {
  state: "failed",
  last_error: "MCPError: the server closed the connection (exit code 1)",
  reason: "It needs BRAVE_API_KEY and none was given",
  fix: "Add BRAVE_API_KEY in Settings > Tools, then Retry",
  stderr_tail: TAIL,
});
const FAILING = row("flaky", {
  state: "failing",
  tools_loaded: 4,
  tool_names: ["w", "x", "y", "z"],
  failures: 2,
  last_error: "MCPServerStopped: pack 'flaky': the MCP server closed the connection (exit code 137)",
  reason: "Its calls fail: the server stops when called",
  fix: "",
  stderr_tail: "Killed",
});

const rowOf = (name: string) =>
  screen.getByTestId(`mcp-state-${name}`).closest(".rounded-xl") as HTMLElement;

beforeEach(() => {
  localStorage.clear();
  api.gets.length = 0;
  api.posts.length = 0;
  api.dels.length = 0;
  api.servers = [STARTING, HEALTHY, EMPTY_OK, FAILED, FAILING];
  api.catalog = [];
  api.connectors = [];
  api.test = {};
  api.reload = { ok: false, tools_loaded: 0, last_error: null };
});
afterEach(() => cleanup());

/* ------------------------------------------------------------ Directory --- */

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
    connected: true,
    status: "connected",
    tools_loaded: 0,
    tool_names: [],
    ...over,
  };
}

describe("Directory (F2): a connected connector card says the backend's status", () => {
  it("no_tools: the words, the backend's detail, the fix, and a Retry that reloads the pack", async () => {
    api.connectors = [
      connector("notion", "Notion", {
        status: "no_tools",
        detail: "connected, but 0 tools are loaded",
        fix: "Install Node, then Retry",
      }),
    ];
    api.reload = { ok: true, tools_loaded: 2, last_error: null };
    render(<DirectoryPage />);
    const line = await screen.findByTestId("connector-status-notion");
    expect(line.textContent).toContain("No tools loaded: connected, but 0 tools are loaded");
    expect(screen.getByTestId("connector-fix-notion").textContent).toBe("Install Node, then Retry");
    expect(screen.queryByText("0 tools loaded")).toBeNull();
    fireEvent.click(screen.getByTestId("connector-retry-notion"));
    await screen.findByText("Connected, 2 tools ready to use.");
    expect(api.posts).toContain("/mcp/servers/notion/reload");
  });

  it("failed: Didn't start with the reason, the last output folded, and a Retry that reports a second failure", async () => {
    api.connectors = [
      connector("github", "GitHub", {
        status: "failed",
        detail: "GITHUB_TOKEN is missing",
        reason: "It needs GITHUB_TOKEN and none was given",
        fix: "Add GITHUB_TOKEN, then Retry",
        stderr_tail: "Error: GITHUB_TOKEN is not set",
      }),
    ];
    api.reload = {
      ok: false,
      tools_loaded: 0,
      last_error: "exit code 1",
      reason: "It still needs GITHUB_TOKEN",
      fix: "Add GITHUB_TOKEN",
      stderr_tail: "Error: GITHUB_TOKEN is not set",
    } as typeof api.reload;
    render(<DirectoryPage />);
    const line = await screen.findByTestId("connector-status-github");
    expect(line.textContent).toContain("Didn’t start: It needs GITHUB_TOKEN and none was given");
    const fold = screen.getByTestId("connector-stderr-github");
    expect(fold.tagName).toBe("DETAILS");
    expect(fold.querySelector("pre")?.textContent).toBe("Error: GITHUB_TOKEN is not set");
    fireEvent.click(screen.getByTestId("connector-retry-github"));
    const fail = await screen.findByTestId("connector-test-fail-github");
    expect(fail.textContent).toContain("exit code 1");
    expect(fail.textContent).toContain("It still needs GITHUB_TOKEN");
    expect(fail.textContent).toContain("Add GITHUB_TOKEN");
  });

  it("connected with tools: the plain count, no amber line, no Retry", async () => {
    api.connectors = [connector("notion", "Notion", { tools_loaded: 3, tool_names: ["a", "b", "c"] })];
    render(<DirectoryPage />);
    expect(await screen.findByText("3 tools loaded")).toBeTruthy();
    expect(screen.queryByTestId("connector-status-notion")).toBeNull();
    expect(screen.queryByTestId("connector-retry-notion")).toBeNull();
  });

  it("a failed Test on a connector renders error, reason, fix and last output", async () => {
    api.connectors = [connector("notion", "Notion", { status: "no_tools", detail: "0 tools" })];
    api.test = {
      ok: false,
      count: 0,
      tools: [],
      error: "MCPServerStopped: pack 'notion': the MCP server closed the connection (exit code 1)",
      reason: "It needs NOTION_TOKEN and none was given",
      fix: "Add NOTION_TOKEN",
      stderr_tail: "Error: NOTION_TOKEN is not set",
    };
    render(<DirectoryPage />);
    await screen.findByTestId("connector-status-notion");
    fireEvent.click(screen.getByRole("button", { name: /^Test/ }));
    const fail = await screen.findByTestId("connector-test-fail-notion");
    expect(api.posts).toContain("/connectors/notion/test");
    expect(fail.textContent).toContain("MCPServerStopped: pack 'notion'");
    expect(fail.textContent).toContain("It needs NOTION_TOKEN and none was given");
    expect(fail.textContent).toContain("Add NOTION_TOKEN");
    expect(screen.getByTestId("connector-test-stderr-notion").querySelector("pre")?.textContent).toBe(
      "Error: NOTION_TOKEN is not set",
    );
  });
});

describe("a pack row says its state in words", () => {
  it("Starting / N tools / Didn't start / Failing: N calls, on the calm Badge", async () => {
    render(<ToolsPage />);
    const starting = await screen.findByTestId("mcp-state-slow");
    expect(starting.textContent).toContain("Starting");
    expect(starting.getAttribute("data-badge-variant")).toBe("calm");
    expect(screen.getByTestId("mcp-state-filesystem").textContent).toContain("3 tools");
    expect(screen.getByTestId("mcp-state-quiet").textContent).toContain("0 tools");
    expect(screen.getByTestId("mcp-state-brave").textContent).toMatch(/Didn.t start/);
    expect(screen.getByTestId("mcp-state-flaky").textContent).toContain("Failing: 2 calls");
    // The tone rides the badge's token, never only the words' colour.
    expect(screen.getByTestId("mcp-state-brave").getAttribute("data-tone")).toBe("amber");
    expect(screen.getByTestId("mcp-state-flaky").getAttribute("data-tone")).toBe("amber");
    expect(screen.getByTestId("mcp-state-filesystem").getAttribute("data-tone")).toBe("green");
  });

  it("an older daemon with no `state`: an error reads as failed, a loaded pack as ok", async () => {
    api.servers = [
      row("old-bad", { state: undefined, last_error: "boom", tools_loaded: 0 }),
      row("old-good", { state: undefined, tools_loaded: 2, tool_names: ["p", "q"] }),
    ];
    render(<ToolsPage />);
    expect((await screen.findByTestId("mcp-state-old-bad")).textContent).toMatch(/Didn.t start/);
    expect(screen.getByTestId("mcp-state-old-good").textContent).toContain("2 tools");
    expect(within(rowOf("old-good")).queryByRole("button", { name: /Retry/ })).toBeNull();
  });
});

describe("the amber line: reason, fix, last output", () => {
  it("the reason leads, the fix follows, and the last output is folded, monospace and wrapped", async () => {
    render(<ToolsPage />);
    const line = await screen.findByTestId("mcp-last-error-brave");
    expect(line.textContent).toContain("Didn’t start: It needs BRAVE_API_KEY and none was given");
    expect(screen.getByTestId("mcp-fix-brave").textContent).toBe(
      "Add BRAVE_API_KEY in Settings > Tools, then Retry",
    );
    // The raw error stays for a bug report.
    expect(line.textContent).toContain("MCPError: the server closed the connection (exit code 1)");
    const fold = screen.getByTestId("mcp-stderr-brave");
    expect(fold.tagName).toBe("DETAILS");
    expect(fold.hasAttribute("open")).toBe(false);
    expect(within(fold).getByText("Last output")).toBeTruthy();
    const pre = fold.querySelector("pre") as HTMLElement;
    expect(pre.textContent).toBe(TAIL);
    expect(pre.className).toMatch(/\bfont-mono\b/);
    expect(pre.className).toMatch(/\bwhitespace-pre-wrap\b/);
    expect(pre.className).toMatch(/\boverflow-y-auto\b/);
    expect(pre.className).toMatch(/\bmax-h-/);
  });

  it("a failing pack says Failing with its reason and keeps its tools on the row", async () => {
    render(<ToolsPage />);
    const line = await screen.findByTestId("mcp-last-error-flaky");
    expect(line.textContent).toContain("Failing: Its calls fail: the server stops when called");
    expect(screen.getByTestId("mcp-stderr-flaky").querySelector("pre")?.textContent).toBe("Killed");
    expect(screen.queryByTestId("mcp-fix-flaky")).toBeNull();
    expect(within(rowOf("flaky")).getByText("w")).toBeTruthy();
  });

  it("no fold when the tail is empty, no amber line on a healthy row", async () => {
    render(<ToolsPage />);
    await screen.findByTestId("mcp-state-filesystem");
    expect(screen.queryByTestId("mcp-last-error-filesystem")).toBeNull();
    expect(screen.queryByTestId("mcp-stderr-filesystem")).toBeNull();
    expect(screen.queryByTestId("mcp-stderr-quiet")).toBeNull();
  });
});

describe("Retry shows for failed, failing and zero-tools packs", () => {
  it("failed and failing rows: Retry; a zero-tools ok row: Retry with a plain line", async () => {
    render(<ToolsPage />);
    await screen.findByTestId("mcp-state-brave");
    expect(within(rowOf("brave")).getAllByRole("button", { name: /Retry/ }).length).toBe(1);
    expect(within(rowOf("flaky")).getAllByRole("button", { name: /Retry/ }).length).toBe(1);
    const quiet = screen.getByTestId("mcp-no-tools-quiet");
    expect(quiet.textContent).toContain("No tools loaded.");
    expect(within(quiet).getByRole("button", { name: /Retry/ })).toBeTruthy();
  });

  it("a loaded row and a starting row offer no Retry", async () => {
    render(<ToolsPage />);
    await screen.findByTestId("mcp-state-filesystem");
    expect(within(rowOf("filesystem")).queryByRole("button", { name: /Retry/ })).toBeNull();
    expect(within(rowOf("slow")).queryByRole("button", { name: /Retry/ })).toBeNull();
    expect(screen.queryByTestId("mcp-no-tools-slow")).toBeNull();
  });

  it("Retry on a failing row posts the reload route", async () => {
    api.reload = { ok: true, tools_loaded: 4, last_error: null };
    render(<ToolsPage />);
    await screen.findByTestId("mcp-state-flaky");
    fireEvent.click(within(rowOf("flaky")).getByRole("button", { name: /Retry/ }));
    await waitFor(() => expect(api.posts).toContain("/mcp/servers/flaky/reload"));
    expect(await screen.findByText(/Connected — 4 tools available now/)).toBeTruthy();
  });

  it("a Retry that fails shows the reload's reason, fix and last output in the result slot", async () => {
    api.reload = {
      ok: false,
      tools_loaded: 0,
      last_error: "exit code 1",
      reason: "It needs BRAVE_API_KEY and none was given",
      fix: "Add the key, then Retry",
      stderr_tail: "Error: BRAVE_API_KEY is not set",
    } as typeof api.reload;
    render(<ToolsPage />);
    await screen.findByTestId("mcp-state-brave");
    fireEvent.click(within(rowOf("brave")).getByRole("button", { name: /Retry/ }));
    const fail = await screen.findByTestId("mcp-test-fail-brave");
    expect(fail.textContent).toContain("exit code 1");
    expect(fail.textContent).toContain("It needs BRAVE_API_KEY and none was given");
    expect(screen.getByTestId("mcp-test-fix-brave").textContent).toBe("Add the key, then Retry");
    expect(screen.getByTestId("mcp-test-stderr-brave").querySelector("pre")?.textContent).toBe(
      "Error: BRAVE_API_KEY is not set",
    );
  });
});

describe("the catalog card never says Enabled for a failed pack", () => {
  const entry = (id: string, name: string) => ({
    id,
    name,
    description: `${name} pack.`,
    command: "npx",
    args: ["-y", `@example/${id}`],
    category: "reference",
  });

  it("a failed pack's card wears the amber word; a loaded pack's card keeps Enabled", async () => {
    api.catalog = [entry("brave", "Brave search"), entry("filesystem", "Files & folders")];
    render(<ToolsPage />);
    await screen.findByTestId("packs-enabled");
    const brave = document.querySelector('[data-pack="brave"]') as HTMLElement;
    const fs = document.querySelector('[data-pack="filesystem"]') as HTMLElement;
    expect(brave).toBeTruthy();
    expect(within(brave).queryByTestId("status-added")).toBeNull();
    expect(within(brave).queryByText("Enabled")).toBeNull();
    const chip = within(brave).getByTestId("status-failed");
    expect(chip.textContent).toMatch(/Didn.t start/);
    expect(chip.getAttribute("data-tone")).toBe("amber");
    expect(chip.getAttribute("data-badge-variant")).toBe("calm");
    expect(brave.className).not.toMatch(/emerald/);
    expect(within(fs).getByTestId("status-added").textContent).toContain("Enabled");
  });

  it("a failing pack's card says Failing, not Enabled", async () => {
    api.catalog = [entry("flaky", "Flaky pack")];
    render(<ToolsPage />);
    await screen.findByTestId("packs-enabled");
    const card = document.querySelector('[data-pack="flaky"]') as HTMLElement;
    expect(within(card).queryByText("Enabled")).toBeNull();
    expect(within(card).getByTestId("status-failed").textContent).toContain("Failing");
  });
});

describe("env is shown by KEY name only", () => {
  it("counts env_keys and secret_keys, names the keys in the tooltip, and prints no value", async () => {
    api.servers = [
      row("keyed", {
        tools_loaded: 1,
        tool_names: ["t"],
        env_keys: ["BRAVE_API_KEY", "REGION"],
        secret_keys: ["BRAVE_API_KEY"],
        // An older daemon's value map, if one ever arrived, must not be printed.
        env: { BRAVE_API_KEY: "sk-live-SHOULD-NEVER-SHOW", REGION: "eu" },
      }),
    ];
    render(<ToolsPage />);
    const env = await screen.findByTestId("mcp-env-keys-keyed");
    // v1.356.0 (G74, changed on purpose): counted as "settings", never "env".
    expect(env.textContent).toContain("2 settings");
    expect(env.getAttribute("title")).toBe("BRAVE_API_KEY, REGION");
    const vault = screen.getByTestId("mcp-secret-keys-keyed");
    expect(vault.textContent).toContain("1 in vault");
    expect(vault.getAttribute("title")).toContain("BRAVE_API_KEY");
    expect(document.body.innerHTML).not.toContain("SHOULD-NEVER-SHOW");
  });

  it("an older daemon with only an `env` map still shows the count by its keys", async () => {
    api.servers = [row("older", { env_keys: undefined, secret_keys: undefined, env: { A: "1" } })];
    render(<ToolsPage />);
    expect((await screen.findByTestId("mcp-env-keys-older")).textContent).toContain("1 setting");
  });

  it("the KEY=value box says keys that look like tokens are kept in the vault", async () => {
    render(<ToolsPage />);
    await screen.findByTestId("mcp-state-filesystem");
    fireEvent.change(screen.getByPlaceholderText(/@scope\/package/), {
      target: { value: "@scope/some-mcp" },
    });
    const hint = await screen.findByTestId("mcp-env-vault-hint");
    // v1.356.0 (G74, changed on purpose): plain words, no "environment variable".
    expect(hint.textContent).toMatch(/token/i);
    expect(hint.textContent).toMatch(/kept encrypted on this PC/);
  });
});

// v1.356.0 (G74, changed on purpose): the row's button says Remove, the
// same word as the catalog card and the Directory.
describe("Remove says Removed, not restart", () => {
  it("two presses remove the pack; the note says Removed and never asks for a restart", async () => {
    render(<ToolsPage />);
    await screen.findByTestId("mcp-state-filesystem");
    const del = within(rowOf("filesystem")).getByRole("button", { name: /Remove/ });
    fireEvent.click(del);
    fireEvent.click(within(rowOf("filesystem")).getByRole("button", { name: /Confirm/ }));
    await waitFor(() => expect(api.dels).toContain("/mcp/servers/filesystem"));
    const note = await screen.findByText(/Removed "filesystem"/);
    expect(note.textContent).not.toMatch(/Restart Iron Jarvis/);
    expect(document.body.textContent).not.toMatch(/fully unload/);
  });
});

describe("a failed Test says the real load error", () => {
  it("renders error, reason, fix and the last output when ok is false", async () => {
    api.test = {
      ok: false,
      count: 0,
      tools: [],
      error: "MCPServerStopped: pack 'brave': the MCP server closed the connection (exit code 1)",
      reason: "It needs BRAVE_API_KEY and none was given",
      fix: "Add BRAVE_API_KEY in Settings > Tools",
      stderr_tail: TAIL,
    };
    render(<ToolsPage />);
    await screen.findByTestId("mcp-state-brave");
    fireEvent.click(within(rowOf("brave")).getByRole("button", { name: /^Test/ }));
    const fail = await screen.findByTestId("mcp-test-fail-brave");
    expect(api.posts).toContain("/mcp/servers/brave/test");
    expect(fail.textContent).toContain("MCPServerStopped: pack 'brave'");
    expect(fail.textContent).toContain("It needs BRAVE_API_KEY and none was given");
    expect(screen.getByTestId("mcp-test-fix-brave").textContent).toBe(
      "Add BRAVE_API_KEY in Settings > Tools",
    );
    expect(screen.getByTestId("mcp-test-stderr-brave").querySelector("pre")?.textContent).toBe(TAIL);
    expect(fail.textContent).not.toContain("advertised no tools");
  });

  it("a Test with ok false and nothing else still says Test failed, with no fold", async () => {
    api.test = { ok: false, count: 0, tools: [], error: null };
    render(<ToolsPage />);
    await screen.findByTestId("mcp-state-filesystem");
    fireEvent.click(within(rowOf("filesystem")).getByRole("button", { name: /^Test/ }));
    const fail = await screen.findByTestId("mcp-test-fail-filesystem");
    expect(fail.textContent).toContain("Test failed.");
    expect(screen.queryByTestId("mcp-test-stderr-filesystem")).toBeNull();
  });
});

describe("the new copy keeps the house rules", () => {
  const TOOLS = "components/settings/pages/ToolsPage.tsx";
  const DIRECTORY = "components/settings/pages/DirectoryPage.tsx";
  const NEW_COPY = [
    "Last output",
    "No tools loaded.",
    "Failing: ",
    "in vault",
    "encrypted vault",
    "Its tools are closed.",
  ];

  it("every new sentence is in the source and none holds a spaced dash", () => {
    const src = readSrc(TOOLS);
    for (const piece of NEW_COPY) {
      expect(src, piece).toContain(piece);
      expect(piece).not.toMatch(ASIDE);
    }
    const dir = readSrc(DIRECTORY);
    for (const piece of ["Last output", "No tools loaded", "Didn’t start", "Retry"]) {
      expect(dir, piece).toContain(piece);
    }
  });

  it("the v1.342.0 blocks use whole-pixel sizes only (the 10.5px raw-error line is gone)", () => {
    const src = readSrc(TOOLS);
    expect(src).not.toContain("text-[10.5px]");
    const start = src.indexOf("function LastOutput");
    const end = src.indexOf("/** The quarantined tool names of a pack");
    expect(start).toBeGreaterThan(0);
    expect(end).toBeGreaterThan(start);
    expect(src.slice(start, end)).not.toMatch(/\[\d+\.\d+px\]/);
    const dir = readSrc(DIRECTORY);
    const dStart = dir.indexOf("function LastOutput");
    // v1.353.0 (G42): the mechanism tag (`const VIA`) became the category
    // line's tooltip table, `VIA_TITLE`, in the same place.
    const dEnd = dir.indexOf("const VIA_TITLE: Record<ConnectVia");
    expect(dStart).toBeGreaterThan(0);
    expect(dEnd).toBeGreaterThan(dStart);
    expect(dir.slice(dStart, dEnd)).not.toMatch(/\[\d+\.\d+px\]/);
  });
});
