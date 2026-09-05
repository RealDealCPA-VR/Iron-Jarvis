/**
 * v1.229.0 (audit Wave 3, U4) — a tool pack that did not start is visible
 * where the user looks.
 *
 * GET /mcp/servers now carries `last_error` (the exception text the loader
 * used to log and drop). Pinned here:
 *  - Tools page: the row renders an amber "Didn’t start: <last_error>" line
 *    with a Retry that POSTs /mcp/servers/{name}/reload and re-reads the list;
 *    a row with last_error null renders no such line.
 *  - Overview: /diagnostics `mcp_servers[].last_error` degrades the hero to
 *    "1 thing needs attention" and a note names the pack + reason with a link
 *    to Tools; with no failed pack the hero still says nominal.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const ERR = "FileNotFoundError: [WinError 2] The system cannot find the file specified: npx";

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
    servers: [] as Record<string, unknown>[],
    diag: {} as Record<string, unknown>,
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
    if (path === "/mcp/catalog") return { catalog: [] };
    if (path === "/tools/custom") return { tools: [] };
    if (path.startsWith("/diagnostics")) return api.diag;
    if (path.startsWith("/sessions")) return { sessions: [] };
    if (path === "/health")
      return { status: "ok", version: "1.229.0", providers: [], default_provider: "mock" };
    if (path === "/metrics")
      return {
        sessions_evaluated: 1,
        avg_completion: 1,
        avg_tool_success_rate: 1,
        avg_latency_s: 1,
        total_tool_invocations: 1,
        event_count: 1,
      };
    if (path === "/vault") return { providers: [] };
    if (path.startsWith("/onboarding"))
      return { complete: true, done: true, dismissed: true, checklist: [], checks: [], next_step: null };
    if (path.startsWith("/goals") || path.startsWith("/autonomy"))
      return { goals: [], rules: [], enabled: false };
    if (path === "/templates") return { templates: [] };
    if (path.startsWith("/reflex")) return { rules: [] };
    if (path.startsWith("/workflows/runs")) return { runs: [] };
    if (path.startsWith("/chat/approvals")) return { approvals: [] };
    if (path.startsWith("/computeruse")) return { pending_approvals: 0 };
    return {};
  },
  post: async (path: string) => {
    api.posts.push(path);
    if (path.endsWith("/reload")) {
      // The daemon refreshed its record: the next GET reflects it.
      api.servers = api.servers.map((s) =>
        s.name === "brave_search"
          ? { ...s, last_error: api.reload.last_error, tools_loaded: api.reload.tools_loaded }
          : s,
      );
      return api.reload;
    }
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

const ToolsPage = (await import("@/app/tools/page")).default;
const OverviewPage = (await import("@/app/page")).default;

const failedRow = {
  name: "brave_search",
  command: "npx",
  args: ["-y", "@brave/brave-search-mcp-server"],
  env: {},
  tools_loaded: 0,
  tool_names: [],
  last_error: ERR,
  last_attempt_at: new Date().toISOString(),
};
const healthyRow = {
  name: "filesystem",
  command: "npx",
  args: ["-y", "@modelcontextprotocol/server-filesystem"],
  env: {},
  tools_loaded: 3,
  tool_names: ["read_file", "write_file", "list_dir"],
  last_error: null,
  last_attempt_at: null,
};

beforeEach(() => {
  localStorage.clear();
  api.gets.length = 0;
  api.posts.length = 0;
  api.servers = [failedRow, healthyRow];
  api.diag = {};
  api.reload = { ok: false, tools_loaded: 0, last_error: null };
});
afterEach(() => cleanup());

describe("Tools page names the pack that did not start (v1.229.0)", () => {
  it("renders the amber Didn’t-start line with the daemon's reason, only on the failed row", async () => {
    render(<ToolsPage />);
    const line = await screen.findByTestId("mcp-last-error-brave_search");
    expect(line.textContent).toContain("Didn’t start:");
    expect(line.textContent).toContain(ERR);
    expect(screen.queryByTestId("mcp-last-error-filesystem")).toBeNull();
  });

  it("Retry POSTs the reload route and the line clears once the row reloads clean", async () => {
    api.reload = { ok: true, tools_loaded: 2, last_error: null };
    render(<ToolsPage />);
    const line = await screen.findByTestId("mcp-last-error-brave_search");
    fireEvent.click(line.querySelector("button") as HTMLButtonElement);
    // The thing itself: the amber line is gone and the reload result shows.
    await waitFor(() => {
      expect(screen.queryByTestId("mcp-last-error-brave_search")).toBeNull();
    });
    expect(api.posts).toContain("/mcp/servers/brave_search/reload");
    expect(screen.getByText(/Connected — 2 tools available now/)).not.toBeNull();
  });

  it("a Retry that fails again shows the NEW reason on the row", async () => {
    api.reload = { ok: false, tools_loaded: 0, last_error: "TimeoutError: did not respond within 15s" };
    render(<ToolsPage />);
    const line = await screen.findByTestId("mcp-last-error-brave_search");
    fireEvent.click(line.querySelector("button") as HTMLButtonElement);
    await waitFor(() => {
      expect(screen.getByTestId("mcp-last-error-brave_search").textContent).toContain(
        "TimeoutError: did not respond within 15s",
      );
    });
  });
});

describe("Overview hero degrades over a failed pack (v1.229.0)", () => {
  it("says '1 thing needs attention' and names the pack with a way to Retry", async () => {
    api.diag = {
      background_loops: { scheduler: { ok: true } },
      mcp_servers: [
        { name: "brave_search", tools_loaded: 0, last_error: ERR },
        { name: "filesystem", tools_loaded: 3, last_error: null },
      ],
    };
    render(<OverviewPage />);
    const note = await screen.findByTestId("pack-failing-note");
    expect(note.textContent).toContain("brave_search");
    expect(note.textContent).toContain(ERR);
    expect(note.querySelector('a[href="/tools"]')).not.toBeNull();
    expect(screen.getByText("1 thing needs attention")).toBeTruthy();
    expect(screen.queryByText(/All systems nominal/)).toBeNull();
  });

  it("no failed pack: hero stays nominal and there is no note", async () => {
    api.diag = {
      background_loops: { scheduler: { ok: true } },
      mcp_servers: [{ name: "filesystem", tools_loaded: 3, last_error: null }],
    };
    render(<OverviewPage />);
    await screen.findByText("All systems nominal");
    expect(screen.queryByTestId("pack-failing-note")).toBeNull();
  });
});
