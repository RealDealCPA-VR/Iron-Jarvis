/**
 * v1.354.0 integrate, seam (b): the Directory's "found in your other apps"
 * line (wave 3, G42) and the Tools page's add area (lane 4C, G49/G50) are
 * the SAME door.
 *
 * - Both mount `SourcesDoor` and read ONE answer of GET /mcp/import/sources.
 * - Opened from the Directory, the door behaves exactly as lane 4C built it
 *   on the Tools page: a server switched off in its app is listed UNTICKED
 *   with "turned off in Claude Desktop", the catalog's one line says what a
 *   tool does, and the launch command sits under a closed "Details".
 * - It is named with the door's own words (`DOOR_LABELS.sources`) on both
 *   pages, and every "Add a tool" link lands ON the add area
 *   (`/tools?focus=add`, the add area's `useFocusRef("add")`).
 *
 * Driven through the REAL pages and the real useApi; only lib/api is a fake
 * (the connections-home-v13530 harness). A waitFor waits for the thing it
 * asserts.
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
    sources: [] as Record<string, unknown>[],
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
    if (path === "/connectors") return { connectors: [], categories: [] };
    if (path === "/mcp/import/sources") return { sources: api.sources };
    if (path === "/mcp/servers") return { servers: [] };
    if (path === "/mcp/catalog") return { catalog: [] };
    if (path === "/tools/custom") return { tools: [] };
    return {};
  },
  post: async (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    if (path === "/mcp/import/apply") return { added: [], skipped: [] };
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
const { DOOR_LABELS } = await import("@/lib/addPackWords");

const ROOT = process.cwd();
const src = (rel: string) => readFileSync(join(ROOT, rel), "utf8").replace(/\r\n/g, "\n");

/** Claude Desktop holds GitHub (on, a known package) and Notion (switched OFF
 *  in Claude Desktop); Cursor holds Linear. */
const SOURCES = [
  {
    source: "claude-desktop",
    label: "Claude Desktop",
    origin: "C:/Users/me/AppData/Roaming/Claude/claude_desktop_config.json",
    readable: true,
    note: "",
    servers: [
      {
        name: "github",
        transport: "stdio",
        command: "npx",
        args: ["-y", "@modelcontextprotocol/server-github"],
        exists: false,
        runtime_ready: true,
        blurb: "Repos, issues, and pull requests.",
      },
      {
        name: "notion",
        transport: "stdio",
        command: "npx",
        args: ["-y", "notion-server"],
        exists: false,
        runtime_ready: true,
        disabled: true,
      },
    ],
  },
  {
    source: "cursor",
    label: "Cursor",
    origin: "C:/Users/me/.cursor/mcp.json",
    readable: true,
    note: "",
    servers: [{ name: "linear", transport: "http", url: "https://mcp.linear.app/mcp", exists: false }],
  },
];

beforeEach(() => {
  localStorage.clear();
  __resetApiCache();
  api.gets = [];
  api.posts = [];
  api.sources = SOURCES.map((s) => ({ ...s, servers: s.servers.map((r) => ({ ...r })) }));
});
afterEach(() => cleanup());

describe("seam (b): the Directory's import line opens the Tools page's door", () => {
  it("from the Directory, a switched-off server is listed unticked in the app's words, a known one says what it does", async () => {
    render(<DirectoryPage />);
    // Wait for the line (the loading state wears the same test id).
    const section = (await screen.findByTestId("directory-import-line")).closest("section") as HTMLElement;
    // Named with the door's own words — the label the Tools page's first door wears.
    expect(section.getAttribute("aria-label")).toBe(DOOR_LABELS.sources);
    fireEvent.click(screen.getByTestId("directory-import-open"));
    const group = await screen.findByTestId("import-source-claude-desktop");

    // G50 through the Directory: Notion is off in Claude Desktop — unticked, said so.
    const notion = within(group).getByTestId("import-row-notion");
    expect(within(notion).getByTestId("import-off-notion").textContent).toBe("turned off in Claude Desktop");
    expect((within(notion).getByRole("checkbox") as HTMLInputElement).checked).toBe(false);
    // GitHub is on: ticked, and says what it does in the catalog's line (G49)…
    const github = within(group).getByTestId("import-row-github");
    expect((within(github).getByRole("checkbox") as HTMLInputElement).checked).toBe(true);
    expect(within(github).getByTestId("import-about-github").textContent).toBe("Repos, issues, and pull requests.");
    // …with the launch command folded under a closed Details.
    const details = github.querySelector("details");
    expect(details).not.toBeNull();
    expect(details!.open).toBe(false);
    // ONE read of the sources answer: the door was fed the list the line read.
    expect(api.gets.filter((p) => p === "/mcp/import/sources")).toHaveLength(1);
    // Nothing is added without the press.
    expect(api.posts).toEqual([]);
  });

  it("Add selected from the Directory imports through the one apply route, leaving the switched-off row out", async () => {
    render(<DirectoryPage />);
    fireEvent.click(await screen.findByTestId("directory-import-open"));
    const group = await screen.findByTestId("import-source-claude-desktop");
    fireEvent.click(within(group).getByRole("button", { name: /Add selected/ }));
    await waitFor(() => expect(api.posts.some((p) => p.path === "/mcp/import/apply")).toBe(true));
    const body = api.posts.find((p) => p.path === "/mcp/import/apply")!.body as { names?: string[]; source?: string };
    expect(body.source).toBe("claude-desktop");
    expect(body.names).toContain("github");
    expect(body.names).not.toContain("notion");
  });

  it("both pages mount the SAME door component; every 'Add a tool' link lands on the add area", () => {
    const directory = src("components/settings/pages/DirectoryPage.tsx");
    const tools = src("components/settings/pages/ToolsPage.tsx");
    const addPack = src("components/tools/AddPack.tsx");
    expect(directory).toContain('import { SourcesDoor } from "@/components/tools/AddPack";');
    expect(directory).toContain("<SourcesDoor");
    expect(addPack).toMatch(/useState<Door>\("sources"\)/);
    expect(tools).toContain('const addFocusRef = useFocusRef<HTMLDivElement>("add");');
    expect(tools).toMatch(/<div ref=\{addFocusRef\}[^>]*>[\s\S]{0,200}<SectionLabel>Add a tool<\/SectionLabel>[\s\S]{0,120}<AddPack /);
    for (const [file, n] of [
      ["components/settings/pages/ConnectionsPage.tsx", 2],
      ["components/connections/RestHookups.tsx", 1],
      ["components/settings/pages/DirectoryPage.tsx", 1],
    ] as const) {
      expect((src(file).match(/"\/tools\?focus=add"/g) ?? []).length, file).toBe(n);
    }
  });
});
