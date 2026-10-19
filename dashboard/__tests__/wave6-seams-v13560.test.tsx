/**
 * v1.356.0 integrate (product-readiness Wave 6): the lanes joined, through
 * the REAL components. Only lib/api (the transport) and a few whole pages
 * that have their own pins are doubles.
 *
 *  (a) G78: the REAL daemon's rows for a switched-off pack
 *      (`fixtures/wave6-seams-v13560.json`, written by
 *      tests/test_integrate_wave6_v13560.py — regenerate with
 *      IJ_REGEN_WAVE6_SEAMS=1) read "Off" on the Tools row (6A) AND on the
 *      Directory card (6A's switch reading 6D's service row) — never failed,
 *      never Retry — and "(off)" in the reach summary (6C).
 *  (c) G80: the calm default — the Tools catalog card's status chip (now the
 *      calm Badge) and a connected pack's row chip are the same chip.
 *  Plus the integrator's follow-ups: the restart tag opens Maintenance in
 *  place (6B's prop, wired by SettingsHome); a folder list keeps a comma in
 *  a folder name (6C); a key of the same name says so on an import row; the
 *  hidden Continue prompt is not a question (panelAskedAt).
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
    servers: [] as Record<string, unknown>[],
    catalog: [] as Record<string, unknown>[],
    connectors: [] as Record<string, unknown>[],
    gets: {} as Record<string, unknown>,
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
    const bare = path.split("?")[0];
    if (bare === "/mcp/servers") return { servers: api.servers };
    if (bare === "/mcp/catalog") return { catalog: api.catalog };
    if (bare === "/tools/custom") return { tools: [] };
    if (bare === "/connectors") return { connectors: api.connectors, categories: ["Communication"] };
    if (bare === "/mcp/import/sources") return { sources: [] };
    if (bare in api.gets) return JSON.parse(JSON.stringify(api.gets[bare]));
    return {};
  },
  post: async () => ({}),
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
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: true, checking: false, health: { providers: [] }, refresh: vi.fn() }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(window.location.search),
  usePathname: () => "/settings",
  redirect: () => {
    throw new Error("NEXT_REDIRECT");
  },
}));
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
// The System group's sections have their own pins; stand-ins here.
vi.mock("@/components/settings/MaintenanceCard", () => ({
  MaintenanceCard: () => <div data-testid="maintenance-stand-in">maintenance</div>,
}));
vi.mock("@/components/settings/pages/UpdatesPage", () => ({ default: () => <div>updates</div> }));
vi.mock("@/components/settings/DaemonTokenCard", () => ({ DaemonTokenCard: () => <div>token</div> }));

const { __resetApiCache } = await import("@/lib/apiCache");
const ToolsPage = (await import("@/components/settings/pages/ToolsPage")).default;
const DirectoryPage = (await import("@/components/settings/pages/DirectoryPage")).default;
const { SettingsHome } = await import("@/components/settings/SettingsHome");
const { connectedApps } = await import("@/components/settings/AccessSummary");
const { fromText } = await import("@/components/settings/SettingRow");
const { CALM_BADGE } = await import("@/components/ui");
const { signedInWords } = await import("@/lib/addPackWords");
const { panelAskedAt } = await import("@/lib/panelReceipt");
const words = await import("@/lib/extensionWords");

const FIXTURE = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "wave6-seams-v13560.json"), "utf8"),
) as { tools_row: Record<string, unknown>; directory_card: Record<string, unknown> };

beforeEach(() => {
  localStorage.clear();
  __resetApiCache();
  api.servers = [];
  api.catalog = [];
  api.connectors = [];
  api.gets = {};
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => cleanup());

/* ------------------------------------------------------------ (a) G78 --- */

