/**
 * v1.299.0 — MCP pack-tool quarantine, the Tools page half.
 *
 * GET /mcp/servers rows now carry `quarantined: string[]` (write-like tools
 * that appeared after the pack was first trusted) and `tools: [{name,
 * write_like, quarantined}]`. Pinned here:
 *  - the Permissions panel row wears an amber "N new — asks until trusted"
 *    badge and lists each held tool with a Trust button; a row with nothing
 *    held wears none; a row from an OLDER daemon (no field at all) wears none;
 *  - Trust POSTs exactly /mcp/servers/{name}/tools/{tool}/trust and the list
 *    re-reads, so the badge is gone once the daemon reports the tool clear;
 *  - the pack's tool chip for a held tool is marked (data-quarantined).
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
    gets: [] as string[],
    posts: [] as string[],
    servers: [] as Record<string, unknown>[],
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
    if (path.startsWith("/diagnostics")) return {};
    if (path.startsWith("/sessions")) return { sessions: [] };
    if (path === "/health")
      return { status: "ok", version: "1.299.0", providers: [], default_provider: "mock" };
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
    if (path.endsWith("/trust")) {
      // The daemon cleared the tool: the next GET says so.
      const [, , , server, , tool] = path.split("/");
      api.servers = api.servers.map((s) =>
        s.name === server
          ? {
              ...s,
              quarantined: (s.quarantined as string[]).filter((n) => n !== tool),
              tools: (s.tools as { name: string; quarantined?: boolean }[]).map((t) =>
                t.name === tool ? { ...t, quarantined: false } : t,
              ),
            }
          : s,
      );
      return { ok: true, tool };
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

const ToolsPage = (await import("@/components/settings/pages/ToolsPage")).default;

const heldRow = {
  name: "filesystem",
  command: "npx",
  args: ["-y", "@modelcontextprotocol/server-filesystem"],
  env: {},
  tools_loaded: 3,
  tool_names: ["read_file", "write_file", "delete_file"],
  last_error: null,
  quarantined: ["write_file", "delete_file"],
  tools: [
    { name: "read_file", write_like: false, quarantined: false },
    { name: "write_file", write_like: true, quarantined: true },
    { name: "delete_file", write_like: true, quarantined: true },
  ],
};
const clearRow = {
  name: "brave_search",
  command: "npx",
  args: ["-y", "@brave/brave-search-mcp-server"],
  env: {},
  tools_loaded: 1,
  tool_names: ["search"],
  last_error: null,
  quarantined: [],
  tools: [{ name: "search", write_like: false, quarantined: false }],
};
/** A row from a daemon older than the quarantine: neither field exists. */
const olderRow = {
  name: "legacy_pack",
  command: "npx",
  args: [],
  env: {},
  tools_loaded: 2,
  tool_names: ["a", "b"],
  last_error: null,
};

beforeEach(() => {
  localStorage.clear();
  api.gets.length = 0;
  api.posts.length = 0;
  api.servers = [heldRow, clearRow, olderRow];
});
afterEach(() => cleanup());

describe("MCP quarantine on the Tools page (v1.299.0)", () => {
  it("badges only the pack with held tools, names each, and marks the pack's chips", async () => {
    render(<ToolsPage />);
    const badge = await screen.findByTestId("perm-quarantine-filesystem");
    expect(badge.textContent).toContain("2 new");
    expect(badge.textContent).toContain("asks until trusted");
    expect(screen.queryByTestId("perm-quarantine-brave_search")).toBeNull();
    expect(screen.queryByTestId("perm-quarantine-legacy_pack")).toBeNull();
    // Per-tool Trust buttons, one per held tool, none for the read tool.
    expect(screen.getByTestId("perm-trust-filesystem-write_file")).toBeEnabled();
    expect(screen.getByTestId("perm-trust-filesystem-delete_file")).toBeEnabled();
    expect(screen.queryByTestId("perm-trust-filesystem-read_file")).toBeNull();
    expect(screen.queryByTestId("perm-trust-brave_search-search")).toBeNull();
    // The one-line explanation is on the row.
    const panel = screen.getByTestId("permission-rows");
    expect(within(panel).getByText(/New since last load — asks until you trust it/)).not.toBeNull();
    // The pack row's chip for a held tool is marked; the read tool's is not.
    const marked = document.querySelectorAll("[data-quarantined='1']");
    expect(Array.from(marked).map((n) => n.textContent)).toEqual(["write_file", "delete_file"]);
  });

  it("Trust POSTs /mcp/servers/{name}/tools/{tool}/trust and the badge counts down as the list re-reads", async () => {
    render(<ToolsPage />);
    await screen.findByTestId("perm-quarantine-filesystem");
    fireEvent.click(screen.getByTestId("perm-trust-filesystem-write_file"));
    // The thing itself: the trusted tool's button is gone and the badge says 1.
    await waitFor(() => {
      expect(screen.queryByTestId("perm-trust-filesystem-write_file")).toBeNull();
    });
    expect(api.posts).toEqual(["/mcp/servers/filesystem/tools/write_file/trust"]);
    expect(screen.getByTestId("perm-quarantine-filesystem").textContent).toContain("1 new");
    expect(screen.getByTestId("perm-trust-filesystem-delete_file")).toBeEnabled();

    fireEvent.click(screen.getByTestId("perm-trust-filesystem-delete_file"));
    await waitFor(() => {
      expect(screen.queryByTestId("perm-quarantine-filesystem")).toBeNull();
    });
    expect(api.posts).toEqual([
      "/mcp/servers/filesystem/tools/write_file/trust",
      "/mcp/servers/filesystem/tools/delete_file/trust",
    ]);
  });

  it("an older daemon (no quarantine fields anywhere) shows no badge and no Trust at all", async () => {
    api.servers = [olderRow, { ...clearRow, quarantined: undefined, tools: undefined }];
    render(<ToolsPage />);
    await screen.findByTestId("permission-rows");
    await waitFor(() => expect(api.gets).toContain("/mcp/servers"));
    expect(screen.queryByText(/asks until trusted/)).toBeNull();
    expect(document.querySelector("[data-testid^='perm-trust-']")).toBeNull();
    expect(document.querySelector("[data-quarantined]")).toBeNull();
  });
});
