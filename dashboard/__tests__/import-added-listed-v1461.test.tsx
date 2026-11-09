/**
 * v1.461.0 — the owner, with a screenshot of the import door: "if the tools
 * are already added, then they should just be listed and not shown as
 * potentially new tools to add if they have already been integrated".
 *
 * A row the daemon calls `exists` is listed apart under "Already added": no
 * tick box, never counted in "Add selected (N)", never sent to the apply
 * route. A group (or a pasted config) with nothing new says so and offers no
 * Add button.
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
    posts: [] as { path: string; body: unknown }[],
    servers: [] as Record<string, unknown>[],
    serversAfterApply: null as Record<string, unknown>[] | null,
    applied: false,
    sources: [] as Record<string, unknown>[],
    apply: { added: [], skipped: [] } as Record<string, unknown>,
    preview: { servers: [] as Record<string, unknown>[], unrecognised: false } as Record<string, unknown>,
    addResult: { name: "", added: true, tools_loaded: 1, auto_approve: false, note: null } as Record<string, unknown>,
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
    if (path === "/mcp/servers") {
      return { servers: api.applied && api.serversAfterApply ? api.serversAfterApply : api.servers };
    }
    if (path === "/mcp/catalog") return { catalog: [] };
    if (path === "/tools/custom") return { tools: [] };
    if (path === "/mcp/import/sources") return { sources: api.sources };
    return {};
  },
  post: async (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    if (path === "/mcp/import/preview") return api.preview;
    if (path === "/mcp/import/apply") {
      api.applied = true;
      return api.apply;
    }
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
const words = await import("@/lib/addPackWords");

function row(name: string, over: Record<string, unknown> = {}) {
  return {
    name,
    transport: "stdio",
    command: "npx",
    args: ["-y", `@example/${name}`],
    url: "",
    header_names: [],
    env_keys: [],
    secret_keys: [],
    exists: false,
    runtime_ready: true,
    launcher_note: "",
    source: "claude-desktop",
    origin: "C:\\AppData\\Claude\\claude_desktop_config.json",
    disabled: false,
    exists_as: "",
    blurb: "",
    ...over,
  };
}

const GITHUB = row("github", {
  args: ["-y", "@modelcontextprotocol/server-github"],
  env_keys: ["GITHUB_HOST", "GITHUB_PERSONAL_ACCESS_TOKEN"],
  secret_keys: ["GITHUB_PERSONAL_ACCESS_TOKEN"],
  blurb: "Repos, issues, and pull requests.",
});
const LEDGER = row("ledger-tool", { args: ["-y", "@acme/ledger-mcp"] });
const PARKED = row("parked", { disabled: true });
const DRIVE = row("google-drive", { exists: true, exists_as: "app" });
const BRAIN = row("basic-memory", { exists: true, exists_as: "memory" });

function desktop(servers: Record<string, unknown>[]) {
  return {
    source: "claude-desktop",
    label: "Claude Desktop",
    origin: "C:\\AppData\\Claude\\claude_desktop_config.json",
    readable: true,
    note: "",
    servers,
  };
}

const posted = (path: string) => api.posts.filter((p) => p.path === path);

beforeEach(() => {
  api.gets = [];
  api.posts = [];
  api.servers = [];
  api.serversAfterApply = null;
  api.applied = false;
  api.sources = [];
  api.apply = { added: [], skipped: [] };
  api.preview = { servers: [], unrecognised: false };
  api.addResult = { name: "", added: true, tools_loaded: 1, auto_approve: false, note: null };
});
afterEach(() => cleanup());

const QB = row("quickbooks", { exists: true, exists_as: "pack" });
const BRAIN_SSH = row("hermes-brain", { exists: true, exists_as: "pack" });

describe("already added: listed, never offered", () => {
  it("an added row has no tick box, sits under 'Already added', and is not counted or sent", async () => {
    api.sources = [desktop([QB, LEDGER, BRAIN_SSH])];
    render(<ToolsPage />);
    const qb = await screen.findByTestId("import-row-quickbooks");
    expect(within(qb).queryByTestId("import-pick-quickbooks")).toBeNull();
    expect(within(qb).getByTestId("import-exists-quickbooks").textContent).toBe("already added");
    const listed = screen.getByTestId("import-added");
    expect(listed.textContent).toContain("Already added");
    expect(within(listed).getByTestId("import-row-hermes-brain")).toBeTruthy();
    expect(within(listed).queryByTestId("import-row-ledger-tool")).toBeNull();
    // The new one is offered, ticked, and is the only one counted.
    const fresh = screen.getByTestId("import-new");
    expect((within(fresh).getByTestId("import-pick-ledger-tool") as HTMLInputElement).checked).toBe(true);
    expect(within(fresh).queryByTestId("import-row-quickbooks")).toBeNull();
    const apply = screen.getByTestId("import-source-apply-claude-desktop");
    expect(apply.textContent).toContain("Add selected (1)");
    fireEvent.click(apply);
    await waitFor(() => expect(posted("/mcp/import/apply")).toHaveLength(1));
    expect((posted("/mcp/import/apply")[0].body as { names: string[] }).names).toEqual(["ledger-tool"]);
  });

  it("a group where everything is already added lists them and offers no Add button", async () => {
    api.sources = [desktop([QB, BRAIN_SSH])];
    render(<ToolsPage />);
    expect(
      (await screen.findByTestId("import-source-nothing-new-claude-desktop")).textContent,
    ).toBe(words.NOTHING_NEW_WORDS);
    expect(screen.queryByTestId("import-source-apply-claude-desktop")).toBeNull();
    expect(screen.queryByTestId("import-new")).toBeNull();
    expect(within(screen.getByTestId("import-added")).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
  });

  it("a pasted config whose servers are all added says so and offers no Add button", async () => {
    api.preview = { servers: [QB], unrecognised: false };
    render(<ToolsPage />);
    const area = await screen.findByTestId("add-pack");
    fireEvent.click(within(area).getByTestId("add-pack-door-paste"));
    const box = within(area).getByTestId("add-pack-paste") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: '{"mcpServers": {"quickbooks": {}}}' } });
    fireEvent.blur(box);
    expect((await screen.findByTestId("add-pack-nothing-new")).textContent).toBe(words.NOTHING_NEW_WORDS);
    expect(screen.queryByTestId("add-pack-apply")).toBeNull();
    expect(within(screen.getByTestId("import-row-quickbooks")).queryByTestId("import-pick-quickbooks")).toBeNull();
  });
});