describe("G78: the daemon's switched-off pack reads Off on the Tools row AND the Directory card", () => {
  it("the contract is the daemon's off row (no secret, no failure)", () => {
    expect(FIXTURE.tools_row.state).toBe("off");
    expect(FIXTURE.tools_row.enabled).toBe(false);
    expect(FIXTURE.directory_card.status).toBe("off");
    expect(FIXTURE.directory_card.enabled).toBe(false);
    // The token the daemon was given (tests/test_integrate_wave6_v13560.TOKEN) never rides a row.
    expect(JSON.stringify(FIXTURE)).not.toMatch(/WAVE6-integrate|NEVERSHOWN/);
  });

  it("Tools: Off, the off line, the switch unchecked, no Retry, no failure line", async () => {
    api.servers = [FIXTURE.tools_row];
    render(<ToolsPage />);
    await waitFor(() => expect(screen.getByTestId("mcp-state-slack").textContent).toBe("Off"));
    const row = document.querySelector('[data-pack-row="slack"]') as HTMLElement;
    expect(within(row).getByTestId("mcp-off-slack").textContent).toBe(words.PACK_OFF_LINE);
    expect(within(row).getByTestId("mcp-switch-slack").getAttribute("aria-checked")).toBe("false");
    expect(within(row).queryByTestId("mcp-retry-slack")).toBeNull();
    expect(within(row).queryByTestId("mcp-last-error-slack")).toBeNull();
    expect(row.textContent).not.toMatch(/didn't start|failed/i);
  });

  it("Directory: Off with its line, the switch unchecked, never Needs attention or Retry", async () => {
    api.connectors = [FIXTURE.directory_card];
    render(<DirectoryPage />);
    const c = await screen.findByTestId("connector-card-slack", {}, { timeout: 10_000 });
    expect(within(c).getByTestId("connector-off-slack").textContent).toBe("Off");
    expect(within(c).getByTestId("connector-off-line-slack").textContent).toBe(words.PACK_OFF_LINE);
    expect(within(c).getByTestId("connector-switch-slack").getAttribute("aria-checked")).toBe("false");
    expect(within(c).queryByTestId("connector-attention-slack")).toBeNull();
    expect(within(c).queryByTestId("connector-retry-slack")).toBeNull();
  });

  it("the reach summary names it off, never 'not working'", () => {
    const card = FIXTURE.directory_card as { id: string; name: string; status: string; connected: boolean; category: string };
    expect(connectedApps([card])).toEqual(["Slack (off)"]);
    expect(connectedApps([{ ...card, status: "failed" }])).toEqual(["Slack (not working)"]);
  });
});

/* ------------------------------------------------------------ (c) G80 --- */

describe("G80: one calm status chip on the Tools page — catalog cards and pack rows", () => {
  it("a catalog card's chip and a loaded pack's row chip are the same calm chip", async () => {
    api.servers = [{ ...FIXTURE.tools_row, name: "fs", enabled: true, state: "ok", tools_loaded: 2, tool_names: ["a", "b"] }];
    api.catalog = [
      { id: "fetch", name: "Fetch", description: "Read a web page.", command: "npx", args: [], category: "integration" },
      { id: "fs", name: "Files", description: "Read files.", command: "npx", args: [], category: "integration" },
    ];
    render(<ToolsPage />);
    const rowChip = await screen.findByTestId("mcp-state-fs", {}, { timeout: 10_000 });
    const cards = await screen.findAllByTestId(/^status-(available|added)$/, {}, { timeout: 10_000 });
    for (const chip of [rowChip, ...cards]) {
      expect(chip.getAttribute("data-badge-variant")).toBe("calm");
      for (const cls of CALM_BADGE.split(" ")) expect(chip.className).toContain(cls);
      expect(chip.className).not.toMatch(/\bborder\b|emerald|amber-/);
    }
    const added = screen.getAllByTestId("status-added")[0];
    expect(added.textContent).toBe("Enabled");
    expect(added.getAttribute("data-tone")).toBe("green");
    expect(screen.getAllByTestId("status-available")[0].textContent).toBe("Not added");
  });
});

/* -------------------------------------------------- integrator follow-ups --- */

describe("the restart tag opens Settings › System › Maintenance in place (6B's prop, wired)", () => {
  it("pressing 'after a restart' opens the Maintenance section, never a /settings?section= link", async () => {
    api.gets = {
      "/settings/schema": {
        groups: [
          { id: "automation", label: "Automation", description: "What runs on its own." },
          { id: "system", label: "System", description: "Updates and maintenance." },
        ],
        settings: [
          { key: "max_concurrent_sessions", label: "Background runs at once", group: "automation", section: "", type: "number", restart: true },
        ],
        secrets: [],
      },
      "/settings/values": { values: { max_concurrent_sessions: 0 } },
      "/config/ledger": { changes: [] },
    };
    window.history.replaceState({}, "", "/settings?section=automation");
    render(<SettingsHome />);
    const tag = await screen.findByTestId("setting-max_concurrent_sessions-restart", {}, { timeout: 10_000 });
    expect(tag.tagName).toBe("BUTTON");
    expect(tag.closest("a")).toBeNull();
    fireEvent.click(tag);
    await screen.findByTestId("maintenance-stand-in", {}, { timeout: 10_000 });
    const sub = screen.getByTestId("settings-sub-system-maintenance");
    expect(within(sub).getAllByRole("button")[0].getAttribute("aria-expanded")).toBe("true");
  });
});

describe("a folder list keeps a comma inside a folder name (6C review C)", () => {
  const folders = { key: "readable_folders", label: "Folders Jarvis may read", group: "permissions", type: "list" } as never;
  const other = { key: "default_skills", label: "Skills always loaded", group: "automation", type: "list" } as never;
  it("folders split on new lines only; other lists still take commas", () => {
    expect(fromText(folders, "C:\\Clients\\Smith, John\nD:\\Tax 2025")).toEqual(["C:\\Clients\\Smith, John", "D:\\Tax 2025"]);
    expect(fromText(other, "a, b\nc")).toEqual(["a", "b", "c"]);
  });
});

describe("an import row whose name is a saved KEY says so (wave-5 follow-up 2)", () => {
  it("never 'already connected'; one quiet line naming the key", () => {
    const row = { name: "pixio", exists: false, exists_as: "", key_app: "Pixio" } as never;
    expect(signedInWords(row)).toBe("Your Pixio key is saved here for its built-in tools; this adds the server's own tools.");
    expect(signedInWords({ name: "pixio", exists: true, key_app: "Pixio" } as never)).toBe("");
  });
});

describe("the hidden Continue prompt is not a question (wave-5 follow-up 3)", () => {
  it("an agent's reply after a Continue is timed from the real question", () => {
    const msgs = [
      { role: "user", content: "@hermes find me a place to eat", at: "2026-10-19T10:00:00Z" },
      { role: "assistant", content: "On it.", panelWho: "remote:hermes", panelKind: "progress", at: "2026-10-19T10:00:05Z" },
      { role: "user", content: "Continue", continuation: true, at: "2026-10-19T10:03:00Z" },
      { role: "assistant", content: "Here are three.", panelWho: "remote:other", at: "2026-10-19T10:04:00Z" },
    ];
    const asked = panelAskedAt(msgs);
    expect(asked[3]).toBe("2026-10-19T10:00:00Z");
    // Control: a real question in its place does move the clock.
    const typed = msgs.map((m, i) => (i === 2 ? { ...m, continuation: undefined } : m));
    expect(panelAskedAt(typed)[3]).toBe("2026-10-19T10:03:00Z");
  });
});
